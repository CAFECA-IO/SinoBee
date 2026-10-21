"""Sign in to sinobee.ai (`sinobee login`) with the OAuth 2.0 device authorization grant (RFC 8628).

The access token is stored only in secrets.json (`sinobee-ai:token`, mode 0600) and never shown in full,
logged or sent to a model. Account details (`sinobee-ai:account`) are cached next to it for the UI.

Request signing (sinobee.ai P5): this install keeps an Ed25519 key (`sinobee-ai:signing-key`) and registers its
public half when signing in. sinobee.ai then issues 15-minute access tokens plus a refresh token bound to the
device, and every request carries X-SB-Timestamp / X-SB-Nonce / X-SB-Signature over
METHOD, path+query, timestamp, nonce and sha256(body). A copied token is useless without the key.
Use `send()` for every call to sinobee.ai so requests are signed and the access token is refreshed in time.
"""
from __future__ import annotations

import json
import platform
import re
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx

from . import __version__

TOKEN = "sinobee-ai:token"
DEVICE_KEY = "sinobee-ai:device-key"   # this install's X25519 private key (base64), for its copy of the vault key
ACCOUNT = "sinobee-ai:account"
SIGNING_KEY = "sinobee-ai:signing-key"  # Ed25519 private key (base64 raw) that signs requests to sinobee.ai
REFRESH = "sinobee-ai:refresh"           # device-bound refresh token (only usable with a signed request)
SCOPES = "skills:publish tools:write vault:rw shares:write llm evals:write"
UA = {"User-Agent": f"sinobee/{__version__}"}


class AccountError(RuntimeError):
    pass


def base_url(config) -> str:
    return str(config.get("skills.hub_url") or "https://sinobee.ai").rstrip("/")


def device_name() -> str:
    try:
        return f"SinoBee on {socket.gethostname()}"[:80]
    except OSError:
        return "SinoBee"


def _post(url: str, data: dict, timeout: float = 20) -> httpx.Response:
    try:
        return httpx.post(url, json=data, headers=UA, timeout=timeout)
    except httpx.HTTPError as e:
        raise AccountError(f"無法連線到 {url.split('/api/')[0]}：{e}") from None


def device_public_key(config) -> str:
    """This install's X25519 public key (created on first use, kept in secrets.json)."""
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    raw = config.secrets.get_secret(DEVICE_KEY)
    if raw:
        sk = X25519PrivateKey.from_private_bytes(base64.b64decode(raw))
    else:
        sk = X25519PrivateKey.generate()
        config.secrets.set_secret(DEVICE_KEY, base64.b64encode(sk.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode())
    return base64.b64encode(sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def device_private_key(config):
    import base64
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    raw = config.secrets.get_secret(DEVICE_KEY)
    return X25519PrivateKey.from_private_bytes(base64.b64decode(raw)) if raw else None


def signing_public_key(config) -> str:
    """This install's Ed25519 public key (created on first use, kept in secrets.json)."""
    import base64
    from cryptography.hazmat.primitives import serialization
    sk = _signing_key(config, create=True)
    return base64.b64encode(sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def _signing_key(config, create: bool = False):
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    raw = config.secrets.get_secret(SIGNING_KEY)
    if raw:
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw))
    if not create:
        return None
    sk = Ed25519PrivateKey.generate()
    config.secrets.set_secret(SIGNING_KEY, base64.b64encode(sk.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode())
    return sk


def sign_headers(config, method: str, url: str, body: bytes = b"") -> dict:
    """X-SB-* headers for one request (empty when this install has no signing key)."""
    import base64
    import hashlib
    import secrets as _secrets
    from urllib.parse import urlsplit
    sk = _signing_key(config)
    if sk is None:
        return {}
    u = urlsplit(url)
    path = u.path + (f"?{u.query}" if u.query else "")
    ts = str(int(time.time()))
    nonce = _secrets.token_urlsafe(18)
    msg = f"{method.upper()}\n{path}\n{ts}\n{nonce}\n{hashlib.sha256(body or b'').hexdigest()}"
    return {"X-SB-Timestamp": ts, "X-SB-Nonce": nonce,
            "X-SB-Signature": base64.b64encode(sk.sign(msg.encode())).decode()}


def start(config) -> dict:
    """Step 1: request a device code. Returns the RFC 8628 response (device_code stays local)."""
    def ask(scope: str):
        return _post(f"{base_url(config)}/api/v1/device/code",
                     {"client_id": "sinobee", "device_name": device_name(),
                      "platform": f"{platform.system()} {platform.machine()}".strip(), "scope": scope,
                      "public_key": device_public_key(config), "signing_key": signing_public_key(config)})
    r = ask(SCOPES)
    if r.status_code == 400 and "invalid_scope" in r.text:
        # a sinobee.ai that does not know a newer scope yet (e.g. evals:write): sign in without the unknown ones
        bad = set(re.findall(r"[a-z]+:[a-z]+|\b[a-z]+\b", r.text.split("invalid_scope", 1)[1])[:10])
        r = ask(" ".join(x for x in SCOPES.split() if x not in bad))
    if r.status_code == 429:
        raise AccountError("登入要求太頻繁，請幾分鐘後再試")
    if r.status_code >= 400 or "json" not in r.headers.get("content-type", ""):
        raise AccountError(f"sinobee.ai 無法開始登入（HTTP {r.status_code}）")
    return r.json()


def qr_svg(url: str) -> str | None:
    """QR code of the verification link (for Docker / phones). None when segno is missing."""
    try:
        import segno
    except ImportError:
        return None
    import io
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=4, border=2, dark="#04201a", light="#ffffff", xmldecl=False,
                                     svgns=True, nl=False)
    return buf.getvalue().decode()


def qr_terminal(url: str) -> None:
    try:
        import segno
        segno.make(url, error="l").terminal(compact=True)
    except Exception:  # noqa: BLE001 - optional
        pass


def poll_once(config, device_code: str) -> dict:
    """Step 3 (one poll). Returns {"status": pending|slow_down|denied|expired|ok, ...}; stores the token on ok."""
    r = _post(f"{base_url(config)}/api/v1/device/token",
              {"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": device_code})
    try:
        body = r.json()
    except ValueError:
        raise AccountError(f"sinobee.ai 回應格式錯誤（HTTP {r.status_code}）") from None
    if r.status_code == 200 and body.get("access_token"):
        save(config, body)
        return {"status": "ok", "account": account(config)}
    err = body.get("error", "")
    return {"status": {"authorization_pending": "pending", "slow_down": "slow_down", "access_denied": "denied",
                       "expired_token": "expired"}.get(err, "error"), "error": err}


def wait(config, start_resp: dict, on_tick=None) -> dict:
    """Poll until approved/denied/expired (CLI)."""
    interval = int(start_resp.get("interval", 5))
    deadline = time.time() + int(start_resp.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        res = poll_once(config, start_resp["device_code"])
        if res["status"] == "slow_down":
            interval += 5
        elif res["status"] != "pending":
            return res
        if on_tick:
            on_tick()
    return {"status": "expired"}


def save(config, body: dict) -> None:
    config.secrets.set_secret(TOKEN, body["access_token"])
    config.secrets.set_secret(REFRESH, body.get("refresh_token"))
    info = {k: body.get(k) for k in ("account", "handle", "publisher", "device_id")}
    info["scopes"] = (body.get("scope") or "").split()
    info["signed_in_at"] = time.time()
    info["expires_at"] = time.time() + int(body.get("expires_in") or 0) if body.get("expires_in") else None
    info["signed"] = bool(body.get("refresh_token"))
    config.secrets.set_secret(ACCOUNT, json.dumps(info))


def _update_tokens(config, body: dict) -> None:
    """Store a refreshed token pair, keeping the cached account details."""
    config.secrets.set_secret(TOKEN, body["access_token"])
    if body.get("refresh_token"):
        config.secrets.set_secret(REFRESH, body["refresh_token"])
    info = account(config) or {}
    info["expires_at"] = time.time() + int(body.get("expires_in") or 900)
    config.secrets.set_secret(ACCOUNT, json.dumps(info))


_REFRESH_LOCK = threading.Lock()


@contextmanager
def _refresh_guard(config):
    """One refresh at a time - across threads and across SinoBee processes (server, CLI). A refresh token is single
    use: sinobee.ai treats a second use as theft and signs every session of the device out, so two calls that both
    saw an expiring token must not both spend it."""
    with _REFRESH_LOCK:
        fh = None
        try:
            import fcntl
            path = Path(config.home) / ".refresh.lock"
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(path, "a+")
            fcntl.flock(fh, fcntl.LOCK_EX)
        except (ImportError, OSError):
            fh = None                      # Windows / read-only home: the thread lock still covers this process
        try:
            yield
        finally:
            if fh is not None:
                try:
                    import fcntl
                    fcntl.flock(fh, fcntl.LOCK_UN)
                finally:
                    fh.close()


def refresh(config, *, force: bool = False) -> bool:
    """Swap the refresh token for a new access + refresh token (signed). False when signed out / not possible.
    Serialised (_refresh_guard); a caller that waited finds the token already renewed and does not spend the new
    refresh token again."""
    before = config.secrets.get_secret(REFRESH)
    with _refresh_guard(config):
        rt = config.secrets.get_secret(REFRESH)
        if rt and rt != before:
            return True                    # another thread / process refreshed while this one waited
        acc = account(config) or {}
        if not force and acc.get("expires_at") is not None and acc["expires_at"] - time.time() >= 60:
            return True                    # still fresh (renewed by someone else, the pair read before it)
        return _refresh_locked(config, rt)


def _refresh_locked(config, rt: str | None) -> bool:
    if not rt or _signing_key(config) is None:
        return False
    url = f"{base_url(config)}/api/v1/device/token"
    body = json.dumps({"grant_type": "refresh_token", "refresh_token": rt}, separators=(",", ":")).encode()
    try:
        r = httpx.post(url, content=body, timeout=20,
                       headers={**UA, "Content-Type": "application/json", **sign_headers(config, "POST", url, body)})
    except httpx.HTTPError:
        return False
    if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
        _update_tokens(config, r.json())
        return True
    if r.status_code in (400, 401):
        logout(config)  # revoked, expired or reused: sign in again
    return False


def _fresh_token(config) -> str | None:
    acc = account(config) or {}
    if acc.get("signed") and acc.get("expires_at") is not None and acc["expires_at"] - time.time() < 60:
        refresh(config)
    return token(config)


def token(config) -> str | None:
    return config.secrets.get_secret(TOKEN)


def account(config) -> dict | None:
    raw = config.secrets.get_secret(ACCOUNT)
    if not raw or not token(config):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def whoami(config, remote: bool = True) -> dict | None:
    """Cached account; with remote=True, confirm with /api/v1/me (clears a revoked token)."""
    tok = token(config)
    if not tok:
        return None
    if not remote:
        return account(config)
    try:
        r = send(config, "GET", f"{base_url(config)}/api/v1/me", timeout=15)
    except (httpx.HTTPError, AccountError):
        return {**(account(config) or {}), "offline": True}
    if r.status_code == 401 and refresh(config, force=True):
        # an access token that expired while this computer slept: renew it before deciding the sign-in is gone
        try:
            r = send(config, "GET", f"{base_url(config)}/api/v1/me", timeout=15)
        except (httpx.HTTPError, AccountError):
            return {**(account(config) or {}), "offline": True}
    if r.status_code == 401:
        logout(config)
        return None
    if r.status_code >= 400:
        return {**(account(config) or {}), "offline": True}
    me = r.json()
    cached = account(config) or {}
    cached.update({k: me.get(k) for k in ("account", "handle", "publisher")}, scopes=me.get("scopes") or [])
    config.secrets.set_secret(ACCOUNT, json.dumps(cached))
    return cached


def logout(config) -> None:
    config.secrets.set_secret(TOKEN, None)
    config.secrets.set_secret(REFRESH, None)
    config.secrets.set_secret(ACCOUNT, None)


def auth_headers(config, scope: str | None = None) -> dict:
    """Authorization header for sinobee.ai calls; raises when not signed in or the token lacks `scope`.
    Signed installs need per-request signatures: use send() instead."""
    tok = _fresh_token(config)
    if not tok:
        raise AccountError("需要先登入 sinobee.ai（執行 sinobee login，或在設定 → SinoBee.ai 帳號登入）")
    acc = account(config) or {}
    if scope and acc.get("scopes") and scope not in acc["scopes"]:
        raise AccountError(f"登入權杖沒有 {scope} 權限，請重新登入")
    return {**UA, "Authorization": f"Bearer {tok}"}


def send(config, method: str, url: str, scope: str | None = None, timeout: float = 60, **kw) -> httpx.Response:
    """One authenticated request to sinobee.ai: fresh access token, Authorization and (when this install signs)
    the X-SB-* signature over the exact bytes sent. Accepts httpx keyword arguments (json, content, files, data,
    params, headers)."""
    headers = {**auth_headers(config, scope), **(kw.pop("headers", None) or {})}
    req = httpx.Request(method, url, headers=headers, **kw)  # encode once, so we sign the exact bytes
    body = req.read()
    out = {k: v for k, v in req.headers.items() if k.lower() not in ("content-length", "host")}
    out.update(sign_headers(config, method, str(req.url), body))
    return httpx.request(method, str(req.url), headers=out, content=body, timeout=timeout)


# ───────────── SinoBee.ai subscription models (relay) ─────────────

def relay_base(config) -> str:
    """OpenAI-format relay root; the Anthropic format lives under /anthropic (…/anthropic/v1/messages)."""
    return f"{base_url(config)}/api/v1/llm"


def can_relay(config) -> bool:
    """Signed in with the llm scope (older sign-ins lack it: sign in again)."""
    acc = account(config)
    return bool(acc) and "llm" in (acc.get("scopes") or [])


def relay_headers(config, method: str, url: str, body: bytes = b"") -> dict:
    """Authorization plus the request signature for one relay call (used by the `sinobee` provider)."""
    if not token(config):
        raise AccountError("需要先登入 sinobee.ai 才能使用訂閱模型（設定 → SinoBee.ai 帳號，或執行 sinobee login）")
    headers = auth_headers(config, "llm")
    headers.update(sign_headers(config, method, url, body))
    return headers


def quota(config) -> dict:
    """Subscription status and the 6 h / 7 day rings from sinobee.ai (GET /api/v1/llm/quota).
    Returns {"status": ok|signed_out|no_scope|no_subscription|offline|error, ...}."""
    if not token(config):
        return {"status": "signed_out"}
    if not can_relay(config):
        return {"status": "no_scope"}
    try:
        r = send(config, "GET", f"{relay_base(config)}/quota", scope="llm", timeout=15)
    except (httpx.HTTPError, AccountError) as e:
        return {"status": "offline", "error": str(e)[:300]}
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 200:
        return {"status": "ok", **body}
    err = (body.get("error") or {}) if isinstance(body, dict) else {}
    if r.status_code == 402 or err.get("code") == "subscription" or err.get("quota") == "subscription":
        out = {"status": "no_subscription", "error": err.get("message"), "subscribe_url": f"{base_url(config)}/subscribe"}
        if isinstance(body.get("points"), dict):        # points can pay without a subscription
            out["points"] = body["points"]
        return out
    if r.status_code == 401:
        return {"status": "signed_out", "error": err.get("message")}
    return {"status": "error", "error": err.get("message") or f"HTTP {r.status_code}"}
