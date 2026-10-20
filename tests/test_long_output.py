"""A long answer (a whole HTML page with hidden reasoning) on a thinking model: twice finish=length with nothing
visible, then a non-streamed call that ran past the 180 s read timeout and was reported as a network outage
(gemini-flash-latest, 2026-10-20). Calls now wait in proportion to their output limit, a truncated empty reply asks
for the file in parts, and the failure report says what happened."""
from sinobee.agent import Agent, Runtime
from sinobee.agent import failures as F
from sinobee.agent.errors import explain_error
from sinobee.agent.loop import REASONING_MODEL_RE
from sinobee.types import ChatResponse, Message, Usage


class Truncating:
    label = "gemini/gemini-flash-latest"
    provider_name = "gemini"
    model = "gemini-flash-latest"

    def __init__(self, n_empty):
        self.n_empty = n_empty
        self.calls = []
        self.recorder = None

    async def chat(self, messages, tools=None, **kw):
        self.calls.append({"max_tokens": kw.get("max_tokens"), "tail": messages[-1].text()})
        if len(self.calls) <= self.n_empty:
            return ChatResponse(Message("assistant", ""), Usage(1000, 3), "length", self.model)
        return ChatResponse(Message("assistant", "完成：已分段寫好"), Usage(1000, 50), "stop", self.model)

    async def embed(self, texts):
        raise RuntimeError("no embeddings")


async def test_a_truncated_empty_reply_asks_for_the_file_in_parts(config):
    p = Truncating(2)
    agent = Agent(Runtime(config, provider=p))
    out = await agent.run("用動畫展示風力發電機")
    assert out == "完成：已分段寫好"
    assert "Do not write a large file in one call" in p.calls[1]["tail"]
    assert p.calls[1]["max_tokens"] == 2 * p.calls[0]["max_tokens"]


async def test_the_failure_report_keeps_the_empty_replies(config):
    p = Truncating(5)
    agent = Agent(Runtime(config, provider=p))
    await agent.run("用動畫展示風力發電機")
    rep = F.get(config.home, F.listing(config.home, 1)[0]["id"])
    assert [e["finish"] for e in rep["empty_replies"]] == ["length"] * 3
    assert "EMPTY REPLIES" in F.summary_text(rep) and "max_tokens=" in F.summary_text(rep)


def test_gemini_latest_models_get_the_reasoning_budget():
    for m in ("gemini/gemini-flash-latest", "gemini/gemini-pro-latest", "gemini/gemini-3.8-flash"):
        assert REASONING_MODEL_RE.search(m), m
    assert not REASONING_MODEL_RE.search("openai/gpt-4.1")


async def test_calls_wait_in_proportion_to_their_output_limit(monkeypatch):
    from sinobee.providers.openai_compat import OpenAICompatProvider
    seen = []

    async def post(self, url, payload, headers, timeout=None):
        seen.append(timeout)
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}
    monkeypatch.setattr(OpenAICompatProvider, "_post", post)
    p = OpenAICompatProvider("gemini-flash-latest", "https://example.invalid/v1", "k", provider_name="gemini")
    for n in (1000, 8192, 32768):
        await p._chat([Message("user", "hi")], None, temperature=0, max_tokens=n)
    assert seen[0] == p.timeout == 180
    assert seen[1] > 180 and seen[2] >= 700


def test_a_read_timeout_is_not_called_a_network_outage():
    e = explain_error("gemini/gemini-flash-latest timed out: ReadTimeout('')")
    assert "沒有回完" in e["summary"] and "分段" in e["hint"]
    assert "連不到" in explain_error("gemini/x network error: ConnectError('refused')")["summary"]


async def test_streamed_tool_calls_without_an_index_stay_apart(monkeypatch):
    """Gemini streams each tool call whole and without "index"; argument pieces of one call (OpenAI) still join."""
    from sinobee.providers.openai_compat import OpenAICompatProvider
    p = OpenAICompatProvider("gemini-flash-latest", "https://example.invalid/v1", "k", provider_name="gemini", stream=True)
    events = [{"choices": [{"delta": {"tool_calls": [
                  {"id": "a", "function": {"name": "get_weather", "arguments": '{"city":"台北"}'}, "extra_content": {"s": 1}},
                  {"id": "b", "function": {"name": "get_weather", "arguments": '{"city":"高雄"}'}}]}}]},
              {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"completion_tokens": 9}}]

    async def sse(_r):
        for e in events:
            yield e
    monkeypatch.setattr(p, "_sse", sse)
    data = await p._read_stream(None)
    calls = data["choices"][0]["message"]["tool_calls"]
    assert [c["function"]["arguments"] for c in calls] == ['{"city":"台北"}', '{"city":"高雄"}']
    assert calls[0]["extra_content"] == {"s": 1} and data["usage"]["completion_tokens"] == 9
    events[:] = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "x", "function": {"name": "f", "arguments": '{"a":'}}]}}]},
                 {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]}, "finish_reason": "tool_calls"}]}]
    data = await p._read_stream(None)
    assert [c["function"]["arguments"] for c in data["choices"][0]["message"]["tool_calls"]] == ['{"a":1}']


def test_openai_compatible_services_stream_by_default(config):
    from sinobee.providers import build_provider
    assert build_provider("gemini/gemini-flash-latest", config).stream is True
    config.data["providers"] = {"gemini": {"stream": False}}
    assert build_provider("gemini/gemini-flash-latest", config).stream is False
