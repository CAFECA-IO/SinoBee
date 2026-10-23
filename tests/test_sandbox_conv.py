"""One settings file per conversation (sandbox/convconf.py): folders, network and overrides, the API behind the
「對話設定」 popover, and tidying up ~/.sinobee/sandbox."""
import json
import os
import time
import types

import pytest

from sinobee.sandbox import convconf
from sinobee.sandbox import folders as fd
from sinobee.sandbox import staging
from sinobee.sandbox.manager import SandboxManager, image_name


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "userhome"
    (h / "Documents" / "報告").mkdir(parents=True)
    (h / "Documents" / "報告" / "a.md").write_text("第一版\n")
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture(autouse=True)
def no_docker_check(monkeypatch):
    monkeypatch.setattr(SandboxManager, "unavailable", lambda self, max_age=60: None)


def test_old_grants_json_becomes_conversation_json(config, home):
    d = convconf.conv_dir(config, "old")
    d.mkdir(parents=True)
    (d / "grants.json").write_text(json.dumps([{"path": str(home / "Documents" / "報告"), "name": "報告", "mode": "ro"}]))
    assert [f["mode"] for f in fd.folders_for(config, "old")] == ["ro"]
    assert not (d / "grants.json").exists()
    data = json.loads((d / "conversation.json").read_text())
    assert data["version"] == 1 and data["conversation"] == "old" and data["folders"][0]["name"] == "報告"
    assert data["network"] == {"domains": [], "all": False} and data["overrides"] == {}


def test_a_broken_or_missing_file_is_a_blank_set(config, home):
    assert convconf.load(config, "none")["folders"] == [] and not convconf.path_of(config, "none").exists()
    convconf.touch(config, "none")                                   # nothing to keep: no file is made
    assert not convconf.path_of(config, "none").exists()
    p = convconf.path_of(config, "bad")
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    assert convconf.load(config, "bad")["network"]["all"] is False


def test_a_conversation_can_differ_from_settings(config, home):
    assert fd.write_mode(config, "write", "c1") == "review" and image_name(config, "c1") == image_name(config)
    convconf.update(config, "c1", lambda d: d["overrides"].update(review_writes=False, image="office"))
    assert fd.write_mode(config, "write", "c1") == "rw" and fd.write_mode(config, "write", "c2") == "review"
    assert "office" in image_name(config, "c1") and "office" not in image_name(config, "c2")
    convconf.update(config, "c1", lambda d: d["overrides"].update(image="nonsense"))
    assert convconf.image(config, "c1") == "standard"


def test_kept_domains_open_only_their_conversation(config, home):
    convconf.update(config, "c1", lambda d: d["network"].update(domains=["github.com"]))
    m = SandboxManager(config)
    assert "github.com" in m.policy("c1")["domains"] and "github.com" not in m.policy("c2")["domains"]
    convconf.update(config, "c1", lambda d: d["network"].update(all=True))
    assert m.policy("c1")["all"] is True and m.policy("c2")["all"] is False


def test_sweep_removes_old_drafts_tidies_unused_and_keeps_pending(config, home):
    now = time.time()
    old = now - 40 * 86400
    rep = home / "Documents" / "報告"
    for c in ("draft", "unused", "waiting", "fresh"):
        fd.add_grant(config, c, fd.entry(rep, "review"))
        staging.ensure_copy(config, c, fd.folders_for(config, c)[0])
    for c in ("draft", "unused", "waiting"):
        convconf.update(config, c, lambda d: d.__setitem__("used", old))
    (staging.copy_dir(config, "waiting", "報告") / "a.md").write_text("改了\n")
    staging.pending(config, "waiting")
    # an old apply backup of a fresh conversation
    b = convconf.conv_dir(config, "fresh") / "applied" / "b1"
    b.mkdir(parents=True)
    (b / "record.json").write_text(json.dumps({"t": old}))
    out = convconf.sweep_files(config, known=lambda c: c != "draft", now=now)
    assert out["drafts"] == ["draft"] and out["tidied"] == ["unused"] and out["kept_pending"] == ["waiting"]
    assert out["backups"] == 1 and not b.exists()
    assert not convconf.conv_dir(config, "draft").exists()
    u = convconf.conv_dir(config, "unused")
    assert (u / "conversation.json").exists() and not (u / "host").exists() and not (u / "base").exists()
    assert fd.folders_for(config, "unused")[0]["name"] == "報告"          # the folder is still given
    assert (convconf.conv_dir(config, "waiting") / "host").exists()
    assert (convconf.conv_dir(config, "fresh") / "host").exists()
    assert (rep / "a.md").read_text() == "第一版\n"                        # the user's folder is never touched
    # a day-old draft of a conversation that may still exist (known=None) stays
    assert convconf.sweep_files(config, now=now)["drafts"] == []


def test_settings_api_reads_and_changes_this_conversation(config, home, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sinobee.server import sandbox_api
    config.data["permissions"]["approval"] = "sandbox"
    recreated = []

    async def recreate(self, conv):
        recreated.append(conv)
    monkeypatch.setattr(SandboxManager, "recreate", recreate)
    events = []
    app = FastAPI()
    sandbox_api.register(app, lambda: types.SimpleNamespace(config=config), lambda k, d: events.append((k, d)))
    with TestClient(app) as c:
        s = c.get("/api/sandbox/s1/settings").json()
        assert s["network"] == {"domains": [], "all": False} and s["overrides"] == {} and s["folders"] == []
        assert s["effective"] == s["defaults"] == {"review_writes": True, "image": "standard"}
        s = c.put("/api/sandbox/s1/settings", json={"network": {"domains": ["https://GitHub.com/x", "bad one"]}}).json()
        assert s["network"]["domains"] == ["github.com"] and events[-1] == ("folders", {"session_id": "s1"})
        s = c.put("/api/sandbox/s1/settings", json={"overrides": {"review_writes": False}}).json()
        assert s["effective"]["review_writes"] is False and s["review_writes"] is False
        assert c.post("/api/sandbox/s1/folders", json={"path": "~/Documents/報告"}).json()["folders"][0]["mode"] == "rw"
        assert c.put("/api/sandbox/s1/settings", json={"overrides": {"image": "huge"}}).status_code == 400
        assert recreated == []
        s = c.put("/api/sandbox/s1/settings", json={"overrides": {"image": "office"}}).json()
        assert s["effective"]["image"] == "office" and recreated == ["s1"]
        s = c.put("/api/sandbox/s1/settings", json={"overrides": {"review_writes": None, "image": None}, "network": {"all": True}}).json()
        assert s["overrides"] == {} and s["network"]["all"] is True and recreated == ["s1", "s1"]
        assert c.get("/api/sandbox/s2/settings").json()["network"]["all"] is False
    data = json.loads(convconf.path_of(config, "s1").read_text())
    assert data["folders"][0]["mode"] == "rw" and data["network"] == {"domains": ["github.com"], "all": True}


def test_gui_may_open_files_listed_in_conversation_json(config, home):
    from sinobee import fileaccess
    fd.add_grant(config, "g1", fd.entry(home / "Documents" / "報告", "ro"))
    roots = [str(r) for r in fileaccess.allowed_roots(config)]
    assert any(r.endswith("報告") for r in roots), roots
    assert os.path.isdir(next(r for r in roots if r.endswith("報告")))
