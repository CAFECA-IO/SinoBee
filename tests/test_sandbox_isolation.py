"""Each conversation's own /workspace (sandbox/workspaces.py), where a turn's files are (artifacts.places) with
在 Finder 中顯示 (/api/files/reveal), and folders shown before a new conversation's first message."""
import asyncio
import json
import subprocess
import types

import pytest

from sinobee.agent import artifacts
from sinobee.sandbox import convconf, workspaces
from sinobee.sandbox import folders as fd
from sinobee.sandbox.manager import SandboxManager


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "userhome"
    (h / "Documents" / "proj").mkdir(parents=True)
    (h / "Documents" / "proj" / "a.md").write_text("x\n")
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture(autouse=True)
def sandboxed(config, monkeypatch):
    monkeypatch.setattr(SandboxManager, "unavailable", lambda self, max_age=60: None)
    config.data["permissions"]["approval"] = "sandbox"


def test_a_new_conversation_has_its_own_workspace_an_older_one_keeps_the_shared(config, home):
    from sinobee.sandbox import for_config
    assert workspaces.settle(config, "new1", legacy=False) == "own"
    assert workspaces.settle(config, "new1", legacy=True) == "own"                    # decided once
    assert workspaces.settle(config, "old1", legacy=True) == "shared"
    own = workspaces.own_dir(config, "new1")
    assert own == config.workspace / "conversations" / "new1"
    assert for_config(config, "new1").to_host("/workspace/site/index.html") == own / "site" / "index.html"
    assert for_config(config, "old1").to_host("/workspace/x") == config.workspace / "x"
    assert workspaces.mode(config, "draft-not-settled") == "own"
    m = SandboxManager(config)
    mounts = {inner: host for host, inner, _m in m.folder_mounts("new1")}
    assert mounts["/workspace"] == own.resolve() and "/workspace" not in {i for _h, i, _m in m.mounts()}
    assert {i: h for h, i, _m in m.folder_mounts("old1")}["/workspace"] == config.workspace.resolve()
    assert m.mount_key("new1") != m.mount_key("new2")
    key = m.mount_key("old1")
    convconf.update(config, "old1", lambda d: d.__setitem__("workspace", "own"))
    assert m.mount_key("old1") != key                                                   # switching remakes it


def test_tools_write_relative_paths_into_the_conversations_workspace(config, home):
    from test_sandbox_e import dispatch, sandbox_ctx
    ctx, m, ex, asked = sandbox_ctx(config, None)
    workspaces.settle(config, "c1", legacy=False)
    out = dispatch(ctx, "write_file", path="notes/hello.md", content="hi\n").content
    assert "ERROR" not in out, out
    assert (workspaces.own_dir(config, "c1") / "notes" / "hello.md").read_text() == "hi\n"
    assert not (config.workspace / "notes").exists()
    (config.workspace / "secret-of-another-task.txt").write_text("另一個任務的秘密\n")
    out = dispatch(ctx, "read_file", path=str(config.workspace / "secret-of-another-task.txt")).content
    assert "另一個任務的秘密" not in out and "not in the sandbox" in out


def test_uploads_and_file_links_follow_the_conversation(config, home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sinobee.fileaccess import resolve_file
    from sinobee.server import files_api
    app = FastAPI()
    files_api.register(app, lambda: types.SimpleNamespace(config=config))
    with TestClient(app) as c:
        r = c.post("/api/upload", params={"sid": "conv01"}, files={"files": ("報告.txt", b"data")}).json()
        assert r[0]["path"].startswith(str(workspaces.own_dir(config, "conv01") / "uploads"))
        r2 = c.post("/api/upload", files={"files": ("a.txt", b"data")}).json()
        assert r2[0]["path"].startswith(str(config.workspace / "uploads"))
        (workspaces.own_dir(config, "conv01") / "page.md").write_text("# 頁\n")
        assert c.get("/api/files", params={"path": "/workspace/page.md", "sid": "conv01"}).text == "# 頁\n"
        assert c.get("/api/files", params={"path": "/workspace/page.md"}).status_code == 404
    assert resolve_file(config, "/workspace/page.md", "conv01").read_text() == "# 頁\n"


def test_other_conversations_results_can_be_given_like_a_folder(config, home):
    other = workspaces.own_dir(config, "older") / "site"
    other.mkdir()
    assert fd.refuse(config, other) is None and fd.refuse(config, config.workspace) is None
    assert fd.refuse(config, config.home) and fd.refuse(config, config.home / "sandbox")


def test_where_the_files_are_and_reveal_in_finder(config, home, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sinobee.sandbox import staging
    from sinobee.server import files_api
    workspaces.settle(config, "p1", legacy=False)
    own = workspaces.own_dir(config, "p1")
    (own / "sewage-plant" / "app").mkdir(parents=True)
    (own / "sewage-plant" / "app" / "page.tsx").write_text("x")
    (own / "sewage-plant" / "README.md").write_text("x")
    (own / "notes.md").write_text("x")
    fd.add_grant(config, "p1", fd.entry(home / "Documents" / "proj", "review", via="copy"))
    copy = staging.ensure_copy(config, "p1", fd.folders_for(config, "p1")[0])
    (copy / "b.md").write_text("new\n")
    items = [{"path": str(p.relative_to(config.workspace))} for p in
             (own / "sewage-plant" / "app" / "page.tsx", own / "sewage-plant" / "README.md", own / "notes.md")]
    items.append({"path": str(copy / "b.md")})
    got = artifacts.places(config, "p1", items)
    assert got[0] == {"path": str(own / "sewage-plant"), "shown": workspaces.shown(own / "sewage-plant"),
                      "kind": "workspace", "files": 2}
    assert {(g["path"], g["kind"]) for g in got[1:]} == {(str(own), "workspace"),
                                                        (str(home / "Documents" / "proj"), "review")}
    assert got[0]["shown"].startswith("~/")
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(files_api, "in_container", lambda: False)
    app = FastAPI()
    files_api.register(app, lambda: types.SimpleNamespace(config=config))
    with TestClient(app) as c:
        r = c.post("/api/files/reveal", json={"path": got[0]["path"]})
        assert r.status_code == 200 and calls and str(own / "sewage-plant") in calls[-1][-1]
        assert c.post("/api/files/reveal", json={"path": "/workspace/notes.md", "sid": "p1"}).status_code == 200
        assert c.post("/api/files/reveal", json={"path": str(home / "Documents")}).status_code == 403
        assert c.post("/api/files/reveal", json={"path": str(own / "nope")}).status_code == 404


def test_settings_switch_the_workspace_and_drafts_are_tidied(config, home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sinobee.server import sandbox_api
    app = FastAPI()
    mem = types.SimpleNamespace(session_exists=lambda sid: sid == "legacy1")
    sandbox_api.register(app, lambda: types.SimpleNamespace(config=config, memory=mem), None)
    with TestClient(app) as c:
        w = c.get("/api/sandbox/fresh01/settings").json()["workspace"]
        assert w["mode"] == "own" and w["path"].endswith("conversations/fresh01")
        assert c.get("/api/sandbox/legacy1/settings").json()["workspace"]["mode"] == "shared"
        w = c.put("/api/sandbox/fresh01/settings", json={"workspace": "shared"}).json()["workspace"]
        assert w["mode"] == "shared" and w["path"] == str(config.workspace)
        assert c.put("/api/sandbox/fresh01/settings", json={"workspace": "all"}).status_code == 400
    # a draft that only got an attachment, a day old, is removed; a real conversation's folder never
    import os
    import time
    d = workspaces.own_dir(config, "draft9") / "uploads"
    d.mkdir(parents=True)
    keep = workspaces.own_dir(config, "real9")
    (keep / "uploads").mkdir()
    old = time.time() - 3 * 86400
    for p in (workspaces.own_dir(config, "draft9"), keep):
        os.utime(p, (old, old))
    out = convconf.sweep_files(config, known=lambda c: c == "real9")
    assert "draft9" in out["drafts"] and not workspaces.own_dir(config, "draft9", make=False).exists()
    assert keep.exists()


def test_the_prompt_names_the_folder_on_the_users_computer(config, home):
    from sinobee.agent.prompt import environment_block
    workspaces.settle(config, "pr1", legacy=False)
    block = environment_block(config, "pr1")
    assert "conversations/pr1" in block and "this conversation's own folder" in block
    assert "give its path on their computer" in block


@pytest.mark.docker
def test_one_conversation_cannot_see_anothers_files(config, home):
    import uuid

    from sinobee.sandbox.manager import manager_for
    from sinobee.tools.registry import ToolContext
    from test_sandbox_e import dispatch
    config.data["sandbox"]["memory"] = "1500m"
    a, b = "test-" + uuid.uuid4().hex[:8], "test-" + uuid.uuid4().hex[:8]
    for conv in (a, b):
        workspaces.settle(config, conv, legacy=False)
        workspaces.own_dir(config, conv).chmod(0o777)
    try:
        ca, cb = ToolContext(config=config, session_id=a), ToolContext(config=config, session_id=b)
        assert "ERROR" not in dispatch(ca, "shell", command="echo 只屬於 A > /workspace/a-secret.txt").content
        out = dispatch(cb, "shell", command="ls -la /workspace; cat /workspace/a-secret.txt 2>&1; "
                                            "ls /workspace/../ 2>&1").content
        assert "只屬於 A" not in out and "No such file" in out
        assert (workspaces.own_dir(config, a) / "a-secret.txt").read_text() == "只屬於 A\n"
    finally:
        for conv in (a, b):
            asyncio.run(manager_for(config).remove(conv))
