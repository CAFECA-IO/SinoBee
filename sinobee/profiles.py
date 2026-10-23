"""One set of SinoBee data per SinoBee.ai account on this computer.

Signing out of SinoBee.ai used to delete only the sign-in token: the conversations, memory, workspace, folders given
to conversations, write-back records and model API keys all stayed in ~/.sinobee, so whoever signed in next on the
same macOS account saw and used them. Now each account has its own data folder ("profile"), and signed out you work
in a separate local one:

    ~/.sinobee/                       the launcher folder; also the first profile ("base"), kept where it was
    ~/.sinobee/profiles.json          {"active": key, "base_owner": owner, "profiles": {key: {"owner", "label"}}}
    ~/.sinobee/profiles/<key>/        any other profile: acct-<hash of the account> | local

- The data that was there before belongs to the first account that signs in (or is signed in when this version
  starts) - the person who has been using it.
- Signing in as another account switches to that account's profile (made empty the first time); the new sign-in
  (token, device keys) moves there. Signing out switches to the local profile.
- A switch restarts SinoBee in place (same command, same port and server token), and refuses while a task runs.

This separates accounts inside SinoBee; it is not encryption: anyone using the same macOS account can still open the
folders with Finder. Different people should use different macOS accounts (and FileVault).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

ACCOUNT_SECRETS = ("sinobee-ai:token", "sinobee-ai:refresh", "sinobee-ai:account", "sinobee-ai:signing-key",
                   "sinobee-ai:device-key")
CARRY_SETTINGS = ("server",)               # settings a new profile starts with (the rest is the account's own)


def base_dir() -> Path:
    return Path(os.environ.get("SINOBEE_HOME", Path.home() / ".sinobee")).expanduser()


def _state_path(base: Path) -> Path:
    return base / "profiles.json"


def load(base: Path | None = None) -> dict:
    base = base or base_dir()
    try:
        st = json.loads(_state_path(base).read_text(encoding="utf-8"))
        if not isinstance(st, dict):
            raise ValueError
    except (OSError, ValueError):
        st = {}
    st.setdefault("active", "base")
    st.setdefault("base_owner", None)
    st.setdefault("profiles", {})
    return st


def save(st: dict, base: Path | None = None) -> None:
    base = base or base_dir()
    base.mkdir(parents=True, exist_ok=True)
    tmp = _state_path(base).with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_state_path(base))


def dir_of(key: str, base: Path | None = None) -> Path:
    base = base or base_dir()
    return base if key == "base" else base / "profiles" / key


def active_home(base: Path | None = None) -> Path:
    """The data folder SinoBee uses now."""
    base = base or base_dir()
    st = load(base)
    key = st.get("active") or "base"
    d = dir_of(key, base)
    if key != "base" and not d.is_dir():
        return base
    return d


def owner_of(info: dict | None) -> str | None:
    """Who an account is, from the cached sign-in (sinobee-ai:account)."""
    if not info:
        return None
    acc = info.get("account")
    if isinstance(acc, dict):
        acc = acc.get("id") or acc.get("email") or acc.get("handle")
    acc = acc or info.get("handle") or info.get("publisher")
    return str(acc) if acc else None


def key_for(owner: str) -> str:
    return "local" if owner == "local" else "acct-" + hashlib.sha256(owner.encode()).hexdigest()[:12]


def _owner(st: dict, key: str) -> str | None:
    return st.get("base_owner") if key == "base" else (st["profiles"].get(key) or {}).get("owner")


def _find(st: dict, owner: str) -> str | None:
    if st.get("base_owner") == owner:
        return "base"
    return next((k for k, v in st["profiles"].items() if v.get("owner") == owner), None)


def current(config) -> dict:
    """{"key", "owner", "home"} of the profile in use."""
    base = base_dir()
    st = load(base)
    key = st.get("active") or "base"
    return {"key": key, "owner": _owner(st, key), "home": str(active_home(base)),
            "separated": bool(st.get("base_owner")) or bool(st["profiles"])}


def claim_if_unowned(config, info: dict | None) -> bool:
    """The data that was here before belongs to the first account seen signed in."""
    owner = owner_of(info)
    base = base_dir()
    st = load(base)
    key = st.get("active") or "base"
    if not owner or _owner(st, key) is not None:
        return False
    if key == "base":
        st["base_owner"] = owner
    else:
        st["profiles"].setdefault(key, {})["owner"] = owner
    st.setdefault("claimed_at", time.time())
    save(st, base)
    return True


def _prepare(config, key: str, owner: str, label: str) -> Path:
    """Make profile `key` ready: its folder, the settings carried over, the server token (so the open window keeps
    working after the restart)."""
    from .secrets import SecretStore
    target = dir_of(key)
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(target, 0o700)
    src_settings, dst_settings = Path(config.home) / "settings.json", target / "settings.json"
    try:
        theirs = json.loads(src_settings.read_text(encoding="utf-8")) if src_settings.is_file() else {}
        mine = json.loads(dst_settings.read_text(encoding="utf-8")) if dst_settings.is_file() else {}
        changed = False
        for k in CARRY_SETTINGS:
            if k in theirs and k not in mine:
                mine[k], changed = theirs[k], True
        if changed or not dst_settings.exists():
            dst_settings.write_text(json.dumps(mine, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass
    tok = config.server_token() if hasattr(config, "server_token") else None
    if tok:
        SecretStore(target / "secrets.json").set_secret("server_token", tok)
    return target


def switch_after_login(config, info: dict | None) -> dict:
    """After a sign-in finished in this profile: claim it, stay, or move the sign-in to the account's own profile.
    Returns {"switched": bool, "key", "claimed": bool}."""
    from .secrets import SecretStore
    owner = owner_of(info)
    if not owner:
        return {"switched": False, "claimed": False, "key": current(config)["key"]}
    if claim_if_unowned(config, info):
        return {"switched": False, "claimed": True, "key": current(config)["key"]}
    base = base_dir()
    st = load(base)
    cur = st.get("active") or "base"
    if _owner(st, cur) == owner:
        return {"switched": False, "claimed": False, "key": cur}
    target_key = _find(st, owner) or key_for(owner)
    label = (info or {}).get("handle") or owner
    target = _prepare(config, target_key, owner, label)
    dst = SecretStore(target / "secrets.json")
    for name in ACCOUNT_SECRETS:                           # the sign-in belongs to the account's profile
        v = config.secrets.get_secret(name)
        if v is not None:
            dst.set_secret(name, v)
        config.secrets.set_secret(name, None)
    if target_key != "base":
        st["profiles"].setdefault(target_key, {"created": time.time()}).update(owner=owner, label=label)
    st["active"] = target_key
    save(st, base)
    return {"switched": True, "claimed": False, "key": target_key}


def switch_after_logout(config) -> dict:
    """After signing out: work in the local profile, so the account's data is out of sight."""
    base = base_dir()
    st = load(base)
    cur = st.get("active") or "base"
    if _owner(st, cur) in (None, "local"):
        return {"switched": False, "key": cur}
    target_key = _find(st, "local") or "local"
    _prepare(config, target_key, "local", "本機")
    if target_key != "base":
        st["profiles"].setdefault(target_key, {"created": time.time()}).update(owner="local", label="本機")
    st["active"] = target_key
    save(st, base)
    return {"switched": True, "key": target_key}


def restart_process(delay: float = 0.8) -> None:
    """Start SinoBee again in place (same command line), after `delay` seconds - the HTTP answer goes out first."""
    if os.environ.get("SINOBEE_NO_RESTART") == "1":
        return
    import threading

    def go():
        time.sleep(delay)
        argv = list(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(sys.executable, [sys.executable, *argv[1:]])
    threading.Thread(target=go, daemon=True).start()
