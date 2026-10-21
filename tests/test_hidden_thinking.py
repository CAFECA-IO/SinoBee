"""Claude 4.6+ think by default and hide it: a big write can spend the whole output limit thinking and come back empty
(Roadster Rocket, 2026-10-21: claude-haiku-5-5 used 8192 / 16384 / 32000 output tokens, all thinking). After an empty
answer the agent turns thinking down step by step - effort medium, then low, then off (Anthropic: output_config.effort
where the relay passes it, else thinking disabled) - and only when thinking took the room, not when a long visible
answer was cut. A call cut inside its arguments is passed on as cut, so the model is told to write in parts."""
import asyncio
import json

import httpx
import pytest

from sinobee.providers import base, compat
from sinobee.providers.anthropic import AnthropicProvider
from sinobee.types import Message, ToolCall, ToolSpec


def sse(events: list[tuple[str, dict]]) -> httpx.Response:
    body = "".join(f"event: {k}\ndata: {json.dumps({'type': k, **d})}\n\n" for k, d in events).encode()
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


THINKING_ONLY = [
    ("message_start", {"message": {"id": "m", "model": "claude-haiku-5-5", "usage": {"input_tokens": 10}}}),
    ("content_block_start", {"index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
    ("content_block_delta", {"index": 0, "delta": {"type": "signature_delta", "signature": "sig"}}),
    ("content_block_stop", {"index": 0}),
    ("message_delta", {"delta": {"stop_reason": "max_tokens"},
                       "usage": {"output_tokens": 8192, "output_tokens_details": {"thinking_tokens": 8192}}}),
    ("message_stop", {}),
]

CUT_CALL = [
    ("message_start", {"message": {"id": "m", "model": "claude-haiku-5-5", "usage": {"input_tokens": 10}}}),
    ("content_block_start", {"index": 0, "content_block": {"type": "tool_use", "id": "t1", "name": "write_file"}}),
    ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"path": "a.html", "content": "<html>'}}),
    ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": "x" * 500}}),
    # max_tokens inside the arguments: no content_block_stop
    ("message_delta", {"delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 8192}}),
    ("message_stop", {}),
]


@pytest.fixture
def anthropic(monkeypatch, tmp_path):
    monkeypatch.setenv("SINOBEE_HOME", str(tmp_path))
    compat._cache = None
    seen: list[dict] = []
    script: list = []
    real = httpx.AsyncClient

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return script.pop(0)(req)

    monkeypatch.setattr(base.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return AnthropicProvider("claude-haiku-5-5", "https://api.test", "k"), seen, script


def tools():
    return [ToolSpec("write_file", "Create a file.", {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}})]


def test_hidden_thinking_is_counted_and_turned_down_by_effort_or_off(anthropic):
    p, seen, script = anthropic
    script += [lambda req: sse(THINKING_ONLY)] * 4
    r = asyncio.run(p.chat([Message("user", "write the game")], tools()))
    assert r.message.text() == "" and not r.message.tool_calls and r.stop_reason == "max_tokens"
    assert r.usage.thinking_tokens == 8192 == r.usage.output_tokens
    assert "thinking" not in seen[0], "the model's default when nothing asks for less"

    async def at(level):
        tok = base.REASONING_EFFORT.set(level)
        try:
            return await p.chat([Message("user", "write the game")], tools())
        finally:
            base.REASONING_EFFORT.reset(tok)
    # own key (no relay): effort is set directly
    asyncio.run(at("medium"))
    assert seen[1]["output_config"] == {"effort": "medium"} and "thinking" not in seen[1]
    asyncio.run(at("none"))
    assert seen[2]["thinking"] == {"type": "disabled"} and "output_config" not in seen[2]
    # through a relay that has not said it passes output_config: thinking off rather than a silent no-op
    p.auth = lambda m, u, b: {}
    base.RELAY_FEATURES.discard("output_config")
    asyncio.run(at("low"))
    assert seen[3]["thinking"] == {"type": "disabled"} and "output_config" not in seen[3]


def test_a_model_that_refuses_effort_is_remembered_and_thinking_goes_off_instead(anthropic):
    p, seen, script = anthropic
    err = {"type": "error", "error": {"type": "invalid_request_error", "message": "output_config: Extra inputs are not permitted"}}
    err2 = {"type": "error", "error": {"type": "invalid_request_error", "message": '"thinking.type.disabled" is not supported for this model.'}}
    script += [lambda req: httpx.Response(400, json=err), lambda req: httpx.Response(400, json=err2), lambda req: sse(THINKING_ONLY),
               lambda req: sse(THINKING_ONLY)]

    async def low():
        tok = base.REASONING_EFFORT.set("low")
        try:
            await p.chat([Message("user", "x")], tools())
            await p.chat([Message("user", "x")], tools())
        finally:
            base.REASONING_EFFORT.reset(tok)
    asyncio.run(low())
    assert seen[0]["output_config"] == {"effort": "low"}
    assert seen[1]["thinking"] == {"type": "disabled"} and "output_config" not in seen[1], "no effort control: off"
    assert "thinking" not in seen[2] and "thinking" not in seen[3], "nor that: the model's default"
    assert {"output_config", "thinking"} <= compat.dropped(p.label)


def test_a_call_cut_inside_its_arguments_tells_the_model_to_write_in_parts(anthropic, config):
    p, seen, script = anthropic
    script.append(lambda req: sse(CUT_CALL))
    r = asyncio.run(p.chat([Message("user", "x")], tools()))
    [call] = r.message.tool_calls
    assert call.arguments["_truncated"] > 500
    from sinobee.tools import REGISTRY
    from sinobee.tools.registry import ToolContext
    res = asyncio.run(REGISTRY.dispatch(ToolCall("t1", "write_file", call.arguments), ToolContext(config=config)))
    assert res.is_error and "cut off at the output limit" in res.content and "smaller parts" in res.content


def test_the_agent_says_thinking_used_the_limit_and_turns_it_down(config):
    from sinobee.agent import Agent, Runtime
    from sinobee.types import ChatResponse, Usage

    class Thinker:
        label = "test/thinker"
        provider_name = "anthropic"
        model = "claude-haiku-5-5"
        recorder = None

        def __init__(self):
            self.efforts = []

        async def chat(self, messages, tools=None, **kw):
            self.efforts.append(base.REASONING_EFFORT.get())
            if len(self.efforts) == 1:
                return ChatResponse(Message("assistant", ""), Usage(1000, 8192, 0, 0, 8192), "max_tokens", self.model)
            return ChatResponse(Message("assistant", "好了"), Usage(1000, 50), "end_turn", self.model)

    p = Thinker()
    seen = []
    out = asyncio.run(Agent(Runtime(config, provider=p), session_id="think", emit=lambda k, d: seen.append((k, d))).run("寫一頁"))
    assert "好了" in out
    assert p.efforts[:2] == [None, "medium"], "the next step thinks less, not nothing"
    assert any("隱藏的思考" in str(d) for k, d in seen if k == "status")


def test_thinking_is_turned_down_further_each_time_and_not_for_a_visible_cut(config):
    from sinobee.agent import Agent, Runtime
    from sinobee.types import ChatResponse, Usage

    class P:
        label = "test/p"
        provider_name = "anthropic"
        model = "m"
        recorder = None

        def __init__(self, replies):
            self.replies, self.efforts = list(replies), []

        async def chat(self, messages, tools=None, **kw):
            self.efforts.append(base.REASONING_EFFORT.get())
            return self.replies.pop(0) if self.replies else ChatResponse(Message("assistant", "好了"), Usage(10, 5), "end_turn", "m")

    def empty(out, think):
        return ChatResponse(Message("assistant", ""), Usage(1000, out, 0, 0, think), "max_tokens", "m")

    p = P([empty(8192, 8192), empty(16384, 16000)])
    asyncio.run(Agent(Runtime(config, provider=p), session_id="t2").run("寫一頁"))
    assert p.efforts[:3] == [None, "medium", "low"]

    # thinking took a third of it: the answer itself was long - more room and write in parts, thinking kept
    p = P([empty(8192, 3000)])
    asyncio.run(Agent(Runtime(config, provider=p), session_id="t3").run("寫一頁"))
    assert p.efforts[:2] == [None, None]


def test_effort_goes_through_the_relay_once_it_says_it_passes_it(anthropic):
    p, seen, script = anthropic
    p.auth = lambda m, u, b: {}
    base.RELAY_FEATURES.discard("output_config")
    p._relay_headers({"x-sinobee-relay-features": "output_config"})
    tok = base.REASONING_EFFORT.set("low")
    try:
        assert p.thinking_params() == {"output_config": {"effort": "low"}}
    finally:
        base.REASONING_EFFORT.reset(tok)
        base.RELAY_FEATURES.discard("output_config")


def test_openai_compatible_models_get_low_for_every_turned_down_level(monkeypatch):
    from sinobee.providers.openai_compat import OpenAICompatProvider
    sent = []

    async def post(self, url, payload, headers, timeout=None):
        sent.append(payload.get("reasoning_effort"))
        return {"choices": [{"message": {"content": "ok"}}], "usage": {"completion_tokens": 900, "completion_tokens_details": {"reasoning_tokens": 800}}}
    monkeypatch.setattr(OpenAICompatProvider, "_post", post)
    p = OpenAICompatProvider("gpt-5.6-luna", "https://example.invalid/v1", "k", provider_name="openai")
    for level in ("medium", "low", "none"):
        tok = base.REASONING_EFFORT.set(level)
        try:
            r = asyncio.run(p.chat([Message("user", "hi")], max_tokens=1000))
        finally:
            base.REASONING_EFFORT.reset(tok)
    assert sent == ["low", "low", "low"]
    assert r.usage.thinking_tokens == 800


def test_the_sinobee_provider_reports_its_inner_thinking_settings(config, monkeypatch):
    from sinobee.providers.sinobee import SinoBeeProvider
    p = SinoBeeProvider("anthropic/claude-haiku-5-5", "https://sb.test/api/v1/llm", config=config)
    monkeypatch.setattr("sinobee.providers.sinobee.guess_format", lambda m: "anthropic")
    base.RELAY_FEATURES.discard("output_config")
    tok = base.REASONING_EFFORT.set("low")
    try:
        assert p.thinking_params() == {"thinking": {"type": "disabled"}}, "an old relay: off rather than a silent no-op"
    finally:
        base.REASONING_EFFORT.reset(tok)
