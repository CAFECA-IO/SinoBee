"""Harness guards for weaker models: tool errors are errors, identical failing calls are refused, a skill's
entry script runs on the natural call, active skills stay pinned, and a turn that ends in failure is not 'done'."""
from __future__ import annotations

import time

import pytest

from sinobee.agent import Agent, Runtime
from sinobee.types import ChatResponse, Message, ToolCall, Usage

SKILL = "ai-video-production"


class Scripted:
    label = "test/scripted"
    provider_name = "openai"
    model = "gpt-4.1"

    def __init__(self, steps):
        self.steps = list(steps)
        self.requests = []

    async def chat(self, messages, tools=None, **kw):
        self.requests.append(messages)
        out = self.steps.pop(0) if self.steps else "好的，影片已經完成。"
        return ChatResponse(out if isinstance(out, Message) else Message("assistant", out), Usage(10, 5, 0))

    async def embed(self, texts):
        raise RuntimeError("no embeddings")


def call(tool, **args):
    return Message("assistant", "", [ToolCall(f"c{time.time_ns()}", tool, args)])


@pytest.fixture
def cfg(config):
    config.data["permissions"]["toolsets"] = ["files", "skills", "shell"]
    config.data["permissions"]["approval"] = "auto"
    config.data.setdefault("learning", {})["enabled"] = False
    return config


def events(agent):
    seen = []
    agent.on_event = lambda name, data: seen.append((name, data)) if hasattr(agent, "on_event") else None
    return seen


async def run(cfg, steps):
    model = Scripted(steps)
    rt = Runtime(cfg, provider=model)
    agent = Agent(rt, session_id="robust")
    seen = []
    orig = agent._emit
    agent._emit = lambda ev, data: (seen.append((ev, data)), orig(ev, data))
    await agent.run("拍一段古裝武打片")
    results = [d for e, d in seen if e == "tool_result"]
    return agent, model, results


async def test_natural_call_runs_the_skill_entry(cfg):
    # the exact call the weak model kept making: project.json given as `script`
    agent, model, results = await run(cfg, [
        call("skill_run", name=SKILL, script="videos/duel/project.json"),
        "需要先寫 plan.json。"])
    assert not results[0]["error"]
    assert '"status": "needs_content"' in results[0]["preview"] and "plan.json" in results[0]["preview"]
    # the skill's quick start is pinned into every later request
    last_user = [m for m in model.requests[-1] if m.role == "user"][-1]
    assert "<active_skills>" in last_user.text() and "plan.json" in last_user.text()


async def test_identical_failing_call_is_refused_and_the_turn_is_not_done(cfg):
    bad = dict(name=SKILL, script="scripts/project.py", args="profiling videos/duel")
    agent, model, results = await run(cfg, [call("skill_run", **bad) for _ in range(6)])
    assert results[0]["error"] and "not a command" in results[0]["preview"]
    assert all(r["error"] and r["preview"].startswith("ERROR: REPEATED CALL") for r in results[1:])
    assert len(results) == 4                                   # stopped after 3 refused repeats
    assert agent.last_status["status"] == "incomplete"
    assert "重複執行" in agent.last_status["error"]["summary"]


async def test_stopping_right_after_an_error_is_incomplete(cfg):
    agent, model, results = await run(cfg, [
        call("read_file", path="no/such/file.txt"),
        "完成了！影片已經準備好。"])
    assert results[0]["error"]
    assert agent.last_status["status"] == "incomplete"


async def test_error_strings_are_flagged_and_recovery_is_done(cfg):
    agent, model, results = await run(cfg, [
        call("skill_run", name=SKILL, script="scripts/nope.py"),     # no such script -> entry gets it as input
        call("read_file", path="no/such/file.txt"),
        call("list_dir", path="."),
        "看過了。"])
    assert results[1]["error"] and not results[2]["error"]
    assert agent.last_status["status"] == "done"


async def test_intermediate_files_are_reported_as_artifacts(cfg):
    agent, model, results = await run(cfg, [
        call("write_file", path="out/notes.md", content="# 草稿"),
        call("shell", command="mkdir -p v/qc && printf x > v/qc/SC01.jpg && echo '{\"folder\": \"v\", \"sheet\": \"qc/SC01.jpg\"}'"),
        call("read_file", path="out/notes.md"),
        "好了。"])
    arts = agent.last_status and [a for a in agent.ctx.state.get("artifact_seen", set())]
    names = sorted(a.rsplit("/", 1)[-1] for a in arts)
    assert names == ["SC01.jpg", "notes.md"]                     # reading does not count as producing


async def test_steering_message_reaches_the_running_task(cfg):
    model = Scripted([])
    rt = Runtime(cfg, provider=model)
    agent = Agent(rt, session_id="steer")
    seen = []
    orig = agent._emit
    agent._emit = lambda ev, data: (seen.append((ev, data)), orig(ev, data))
    steps = iter([call("list_dir", path="."), "已改成 9:16 繼續。"])

    def step():
        out = next(steps)
        if isinstance(out, Message) and out.tool_calls:
            agent.steer("改成直式 9:16")                      # user typed while the tool was running
        return out

    async def chat(messages, tools=None, **kw):
        model.requests.append(messages)
        out = step()
        return ChatResponse(out if isinstance(out, Message) else Message("assistant", out), Usage(1, 1, 0))
    model.chat = chat
    await agent.run("做一支影片")
    texts = [m.text() for m in model.requests[-1] if m.role == "user"]
    assert any("[使用者在執行中補充指示] 改成直式 9:16" in t for t in texts)
    assert ("steer_applied", {"text": "改成直式 9:16"}) in seen
    assert agent.last_status["status"] == "done"
