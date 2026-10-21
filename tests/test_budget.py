"""Phase 3: task budgets - baselines by task type (p50/p90, or synthetic on a cold start), soft checkpoints that only
warn, the per-task cap that pauses, and a partial delivery of what is finished."""
import json

from sinobee.agent import Agent, Runtime, failures
from sinobee.agent import budget as B
from sinobee.types import Message, ToolCall
from sinobee.work.hub import get_hub

from conftest import ScriptedProvider


def msg(text="", calls=None):
    return Message("assistant", text, [ToolCall(f"c{i}{n}{len(json.dumps(a, ensure_ascii=False))}", n, a)
                                       for i, (n, a) in enumerate(calls or [])])


def seed(store, task_type, tokens, cost, steps=10, milestones=False, status="done", billed=None):
    r = store.create_run(job_id=None, name="t", kind="chat", trigger="chat", state={})
    store.update_run(r["id"], status=status, state={"usage": {"task_type": task_type, "tokens": tokens,
                                                              "cost_usd": cost, "steps": steps, "billed_tokens": billed,
                                                              "milestones": milestones}})


def make(config, main, thinker=None, ask=None, **budget):
    config.data["agent"].update(quality="fast", failure_diagnosis=False)
    config.data["agent"]["budget"] = {**B.DEFAULTS, **budget}
    events = []
    rt = Runtime(config, provider=main, thinking_provider=thinker or ScriptedProvider(),
                 aux_provider=ScriptedProvider())
    agent = Agent(rt, session_id="b-sess", platform="web", emit=lambda k, d: events.append((k, d)), ask=ask)
    return rt, agent, events


def ctl(events, event=None):
    return [d for k, d in events if k == "control" and (event is None or d["event"] == event)]


# ------------------------------------------------------------------ baselines and the cap
def test_baseline_from_history_and_the_synthetic_cold_start(home, config):
    store = get_hub(Runtime(config, provider=ScriptedProvider())).store
    for i in range(10):
        seed(store, "document", tokens=10_000 * (i + 1), cost=0.1 * (i + 1), steps=20)
    b = B.baseline(store, config, "document", 12)
    assert b["source"] == "history" and b["n"] == 10
    assert b["tokens"]["p50"] == 55_000 and b["tokens"]["p90"] == 91_000 and b["ref_tokens"] == 91_000
    assert abs(b["cost"]["p90"] - 0.91) < 1e-9
    # a type without enough runs: estimated steps x the average per step over all runs x 2
    s = B.baseline(store, config, "coding", 12)
    per = sum(10_000 * (i + 1) for i in range(10)) / 200
    assert s["source"] == "synthetic" and s["est_steps"] == 12 and s["per_step"]["tokens"] == round(per)
    assert s["ref_tokens"] == round(12 * per * 2) and "10 次任務的平均" in s["per_step"]["from"]


def test_the_very_first_tasks_use_the_default_per_step_values(home, config):
    store = get_hub(Runtime(config, provider=ScriptedProvider())).store
    s = B.baseline(store, config, "research", None)
    assert s["per_step"]["from"] == "預設值" and s["est_steps"] == 30 and s["ref_tokens"] == 30 * 8000 * 2
    plan = {"milestones": {"milestones": [{"estimate_steps": 15}, {"estimate_steps": 25}]}}
    assert B.estimate_steps(plan) == 40 and B.estimate_steps({"steps": ["a", "b"]}) == 6


def test_the_per_task_cap(home, config):
    store = get_hub(Runtime(config, provider=ScriptedProvider())).store
    assert B.hard_cap(store, config) == {"source": "initial", "usd": 10.0, "tokens": 4_000_000, "quota_pct": 50.0}
    for i in range(8):   # older records without billed tokens: the token cap stays at the initial value
        seed(store, "document", tokens=100_000 * (i + 1), cost=1.0 * (i + 1), milestones=True)
    cap = B.hard_cap(store, config)
    assert cap["source"] == "large_p95" and cap["tokens"] == 4_000_000 and cap["usd"] == round(7.65 * 1.5, 4)
    for i in range(8):
        seed(store, "document", tokens=1, cost=0, milestones=True, billed=10_000 * (i + 1))
    assert B.hard_cap(store, config)["tokens"] == round(B._pct([10_000.0 * (i + 1) for i in range(8)], .95) * 1.5)
    config.data["usage"]["task_max_usd"] = 3
    config.data["usage"]["task_max_quota_pct"] = 20
    assert B.hard_cap(store, config) == {"source": "setting", "usd": 3.0, "tokens": None, "quota_pct": 20.0}


# ------------------------------------------------------------------ soft checkpoints
async def test_checkpoints_at_50_and_80_percent_only_warn(home, config):
    # reference = 2 steps x 1,000 tokens x 2 = 4,000 tokens; every scripted call uses 1,500
    thinker = ScriptedProvider([
        msg(json.dumps({"verdict": "behind", "estimate": "還需要約 40%", "note": "花費比進度快"}, ensure_ascii=False)),
        msg(json.dumps({"verdict": "at_risk", "estimate": "可能超出一倍", "note": "會超出參考預算",
                        "advice": "先完成報告主體，圖表最後做"}, ensure_ascii=False))])
    main = ScriptedProvider([msg("", [("list_dir", {"path": f"d{i}"})]) for i in range(4)] + [msg("完成")])
    rt, agent, events = make(config, main, thinker, default_steps=2, default_step_tokens=1000)
    assert await agent.run("整理資料夾") == "完成"
    assert agent.last_status["status"] == "done"                      # the task was never interrupted
    cps = ctl(events, "budget")
    assert [c["trigger"] for c in cps] == ["budget_50", "budget_80"]
    assert cps[0]["decision"] == "behind" and "達參考預算 50%" in cps[0]["detail"] and "合成基準 4,000 tokens" in cps[0]["detail"]
    assert "估計 2 步 × 每步 1,000 tokens" in cps[0]["detail"] and "還需要約 40%" in cps[0]["detail"]
    assert cps[1]["decision"] == "at_risk"
    assert any(m.text().startswith("[預算提醒]") and "先完成報告主體" in m.text() for m in agent.history)
    end = next(d for k, d in events if k == "turn_end")
    assert end["budget"]["checkpoints"] == [0.5, 0.8] and end["budget"]["baseline"]["source"] == "synthetic"
    run = get_hub(rt).store.run(agent._run_rec.rid)
    assert run["state"]["usage"]["task_type"] == "other" and run["state"]["usage"]["steps"] == 5
    assert run["state"]["usage"]["tokens"] > 0 and run["state"]["budget"]["checkpoints"] == [0.5, 0.8]


# ------------------------------------------------------------------ the hard cap
def _files_task(n):
    return [msg("", [("write_file", {"path": f"part{i}.md", "content": f"# 第 {i} 部分\n內容"})]) for i in range(n)] + \
        [msg("全部完成")]


async def test_the_cap_pauses_and_hands_over_what_is_finished(home, config):
    config.data["usage"]["task_max_tokens"] = 5000
    asked = []

    async def ask(q, options, multi, allow_text):
        asked.append((q, [o["label"] for o in options]))
        return {"choices": ["停止並交付"], "text": ""}
    rt, agent, events = make(config, ScriptedProvider(_files_task(8)), ask=ask)
    out = await agent.run("寫八個部分")
    st = agent.last_status
    assert st["status"] == "incomplete" and st["error"]["kind"] == "budget"
    assert "已達每個任務的上限" in st["error"]["summary"] and "5,000 計費 tokens（設定）" in st["error"]["summary"]
    assert asked and asked[0][1] == ["停止並交付", "追加 1.5 倍並繼續"] and "達到每個任務的上限" in asked[0][0]
    assert out.startswith("⏸ 任務已暫停") and "**部分交付**" in out and "[part0.md]" in out
    assert [c["trigger"] for c in ctl(events, "budget")] == ["budget_cap", "partial_delivery"]
    pd = next(d for k, d in events if k == "partial_delivery")
    assert [f["name"] for f in pd["files"]][:2] == ["part0.md", "part1.md"] and pd["status"] == "incomplete"
    run = get_hub(rt).store.run(agent._run_rec.rid)
    assert run["state"]["partial"]["files"] and run["status"] == "incomplete"


async def test_extending_the_cap_lets_it_finish(home, config):
    config.data["usage"]["task_max_tokens"] = 5000

    async def ask(q, options, multi, allow_text):
        return {"choices": ["追加 1.5 倍並繼續"], "text": ""}
    rt, agent, events = make(config, ScriptedProvider(_files_task(4)), ask=ask, cap_extend=3)
    assert await agent.run("寫四個部分") == "全部完成" and agent.last_status["status"] == "done"
    ext = [c for c in ctl(events, "budget") if c["trigger"] == "budget_extended"]
    assert ext and "15,000 計費 tokens（已追加）" in ext[0]["detail"]


async def test_unattended_tasks_stop_at_the_cap(home, config):
    config.data["usage"]["task_max_tokens"] = 3000
    rt, agent, events = make(config, ScriptedProvider(_files_task(5)), ask=None)
    await agent.run("寫五個部分")
    cap = [c for c in ctl(events, "budget") if c["trigger"] == "budget_cap"]
    assert cap and "無人可確認" in cap[0]["detail"] and agent.last_status["error"]["kind"] == "budget"


# ------------------------------------------------------------------ partial delivery
async def test_a_failed_milestone_task_delivers_the_accepted_milestones(home, config):
    from sinobee.agent import progress as P
    plan = {"understanding": "u", "approach": "a", "steps": ["s"], "checklist": [], "risks": [], "ask_user": None,
            "task_type": "document", "facts": {},
            "milestones": [{"id": "m1", "title": "推進", "goal": "整理推進", "outputs": ["push.md"]},
                           {"id": "m2", "title": "浮空", "goal": "整理浮空", "depends_on": ["m1"]},
                           {"id": "m3", "title": "整合", "goal": "組成報告", "depends_on": ["m2"]}]}
    config.data["agent"].update(plan="always", consult_max=0)
    config.data["agent"]["progress"] = {**P.DEFAULTS, "k": 3}
    main = ScriptedProvider(
        [msg("", [("write_file", {"path": "push.md", "content": "# 推進\n3 kN"})]),
         msg("", [("milestone", {"action": "done", "summary": "推力 3 kN", "outputs": ["push.md"]})])]
        + [msg("", [("read_file", {"path": f"missing{i}.md"})]) for i in range(10)])
    rt, agent, events = make(config, main, ScriptedProvider([msg(json.dumps(plan, ensure_ascii=False))]))
    out = await agent.run("寫 Roadster 報告")
    assert agent.last_status["status"] == "failed"
    assert "**已完成並驗收的里程碑**" in out and "✓ m1 推進：推力 3 kN" in out and "產出：push.md" in out
    assert "- [ ] m2 浮空：整理浮空" in out and "- [ ] m3 整合" in out and "從 m2 接著做" in out
    end = next(d for k, d in events if k == "turn_end")
    rid = end["failure"]
    rep = failures.get(config.home, rid)
    assert rep["partial"]["done"][0]["id"] == "m1" and [m["id"] for m in rep["partial"]["left"]] == ["m2", "m3"]
    assert end["partial"]["left"][0]["id"] == "m2"
    usage = get_hub(rt).store.run(agent._run_rec.rid)["state"]["usage"]
    assert usage["task_type"] == "document" and usage["milestones"] is True and usage["est_steps"] == 30


def test_nothing_to_deliver_means_no_delivery(home, config):
    class A:
        _msr = None

        class ctx:
            state = {}
    A.ctx.state = {"checklist": []}
    assert B.partial_delivery(A(), {"artifacts": []}, "error", "x") is None


def test_settings_save_the_task_cap(home, config):
    from fastapi.testclient import TestClient
    from sinobee.server import create_app
    rt = Runtime(config, provider=ScriptedProvider())
    with TestClient(create_app(rt)) as c:
        r = c.put("/api/settings", json={"usage": {"task_max_usd": "2.5", "task_max_tokens": "800000"}})
        assert r.status_code == 200
        assert rt.config.get("usage.task_max_usd") == 2.5 and rt.config.get("usage.task_max_tokens") == 800000
