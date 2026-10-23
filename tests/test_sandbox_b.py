"""Sandbox plan B + C1: tools in a Docker container per conversation, approval mode "sandbox", the egress proxy.
Tests marked docker need Docker and the sinobee-sandbox image (sinobee sandbox build); the rest run anywhere."""
import asyncio
import json
import time
import urllib.request
import uuid

import pytest

from sinobee.sandbox import egress, executor as exmod, executor_kind, executor_of, sandbox_map
from sinobee.sandbox.executor import Executor, RunResult, SandboxExecutor
from sinobee.sandbox.manager import SandboxManager, manager_for
from sinobee.tools.registry import REGISTRY, ToolContext
from sinobee.types import ToolCall


def dispatch(ctx, name, **args):
    return asyncio.run(REGISTRY.dispatch(ToolCall("1", name, args), ctx))


# ------------------------------------------------------------------ approval mode "sandbox"
class FakeBox(Executor):
    kind = "sandbox"

    def __init__(self, config, paths):
        super().__init__(config, paths)
        self.ran = []

    def check(self, p, write=False):
        if not self.paths.visible(p):
            raise PermissionError("not in the sandbox")

    async def run(self, command, cwd, timeout, env=None):
        self.ran.append(command)
        return RunResult(0, "ok")


def ctx_with(config, executor, asked):
    async def approve(tool, args, reason):
        asked.append((tool, reason))
        return False
    ctx = ToolContext(config=config, approve=approve)
    ctx.state["executor"] = executor
    return ctx


def test_inside_the_sandbox_nothing_is_asked(config, monkeypatch):
    config.data["permissions"]["approval"] = "sandbox"
    monkeypatch.setattr(exmod, "executor_kind", lambda c: "sandbox")
    monkeypatch.setattr("sinobee.sandbox.executor_kind", lambda c: "sandbox")
    asked = []
    box = FakeBox(config, sandbox_map(config.workspace))
    ctx = ctx_with(config, box, asked)
    out = dispatch(ctx, "shell", command="rm -rf build && sudo apt-get install -y ffmpeg && pip install numpy")
    assert not out.is_error and box.ran and asked == []


def test_outside_the_sandbox_it_asks_as_before(config, monkeypatch):
    config.data["permissions"]["approval"] = "sandbox"          # wanted, but Docker is not there
    monkeypatch.setattr(SandboxManager, "unavailable", lambda self, max_age=60: "Docker is not running")
    assert executor_kind(config) == "host"
    asked = []
    ctx = ToolContext(config=config)
    async def approve(tool, args, reason):
        asked.append(tool)
        return False
    ctx.approve = approve
    out = dispatch(ctx, "shell", command="rm -rf build")
    assert out.is_error and asked == ["shell"]


def test_only_the_wanted_kind_is_checked(config, monkeypatch):
    called = []
    monkeypatch.setattr(SandboxManager, "unavailable", lambda self, max_age=60: called.append(1) or None)
    assert executor_kind(config) == "host" and called == []      # approval "confirm": Docker is not even asked
    config.data["permissions"]["approval"] = "sandbox"
    assert executor_kind(config) == "sandbox" and called == [1]
    config.data["sandbox"]["executor"] = "host"                    # an explicit choice wins
    assert executor_kind(config) == "host"


def test_settings_accept_the_sandbox_mode(config):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sinobee.agent import Runtime
    from sinobee.server import settings_api
    rt = Runtime(config)
    app = FastAPI()
    settings_api.register(app, lambda: rt) if hasattr(settings_api, "register") else pytest.skip("no register()")
    with TestClient(app) as c:
        r = c.put("/api/settings", json={"permissions": {"approval": "sandbox"}})
        assert r.status_code == 200, r.text
        s = c.get("/api/settings").json()
        assert s["permissions"]["approval"] == "sandbox" and "image" in s["sandbox"]


def test_limits_follow_dockers_memory(config, monkeypatch):
    from sinobee.sandbox import manager as mgr
    monkeypatch.setattr(mgr, "docker_sync", lambda *a, **k: (0, f"10 {int(7.7 * (1 << 30))}\n", ""))
    # 7.7 GB: three at once, each with its share of 80% (stage 1: sandbox.max_running)
    assert SandboxManager(config).limits() == ("4", f"{int(7.7 * (1 << 30) * 0.8 / 3) // (1 << 20)}m")
    monkeypatch.setattr(mgr, "docker_sync", lambda *a, **k: (0, f"16 {64 << 30}\n", ""))
    assert SandboxManager(config).limits() == ("4", "8192m")                  # at most 8 GB
    config.data["sandbox"].update(cpus="2", memory="6g")
    assert SandboxManager(config).limits() == ("2", "6g")


def test_the_prompt_tells_the_model_about_the_sandbox(config, monkeypatch):
    from sinobee.agent.prompt import environment_block
    monkeypatch.setattr(SandboxManager, "unavailable", lambda self, max_age=60: None)
    config.data["permissions"]["approval"] = "sandbox"
    block = environment_block(config)
    assert "/workspace = " in block and "/sinobee/skills" in block and "pypi.org" in block and "403" in block


# ------------------------------------------------------------------ the egress proxy (no Docker needed)
def test_domain_rules():
    egress.policies.clear()
    egress.policies["10.0.0.2"] = {"conversation": "c", "domains": ["pypi.org", "github.com", "*.npmjs.org", "8.8.8.8"]}
    ok = lambda h: egress.allowed("10.0.0.2", h)[0]
    assert ok("pypi.org") and ok("PyPI.org.") and ok("api.github.com") and ok("registry.npmjs.org")
    assert not ok("evilpypi.org") and not ok("github.com.evil.net") and not ok("example.com")
    assert ok("8.8.8.8") and not ok("1.1.1.1")
    assert not egress.allowed("10.0.0.9", "pypi.org")[0]                     # not a known sandbox
    egress.policies["10.0.0.3"] = {"all": True}
    assert egress.allowed("10.0.0.3", "anything.example")[0]


async def test_the_proxy_passes_allowed_and_refuses_the_rest(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"hello from upstream")

        def log_message(self, *a):
            pass
    up = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    monkeypatch.setattr(egress, "TOKEN", "t0k")
    egress.policies.clear()
    egress.log.clear()
    proxy, control = await egress.start("127.0.0.1", 0, 0)
    pport, cport = proxy.sockets[0].getsockname()[1], control.sockets[0].getsockname()[1]

    def ctl(method, path, body=None, token="t0k"):
        req = urllib.request.Request(f"http://127.0.0.1:{cport}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-SinoBee-Token": token})
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with op.open(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    def via_proxy(url):          # by hand: urllib skips proxies for localhost
        import socket
        with socket.create_connection(("127.0.0.1", pport), timeout=5) as s:
            s.sendall(f"GET {url} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
            data = b""
            while chunk := s.recv(65536):
                data += chunk
        head, _, body = data.partition(b"\r\n\r\n")
        return int(head.split()[1]), body.decode()

    try:
        assert (await asyncio.to_thread(ctl, "GET", "/health", None, "wrong"))[0] == 403
        url = f"http://localhost:{up.server_port}/x"
        code, body = await asyncio.to_thread(via_proxy, url)
        assert code == 403 and "localhost" in body and "request_network" in body       # no policy yet
        code, _ = await asyncio.to_thread(ctl, "PUT", "/policy/127.0.0.1", {"conversation": "c1", "domains": ["localhost"]})
        assert code == 200
        assert await asyncio.to_thread(via_proxy, url) == (200, "hello from upstream")
        code, entries = await asyncio.to_thread(ctl, "GET", "/log?since=1")
        assert [e["allowed"] for e in entries] == [True] and entries[0]["conversation"] == "c1"
    finally:
        proxy.close()
        control.close()
        up.shutdown()


# ------------------------------------------------------------------ in Docker
@pytest.fixture
def box_config(config):
    config.data["permissions"]["approval"] = "sandbox"
    config.data["sandbox"]["memory"] = "1g"
    config.workspace.mkdir(parents=True, exist_ok=True)
    config.workspace.chmod(0o777)                 # Linux: let uid 1000 write (Docker Desktop maps owners itself)
    conv = "test-" + uuid.uuid4().hex[:8]
    from sinobee.sandbox.workspaces import own_dir
    own_dir(config, conv).chmod(0o777)            # its own /workspace, writable by uid 1000 on Linux
    yield config, conv
    asyncio.run(manager_for(config).remove(conv))


def sandbox_ctx(config, conv, asked):
    async def approve(tool, args, reason):
        asked.append(tool)
        return False
    return ToolContext(config=config, session_id=conv, approve=approve)


@pytest.mark.docker
def test_a_task_runs_in_its_sandbox_without_asking(box_config):
    config, conv = box_config
    asked = []
    ctx = sandbox_ctx(config, conv, asked)
    assert isinstance(executor_of(ctx), SandboxExecutor)
    out = dispatch(ctx, "shell", command="id -u; pwd; echo hi > /workspace/hello.txt; rm -rf /tmp/x; "
                                         "touch /etc/nope 2>&1; python3 -c 'print(6*7)'").content
    assert out.startswith("[exit 0] cwd=/workspace") and "\n1000\n/workspace\n" in out and "42" in out
    assert "Read-only file system" in out
    from sinobee.sandbox.workspaces import dir_of
    assert (dir_of(config, conv) / "hello.txt").read_text() == "hi\n"      # its own /workspace
    assert asked == []
    assert "not in the sandbox" in dispatch(ctx, "read_file", path="/etc/hostname").content
    assert "read-only" in dispatch(ctx, "write_file", path="/sinobee/skills/x.md", content="x").content
    out = dispatch(ctx, "shell", command="ls /sinobee/bundled_skills | head -2").content
    assert "ai-video-production" in out


@pytest.mark.docker
def test_network_only_through_the_proxy(box_config):
    config, conv = box_config
    ctx = sandbox_ctx(config, conv, [])
    out = dispatch(ctx, "shell", command="curl -s -m 8 -o /dev/null -w 'blocked=%{http_code}' https://example.com; "
                                         "curl -s -m 5 --noproxy '*' https://example.com || echo ' direct=refused'").content
    assert "direct=refused" in out and "network blocked: example.com" in out
    log = manager_for(config).connections
    assert any(e["host"] == "example.com" and not e["allowed"] and e["conversation"] == conv for e in log)


@pytest.mark.docker
def test_a_timeout_ends_the_command_inside_the_sandbox(box_config):
    config, conv = box_config
    ctx = sandbox_ctx(config, conv, [])
    out = dispatch(ctx, "shell", command="sleep 40 & sleep 41", timeout=2).content
    assert "timed out after 2s" in out
    time.sleep(0.5)
    left = dispatch(ctx, "shell", command="pgrep -f 'sleep 4[01]' | wc -l").content
    assert left.strip().endswith("0")


@pytest.mark.docker
def test_idle_sandboxes_stop_and_old_ones_go(box_config):
    config, conv = box_config
    ctx = sandbox_ctx(config, conv, [])
    dispatch(ctx, "shell", command="true")
    m = manager_for(config)
    name = m.name_of(conv)
    m.last_used[name] = time.time() - 3600
    res = asyncio.run(m.sweep())
    assert name in res["stopped"]
    out = dispatch(ctx, "shell", command="echo back").content            # the next tool call starts it again
    assert out.endswith("back")


@pytest.mark.docker
async def test_g2_an_offline_task_needs_no_approval(box_config, provider):
    """Gate G2: write a Python script in the workspace, install packages from pypi and npm (allowed), delete scratch
    files - no approval at all, and nothing new in the home folder of this computer."""
    from pathlib import Path

    from sinobee.agent import Agent, Runtime
    config, conv = box_config
    home_before = {p.name for p in Path.home().iterdir()}
    asked = []
    rt = Runtime(config, provider=provider)
    async def approve(tool, args, reason):
        asked.append(tool)
        return False
    agent = Agent(rt, conv, approve=approve)
    provider.push("", [("write_file", {"path": "g2/fib.py", "content": "import six\nprint(six.__name__, sum(range(10)))\n"})])
    provider.push("", [("shell", {"command": "pip install -q six && python3 g2/fib.py && mkdir -p ~/projects/p && "
                                             "cd ~/projects/p && npm init -y >/dev/null && npm install --silent left-pad "
                                             "&& node -e \"console.log(require('left-pad')('7', 3, '0'))\" && "
                                             "rm -rf /workspace/g2/tmp ~/projects/p/node_modules/.cache && sudo true 2>/dev/null; "
                                             "echo done", "timeout": 300})])
    provider.push("完成")
    out = await agent.run("在沙盒裡跑一個小腳本")
    assert out == "完成" and asked == []
    shell_out = [m.text() for m in agent.history if m.role == "tool" and "done" in m.text()]
    assert shell_out and "six 45" in shell_out[0] and "007" in shell_out[0], shell_out
    assert {p.name for p in Path.home().iterdir()} - home_before <= {".sinobee"}


@pytest.mark.docker
async def test_stop_ends_the_command_inside_the_sandbox(box_config):
    config, conv = box_config
    ctx = sandbox_ctx(config, conv, [])
    task = asyncio.create_task(REGISTRY.dispatch(ToolCall("1", "shell", {"command": "sleep 47; echo never"}), ctx))
    await asyncio.sleep(4)                       # the first call also starts the container
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.5)
    left = await REGISTRY.dispatch(ToolCall("2", "shell", {"command": "pgrep -f 'sleep 4[7]' | wc -l"}), ctx)
    assert left.content.strip().endswith("0")
