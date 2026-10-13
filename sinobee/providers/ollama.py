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
                 think: bool | None = None, **kw):
        kw.setdefault("timeout", 900.0)  # CPU inference and first-time model loads can be slow
        super().__init__(*a, **kw)
        self.base_url = self.base_url.removesuffix("/v1")
        self.vision = vision
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.think = think

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def _chat(self, messages, tools, *, temperature, max_tokens) -> ChatResponse:
        opts: dict = {"num_ctx": self.num_ctx, "num_predict": min(max_tokens, max(1024, self.num_ctx // 4))}
        if temperature is not None:
            opts["temperature"] = temperature
        payload: dict = {"model": self.model, "messages": to_ollama_messages(messages, self.vision),
                         "stream": False, "options": opts, "keep_alive": self.keep_alive}
        if self.think is not None:
            payload["think"] = self.think
        if tools:
            payload["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]
        try:
            data = await self._post(f"{self.base_url}/api/chat", payload, self._headers())
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
        usage = Usage(int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0))
        reason = data.get("done_reason") or ("tool_calls" if calls else "stop")
        return ChatResponse(Message("assistant", msg.get("content") or "", calls), usage, reason,
                            data.get("model", self.model))

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
