"""The local web UI is not open to other web pages (security review 2026-10-24, ClawJacked / DNS rebinding):
a token always, given to the page as an HttpOnly SameSite=Strict cookie; Host and Origin checked."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sinobee.agent import Runtime
from sinobee.config import Config
from sinobee.server import create_app, security


@pytest.fixture
def secured(home, provider, monkeypatch):
    monkeypatch.delenv("SINOBEE_INSECURE_NO_AUTH", raising=False)
    monkeypatch.delenv("SINOBEE_SERVER_TOKEN", raising=False)
    cfg = Config.load()
    app = create_app(Runtime(cfg, provider=provider))
    return app, cfg


def local(app, **kw):
    return TestClient(app, base_url="http://localhost:10024", client=("127.0.0.1", 50000), **kw)


def test_this_computer_gets_the_cookie_by_opening_the_page(secured):
    app, cfg = secured
    c = local(app)
    assert c.get("/api/status").status_code == 401                      # no token yet: nothing
    r = c.get("/")
    assert r.status_code == 200
    sc = r.headers["set-cookie"]
    assert "sb_session=" in sc and "HttpOnly" in sc and "SameSite=strict" in sc.replace("Strict", "strict")
    assert c.get("/api/status").status_code == 200                      # the cookie travels with the page's requests
    tok = security.ensure_token(cfg)
    assert tok and tok not in r.text                                    # the page itself never holds it


def test_another_device_needs_the_token_once(secured):
    app, cfg = secured
    lan = TestClient(app, base_url="http://192.168.1.20:10024", client=("192.168.1.33", 50000),
                     follow_redirects=False)
    r = lan.get("/")
    assert "set-cookie" not in r.headers                                # not from this computer: no cookie for free
    assert lan.get("/api/status").status_code == 401
    r = lan.get("/?token=wrong")
    assert r.status_code == 303 and "set-cookie" not in r.headers
    r = lan.get("/?token=" + security.ensure_token(cfg))
    assert r.status_code == 303 and r.headers["location"] == "/" and "sb_session=" in r.headers["set-cookie"]
    assert lan.get("/api/status").status_code == 200
    other = TestClient(app, base_url="http://192.168.1.20:10024", client=("192.168.1.34", 50000))
    assert other.post("/auth/login", json={"token": "nope"}).status_code == 401
    assert other.post("/auth/login", json={"token": security.ensure_token(cfg)}).status_code == 200
    assert other.get("/api/status").status_code == 200


def test_dns_rebinding_and_other_sites_are_refused(secured):
    app, cfg = secured
    c = local(app)
    c.get("/")
    # a rebinding attack reaches 127.0.0.1 under the attacker's name
    assert c.get("/api/settings", headers={"host": "evil.example:10024"}).status_code == 421
    assert c.get("/", headers={"host": "rebind.attacker.test"}).status_code == 421
    # a page on another site posting to the API, even if a cookie were sent
    r = c.post("/api/sandbox/resources", json={"max_running": 1}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert c.put("/api/sandbox/resources", json={"max_running": 1}, headers={"origin": "null"}).status_code == 403
    # names you allow (a tunnel, Tailscale) work
    cfg.data.setdefault("server", {})["allowed_hosts"] = ["*.ts.net"]
    assert c.get("/api/status", headers={"host": "mac.tail1234.ts.net"}).status_code == 200


def test_the_websocket_checks_origin_and_token(secured):
    app, cfg = secured
    c = local(app)
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/ws", headers={"origin": "https://evil.example", "host": "localhost:10024"}) as ws:
            ws.receive_json()
    assert e.value.code == 4403
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/ws", headers={"origin": "http://localhost:10024", "host": "localhost:10024"}) as ws:
            ws.receive_json()
    assert e.value.code == 4401                                          # same site, but no token
    cookie = c.get("/").headers["set-cookie"].split(";")[0]               # what the browser keeps and sends
    with c.websocket_connect("/ws", headers={"origin": "http://localhost:10024", "host": "localhost:10024",
                                             "cookie": cookie}) as ws:
        assert ws.receive_json()["type"] == "hello"


def test_static_files_and_capability_pages_stay_reachable(secured):
    app, _ = secured
    c = TestClient(app, base_url="http://192.168.1.20:10024", client=("192.168.1.33", 50000))
    assert c.get("/favicon.ico").status_code == 200
    assert c.get("/preview/unknown/").status_code == 404                 # a capability link, not the token


def test_host_and_origin_rules():
    cfg = type("C", (), {"get": lambda self, k, d=None: d})()
    assert security.host_ok(cfg, "localhost:10024") and security.host_ok(cfg, "127.0.0.1")
    assert security.host_ok(cfg, "[::1]:10024") and security.host_ok(cfg, "10.0.0.5:10024")
    assert not security.host_ok(cfg, "localhost.evil.example") and not security.host_ok(cfg, "")
    assert security.origin_ok(cfg, None, "localhost:1")
    assert security.origin_ok(cfg, "http://localhost:1", "localhost:1")
    assert not security.origin_ok(cfg, "http://localhost:2", "localhost:1")
    assert not security.origin_ok(cfg, "null", "localhost:1")


def test_the_docker_desktop_goes_through_sinobee_with_the_token(secured, monkeypatch):
    """No more websockify on :6080 without a password: the page's noVNC connects to /api/desktop/vnc."""
    import socket
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    monkeypatch.setenv("SINOBEE_VNC_PORT", str(srv.getsockname()[1]))

    def serve():
        conn, _ = srv.accept()
        conn.sendall(b"RFB 003.008\n")
        conn.recv(10)
        conn.close()
    threading.Thread(target=serve, daemon=True).start()
    app, _ = secured
    c = local(app)
    hdr = {"origin": "http://localhost:10024", "host": "localhost:10024"}
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/api/desktop/vnc", headers=hdr) as ws:
            ws.receive_bytes()
    assert e.value.code == 4401
    hdr["cookie"] = c.get("/").headers["set-cookie"].split(";")[0]
    with c.websocket_connect("/api/desktop/vnc", headers=hdr) as ws:
        assert ws.receive_bytes() == b"RFB 003.008\n"
        ws.send_bytes(b"RFB 003.008\n")
    srv.close()
