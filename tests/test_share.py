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
    assert b["files"][0]["name"] == "chart.png" and b["files"][0]["path"].endswith("chart.png")
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
        ct = req.headers.get("content-type", "")
        if ct.startswith("multipart/"):
            import re as _re
            raw = req.read().decode("latin-1")
            m = _re.search(r'name="share"\r\n\r\n(.*?)\r\n--', raw, _re.S)
            seen["body"] = json.loads(m.group(1).encode("latin-1").decode())
            seen["files"] = _re.findall(r'name="files"; filename="([^"]+)"', raw)
        else:
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
    # attachments go as multipart: `share` JSON + files
    assert seen["body"]["visibility"] == "LOGIN" and seen["body"]["accounts"] == ["@carol"] and seen["body"]["expiresInDays"] == 7


def test_turns_rows_hosts_and_custom_mask(config):
    rows = "| id | name |\n|---|---|\n" + "\n".join(f"| {i} | n{i} |" for i in range(50))
    m = [
        {"role": "user", "content": "第一題 db.corp.internal 10.1.2.3 客戶 ACME"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "q", "name": "db_query", "arguments": {"sql": "select 1"}}]},
        {"role": "tool", "tool_call_id": "q", "content": rows},
        {"role": "assistant", "content": "ACME 很多訂單"},
        {"role": "user", "content": "第二題"},
        {"role": "assistant", "content": "答二", "tool_calls": [{"id": "s", "name": "skill_view", "arguments": {"name": "sales-report"}}]},
    ]
    b = share.build(config, m, until_turn=1, mask_hosts=True, extra_mask=["ACME"])
    assert [x["role"] for x in b["messages"]] == ["user", "assistant"]
    t = json.dumps(b, ensure_ascii=False)
    assert "ACME" not in t and "10.1.2.3" not in t and "db.corp.internal" not in t
    out = b["messages"][1]["tools"][0]["output"]
    assert "| 19 | n19 |" in out and "| 20 | n20 |" not in out and "另有 30 列" in out
    assert share.build(config, m, query_rows=0)["messages"][1]["tools"][0]["output"] == "（查詢結果未分享）"
    assert share.build(config, m)["skills"] == ["sales-report"]
    assert share.user_turns(m) == ["第一題 db.corp.internal 10.1.2.3 客戶 ACME", "第二題"]


def test_links_to_workspace_files_are_attached(config):
    ws = Path(config.workspace)
    (ws / "report.pdf").write_bytes(b"%PDF fake")
    (ws / "clip.mp4").write_bytes(b"mp4")
    b = share.build(config, [{"role": "user", "content": "x"},
                             {"role": "assistant", "content": "[報告](report.pdf) 和 ![影片](clip.mp4) 與 [官網](https://sinobee.ai)"}])
    assert [f["name"] for f in b["files"]] == ["clip.mp4", "report.pdf"]
    assert "[報告](files/2)" in b["messages"][1]["text"] and "[官網](https://sinobee.ai)" in b["messages"][1]["text"]


def test_import_share(config, monkeypatch):
    doc = {"v": 1, "title": "分析", "messages": [{"role": "user", "text": "問"}, {"role": "assistant", "text": "答 ![圖](files/1)",
           "tools": [{"name": "db_query", "output": "| a |"}]}], "files": [{"id": "1", "name": "c.png", "mime": "image/png", "size": 3}]}

    def get(url, **kw):
        if url.endswith("/api/v1/shares/AbCdEf123456"):
            return httpx.Response(200, json={"id": "AbCdEf123456", "doc": doc})
        if url.endswith("/files/1"):
            return httpx.Response(200, content=b"png")
        return httpx.Response(404)
    monkeypatch.setattr(share.httpx, "get", get)
    from sinobee.memory.store import MemoryStore
    mem = MemoryStore(config.db_path)
    r = share.import_share(config, mem, "https://sinobee.ai/s/AbCdEf123456")
    msgs = mem.load_messages(r["session_id"])
    assert [x.role for x in msgs] == ["user", "assistant"] and "imports/AbCdEf123456/c.png" in msgs[1].content
    assert (Path(config.workspace) / "imports/AbCdEf123456/c.png").read_bytes() == b"png"
    with pytest.raises(share.ShareError):
        share.import_share(config, mem, "https://evil.example/x")
