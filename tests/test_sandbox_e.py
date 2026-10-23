"""Sandbox plan E: folders of this computer in the sandbox, review copies, applying changes back.
Tests marked docker need Docker and the sinobee-sandbox image; the rest run anywhere."""
import asyncio
import os
import time
import types
import uuid
from pathlib import Path

import pytest

from sinobee.sandbox import folders as fd
from sinobee.sandbox import sandbox_map, staging
from sinobee.sandbox.manager import SandboxManager, manager_for
from sinobee.tools.registry import REGISTRY, ToolContext
from sinobee.types import ToolCall


def dispatch(ctx, name, **args):
    return asyncio.run(REGISTRY.dispatch(ToolCall("1", name, args), ctx))


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home folder (Trash, ~ paths) with a report folder in Documents."""
    h = tmp_path / "userhome"
    (h / "Documents" / "報告").mkdir(parents=True)
    (h / ".ssh").mkdir()
    (h / "Library" / "Keychains").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    rep = h / "Documents" / "報告"
    (rep / "a.md").write_text("第一版\n內容\n")
    (rep / "b.md").write_text("另一份\n")
    (rep / "c.bin").write_bytes(b"\0\1\2")
    (rep / "sub").mkdir()
    (rep / "sub" / "d.txt").write_text("deep\n")
    return h


@pytest.fixture(autouse=True)
def sandbox_wanted(monkeypatch, request):
    if "docker" not in request.keywords:
        monkeypatch.setattr(SandboxManager, "unavailable", lambda self, max_age=60: None)


# ------------------------------------------------------------------ what may be given
def test_private_and_system_places_are_never_offered(config, home):
    assert fd.refuse(config, home) and fd.refuse(config, "/") and fd.refuse(config, "/etc")
    assert "金鑰" in fd.refuse(config, home / ".ssh") and "金鑰" in fd.refuse(config, home / "Library")
    assert "SinoBee" in fd.refuse(config, config.home)
    assert "找不到" in fd.refuse(config, home / "nope") and "不是資料夾" in fd.refuse(config, home / "Documents/報告/a.md")
    assert fd.refuse(config, "~/Documents/報告") is None and fd.refuse(config, home / "Documents") is None
    assert fd.count_files(home / "Documents" / "報告", 2) == 3 and fd.count_files(home / "Documents" / "報告", 99) == 4


def test_folders_belong_to_one_conversation_and_follow_the_review_switch(config, home):
    config.data["sandbox"]["folders"] = [{"path": str(home / "Documents")}]       # an old global list is not used
    assert fd.folders_for(config, "c1") == []
    assert fd.write_mode(config) == "review" and fd.write_mode(config, "ro") == "ro"
    fd.add_grant(config, "c1", fd.entry(home / "Documents" / "報告", fd.write_mode(config)))
    config.data["sandbox"]["review_writes"] = False
    assert fd.write_mode(config) == "rw"
    fd.add_grant(config, "c1", fd.entry(home / "Documents", fd.write_mode(config)))
    assert {x["name"]: x["mode"] for x in fd.folders_for(config, "c1")} == {"報告": "review", "Documents": "rw"}
    assert fd.folders_for(config, "c2") == []
    f = fd.folders_for(config, "c1")[0]
    copy = staging.ensure_copy(config, "c1", f)
    assert fd.remove_grant(config, "c1", str(home / "Documents" / "報告"))["mode"] == "review"
    assert not copy.exists() and [x["name"] for x in fd.folders_for(config, "c1")] == ["Documents"]


def test_a_review_folder_maps_to_its_copy(config, home):
    stage = config.home / "sandbox" / "c1" / "host" / "報告"
    m = sandbox_map(config.workspace, [{"path": str(home / "Documents" / "報告"), "name": "報告", "mode": "review",
                                        "stage": str(stage)}])
    assert m.to_host("/host/報告/a.md") == stage / "a.md"
    assert m.to_host("~/Documents/報告/sub/d.txt") == stage / "sub" / "d.txt"      # the user's own path: the copy
    assert m.to_exec(home / "Documents" / "報告" / "a.md") == "/host/報告/a.md"
    assert "~/Documents/報告" in m.describe() and "sandbox/c1" not in m.describe()


# ------------------------------------------------------------------ copies, changes, apply, discard, undo
def make_copy(config, home, conv="c1"):
    f = fd.entry(home / "Documents" / "報告", "review")
    copy = staging.ensure_copy(config, conv, f)
    return f, copy


def test_changes_are_listed_against_the_baseline(config, home):
    f, copy = make_copy(config, home)
    assert (copy / "sub" / "d.txt").read_text() == "deep\n" and staging.changes(config, "c1", f) == []
    (copy / "a.md").write_text("第二版\n內容\n")
    (copy / "new.md").write_text("新檔\n")
    (copy / "b.md").unlink()
    (copy / "c.bin").write_bytes(b"\0\9")
    ch = {c["path"]: c for c in staging.changes(config, "c1", f)}
    assert {k: v["kind"] for k, v in ch.items()} == {"a.md": "modified", "new.md": "added", "b.md": "deleted",
                                                     "c.bin": "modified"}
    assert "-第一版" in ch["a.md"]["diff"] and "+第二版" in ch["a.md"]["diff"] and ch["c.bin"]["binary"]
    assert not any(c["conflict"] for c in ch.values())
    # the original is untouched until you apply
    assert (home / "Documents" / "報告" / "a.md").read_text() == "第一版\n內容\n"


def test_apply_writes_back_saves_the_old_and_trashes_deletions(config, home):
    f, copy = make_copy(config, home)
    orig = home / "Documents" / "報告"
    (copy / "a.md").write_text("第二版\n")
    (copy / "new.md").write_text("新檔\n")
    (copy / "b.md").unlink()
    res = staging.apply(config, "c1", f)
    assert sorted(res["applied"]) == ["a.md", "b.md", "new.md"] and not res["conflicts"]
    assert (orig / "a.md").read_text() == "第二版\n" and (orig / "new.md").read_text() == "新檔\n"
    assert not (orig / "b.md").exists() and (home / ".local/share/Trash/files/b.md").read_text() == "另一份\n"
    assert staging.changes(config, "c1", f) == []                 # nothing left to review
    assert staging.undo(config, "c1", res["id"])
    assert (orig / "a.md").read_text() == "第一版\n內容\n" and (orig / "b.md").read_text() == "另一份\n"
    assert not (orig / "new.md").exists()
    assert staging.applied_batches(config, "c1")[0]["undone"]


def test_your_own_edit_meanwhile_is_a_conflict_never_overwritten_by_itself(config, home):
    f, copy = make_copy(config, home)
    orig = home / "Documents" / "報告"
    (copy / "a.md").write_text("沙盒版\n")
    (copy / "b.md").write_text("沙盒版 b\n")
    time.sleep(0.01)
    (orig / "a.md").write_text("你自己改的\n")
    ch = {c["path"]: c for c in staging.changes(config, "c1", f)}
    assert ch["a.md"]["conflict"] and not ch["b.md"]["conflict"]
    res = staging.apply(config, "c1", f)
    assert res["conflicts"] == ["a.md"] and res["applied"] == ["b.md"]
    assert (orig / "a.md").read_text() == "你自己改的\n"
    res = staging.apply(config, "c1", f, ["a.md"], force=["a.md"])
    assert res["applied"] == ["a.md"] and (orig / "a.md").read_text() == "沙盒版\n"
    staging.undo(config, "c1", res["id"])
    assert (orig / "a.md").read_text() == "你自己改的\n"           # your version comes back


def test_discard_puts_the_original_back_into_the_copy(config, home):
    f, copy = make_copy(config, home)
    (copy / "a.md").write_text("不要了\n")
    (copy / "x.md").write_text("暫時\n")
    assert sorted(staging.discard(config, "c1", f)) == ["a.md", "x.md"]
    assert (copy / "a.md").read_text() == "第一版\n內容\n" and not (copy / "x.md").exists()
    assert staging.changes(config, "c1", f) == []


# ------------------------------------------------------------------ asking for a folder
class StubManager:
    def __init__(self, config):
        self.config, self.boxes, self.takeover, self.refreshed = config, {}, {}, []

    async def refresh(self, conv):
        self.refreshed.append(conv)
        return True


def sandbox_ctx(config, answer):
    from sinobee.sandbox import for_config
    from sinobee.sandbox.executor import SandboxExecutor
    config.data["permissions"]["approval"] = "sandbox"
    m = StubManager(config)
    ex = SandboxExecutor(config, for_config(config, "c1"), "c1", m)
    asked = []

    async def ask(question, options, multi, allow_text):
        assert all(isinstance(o, dict) for o in options)
        asked.append(question)
        return None if answer is None else {"choices": [answer]}
    ctx = ToolContext(config=config, session_id="c1", ask=ask)
    ctx.state["executor"] = ex
    return ctx, m, ex, asked


def test_asking_for_a_folder_in_review_mode_makes_the_copy(config, home):
    from sinobee.tools.sandbox_tools import ALLOW
    ctx, m, ex, asked = sandbox_ctx(config, ALLOW)
    out = dispatch(ctx, "request_host_access", path="~/Documents/報告", reason="整理報告").content
    assert "/host/報告" in out and "審核後寫回" in asked[0] and "~/Documents/報告" in asked[0]
    assert (config.home / "sandbox" / "c1" / "host" / "報告" / "a.md").is_file() and not m.refreshed
    assert ex.paths.to_host("/host/報告/a.md").parent.name == "報告" and "sandbox" in str(ex.paths.to_host("/host/報告"))
    assert "already" in dispatch(ctx, "request_host_access", path="~/Documents/報告", reason="x").content
    assert fd.grants(config, "c2") == []                                      # only this conversation


def test_read_only_folders_restart_the_sandbox_and_review_off_writes_directly(config, home):
    from sinobee.tools.sandbox_tools import ALLOW, READ
    ctx, m, ex, asked = sandbox_ctx(config, READ)               # asked for writing, the user allows reading only
    m.boxes["c1"] = object()
    out = dispatch(ctx, "request_host_access", path=str(home / "Documents"), reason="讀資料").content
    assert "read-only" in out and "restarted" in out and m.refreshed == ["c1"]
    assert [f["mode"] for f in fd.folders_for(config, "c1")] == ["ro"] and not config.get("sandbox.folders")
    with pytest.raises(PermissionError):
        ex.check(home / "Documents" / "報告" / "a.md", write=True)
    config.data["sandbox"]["review_writes"] = False
    ctx2, m2, ex2, asked2 = sandbox_ctx(config, ALLOW)
    (home / "proj").mkdir()
    out = dispatch(ctx2, "request_host_access", path=str(home / "proj"), reason="改程式").content
    assert "changes land directly" in out and "直接寫入" in asked2[0] or "直接寫入" in str(asked2)


def test_refused_places_and_refusals(config, home):
    from sinobee.tools.sandbox_tools import NO
    ctx, m, ex, asked = sandbox_ctx(config, NO)
    assert "金鑰" in dispatch(ctx, "request_host_access", path="~/.ssh", reason="x").content and not asked
    assert "家目錄" in dispatch(ctx, "request_host_access", path="~", reason="x").content
    assert "did not allow" in dispatch(ctx, "request_host_access", path="~/Documents/報告", reason="x").content
    config.data["sandbox"]["review_max_files"] = 2
    out = dispatch(ctx, "request_host_access", path="~/Documents/報告", reason="x").content
    assert "太大無法做審核用的副本" in out and "strategy=direct" in out and "strategy=ro" in out


# ------------------------------------------------------------------ settings and the review API
def test_settings_have_only_the_review_switch(config, home):
    from fastapi.testclient import TestClient

    from sinobee.agent import Runtime
    from sinobee.server import create_app
    c = TestClient(create_app(Runtime(config)))
    assert c.get("/api/settings").json()["sandbox"]["review_writes"] is True and "folders" not in c.get("/api/settings").json()["sandbox"]
    assert c.put("/api/settings", json={"sandbox": {"review_writes": False}}).status_code == 200
    assert c.get("/api/settings").json()["sandbox"]["review_writes"] is False
    assert config.get("sandbox.network.mode") == "ask"                       # the rest of sandbox.* is kept


def test_the_conversation_folder_button_gives_and_takes_folders(config, home, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sinobee.server import sandbox_api
    config.data["permissions"]["approval"] = "sandbox"
    events = []
    app = FastAPI()
    sandbox_api.register(app, lambda: types.SimpleNamespace(config=config), lambda k, d: events.append((k, d)))
    with TestClient(app) as c:
        assert c.get("/api/sandbox/s1/folders").json() == {"folders": [], "review_writes": True, "sandbox": True}
        assert c.post("/api/sandbox/s1/folders", json={"path": "~/.ssh"}).status_code == 400
        r = c.post("/api/sandbox/s1/folders", json={"path": "~/Documents/報告"}).json()
        f = r["folders"][0]
        assert f["mode"] == "review" and f["inner"] == "/host/報告" and f["shown"] == "~/Documents/報告" and f["pending"] == 0
        assert events[-1] == ("folders", {"session_id": "s1"})
        (staging.copy_dir(config, "s1", "報告") / "a.md").write_text("改了\n")
        assert c.get("/api/sandbox/s1/folders").json()["folders"][0]["pending"] == 1
        r = c.delete("/api/sandbox/s1/folders", params={"path": "~/Documents/報告"})
        assert r.status_code == 409 and "1 個變更" in r.json()["detail"]
        assert c.delete("/api/sandbox/s1/folders", params={"path": "~/Documents/報告", "force": True}).json()["folders"] == []
        assert (home / "Documents" / "報告" / "a.md").read_text() == "第一版\n內容\n"
        r = c.post("/api/sandbox/s1/folders", json={"path": str(home / "Documents"), "mode": "ro"}).json()
        assert r["folders"][0]["mode"] == "ro"
    config.data["permissions"]["approval"] = "confirm"
    with TestClient(app) as c:
        assert c.post("/api/sandbox/s1/folders", json={"path": "~/Documents/報告"}).status_code == 409


def test_review_api_lists_applies_discards_and_undoes(config, home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sinobee.server import sandbox_api
    fd.add_grant(config, "s1", fd.entry(home / "Documents" / "報告", "review"))
    f = fd.folders_for(config, "s1")[0]
    copy = staging.ensure_copy(config, "s1", f)
    (copy / "a.md").write_text("新版\n")
    (copy / "b.md").write_text("改 b\n")
    events = []
    app = FastAPI()
    sandbox_api.register(app, lambda: types.SimpleNamespace(config=config), lambda k, d: events.append((k, d)))
    with TestClient(app) as c:
        r = c.get("/api/sandbox/s1/changes").json()
        assert r["count"] == 2 and r["folders"][0]["origin"].endswith("報告") and "+新版" in r["folders"][0]["changes"][0]["diff"]
        assert c.get("/api/sandbox-pending").json() == {"s1": 2}
        j = c.post("/api/sandbox/s1/changes/apply", json={"folder": "報告", "paths": ["a.md"]}).json()
        assert j["applied"] == ["a.md"] and j["pending"] == 1 and events[-1] == ("host_changes", {"session_id": "s1", "count": 1, "interrupted": 0})
        assert (home / "Documents" / "報告" / "a.md").read_text() == "新版\n"
        assert c.post("/api/sandbox/s1/changes/discard", json={"folder": "報告"}).json()["pending"] == 0
        assert c.get("/api/sandbox-pending").json() == {}
        assert c.post("/api/sandbox/s1/changes/undo", json={"id": j["id"]}).json()["restored"] == ["a.md"]
        assert (home / "Documents" / "報告" / "a.md").read_text() == "第一版\n內容\n"
        assert c.post("/api/sandbox/s1/changes/apply", json={"folder": "nope"}).status_code == 404


def test_the_gui_can_open_files_in_copies_and_authorised_folders(config, home):
    from sinobee.fileaccess import FileAccessError, resolve_file
    fd.add_grant(config, "c8", fd.entry(home / "Documents", "ro"))
    assert resolve_file(config, str(home / "Documents" / "報告" / "a.md")).name == "a.md"
    f, copy = make_copy(config, home, "c9")
    assert resolve_file(config, str(copy / "a.md")) == (copy / "a.md").resolve()
    with pytest.raises(FileAccessError):
        resolve_file(config, str(config.home / "sandbox" / "c9" / "base" / "報告.json"))


# ------------------------------------------------------------------ in Docker
@pytest.fixture
def box(config, home):
    config.data["permissions"]["approval"] = "sandbox"
    config.data["sandbox"]["memory"] = "1500m"
    config.workspace.mkdir(parents=True, exist_ok=True)
    config.workspace.chmod(0o777)
    for p in [home, *home.rglob("*")]:
        p.chmod(0o777 if p.is_dir() else 0o666)
    conv = "test-" + uuid.uuid4().hex[:8]
    from sinobee.sandbox.workspaces import own_dir
    own_dir(config, conv).chmod(0o777)            # its own /workspace, writable by uid 1000 on Linux
    ctx = ToolContext(config=config, session_id=conv)
    yield config, conv, ctx
    asyncio.run(manager_for(config).remove(conv))


@pytest.mark.docker
def test_the_sandbox_works_on_the_copy_and_sees_read_only_folders(box, home):
    from sinobee.tools.sandbox_tools import ALLOW
    config, conv, ctx = box
    answers = iter([ALLOW, ALLOW])

    async def ask(q, options, multi, allow_text):
        return {"choices": [next(answers)]}
    ctx.ask = ask
    assert "/host/報告" in dispatch(ctx, "request_host_access", path=str(home / "Documents" / "報告"), reason="x").content
    (config.home / "sandbox").chmod(0o777)
    for p in (config.home / "sandbox").rglob("*"):
        p.chmod(0o777 if p.is_dir() else 0o666)
    out = dispatch(ctx, "shell", command="cat /host/報告/a.md && echo 沙盒改的 > /host/報告/a.md && ls /host").content
    assert "第一版" in out
    assert (home / "Documents" / "報告" / "a.md").read_text() == "第一版\n內容\n"     # the original is untouched
    assert [c["path"] for c in staging.pending(config, conv)] == ["a.md"]
    (home / "notes").mkdir()
    (home / "notes" / "n.txt").write_text("筆記\n")
    out = dispatch(ctx, "request_host_access", path=str(home / "notes"), reason="讀", mode="ro").content
    assert "/host/notes" in out
    out = dispatch(ctx, "shell", command="cat /host/notes/n.txt; touch /host/notes/x 2>&1; cat /host/報告/a.md").content
    assert "筆記" in out and "Read-only" in out and "沙盒改的" in out        # the copy survived the restart


def test_folders_can_be_picked_by_browsing_the_home_folder(config, home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sinobee.server import sandbox_api
    app = FastAPI()
    sandbox_api.register(app, lambda: types.SimpleNamespace(config=config))
    with TestClient(app) as c:
        r = c.get("/api/sandbox-browse").json()
        assert r["path"] == str(home.resolve()) and not r["can_pick"] and r["parent"] is None
        names = {d["name"]: d for d in r["dirs"]}
        assert set(names) == {"Documents", "Library"} and names["Documents"]["can_pick"] and not names["Library"]["can_pick"]
        assert ".ssh" in {d["name"] for d in c.get("/api/sandbox-browse", params={"hidden": True}).json()["dirs"]}
        assert [s["label"] for s in r["shortcuts"]] == ["文件", "SinoBee 工作區"]       # other conversations' results
        r = c.get("/api/sandbox-browse", params={"path": "~/Documents/報告"}).json()
        assert r["can_pick"] and r["shown"] == "~/Documents/報告" and [x["label"] for x in r["crumbs"]] == ["~", "Documents", "報告"]
        assert {d["name"] for d in r["dirs"]} == {"sub"}                     # folders only, no files
        assert c.get("/api/sandbox-browse", params={"path": "/etc"}).status_code == 403


def test_the_prompt_says_the_given_folder_is_where_the_work_goes(config, home):
    from sinobee.agent.prompt import environment_block
    config.data["permissions"]["approval"] = "sandbox"
    assert "THE USER GAVE" not in environment_block(config, "c1")
    fd.add_grant(config, "c1", fd.entry(home / "Documents" / "報告", "review"))
    text = environment_block(config, "c1")
    assert "THIS FOLDER: /host/報告 (= " in text and "not in /workspace" in text and "審核變更" in text
    fd.add_grant(config, "c2", fd.entry(home / "Documents" / "報告", "ro"))
    assert "THE USER GAVE" not in environment_block(config, "c2")          # read-only: nothing to deliver there


def test_sub_agents_work_in_their_conversations_sandbox(config, home, monkeypatch):
    from sinobee.agent import Agent, Runtime
    from sinobee.sandbox import executor_of
    from sinobee.work import agents as wa
    config.data["permissions"]["approval"] = "sandbox"
    fd.add_grant(config, "conv1", fd.entry(home / "Documents" / "報告", "review"))
    seen = []

    async def fake_run(self, prompt):
        ex = executor_of(self.ctx)
        seen.append((self.session_id, ex.conversation, "/host/報告" in self.system.text()))
        if len(seen) == 1:                                   # the sub-agent starts one of its own
            await wa.run_subagent(self.rt, role="general", task="inner", board_scope="b", parent_session=self.session_id)
        return "ok"
    monkeypatch.setattr(Agent, "run", fake_run)
    rt = Runtime(config)
    asyncio.run(wa.run_subagent(rt, role="general", task="outer", board_scope="b", parent_session="conv1"))
    assert [s[1] for s in seen] == ["conv1", "conv1"] and all(s[0].startswith("sub-") for s in seen)
    assert all(s[2] for s in seen) and wa.SANDBOX_ROOT == {}
