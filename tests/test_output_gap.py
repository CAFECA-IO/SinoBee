"""Producing nothing while the acceptance checklist is open asks the thinking model (production_gap), and a budget
that is 80% gone with nothing produced does too (budget_no_output). Both come from the wind-turbine task: 18 steps of
reading a kit's source and grepping a PDF, every step "new output", checklist 0/6, no file - K never counted, and
the two budget notes were ignored."""
import json

from sinobee.agent import Agent, Runtime
from sinobee.agent import budget as B
from sinobee.agent import progress as P
from sinobee.types import Message, ToolCall

from conftest import ScriptedProvider

OPEN = [{"id": i, "text": f"項目 {i}", "status": "todo"} for i in range(1, 7)]


def grep(i):
    return ("shell", {"command": f"grep -n 'Rated {i}' nrel5.txt"}, f"[exit 0]\n{i * 37}: Rated Power {i} MW", False)


def test_reading_without_producing_while_the_checklist_is_open(tmp_path):
    tr = P.Tracker(None, read_file=lambda p: (tmp_path / p).read_text())
    tr.set_checklist(OPEN)
    loops = []
    for n in range(1, 10):
        v = tr.observe(n, [grep(n)], OPEN)
        assert v["progress"]                                   # new output every step: K never counts
        if v.get("loop"):
            loops.append((n, v["loop"]))
    assert tr.no_progress == 0
    n, lp = loops[0]
    assert n == 8 and lp["trigger"] == "production_gap" and lp["soft"]
    assert "驗收清單還有 6 項未完成，已連續 8 步（第 1–8 步）沒有寫出檔案" in lp["detail"]
    assert "output_gap=8" in lp["detail"] and tr.thresholds()["output_gap"] == 8
    # the advice gets a fresh gap
    tr.advised(9)
    assert all(not tr.observe(n, [grep(n)], OPEN).get("loop") for n in range(10, 17))


def test_no_gap_without_a_checklist_or_while_producing(tmp_path):
    (tmp_path / "page.html").write_text("<h1>x</h1>")
    tr = P.Tracker(None, read_file=lambda p: (tmp_path / p).read_text())
    assert not any(tr.observe(n, [grep(n)], []).get("loop") for n in range(1, 12))
    tr = P.Tracker(None, read_file=lambda p: (tmp_path / p).read_text())
    tr.set_checklist(OPEN)
    out = []
    for n in range(1, 16):
        calls = [grep(n)]
        if n % 6 == 0:                                         # a section written every few steps
            (tmp_path / "page.html").write_text("<h1>x</h1>" + "<section>s</section>" * n)
            calls = [("write_file", {"path": "page.html", "content": "x"}, "wrote", False)]
        out.append(tr.observe(n, calls, OPEN).get("loop"))
    assert not any(out)
    # polling a running render does not count toward the gap
    tr = P.Tracker(None)
    tr.set_checklist(OPEN)
    poll = ("shell", {"command": "cat status.json"}, '{"status": "running"}', False)
    assert not any(tr.observe(n, [poll], OPEN).get("loop") for n in range(1, 12))


def msg(text="", calls=None):
    return Message("assistant", text, [ToolCall(f"c{i}{n}{len(json.dumps(a, ensure_ascii=False))}", n, a)
                                       for i, (n, a) in enumerate(calls or [])])


def checklist_step():
    return msg("", [("task_checklist", {"action": "add", "items": ["寫出 wind.html", "參數表", "原理說明"]})])


def shells(n):
    return [msg("", [("shell", {"command": f"echo rated-{i}-$((6*{i}))"})]) for i in range(n)]


async def test_the_gap_asks_the_thinking_model_and_never_stops_the_task(home, config):
    config.data["agent"].update(failure_diagnosis=False, consult_max=1)
    main = ScriptedProvider([checklist_step()] + shells(20) + [msg("完成")])
    thinker = ScriptedProvider([msg("已有足夠參數，先寫出頁面骨架。\nDECISION: continue")])
    events = []
    rt = Runtime(config, provider=main, thinking_provider=thinker, aux_provider=ScriptedProvider())
    agent = Agent(rt, session_id="gap", platform="web", emit=lambda k, d: events.append((k, d)))
    await agent.run("用動畫展示風力發電機")
    assert agent.last_status["status"] != "failed"       # (incomplete: the scripted model never wrote the page)
    ctl = [d for k, d in events if k == "control"]
    assert ctl[0]["event"] == "consult_requested" and ctl[0]["trigger"] == "production_gap"
    assert ctl[1]["event"] == "consult" and ctl[1]["decision"] == "continue"
    assert any(m.text().startswith("[思維模型的建議]") and "頁面骨架" in m.text() for m in agent.history)
    # the advice is used up: the next gap is a reminder to the execution model, not a stop
    assert any(d["event"] == "consult_skipped" and d["trigger"] == "production_gap" for d in ctl)
    assert not any(d["event"] == "stop" for d in ctl)
    assert any(m.text().startswith("[進度提醒] 驗收清單還有 3 項未完成") for m in agent.history)


async def test_most_of_the_budget_gone_with_nothing_produced_asks(home, config):
    config.data["agent"].update(quality="fast", failure_diagnosis=False)
    config.data["agent"]["budget"] = {**B.DEFAULTS, "default_steps": 2, "default_step_tokens": 1000}
    config.data["agent"]["progress"] = {**P.DEFAULTS, "output_gap": 0}          # only the budget rule here
    thinker = ScriptedProvider([
        msg(json.dumps({"verdict": "behind", "note": "還沒有產出"}, ensure_ascii=False)),
        msg(json.dumps({"verdict": "at_risk", "note": "沒有產出", "advice": "先寫頁面"}, ensure_ascii=False)),
        msg("停止查資料，先寫 wind.html。\nDECISION: continue")])
    main = ScriptedProvider([checklist_step()] + shells(5) + [msg("完成")])
    events = []
    rt = Runtime(config, provider=main, thinking_provider=thinker, aux_provider=ScriptedProvider())
    agent = Agent(rt, session_id="bud", platform="web", emit=lambda k, d: events.append((k, d)))
    await agent.run("用動畫展示風力發電機")
    ctl = [d for k, d in events if k == "control"]
    req = next(d for d in ctl if d["event"] == "consult_requested")
    assert req["trigger"] == "budget_no_output" and "驗收清單完成 0/3" in req["detail"]
    assert [d["trigger"] for d in ctl if d["event"] == "budget"] == ["budget_50", "budget_80"]
    assert next(d for d in ctl if d["event"] == "consult")["trigger"] == "budget_no_output"
    assert any(m.text().startswith("[思維模型的建議]") and "先寫 wind.html" in m.text() for m in agent.history)


def test_replay_reminds_instead_of_stopping():
    from sinobee.agent import replay
    turn = {"request": "x", "steps": [[("task_checklist", {"action": "add", "items": ["a", "b"]}, "ok", False)]]
            + [[("shell", {"command": f"grep {i}"}, f"line {i}", False)] for i in range(30)]}
    r = replay.replay_turn(turn, None, consult_max=1)
    assert r["stop"] is None
    assert [d["trigger"] for d in r["decisions"]] == ["production_gap"]
