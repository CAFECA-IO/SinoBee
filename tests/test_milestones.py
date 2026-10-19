"""Phase 2: a large task runs as milestones - planned by the thinking model, confirmed by the user when needed,
each milestone with a fresh context, independent ones on sub-agents with staged facts, corrections at the next
boundary or at once, and resumable from the blackboard."""
import asyncio
import json

from sinobee.agent import Agent, Runtime
from sinobee.agent import milestones as M
from sinobee.types import Message, ToolCall

from conftest import ScriptedProvider

BIG = "## 推進系統\n" + "冷氣推進器以高壓氮氣產生推力。" * 400          # a large milestone output


def msg(text="", calls=None):
    return Message("assistant", text, [ToolCall(f"c{i}{n}{len(json.dumps(a, ensure_ascii=False))}", n, a)
                                       for i, (n, a) in enumerate(calls or [])])


def plan(milestones, **extra):
    return msg(json.dumps({"understanding": "做報告", "approach": "分段", "steps": ["a", "b"], "checklist": [],
                           "risks": [], "ask_user": None, "task_type": "document", "milestones": milestones,
                           "facts": {"單位": "kN"}, **extra}, ensure_ascii=False))


THREE = [{"id": "m1", "title": "研究推進", "goal": "整理推進系統", "outputs": ["push.md"]},
         {"id": "m2", "title": "研究浮空", "goal": "整理浮空極限", "depends_on": ["m1"], "outputs": ["hover.md"]},
         {"id": "m3", "title": "整合報告", "goal": "組成報告並檢查", "depends_on": ["m1", "m2"]}]


def setup(config, main, thinker, ask=None, platform="web"):
    config.data["agent"].update(plan="always", quality="fast", failure_diagnosis=False)
    events = []
    rt = Runtime(config, provider=main, thinking_provider=thinker, aux_provider=ScriptedProvider())
    agent = Agent(rt, session_id="ms-sess", platform=platform, emit=lambda k, d: events.append((k, d)), ask=ask)
    return rt, agent, events


def controls(events, prefix="milestone"):
    return [d["trigger"] for k, d in events if k == "control" and d["trigger"].startswith(prefix)]


# ------------------------------------------------------------------ the plan
def test_parse_normalises_the_plan():
    p = M.parse({"milestones": [{"id": "a", "goal": "x", "parallel": True, "role": "researcher"},
                                {"id": "b", "goal": "y", "parallel": True, "depends_on": ["a", "zz"]},
                                {"id": "c", "goal": "z", "parallel": True}], "facts": {"u": "kN"}, "task_type": "x"})
    ms = p["milestones"]
    assert [m["parallel"] for m in ms] == [True, False, False]          # dependent / last are never parallel
    assert ms[1]["depends_on"] == ["a"] and p["task_type"] == "other" and p["facts"] == {"u": "kN"}
    assert M.parse({"milestones": [{"goal": "only one"}]}) is None
    plan_ = M.Plan(p)
    ms[0]["status"], ms[1]["status"] = "done", "delegated"
    cancel = plan_.apply_replan({"milestones": [{"id": "b", "goal": "y2"}, {"id": "d", "goal": "new"}],
                                 "cancel": ["b"]})
    assert cancel == ["b"] and [m["id"] for m in plan_.milestones] == ["a", "b", "d"]
    assert plan_.get("a")["status"] == "done" and plan_.get("b")["status"] == "todo" and plan_.data["revision"] == 2


# ------------------------------------------------------------------ a clear task: shown and started, fresh context
async def test_a_clear_large_task_runs_milestone_by_milestone_with_a_fresh_context(home, config):
    asked = []

    async def ask(*a):
        asked.append(a)
        return {"choices": ["開始"], "text": ""}
    thinker = ScriptedProvider([plan(THREE)])
    main = ScriptedProvider([
        msg("", [("write_file", {"path": "push.md", "content": BIG})]),
        msg("", [("milestone", {"action": "done", "summary": "推進：氮氣推力約 3 kN，見 push.md", "outputs": ["push.md"]})]),
        msg("", [("write_file", {"path": "hover.md", "content": "## 浮空\n浮空極限約 8 秒。"})]),
        msg("", [("milestone", {"action": "done", "summary": "浮空：極限約 8 秒，見 hover.md", "outputs": ["hover.md"]})]),
        msg("報告完成：推進 3 kN、浮空 8 秒。")])
    rt, agent, events = setup(config, main, thinker, ask=ask)
    out = await agent.run("寫一份 Roadster 推進與浮空的完整分析報告")
    assert out.startswith("報告完成") and agent.last_status["status"] == "done"
    assert not asked                                                    # clear and no sub-agents: no waiting
    assert controls(events) == ["milestone_started", "milestone_done", "milestone_done"]
    # fresh context: the step after m1 no longer carries m1's 6,000-character file, only its summary
    later = main.requests[2][0]
    text = "\n".join(m.text() for m in later)
    assert BIG[:200] not in text and "推進：氮氣推力約 3 kN" in text and "[里程碑 m1 完成]" in text
    assert "<milestones>" in later[-1].text() and "CURRENT: m2" in later[-1].text()
    # the tool exists only for a milestone task, and the plan is saved, done, with the facts
    assert any(t.name == "milestone" for t in main.requests[0][1])
    saved = M.load(agent.rt._work_hub.store, "session:ms-sess")
    assert saved.finished() and [m["status"] for m in saved.milestones] == ["done", "done", "done"]
    assert M.facts(agent.rt._work_hub.store, "session:ms-sess") == {"單位": "kN"}
    end = next(d for k, d in events if k == "turn_end")
    assert [m["status"] for m in end["milestones"]] == ["done", "done", "done"]
    assert any(k == "milestones" and d["phase"] == "running" for k, d in events)
    # the whole work stays in the transcript and the turn's work log
    assert any(c.arguments.get("content") == BIG for m in rt.memory.load_messages("ms-sess") for c in m.tool_calls)
    assert any(c.name == "write_file" and c.arguments["path"] == "push.md" for m in agent._turn_messages(0)
               for c in m.tool_calls)


async def test_fresh_context_keeps_a_long_report_from_compacting(home, config):
    """Acceptance (phase 2): the large report task no longer compacts its context again and again."""
    config.data["model"]["context_window"] = 40000
    config.data["model"]["max_tokens"] = 2000
    parts = [{"id": f"m{i}", "title": f"第 {i} 章", "goal": f"寫第 {i} 章"} for i in range(1, 6)]
    script = []
    for i in range(1, 6):
        script += [msg("", [("write_file", {"path": f"ch{i}.md", "content": BIG})]),
                   msg("", [("milestone", {"action": "done", "summary": f"第 {i} 章寫好，ch{i}.md", "outputs": [f"ch{i}.md"]})])]
    script.append(msg("五章都寫好了。"))
    rt, agent, events = setup(config, ScriptedProvider(script), ScriptedProvider([plan(parts)]))
    assert await agent.run("寫五章的報告") == "五章都寫好了。"
    assert not any(k == "status" and "壓縮上下文" in d.get("message", "") for k, d in events)
    # the same work without milestones keeps every chapter in the context and compacts
    config.data["agent"]["plan"] = "off"
    flat = []
    for i in range(1, 6):
        flat.append(msg("", [("write_file", {"path": f"c{i}.md", "content": BIG})]))
    flat.append(msg("寫好了。"))
    ev2 = []
    a2 = Agent(Runtime(config, provider=ScriptedProvider(flat), aux_provider=ScriptedProvider()), session_id="flat",
               emit=lambda k, d: ev2.append((k, d)))
    await a2.run("寫五章的報告")
    assert any(k == "status" and "壓縮上下文" in d.get("message", "") for k, d in ev2)


async def test_done_is_refused_while_the_milestones_acceptance_is_open(home, config):
    ms = [{"id": "m1", "title": "A", "goal": "a", "acceptance": ["a.md 存在"]}, {"id": "m2", "title": "B", "goal": "b"}]
    main = ScriptedProvider([msg("", [("milestone", {"action": "done", "summary": "做完了"})]), msg("（先停）")])
    rt, agent, events = setup(config, main, ScriptedProvider([plan(ms)]))
    config.data["agent"]["quality_rounds"] = 0
    await agent.run("做 A 和 B")
    res = next(d for k, d in events if k == "tool_result" and d["name"] == "milestone")
    assert "acceptance items of this milestone are still open" in res["preview"] and "#1 [m1] a.md 存在" in res["preview"]
    from sinobee.tools import checklist_tools
    assert any(i["text"] == "[m1] a.md 存在" and i["milestone"] == "m1" for i in checklist_tools.get(agent.ctx))


# ------------------------------------------------------------------ confirmation
async def test_an_ambiguous_plan_waits_and_a_correction_replans(home, config):
    answers = [{"choices": [], "text": "第二部分改成比較三家廠商"}, {"choices": ["開始"], "text": ""}]
    asked = []

    async def ask(question, options, multi, allow_text):
        asked.append(question)
        return answers.pop(0)
    thinker = ScriptedProvider([
        plan(THREE, ask_user="報告要給工程師還是一般讀者？"),
        msg(json.dumps({"milestones": [{"id": "m2", "title": "比較三家廠商", "goal": "比較"},
                                       {"id": "m3", "title": "整合報告", "goal": "組成", "depends_on": ["m2"]},
                                       {"id": "m1", "title": "研究推進", "goal": "整理推進系統"}],
                        "note": "第二部分改為廠商比較"}, ensure_ascii=False))])
    main = ScriptedProvider([msg("先做到這裡。")])
    config.data["agent"]["quality_rounds"] = 0
    rt, agent, events = setup(config, main, thinker, ask=ask)
    await agent.run("寫 Roadster 報告")
    assert len(asked) == 2 and "工程師還是一般讀者" in asked[0] and "m1. 研究推進" in asked[0]
    assert "比較三家廠商" in asked[1]
    assert controls(events) == ["milestone_corrected", "milestone_replanned", "milestone_confirmed"]
    assert agent._msr.plan.data["revision"] == 2 and agent._msr.plan.data["confirmed"] == "user"


async def test_sub_agents_make_the_plan_wait_but_unattended_runs_never_do(home, config):
    par = [{"id": "m1", "title": "研究", "goal": "查資料", "parallel": True, "role": "researcher"},
           {"id": "m2", "title": "寫", "goal": "寫報告", "depends_on": ["m1"]}]
    rt, agent, events = setup(config, ScriptedProvider([msg("好")]), ScriptedProvider([plan(par)]), ask=None,
                              platform="job")
    from sinobee.agent.milestone_runner import MilestoneRunner
    r = MilestoneRunner(agent, M.Plan(M.parse({"milestones": par})), "x")
    assert r.needs_confirmation({}) is False                            # a scheduled job has no one to ask

    async def ask(*a):
        return None
    agent2 = Agent(rt, session_id="s2", platform="web", ask=ask)
    assert MilestoneRunner(agent2, M.Plan(M.parse({"milestones": par})), "x").needs_confirmation({}) is True
    assert MilestoneRunner(agent2, M.Plan(M.parse({"milestones": par})), "[自動續跑] x").needs_confirmation({}) is False


# ------------------------------------------------------------------ sub-agents and facts
async def test_parallel_milestone_runs_on_a_sub_agent_and_its_facts_are_merged(home, config, monkeypatch):
    par = [{"id": "m1", "title": "查規格", "goal": "查車重與推力", "parallel": True, "role": "researcher"},
           {"id": "m2", "title": "畫圖", "goal": "畫推進示意"},
           {"id": "m3", "title": "整合", "goal": "寫報告", "depends_on": ["m1", "m2"]}]
    started = []

    async def fake_sub(rt, *, role, task, board_scope, **kw):
        started.append((role, task, kw.get("context", "")))
        await asyncio.sleep(0.05)
        store = rt._work_hub.store
        store.board_write(board_scope, "fact", "1200 kg", key="stage:sub-x:車重", author="sub")
        store.board_write(board_scope, "fact", "N", key="stage:sub-x:單位", author="sub")
        return {"role": role, "status": "done", "result": "車重 1200 kg，推力 3 kN", "session_id": "sub-x"}
    monkeypatch.setattr("sinobee.work.agents.run_subagent", fake_sub)

    async def ask(*a):
        return {"choices": ["開始"], "text": ""}
    thinker = ScriptedProvider([plan(par), msg(json.dumps({"value": "kN", "reason": "報告其餘部分都用 kN"}, ensure_ascii=False))])
    main = ScriptedProvider([
        msg("", [("write_file", {"path": "fig.svg", "content": "<svg/>"})]),
        msg("", [("milestone", {"action": "done", "summary": "示意圖 fig.svg", "outputs": ["fig.svg"]})]),
        msg("報告完成")])
    rt, agent, events = setup(config, main, thinker, ask=ask)
    assert await agent.run("Roadster 報告") == "報告完成"
    assert started and started[0][0] == "researcher" and "單位: kN" in started[0][2]
    plan_ = agent._msr.plan
    assert [m["status"] for m in plan_.milestones] == ["done", "done", "done"]
    assert plan_.get("m1")["summary"] == "車重 1200 kg，推力 3 kN"
    store = rt._work_hub.store
    assert M.facts(store, "session:ms-sess") == {"單位": "kN", "車重": "1200 kg"}
    assert any("事實衝突 單位" in r["content"] for r in store.board_read("session:ms-sess", kinds=["issue"]))
    ctl = [d for k, d in events if k == "control" and d["trigger"] == "fact_conflict"]
    assert ctl and "採用「kN」" in ctl[0]["detail"]
    assert controls(events)[:2] == ["milestone_confirmed", "milestone_delegated"]
    # m3 needed m1: the integration step saw the sub-agent's summary
    assert any("車重 1200 kg" in m.text() for m in main.requests[-1][0])


async def test_a_sub_agent_that_fails_is_taken_over(home, config, monkeypatch):
    par = [{"id": "m1", "title": "查", "goal": "查", "parallel": True}, {"id": "m2", "title": "寫", "goal": "寫", "depends_on": ["m1"]}]

    async def fake_sub(rt, **kw):
        return {"status": "failed", "result": "找不到資料", "session_id": "sub-y"}
    monkeypatch.setattr("sinobee.work.agents.run_subagent", fake_sub)

    async def ask(*a):
        return {"choices": ["開始"], "text": ""}
    main = ScriptedProvider([msg("", [("milestone", {"action": "done", "summary": "改由我查到了"})]),
                             msg("", [("milestone", {"action": "done", "summary": "寫好"})]), msg("完成")])
    rt, agent, events = setup(config, main, ScriptedProvider([plan(par)]), ask=ask)
    assert await agent.run("報告") == "完成"
    assert "milestone_taken_over" in controls(events)
    assert [m["status"] for m in agent._msr.plan.milestones] == ["done", "done"]


def test_a_sub_agents_facts_go_to_its_staging_area(home, config):
    from sinobee.tools.registry import ToolContext
    from sinobee.tools.work_tools import board_write
    rt = Runtime(config, provider=ScriptedProvider())
    from sinobee.work.hub import get_hub
    hub = get_hub(rt)

    class A:
        session_id = "sub-abc"
    ctx = ToolContext(config=config, session_id="sub-abc", memory=rt.memory)
    ctx.state.update(runtime=rt, depth=1, agent=A(), board_scope="session:main")
    r = asyncio.run(board_write(ctx, "fact", "N", key="單位"))
    assert r["key"] == "stage:sub-abc:單位" and "staged" in r["note"]
    assert M.facts(hub.store, "session:main") == {} and M.staged(hub.store, "session:main", "sub-abc") == {"單位": "N"}
    assert "a fact needs key" in asyncio.run(board_write(ctx, "fact", "x"))


# ------------------------------------------------------------------ corrections while it runs
async def test_a_correction_applies_at_the_next_boundary(home, config):
    thinker = ScriptedProvider([plan(THREE), msg(json.dumps({
        "milestones": [{"id": "m4", "title": "比較廠商", "goal": "比較三家"}, {"id": "m3", "title": "整合報告", "goal": "組成",
                                                                       "depends_on": ["m4"]}], "note": "改做廠商比較"},
        ensure_ascii=False))])
    agent_ref = {}

    def step1(messages):
        agent_ref["a"].milestone_feedback("浮空部分不要了，改成比較三家廠商", "boundary")
        return msg("", [("write_file", {"path": "push.md", "content": "推進"})])
    main = ScriptedProvider([step1, msg("", [("milestone", {"action": "done", "summary": "推進完成"})]),
                             msg("", [("milestone", {"action": "done", "summary": "比較完成"})]), msg("完成")])
    rt, agent, events = setup(config, main, thinker)
    agent_ref["a"] = agent
    assert await agent.run("Roadster 報告") == "完成"
    ids = [(m["id"], m["status"]) for m in agent._msr.plan.milestones]
    assert ids == [("m1", "done"), ("m4", "done"), ("m3", "done")]
    assert controls(events) == ["milestone_started", "milestone_done", "milestone_replanned", "milestone_done"]
    assert any(k == "steer_applied" and d.get("milestone_mode") == "boundary" for k, d in events)


async def test_interrupt_and_replan_now_keeps_finished_work_and_is_not_a_stop(home, config):
    thinker = ScriptedProvider([plan(THREE), msg(json.dumps({
        "milestones": [{"id": "m5", "title": "改寫浮空", "goal": "用新的假設"}, {"id": "m3", "title": "整合報告", "goal": "組成",
                                                                       "depends_on": ["m5"]}]}, ensure_ascii=False))])
    agent_ref = {}

    def m2_step(messages):
        agent_ref["a"].milestone_feedback("浮空的假設錯了，用 3 秒重算", "now")
        return msg("", [("write_file", {"path": "hover.md", "content": "浮空 8 秒"})])
    main = ScriptedProvider([msg("", [("milestone", {"action": "done", "summary": "推進完成"})]), m2_step,
                             msg("", [("milestone", {"action": "done", "summary": "浮空 3 秒"})]), msg("完成")])
    rt, agent, events = setup(config, main, thinker)
    agent_ref["a"] = agent
    assert await agent.run("Roadster 報告") == "完成"
    plan_ = agent._msr.plan
    assert [(m["id"], m["status"]) for m in plan_.milestones] == [("m1", "done"), ("m5", "done"), ("m3", "done")]
    assert "milestone_interrupted" in controls(events) and agent.last_status["status"] == "done"
    run = rt._work_hub.store.run(agent._run_rec.rid)
    assert not run["state"].get("user_stopped")
    # the interrupted milestone's messages left the context with a note saying so
    assert any("[里程碑 m2 中斷]" in m.text() for m in rt.memory.load_messages("ms-sess"))


# ------------------------------------------------------------------ resume
async def test_an_unfinished_plan_resumes_at_the_first_open_milestone(home, config):
    main = ScriptedProvider([msg("", [("milestone", {"action": "done", "summary": "推進完成"})]), msg("（中斷前）")])
    config.data["agent"]["quality_rounds"] = 0
    rt, agent, events = setup(config, main, ScriptedProvider([plan(THREE)]))
    await agent.run("Roadster 報告")
    saved = M.load(rt._work_hub.store, "session:ms-sess")
    assert [m["status"] for m in saved.milestones][:1] == ["done"] and not saved.finished()
    main2 = ScriptedProvider([msg("", [("milestone", {"action": "done", "summary": "浮空完成"})]), msg("完成")])
    ev2 = []
    a2 = Agent(Runtime(config, provider=main2, thinking_provider=ScriptedProvider()), session_id="ms-sess",
               platform="web", emit=lambda k, d: ev2.append((k, d)))
    assert await a2.run("[續跑] 上一次的任務在中途被中斷了") == "完成"
    assert controls(ev2)[0] == "milestone_started"
    started = next(d for k, d in ev2 if k == "control" and d["trigger"] == "milestone_started")
    assert "接續未完成的里程碑" in started["detail"]
    assert "CURRENT: m2" in main2.requests[0][0][-1].text()


def test_milestone_api(home, config):
    from fastapi.testclient import TestClient
    from sinobee.server import create_app
    rt = Runtime(config, provider=ScriptedProvider())
    from sinobee.work.hub import get_hub
    store = get_hub(rt).store
    with TestClient(create_app(rt)) as c:
        assert c.get("/api/sessions/zz/milestones").json() == {"plan": None}
        M.save(store, "session:zz", M.Plan(M.parse({"milestones": THREE})))
        r = c.get("/api/sessions/zz/milestones").json()
        assert r["plan"]["total"] == 3 and r["running"] is False
        assert c.post("/api/sessions/zz/milestones/feedback", json={"text": "改", "mode": "now"}).status_code == 409
        assert c.post("/api/sessions/zz/milestones/feedback", json={"text": ""}).status_code == 400
