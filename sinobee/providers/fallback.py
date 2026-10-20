"""A fallback model to offer when a temporary model error stopped a task and none is set (proposal "large tasks no
longer stop half-way", U5, decided 2026-10-20): another vendor the user already has a key for, at the same tier as
the model that failed; else the SinoBee.ai relay when signed in. Offered once on the failure card; never set
without the user's click.
"""
from __future__ import annotations

import re

from .catalog import POPULAR, RECOMMENDED

TIERS = {"便宜快速": "fast", "平衡，推薦": "balanced", "最強": "strong", "較強": "strong", "最強，昂貴": "strong"}


def tier(label: str) -> str:
    """fast | balanced | strong for a model spec, from the curated list, else from its name."""
    prov, _, mid = (label or "").partition("/")
    for m, note in RECOMMENDED.get(prov, []):
        if m == mid and note in TIERS:
            return TIERS[note]
    if re.search(r"lite|mini|nano|haiku|small|flash-8b", mid, re.I):
        return "fast"
    if re.search(r"opus|-pro\b|pro-|ultra|large|\bmax\b", mid, re.I):
        return "strong"
    return "balanced"


def _pick(prov: str, want: str) -> str | None:
    picks = [(m, TIERS.get(note)) for m, note in RECOMMENDED.get(prov, []) if TIERS.get(note)]
    for m, t in picks:
        if t == want:
            return m
    return next((m for m, t in picks if t == "balanced"), picks[0][0] if picks else None)


def suggest(config, failed: str) -> dict | None:
    """{"spec", "label", "why"} or None when nothing usable is configured."""
    from . import usable_now
    from .catalog import LABELS
    vendor = (failed or "").split("/", 1)[0]
    want = tier(failed)
    for prov in POPULAR:
        if prov in (vendor, "sinobee", "ollama", "openrouter") or prov not in RECOMMENDED:
            continue
        try:
            ok = usable_now(prov, config)
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            continue
        m = _pick(prov, want)
        if m:
            return {"spec": f"{prov}/{m}", "label": f"{LABELS.get(prov, prov)} {m}",
                    "why": f"你已有 {LABELS.get(prov, prov)} 的金鑰；這是與 {failed} 同級的模型"}
    try:
        from .. import account
        relay = vendor != "sinobee" and account.can_relay(config)
    except Exception:  # noqa: BLE001
        relay = False
    if relay:
        m = _pick("sinobee", want)
        if m:
            return {"spec": f"sinobee/{m}", "label": f"SinoBee.ai 訂閱 {m}", "why": "你已登入 SinoBee.ai，可以用訂閱中繼"}
    return None
