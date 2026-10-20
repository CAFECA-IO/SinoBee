"""OpenAI Chat Completions compatible transport.

Covers OpenAI, Azure-compatible gateways, OpenRouter, Groq, DeepSeek, Mistral, xAI, Together,
Fireworks, Moonshot, DashScope (Qwen), Zhipu, Gemini (OpenAI endpoint), Ollama, vLLM, LM Studio...
"""
from __future__ import annotations

import base64
import json
import re

from ..types import ChatResponse, ImagePart, Message, TextPart, ToolCall, Usage
from .base import Provider, ProviderError

_REASONING_MODEL = re.compile(r"^(o\d|gpt-5)", re.I)
# models that think before answering, and count the thinking against the output limit: a short limit (a JSON verdict
# asked with a few hundred tokens) leaves them nothing to answer with - Gemini 2.5 / 3 and their -latest aliases too
_THINKS = re.compile(r"^(o\d|gpt-5|gemini-(2\.5|[3-9])|gemini-[\w.-]*latest)", re.I)
THINKING_FLOOR = 2048


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

    def __init__(self, *a, vision: bool = True, stream: bool = False, **kw):
        super().__init__(*a, **kw)
        self.vision = vision
        # streamed answers (the SinoBee.ai relay): a long answer never looks idle to the proxy in front of it
        self.stream = stream

    async def _read_stream(self, r) -> dict:
        content: list[str] = []
        calls: dict[int, dict] = {}
        finish, model, usage = "", self.model, {}
        async for ev in self._sse(r):
            if ev.get("error"):
                err = ev["error"] if isinstance(ev["error"], dict) else {"message": str(ev["error"])}
                raise ProviderError(f"{self.label} stream error: {err.get('message', '')}", 500, True)
            model = ev.get("model") or model
            if ev.get("usage"):
                usage = ev["usage"]
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or {}
                if isinstance(d.get("content"), str):
                    content.append(d["content"])
                for tc in d.get("tool_calls") or []:
                    # a service that sends no index (Gemini) sends each call whole: a new id is a new call
                    idx = tc.get("index")
                    if idx is None:
                        last = max(calls) if calls else -1
                        idx = last + 1 if (tc.get("id") or last < 0 or (tc.get("function") or {}).get("name")) else last
                    c = calls.setdefault(int(idx), {"id": None, "name": "", "args": [], "extra": None})
                    c["id"] = tc.get("id") or c["id"]
                    fn = tc.get("function") or {}
                    c["name"] += fn.get("name") or ""
                    c["args"].append(fn.get("arguments") or "")
                    c["extra"] = tc.get("extra_content") or c["extra"]
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
        if finish == "quota":                     # the relay ended the answer: the plan's window is used up
            from .base import QuotaExceeded
            raise QuotaExceeded(f"{self.label} SinoBee.ai quota used up while answering", "6h")
        tool_calls = [{"id": c["id"] or f"call_{i}", "type": "function", "extra_content": c["extra"],
                       "function": {"name": c["name"], "arguments": "".join(c["args"])}}
                      for i, c in sorted(calls.items())]
        return {"model": model, "usage": usage, "choices": [{"finish_reason": finish, "message": {
            "role": "assistant", "content": "".join(content), "tool_calls": tool_calls}}]}

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
        if _THINKS.match(self.model.split("/")[-1]) and max_tokens < THINKING_FLOOR:
            max_tokens = THINKING_FLOOR        # the visible answer stays as short as the caller asked; only room to think
        if self.provider_name == "openai" or reasoning:
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["max_tokens"] = max_tokens
        if temperature is not None and not reasoning:
            payload["temperature"] = temperature
        from . import compat
        from .base import REASONING_EFFORT
        effort = REASONING_EFFORT.get()
        if effort and (reasoning or _THINKS.match(self.model.split("/")[-1])) and \
                "reasoning_effort" not in compat.dropped(self.label):
            payload["reasoning_effort"] = effort
        if tools:
            payload["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]
            payload["tool_choice"] = "auto"
        if self.stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        data = await self._post(f"{self.base_url}/chat/completions", payload, self._headers(),
                                timeout=self.call_timeout(max_tokens))
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
