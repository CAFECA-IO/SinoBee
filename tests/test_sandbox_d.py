"""Sandbox plan D + C2: the desktop and browser in the sandbox, the live view, previews, asking for network.
Tests marked docker need Docker and the sinobee-sandbox image; the rest run anywhere."""
import asyncio
import time
import uuid

import pytest

from sinobee.sandbox import sandbox_map
from sinobee.sandbox.executor import Executor, RunResult, SandboxExecutor
from sinobee.sandbox.manager import SandboxManager, manager_for
from sinobee.tools.registry import REGISTRY, ToolContext
from sinobee.tools.sandbox_tools import ALL, NO, ONCE, REMEMBER, clean
from sinobee.types import ToolCall


def dispatch(ctx, name, **args):
    return asyncio.run(REGISTRY.dispatch(ToolCall("1", name, args), ctx))


# ------------------------------------------------------------------ asking for network (no Docker)
class StubManager:
    def __init__(self, config):
        self.config = config
        self.grants, self.open_all, self.takeover, self.pushed = {}, set(), {}, []
        self.connections = []

    def policy(self, conv):
        return SandboxManager.policy(self, conv)

    async def push_policy(self, conv):
        self.pushed.append((conv, sorted(self.grants.get(conv, set())), conv in self.open_all))

    async def connection_log(self):
        return []

    def name_of(self, conv):
        return f"sinobee-{conv}"


class StubBox(SandboxExecutor):
    async def run(self, command, cwd, timeout, env=None):
        return RunResult(0, "ok" + await self._blocked_note())


@pytest.fixture(autouse=True)
def sandbox_wanted(config, monkeypatch, request):
    """These tests act as if the tools ran in the sandbox (the docker ones really do)."""
    if "docker" not in request.keywords:
        monkeypatch.setattr(SandboxManager, "unavailable", lambda self, max_age=60: None)


def sandbox_ctx(config, answer):
    config.data["permissions"]["approval"] = "sandbox"
    m = StubManager(config)
    ex = StubBox(config, sandbox_map(config.workspace), "c1", m)
    asked = []

    async def ask(question, options, multi, allow_text):
        asked.append((question, options))
        return None if answer is None else {"choices": [answer]}
    ctx = ToolContext(config=config, session_id="c1", ask=ask)
    ctx.state["executor"] = ex
    return ctx, m, asked


def test_domain_names_are_cleaned():
    assert clean(["https://GitHub.com/x/y", "api.github.com:443", "*.npmjs.org", "not a domain", "8.8.8.8", "github.com"]) \
        == ["github.com", "api.github.com", "*.npmjs.org", "8.8.8.8"]


@pytest.mark.parametrize("answer", [ONCE, REMEMBER, ALL, NO, None])
def test_request_network_answers(config, answer):
    ctx, m, asked = sandbox_ctx(config, answer)
    out = dispatch(ctx, "request_network", domains=["github.com"], reason="下載範例").content
    assert asked and "github.com" in asked[0][0] and "下載範例" in asked[0][0]
    if answer in (NO, None):
        assert "did not allow" in out and m.pushed == []
        return
    assert m.pushed and "Run the command again" in out
    if answer == ALL:
        assert "c1" in m.open_all
    else:
        assert m.grants["c1"] == {"github.com"}
    remembered = config.get("sandbox.network.remembered")
    assert ("github.com" in remembered) == (answer == REMEMBER)
    assert "pypi.org" in remembered


def test_already_allowed_is_not_asked(config):
    ctx, m, asked = sandbox_ctx(config, ONCE)
    assert "already allowed" in dispatch(ctx, "request_network", domains=["files.pythonhosted.org"], reason="x").content
    assert asked == []


async def test_this_task_only_ends_with_the_task(config, provider):
    from sinobee.agent import Agent, Runtime
    config.data["permissions"]["approval"] = "sandbox"
    agent = Agent(Runtime(config, provider=provider), "c1")
    m = StubManager(config)
    agent.ctx.state["executor"] = StubBox(config, sandbox_map(config.workspace), "c1", m)
    m.grants["c1"] = {"github.com"}
    m.open_all.add("c1")
    await agent._sandbox_task_end()
    assert m.grants == {} and m.open_all == set() and m.pushed == [("c1", [], False)]


def test_blocked_connections_are_reported_once_without_browser_noise(config):
    ctx, m, _ = sandbox_ctx(config, NO)
    m.connections = [{"seq": 1, "conversation": "c1", "host": "example.com", "allowed": False},
                     {"seq": 2, "conversation": "c1", "host": "example.com", "allowed": False},
                     {"seq": 3, "conversation": "c1", "host": "update.googleapis.com", "allowed": False},
                     {"seq": 4, "conversation": "other", "host": "evil.test", "allowed": False},
                     {"seq": 5, "conversation": "c1", "host": "pypi.org", "allowed": True}]
    out = dispatch(ctx, "shell", command="curl https://example.com").content
    assert "network blocked: example.com (2×)" in out and "googleapis" not in out and "evil" not in out
    assert "network blocked" not in dispatch(ctx, "shell", command="true").content       # reported once


def test_settings_keep_the_network_choices(config):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sinobee.agent import Runtime
    from sinobee.server import settings_api
    rt = Runtime(config)
    app = FastAPI()
    settings_api.register(app, lambda: rt)
    with TestClient(app) as c:
        r = c.put("/api/settings", json={"sandbox": {"network": {"mode": "open",
                                                                  "remembered": ["pypi.org", "https://GitHub.com/a"]}}})
        assert r.status_code == 200, r.text
        net = c.get("/api/settings").json()["sandbox"]["network"]
        assert net == {"mode": "open", "remembered": ["pypi.org", "github.com"]}
        assert c.put("/api/settings", json={"sandbox": {"network": {"mode": "wide"}}}).status_code == 400


# ------------------------------------------------------------------ desktop actions (no Docker)
class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, stdin=None, timeout=30):
        import subprocess
        self.calls.append((list(argv), stdin))
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_hold_and_drag_paths_are_one_xdotool_call():
    from sinobee.tools.desktop import XdotoolBackend
    b = XdotoolBackend()
    b._run = rec = Recorder()
    b.hold("ctrl+space", 3)
    b.drag_path([(10, 10), (50, 20), (90, 40)], 1.0)
    (hold, _), (drag, _) = rec.calls
    assert hold == ["xdotool", "keydown", "ctrl+space", "sleep", "3.00", "keyup", "ctrl+space"]
    assert drag[:6] == ["xdotool", "mousemove", "--sync", "10", "10", "mousedown"] and drag[-1] == "1"
    assert drag.count("mousemove") == 3 and "0.500" in drag


def test_the_desktop_is_there_in_the_sandbox_even_without_one_here(config, monkeypatch):
    from sinobee.tools import desktop
    monkeypatch.setattr(SandboxManager, "unavailable", lambda self, max_age=60: "Docker is not running")
    monkeypatch.setattr(desktop, "_has", lambda m: False)
    monkeypatch.setattr(desktop.platform, "system", lambda: "Darwin")
    names = {t.name for t in REGISTRY.available(config)}
    config.data["permissions"]["toolsets"] = list(set(config.data["permissions"]["toolsets"]) | {"desktop"})
    assert "screenshot" not in {t.name for t in REGISTRY.available(config)}
    monkeypatch.setattr(SandboxManager, "unavailable", lambda self, max_age=60: None)
    config.data["permissions"]["approval"] = "sandbox"
    avail = {t.name for t in REGISTRY.available(config)}
    assert {"screenshot", "mouse", "wait_change", "zoom", "launch", "request_network"} <= avail
    assert "request_network" not in names


# ------------------------------------------------------------------ in Docker
@pytest.fixture
def box(config):
    config.data["permissions"]["approval"] = "sandbox"
    config.data["sandbox"]["memory"] = "1500m"
    config.workspace.mkdir(parents=True, exist_ok=True)
    config.workspace.chmod(0o777)
    conv = "test-" + uuid.uuid4().hex[:8]
    ctx = ToolContext(config=config, session_id=conv)
    yield config, conv, ctx
    from sinobee.tools import browser
    asyncio.run(browser.shutdown())
    asyncio.run(manager_for(config).remove(conv))


@pytest.mark.docker
def test_the_agent_uses_the_sandbox_desktop(box):
    config, conv, ctx = box
    shot = dispatch(ctx, "screenshot").content
    assert "screen 1440x900" in shot[0].text
    assert "started" in dispatch(ctx, "launch", command="xterm -T probe -e sleep 60").content
    out = dispatch(ctx, "wait_change", mode="stable", timeout=8).content
    assert out[0].text in ("the screen is stable", "the screen changed")
    assert "probe" in dispatch(ctx, "window", action="list").content
    dispatch(ctx, "clipboard", action="write", text="剪貼簿 ok")
    assert dispatch(ctx, "clipboard", action="read").content == "剪貼簿 ok"
    assert "held space" in dispatch(ctx, "keyboard", action="hold", keys="space", seconds=0.3).content
    assert "dragged" in dispatch(ctx, "mouse", action="drag_path", path=[[10, 10], [100, 60]], seconds=0.3).content
    z = dispatch(ctx, "zoom", region=[0, 0, 100, 50], scale=3).content
    assert "336x168" in z[0].text          # 100x50 in the 1280-wide screenshot = 112x56 screen pixels, x3


@pytest.mark.docker
def test_taking_over_pauses_the_agent(box):
    config, conv, ctx = box
    dispatch(ctx, "screen_info")
    m = manager_for(config)
    m.set_takeover(conv, True)
    assert "taken over" in dispatch(ctx, "screenshot").content
    assert "taken over" in dispatch(ctx, "browser", action="snapshot").content
    m.set_takeover(conv, False)
    assert not dispatch(ctx, "screen_info").is_error


@pytest.mark.docker
def test_the_sandbox_browser_and_previews(box):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sinobee.agent import Runtime
    from sinobee.server import sandbox_api
    config, conv, ctx = box
    dispatch(ctx, "shell", command="mkdir -p /workspace/site && echo '<h1>preview ok</h1>' > /workspace/site/index.html"
                                   " && (cd /workspace/site && nohup python3 -m http.server 8765 >/tmp/h.log 2>&1 &)"
                                   " && sleep 1")
    out = dispatch(ctx, "browser", action="navigate", url="http://localhost:8765/").content
    assert "preview ok" in out
    out = dispatch(ctx, "browser", action="navigate", url="https://example.com").content
    assert "request_network(domains=['example.com']" in out
    rt = Runtime(config)
    app = FastAPI()
    sandbox_api.register(app, lambda: rt)
    with TestClient(app) as c:
        r = c.get(f"/api/sandbox/{conv}/preview/8765/index.html")
        assert r.status_code == 200 and "preview ok" in r.text
        assert c.get(f"/api/sandbox/{conv}/preview/9999/").status_code == 502
        st = c.get(f"/api/sandbox/{conv}").json()
        assert st["sandbox"] and not st["takeover"]
        with c.websocket_connect(f"/api/sandbox/{conv}/vnc") as ws:
            assert ws.receive_bytes()[:12] == b"RFB 003.008\n"
        assert c.post(f"/api/sandbox/{conv}/takeover", json={"on": True}).json() == {"takeover": True}
        assert manager_for(config).takeover.get(conv)
