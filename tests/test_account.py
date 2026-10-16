"""sinobee.ai device-code sign-in client (RFC 8628) against a fake hub."""
import json

import httpx
import pytest

from sinobee import account


class FakeHub:
    def __init__(self):
        self.polls = 0
        self.approve_after = 2
        self.deny = False
        self.revoked = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/device/code":
            body = json.loads(request.content)
            assert body["client_id"] == "sinobee" and "tools:write" in body["scope"]
            return httpx.Response(200, json={"device_code": "d" * 43, "user_code": "ABCD-EFGH",
                                             "verification_uri": "https://hub/device",
                                             "verification_uri_complete": "https://hub/device?code=ABCD-EFGH",
                                             "expires_in": 900, "interval": 0})
        if path == "/api/v1/device/token":
            self.polls += 1
            if self.deny:
                return httpx.Response(400, json={"error": "access_denied"})
            if self.polls < self.approve_after:
                return httpx.Response(400, json={"error": "authorization_pending"})
            return httpx.Response(200, json={"access_token": "sbh_" + "x" * 40, "token_type": "Bearer",
                                             "scope": "skills:publish tools:write", "expires_in": 3600,
                                             "account": "0xabc", "handle": "alice", "publisher": "alice",
                                             "device_id": "dev1"})
        if path == "/api/v1/me":
            if self.revoked or request.headers.get("authorization") != "Bearer sbh_" + "x" * 40:
                return httpx.Response(401, json={"error": "invalid"})
            return httpx.Response(200, json={"account": "0xabc", "handle": "alice2", "publisher": "alice",
                                             "scopes": ["skills:publish", "tools:write"]})
        return httpx.Response(404)


@pytest.fixture
def hub(monkeypatch):
    h = FakeHub()
    transport = httpx.MockTransport(h.handler)
    real = httpx.Client

    def post(url, **kw):
        kw.pop("timeout", None)
        with real(transport=transport) as c:
            return c.post(url, **kw)

    def get(url, **kw):
        kw.pop("timeout", None)
        with real(transport=transport) as c:
            return c.get(url, **kw)
    def request(method, url, **kw):
        kw.pop("timeout", None)
        with real(transport=transport) as c:
            return c.request(method, url, **kw)
    monkeypatch.setattr(account.httpx, "post", post)
    monkeypatch.setattr(account.httpx, "get", get)
    monkeypatch.setattr(account.httpx, "request", request)
    monkeypatch.setattr(account.time, "sleep", lambda s: None)
    return h


def test_login_flow_stores_token_secretly(config, hub):
    s = account.start(config)
    assert s["user_code"] == "ABCD-EFGH"
    res = account.wait(config, s)
    assert res["status"] == "ok" and hub.polls == 2
    assert account.token(config).startswith("sbh_")
    acc = account.account(config)
    assert acc["handle"] == "alice" and acc["scopes"] == ["skills:publish", "tools:write"]
    # token lives only in secrets.json (0600), not in config.yaml
    assert "sbh_" not in json.dumps(config.data)
    assert account.auth_headers(config, "tools:write")["Authorization"].startswith("Bearer sbh_")
    with pytest.raises(account.AccountError, match="vault:rw"):
        account.auth_headers(config, "vault:rw")
    # hub client picks up the device token for publishing
    from sinobee.skills.hub import hub_client
    assert hub_client(config).token == account.token(config)


def test_whoami_refreshes_and_clears_revoked(config, hub):
    account.wait(config, account.start(config))
    assert account.whoami(config)["handle"] == "alice2"
    hub.revoked = True
    assert account.whoami(config) is None
    assert account.token(config) is None


def test_denied(config, hub):
    hub.deny = True
    assert account.wait(config, account.start(config))["status"] == "denied"
    assert account.token(config) is None
    with pytest.raises(account.AccountError, match="sinobee login"):
        account.auth_headers(config)


def test_web_api_login(config, hub):
    from fastapi.testclient import TestClient
    from sinobee.agent import Runtime
    from sinobee.server import create_app
    with TestClient(create_app(Runtime(config))) as c:
        assert c.get("/api/account").json()["signed_in"] is False
        lg = c.post("/api/account/login").json()
        assert lg["user_code"] == "ABCD-EFGH" and "device_code" not in lg
        import time
        for _ in range(50):
            st = c.get("/api/account").json()
            if st["signed_in"]:
                break
            time.sleep(0.05)
        assert st["signed_in"] and st["account"]["handle"] == "alice"
        assert "sbh_" not in json.dumps(st)
        assert c.post("/api/account/logout").json()["ok"]
        assert c.get("/api/account").json()["signed_in"] is False


def test_signed_requests_and_refresh(config, monkeypatch):
    """P5: the install signs every request with its Ed25519 key and refreshes its 15-minute token in time."""
    import base64
    import hashlib
    from urllib.parse import urlsplit
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    pub = base64.b64decode(account.signing_public_key(config))
    assert len(pub) == 32 and account.signing_public_key(config) == base64.b64encode(pub).decode(), "stable key"
    verify_key = Ed25519PublicKey.from_public_bytes(pub)
    seen = []

    def check(req: httpx.Request):
        u = urlsplit(str(req.url))
        body = req.read()
        msg = "\n".join([req.method, u.path + (f"?{u.query}" if u.query else ""), req.headers["x-sb-timestamp"],
                         req.headers["x-sb-nonce"], hashlib.sha256(body).hexdigest()])
        verify_key.verify(base64.b64decode(req.headers["x-sb-signature"]), msg.encode())  # raises if wrong
        seen.append((req.method, u.path, req.headers.get("authorization"), body))

    def handler(req: httpx.Request) -> httpx.Response:
        check(req)
        if req.url.path == "/api/v1/device/token":
            assert json.loads(req.content) == {"grant_type": "refresh_token", "refresh_token": "sbr_" + "r" * 40}
            return httpx.Response(200, json={"access_token": "sbh_" + "n" * 40, "refresh_token": "sbr_" + "s" * 40, "expires_in": 900})
        return httpx.Response(200, json={"ok": True})

    real = httpx.Client

    def call(method, url, **kw):
        kw.pop("timeout", None)
        with real(transport=httpx.MockTransport(handler)) as c:
            return c.request(method, url, **kw)
    monkeypatch.setattr(account.httpx, "request", call)
    monkeypatch.setattr(account.httpx, "post", lambda url, **kw: call("POST", url, **kw))

    account.save(config, {"access_token": "sbh_" + "a" * 40, "refresh_token": "sbr_" + "r" * 40, "expires_in": 900, "scope": "llm vault:rw"})
    r = account.send(config, "POST", "https://sinobee.ai/api/v1/vault/key?x=1", scope="vault:rw", json={"wrapped": "abc"})
    assert r.status_code == 200 and seen[-1][2] == "Bearer sbh_" + "a" * 40
    # about to expire → refreshed (signed) before the call, and both tokens rotate
    info = json.loads(config.secrets.get_secret(account.ACCOUNT))
    info["expires_at"] = 0
    config.secrets.set_secret(account.ACCOUNT, json.dumps(info))
    account.send(config, "GET", "https://sinobee.ai/api/v1/me")
    assert [s[1] for s in seen[-2:]] == ["/api/v1/device/token", "/api/v1/me"]
    assert seen[-1][2] == "Bearer sbh_" + "n" * 40
    assert config.secrets.get_secret(account.REFRESH) == "sbr_" + "s" * 40
    # multipart bodies are signed over the exact bytes sent
    account.send(config, "POST", "https://sinobee.ai/api/v1/shares", files={"files": ("a.txt", b"hello")}, data={"share": "{}"})
    assert b"hello" in seen[-1][3]
