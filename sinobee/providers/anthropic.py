"""Anthropic Messages API transport (native tool use, images in tool results, prompt caching)."""
from __future__ import annotations

import json
import logging

from ..types import ChatResponse, ImagePart, Message, TextPart, ToolCall, Usage
from .base import Provider, ProviderError

log = logging.getLogger("sinobee.providers")

CACHE = {"type": "ephemeral"}


def _blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    out = []
    for p in content:
        if isinstance(p, TextPart):
            if p.text:
                out.append({"type": "text", "text": p.text, **({"_volatile": True} if p.volatile else {})})
        elif isinstance(p, ImagePart):
            out.append({"type": "image", "source": {"type": "base64", "media_type": p.media_type, "data": p.data}})
    return out


def to_anthropic(messages: list[Message]) -> tuple[list[dict], list[dict]]:
    system: list[dict] = []
    out: list[dict] = []

    def push(role: str, blocks: list[dict]):
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        if m.role == "system":
            system.extend(_blocks(m.content))
        elif m.role == "tool":
            inner = _blocks(m.content) or [{"type": "text", "text": "(empty)"}]
            push("user", [{"type": "tool_result", "tool_use_id": m.tool_call_id, "content": inner,
                           **({"is_error": True} if m.text().startswith("ERROR:") else {})}])
        elif m.role == "assistant":
            blocks = _blocks(m.content)
            blocks += [{"type": "tool_use", "id": t.id, "name": t.name, "input": t.arguments} for t in m.tool_calls]
            push("assistant", blocks)
        else:
            push("user", _blocks(m.content) or [{"type": "text", "text": "(empty)"}])
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(start)"}]})
    return system, out


class AnthropicProvider(Provider):
    name = "anthropic"
    # answers are streamed: a long answer (a whole HTML page) keeps bytes flowing, so neither a proxy's idle cut
    # (Cloudflare ~100 s in front of the SinoBee.ai relay) nor a read timeout ends it; the result is assembled into
    # the same shape as a plain answer
    stream = True

    async def _read_stream(self, r) -> dict:
        blocks: dict[int, dict] = {}
        partial: dict[int, list[str]] = {}
        msg: dict = {"content": [], "usage": {}, "stop_reason": "", "model": self.model}
        async for ev in self._sse(r):
            t = ev.get("type")
            if t == "message_start":
                m = ev.get("message") or {}
                msg["model"] = m.get("model") or msg["model"]
                msg["usage"].update({k: v for k, v in (m.get("usage") or {}).items() if v is not None})
            elif t == "content_block_start":
                i = int(ev.get("index", len(blocks)))
                blocks[i] = dict(ev.get("content_block") or {})
                if blocks[i].get("type") == "tool_use":
                    blocks[i]["input"] = {}
            elif t == "content_block_delta":
                i = int(ev.get("index", 0))
                b = blocks.setdefault(i, {"type": "text", "text": ""})
                d = ev.get("delta") or {}
                dt = d.get("type")
                if dt == "text_delta":
                    b["text"] = b.get("text", "") + d.get("text", "")
                elif dt == "input_json_delta":
                    partial.setdefault(i, []).append(d.get("partial_json", ""))
                elif dt == "thinking_delta":
                    b["thinking"] = b.get("thinking", "") + d.get("thinking", "")
                elif dt == "signature_delta":
                    b["signature"] = d.get("signature", "")
            elif t == "content_block_stop":
                i = int(ev.get("index", 0))
                if i in partial and blocks.get(i, {}).get("type") == "tool_use":
                    raw = "".join(partial.pop(i))
                    try:
                        blocks[i]["input"] = json.loads(raw) if raw.strip() else {}
                    except ValueError:
                        from .openai_compat import parse_arguments
                        blocks[i]["input"] = parse_arguments(raw)
            elif t == "message_delta":
                d = ev.get("delta") or {}
                if d.get("stop_reason"):
                    msg["stop_reason"] = d["stop_reason"]
                msg["usage"].update({k: v for k, v in (ev.get("usage") or {}).items() if v is not None})
            elif t == "error":
                err = ev.get("error") or {}
                kind = err.get("type") or "error"
                raise ProviderError(f"{self.label} stream error {kind}: {err.get('message', '')}",
                                    529 if kind == "overloaded_error" else 500,
                                    kind in ("overloaded_error", "api_error", "rate_limit_error"))
        if msg["stop_reason"] == "quota":            # the relay ended the answer: the plan's window is used up
            from .base import QuotaExceeded
            raise QuotaExceeded(f"{self.label} SinoBee.ai quota used up while answering", "6h")
        for i, parts in partial.items():
            # the answer ended inside this call's arguments (max_tokens: no content_block_stop is sent): pass on what
            # arrived, marked cut, so the model is told to write it in parts instead of getting an empty call
            if blocks.get(i, {}).get("type") == "tool_use":
                raw = "".join(parts)
                blocks[i]["input"] = {"_raw": raw[-300:], "_truncated": len(raw)}
        msg["content"] = [blocks[i] for i in sorted(blocks)]
        kinds = [b.get("type") for b in msg["content"]]
        if any(k not in ("text", "tool_use") for k in kinds):
            log.info("%s answer blocks: %s (output %s tokens)", self.label, ",".join(kinds),
                     msg["usage"].get("output_tokens"))
        return msg

    def thinking_param(self) -> dict | None:
        """
        Claude 4.6+ models think by default (adaptive) and do not show it: on a big write the hidden thinking can use
        the whole output limit and the answer comes back empty (finish=max_tokens, no text, no tool call). When the
        agent asks for less reasoning (after such an empty answer, REASONING_EFFORT "low"), thinking is turned off so
        the room goes to the answer. A model that rejects it is remembered (compat) and not sent it again.
        """
        from . import compat
        from .base import REASONING_EFFORT
        if REASONING_EFFORT.get() in ("low", "minimal", "none") and "thinking" not in compat.dropped(self.label):
            return {"type": "disabled"}
        return None

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01", "content-type": "application/json"}

    async def list_models(self) -> list[str]:
        data = await self._get(f"{self.base_url}/v1/models?limit=100", self._headers())
        return [m["id"] for m in data.get("data", [])]

    async def _chat(self, messages, tools, *, temperature, max_tokens) -> ChatResponse:
        system, msgs = to_anthropic(messages)
        # prompt caching breakpoints: end of system, end of tools, and the last block before the volatile tail
        # (context, checklist): that tail changes every request, so a breakpoint on it would never be hit again
        if system:
            system[-1] = {**system[-1], "cache_control": CACHE}
        stable = [(mi, bi) for mi, m in enumerate(msgs) for bi, b in enumerate(m["content"]) if not b.get("_volatile")]
        if stable:
            mi, bi = stable[-1]
            msgs[mi]["content"][bi] = {**msgs[mi]["content"][bi], "cache_control": CACHE}
        for m in msgs:
            m["content"] = [{k: v for k, v in b.items() if k != "_volatile"} for b in m["content"]]
        payload: dict = {"model": self.model, "max_tokens": max_tokens, "messages": msgs}
        if self.stream:
            payload["stream"] = True
        if system:
            payload["system"] = system
        if temperature is not None:
            payload["temperature"] = temperature
        thinking = self.thinking_param()
        if thinking:
            payload["thinking"] = thinking
        if tools:
            # eager_input_streaming: a big argument (a whole page for write_file) is sent while the model writes it,
            # not held back until it is complete - minutes of silence otherwise. The input may then be cut or invalid
            # JSON; that comes back to the model as a tool error ("could not parse tool arguments").
            payload["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters,
                                 "eager_input_streaming": True} for t in tools]
            payload["tools"][-1]["cache_control"] = CACHE
        data = await self._post(f"{self.base_url}/v1/messages", payload, self._headers(),
                                timeout=self.call_timeout(max_tokens))
        texts, calls = [], []
        for b in data.get("content", []):
            if b.get("type") == "text":
                texts.append(b["text"])
            elif b.get("type") == "tool_use":
                calls.append(ToolCall(b["id"], b["name"], b.get("input") or {}))
        u = data.get("usage") or {}
        usage = Usage(u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0),
                      u.get("output_tokens", 0), u.get("cache_read_input_tokens", 0) or 0,
                      u.get("cache_creation_input_tokens", 0) or 0,
                      int((u.get("output_tokens_details") or {}).get("thinking_tokens") or 0))
        return ChatResponse(Message("assistant", "\n".join(texts), calls), usage, data.get("stop_reason") or "",
                            data.get("model", self.model))
