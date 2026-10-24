"""Sandbox plan stage 3, F1: a task in the sandbox may be lent your Mac's browser or screen - asked once, for this
task, shown at the top with 停止, taken back when the task ends."""
import asyncio

import pytest

from sinobee.sandbox import sandbox_map, screen_executor
from sinobee.sandbox.executor import RunResult, SandboxExecutor
from sinobee.sandbox.manager import SandboxManager
from sinobee.tools import browser, desktop, sandbox_tools
from sinobee.tools.registry import REGISTRY, ToolContext
from sinobee.tools.sandbox_tools import LEND, NOT_LEND, lend_end
from sinobee.types import ToolCall


def dispatch(ctx, name, **args):
    return asyncio.run(REGISTRY.dispatch(ToolCall("1", name, args), ctx))


class Box(SandboxExecutor):
    async def run(self, command, cwd, timeout, env=None):
        return RunResult(0, "ok")

    async def prepare(self):
        self.prepared = True


@pytest.fixture
def ctx(config, monkeypatch):
    monkeypatch.setattr(SandboxManager, "unavailable", lambda self, max_age=60: None)
    monkeypatch.setattr(desktop, "host_available", lambda: True)
    config.data["permissions"]["approval"] = "sandbox"
    config.data["permissions"]["toolsets"] = ["shell", "files", "web", "browser", "desktop"]
    events, asked, answers = [], [], []

    async def ask(q, options, multi, text):
        asked.append(q)
        return {"choices": [answers.pop(0)]} if answers else None

    async def approve(tool, args, reason):
        asked.append(("approve", tool))
        return False
    c = ToolContext(config=config, session_id="c1", ask=ask, approve=approve, emit=lambda k, d: events.append((k, d)))
    c.state["executor"] = Box(config, sandbox_map(config.workspace), "c1", SandboxManager(config))
    c.events, c.asked, c.answers = events, asked, answers
    return c


def test_lending_is_asked_for_and_routes_the_browser_and_desktop(ctx):
    assert screen_executor(ctx, "browser").kind == "sandbox" and browser._key(ctx) == "sandbox:c1"
    ctx.answers.append(NOT_LEND)
    out = dispatch(ctx, "request_host_screen", what=["browser"], reason="登入公司後台下載報表").content
    assert "did not lend" in out and "登入公司後台" in ctx.asked[0] and "停止" in ctx.asked[0]
    assert screen_executor(ctx, "browser").kind == "sandbox"
    ctx.answers.append(LEND)
    out = dispatch(ctx, "request_host_screen", what=["browser"], reason="登入公司後台下載報表").content
    assert "lent for this task" in out
    assert screen_executor(ctx, "browser").kind == "host" and browser._key(ctx) == "host"
    assert screen_executor(ctx, "desktop").kind == "sandbox"               # only what was asked for
    assert ctx.events[-1] == ("host_screen", {"on": True, "what": ["browser"], "reason": "登入公司後台下載報表"})
    assert "already lent" in dispatch(ctx, "request_host_screen", what=["browser"], reason="x").content


def test_on_your_mac_the_dangerous_ones_ask_and_sinobees_own_page_is_off_limits(ctx):
    ctx.state["host_screen"] = {"browser", "desktop"}
    dispatch(ctx, "browser", action="evaluate", script="document.cookie")
    assert ("approve", "browser") in ctx.asked                              # not "contained" any more
    out = dispatch(ctx, "browser", action="navigate", url="http://localhost:10024/")
    assert "SinoBee's own page" in out.content


def test_stop_and_the_end_of_the_task_take_it_back(ctx, monkeypatch):
    ctx.state["host_screen"] = {"desktop"}
    assert lend_end(ctx, lambda k, d: ctx.events.append((k, d)), stopped=True)
    assert ctx.events[-1] == ("host_screen", {"on": False, "what": ["desktop"], "stopped": True})
    out = dispatch(ctx, "screenshot")
    assert out.is_error and "took back" in out.content                      # said once
    assert screen_executor(ctx, "desktop").kind == "sandbox"
    assert not lend_end(ctx)                                                 # nothing lent any more


async def test_the_task_end_returns_it(config, provider, monkeypatch):
    from sinobee.agent import Agent, Runtime
    agent = Agent(Runtime(config, provider=provider), "c1")
    seen = []
    agent.emit_fn = lambda k, d: seen.append((k, d))
    agent.ctx.state["host_screen"] = {"browser"}
    await agent._sandbox_task_end()
    assert "host_screen" not in agent.ctx.state and ("host_screen", {"on": False, "what": ["browser"],
                                                                     "stopped": False}) in seen


def test_not_offered_outside_the_sandbox(config, monkeypatch):
    config.data["permissions"]["approval"] = "confirm"
    assert not sandbox_tools.host_screen_available(config)


def test_the_banner_api(config, provider):
    from fastapi.testclient import TestClient

    from sinobee.agent import Runtime
    from sinobee.server import create_app
    c = TestClient(create_app(Runtime(config, provider=provider)))
    assert c.get("/api/host-screen/nobody").json() == {"what": []}
    assert c.post("/api/host-screen/nobody/stop").json() == {"stopped": False}
