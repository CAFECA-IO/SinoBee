"""Automatic memory upkeep: runs without the model choosing to, so weak models and unattended runs stay tidy.

Three entry points:
- maybe_maintain(rt): after a chat turn; rate-limited (memory.maintain_interval_hours) and run in the background.
- maintain(rt, force=True): the daily scheduled job (workflow "memory-maintenance", action memory.maintain), the
  UI button and `sinobee memory maintain`.
- flush_to_memory(rt, ...): before old conversation is summarised away (context near the model limit), the key facts
  of the part being dropped are saved as short-term memories so they can still be recalled.

Guards (automatic mode is stricter than the memory_compress tool):
- one run at a time per runtime; skipped while the spending budget is exceeded (then merges are extractive only);
- never touches permanent (pinned) or core memories, nor memories written or changed in the last
  memory.auto_compress_min_age_days (they may belong to the task in progress);
- merges only pass the loss check (numbers, paths, URLs kept) and stay reversible with restore(id);
- never deletes: it only consolidates (expiry/decay rules) and lists forget candidates.
"""
from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger("sinobee.memory")

FLUSH_PROMPT = """The conversation below is about to be summarised to fit the model's context window. Extract the
facts from it that someone would need again in a later task: decisions, file locations, names, settings,
user preferences or corrections, unresolved problems. Skip greetings, transient progress and anything secret
(passwords, keys, tokens). At most {n} items, each ONE self-contained statement in the conversation's language.
Use tier "short" for facts about this task/project, "long" for stable preferences, profile facts and lessons.
Return ONLY JSON: {{"memories": [{{"content": "...", "kind": "fact|preference|lesson|environment|profile|procedure",
"tier": "short|long", "importance": 0.0-1.0}}]}}  (an empty list is fine)

CONVERSATION SUMMARY:
{text}"""


def _lock(rt) -> asyncio.Lock:
    lock = getattr(rt, "_memory_maint_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        rt._memory_maint_lock = lock
    return lock


def due(rt, now: float | None = None) -> bool:
    if not rt.config.get("memory.auto_maintain", True):
        return False
    last = rt.memory.meta_get("maintain.last_at", 0) or 0
    hours = float(rt.config.get("memory.maintain_interval_hours", 6))
    return (now or time.time()) - float(last) >= hours * 3600


def maybe_maintain(rt, reason: str = "turn", emit=None):
    """Called at the end of a turn: starts a background run when one is due. Returns the task or None."""
    try:
        if not due(rt) or _lock(rt).locked():
            return None
        return rt.spawn(maintain(rt, reason=reason, emit=emit))
    except Exception:  # noqa: BLE001 - upkeep must never break a conversation
        log.exception("could not start memory maintenance")
        return None


async def maintain(rt, *, reason: str = "manual", force: bool = False, emit=None) -> dict:
    cfg, store = rt.config, rt.memory
    async with _lock(rt):
        if not force and not due(rt):
            return {"skipped": "not due"}
        started = time.time()
        report: dict = {"reason": reason, "at": started}
        store.meta_set("maintain.last_at", started)     # set first: a crash must not retry in a tight loop
        report["consolidation"] = store.consolidate(archive_after_days=float(cfg.get("memory.archive_after_days", 90)))
        before = store.stats()
        size = before["short"]["chars"] + before["long"]["chars"]
        limit = int(cfg.get("memory.auto_compress_chars", 12000))
        report["size_chars"] = size
        if cfg.get("memory.auto_compress", True) and size > limit:
            from .compress import compress
            budget = rt.budget_status() if hasattr(rt, "budget_status") else {"exceeded": False}
            aux = None if budget.get("exceeded") else getattr(rt, "aux", None)
            try:
                rep = await compress(store, aux, dry_run=False, exclude_core=True, include_pinned=False,
                                     min_age_days=float(cfg.get("memory.auto_compress_min_age_days", 1)),
                                     target_chars=int(limit * 0.8), limit=1000)
                report["compression"] = {
                    "merged": [{"new_id": c.get("new_id"), "sources": c["sources"], "method": c["method"]}
                               for c in rep["clusters"]],
                    "saved_chars": rep["chars_before"] - rep["chars_after"],
                    "forget_candidates": [c["id"] for c in rep.get("forget_candidates", [])][:20]}
            except Exception as e:  # noqa: BLE001
                log.exception("automatic memory compression failed")
                report["errors"] = [f"compress: {e}"]
        after = store.stats()
        report["after"] = {k: after[k] for k in ("permanent", "core", "short", "long", "archive")}
        report["core_over_limit"] = after["core_usage"]["over_limit"]
        report["seconds"] = round(time.time() - started, 2)
        store.meta_set("maintain.last_report", report)
        changed = any(report["consolidation"].get(k) for k in report["consolidation"]) or \
            bool(report.get("compression", {}).get("merged"))
        if emit and changed:
            try:
                emit("memory", {"action": "maintain", "saved_chars": report.get("compression", {}).get("saved_chars", 0),
                                "archived": len(report["consolidation"].get("archived", []))
                                + len(report["consolidation"].get("expired_short", []))})
            except Exception:  # noqa: BLE001
                pass
        log.info("memory maintenance (%s): %s", reason, {k: v for k, v in report.items() if k != "after"})
        return report


async def flush_to_memory(rt, session_id: str, text: str, *, max_items: int = 5) -> list[dict]:
    """Save the durable facts of conversation text that is being compacted away (as short / long memories)."""
    if not text or not text.strip():
        return []
    from ..learning.reflector import parse_json
    from ..types import Message
    prompt = FLUSH_PROMPT.format(n=max_items, text=text[:12000])
    try:
        resp = await rt.aux.chat([Message("user", prompt)], None, temperature=0, max_tokens=800)
    except Exception:  # noqa: BLE001
        log.exception("memory flush failed")
        return []
    saved = []
    for m in (parse_json(resp.message.text()).get("memories") or [])[:max_items]:
        if not isinstance(m, dict) or not str(m.get("content", "")).strip():
            continue
        tier = m.get("tier") if m.get("tier") in ("short", "long") else "short"     # never core / permanent
        try:
            if rt.config.get("memory.write_approval"):
                rt.memory.stage("memory_save", {**m, "tier": tier})
                continue
            saved.append(await rt.memory.asave(str(m["content"]), kind=m.get("kind", "fact"), tier=tier,
                                               importance=float(m.get("importance", 0.5)),
                                               tags=["context-flush"], source=session_id, scope="conversation"))
        except Exception as e:  # noqa: BLE001 - e.g. refused by the content scan
            log.info("flushed memory not saved: %s", e)
    return saved
