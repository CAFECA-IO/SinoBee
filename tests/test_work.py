"""Background work: blackboard, sub-agents, workflows (graph), schedules, approvals of unattended runs."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import time

import pytest

from sinobee.agent import Agent, Runtime
from sinobee.types import ChatResponse, Message, ToolCall, Usage
from sinobee.work import schedule as S
from sinobee.work.graph import WorkflowError, evaluate, render, validate
from sinobee.work.hub import get_hub


class RoleModel:
    """Fake model that answers by looking at who is asking (system prompt) and what (last user message)."""
    label = "test/role"
    provider_name = "openai"
    model = "gpt-4.1"

    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.recorder = None

    async def chat(self, messages, tools=None, **kw):
        system = messages[0].text() if messages else ""
        last = next((m for m in reversed(messages) if m.role in ("user", "tool")), None)
        self.calls.append((system[:4000], last.text() if last else "", [t.name for t in tools or []]))
        out = self.handler(system, messages, tools or [])
        msg = out if isinstance(out, Message) else Message("assistant", out)
        return ChatResponse(msg, Usage(100, 50, 0))

    async def embed(self, texts):
        raise RuntimeError("no embeddings")


def call(_tool, **args):
    return Message("assistant", "", [ToolCall(f"c{time.time_ns()}", _tool, args)])


def tool_results(messages):
    return [m for m in messages if m.role == "tool"]


@pytest.fixture
def work_config(config):
    config.data["permissions"]["toolsets"] = ["files", "web", "memory", "skills", "work"]
    config.data["permissions"]["toolsets_v"] = 2
    return config


# ------------------------------------------------------------------ schedules
def test_schedules_next_runs_and_descriptions():
    base = dt.datetime(2026, 10, 16, 8, 30).timestamp()      # Friday
    s = S.validate({"type": "daily", "time": "09:00", "days": [0, 1, 2, 3, 4]})
    nxt = S.upcoming(s, base, 3)
    assert [dt.datetime.fromtimestamp(t).strftime("%a %H:%M") for t in nxt] == ["Fri 09:00", "Mon 09:00", "Tue 09:00"]
    assert S.describe(s) == "平日 09:00"
    c = S.validate({"type": "cron", "expr": "*/15 9-10 * * 1-5"})
    assert dt.datetime.fromtimestamp(S.next_run(c, base)).strftime("%H:%M") == "09:00"
    assert dt.datetime.fromtimestamp(S.next_run(c, dt.datetime(2026, 10, 16, 10, 50).timestamp())).strftime("%a %H:%M") == "Mon 09:00"
    i = S.validate({"type": "interval", "every": 3600})
    assert S.next_run(i, base, last_run=base - 10) == base + 3590
    with pytest.raises(S.ScheduleError):
        S.validate({"type": "interval", "every": 5})
    with pytest.raises(S.ScheduleError):
        S.validate({"type": "cron", "expr": "61 * * * *"})
    once = S.validate({"type": "once", "at": "2026-10-20T07:00"})
    assert S.next_run(once, base) == once["at"] and S.next_run(once, base, last_run=base) is None


# ------------------------------------------------------------------ graph helpers
def test_expressions_and_templates():
    scope = {"inputs": {"topic": "EV", "replace": False}, "nodes": {"plan": {"output": {"questions": ["a", "b"]}},
                                                                 "review": {"output": "VERDICT: revise - x"}}}
    assert evaluate("len(nodes.plan.output.questions) == 2 and contains(nodes.review.output, 'revise')", scope)
    assert evaluate("'replace' if inputs.replace else 'fail'", scope) == "fail"
    assert render("{{nodes.plan.output.questions}}", scope) == ["a", "b"]           # keeps the type
    assert render("主題：{{inputs.topic}}", scope) == "主題：EV"
    assert render("{{nodes.missing.output or inputs.topic}}", scope) == "EV"
    with pytest.raises(WorkflowError):
        evaluate("__import__('os')", scope)
    with pytest.raises(WorkflowError, match="循環"):
        validate({"name": "xy", "nodes": [{"id": "a", "type": "action", "needs": ["b"]},
                                         {"id": "b", "type": "action", "needs": ["a"]}]})


# ------------------------------------------------------------------ blackboard + sub-agents
async def test_delegate_runs_sub_agents_in_parallel_on_a_shared_board(work_config):
    active, peak = {"n": 0}, {"n": 0}

    def handler(system, messages, tools):
        names = {t.name for t in tools}
        if "RESEARCHER sub-agent" in system:
            done = tool_results(messages)
            if not done:
                import re
                topic = re.search(r"研究：(\S)", messages[-1].text()).group(1)
                return call("board_write", kind="finding", content=f"發現：{topic}", key=topic)
            return "研究完成"
        if "REVIEWER sub-agent" in system:
            assert "write_file" not in names and "delegate" not in names     # read-only, no recursion
            return "VERDICT: pass"
        # main agent
        if not tool_results(messages):
            return call("delegate", tasks=[{"role": "researcher", "task": "研究：甲"},
                                           {"role": "researcher", "task": "研究：乙"},
                                           {"role": "reviewer", "task": "審查黑板"}])
        return "全部完成"

    model = RoleModel(handler)
    orig = model.chat

    async def tracked(messages, tools=None, **kw):
        active["n"] += 1
        peak["n"] = max(peak["n"], active["n"])
        await asyncio.sleep(0.05)
        try:
            return await orig(messages, tools, **kw)
        finally:
            active["n"] -= 1
    model.chat = tracked
    rt = Runtime(work_config, provider=model)
    agent = Agent(rt)
    out = await agent.run("研究甲和乙")
    assert out == "全部完成"
    delegate_result = json.loads(next(m for m in agent.history if m.role == "tool" and m.name == "delegate").text())
    assert [r["status"] for r in delegate_result] == ["done", "done", "done"]
    assert delegate_result[2]["report"] == "VERDICT: pass"
    board = get_hub(rt).store.board_read(f"session:{agent.session_id}")
    assert {b["kind"] for b in board} >= {"finding", "decision"}
    assert sum(1 for b in board if b["kind"] == "finding") == 2
    assert peak["n"] >= 2                                   # sub-agents really ran at the same time
    assert all(s["platform"] not in ("subagent",) for s in rt.memory.sessions())    # hidden from the chat list


async def test_board_is_shown_in_context_and_sub_agents_cannot_delegate(work_config):
    seen = {}

    def handler(system, messages, tools):
        seen["ctx"] = messages[-1].text()
        seen["tools"] = {t.name for t in tools}
        return "ok"
    rt = Runtime(work_config, provider=RoleModel(handler))
    hub = get_hub(rt)
    hub.store.board_write("job:j1", "note", "上次處理到訂單 #1042", key="last_order")
    agent = Agent(rt, board_scope="job:j1", depth=1, tool_filter=lambda t: t.name != "delegate")
    await agent.run("繼續")
    assert "上次處理到訂單 #1042" in seen["ctx"] and "delegate" not in seen["tools"]


# ------------------------------------------------------------------ workflows
def research_handler(system, messages, tools):
    last = messages[-1].text()
    if "拆成 3 到 5 個" in last:
        return '{"questions": ["市場規模", "主要品牌"]}'
    if "RESEARCHER sub-agent" in system:
        if not tool_results(messages):
            return call("board_write", kind="finding", content="資料 X（來源 https://example.com）", key="x")
        return "找到資料 X"
    if "REVIEWER sub-agent" in system:
        return "VERDICT: pass"
    if "WRITER sub-agent" in system:
        return "# 報告\n\n市場很大（https://example.com）"
    return "ok"


async def test_research_workflow_runs_as_a_graph_and_writes_a_report(work_config):
    rt = Runtime(work_config, provider=RoleModel(research_handler))
    hub = get_hub(rt)
    run = hub.start_workflow("research-report", {"topic": "電動機車"}, trigger="manual")
    await asyncio.wait_for(hub.tasks[run["id"]], 30)
    r = hub.store.run(run["id"])
    assert r["status"] == "done", r["error"]
    nodes = r["state"]["nodes"]
    assert nodes["revise"]["status"] == "skipped"                    # review passed
    assert len(nodes["research"]["output"]) == 2 and len(nodes["research"]["sessions"]) == 2
    path = nodes["report"]["output"]["path"]
    text = open(path, encoding="utf-8").read()
    assert text.startswith("# 電動機車") and "市場很大" in text
    detail = hub.run_detail(run["id"])
    assert any(b["kind"] == "finding" for b in detail["board"]) and any(b["kind"] == "artifact" for b in detail["board"])


async def test_unattended_approval_pauses_until_decided_and_survives_cancel(work_config):
    work_config.data["permissions"]["approval"] = "confirm"
    work_config.data["permissions"]["toolsets"].append("shell")

    def handler(system, messages, tools):
        if not tool_results(messages):
            return call("shell", command="rm -rf ./build")
        return "清理完成" if "user did not approve" not in tool_results(messages)[-1].text() else "沒有核准，未清理"
    rt = Runtime(work_config, provider=RoleModel(handler))
    hub = get_hub(rt)
    job = hub.create_job({"name": "清理", "prompt": "清掉 build 資料夾", "schedule": {"type": "manual"}})
    run = hub.run_job(job["id"])
    for _ in range(100):
        await asyncio.sleep(0.02)
        if hub.store.approvals():
            break
    a = hub.store.approvals()[0]
    assert hub.store.run(run["id"])["status"] == "waiting" and "recursive delete" in a["reason"]
    assert hub.overview()["approvals"][0]["id"] == a["id"]
    hub.decide(a["id"], False)
    await asyncio.wait_for(hub.tasks[run["id"]], 10)
    r = hub.store.run(run["id"])
    assert r["status"] == "done" and r["summary"] == "沒有核准，未清理"
    assert hub.store.job(job["id"])["last_status"] == "done"


async def test_scheduler_runs_due_jobs_once_and_keeps_job_memory(work_config):
    prompts = []

    def handler(system, messages, tools):
        prompts.append(messages[-1].text())
        if not tool_results(messages):
            return call("board_write", kind="note", content=f"第 {len(prompts)} 次", key="progress")
        return "記下進度"
    rt = Runtime(work_config, provider=RoleModel(handler))
    hub = get_hub(rt)
    job = hub.create_job({"name": "巡檢", "prompt": "檢查並記錄進度", "schedule": {"type": "interval", "every": 3600}})
    assert job["next_run_at"] and job["upcoming"]
    started = hub.tick(at=job["next_run_at"] + 1)
    assert len(started) == 1
    await asyncio.wait_for(hub.tasks[started[0]], 10)
    j = hub.store.job(job["id"])
    assert j["last_status"] == "done" and j["next_run_at"] > time.time() + 3000
    assert hub.tick(at=time.time()) == []                           # not due again yet
    started = hub.tick(at=j["next_run_at"] + 1)
    await asyncio.wait_for(hub.tasks[started[0]], 10)
    second = [p for p in prompts if "背景工作" in p][-1]
    assert "上次執行" in second and "記下進度" in second           # previous result is carried over
    notes = hub.store.board_read(f"job:{job['id']}", kinds=["note"])
    assert len(notes) == 1                                          # keyed note replaced, not duplicated
    ov = hub.overview()
    assert any(h["type"] == "run" and h["name"] == "巡檢" for h in ov["history"])
    assert ov["upcoming"][0]["job_id"] == job["id"]


async def test_job_create_tool_needs_confirmation(work_config):
    asked = []

    def handler(system, messages, tools):
        if not tool_results(messages):
            return call("job_create", name="每日健檢", schedule={"type": "daily", "time": "09:00"},
                        kind="task", prompt="檢查資料庫")
        return tool_results(messages)[-1].text()[:200]
    rt = Runtime(work_config, provider=RoleModel(handler))

    async def approve(name, args, reason):
        asked.append((name, reason))
        return True
    agent = Agent(rt, approve=approve)
    await agent.run("每天九點幫我檢查資料庫")
    assert asked and asked[0][0] == "job_create" and "每日健檢" in asked[0][1]
    jobs = get_hub(rt).store.jobs()
    assert jobs[0]["name"] == "每日健檢" and jobs[0]["created_by"] == "agent"


async def test_interrupted_workflow_resumes_after_finished_nodes(work_config):
    rt = Runtime(work_config, provider=RoleModel(research_handler))
    hub = get_hub(rt)
    wf = hub.workflow("research-report")
    run = hub.store.create_run(job_id=None, name=wf["title"], kind="workflow", trigger="manual",
                               state={"workflow": wf["name"], "inputs": {"topic": "T"},
                                      "nodes": {"plan": {"status": "done", "output": {"questions": ["q1"]}},
                                                "research": {"status": "running"}}})
    hub.store.update_run(run["id"], status="running")
    resumed = hub.store.interrupt_active()
    assert resumed[0]["id"] == run["id"]
    hub.resume(run["id"])
    await asyncio.wait_for(hub.tasks[run["id"]], 30)
    r = hub.store.run(run["id"])
    assert r["status"] == "done"
    assert len(r["state"]["nodes"]["research"]["output"]) == 1      # used the plan that was already done
