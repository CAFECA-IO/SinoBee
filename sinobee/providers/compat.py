"""Model parameter compatibility, learned at run time.

New models drop parameters older ones accepted - Claude Opus 5.5 rejects `temperature` ("`temperature` is
deprecated for this model"), OpenAI reasoning models reject it too. Instead of a hand-kept list of models, the
provider layer learns it: a 400 that names a parameter as unsupported makes the call retry at once without it, and
the model is remembered in $SINOBEE_HOME/model_caps.json so later calls (after a restart too) never send it again.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("sinobee.providers")

# a parameter the request may leave out without changing what is asked; the model then uses its own default
OPTIONAL = ("temperature", "top_p", "top_k")
_REJECT = [
    r"[`'\"]?{p}[`'\"]?[^\n]{{0,80}}?\b(?:is )?(?:deprecated|not supported|unsupported|not allowed|not permitted|"
    r"cannot be (?:set|used)|is not available|has been removed|no longer supported)",
    r"(?:unsupported|unknown|unrecognized|unexpected|invalid|extra)[ _](?:parameter|argument|field|key|value)s?"
    r"[^\n]{{0,40}}?[`'\"]?{p}\b",
    r"does not support[^\n]{{0,40}}?[`'\"]?{p}\b",
    r"[`'\"]?{p}[`'\"]?: extra inputs are not permitted",
]
PATTERNS = {p: re.compile("|".join(x.format(p=re.escape(p)) for x in _REJECT), re.I) for p in OPTIONAL}

_lock = threading.Lock()
_cache: dict | None = None
_cache_path: Path | None = None


def path() -> Path:
    return Path(os.environ.get("SINOBEE_HOME", Path.home() / ".sinobee")).expanduser() / "model_caps.json"


def _load() -> dict:
    global _cache, _cache_path
    p = path()
    if _cache is None or _cache_path != p:
        try:
            _cache = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(_cache, dict):
                _cache = {}
        except (OSError, ValueError):
            _cache = {}
        _cache_path = p
    return _cache


def dropped(label: str) -> set[str]:
    """Parameters this model is known not to accept."""
    with _lock:
        return set((_load().get(label) or {}).get("drop") or [])


def rejected_param(err, sent: dict) -> str | None:
    """The optional parameter a provider error says the model does not accept (one that was sent), or None."""
    status = getattr(err, "status", None)
    if status not in (400, 422, None):
        return None
    text = str(err)
    if status is None and not re.search(r"\bHTTP 4(?:00|22)\b", text):
        return None
    for p in OPTIONAL:
        if sent.get(p) is not None and PATTERNS[p].search(text):
            return p
    return None


def learn(label: str, param: str, message: str) -> None:
    """Remember that `label` does not accept `param` (kept across restarts)."""
    with _lock:
        caps = _load()
        entry = caps.setdefault(label, {"drop": []})
        if param in entry["drop"]:
            return
        entry["drop"].append(param)
        entry.setdefault("learned", {})[param] = {"at": time.time(), "message": str(message)[:300]}
        try:
            p = path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(caps, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(p)
        except OSError:
            log.warning("could not save %s", path(), exc_info=True)
    log.warning("%s does not accept %s: retrying without it from now on (%s)", label, param, str(message)[:160])


def forget(label: str | None = None) -> None:
    """Clear what was learned (all models, or one) - e.g. after a provider fixed its API."""
    with _lock:
        caps = _load()
        if label is None:
            caps.clear()
        else:
            caps.pop(label, None)
        try:
            path().write_text(json.dumps(caps, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass


def note(label: str, param: str) -> None:
    """Tell the running turn (UI note) that a call was adapted."""
    try:
        from ..accounting import current_scope
        current_scope.get().compat_events.append({"model": label, "param": param})
    except Exception:  # noqa: BLE001
        pass
