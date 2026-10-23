"""Writing back as a transaction (sandbox/writeback.py): prepared first, then file by file with a log, so a write-back
cut off halfway can be finished or undone; and the difference as a .patch / .zip."""
import io
import json
import os
import subprocess
import types
import zipfile

import pytest

from sinobee.sandbox import folders as fd
from sinobee.sandbox import staging, writeback
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


def folder_with(config, home, conv="w1", git=False, n=10):
    src = home / "Documents" / "proj"
    (src / "src").mkdir(parents=True)
    for i in range(n):
        (src / "src" / f"f{i}.txt").write_text(f"old {i}\n")
    (src / "gone.txt").write_text("delete me\n")
    (src / ".env").write_text("SECRET=1\n")
    (src / "logo.bin").write_bytes(b"\0\1old")
    if git:
        env = {**os.environ, "GIT_AUTHOR_NAME": "u", "GIT_AUTHOR_EMAIL": "u@x", "GIT_COMMITTER_NAME": "u",
               "GIT_COMMITTER_EMAIL": "u@x"}
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "first"]):
            subprocess.run(["git", "-C", str(src), *args], check=True, capture_output=True, env=env)
    fd.add_grant(config, conv, fd.entry(src, "review", via=fd.review_via(src)))
    f = fd.folders_for(config, conv)[0]
    copy = staging.ensure_copy(config, conv, f)
    for i in range(n):
        (copy / "src" / f"f{i}.txt").write_text(f"new {i}\n")
    (copy / "src" / "added.txt").write_text("added\n")
    (copy / "gone.txt").unlink()
    (copy / ".env").write_text("SECRET=2\n")
    (copy / "logo.bin").write_bytes(b"\0\1new")
    return src, copy, f


def state(src, n=10):
    return {i: (src / "src" / f"f{i}.txt").read_text() for i in range(n)}


def test_an_interrupted_write_back_can_be_finished(config, home):
    src, copy, f = folder_with(config, home)
    with pytest.raises(writeback._Stop):
        writeback.apply(config, "w1", f, stop_after=4)
    [cut] = writeback.interrupted(config, "w1")
    assert cut["done"] == 4 and cut["total"] == 14
    # half old, half new: the first four (.env, gone.txt, logo.bin, src/added.txt) are written, src/f* not yet
    assert (src / "logo.bin").read_bytes() == b"\0\1new" and not (src / "gone.txt").exists()
    assert all(v == f"old {i}\n" for i, v in state(src).items())
    assert staging.applied_batches(config, "w1") == []                            # not offered as a finished batch
    with pytest.raises(staging.StagingError):
        staging.undo(config, "w1", cut["id"])
    # the sandbox keeps changing its copy: what is finished is what was reviewed
    (copy / "src" / "f9.txt").write_text("later\n")
    res = writeback.resume(config, "w1", cut["id"])
    assert len(res["applied"]) == 14 and not res["conflicts"]
    assert all(v == f"new {i}\n" for i, v in state(src).items()) and not (src / "gone.txt").exists()
    assert (src / "src" / "added.txt").read_text() == "added\n" and (src / "logo.bin").read_bytes() == b"\0\1new"
    assert writeback.interrupted(config, "w1") == []
    assert [c["path"] for c in staging.changes(config, "w1", f)] == ["src/f9.txt"]  # only the later edit is left
    assert not list(src.rglob("*.sinobee-tmp"))
    # and the finished batch can be undone as a whole
    staging.undo(config, "w1", res["id"])
    assert all(v == f"old {i}\n" for i, v in state(src).items()) and (src / "gone.txt").exists()


def test_an_interrupted_write_back_can_be_rolled_back(config, home):
    src, copy, f = folder_with(config, home)
    with pytest.raises(writeback._Stop):
        writeback.apply(config, "w1", f, stop_after=7)
    # a file written but not yet logged (the power went off between the two), and a temporary file left over
    rec = writeback.load(config, "w1", writeback.interrupted(config, "w1")[0]["id"])
    nxt = rec["items"][7]
    if nxt["kind"] == "modified":
        (src / nxt["path"]).write_bytes((writeback._store(config, "w1", rec["id"]) / "new" / nxt["path"]).read_bytes())
    writeback._tmp(src / rec["items"][8]["path"]).write_text("half")
    res = writeback.rollback(config, "w1", rec["id"])
    assert all(v == f"old {i}\n" for i, v in state(src).items())
    assert (src / "gone.txt").read_text() == "delete me\n" and not (src / "src" / "added.txt").exists()
    assert not list(src.rglob("*.sinobee-tmp")) and res["restored"]
    assert writeback.interrupted(config, "w1") == []
    assert len(staging.changes(config, "w1", f)) == 14                           # waiting for review again
    with pytest.raises(staging.StagingError):
        writeback.resume(config, "w1", rec["id"])


def test_your_edit_after_preparing_is_never_overwritten(config, home):
    src, copy, f = folder_with(config, home)
    with pytest.raises(writeback._Stop):
        writeback.apply(config, "w1", f, stop_after=2)
    rec = writeback.load(config, "w1", writeback.interrupted(config, "w1")[0]["id"])
    last = next(it for it in reversed(rec["items"]) if it["kind"] == "modified")
    (src / last["path"]).write_text("you, meanwhile\n")
    res = writeback.resume(config, "w1", rec["id"])
    assert last["path"] in res["conflicts"] and (src / last["path"]).read_text() == "you, meanwhile\n"


def test_nothing_is_written_when_preparing_finds_a_problem(config, home, monkeypatch):
    src, copy, f = folder_with(config, home)
    monkeypatch.setattr(writeback.shutil, "disk_usage", lambda p: types.SimpleNamespace(free=1000))
    with pytest.raises(staging.StagingError, match="磁碟空間不足"):
        writeback.apply(config, "w1", f)
    assert all(v == f"old {i}\n" for i, v in state(src).items())
    assert not (config.home / "sandbox" / "w1" / "applied").exists() or \
        not list((config.home / "sandbox" / "w1" / "applied").iterdir())


def test_the_difference_exports_as_zip_without_secrets(config, home):
    src, copy, f = folder_with(config, home, n=2)
    name, data, media = writeback.export_current(config, "w1", f)
    z = zipfile.ZipFile(io.BytesIO(data))
    names = set(z.namelist())
    assert name.endswith(".zip") and media == "application/zip"
    assert {"files/src/f0.txt", "files/src/added.txt", "files/logo.bin", "deleted.txt", "changes.diff", "README.txt"} <= names
    assert not any(".env" in n for n in names) and ".env" in z.read("README.txt").decode()
    assert z.read("deleted.txt").decode() == "gone.txt\n"
    diff = z.read("changes.diff").decode()
    assert "-old 0\n+new 0" in diff and "+++ b/src/added.txt" in diff and "Binary files a/logo.bin" in diff
    # patch -p1 brings an untouched folder to the new state (text files)
    other = home / "other"
    import shutil
    shutil.copytree(src, other)
    subprocess.run(["patch", "-p1", "-s", "-d", str(other)], input=diff.encode(), check=True)
    assert (other / "src" / "f1.txt").read_text() == "new 1\n" and (other / "src" / "added.txt").read_text() == "added\n"
    # what a finished write-back wrote, from its own record
    res = staging.apply(config, "w1", f)
    _n, data, _m = writeback.export_batch(config, "w1", res["id"])
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.read("files/src/f0.txt") == b"new 0\n" and "SECRET" not in data.decode("latin-1")


def test_a_git_folder_exports_a_patch_git_can_apply(config, home):
    src, copy, f = folder_with(config, home, git=True, n=2)
    name, data, media = writeback.export_current(config, "w1", f, ["src/f0.txt", "src/added.txt", "logo.bin"])
    text = data.decode()
    assert name.endswith(".patch") and "GIT binary patch" in text and "+new 0" in text and "f1.txt" not in text
    p = home / "x.patch"
    p.write_bytes(data)
    subprocess.run(["git", "-C", str(src), "apply", "--3way", str(p)], check=True, capture_output=True)
    assert (src / "src" / "f0.txt").read_text() == "new 0\n" and (src / "logo.bin").read_bytes() == b"\0\1new"


def test_the_review_panel_offers_finish_or_roll_back(config, home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sinobee.server import sandbox_api
    src, copy, f = folder_with(config, home)
    config.data["permissions"]["approval"] = "sandbox"
    with pytest.raises(writeback._Stop):
        writeback.apply(config, "w1", f, stop_after=5)
    events = []
    app = FastAPI()
    sandbox_api.register(app, lambda: types.SimpleNamespace(config=config), lambda k, d: events.append((k, d)))
    with TestClient(app) as c:
        r = c.get("/api/sandbox/w1/changes").json()
        [cut] = r["interrupted"]
        assert cut["done"] == 5
        assert c.post("/api/sandbox/w1/changes/undo", json={"id": cut["id"]}).status_code == 404
        j = c.post("/api/sandbox/w1/changes/rollback", json={"id": cut["id"]}).json()
        assert j["interrupted"] == 0 and j["pending"] == 14 and events[-1][1]["interrupted"] == 0
        assert c.post("/api/sandbox/w1/changes/resume", json={"id": cut["id"]}).status_code == 409
        r = c.get("/api/sandbox/w1/changes/export", params={"folder": "proj", "path": ["src/f0.txt"]})
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        assert zipfile.ZipFile(io.BytesIO(r.content)).namelist().count("files/src/f0.txt") == 1
        j = c.post("/api/sandbox/w1/changes/apply", json={"folder": "proj"}).json()
        assert len(j["applied"]) == 14 and j["pending"] == 0
        r = c.get(f"/api/sandbox/w1/applied/{j['id']}/export")
        assert r.status_code == 200 and json.loads(json.dumps(r.headers["content-type"])) == "application/zip"


def test_no_new_write_back_while_one_is_cut_off(config, home):
    src, copy, f = folder_with(config, home)
    with pytest.raises(writeback._Stop):
        writeback.apply(config, "w1", f, stop_after=3)
    with pytest.raises(staging.StagingError, match="中斷"):
        staging.apply(config, "w1", f)
