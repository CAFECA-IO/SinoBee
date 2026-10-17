"""OpenAI Chat Completions compatible transport.

Covers OpenAI, Azure-compatible gateways, OpenRouter, Groq, DeepSeek, Mistral, xAI, Together,
Fireworks, Moonshot, DashScope (Qwen), Zhipu, Gemini (OpenAI endpoint), Ollama, vLLM, LM Studio...
"""
from __future__ import annotations

import base64
import json
import re

from ..types import ChatResponse, ImagePart, Message, TextPart, ToolCall, ToolSpec, Usage
from .base import Provider

_REASONING_MODEL = re.compile(r"^(o\d|gpt-5)", re.I)


def _parts_to_openai(content) -> str | list:
    if isinstance(content, str):
        return content
    out = []
    for p in content:
        if isinstance(p, TextPart):
            out.append({"type": "text", "text": p.text})
        elif isinstance(p, ImagePart):
            out.append({"type": "image_url", "image_url": {"url": f"data:{p.media_type};base64,{p.data}"}})
    return out


# Gemini 3+ (OpenAI-compatible endpoint) requires the thought signature it returned on a function call to be sent
# back with that call. For history that has none (older sessions, another model), Google accepts this placeholder.
DUMMY_THOUGHT_SIGNATURE = base64.b64encode(b"skip_thought_signature_validator").decode()


def to_openai_messages(messages: list[Message], vision: bool = True, gemini: bool = False) -> list[dict]:
    out: list[dict] = []
    pending_images: list[ImagePart] = []

    def flush_images():
        if pending_images:
            parts = [{"type": "text", "text": "[Images returned by the previous tool call(s)]"}]
            parts += [{"type": "image_url", "image_url": {"url": f"data:{i.media_type};base64,{i.data}"}}
                      for i in pending_images]
            out.append({"role": "user", "content": parts})
            pending_images.clear()

    for m in messages:
        if m.role != "tool":
            flush_images()
        if m.role == "tool":
            text = m.text() or ("(see image)" if m.has_images() else "")
            if isinstance(m.content, list) and vision:
                pending_images.extend(p for p in m.content if isinstance(p, ImagePart))
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": text})
        elif m.role == "assistant":
            if not m.text().strip() and not m.tool_calls:
                continue  # empty replies are invalid for several APIs
            d: dict = {"role": "assistant", "content": m.text() or None}
            if m.tool_calls:
                d["tool_calls"] = []
                for i, t in enumerate(m.tool_calls):
                    tc = {"id": t.id, "type": "function",
                          "function": {"name": t.name, "arguments": json.dumps(t.arguments, ensure_ascii=False)}}
                    if t.extra:
                        tc["extra_content"] = t.extra
                    elif gemini and i == 0:
                        tc["extra_content"] = {"google": {"thought_signature": DUMMY_THOUGHT_SIGNATURE}}
                    d["tool_calls"].append(tc)
            out.append(d)
        else:
            content = m.content
            if not vision and isinstance(content, list):
                content = m.text()
            out.append({"role": m.role, "content": _parts_to_openai(content)})
    flush_images()
    return out


def parse_arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {"value": val}
    except json.JSONDecodeError:
        # LaTeX written without doubling its backslashes ("\\gamma", "\\sqrt"): keep the backslash as text
        fixed = re.sub(r'(?<!\\)((?:\\\\)*)\\(?!["\\/bfnrtu])', r"\1\\\\", raw)
        if fixed != raw:
            try:
                val = json.loads(fixed)
                return val if isinstance(val, dict) else {"value": val}
            except json.JSONDecodeError:
                pass
        # tolerate trailing garbage from weaker models
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return {"_raw": raw}


class OpenAICompatProvider(Provider):
    name = "openai"

    def __init__(self, *a, vision: bool = True, **kw):
        super().__init__(*a, **kw)
        self.vision = vision

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if self.provider_name == "openrouter":
            h["HTTP-Referer"] = "https://sinobee.ai"
            h["X-Title"] = "SinoBee"
        return h

    async def _chat(self, messages, tools, *, temperature, max_tokens) -> ChatResponse:
        gemini = self.provider_name == "gemini" or "generativelanguage.googleapis.com" in self.base_url
        payload: dict = {"model": self.model, "messages": to_openai_messages(messages, self.vision, gemini)}
        reasoning = _REASONING_MODEL.match(self.model.split("/")[-1])
        if self.provider_name == "openai" or reasoning:
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["max_tokens"] = max_tokens
        if temperature is not None and not reasoning:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]
            payload["tool_choice"] = "auto"
        data = await self._post(f"{self.base_url}/chat/completions", payload, self._headers())
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            calls.append(ToolCall(tc.get("id") or f"call_{i}", fn.get("name", ""), parse_arguments(fn.get("arguments")),
                                  tc.get("extra_content") or None))
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        u = data.get("usage") or {}
        usage = Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                      (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0)
        return ChatResponse(Message("assistant", content, calls), usage, choice.get("finish_reason") or "",
                            data.get("model", self.model))

    async def embed(self, texts: list[str]) -> list[list[float]]:
        data = await self._post(f"{self.base_url}/embeddings", {"model": self.model, "input": texts}, self._headers())
        recorder = getattr(self, "recorder", None)
        if recorder:
            try:
                recorder(self, Usage((data.get("usage") or {}).get("prompt_tokens", 0), 0), "embedding")
            except Exception:
                pass
        return [d["embedding"] for d in sorted(data["data"], key=lambda d: d.get("index", 0))]

    async def list_models(self) -> list[str]:
        data = await self._get(f"{self.base_url}/models", self._headers())
        items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
        ids = [(m.get("id") or m.get("name", "")) if isinstance(m, dict) else str(m) for m in items]
        return sorted(i.removeprefix("models/") for i in ids if i)
