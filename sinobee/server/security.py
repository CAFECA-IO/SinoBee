"""Who may use this SinoBee: an access token always, the right Host, and requests from SinoBee's own page.

The 2026-10-24 security review found the local web UI open to any web page (ClawJacked): no token on 127.0.0.1, no
Origin check on /ws, no Host check (DNS rebinding). Now:

- **A token always exists** (`ensure_token`), also on 127.0.0.1 and inside Docker. The page gets it as an HttpOnly,
  SameSite=Strict cookie (`sb_session`): opening `/` from this computer sets it; from another device, open the URL
  with `?token=` once (SinoBee sets the cookie and drops the token from the address) or paste it on the sign-in card.
  Another site's page never sends a SameSite=Strict cookie, and cannot read it.
- **Host**: `localhost`, `127.0.0.1`, `::1`, any IP address, this computer's name (`name`, `name.local`) and
  `server.allowed_hosts`. DNS rebinding always uses a domain name of the attacker's, so it is refused.
- **Origin**: a browser request that changes something (not GET/HEAD) and every WebSocket must come from the page's own
  origin (or `server.allowed_origins`); `null` (a sandboxed or file page) never.

`SINOBEE_INSECURE_NO_AUTH=1` turns all of it off - only the test suite sets it (an environment variable, so nothing
SinoBee or a task writes to its settings can).
"""
from __future__ import annotations

import ipaddress
import os
import secrets as _secrets
import socket
from urllib.parse import urlsplit

COOKIE = "sb_session"
LOCAL_NAMES = {"localhost", "127.0.0.1", "::1", "[::1]"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def disabled() -> bool:
    return os.environ.get("SINOBEE_INSECURE_NO_AUTH") == "1"


def ensure_token(config) -> str:
    """The access token: SINOBEE_SERVER_TOKEN / server.token, else the saved one, else a new one (saved)."""
    tok = config.server_token()
    if not tok:
        tok = _secrets.token_urlsafe(24)
        config.secrets.set_secret("server_token", tok)
    return tok


def token_ok(config, token: str | None) -> bool:
    return bool(token) and _secrets.compare_digest(str(token), str(ensure_token(config)))


def _hostname(host_header: str) -> str:
    h = (host_header or "").strip().lower()
    if h.startswith("["):                                   # [::1]:10024
        return h[1:h.index("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def _is_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def _own_names() -> set[str]:
    try:
        n = socket.gethostname().lower()
    except OSError:
        return set()
    base = n.split(".")[0]
    return {n, base, f"{base}.local"}


def host_ok(config, host_header: str) -> bool:
    name = _hostname(host_header)
    if not name:
        return False
    if name in LOCAL_NAMES or _is_ip(name) or name in _own_names():
        return True
    allowed = [str(x).lower() for x in (config.get("server.allowed_hosts") or [])]
    return name in allowed or any(a.startswith("*.") and name.endswith(a[1:]) for a in allowed)


def origin_ok(config, origin: str | None, host_header: str) -> bool:
    """No Origin (not a browser, or a same-origin navigation) is fine; otherwise it must be the page's own."""
    if origin is None:
        return True
    origin = origin.strip()
    if not origin or origin == "null":
        return False
    netloc = urlsplit(origin).netloc.lower()
    if netloc and netloc == (host_header or "").strip().lower():
        return True
    allowed = [str(x).rstrip("/").lower() for x in (config.get("server.allowed_origins") or [])]
    return origin.rstrip("/").lower() in allowed


def is_loopback_client(client_host: str | None) -> bool:
    try:
        return ipaddress.ip_address(client_host or "").is_loopback
    except ValueError:
        return False


def request_token(headers, query, cookies) -> str | None:
    return headers.get("x-sinobee-token") or (query.get("token") or None) or cookies.get(COOKIE)


def check(config, *, method: str, headers, query, cookies, websocket: bool = False) -> tuple[int, str] | None:
    """None when allowed, else (status, reason)."""
    if disabled():
        return None
    host = headers.get("host", "")
    if not host_ok(config, host):
        return 421, "unknown host name (add it to server.allowed_hosts)"
    if (websocket or method.upper() not in SAFE_METHODS) and not origin_ok(config, headers.get("origin"), host):
        return 403, "request from another site"
    if not token_ok(config, request_token(headers, query, cookies)):
        return 401, "invalid token"
    return None


def set_cookie(response, config, *, https: bool = False) -> None:
    response.set_cookie(COOKIE, ensure_token(config), httponly=True, samesite="strict", secure=https, path="/",
                        max_age=400 * 86400)
