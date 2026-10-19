"""Every task leaves a run report: the cost of every step (tokens, time on models and tools, calls sent again) next
to its progress verdict, and what the task produced - so the tasks that spent the most for the least can be listed,
finished or not."""
import asyncio
import json
import types

from sinobee.agent import Agent, Runtime, runreport
from sinobee.providers import base
from sinobee.providers.base import FallbackChain, Provider, ProviderError
from sinobee.types import ChatResponse, Message, ToolCall, Usage

from conftest import ScriptedProvider


def msg(text="", calls=None):
    return Message("assistant", text, [ToolCall(f"c{i}{n}{len(json.dumps(a, ensure_ascii=False))}", n, a)
                                       for i, (n, a) in enumerate(calls or [])])


def run(config, main, sid="rr", **kw):
    config.data["agent"].update(failure_diagnosis=False)
    events = []
    rt = Runtime(config, provider=main, thinking_provider=kw.get("thinker") or ScriptedProvider(),
                 aux_provider=ScriptedProvider())
    agent = Agent(rt, session_id=sid, platform="web", emit=lambda k, d: events.append((k, d)))
    return rt, agent, events


def end(events):
    return next(d for k, d in events if k == "turn_end")


async def test_a_finished_task_has_a_report_with_the_cost_of_every_step(home, config):
    main = ScriptedProvider([
        msg("", [("task_checklist", {"action": "add", "items": ["寫出 wind.html"]})]),
        msg("", [("write_file", {"path": "wind.html", "content": "<h1>風機</h1>" + "<p>葉片</p>" * 50})]),
        msg("", [("task_checklist", {"action": "update", "id": 1, "status": "done", "evidence": "wind.html"})]),
        msg("完成")])
    rt, agent, events = run(config, main)
    await agent.run("寫風機頁面")
    e = end(events)
    assert e["status"] == "done" and e["run_report"] and e["failure"] is None
    r = runreport.get(config.home, e["run_report"])
    sc = r["step_costs"]
    assert [s["step"] for s in sc] == [1, 2, 3, 4] and sc[3]["why"] == "最後的回答"   # the answer step costs too
    assert all(s["calls"] == 1 and s["tokens"] == 1500 and s["input"] == 1000 and s["output"] == 500 for s in sc)
    assert all(s["model_s"] >= 0 and s["tools_s"] >= 0 and s["retries"] == 0 for s in sc)
    assert sc[1]["produced"] is True and sc[0]["produced"] is False        # a new file is output; a checklist is not
    assert r["outputs"]["files"] == ["wind.html"] and r["outputs"]["checklist_done"] == 1
    assert r["efficiency"]["units"] == 2 and r["efficiency"]["tokens"] == 4 * 1500 and not r["efficiency"]["no_output"]
    assert len(r["top_steps"]) == 3 and r["time"]["total_s"] >= r["time"]["model_s"]
    text = runreport.summary_text(r)
    assert "【每步花費】" in text and "1,500 tok" in text and "產出：1 個檔案、驗收清單完成 1/1" in text
    # the progress trace in the run record carries the same costs
    assert all("cost" in t for t in r["progress"])


class Flaky(Provider):
    """Fails with a 503 the first time each step is asked, then answers from the script."""

    def __init__(self, script):
        super().__init__("flaky", "https://x.invalid", None, provider_name="t")
        self.script, self.fail = list(script), True

    async def _chat(self, messages, tools, *, temperature, max_tokens):
        if self.fail:
            self.fail = False
            raise ProviderError("t/flaky HTTP 503: upstream overloaded", 503, True)
        self.fail = True
        return ChatResponse(self.script.pop(0), Usage(2000, 100))


async def test_calls_sent_again_and_the_failed_step_are_in_the_report(home, config, monkeypatch):
    async def no_wait(_s):
        return None
    monkeypatch.setattr(base, "asyncio", types.SimpleNamespace(**{**vars(asyncio), "sleep": no_wait}))
    main = FallbackChain([Flaky([msg("", [("list_dir", {"path": "."})]), msg("好了")])])
    rt, agent, events = run(config, main, sid="flaky")
    await agent.run("看資料夾")
    r = runreport.get(config.home, end(events)["run_report"])
    s1 = r["step_costs"][0]
    assert s1["retries"] == 1 and "HTTP 503" in s1["retry_errors"][0] and s1["tokens"] == 2100
    assert r["retries"] == 2                                   # step 2 (the answer) was retried too
    assert "重送 1" in runreport.summary_text(r)

    # a model that never answers: the failed step is kept, with what it cost
    class Down(Provider):
        def __init__(self):
            super().__init__("down", "https://x.invalid", None, provider_name="t")

        async def _chat(self, messages, tools, *, temperature, max_tokens):
            raise ProviderError("t/down HTTP 504: <title>sinobee.ai | 504: Gateway time-out</title>", 504, True)
    rt, agent, events = run(config, FallbackChain([Down()]), sid="down")
    await agent.run("寫頁面")
    e = end(events)
    assert e["status"] == "error" and e["failure"] and e["run_report"]
    r = runreport.get(config.home, e["run_report"])
    last = r["step_costs"][-1]
    assert last["failed"] and last["retries"] == 1 and "閘道逾時" in last["why"]
    assert r["failure_id"] == e["failure"]


async def test_the_most_for_the_least_are_listed_first(home, config):
    # a task that only reads (nothing produced) and one that writes a file
    reads = ScriptedProvider([msg("", [("task_checklist", {"action": "add", "items": ["頁面"]})])]
                             + [msg("", [("shell", {"command": f"echo r{i}"})]) for i in range(4)] + [msg("查完了")])
    rt, agent, events = run(config, reads, sid="reads")
    await agent.run("查資料")
    writes = ScriptedProvider([msg("", [("write_file", {"path": "a.md", "content": "# A\n\n內容"})]), msg("寫好了")])
    rt, agent, events = run(config, writes, sid="writes")
    await agent.run("寫 a.md")
    rows = runreport.listing(config.home)
    assert [r["session_id"] for r in rows] == ["reads", "writes"]
    assert rows[0]["no_output"] and rows[0]["checklist"] == "0/1" and rows[0]["top_step"]["tokens"] == 1500
    assert rows[1]["files"] == 1 and rows[1]["tokens_per_output"] == 3000
    assert [r["session_id"] for r in runreport.listing(config.home, sort="recent")] == ["writes", "reads"]


def test_api_and_cli(home, config, capsys):
    from fastapi.testclient import TestClient
    from sinobee.server import create_app
    rt, agent, events = run(config, ScriptedProvider([msg("", [("list_dir", {"path": "."})]), msg("好")]), sid="api")
    asyncio.run(agent.run("看"))
    rid = end(events)["run_report"]
    with TestClient(create_app(rt)) as c:
        rows = c.get("/api/run-reports?days=1").json()["reports"]
        assert rows[0]["id"] == rid and rows[0]["steps"] == 2
        one = c.get(f"/api/run-reports/{rid}").json()
        assert one["step_costs"][0]["tokens"] == 1500 and "【每步花費】" in one["text"]
        assert c.get("/api/run-reports/nope").status_code == 404
    from sinobee.cli import main
    main(["runs"])
    assert rid in capsys.readouterr().out
    main(["runs", rid])
    assert "【每步花費】" in capsys.readouterr().out


def test_the_failure_report_shows_step_costs():
    from sinobee.agent import failures
    text = failures.summary_text({"request": "x", "status": "error", "progress": [
        {"step": 1, "progress": True, "why": "shell 新的輸出", "cost": {"tokens": 52300, "model_s": 98.2, "retries": 1}}]})
    assert "#1 ✓ shell 新的輸出（52,300 tokens · 模型 98.2s · 重送 1）" in text
