"""Proposal "large tasks no longer stop half-way", phase 2 (2026-10-21): a timeout right after a cut-off reply is taken
as "too long" (U4); a turn that failed on a temporary model error is resumed after 1 / 5 / 15 minutes (U5) and the
failure card offers a fallback model once; the failure report lists the files the turn wrote (U6); a skill that
produces a page with no word about parts is flagged and gets SinoBee's note (U7)."""
import time

import httpx
import pytest

from sinobee.agent import Agent, Runtime
from sinobee.agent import failures as F
from sinobee.agent.errors import explain_error, transient
from sinobee.providers.base import TOO_LONG, ProviderError
from sinobee.types import ChatResponse, Message, ToolCall, Usage
from sinobee.work.hub import get_hub

from conftest import ScriptedProvider


# ---------------------------------------------------------------- U4
class TooLong:
    """Cut off once, then a read timeout, then (asked for parts) it writes a section and finishes."""
    label = "gemini/gemini-flash-latest"
    provider_name = "gemini"
    model = "gemini-flash-latest"

    def __init__(self, timeouts=1):
        self.calls, self.timeouts, self.recorder = [], timeouts, None

    async def chat(self, messages, tools=None, **kw):
        self.calls.append({"too_long": TOO_LONG.get(), "tail": messages[-1].text()})
        n = len(self.calls)
        if n == 1:
            return ChatResponse(Message("assistant", ""), Usage(100, 3), "length", self.model)
        if n <= 1 + self.timeouts:
            raise ProviderError(f"{self.label} timed out: ReadTimeout('')")
        if n == 2 + self.timeouts:
            return ChatResponse(Message("assistant", "", [ToolCall("w", "write_file", {"path": "page.html", "content": "<p>骨架</p>"})]),
                                Usage(100, 50), "tool_calls", self.model)
        return ChatResponse(Message("assistant", "完成：分段寫好"), Usage(100, 10), "stop", self.model)

    async def embed(self, texts):
        raise RuntimeError("no embeddings")


async def test_a_timeout_after_a_cut_off_reply_asks_for_parts_instead_of_failing(config):
    p = TooLong()
    agent = Agent(Runtime(config, provider=p))
    out = await agent.run("用動畫展示風力發電機")
    assert out == "完成：分段寫好"
    assert [c["too_long"] for c in p.calls[:3]] == [False, True, False]   # only the call right after the cut-off
    assert "Do not write a large file in one call" in p.calls[2]["tail"]
    assert TOO_LONG.get() is False


async def test_the_timeout_counts_as_too_long_twice_then_the_task_stops(config):
    p = TooLong(timeouts=5)
    agent = Agent(Runtime(config, provider=p))
    await agent.run("用動畫展示風力發電機")
    assert len(p.calls) == 3            # cut off, timeout (too long, asked for parts), then a plain timeout: stop
    rep = F.get(config.home, F.listing(config.home, 1)[0]["id"])
    assert [t["step"] for t in rep["too_long_timeouts"]] == [2] and "TIMEOUTS RIGHT AFTER" in F.summary_text(rep)


async def test_a_too_long_call_is_not_sent_again(monkeypatch):
    from sinobee.providers.openai_compat import OpenAICompatProvider
    sent = []

    async def chat(self, *a, **k):
        sent.append(1)
        raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(OpenAICompatProvider, "_chat", chat)
    p = OpenAICompatProvider("gemini-flash-latest", "https://example.invalid/v1", "k", provider_name="gemini")
    with pytest.raises(ProviderError):
        await p.chat([Message("user", "hi")], max_tokens=10, retries=4)
    assert len(sent) == 2                                                    # an ordinary timeout: retried once
    sent.clear()
    tok = TOO_LONG.set(True)
    try:
        with pytest.raises(ProviderError):
            await p.chat([Message("user", "hi")], max_tokens=10, retries=4)
    finally:
        TOO_LONG.reset(tok)
    assert len(sent) == 1                                                    # too long: not sent again


# ---------------------------------------------------------------- U5
def test_temporary_and_lasting_model_errors():
    for e in ("gemini/x network error: ConnectError('x')", "gemini/x timed out: ReadTimeout('')",
              "openai/x HTTP 503: overloaded", "openai/x HTTP 429: rate limit"):
        assert transient(e), e
        assert explain_error(e)["transient"]
    for e in ("openai/x HTTP 401: invalid api key", "gemini/x HTTP 404: model not found",
              "openai/x HTTP 400: bad request", "openai/x HTTP 429: insufficient_quota"):
        assert not transient(e), e


def setup(config):
    rt = Runtime(config, provider=ScriptedProvider())
    hub = get_hub(rt)
    calls = []
    hub.chat_resumer = lambda sid, text: calls.append((sid, text))
    hub.chat_alive = lambda sid: False
    return rt, hub, calls


def failed_run(rt, hub, sid, *, transient_=True, quota=None, ago=30):
    rt.memory.ensure_session(sid, "web", title="風力發電機")
    run = hub.store.create_run(job_id=None, name="風力發電機", kind="chat", trigger="chat",
                               state={"input": "用動畫展示風力發電機", "steps": [], "turn_id": 7,
                                      "model_error": {"at": time.time() - ago, "transient": transient_, "quota": quota,
                                                      "summary": "連不到模型服務"}})
    hub.store.update_run(run["id"], status="failed", session_id=sid, created_at=time.time() - ago - 60,
                         finished_at=time.time() - ago, error="連不到模型服務")
    return run["id"]


async def test_a_turn_failed_on_a_temporary_model_error_is_resumed_after_a_wait(home, config):
    rt, hub, calls = setup(config)
    rid = failed_run(rt, hub, "m1", ago=30)
    events = []
    hub.notify = lambda what, data: events.append((what, data))
    await hub.recovery.check()
    assert not calls                                                       # waits a minute first
    sched = [d for w, d in events if w == "auto_resume" and d.get("scheduled")]
    assert sched and sched[0]["n"] == 1 and sched[0]["max"] == 3 and sched[0]["due"] > time.time()
    st = hub.store.run(rid)["state"]
    st["model_error"]["at"] = time.time() - 61
    hub.store.update_run(rid, state=st)
    await hub.recovery.check()
    assert calls and calls[0][0] == "m1" and calls[0][1].startswith("[自動續跑]")
    assert hub.store.run(rid)["state"]["auto_resume"]["resumed"] is True
    assert hub.recovery._model_count("m1") == 1
    assert hub.recovery.model_plan(hub.store.run(failed_run(rt, hub, "m1b")))["due"] - time.time() > 0


async def test_the_waits_grow_and_stop_after_three_a_day(home, config):
    rt, hub, calls = setup(config)
    rec = hub.recovery
    waits = []
    for _ in range(3):
        p = rec.model_plan(hub.store.run(failed_run(rt, hub, "m2", ago=0)))
        waits.append(round(p["due"] - time.time()))
        rec._model_counted("m2")
    assert waits[0] in range(58, 62) and waits[1] in range(298, 302) and waits[2] in range(898, 902)
    assert "3 次" in rec.model_plan(hub.store.run(failed_run(rt, hub, "m2")))["reason"]


async def test_a_quota_stop_waits_for_the_quota_and_is_not_counted(home, config):
    rt, hub, calls = setup(config)
    at = time.time() + 1800
    rid = failed_run(rt, hub, "m3", quota={"window": "6h", "retry_at": at})
    p = hub.recovery.model_plan(hub.store.run(rid))
    assert p["quota"] and abs(p["due"] - (at + 30)) < 2 and p["n"] is None


async def test_a_lasting_error_or_a_newer_turn_is_not_resumed(home, config):
    rt, hub, calls = setup(config)
    bad = failed_run(rt, hub, "m4", transient_=False, ago=1000)
    await hub.recovery.check()
    assert not calls and "不會自己消失" in hub.store.run(bad)["state"]["auto_resume"]["reason"]


def test_a_fallback_model_is_offered_from_a_key_you_have(config, monkeypatch):
    from sinobee.providers import fallback
    monkeypatch.setattr("sinobee.providers.usable_now", lambda prov, cfg: prov == "anthropic")
    o = fallback.suggest(config, "gemini/gemini-flash-latest")
    assert o["spec"] == "anthropic/claude-sonnet-5-5"                      # same tier: balanced
    assert fallback.suggest(config, "gemini/gemini-flash-lite-latest")["spec"] == "anthropic/claude-haiku-5-5"
    monkeypatch.setattr("sinobee.providers.usable_now", lambda prov, cfg: False)
    monkeypatch.setattr("sinobee.account.can_relay", lambda cfg: True)
    assert fallback.suggest(config, "gemini/gemini-pro-latest")["spec"].startswith("sinobee/")
    monkeypatch.setattr("sinobee.account.can_relay", lambda cfg: False)
    assert fallback.suggest(config, "gemini/gemini-pro-latest") is None


class Down:
    label = "gemini/gemini-flash-latest"
    provider_name = "gemini"
    model = "gemini-flash-latest"
    recorder = None

    async def chat(self, messages, tools=None, **kw):
        raise ProviderError(f"{self.label} network error: ConnectError('down')")

    async def embed(self, texts):
        raise RuntimeError("no embeddings")


async def test_the_failure_card_offers_a_fallback_once(config, monkeypatch):
    from sinobee.providers import fallback
    monkeypatch.setattr(fallback, "suggest", lambda cfg, failed: {"spec": "anthropic/claude-sonnet-5-5", "label": "x"})
    events = []
    rt = Runtime(config, provider=Down())
    await Agent(rt, emit=lambda k, d: events.append((k, d))).run("hi")
    end = [d for k, d in events if k == "turn_end"][-1]
    assert end["error"]["fallback_offer"]["spec"] == "anthropic/claude-sonnet-5-5" and end["error"]["transient"]
    rt.memory.meta_set("fallback_offer_declined", "1")                    # declined: not offered again
    events.clear()
    await Agent(rt, emit=lambda k, d: events.append((k, d))).run("hi")
    assert "fallback_offer" not in [d for k, d in events if k == "turn_end"][-1]["error"]


# ---------------------------------------------------------------- U6
async def test_the_failure_report_lists_the_files_written(config):
    p = ScriptedProvider()
    p.push("", [("write_file", {"path": "page.html", "content": "<html>骨架</html>"})])
    p.push("", [("edit_file", {"path": "page.html", "old_string": "骨架", "new_string": "葉片"})])
    p.push("", [("edit_file", {"path": "missing.txt", "old_string": "a", "new_string": "b"})])
    rt = Runtime(config, provider=p)
    agent = Agent(rt)
    orig = p.chat

    async def chat(messages, tools=None, **kw):
        if not p.script:
            raise ProviderError("gemini/x network error: ConnectError('down')")
        return await orig(messages, tools, **kw)
    p.chat = chat
    await agent.run("寫一頁")
    rep = F.get(config.home, F.listing(config.home, 1)[0]["id"])
    files = {f["path"]: f for f in rep["written_files"]}
    assert files["page.html"]["exists"] and files["page.html"]["writes"] == 2 and files["page.html"]["ok_writes"] == 2
    assert files["missing.txt"]["exists"] is False and files["missing.txt"]["last_ok"] is False
    assert "FILES WRITTEN IN THIS TURN" in F.summary_text(rep)


# ---------------------------------------------------------------- U7
def test_a_skill_that_makes_a_page_without_parts_is_flagged_and_noted(home, config, tmp_path):
    from sinobee.skills import largefiles as LF
    assert LF.needs_note("Each example writes a standalone HTML page next to the script.")
    assert not LF.needs_note("Write a standalone HTML page: first a skeleton, then one section at a time.")
    assert not LF.needs_note("For a web page, write HTML instead.")                 # an alternative, not a deliverable
    rt = Runtime(config, provider=ScriptedProvider())
    view = rt.skills.view("anatomy", record=False) if "anatomy" in rt.skills.skills else None
    if view is not None:
        assert "<sinobee_note about=\"large files\">" in view
        sk = rt.skills.get("anatomy")
        assert any(str(d).startswith("large-file:") for d in sk.diagnostics)
        from pathlib import Path
        assert "sinobee_note" not in Path(sk.location).read_text(encoding="utf-8")   # the skill's text is unchanged
