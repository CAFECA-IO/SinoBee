"""Anthropic Messages API transport (native tool use, images in tool results, prompt caching)."""
from __future__ import annotations

from ..types import ChatResponse, ImagePart, Message, TextPart, ToolCall, Usage
from .base import Provider

CACHE = {"type": "ephemeral"}


def _blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    out = []
    for p in content:
        if isinstance(p, TextPart):
            if p.text:
                out.append({"type": "text", "text": p.text})
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

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01", "content-type": "application/json"}

    async def list_models(self) -> list[str]:
        data = await self._get(f"{self.base_url}/v1/models?limit=100", self._headers())
        return [m["id"] for m in data.get("data", [])]

    async def _chat(self, messages, tools, *, temperature, max_tokens) -> ChatResponse:
        system, msgs = to_anthropic(messages)
        # prompt caching breakpoints: end of system, end of tools, last user turn
        if system:
            system[-1] = {**system[-1], "cache_control": CACHE}
        if msgs and msgs[-1]["role"] == "user" and msgs[-1]["content"]:
            last = msgs[-1]["content"][-1]
            msgs[-1]["content"][-1] = {**last, "cache_control": CACHE}
        payload: dict = {"model": self.model, "max_tokens": max_tokens, "messages": msgs}
        if system:
            payload["system"] = system
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters}
                                for t in tools]
            payload["tools"][-1]["cache_control"] = CACHE
        data = await self._post(f"{self.base_url}/v1/messages", payload, self._headers())
        texts, calls = [], []
        for b in data.get("content", []):
            if b.get("type") == "text":
                texts.append(b["text"])
            elif b.get("type") == "tool_use":
                calls.append(ToolCall(b["id"], b["name"], b.get("input") or {}))
        u = data.get("usage") or {}
        usage = Usage(u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0),
                      u.get("output_tokens", 0), u.get("cache_read_input_tokens", 0) or 0,
                      u.get("cache_creation_input_tokens", 0) or 0)
        return ChatResponse(Message("assistant", "\n".join(texts), calls), usage, data.get("stop_reason") or "",
                            data.get("model", self.model))
