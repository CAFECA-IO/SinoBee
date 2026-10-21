"""Model allocation on the SinoBee side (plan "模型分配與備援機制" P4/P5, 2026-10-21).

Order of authority:
1. a pinned model (`model.pin`) - used for every role; no allocation is asked for, the preference is ignored, and the
   fallback pool puts the same model on your own key first (billing.py / FallbackChain);
2. `sinobee/auto`: SinoBee.ai picks per turn and role (POST /api/v1/llm/route) by `model.preference`;
3. the fallback pool (your own keys / local models) is ordered by the same preference when SinoBee.ai can rank it.

P5: with `billing.share_stats` on (off by default) each finished turn sends one anonymous outcome
(POST /api/v1/llm/stats): model, kind of task, skill name, outcome and numbers - no conversation, no session id, no
files. Off: nothing is sent and nothing is kept for later.
"""
from __future__ import annotations

import contextvars
import logging
import re
import threading
import time

log = logging.getLogger("sinobee.routing")

PREFERENCES = ("speed", "quality", "cost", "balanced")
ROLES = ("main", "thinking", "summary", "review", "embed")
AUTO = "sinobee/auto"

# what the current turn is about, for the allocation (set by the agent; never sent unless an allocation is asked)
HINT: contextvars.ContextVar[dict | None] = contextvars.ContextVar("sinobee_route_hint", default=None)

KINDS = [
    ("page", r"網頁|頁面|landing|html|css|動畫|互動|視覺化|圖表|dashboard|簡報|slides?"),
    ("code", r"程式|code|bug|函式|function|重構|refactor|測試|tests?\b|api|腳本|script|部署|deploy"),
    ("data", r"資料|data|csv|excel|xlsx|sql|統計|分析|報表"),
    ("research", r"研究|調查|查詢|搜尋|search|比較|整理.*資料|報告|research"),
]


def task_kind(text: str) -> str:
    t = (text or "").lower()
    for kind, pat in KINDS:
        if re.search(pat, t):
            return kind
    return "chat"


COMPLEX_KINDS = ("page", "code")


def is_complex(text: str, kind: str | None = None) -> bool:
    """Work that the cheap-and-fast tier (Haiku, Luna, Flash Lite) should not get for its main and thinking roles:
    a page or code, or a long, detailed request. SinoBee.ai applies the floor (route.ts)."""
    t = text or ""
    return (kind or task_kind(t)) in COMPLEX_KINDS or len(t) > 600 or t.count("\n") >= 10


def preference(config) -> str:
    p = str(config.get("model.preference") or "balanced")
    return p if p in PREFERENCES else "balanced"


def pin(config) -> str | None:
    v = (config.get("model.pin") or "").strip() if config is not None else ""
    return v or None


def role_of(spec: str) -> str:
    """'sinobee/auto' -> main, 'sinobee/auto@thinking' -> thinking."""
    r = spec.split("@", 1)[1] if "@" in spec else "main"
    return r if r in ROLES else "main"


# ───────────── asking SinoBee.ai ─────────────

_cache: dict = {}            # (kind, key) -> (until, value)
_lock = threading.Lock()


def _post(config, path: str, body: dict, timeout: float = 8):
    from . import account
    r = account.send(config, "POST", f"{account.relay_base(config)}/{path}", scope="llm", json=body, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def ask(config, role: str, needs: dict | None = None, exclude: list[str] | None = None) -> dict:
    """{model, reason, preference} for one role in the current turn. Cached for the turn (prompt cache, thinking
    signatures and tool formats stay with one model) and for the answer's ttl across turns of the same kind."""
    hint = HINT.get() or {}
    body = {"role": role, "preference": preference(config), "skill": hint.get("skill"),
            "task_kind": hint.get("task_kind") or "chat", "needs": needs or {}}
    if hint.get("complex"):
        body["complex"] = True
    if exclude:                  # these just failed: ask again without them, and keep the new pick for the turn
        body["exclude"] = [m.removeprefix("sinobee/") for m in exclude]
    key = ("route", role, body["preference"], body["skill"], body["task_kind"], bool((needs or {}).get("vision")),
           bool(hint.get("complex")), hint.get("turn"))
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now and not exclude:
            return hit[1]
    try:
        out = _post(config, "route", body)
        val = {"model": str(out["model"]), "reason": out.get("reason") or "", "preference": body["preference"]}
        ttl = min(float(out.get("ttl") or 600), 3600)
    except Exception as e:  # noqa: BLE001  SinoBee.ai unreachable: the last answer, else the plan's default
        log.info("model allocation unavailable: %s", e)
        last = _last.get(role)
        if exclude:
            raise RuntimeError(f"no other model available: {e}") from e
        val = {**last, "reason": "暫時無法取得分配，沿用上次的模型"} if last else \
            {"model": str(config.get("model.auto_default") or "anthropic/claude-sonnet-5-5"),
             "reason": "暫時無法取得分配，使用預設模型", "preference": body["preference"]}
        ttl = 60
    with _lock:
        _cache[key] = (now + ttl, val)
    _last[role] = val
    return val


_last: dict[str, dict] = {}


def forget(role: str | None = None) -> None:
    """Drop cached picks (a picked model failed): the next ask goes to SinoBee.ai."""
    with _lock:
        for k in [k for k in _cache if k[0] == "route" and (role is None or k[1] == role)]:
            _cache.pop(k, None)


def order_pool(config, labels: list[str], role: str = "main") -> list[str]:
    """Your own keys / local models in the order the preference wants (SinoBee.ai ranks them from public evaluation
    facts; only the model names are sent). Unreachable: unchanged."""
    if len(labels) < 2:
        return labels
    from . import account
    if not account.can_relay(config):
        return labels
    key = ("pool", role, preference(config), tuple(labels))
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    try:
        out = _post(config, "route", {"role": role, "preference": preference(config), "candidates": labels,
                                      "task_kind": (HINT.get() or {}).get("task_kind") or "chat"})
        ranked = [str(r["model"]).lower() for r in out.get("ranking") or []]
        order = sorted(labels, key=lambda lb: ranked.index(lb.lower()) if lb.lower() in ranked else len(ranked))
    except Exception as e:  # noqa: BLE001
        log.info("could not rank the fallback pool: %s", e)
        order = labels
    with _lock:
        _cache[key] = (now + 600, order)
    return order


def board(config, labels: list[str]) -> list[dict]:
    """For the settings page: each of your models with its public evaluation facts and the preferences that would
    pick it (GET /api/v1/llm/board). Public data; nothing about you is sent but the model names."""
    import httpx

    from . import account
    if not labels:
        return []
    r = httpx.get(f"{account.relay_base(config)}/board", params={"models": ",".join(labels[:30])}, timeout=10)
    r.raise_for_status()
    return r.json().get("models") or []


# ───────────── P5: anonymous outcomes (opt-in) ─────────────

def report(config, item: dict) -> bool:
    """Send one finished turn's outcome when the user allowed it (billing.share_stats, off by default). Returns
    whether anything was sent. Only these fields leave the computer: model, task_kind, skill, outcome, seconds,
    tokens_in, tokens_out, retries, cut_off."""
    if config is None or not config.get("billing.share_stats"):
        return False
    from . import account
    if not account.can_relay(config):
        return False
    keep = ("model", "task_kind", "skill", "outcome", "seconds", "tokens_in", "tokens_out", "retries", "cut_off")
    clean = {k: item[k] for k in keep if k in item}
    if not clean.get("model") or clean.get("outcome") not in ("done", "incomplete", "failed", "stopped"):
        return False

    def send():
        try:
            _post(config, "stats", {"items": [clean]}, timeout=10)
        except Exception as e:  # noqa: BLE001
            log.info("could not send the anonymous outcome: %s", e)
    threading.Thread(target=send, daemon=True).start()
    return True
