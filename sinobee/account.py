"""Sign in to sinobee.ai (`sinobee login`) with the OAuth 2.0 device authorization grant (RFC 8628).

The access token is stored only in secrets.json (`sinobee-ai:token`, mode 0600) and never shown in full,
logged or sent to a model. Account details (`sinobee-ai:account`) are cached next to it for the UI.
"""
from __future__ import annotations

import json
import platform
import socket
import time

import httpx

from . import __version__

TOKEN = "sinobee-ai:token"
DEVICE_KEY = "sinobee-ai:device-key"   # this install's X25519 private key (base64), for its copy of the vault key
ACCOUNT = "sinobee-ai:account"
SCOPES = "skills:publish tools:write vault:rw shares:write llm"
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


def start(config) -> dict:
    """Step 1: request a device code. Returns the RFC 8628 response (device_code stays local)."""
    r = _post(f"{base_url(config)}/api/v1/device/code",
              {"client_id": "sinobee", "device_name": device_name(),
               "platform": f"{platform.system()} {platform.machine()}".strip(), "scope": SCOPES,
               "public_key": device_public_key(config)})
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
    info = {k: body.get(k) for k in ("account", "handle", "publisher", "device_id")}
    info["scopes"] = (body.get("scope") or "").split()
    info["signed_in_at"] = time.time()
    info["expires_at"] = time.time() + int(body.get("expires_in") or 0) if body.get("expires_in") else None
    config.secrets.set_secret(ACCOUNT, json.dumps(info))


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
        r = httpx.get(f"{base_url(config)}/api/v1/me", headers={**UA, "Authorization": f"Bearer {tok}"}, timeout=15)
    except httpx.HTTPError:
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
    config.secrets.set_secret(ACCOUNT, None)


def auth_headers(config, scope: str | None = None) -> dict:
    """Authorization header for sinobee.ai calls; raises when not signed in or the token lacks `scope`."""
    tok = token(config)
    if not tok:
        raise AccountError("需要先登入 sinobee.ai（執行 sinobee login，或在設定 → SinoBee.ai 帳號登入）")
    acc = account(config) or {}
    if scope and acc.get("scopes") and scope not in acc["scopes"]:
        raise AccountError(f"登入權杖沒有 {scope} 權限，請重新登入")
    return {**UA, "Authorization": f"Bearer {tok}"}
