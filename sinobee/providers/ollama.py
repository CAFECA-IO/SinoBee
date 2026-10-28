"""Native Ollama transport (/api/chat, /api/embed).

The OpenAI-compatible endpoint of Ollama ignores per-request context size, so prompts larger than the server
default (4k) would be silently truncated. The native API lets SinoBee request `num_ctx` sized for its system
prompt + tools, and returns token counts for usage accounting.
"""
from __future__ import annotations

import uuid

from ..types import ChatResponse, ImagePart, Message, TextPart, ToolCall, Usage
from .base import Provider, ProviderError


def to_ollama_messages(messages: list[Message], vision: bool = True) -> list[dict]:
    out: list[dict] = []
    names: dict[str, str] = {}
    pending: list[str] = []

    def flush():
        if pending:
            out.append({"role": "user", "content": "[Images returned by the previous tool call(s)]",
                        "images": list(pending)})
            pending.clear()

    for m in messages:
        if m.role != "tool":
            flush()
        imgs = [p.data for p in m.content if isinstance(p, ImagePart)] if isinstance(m.content, list) else []
        if m.role == "tool":
            out.append({"role": "tool", "content": m.text() or ("(see image)" if imgs else ""),
                        "tool_name": names.get(m.tool_call_id or "", "")})
            if vision:
                pending.extend(imgs)
        elif m.role == "assistant":
            if not m.text().strip() and not m.tool_calls:
                continue
            d: dict = {"role": "assistant", "content": m.text()}
            if m.tool_calls:
                d["tool_calls"] = []
                for t in m.tool_calls:
                    names[t.id] = t.name
                    d["tool_calls"].append({"function": {"name": t.name, "arguments": t.arguments}})
            out.append(d)
        else:
            d = {"role": m.role, "content": m.text()}
            if imgs and vision:
                d["images"] = imgs
            out.append(d)
    flush()
    return out


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, *a, vision: bool = True, num_ctx: int = 8192, keep_alive: str = "30m",
                 think: bool | None = None, stream: bool = True, **kw):
        kw.setdefault("timeout", 900.0)  # CPU inference and first-time model loads can be slow
        super().__init__(*a, **kw)
        self.base_url = self.base_url.removesuffix("/v1")
        self.vision = vision
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.think = think
        self.stream = stream
        self._no_think_field = False   # learned: this model rejects the `think` field

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _think_value(self):
        """The `think` field for this call. The agent turns thinking down (REASONING_EFFORT) after an answer that came
        back empty because hidden thinking used up num_predict - Ollama leaves the answer empty then. A local model
        has little output room, so any turned-down level switches thinking off; gpt-oss cannot switch it off and
        takes a level instead. Otherwise the user's local.think (None = the model's default)."""
        from .base import REASONING_EFFORT
        if self._no_think_field:
            return None
        effort = REASONING_EFFORT.get()
        if effort:
            return "low" if self.model.lower().startswith("gpt-oss") else False
        return self.think

    async def _chat(self, messages, tools, *, temperature, max_tokens) -> ChatResponse:
        from .. import local
        from ..agent.context import estimate_tokens
        # the window: the configured one, or the larger one the agent asked for during this task (local.larger_ctx)
        num_ctx = max(self.num_ctx, local.grown(self.model))
        # the answer (with its hidden thinking) must fit beside the prompt, or Ollama drops the start of the prompt
        room = num_ctx - estimate_tokens(messages, tools) - 256
        opts: dict = {"num_ctx": num_ctx, "num_predict": max(256, min(max_tokens, room))}
        if temperature is not None:
            opts["temperature"] = temperature
        payload: dict = {"model": self.model, "messages": to_ollama_messages(messages, self.vision),
                         "stream": self.stream, "options": opts, "keep_alive": self.keep_alive}
        think = self._think_value()
        if think is not None:
            payload["think"] = think
        if tools:
            payload["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]
        try:
            try:
                data = await self._chat_request(payload)
            except ProviderError as e:
                if "think" in payload and "does not support thinking" in str(e):
                    self._no_think_field = True     # a model without a thinking mode rejects the field: drop it
                    payload.pop("think")
                    data = await self._chat_request(payload)
                else:
                    raise
        except ProviderError as e:
            msg = str(e)
            if e.status == 404 or "not found" in msg:
                raise ProviderError(f"{self.label} 本地模型尚未下載 (model not found): {msg}", e.status) from None
            if "does not support tools" in msg:
                raise ProviderError(f"{self.label} 這個本地模型不支援工具呼叫 (does not support tools)", e.status) from None
            raise
        msg = data.get("message") or {}
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                from .openai_compat import parse_arguments
                args = parse_arguments(args)
            calls.append(ToolCall(tc.get("id") or f"call_{i}_{uuid.uuid4().hex[:8]}", fn.get("name", ""), args))
        content = msg.get("content") or ""
        out_tok = int(data.get("eval_count") or 0)
        # eval_count includes the thinking; Ollama returns that text separately in message.thinking. Share the count
        # out by length so the agent can tell "thinking used up the limit" from an ordinary empty answer.
        thinking = msg.get("thinking") or ""
        think_tok = 0
        if thinking and out_tok:
            visible = len(content) + sum(len(str(c.arguments)) for c in calls)
            think_tok = out_tok if not visible else round(out_tok * len(thinking) / (len(thinking) + visible))
        usage = Usage(int(data.get("prompt_eval_count") or 0), out_tok, thinking_tokens=think_tok)
        reason = data.get("done_reason") or ("tool_calls" if calls else "stop")
        return ChatResponse(Message("assistant", content, calls), usage, reason, data.get("model", self.model))

    async def _chat_request(self, payload: dict) -> dict:
        """POST /api/chat. Streamed (the default), the wait has no overall limit: the read timeout only fires when
        Ollama goes quiet, never while the model is still thinking or writing - a local model may take as long as it
        needs. The chunks are joined into the shape of a non-streamed answer."""
        url = f"{self.base_url}/api/chat"
        if not payload.get("stream"):
            return await self._post(url, payload, self._headers(), timeout=self.call_timeout(
                (payload.get("options") or {}).get("num_predict")))
        import json as _json

        import httpx
        from .base import StreamMeter
        out: dict = {}
        content, thinking, calls = [], [], []
        meter = StreamMeter()
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=30.0)) as client:
            async with client.stream("POST", url, json=payload, headers=self._headers()) as r:
                if r.status_code >= 400:
                    text = (await r.aread()).decode("utf-8", "replace")[:2000]
                    raise self._http_error(r.status_code, text)
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        ev = _json.loads(line)
                    except ValueError:
                        continue
                    if ev.get("error"):
                        raise ProviderError(f"{self.label}: {ev['error']}", 500, False)
                    m = ev.get("message") or {}
                    if m.get("content") or m.get("thinking") or m.get("tool_calls"):
                        meter.add()
                    content.append(m.get("content") or "")
                    thinking.append(m.get("thinking") or "")
                    calls.extend(m.get("tool_calls") or [])
                    if ev.get("done"):
                        out = ev
        meter.done()
        out["message"] = {"role": "assistant", "content": "".join(content), "thinking": "".join(thinking),
                          "tool_calls": calls}
        return out

    async def embed(self, texts: list[str]) -> list[list[float]]:
        data = await self._post(f"{self.base_url}/api/embed",
                                {"model": self.model, "input": texts, "keep_alive": self.keep_alive}, self._headers())
        recorder = getattr(self, "recorder", None)
        if recorder:
            try:
                recorder(self, Usage(int(data.get("prompt_eval_count") or 0), 0), "embedding")
            except Exception:
                pass
        return data.get("embeddings") or []

    async def list_models(self) -> list[str]:
        data = await self._get(f"{self.base_url}/api/tags", self._headers())
        return sorted(m.get("name", "") for m in data.get("models") or [] if m.get("name"))


__all__ = ["OllamaProvider", "to_ollama_messages", "TextPart"]
