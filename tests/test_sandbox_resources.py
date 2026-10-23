"""Sandbox plan stage 1: how many sandboxes run at once (and what each gets), waiting in line, health with what to
do about it, building images from the GUI, and each conversation's sandbox in Settings."""
import asyncio
import json
import types
import uuid

import pytest

from sinobee.sandbox import admin
from sinobee.sandbox import manager as mgr
from sinobee.sandbox.manager import DockerError, SandboxManager


def fake_info(monkeypatch, ncpu=10, mem=8_270_000_000):
    def ds(*args, timeout=20):
        if args[:1] == ("info",):
            return 0, f"{ncpu} {mem}", ""
        return 0, "", ""
    monkeypatch.setattr(mgr, "docker_sync", ds)


def test_how_many_fit_and_what_each_gets(config, monkeypatch):
    fake_info(monkeypatch)
    m = SandboxManager(config)
    cap = m.capacity()
    assert cap["max_running"] == 3 and cap["cpus"] == "4"
    assert 2000 <= int(cap["memory"][:-1]) <= 2300                     # 80% of 7.7 GB shared by three
    config.data["sandbox"]["max_running"] = 1
    m._cap = None
    assert m.capacity()["max_running"] == 1 and int(m.capacity()["memory"][:-1]) > 6000
    config.data["sandbox"]["max_running"] = "auto"
    fake_info(monkeypatch, mem=64 << 30)
    m._cap = None
    assert m.capacity()["max_running"] == 6 and m.capacity()["memory"] == f"{8 << 10}m"   # at most 8 GB each
    fake_info(monkeypatch, mem=2 << 30)
    m._cap = None
    assert m.capacity()["max_running"] == 1 and m.capacity()["memory"] == "1638m"


class Box(SandboxManager):
    """The scheduler alone: which sandboxes run is a set, stopping one takes it out."""
    def __init__(self, config, running, cap=2):
        super().__init__(config)
        self.now, self.stopped, self.cap = set(running), [], cap

    def capacity(self, max_age=300):
        return {"max_running": self.cap, "cpus": "1", "memory": "1g", "docker_memory": 1, "docker_cpus": 1}

    async def running(self, max_age=2.0):
        return set(self.now)

    async def stop(self, conversation):
        self.stopped.append(conversation)
        self.now.discard(conversation)


@pytest.fixture
def events(monkeypatch):
    seen = []
    monkeypatch.setattr(mgr, "EVENT_HOOKS", [lambda k, d: seen.append((k, d))])
    real = asyncio.sleep
    monkeypatch.setattr(mgr.asyncio, "sleep", lambda s: real(0.01))
    return seen


def test_under_the_limit_a_sandbox_starts_and_an_idle_one_makes_room(config, events):
    m = Box(config, {"a"}, cap=2)
    asyncio.run(m._slot("b"))
    assert m.stopped == [] and "b" in m.starting
    m = Box(config, {"a", "b"}, cap=2)
    m.last_used = {m.name_of("a"): 100, m.name_of("b"): 50}
    asyncio.run(m._slot("c"))
    assert m.stopped == ["b"]                                     # unused longest
    m = Box(config, {"a", "b"}, cap=2)
    m.begin("b")
    m.last_used = {m.name_of("a"): 100, m.name_of("b"): 50}
    asyncio.run(m._slot("c"))
    assert m.stopped == ["a"]                                     # b has a task running: never stopped for room


def test_when_all_are_busy_a_task_waits_its_turn(config, events):
    m = Box(config, {"a", "b"}, cap=2)
    m.begin("a")
    m.begin("b")

    async def scenario():
        c = asyncio.create_task(m._slot("c"))
        await asyncio.sleep(0)
        for _ in range(20):
            await asyncio.sleep(0)
        d = asyncio.create_task(m._slot("d"))
        for _ in range(20):
            await asyncio.sleep(0)
        assert m.waiting == ["c", "d"] and not c.done() and not d.done()
        m.end("a")                                                # a's task ended: idle, stopped for c (first in line)
        await asyncio.wait_for(c, 2)
        assert m.stopped == ["a"] and not d.done()
        m.now.add("c")
        m.starting.discard("c")
        m.begin("c")                                              # c's task runs now
        m.end("b")
        await asyncio.wait_for(d, 2)
        assert m.stopped == ["a", "b"] and m.waiting == []
    asyncio.run(scenario())
    queue = [d for k, d in events if k == "sandbox_queue"]
    assert {"session_id": "c", "position": 1, "running": 2, "max": 2} in queue
    assert {"session_id": "d", "position": 2, "running": 2, "max": 2} in queue
    assert {"session_id": "c", "position": 0} in queue and {"session_id": "d", "position": 0} in queue


def test_waiting_too_long_gives_up_with_a_reason(config, events):
    config.data["sandbox"]["queue_minutes"] = 0.0001
    m = Box(config, {"a"}, cap=1)
    m.begin("a")
    with pytest.raises(DockerError, match="沒有空出的沙盒"):
        asyncio.run(m._slot("b"))
    assert m.waiting == []


# ------------------------------------------------------------------ health and what to do
def test_health_says_what_is_wrong_and_what_to_do(config, monkeypatch):
    config.data["permissions"]["approval"] = "sandbox"
    monkeypatch.setattr(admin.shutil, "which", lambda x: None)
    h = admin.health(config)
    assert h["problem"] == "docker_missing" and h["actions"][0]["url"].startswith("https://")
    monkeypatch.setattr(admin.shutil, "which", lambda x: "/usr/bin/docker")
    monkeypatch.setattr(admin, "docker_sync", lambda *a, timeout=8: (1, "", "Cannot connect to the Docker daemon"))
    monkeypatch.setattr(admin.platform, "system", lambda: "Darwin")
    h = admin.health(config)
    assert h["problem"] == "docker_down" and h["actions"][0]["id"] == "start_docker" and h["wanted"]

    def ds(*a, timeout=8):
        if a[0] == "info":
            return 0, "29.8.2", ""
        if a[:2] == ("image", "inspect"):
            return 1, "", "no such image"
        if a[0] == "images":
            return 0, "sinobee-sandbox:0.1.9\n", ""
        return 0, "", ""
    monkeypatch.setattr(admin, "docker_sync", ds)
    h = admin.health(config)
    assert h["problem"] == "image_outdated" and h["actions"] == [{"id": "build", "label": "重建映像", "kind": "standard"}]


def test_images_build_in_the_background(config, monkeypatch):
    calls = []

    class P:
        def __init__(self, cmd, **kw):
            calls.append(cmd)
            self.stdout = iter(["#1 [internal] load build definition\n", "#9 DONE 12.3s\n"])

        def wait(self):
            return 0
    monkeypatch.setattr(admin.subprocess, "Popen", P)
    admin.BUILDS.clear()
    job = admin.build(config, "office")
    for _ in range(100):
        if job["state"] != "running":
            break
        import time
        time.sleep(0.01)
    assert job["state"] == "done" and "OFFICE=1" in calls[0] and calls[0][calls[0].index("-t") + 1].startswith("sinobee-sandbox-office:")
    assert job["lines"][-1] == "#9 DONE 12.3s"


def test_usage_per_conversation(config, monkeypatch):
    from sinobee.sandbox.workspaces import own_dir
    df = {"Containers": [{"Labels": "org.sinobee.conversation=c1,org.sinobee.role=sandbox", "State": "running",
                          "Size": "20.5kB (virtual 3.5GB)"},
                         {"Labels": "org.sinobee.role=egress", "State": "running", "Size": "4kB"}],
          "Volumes": [{"Name": "sinobee-home-c1", "Size": "312.4MB"}, {"Name": "other", "Size": "1GB"}],
          "Images": [{"Repository": "sinobee-sandbox", "Size": "3.56GB"}, {"Repository": "python", "Size": "74MB"}]}

    async def fake_docker(*a, timeout=120, stdin=None):
        return 0, json.dumps(df), ""
    monkeypatch.setattr(admin, "docker", fake_docker)
    monkeypatch.setattr(SandboxManager, "capacity", lambda self, max_age=300: {"max_running": 3})
    (own_dir(config, "c2") / "big.bin").write_bytes(b"x" * 100_000)
    u = asyncio.run(admin.usage(config, {"c1": "污水處理廠"}))
    rows = {r["conversation"]: r for r in u["items"]}
    assert rows["c1"]["state"] == "running" and rows["c1"]["title"] == "污水處理廠"
    assert rows["c1"]["volume"] == 312_400_000 and rows["c1"]["container"] == 20_500
    assert rows["c2"]["workspace"] >= 100_000 and rows["c2"]["state"] == "none"
    assert u["images"] == 3_560_000_000 and u["running"] == 1 and u["total"] > u["images"]


def test_settings_api_for_resources_and_each_sandbox(config, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sinobee.server import sandbox_api
    fake_info(monkeypatch)
    calls = []

    async def rec(self, conv):
        calls.append(("recreate", conv))

    async def rm(self, conv):
        calls.append(("remove", conv))
    monkeypatch.setattr(SandboxManager, "recreate", rec)
    monkeypatch.setattr(SandboxManager, "remove", rm)
    app = FastAPI()
    sandbox_api.register(app, lambda: types.SimpleNamespace(config=config), None)
    with TestClient(app) as c:
        assert c.put("/api/sandbox/resources", json={"max_running": 9}).status_code == 200
        assert config.get("sandbox.max_running") == 9
        r = c.put("/api/sandbox/resources", json={"max_running": "auto"}).json()
        assert r["capacity"]["max_running"] == 3
        assert c.put("/api/sandbox/resources", json={"max_running": 0}).status_code == 400
        assert c.post("/api/sandbox/c1/admin/recreate").status_code == 200
        mgr.manager_for(config).begin("c1")
        assert c.post("/api/sandbox/c1/admin/clear").status_code == 409            # a task is running there
        mgr.manager_for(config).end("c1")
        assert c.post("/api/sandbox/c1/admin/clear").status_code == 200
        assert c.post("/api/sandbox/c1/admin/explode").status_code == 400
    assert calls == [("recreate", "c1"), ("remove", "c1")]


@pytest.mark.docker
def test_with_one_allowed_the_idle_sandbox_makes_room(config, tmp_path):
    from sinobee.sandbox.manager import manager_for
    from sinobee.sandbox.workspaces import own_dir
    config.data["permissions"]["approval"] = "sandbox"
    config.data["sandbox"].update(memory="800m", max_running=1)
    a, b = "test-" + uuid.uuid4().hex[:8], "test-" + uuid.uuid4().hex[:8]
    for conv in (a, b):
        own_dir(config, conv).chmod(0o777)
    m = manager_for(config)
    m._cap = None
    try:
        asyncio.run(m.ensure(a))
        assert a in asyncio.run(m.running(max_age=0))
        asyncio.run(m.ensure(b))
        now = asyncio.run(m.running(max_age=0))
        assert b in now and a not in now                       # a was idle: stopped (not removed) for b
        rc, out, _ = mgr.docker_sync("inspect", "-f", "{{.State.Status}}", m.name_of(a))
        assert rc == 0 and out.strip() == "exited"
    finally:
        for conv in (a, b):
            asyncio.run(m.remove(conv))
