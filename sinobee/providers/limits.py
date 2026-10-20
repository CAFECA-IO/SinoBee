"""Output limits per model (proposal U8, after Claude Code's CLAUDE_CODE_MAX_OUTPUT_TOKENS).

Every model has a DEFAULT output limit, sent on ordinary steps and reserved from the context window, and a MAX, used
only when a step needs room (after a reply was cut off at the limit). The max comes from the provider's model API
where it says (Gemini's models.get gives outputTokenLimit), else from the verified list below, and is remembered in
$SINOBEE_HOME/model_caps.json next to the parameters a model does not accept (compat.py). A model nobody knows keeps
SinoBee's old fixed values (model.max_tokens, model.reasoning_max_tokens).

    model.max_output_tokens: auto | <number>     the default for every model; a number above a model's max is cut
    SINOBEE_MAX_OUTPUT_TOKENS=<number>           the same, from the environment (wins over the setting)
"""
from __future__ import annotations

import logging
import os
import re
import time

import httpx

log = logging.getLogger("sinobee.providers")

DEFAULT_CAP = 32768          # a default never above this (Claude Code: 32k); more only when a step needs it
# verified limits (source, date) for models whose provider API does not report one
KNOWN = [
    (re.compile(r"(^|/)claude-opus-5-5\b", re.I), 128_000,
     "platform.claude.com/docs/en/models/opus-5-5/overview (2026-10-20)"),
    (re.compile(r"(^|/)gemini-(flash|pro|flash-lite)-latest$", re.I), 65_536, "Gemini models API (2026-10-20)"),
]


_tried: set[str] = set()       # models whose discovery failed in this process: not asked again until a restart


def _caps():
    from . import compat
    return compat


def max_output(label: str | None) -> int | None:
    """The most this model can write in one reply, or None when nobody knows."""
    if not label:
        return None
    c = _caps()
    with c._lock:
        n = (c._load().get(label) or {}).get("max_output")
    if isinstance(n, int) and n > 0:
        return n
    for rx, m, _src in KNOWN:
        if rx.search(label):
            return m
    return None


def remember(label: str, n: int, source: str) -> None:
    c = _caps()
    with c._lock:
        caps = c._load()
        entry = caps.setdefault(label, {"drop": []})
        if entry.get("max_output") == n:
            return
        entry.update(max_output=int(n), max_output_source=source, max_output_checked=time.time())
        try:
            p = c.path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            import json
            tmp.write_text(json.dumps(caps, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(p)
        except OSError:
            log.warning("could not save %s", c.path(), exc_info=True)


def override(config) -> int | None:
    """The user's default output limit (environment first, then the setting), or None for auto."""
    raw = os.environ.get("SINOBEE_MAX_OUTPUT_TOKENS") or config.get("model.max_output_tokens", "auto")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def default_output(config, label: str | None, legacy: int) -> int:
    """The output limit of an ordinary step: the user's override cut to the model's max; else half the model's max
    (at least the old fixed value, at most DEFAULT_CAP); else the old fixed value."""
    mx = max_output(label)
    o = override(config)
    if o:
        return min(o, mx) if mx else o
    if mx:
        return min(mx, max(legacy, min(mx // 2, DEFAULT_CAP)))
    return legacy


async def discover(provider, timeout: float = 5.0) -> int | None:
    """Ask the provider's model API for the output limit, once per model (kept in model_caps.json). Gemini only for
    now; others use KNOWN. Never raises."""
    label = getattr(provider, "label", "")
    c = _caps()
    with c._lock:
        if "max_output" in (c._load().get(label) or {}):
            return None
    if getattr(provider, "provider_name", "") != "gemini" or not getattr(provider, "api_key", None) or label in _tried:
        return None
    _tried.add(label)
    base = re.sub(r"/openai/?$", "", str(getattr(provider, "base_url", "")).rstrip("/"))
    if "generativelanguage.googleapis.com" not in base:
        return None
    model = str(provider.model).split("/")[-1]
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{base}/models/{model}", headers={"x-goog-api-key": provider.api_key})
        n = int((r.json() or {}).get("outputTokenLimit") or 0) if r.status_code == 200 else 0
    except Exception as e:  # noqa: BLE001 - discovery is a convenience; the fallback still works
        log.info("output limit of %s not discovered: %s", label, e)
        return None
    if n > 0:
        remember(label, n, f"{base}/models/{model}")
        return n
    return None
