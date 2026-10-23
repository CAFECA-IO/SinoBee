"""Big projects and real environments (sandbox solutions A, B, D): a light copy that keeps up with your edits, git
repositories reviewed by git, and checkpoints for folders written directly."""
import json
import os
import subprocess
import time

import pytest

from sinobee.sandbox import checkpoints, clone, gitreview, staging
from sinobee.sandbox import folders as fd
from sinobee.sandbox.manager import SandboxManager


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "userhome"
    (h / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture(autouse=True)
def no_docker_check(monkeypatch):
    monkeypatch.setattr(SandboxManager, "unavailable", lambda self, max_age=60: None)


def git(repo, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "u", "GIT_AUTHOR_EMAIL": "u@x", "GIT_COMMITTER_NAME": "u",
           "GIT_COMMITTER_EMAIL": "u@x"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout


def project(root):
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.ts").write_text("export const a = 1;\n")
    (root / "README.md").write_text("# 專案\n")
    (root / "node_modules" / "react").mkdir(parents=True)
    (root / "node_modules" / "react" / "index.js").write_text("big\n")
    (root / ".next").mkdir()
    (root / ".next" / "cache.bin").write_bytes(b"\0" * 10)
    return root


def give(config, conv, path, mode="review"):
    via = fd.review_via(path) if mode == "review" else None
    fd.add_grant(config, conv, fd.entry(path, mode, via=via))
    return next(f for f in fd.folders_for(config, conv) if f["path"] == str(path.resolve()))


# ------------------------------------------------------------------ A: a light copy
def test_the_copy_leaves_out_dependencies_and_hashes_nothing(config, home):
    src = project(home / "Documents" / "app")
    f = give(config, "c1", src)
    assert f["via"] == "copy"
    copy = staging.ensure_copy(config, "c1", f)
    assert (copy / "src" / "app.ts").read_text() == "export const a = 1;\n"
    assert not (copy / "node_modules").exists() and not (copy / ".next").exists()
    base = json.loads((config.home / "sandbox" / "c1" / "base" / "app.json").read_text())
    assert set(base["files"]) == {"src/app.ts", "README.md"} and all(v[2] is None for v in base["files"].values())
    assert os.stat(copy / "README.md").st_mtime_ns == os.stat(src / "README.md").st_mtime_ns
    assert staging.changes(config, "c1", f) == []
    # touched but the same: not a change (and not compared again)
    os.utime(copy / "README.md", ns=(time.time_ns(), time.time_ns()))
    assert staging.changes(config, "c1", f) == []
    (copy / "src" / "app.ts").write_text("export const a = 2;\n")
    (copy / "node_modules").mkdir()
    (copy / "node_modules" / "x.js").write_text("installed in the sandbox\n")
    [c] = staging.changes(config, "c1", f)
    assert c["path"] == "src/app.ts" and c["kind"] == "modified" and not c["conflict"] and "+export const a = 2;" in c["diff"]
    res = staging.apply(config, "c1", f)
    assert res["applied"] == ["src/app.ts"] and (src / "src" / "app.ts").read_text() == "export const a = 2;\n"
    assert staging.changes(config, "c1", f) == []
    assert (src / "node_modules" / "react" / "index.js").read_text() == "big\n"
    staging.undo(config, "c1", res["id"])
    assert (src / "src" / "app.ts").read_text() == "export const a = 1;\n"
    assert [c["path"] for c in staging.changes(config, "c1", f)] == ["src/app.ts"]


def test_the_copy_takes_your_edits_made_meanwhile(config, home):
    src = project(home / "Documents" / "app")
    f = give(config, "c1", src)
    copy = staging.ensure_copy(config, "c1", f)
    (copy / "src" / "app.ts").write_text("sandbox\n")                  # the sandbox's change stays
    time.sleep(0.01)
    (src / "README.md").write_text("# 你改過\n")                         # yours comes in
    (src / "NEW.md").write_text("新檔\n")
    (src / "src" / "app.ts").write_text("you too\n")                    # both: a conflict, not overwritten
    assert staging.sync(config, "c1", f) == 2
    assert (copy / "README.md").read_text() == "# 你改過\n" and (copy / "NEW.md").read_text() == "新檔\n"
    assert (copy / "src" / "app.ts").read_text() == "sandbox\n"
    [c] = staging.changes(config, "c1", f)
    assert c["path"] == "src/app.ts" and c["conflict"]
    (src / "NEW.md").unlink()
    assert staging.sync(config, "c1", f) == 1 and not (copy / "NEW.md").exists()
    assert staging.sync(config, "c1", f) == 0


def test_only_reviewed_files_count_towards_the_limit(config, home):
    src = project(home / "Documents" / "app")
    for i in range(50):
        (src / "node_modules" / f"m{i}.js").write_text("x")
    assert fd.count_files(src, 1000) == 2


# ------------------------------------------------------------------ B: a git repository reviewed by git
@pytest.fixture
def repo(home):
    r = project(home / "Documents" / "repo")
    (r / ".gitignore").write_text("node_modules/\n.next/\n.env\ndist/\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "first")
    (r / "README.md").write_text("# 專案\n未提交的修改\n")                # uncommitted and untracked work is kept
    (r / "notes.txt").write_text("untracked\n")
    (r / ".env").write_text("SECRET=1\n")
    return r


def test_a_repository_is_reviewed_by_git(config, home, repo):
    f = give(config, "g1", repo)
    assert f["via"] == "git"
    status_before = git(repo, "status", "--porcelain")
    copy = staging.ensure_copy(config, "g1", f)
    assert (copy / ".git").is_dir() and (copy / "notes.txt").read_text() == "untracked\n"
    assert "未提交" in (copy / "README.md").read_text() and not (copy / "node_modules").exists()
    assert git(copy, "log", "--format=%s", "-1") == "first\n"               # the sandbox sees your history
    assert staging.changes(config, "g1", f) == []
    (copy / "src" / "app.ts").write_text("export const a = 2;\n")
    (copy / "src" / "new.ts").write_text("new\n")
    (copy / "notes.txt").unlink()
    (copy / "dist").mkdir()
    (copy / "dist" / "bundle.js").write_text("built\n")                    # ignored: not reviewed
    (copy / ".env").write_text("SECRET=sandbox\n")                         # ignored: never written back
    git(copy, "add", "-A")
    git(copy, "commit", "-qm", "the sandbox may commit")                   # its commits stay in the copy
    got = {c["path"]: c for c in staging.changes(config, "g1", f)}
    assert {p: c["kind"] for p, c in got.items()} == {"src/app.ts": "modified", "src/new.ts": "added",
                                                     "notes.txt": "deleted"}
    assert not any(c["conflict"] for c in got.values()) and "+export const a = 2;" in got["src/app.ts"]["diff"]
    assert git(repo, "status", "--porcelain") == status_before              # your repository untouched so far
    (repo / "src" / "new.ts").write_text("yours\n")                          # you made the same file meanwhile
    got = {c["path"]: c for c in staging.changes(config, "g1", f)}
    assert got["src/new.ts"]["conflict"] and not got["src/app.ts"]["conflict"]
    res = staging.apply(config, "g1", f)
    assert sorted(res["applied"]) == ["notes.txt", "src/app.ts"] and res["conflicts"] == ["src/new.ts"]
    assert (repo / "src" / "app.ts").read_text() == "export const a = 2;\n" and not (repo / "notes.txt").exists()
    assert (repo / ".env").read_text() == "SECRET=1\n" and not (repo / "dist").exists()
    assert git(repo, "log", "--format=%s", "-1") == "first\n"               # your branch: nothing committed
    assert [c["path"] for c in staging.changes(config, "g1", f)] == ["src/new.ts"]
    assert staging.discard(config, "g1", f, ["src/new.ts"]) == ["src/new.ts"]
    assert (copy / "src" / "new.ts").read_text() == "yours\n" and staging.changes(config, "g1", f) == []
    staging.undo(config, "g1", res["id"])
    assert (repo / "src" / "app.ts").read_text() == "export const a = 1;\n" and (repo / "notes.txt").exists()
    assert {c["path"] for c in staging.changes(config, "g1", f)} == {"src/app.ts", "notes.txt"}


def test_a_folder_inside_a_repository_or_without_git_is_copied(config, home, repo):
    assert fd.review_via(repo / "src") == "copy" and fd.review_via(repo) == "git"


# ------------------------------------------------------------------ D: checkpoints of folders written directly
def test_git_checkpoints_restore_without_touching_your_branch(config, home, repo):
    f = give(config, "d1", repo, "rw")
    assert "via" not in f
    index_before = (repo / ".git" / "index").read_bytes()
    c1 = checkpoints.make(config, "d1", f, "修好登入")
    assert c1["kind"] == "git" and checkpoints.make(config, "d1", f) == c1        # nothing changed: same one
    assert (repo / ".git" / "index").read_bytes() == index_before
    assert git(repo, "log", "--format=%s", "-1") == "first\n"
    (repo / "src" / "app.ts").write_text("broken\n")
    (repo / "added.txt").write_text("new\n")
    (repo / "notes.txt").unlink()
    assert {(c["path"], c["kind"]) for c in checkpoints.diff(config, "d1", f, c1["id"])} == {
        ("src/app.ts", "modified"), ("added.txt", "added"), ("notes.txt", "deleted")}
    out = checkpoints.restore(config, "d1", f, c1["id"])
    assert sorted(out["restored"]) == ["notes.txt", "src/app.ts"] and len(out["trashed"]) == 1
    assert (repo / "src" / "app.ts").read_text() == "export const a = 1;\n" and not (repo / "added.txt").exists()
    assert (repo / "notes.txt").read_text() == "untracked\n"
    assert checkpoints.diff(config, "d1", f, c1["id"]) == []
    # the restore itself can be undone: the checkpoint taken before it has the broken file
    assert {c["path"] for c in checkpoints.diff(config, "d1", f, out["before"])} >= {"src/app.ts"}


def test_copy_checkpoints_for_a_folder_without_git(config, home):
    src = project(home / "Documents" / "etc-like")
    f = give(config, "d2", src, "rw")
    c1 = checkpoints.make(config, "d2", f, "before")
    assert c1["kind"] == "copy" and c1["files"] == 2 and checkpoints.make(config, "d2", f) == c1
    (src / "README.md").write_text("changed\n")
    (src / "node_modules" / "react" / "index.js").write_text("deps are not checkpointed\n")
    assert [c["path"] for c in checkpoints.diff(config, "d2", f, c1["id"])] == ["README.md"]
    checkpoints.restore(config, "d2", f, c1["id"])
    assert (src / "README.md").read_text() == "# 專案\n"
    config.data["sandbox"]["checkpoints_keep"] = 2
    for i in range(3):
        (src / "README.md").write_text(f"v{i}\n")
        checkpoints.make(config, "d2", f, f"v{i}")
    assert len(checkpoints.listing(config, "d2", "etc-like")) == 2
    assert len([p for p in (config.home / "sandbox" / "d2" / "checkpoints" / "etc-like").iterdir() if p.is_dir()]) == 2


def test_before_a_task_copies_sync_and_direct_folders_get_a_checkpoint(config, home, repo):
    src = project(home / "Documents" / "app")
    fr = give(config, "t1", src)
    staging.ensure_copy(config, "t1", fr)
    give(config, "t1", repo, "rw")
    (src / "README.md").write_text("edited\n")
    out = checkpoints.before_task(config, "t1", "做點事")
    assert out["synced"] == {"app": 1} and out["checkpoints"]["repo"]
    assert checkpoints.listing(config, "t1", "repo")[0]["label"] == "做點事"


def test_clone_keeps_symlinks_and_times(tmp_path):
    src = tmp_path / "s"
    (src / "d").mkdir(parents=True)
    (src / "d" / "f.txt").write_text("x")
    os.symlink("d/f.txt", src / "link")
    m = clone.clone_tree(src, tmp_path / "t")
    assert os.readlink(tmp_path / "t" / "link") == "d/f.txt" and list(m) == ["d/f.txt"]
    assert gitreview.git_ok()


# ------------------------------------------------------------------ the agent chooses (tools) and you restore (API)
def _ctx(config, answer):
    from test_sandbox_e import sandbox_ctx
    return sandbox_ctx(config, answer)


def _run(ctx, name, **args):
    from test_sandbox_e import dispatch
    return dispatch(ctx, name, **args).content


def test_the_agent_looks_then_asks_with_a_strategy(config, home, repo):
    from sinobee.tools.sandbox_tools import ALLOW
    ctx, m, ex, asked = _ctx(config, ALLOW)
    info = json.loads(_run(ctx, "host_folder_info", path="~/Documents/repo"))
    assert info["git_repository"] and info["reviewed_files"] == 5 and info["dependency_folders"] == [".next", "node_modules"]
    assert info["suggested"].endswith("review (by git)") and info["already"] is None
    assert json.loads(_run(ctx, "host_folder_info", path="~/.ssh"))["can_give"] is False
    out = _run(ctx, "request_host_access", path="~/Documents/repo", reason="修 bug", strategy="review")
    assert "reviewed by git" in out and "git" in asked[-1]
    assert next(f for f in fd.folders_for(config, "c1"))["via"] == "git"
    # a big non-git folder: review is refused, direct gets a checkpoint
    big = project(home / "Documents" / "big")
    config.data["sandbox"]["review_max_files"] = 1
    assert "strategy=direct" in _run(ctx, "request_host_access", path=str(big), reason="x", strategy="review")
    config.data["sandbox"]["review_max_files"] = 100
    out = _run(ctx, "request_host_access", path=str(big), reason="修環境", strategy="direct")
    assert "a checkpoint was taken now" in out and "檢查點" in asked[-1]
    assert [c["label"] for c in checkpoints.listing(config, "c1", "big")] == ["授權時"]
    (big / "README.md").write_text("broken\n")
    assert "checkpoint" in _run(ctx, "folder_checkpoint", folder="/host/big", action="make", label="改設定前")
    cid = checkpoints.listing(config, "c1", "big")[0]["id"]
    assert "modified  README.md" in _run(ctx, "folder_checkpoint", folder="big", action="diff", id=cid)
    assert "restored 1 files" in _run(ctx, "folder_checkpoint", folder="big", action="restore", id=cid)
    assert (big / "README.md").read_text() == "# 專案\n"
    assert "授權時" in _run(ctx, "folder_checkpoint", folder="big", action="list")
    assert "not a folder written directly" in _run(ctx, "folder_checkpoint", folder="repo", action="list")


def test_the_settings_popover_gives_folders_and_restores_checkpoints(config, home, repo):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sinobee.server import sandbox_api
    config.data["permissions"]["approval"] = "sandbox"
    app = FastAPI()
    sandbox_api.register(app, lambda: __import__("types").SimpleNamespace(config=config), None)
    with TestClient(app) as c:
        r = c.post("/api/sandbox/s1/folders", json={"path": "~/Documents/repo", "mode": "review"}).json()
        assert r["folders"][0]["via"] == "git"
        (config.home / "sandbox" / "s1" / "host" / "repo" / "README.md").write_text("changed\n")
        ch = c.get("/api/sandbox/s1/changes").json()
        assert ch["count"] == 1 and ch["folders"][0]["via"] == "git" and ch["folders"][0]["error"] is None
        other = project(home / "Documents" / "live")
        r = c.post("/api/sandbox/s1/folders", json={"path": str(other), "mode": "direct"}).json()
        live = next(f for f in r["folders"] if f["name"] == "live")
        assert live["mode"] == "rw" and live["checkpoints"] == 1
        items = c.get("/api/sandbox/s1/checkpoints", params={"folder": "live"}).json()["items"]
        (other / "README.md").write_text("oops\n")
        (other / "junk.txt").write_text("x\n")
        d = c.get(f"/api/sandbox/s1/checkpoints/{items[0]['id']}", params={"folder": "live"}).json()["changes"]
        assert {(x["path"], x["kind"]) for x in d} == {("README.md", "modified"), ("junk.txt", "added")}
        res = c.post(f"/api/sandbox/s1/checkpoints/{items[0]['id']}/restore", json={"folder": "live"}).json()
        assert res["restored"] == ["README.md"] and len(res["trashed"]) == 1 and res["before"]
        assert (other / "README.md").read_text() == "# 專案\n" and not (other / "junk.txt").exists()
        assert len(c.get("/api/sandbox/s1/checkpoints", params={"folder": "live"}).json()["items"]) == 2
        assert c.get("/api/sandbox/s1/checkpoints", params={"folder": "repo"}).status_code == 404
        assert c.post("/api/sandbox/s1/checkpoints", json={"folder": "live", "label": "手動"}).json()["id"]


@pytest.mark.docker
def test_in_the_sandbox_git_works_on_the_review_copy_and_direct_folders_can_go_back(config, home, repo):
    import asyncio
    import uuid

    from sinobee.sandbox.manager import manager_for
    from sinobee.tools.registry import ToolContext
    from sinobee.tools.sandbox_tools import ALLOW
    from test_sandbox_e import dispatch
    config.data["permissions"]["approval"] = "sandbox"
    config.data["sandbox"]["memory"] = "1500m"
    config.workspace.mkdir(parents=True, exist_ok=True)
    config.workspace.chmod(0o777)
    live = project(home / "Documents" / "live")
    for p in [home, *home.rglob("*")]:
        p.chmod(0o777 if p.is_dir() else 0o666)
    conv = "test-" + uuid.uuid4().hex[:8]
    ctx = ToolContext(config=config, session_id=conv)

    async def ask(q, options, multi, allow_text):
        return {"choices": [ALLOW]}
    ctx.ask = ask
    try:
        assert "reviewed by git" in dispatch(ctx, "request_host_access", path=str(repo), reason="x").content
        for p in [config.home / "sandbox", *(config.home / "sandbox").rglob("*")]:
            p.chmod(0o777 if p.is_dir() else 0o666)
        out = dispatch(ctx, "shell", command="cd /host/repo && "
                       "git log --format=%s -1 && echo 'export const b = 2;' >> src/app.ts && git add -A && "
                       "git commit -qm 'from the sandbox' && git log --format=%s -1").content
        assert "first" in out and "from the sandbox" in out
        assert [c["path"] for c in staging.pending(config, conv)] == ["src/app.ts"]
        assert git(repo, "log", "--format=%s", "-1") == "first\n"
        out = dispatch(ctx, "request_host_access", path=str(live), reason="修", strategy="direct").content
        assert "checkpoint" in out
        dispatch(ctx, "shell", command="echo broken > /host/live/README.md")
        assert (live / "README.md").read_text() == "broken\n"
        cid = checkpoints.listing(config, conv, "live")[0]["id"]
        assert "restored 1" in dispatch(ctx, "folder_checkpoint", folder="live", action="restore", id=cid).content
        assert (live / "README.md").read_text() == "# 專案\n"
    finally:
        asyncio.run(manager_for(config).remove(conv))
