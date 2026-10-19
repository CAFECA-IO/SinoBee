"""Answers are streamed, so a long answer never looks idle to the proxy in front of the SinoBee.ai relay (Cloudflare
cuts requests that send nothing for ~100 s with a 504 - the wind-turbine task lost five attempts at writing its page
that way, each charged by the relay). A 504 from a proxy is retried once, not four times."""
import json

import httpx
import pytest

from sinobee.providers.base import Provider, ProviderError, QuotaExceeded
from sinobee.types import Message, ToolSpec


def sse(*events, comment=None):
    out = "".join(f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events)
    return out + (f": x-sinobee-quota {json.dumps(comment)}\n\n" if comment else "")


ANTHROPIC = [
    {"type": "message_start", "message": {"id": "m1", "model": "claude-sonnet-5-5", "usage": {
        "input_tokens": 120, "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 50, "output_tokens": 1}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "先寫骨架"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "先寫"}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "頁面骨架。"}},
    {"type": "content_block_stop", "index": 1},
    {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "tu_1", "name": "write_file", "input": {}}},
    {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"path": "wind.ht'}},
    {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": 'ml", "content": "<h1>風機</h1>"}'}},
    {"type": "content_block_stop", "index": 2},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 812}},
    {"type": "message_stop"},
]


def client(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))


def anthropic(relay=True):
    from sinobee.providers.anthropic import AnthropicProvider
    if relay:
        return AnthropicProvider("anthropic/claude-sonnet-5-5", "https://relay.invalid/anthropic", None,
                                 provider_name="sinobee", auth=lambda m, u, b: {"authorization": "Bearer t"})
    return AnthropicProvider("claude-sonnet-5-5", "https://api.anthropic.invalid", "k")


async def test_an_anthropic_answer_is_streamed_and_assembled(home, monkeypatch):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=sse(*ANTHROPIC, comment={"x-sinobee-cost-6h-pct": "1.25", "x-sinobee-quota-6h": "40",
                                                            "x-sinobee-quota-168h": "4"}))
    client(monkeypatch, handler)
    from sinobee.accounting import UsageScope, current_scope
    sc = UsageScope()
    tok = current_scope.set(sc)
    try:
        r = await anthropic().chat([Message("user", "寫風機頁面")], [ToolSpec("write_file", "w", {"type": "object"})])
    finally:
        current_scope.reset(tok)
    assert seen[0]["stream"] is True
    assert r.message.text() == "先寫頁面骨架。"                        # the thinking block is not part of the answer
    assert r.message.tool_calls[0].name == "write_file"
    assert r.message.tool_calls[0].arguments == {"path": "wind.html", "content": "<h1>風機</h1>"}
    assert r.stop_reason == "tool_use" and r.model == "claude-sonnet-5-5"
    assert r.usage.output_tokens == 812 and r.usage.input_tokens == 120 + 3000 + 50
    assert r.usage.cache_read_tokens == 3000
    assert sc.quota_6h_pct == 1.25 and sc.quota == {"6h": 40.0, "168h": 4.0}    # from the closing SSE comment


async def test_direct_anthropic_streams_too_and_a_plain_json_answer_still_works(home, monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        assert body["stream"] is True
        return httpx.Response(200, json={"content": [{"type": "text", "text": "好"}], "stop_reason": "end_turn",
                                         "usage": {"input_tokens": 3, "output_tokens": 1}})
    client(monkeypatch, handler)
    r = await anthropic(relay=False).chat([Message("user", "hi")])
    assert r.message.text() == "好" and r.usage.output_tokens == 1


async def test_stream_errors(home, monkeypatch):
    plan = {}

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse(*plan["events"]))
    client(monkeypatch, handler)
    # overloaded mid-answer: a retryable error (the chat() retry loop sends it again)
    plan["events"] = [ANTHROPIC[0], {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}]
    with pytest.raises(ProviderError) as e:
        await anthropic()._chat([Message("user", "x")], [], temperature=None, max_tokens=100)
    assert e.value.retryable and "overloaded_error" in str(e.value)
    # the relay ends the stream when the plan's window runs out: a quota stop, not an empty answer
    plan["events"] = [ANTHROPIC[0], ANTHROPIC[4], ANTHROPIC[5],
                      {"type": "message_delta", "delta": {"stop_reason": "quota"}, "usage": {"output_tokens": 3}}]
    with pytest.raises(QuotaExceeded):
        await anthropic()._chat([Message("user", "x")], [], temperature=None, max_tokens=100)


async def test_relay_openai_models_stream_tool_calls(home, monkeypatch):
    from sinobee.providers.sinobee import SinoBeeProvider, _FORMATS
    _FORMATS["openai/gpt-5.6-luna"] = "openai"
    seen = []
    chunks = [
        {"id": "c", "model": "gpt-5.6-luna", "choices": [{"index": 0, "delta": {"content": "寫"}}]},
        {"id": "c", "choices": [{"index": 0, "delta": {"content": "檔案"}}]},
        {"id": "c", "choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_a", "function": {"name": "write_file", "arguments": '{"path":'}}]}}]},
        {"id": "c", "choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": ' "a.md", "content": "x"}'}}]}}]},
        {"id": "c", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"id": "c", "choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 9,
                                             "prompt_tokens_details": {"cached_tokens": 20}}},
    ]

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text="".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n")
    client(monkeypatch, handler)
    p = SinoBeeProvider("openai/gpt-5.6-luna", "https://relay.invalid/api/v1/llm")
    p.inner().auth = lambda m, u, b: {"authorization": "Bearer t"}
    r = await p.inner().chat([Message("user", "x")], [ToolSpec("write_file", "w", {"type": "object"})])
    assert seen[0]["stream"] is True and seen[0]["stream_options"] == {"include_usage": True}
    assert r.message.text() == "寫檔案" and r.stop_reason == "tool_calls"
    assert r.message.tool_calls[0].id == "call_a" and r.message.tool_calls[0].arguments == {"path": "a.md", "content": "x"}
    assert (r.usage.input_tokens, r.usage.output_tokens, r.usage.cache_read_tokens) == (50, 9, 20)


class _Gateway(Provider):
    def __init__(self, err):
        super().__init__("m", "https://x.invalid", None, provider_name="t")
        self.err, self.calls = err, 0

    async def _chat(self, messages, tools, *, temperature, max_tokens):
        self.calls += 1
        raise self.err


async def test_a_proxy_timeout_is_retried_once_other_server_errors_four_times(home, monkeypatch):
    from sinobee.providers import base

    async def no_wait(_s):
        return None
    import asyncio
    import types
    monkeypatch.setattr(base, "asyncio", types.SimpleNamespace(**{**vars(asyncio), "sleep": no_wait}))
    cf = "<title>sinobee.ai | 504: Gateway time-out</title>"
    for err, calls in [(ProviderError(f"m HTTP 504: {cf}", 504, True), 2),
                       (ProviderError("m HTTP 524: A timeout occurred", 524, True), 2),
                       (ProviderError("m HTTP 502: bad gateway", 502, True), 5),
                       (ProviderError("m HTTP 529: overloaded", 529, True), 5)]:
        p = _Gateway(err)
        with pytest.raises(ProviderError):
            await p.chat([Message("user", "x")])
        assert p.calls == calls, (str(err), p.calls)


def test_the_504_is_explained():
    from sinobee.agent.errors import explain_error
    e = explain_error("sinobee/anthropic/claude-sonnet-5-5 HTTP 504: <!DOCTYPE html>\n<html>\n"
                      "<title>sinobee.ai | 504: Gateway time-out</title>")
    assert "被中間的代理伺服器切斷" in e["summary"] and "分段寫檔" in e["hint"]
    assert explain_error("m HTTP 503: overloaded")["summary"].startswith("連不到模型服務")
