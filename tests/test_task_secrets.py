"""Security plan stage 2: keys given to one task (request_secret), the egress proxy refusing to carry them elsewhere,
domains allowed for an hour, and the per-conversation record of what the tools did."""
import asyncio
import json
import time
import types

import pytest

from sinobee import audit
from sinobee import task_secrets as ts
from sinobee.sandbox import egress
from sinobee.sandbox.manager import SandboxManager
from sinobee.tools.registry import REGISTRY, ToolContext
from sinobee.tools.secret_tools import ALLOW, NO
from sinobee.types import ToolCall

KEY = "rk_live_" + "Q1w2E3r4T5y6U7i8O9p0"


def run(ctx, tool_name, **args):
    return asyncio.run(REGISTRY.dispatch(ToolCall("1", tool_name, args), ctx))


def ctx_for(config, answer, conv="c1"):
    asked = []

    async def ask(q, options, multi, text):
        asked.append(q)
        return None if answer is None else {"choices": [answer]}

    async def approve(tool, args, reason):
        asked.append(("approve", tool, reason))
        return False
    c = ToolContext(config=config, session_id=conv, ask=ask, approve=approve)
    c.state["sandbox_conversation"] = conv
    c.asked = asked
    return c


def test_keys_are_kept_without_showing_their_value(config):
    with pytest.raises(ValueError):
        ts.save(config, "stripe", value=KEY, domains=[])                 # a key must say where it goes
    k = ts.save(config, "stripe", value=KEY, domains=["https://api.stripe.com/v1"], note="測試")
    assert k == {**k, "name": "stripe", "env": "STRIPE", "domains": ["api.stripe.com"], "set": True}
    assert KEY not in json.dumps(ts.listing(config)) and k["hint"] == "…" + KEY[-4:]
    ts.save(config, "stripe", env="STRIPE_API_KEY", domains=["api.stripe.com"])   # value kept when not given
    assert config.secrets.task_secrets()["stripe"]["value"] == KEY
    assert KEY in config.secrets.all_values()                               # redacted everywhere keys are


def test_a_task_gets_a_key_only_when_you_allow_it_and_never_sees_it(config):
    config.data["permissions"]["approval"] = "auto"
    ts.save(config, "stripe", value=KEY, env="STRIPE_API_KEY", domains=["api.stripe.com"])
    c = ctx_for(config, NO)
    out = run(c, "request_secret", name="stripe", reason="查詢訂單").content
    assert "did not give" in out and "STRIPE_API_KEY" not in json.dumps(ts.env_for(c))
    assert "stripe" in run(c, "request_secret").content                     # no name: what there is
    c = ctx_for(config, ALLOW)
    out = run(c, "request_secret", name="stripe", reason="查詢訂單").content
    assert "$STRIPE_API_KEY" in out and "查詢訂單" in c.asked[0] and "api.stripe.com" in c.asked[0]
    out = run(c, "shell", command='echo "key=$STRIPE_API_KEY"; echo -n "$STRIPE_API_KEY" | base64').content
    assert KEY not in out and "key=[key stripe]" in out                     # the model sees a label
    assert c.state["secret_seen"] == "the key stripe"
    assert ts.end_task(c) and ts.env_for(c) == {}
    out = run(c, "shell", command='echo "key=$STRIPE_API_KEY"').content
    assert "key=\n" in out + "\n" or out.rstrip().endswith("key=")          # taken back with the task


def test_the_proxy_keeps_a_key_to_its_own_sites():
    egress.policies.clear()
    egress.policies["10.0.0.2"] = {"conversation": "c1", "domains": ["pypi.org", "api.stripe.com"], "all": False,
                                   "until": {"example.com": time.time() + 60, "old.example": time.time() - 1},
                                   "secrets": [{"name": "stripe", "value": KEY, "domains": ["api.stripe.com"]}]}
    assert egress.allowed("10.0.0.2", "example.com")[0] and not egress.allowed("10.0.0.2", "old.example")[0]
    assert egress.leak("10.0.0.2", "api.stripe.com", f"GET /v1/charges HTTP/1.1\r\nAuthorization: Bearer {KEY}".encode()) is None
    why = egress.leak("10.0.0.2", "pypi.org", f"GET /simple/?q={KEY} HTTP/1.1\r\n".encode())
    assert "stripe" in why
    import base64
    assert egress.leak("10.0.0.2", "pypi.org", b"POST / HTTP/1.1\r\n\r\n" + base64.b64encode(KEY.encode()))
    assert egress.leak("10.0.0.2", f"{KEY.lower()}.evil.example", b"CONNECT x:443 HTTP/1.1")   # spelled into a name
    assert "looks like a key" in egress.leak("10.0.0.2", "pypi.org", b"GET /?t=ghp_" + b"a" * 36 + b" HTTP/1.1")
    assert egress.leak("10.0.0.2", "pypi.org", b"GET /simple/requests/ HTTP/1.1\r\n") is None


def test_proxy_end_to_end_blocks_a_request_carrying_the_key(monkeypatch):
    """The real proxy loop: a plain-HTTP request with the key to another allowed site gets a 403 naming the key."""
    async def scenario():
        egress.policies.clear()
        egress.policies["127.0.0.1"] = {"conversation": "c1", "domains": ["127.0.0.1"], "all": False, "until": {},
                                        "secrets": [{"name": "stripe", "value": KEY, "domains": ["api.stripe.com"]}]}
        hits = []

        async def upstream(r, w):
            hits.append(await r.read(1000))
            w.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await w.drain()
            w.close()
        up = await asyncio.start_server(upstream, "127.0.0.1", 0)
        port = up.sockets[0].getsockname()[1]
        proxy = await asyncio.start_server(egress.handle_proxy, "127.0.0.1", 0)
        pport = proxy.sockets[0].getsockname()[1]

        async def send(req: bytes) -> bytes:
            r, w = await asyncio.open_connection("127.0.0.1", pport)
            w.write(req)
            await w.drain()
            data = await r.read(4000)
            w.close()
            return data
        body = f"token={KEY}".encode()
        bad = await send(f"POST http://127.0.0.1:{port}/collect HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                         f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        good = await send(f"POST http://127.0.0.1:{port}/ok HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                          f"Content-Length: 5\r\n\r\nhello".encode())
        up.close()
        proxy.close()
        return bad, good, hits
    bad, good, hits = asyncio.run(scenario())
    assert bad.startswith(b"HTTP/1.1 403") and b"stripe" in bad
    assert good.endswith(b"ok") and len(hits) == 1 and hits[0].endswith(b"hello")   # the body still goes through
    assert any(e["reason"].startswith("key:") and not e["allowed"] for e in egress.log)


def test_the_sandbox_policy_carries_keys_and_hour_grants(config):
    m = SandboxManager(config)
    m.task_secrets["c1"] = {"stripe": {"value": KEY, "domains": ["api.stripe.com"]}}
    m.until["c1"] = {"example.com": time.time() + 3600, "gone.example": time.time() - 5}
    p = m.policy("c1")
    assert "api.stripe.com" in p["domains"] and list(p["until"]) == ["example.com"]
    assert p["secrets"] == [{"name": "stripe", "value": KEY, "domains": ["api.stripe.com"]}]
    assert m.policy("c2")["secrets"] == [] and "api.stripe.com" not in m.policy("c2")["domains"]


def test_the_record_of_what_the_tools_did(config):
    config.data["permissions"]["approval"] = "confirm"
    c = ctx_for(config, None)
    run(c, "write_file", path="notes.md", content="hello")
    run(c, "shell", command="rm -rf build")                                # asks; refused
    run(c, "read_file", path="notes.md")                                    # reading is not recorded
    entries = audit.read(config, "c1")
    assert [(e["tool"], e["outcome"]) for e in entries] == [("write_file", "ok"), ("shell", "refused")]
    assert entries[1]["what"] == "rm -rf build" and entries[1]["reason"] == "recursive delete"
    ts.save(config, "gh", value="ghp_" + "b" * 36, domains=["api.github.com"])
    config.data["permissions"]["approval"] = "auto"
    run(c, "shell", command="echo ghp_" + "b" * 36)
    assert "ghp_" + "b" * 36 not in audit.path_of(config, "c1").read_text()   # keys never written
    # the tools cannot read or rewrite it
    assert run(c, "read_file", path=str(audit.path_of(config, "c1"))).is_error


def test_settings_api_for_keys_and_the_record(config):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sinobee.server import security_api
    app = FastAPI()
    security_api.register(app, lambda: types.SimpleNamespace(config=config))
    with TestClient(app) as c:
        assert c.put("/api/task-secrets/stripe", json={"value": KEY, "domains": ["api.stripe.com"]}).status_code == 200
        assert c.put("/api/task-secrets/bad name", json={"value": "x", "domains": ["a.com"]}).status_code == 400
        assert KEY not in c.get("/api/task-secrets").text
        audit.record(config, "c9", {"tool": "shell", "what": "ls", "outcome": "ok"})
        assert c.get("/api/audit/c9").json()["entries"][0]["what"] == "ls"
        assert c.delete("/api/task-secrets/stripe").json() == {"ok": True}
        assert c.get("/api/task-secrets").json() == []
