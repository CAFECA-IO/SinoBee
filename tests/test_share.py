"""Conversation sharing: redaction, building, upload against a fake hub."""
import json
from pathlib import Path

import httpx
import pytest

from sinobee import account, share


def msgs():
    return [
        {"role": "user", "content": "連到 postgres://demo:pg-S3cret-9@db:5432/shop 看前五大客戶，我的 key 是 sk-abcdefghijklmnopqrstuvwx"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "db_query", "arguments": {"sql": "select * from t", "password": "hunter22"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "db_query", "content": "| name |\n|---|\n| Alice |"},
        {"role": "assistant", "content": f"結果如下，檔案在 {Path.home()}/report.csv\n\n![圖](chart.png)\n\nAuthorization: Bearer abcdefghijklmnopqrstuvwxyz0123"},
    ]


def test_build_redacts_everything_sensitive(config):
    config.secrets.set_secret("sinobee-ai:token", "sbh_" + "z" * 30)
    config.secrets.set("openai", "stored-openai-key-123456")
    (Path(config.workspace) / "chart.png").write_bytes(b"\x89PNG fake")
    m = msgs()
    m[0]["content"] += " 還有 stored-openai-key-123456"
    b = share.build(config, m)
    text = json.dumps(b, ensure_ascii=False)
    for leak in ("pg-S3cret-9", "sk-abcdefghijklmnopqrstuvwx", "hunter22", "stored-openai-key-123456", str(Path.home()), "abcdefghijklmnopqrstuvwxyz0123"):
        assert leak not in text, leak
    kinds = {r["kind"] for r in b["redactions"]}
    assert {"dsn", "apikey", "secret", "password", "path", "token"} <= kinds
    assert b["title"].startswith("連到 postgres://demo:••••@db")
    a = b["messages"][1]
    assert a["role"] == "assistant" and a["tools"][0]["name"] == "db_query" and "Alice" in a["tools"][0]["output"]
    assert "![圖](files/1)" in a["text"]
    assert b["files"][0]["name"] == "chart.png"
    # without tool details only the names remain
    b2 = share.build(config, msgs(), include_tools=False)
    assert b2["messages"][1]["tools"] == [{"name": "db_query"}]


def test_images_outside_workspace_are_not_attached(config, tmp_path):
    secret = tmp_path / "outside.png"
    secret.write_bytes(b"x")
    b = share.build(config, [{"role": "user", "content": "hi"}, {"role": "assistant", "content": f"![x]({secret})\n![y](../../etc/passwd.png)"}])
    assert b["files"] == [] and "未附上" in b["messages"][1]["text"]


def test_upload_uses_device_token(config, monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(201, json={"id": "abc", "url": "http://hub/s/abc", "visibility": "LOGIN", "restricted": True})
    real = httpx.Client

    def request(method, url, **kw):
        kw.pop("timeout", None)
        with real(transport=httpx.MockTransport(handler)) as c:
            return c.request(method, url, **kw)
    monkeypatch.setattr(share.httpx, "request", request)
    with pytest.raises(account.AccountError):
        share.upload(config, {"title": "t", "messages": []})
    account.save(config, {"access_token": "sbh_" + "q" * 30, "scope": "shares:write", "account": "0xabc"})
    r = share.upload(config, share.build(config, msgs()), visibility="login", accounts=["@carol"], expires_days=7)
    assert r["url"].endswith("/s/abc")
    assert seen["auth"] == "Bearer sbh_" + "q" * 30
    assert seen["body"]["visibility"] == "LOGIN" and seen["body"]["accounts"] == ["@carol"] and seen["body"]["expiresInDays"] == 7
