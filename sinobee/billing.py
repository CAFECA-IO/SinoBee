"""Who pays for model calls when the SinoBee.ai subscription allowance runs out (plan "模型分配與備援機制", P2,
decided 2026-10-21).

Order: subscription -> platform points -> the user's own API keys and local models (one pool). Each step down
needs the user's consent:

- subscription used up: the turn stops at a safe point and the GUI asks - continue with points, continue with your
  own keys / local models, or pause until the allowance is back. `billing.auto_points` (off by default) spends
  points without asking; `billing.on_exhausted` = fallback goes straight to your own keys (both are also set by
  "do this next time" in the dialog); pause / ask stop the turn. Nothing waits for an answer forever: an
  unattended conversation turn is resumed and a scheduled task is run again by the recovery check once the allowance
  is back (work/recovery.py). Your own keys are never charged for an unattended task unless on_exhausted = fallback.
- points used up: asked again (your own keys / local, or pause), unless `on_exhausted` = fallback.
- the allowance is back: straight back to the subscription, no question.

The temporary choice lives in <home>/billing.json: {"source": "points"|"own", "until": epoch, "window", "since"}.
"""
from __future__ import annotations

import contextvars
import json
import time
from datetime import datetime

SUBSCRIPTION, POINTS, OWN = "subscription", "points", "own"
WINDOW_SECONDS = {"6h": 6 * 3600, "168h": 7 * 86400}

# set while a relay call is paid with points: the relay provider adds `x-sinobee-billing: points`
PAYING: contextvars.ContextVar[str | None] = contextvars.ContextVar("sinobee_billing", default=None)


def _path(config):
    return config.home / "billing.json"


def _read(config) -> dict:
    try:
        return json.loads(_path(config).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, AttributeError):
        return {}


def _write(config, data: dict) -> None:
    try:
        _path(config).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except (OSError, AttributeError):
        pass


def when(v) -> float | None:
    """Epoch seconds from the relay's retry_at (epoch or ISO date)."""
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def state(config) -> dict:
    """The whole record: the temporary source (if any) plus the last known points balance."""
    return _read(config)


def active(config) -> dict | None:
    """The temporary source in use, or None (= the subscription). Expired when the allowance is back."""
    if config is None:
        return None
    d = _read(config)
    src = d.get("source")
    if src not in (POINTS, OWN):
        return None
    until = d.get("until")
    if until and time.time() >= until:
        clear(config, "allowance back")
        return None
    return d


def choose(config, source: str, *, window: str | None = None, retry_at=None) -> dict:
    """Use points / your own keys until the subscription allowance is back."""
    d = _read(config)
    until = when(retry_at)
    if until is not None and until <= time.time():       # a stale time: look again in a few minutes
        until = time.time() + 600
    if until is None and window in WINDOW_SECONDS:
        until = time.time() + WINDOW_SECONDS[window]
    d.update({"source": source, "until": until, "window": window, "since": time.time()})
    _write(config, d)
    return d


def clear(config, reason: str = "") -> None:
    d = _read(config)
    if d.get("source"):
        d.update({"source": None, "until": None, "window": None, "back_at": time.time(), "back_reason": reason})
        _write(config, d)


def points(config) -> int | None:
    """Last known platform points balance (from sinobee.ai's quota answer), or None when unknown."""
    v = _read(config).get("points")
    return int(v) if isinstance(v, (int, float)) else None


def note_quota(config, q: dict) -> None:
    """Called with each quota answer from sinobee.ai: remember the points balance; when the window that ran out
    has room again, go straight back to the subscription."""
    if not isinstance(q, dict) or q.get("status") not in ("ok", "no_subscription"):
        return
    d = _read(config)
    changed = False
    pts = q.get("points")
    if isinstance(pts, dict):
        pts = pts.get("balance")
    if isinstance(pts, (int, float)) and d.get("points") != pts:
        d["points"] = pts
        changed = True
    if changed:
        _write(config, d)
    if d.get("source") in (POINTS, OWN) and q.get("status") == "ok":
        used = {w.get("window"): w.get("usedPct") for w in q.get("windows") or [] if isinstance(w, dict)}
        win = d.get("window")
        vals = [used.get(win)] if win in used else list(used.values())
        if vals and all(isinstance(v, (int, float)) and v < 100 for v in vals):
            clear(config, "allowance back")


def decide(config, *, has_pool: bool, after_points: bool = False) -> str:
    """What to do when the relay says the allowance (or the points) ran out and the user is not there to ask:
    "points" | "own" | "stop" (stop = ask in the GUI / wait for the allowance)."""
    if config is None:
        return "stop"
    if not after_points and config.get("billing.auto_points") and (points(config) or 0) > 0:
        return POINTS
    if has_pool and config.get("billing.on_exhausted") == "fallback":
        return OWN
    return "stop"


def remember(config, choice: str) -> None:
    """"Do this next time" in the dialog: points -> auto_points; own -> on_exhausted=fallback; pause -> pause."""
    upd = {POINTS: {"auto_points": True}, OWN: {"on_exhausted": "fallback"}, "pause": {"on_exhausted": "pause"}}
    if choice in upd:
        config.save_gui_settings({"billing": upd[choice]})
