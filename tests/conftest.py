import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["SINOBEE_NO_RESTART"] = "1"     # switching accounts restarts SinoBee in place: never the test run
os.environ["SINOBEE_INSECURE_NO_AUTH"] = "1"   # API tests call it directly; tests/test_server_security.py turns it on

from sinobee.config import Config  # noqa: E402
from sinobee.types import ChatResponse, Message, ToolCall, Usage  # noqa: E402


class ScriptedProvider:
    """Fake model: returns queued responses; records requests."""

    label = "test/scripted"
    provider_name = "openai"
    model = "gpt-4.1"

    def __init__(self, script=None):
        self.script = list(script or [])
        self.requests = []
        self.recorder = None

    def push(self, text="", calls=None):
        self.script.append(Message("assistant", text, [ToolCall(f"c{len(self.script)}_{i}", n, a)
                                                        for i, (n, a) in enumerate(calls or [])]))

    async def chat(self, messages, tools=None, **kw):
        self.requests.append((messages, tools))
        if callable(self.script[0] if self.script else None):
            msg = self.script.pop(0)(messages)
        else:
            msg = self.script.pop(0) if self.script else Message("assistant", "{}")
        resp = ChatResponse(msg, Usage(1000, 500, 200))
        if self.recorder:
            self.recorder(self, resp.usage, None)
        return resp

    async def embed(self, texts):
        raise RuntimeError("no embeddings")


@pytest.fixture(autouse=True)
def _fresh_local_windows():
    """local.set_grown is process-wide (one window per model): no test sees another test's window."""
    from sinobee import local
    local.reset_grown()
    yield
    local.reset_grown()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SINOBEE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SINOBEE_WORKSPACE", str(tmp_path / "ws"))
    return tmp_path


@pytest.fixture
def config(home):
    cfg = Config.load()
    cfg.data["permissions"]["approval"] = "auto"
    cfg.data["permissions"]["toolsets"] = ["shell", "files", "web", "memory", "skills"]
    cfg.data["agent"]["verify"] = False      # scripted fake models: the completion check is tested on its own
    cfg.data["agent"]["plan"] = "off"         # the thinking model's plan is tested on its own
    # background memory upkeep / context flush make extra model calls: tested on their own
    cfg.data["memory"].update(auto_maintain=False, flush_on_compact=False, schedule=False)
    return cfg


@pytest.fixture
def provider():
    return ScriptedProvider()


def pytest_collection_modifyitems(config, items):
    """@pytest.mark.docker tests run only where Docker and the sandbox image are there."""
    if not any("docker" in i.keywords for i in items):
        return
    import shutil
    import subprocess
    ok, why = bool(shutil.which("docker")), "docker is not installed"
    if ok:
        from sinobee import __version__
        r = subprocess.run(["docker", "image", "inspect", f"sinobee-sandbox:{__version__}"], capture_output=True)
        ok, why = r.returncode == 0, "Docker or the sinobee-sandbox image is not available"
    if not ok:
        for i in items:
            if "docker" in i.keywords:
                i.add_marker(pytest.mark.skip(reason=why))
