"""Proposal 'large tasks no longer stop half-way', phase 1 (2026-10-20): output limits per model (U8), room for the
thinking model's short calls and a visible plan failure (U1), less thinking after a reply was cut off (U3), and the
section rule with size reminders (U2)."""
import json

import pytest

from sinobee.agent import Agent, Runtime
from sinobee.agent import loop as L
from sinobee.providers import limits
from sinobee.providers.base import REASONING_EFFORT, ProviderError
from sinobee.types import ChatResponse, Message, ToolCall, Usage

from conftest import ScriptedProvider


class Model:
    """A fake model with a real-looking label; records max_tokens and the reasoning effort of every call."""
    def __init__(self, label, replies):
        self.label = label
        self.provider_name, self.model = label.split("/", 1)
        self.replies = list(replies)
        self.calls = []
        self.recorder = None

    async def chat(self, messages, tools=None, **kw):
        self.calls.append({"max_tokens": kw.get("max_tokens"), "effort": REASONING_EFFORT.get(),
                           "tail": messages[-1].text()})
        r = self.replies.pop(0) if self.replies else ("done", "stop", 50)
        text, finish, out = r if isinstance(r, tuple) else (r, "stop", 50)
        msg = text if isinstance(text, Message) else Message("assistant", text)
        return ChatResponse(msg, Usage(1000, out), finish, self.model)

    async def embed(self, texts):
        raise RuntimeError("no embeddings")


# ---------------------------------------------------------------- U8
def test_the_default_output_limit_follows_the_model(home, config, monkeypatch):
    monkeypatch.delenv("SINOBEE_MAX_OUTPUT_TOKENS", raising=False)
    assert limits.max_output("gemini/gemini-flash-latest") == 65536
    assert limits.default_output(config, "gemini/gemini-flash-latest", 16384) == 32768       # half, at most 32k
    assert limits.default_output(config, "anthropic/claude-opus-5-5", 8192) == 32768
    assert limits.max_output("anthropic/claude-opus-5-5") == 128000
    assert limits.default_output(config, "acme/unknown-model", 8192) == 8192               # unknown: as before
    config.data["model"]["max_output_tokens"] = 100000                                      # cut to the model's max
    assert limits.default_output(config, "gemini/gemini-flash-latest", 16384) == 65536
    monkeypatch.setenv("SINOBEE_MAX_OUTPUT_TOKENS", "20000")                               # the environment wins
    assert limits.default_output(config, "gemini/gemini-flash-latest", 16384) == 20000


async def test_the_limit_is_read_from_the_providers_model_api_once(home, config, monkeypatch):
    from sinobee.providers.openai_compat import OpenAICompatProvider
    seen = []

    class Resp:
        status_code = 200

        def json(self):
            return {"outputTokenLimit": 65536, "inputTokenLimit": 1048576}

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            seen.append((url, headers))
            return Resp()
    monkeypatch.setattr(limits.httpx, "AsyncClient", Client)
    p = OpenAICompatProvider("gemini-3.9-flash", "https://generativelanguage.googleapis.com/v1beta/openai", "k",
                             provider_name="gemini")
    assert await limits.discover(p) == 65536
    assert await limits.discover(p) is None and len(seen) == 1                           # remembered
    assert seen[0][0].endswith("/v1beta/models/gemini-3.9-flash") and seen[0][1] == {"x-goog-api-key": "k"}
    assert "key=" not in seen[0][0]                                                      # never in the URL
    assert limits.max_output("gemini/gemini-3.9-flash") == 65536


def test_settings_show_and_take_the_output_limit(home, config):
    from sinobee.server.settings_api import _context_info
    config.data["model"]["default"] = "gemini/gemini-flash-latest"
    info = _context_info(config)
    assert info["output"] == 32768 and info["output_max"] == 65536 and info["output_source"] == "model"


# ---------------------------------------------------------------- U3 + U8 in the loop
async def test_a_cut_off_reply_gets_the_models_max_and_less_thinking(home, config):
    m = Model("gemini/gemini-flash-latest", [("", "length", 0), ("", "length", 0), "完成"])
    agent = Agent(Runtime(config, provider=m))
    out = await agent.run("用動畫展示風力發電機")
    assert out == "完成"
    assert [c["max_tokens"] for c in m.calls] == [32768, 65536, 65536]
    assert [c["effort"] for c in m.calls] == [None, "low", "low"]
    assert "Do not write a large file in one call" in m.calls[1]["tail"]
    assert REASONING_EFFORT.get() is None                                          # never leaks out of the step


async def test_an_unknown_model_keeps_the_old_values(home, config):
    m = Model("acme/some-model", [("", "length", 0), "ok"])
    await Agent(Runtime(config, provider=m)).run("hi")
    assert [c["max_tokens"] for c in m.calls] == [8192, 16384]


async def test_reasoning_effort_is_sent_to_thinking_models_and_learned_when_refused(home, config, monkeypatch):
    from sinobee.providers import compat
    from sinobee.providers.openai_compat import OpenAICompatProvider
    sent = []

    async def post(self, url, payload, headers, timeout=None):
        sent.append(dict(payload))
        if payload.get("reasoning_effort") and len(sent) == 1:
            raise ProviderError("gemini/x HTTP 400: Unknown parameter: 'reasoning_effort'", 400)
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}
    monkeypatch.setattr(OpenAICompatProvider, "_post", post)
    p = OpenAICompatProvider("gemini-flash-latest", "https://example.invalid/v1", "k", provider_name="gemini")
    tok = REASONING_EFFORT.set("low")
    try:
        r = await p.chat([Message("user", "hi")], max_tokens=1000)
        assert r.message.text() == "ok"
        assert "reasoning_effort" in sent[0] and "reasoning_effort" not in sent[1]
        assert "reasoning_effort" in compat.dropped(p.label)
        g = OpenAICompatProvider("gpt-4.1", "https://example.invalid/v1", "k", provider_name="openai")
        await g.chat([Message("user", "hi")], max_tokens=1000)
        assert "reasoning_effort" not in sent[-1]                       # not a thinking model
    finally:
        REASONING_EFFORT.reset(tok)


# ---------------------------------------------------------------- U1
def test_thinking_calls_on_reasoning_models_get_room():
    assert L.thinking_room("gemini/gemini-pro-latest", 1500) == 8192
    assert L.thinking_room("gemini/gemini-pro-latest", 20000) == 20000
    assert L.thinking_room("openai/gpt-4.1", 1500) == 1500


async def test_a_plan_cut_off_is_asked_again_and_a_missing_plan_is_said(home, config):
    config.data["agent"]["plan"] = "always"
    plan = json.dumps({"approach": "先寫骨架", "steps": ["骨架", "填區塊"], "checklist": [], "task_type": "media"})
    think = Model("gemini/gemini-pro-latest", [("", "length", 0), plan])
    exe = Model("gemini/gemini-flash-latest", ["完成"])
    events = []
    rt = Runtime(config, provider=exe, thinking_provider=think)
    agent = Agent(rt, emit=lambda kind, data: events.append((kind, data)))
    await agent.run("用動畫展示風力發電機")
    assert [c["max_tokens"] for c in think.calls[:2]] == [8192, 16384]
    assert not any("沒有給出計畫" in json.dumps(d, ensure_ascii=False) for _, d in events)

    think2 = Model("gemini/gemini-pro-latest", [("", "length", 0), ("", "length", 0)])
    events.clear()
    rt2 = Runtime(config, provider=Model("gemini/gemini-flash-latest", ["完成"]), thinking_provider=think2)
    a2 = Agent(rt2, emit=lambda kind, data: events.append((kind, data)))
    await a2.run("用動畫展示風力發電機")
    assert any("沒有給出計畫" in json.dumps(d, ensure_ascii=False) for _, d in events)


# ---------------------------------------------------------------- U2
def test_size_reminders():
    big = Message("assistant", "", [ToolCall("a", "edit_file", {"path": "x", "old_string": "o", "new_string": "x" * 31000})])
    tip = L.size_reminder(big, ChatResponse(big, Usage(1, 100), "stop"), 32768)
    assert "31,000 characters" in tip
    near = Message("assistant", "", [ToolCall("a", "write_file", {"path": "x", "content": "x" * 100})])
    assert "80%" in L.size_reminder(near, ChatResponse(near, Usage(1, 26215), "stop"), 32768)
    assert L.size_reminder(near, ChatResponse(near, Usage(1, 500), "stop"), 32768) is None


async def test_a_big_call_is_followed_by_a_reminder(home, config):
    big = Message("assistant", "", [ToolCall("a", "write_file", {"path": "page.html", "content": "<p>x</p>" * 5000})])
    m = Model("gemini/gemini-flash-latest", [big, "完成"])
    await Agent(Runtime(config, provider=m)).run("寫一頁")
    assert "more than one section" in m.calls[1]["tail"]


def test_write_file_appends(home, config, tmp_path):
    from sinobee.tools.files import write_file
    from sinobee.tools.registry import ToolContext
    ctx = ToolContext(config=config)
    write_file(ctx, "part.html", "<html>")
    assert "now 13 bytes" in write_file(ctx, "part.html", "</html>", append=True)
    assert (config.workspace / "part.html").read_text() == "<html></html>"
