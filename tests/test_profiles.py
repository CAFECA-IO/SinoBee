"""One set of SinoBee data per SinoBee.ai account (profiles.py): signing out or in as someone else never shows the
previous person's conversations, memory, workspace or API keys."""
import json
import types

import pytest

from sinobee import account, profiles
from sinobee.config import Config

LUPHIA = {"account": "acct-luphia", "handle": "luphia"}
OTHER = {"account": "acct-other", "handle": "other"}


def signed_in(cfg, info, token="tok"):
    cfg.secrets.set_secret(account.TOKEN, token)
    cfg.secrets.set_secret(account.ACCOUNT, json.dumps(info))
    cfg.secrets.set_secret(account.SIGNING_KEY, "sk-" + info["handle"])


@pytest.fixture
def base(tmp_path, monkeypatch):
    b = tmp_path / "sbhome"
    monkeypatch.setenv("SINOBEE_HOME", str(b))
    monkeypatch.delenv("SINOBEE_WORKSPACE", raising=False)
    return b


def test_accounts_never_see_each_others_data(base):
    cfg = Config.load()
    assert cfg.home == base and cfg.workspace == base / "workspace"
    cfg.secrets.set_secret("server_token", "srv-1")
    cfg.secrets.set_secret("openai_api_key", "sk-luphia-key")
    (base / "settings.json").write_text(json.dumps({"server": {"port": 10024}, "agent": {"instructions": "私人"}}))
    (cfg.workspace / "luphia-notes.md").write_text("私人\n")
    signed_in(cfg, LUPHIA)
    assert profiles.claim_if_unowned(cfg, account.account(cfg))          # the data here is the signed-in person's
    assert not profiles.claim_if_unowned(cfg, account.account(cfg))
    # sign out: the local profile, with nothing of hers
    account.logout(cfg)
    assert profiles.switch_after_logout(cfg) == {"switched": True, "key": "local"}
    local = Config.load()
    assert local.home == base / "profiles" / "local" and local.workspace == local.home / "workspace"
    assert not list(local.workspace.iterdir()) and local.secrets.get_secret("openai_api_key") is None
    assert local.server_token() == "srv-1"                                # the open window keeps working
    assert json.loads((local.home / "settings.json").read_text()) == {"server": {"port": 10024}}
    # someone else signs in: their own empty profile; the sign-in moves there
    signed_in(local, OTHER, "tok-other")
    res = profiles.switch_after_login(local, account.account(local))
    assert res["switched"] and res["key"].startswith("acct-")
    assert local.secrets.get_secret(account.TOKEN) is None and local.secrets.get_secret(account.SIGNING_KEY) is None
    theirs = Config.load()
    assert theirs.home == base / "profiles" / res["key"] and account.account(theirs)["handle"] == "other"
    assert theirs.secrets.get_secret(account.SIGNING_KEY) == "sk-other"
    assert not (theirs.workspace / "luphia-notes.md").exists() and theirs.secrets.get_secret("openai_api_key") is None
    # they sign out, she signs in again (from the local profile): back to her data
    account.logout(theirs)
    profiles.switch_after_logout(theirs)
    again = Config.load()
    signed_in(again, LUPHIA, "tok-2")
    assert profiles.switch_after_login(again, account.account(again))["key"] == "base"
    mine = Config.load()
    assert mine.home == base and (mine.workspace / "luphia-notes.md").read_text() == "私人\n"
    assert mine.secrets.get_secret("openai_api_key") == "sk-luphia-key" and account.token(mine) == "tok-2"
    # signing in again as the same account changes nothing
    assert profiles.switch_after_login(mine, account.account(mine)) == {"switched": False, "claimed": False, "key": "base"}


def test_a_profile_dir_that_disappeared_falls_back_to_the_first(base):
    profiles.save({"active": "acct-gone", "base_owner": "x", "profiles": {}}, base)
    assert Config.load().home == base


def test_signing_out_restarts_only_when_idle(base):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sinobee.server import account_api
    cfg = Config.load()
    signed_in(cfg, LUPHIA)
    profiles.claim_if_unowned(cfg, account.account(cfg))
    running, restarts = {"x": True}, []
    app = FastAPI()
    account_api.register(app, lambda: types.SimpleNamespace(config=cfg), busy=lambda: bool(running),
                         restart=lambda: restarts.append(1))
    with TestClient(app) as c:
        st = c.get("/api/account").json()
        assert st["signed_in"] and st["profile"]["owner"] == "acct-luphia" and st["profile"]["separated"]
        r = c.post("/api/account/logout")
        assert r.status_code == 409 and account.token(cfg)                      # nothing happened
        running.clear()
        assert c.post("/api/account/logout").json() == {"ok": True, "restarting": True} and restarts == [1]
    assert profiles.load(base)["active"] == "local"


def test_restart_runs_the_same_command(monkeypatch):
    import sys
    calls = []
    monkeypatch.delenv("SINOBEE_NO_RESTART", raising=False)
    monkeypatch.setattr(profiles.os, "execv", lambda exe, argv: calls.append((exe, argv)))
    monkeypatch.setattr(sys, "orig_argv", ["python3", "-m", "sinobee.cli", "serve", "--port", "8811"])
    monkeypatch.setattr(profiles.time, "sleep", lambda s: None)
    profiles.restart_process(0)
    import time
    for _ in range(50):
        if calls:
            break
        time.sleep(0.02)
    assert calls == [(sys.executable, [sys.executable, "-m", "sinobee.cli", "serve", "--port", "8811"])]
