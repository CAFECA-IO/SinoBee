"""Phase 4: the auxiliary model's semantic check when a progress rule fires, a failure report behind every stop (the
safety valve too) linked from the control log, the observability statistics, and K / N / budget recommendations
that are made only from enough data and can be applied to the settings."""
import json

from sinobee.agent import Agent, Runtime, failures, observe
from sinobee.agent import progress as P
from sinobee.types import Message, ToolCall
from sinobee.work.hub import get_hub

from conftest import ScriptedProvider


def msg(text="", calls=None):
    return Message("assistant", text, [ToolCall(f"c{i}{n}{len(json.dumps(a, ensure_ascii=False))}", n, a)
                                       for i, (n, a) in enumerate(calls or [])])


def run_agent(config, main, thinker=None, aux=None, sid="obs-sess"):
    config.data["agent"].update(failure_diagnosis=False)
    events = []
    rt = Runtime(config, provider=main, thinking_provider=thinker or ScriptedProvider(),
                 aux_provider=aux or ScriptedProvider())
    agent = Agent(rt, session_id=sid, platform="web", emit=lambda k, d: events.append((k, d)))
    return rt, agent, events


SEM_FALSE = json.dumps({"spinning": False, "pattern": "逐一查看不同資料夾", "evidence": "每一步讀的是新內容"},
                       ensure_ascii=False)


# ------------------------------------------------------------------ semantic check
def test_parse_semantic():
    assert P.parse_semantic('```json\n{"spinning": true, "pattern": "重複改寫", "evidence": "x"}\n```')["spinning"] is True
    assert P.parse_semantic('前言 {"spinning": "false", "pattern": "p"} 後記')["spinning"] is False
    assert P.parse_semantic("看不懂") is None


async def test_the_auxiliary_model_checks_before_the_thinking_model_is_asked(home, config):
    steps = [msg("", [("list_dir", {"path": "."})]) for _ in range(3)] + [msg("看完了")]
    thinker = ScriptedProvider([msg("直接回答。\nDECISION: continue")])
    aux = ScriptedProvider([msg(SEM_FALSE)])
    aux.label = "aux/cheap"
    rt, agent, events = run_agent(config, ScriptedProvider(steps), thinker, aux)
    assert await agent.run("看一下資料夾") == "看完了"
    consult = next(d for k, d in events if k == "control" and d["event"] == "consult")
    assert consult["semantic"]["spinning"] is False and consult["semantic"]["model"] == "aux/cheap"
    assert "輔助模型：有實質進展，逐一查看不同資料夾" in consult["detail"]
    # the thinking model was told what the auxiliary model saw
    asked = thinker.requests[0][0][-1].text()
    assert "AUXILIARY MODEL'S CHECK" in asked and "making real progress" in asked
    assert "list_dir" in aux.requests[0][0][0].text() and "loop_repeat" in aux.requests[0][0][0].text()
    row = P.read_log(config.home, event="consult")[-1]
    assert row["semantic"]["pattern"] == "逐一查看不同資料夾"


async def test_no_semantic_check_without_a_separate_auxiliary_model_or_when_off(home, config):
    steps = [msg("", [("list_dir", {"path": "."})]) for _ in range(3)] + [msg("好")]
    config.data["agent"]["progress"] = {**P.DEFAULTS, "semantic_check": False}
    aux = ScriptedProvider([msg(SEM_FALSE)])
    rt, agent, events = run_agent(config, ScriptedProvider(steps), ScriptedProvider([msg("ok\nDECISION: continue")]), aux)
    await agent.run("看")
    consult = next(d for k, d in events if k == "control" and d["event"] == "consult")
    assert "semantic" not in consult or consult["semantic"] is None
    assert not any("loop_repeat" in m[0].text() for m, _ in aux.requests)
    assert "輔助模型" not in consult["detail"]


# ------------------------------------------------------------------ every stop has a report
async def test_the_safety_valve_leaves_a_traceable_report(home, config):
    config.data["agent"]["progress"] = {**P.DEFAULTS, "safety_max_steps": 5}
    steps = [msg("", [("list_dir", {"path": f"d{i}"})]) for i in range(10)]
    rt, agent, events = run_agent(config, ScriptedProvider(steps))
    await agent.run("一直看")
    fid = next(d for k, d in events if k == "turn_end")["failure"]
    rep = P.read_log(config.home, event="report")
    assert len(rep) == 1 and rep[0]["failure_id"] == fid and rep[0]["trigger"] == "safety_valve"
    assert rep[0]["run_id"] == agent._run_rec.rid and rep[0]["status"] == "failed"
    assert f"失敗報告 {fid}" in P.describe(rep[0])
    s = observe.stats(config.home, get_hub(rt).store, days=1)
    assert s["safety_valve"] and s["safety_valve"][0]["failure_id"] == fid
    assert failures.get(config.home, fid) is not None
    assert s["turns"]["n"] == 1 and s["turns"]["status"] == {"failed": 1}


def test_stats_counts_consults_budgets_and_milestones(home, config):
    rt = Runtime(config, provider=ScriptedProvider())
    for ev, trig, extra in [("consult", "no_progress", {"decision": "continue", "semantic": {"spinning": False}}),
                            ("consult", "loop_repeat", {"decision": "stop", "semantic": {"spinning": True}}),
                            ("consult", "no_progress", {"decision": "continue"}),
                            ("budget", "budget_50", {"decision": "on_track"}),
                            ("budget", "budget_80", {"decision": "at_risk"}),
                            ("budget", "budget_cap", {}), ("delivery", "partial_delivery", {}),
                            ("milestone", "replan", {})]:
        P.append_log(config.home, {**P.entry(ev, trig, 3, "d", None, **extra), "session_id": "s"})
    s = observe.stats(config.home, get_hub(rt).store, days=7)
    assert s["consults"]["n"] == 3 and s["consults"]["decisions"] == {"continue": 2, "stop": 1}
    assert s["consults"]["semantic_checked"] == 2 and s["consults"]["semantic_not_spinning"] == 1
    assert s["budget"]["checkpoints"] == {"on_track": 1, "at_risk": 1}
    assert s["budget"]["caps"] == 1 and s["budget"]["partial_deliveries"] == 1
    assert s["milestones"] == {"replan": 1}


# ------------------------------------------------------------------ tuning
def _row(max_np, streaks=(), status="done"):
    trace, n = [], 0
    for kind, length in streaks:
        for i in range(1, length + 1):
            n += 1
            trace.append({"step": n, "file_only": i, "kind": kind, "progress": True})
        n += 1
        trace.append({"step": n, "file_only": 0, "kind": None, "progress": True})
    return {"status": status, "max_no_progress": max_np, "trace": trace}


def test_too_little_data_recommends_nothing(home, config, monkeypatch):
    from sinobee.agent import replay
    monkeypatch.setattr(replay, "replay_sessions", lambda *a, **k: [_row(3)] * 5)
    rt = Runtime(config, provider=ScriptedProvider())
    t = observe.tune(config.home, get_hub(rt).store, rt.memory, config)
    recs = {r["key"]: r for r in t["recommendations"]}
    assert all(r["suggested"] is None and r["confidence"] == "none" and not r["apply"] for r in recs.values())
    assert "只有 5 個順利完成的回合" in recs["agent.progress.k"]["evidence"]
    assert recs["agent.budget.synthetic_factor"]["n"] == 0


def test_enough_data_recommends_from_the_distribution(home, config, monkeypatch):
    from sinobee.agent import replay
    rows = [_row(i % 7, [("document", 1 + i % 3), ("code", 2 + i % 5)]) for i in range(40)]
    rows += [_row(20, status="failed")] * 3
    monkeypatch.setattr(replay, "replay_sessions", lambda *a, **k: rows)
    rt = Runtime(config, provider=ScriptedProvider())
    store = get_hub(rt).store
    for i in range(12):                     # real usage was 1.5x - 2.5x the synthetic estimate
        r = store.create_run(job_id=None, name="t", kind="chat", trigger="chat", state={})
        store.update_run(r["id"], status="done", state={"usage": {"task_type": "document", "tokens": 1000 * (10 + i),
                                                                   "cost_usd": .01, "steps": 10, "est_steps": 10}})
    t = observe.tune(config.home, store, rt.memory, config)
    recs = {r["key"]: r for r in t["recommendations"]}
    k = recs["agent.progress.k"]
    assert t["turns_ok"] == 40 and t["turns_bad"] == 3
    assert k["suggested"] == 9 and k["confidence"] == "medium"        # p99 of 0..6 is 6 -> 6 + 3
    assert k["simulation"][15]["bad_flagged"] == 3 and k["simulation"][15]["ok_flagged"] == 0
    assert recs["agent.progress.n_document"]["suggested"] == 4      # streaks 1..3 -> p95 3 + 1
    assert recs["agent.progress.n_code"]["suggested"] == 7          # streaks 2..6 -> p95 6 + 1
    assert recs["agent.progress.n_other"]["suggested"] is None
    f = recs["agent.budget.synthetic_factor"]
    assert f["suggested"] is not None and 1.2 <= f["suggested"] <= 4 and f["n"] == 12
    assert "document" in t["baselines"]


def test_false_alarms_loosen_k(home, config, monkeypatch):
    from sinobee.agent import replay
    monkeypatch.setattr(replay, "replay_sessions", lambda *a, **k: [_row(2)] * 30)
    for i in range(4):
        P.append_log(config.home, {**P.entry("consult", "no_progress", 15, "d", None,
                                             semantic={"spinning": i == 0}), "session_id": "s"})
    rt = Runtime(config, provider=ScriptedProvider())
    t = observe.tune(config.home, get_hub(rt).store, rt.memory, config)
    k = next(r for r in t["recommendations"] if r["key"] == "agent.progress.k")
    assert k["suggested"] == 20 and "輔助模型認為其實有進展的有 3 次" in k["evidence"]


def test_apply_writes_only_tunable_settings(home, config):
    out = observe.apply(config, {"agent.progress.k": 18, "agent.budget.synthetic_factor": "2.5",
                                 "agent.progress.safety_max_steps": 10, "agent.progress.n_code": None})
    assert out == {"agent.progress.k": 18, "agent.budget.synthetic_factor": 2.5}
    assert config.get("agent.progress.k") == 18 and config.get("agent.budget.synthetic_factor") == 2.5
    assert config.get("agent.progress.safety_max_steps") != 10


# ------------------------------------------------------------------ API and CLI
def test_api_and_cli(home, config, capsys):
    from fastapi.testclient import TestClient
    from sinobee.server import create_app
    P.append_log(config.home, {**P.entry("consult", "no_progress", 15, "d", None, decision="continue"), "session_id": "s"})
    rt = Runtime(config, provider=ScriptedProvider())
    with TestClient(create_app(rt)) as c:
        s = c.get("/api/observability?days=7").json()
        assert s["consults"]["n"] == 1 and "turns" in s
        t = c.get("/api/tuning?last=50").json()
        assert {r["key"] for r in t["recommendations"]} >= {"agent.progress.k", "agent.progress.n_document"}
        assert c.post("/api/tuning/apply", json={"values": {"bogus": 1}}).status_code == 400
        r = c.post("/api/tuning/apply", json={"values": {"agent.progress.n_document": 4}})
        assert r.status_code == 200 and r.json()["applied"] == {"agent.progress.n_document": 4}
        assert rt.config.get("agent.progress.n_document") == 4
    from sinobee.cli import main
    main(["observe", "--days", "7"])
    out = capsys.readouterr().out
    assert "請教" in out or "consult" in out
    main(["tune", "--json"])
    assert json.loads(capsys.readouterr().out)["recommendations"]
