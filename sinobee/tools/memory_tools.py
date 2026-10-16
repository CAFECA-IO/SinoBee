"""Memory tools exposed to the model (L0 core, L1 working, L2 long-term, L3 episodes)."""
from __future__ import annotations

import json

from .registry import ToolContext, tool

KIND_ENUM = ["fact", "preference", "lesson", "environment", "profile", "procedure"]


TIER_HELP = ("Retention tiers: short = facts about the current task/project that may matter for the next days "
             "(expire after memory.short_ttl_days unless used again); long = stable facts, preferences, lessons "
             "(default); core = tiny standing rules shown in every prompt; permanent = never expires or decays, ONLY "
             "when the user explicitly asks to remember something permanently (永久記住/一定要記得).")


def _runtime_aux(ctx: ToolContext):
    rt = ctx.state.get("runtime")
    return getattr(rt, "aux", None)


@tool("memory_save", "memory",
      "Save something worth remembering across sessions: user preferences/profile, environment facts, lessons "
      "learned, corrections, task decisions worth keeping for a while. " + TIER_HELP +
      " Do NOT store secrets or one-off details; the current task's scratch notes belong in working_memory.",
      {"properties": {
          "content": {"type": "string", "description": "one self-contained statement"},
          "kind": {"type": "string", "enum": KIND_ENUM},
          "tier": {"type": "string", "enum": ["short", "long", "core", "permanent"]},
          "tags": {"type": "array", "items": {"type": "string"}},
          "importance": {"type": "number", "description": "0..1 (default 0.5; 0.8+ for standing preferences)"},
          "ttl_days": {"type": "number", "description": "tier short only: days until it expires (default 14)"},
      }, "required": ["content"]})
async def memory_save(ctx: ToolContext, content: str, kind: str = "fact", tier: str = "long",
                      tags: list | None = None, importance: float = 0.5, ttl_days: float | None = None):
    if ctx.config.get("memory.write_approval"):
        pid = ctx.memory.stage("memory_save", {"content": content, "kind": kind, "tier": tier, "tags": tags,
                                               "importance": importance, "ttl_days": ttl_days})
        return f"staged for user approval (pending id {pid})"
    res = await ctx.memory.asave(content, kind=kind, tier=tier, tags=tags, importance=importance,
                                 source=ctx.session_id, ttl_days=ttl_days)
    if ctx.emit:
        ctx.emit("memory", {"action": "save", **res})
    return res


@tool("memory_search", "memory",
      "Search memory (hybrid keyword + semantic) over core, short and long tiers. Relevant memories are already "
      "auto-recalled each turn; use this for deeper or different queries, or to find ids before update/forget.",
      {"properties": {"query": {"type": "string"}, "k": {"type": "integer"},
                      "tier": {"type": "string", "enum": ["core", "short", "long", "permanent", "archive"]},
                      "include_archive": {"type": "boolean"}}, "required": ["query"]})
async def memory_search(ctx: ToolContext, query: str, k: int = 8, include_archive: bool = False,
                        tier: str | None = None):
    if tier in ("core", "short", "long", "archive"):
        tiers = (tier,)
    else:
        tiers = ("core", "short", "long", "archive") if include_archive else ("core", "short", "long")
    hits = await ctx.memory.asearch(query, k=k * 3 if tier == "permanent" else k, tiers=tiers)
    if tier == "permanent":
        hits = [m for m in hits if m.pinned][:k]
    return [m.brief() for m in hits] or "no matching memories"


@tool("memory_update", "memory",
      "Update a memory by id (ids are shown as [mxxxx]). Changing content creates a new version and keeps history. "
      "Use to correct outdated facts, change importance, or move between tiers (short/long/core/permanent). "
      "Changing or un-pinning a permanent memory needs confirm_permanent=true, which you may only set when the "
      "user asked for exactly that change.",
      {"properties": {"id": {"type": "string"}, "content": {"type": "string"},
                      "importance": {"type": "number"},
                      "tier": {"type": "string", "enum": ["short", "long", "core", "permanent"]},
                      "pinned": {"type": "boolean", "description": "false = no longer permanent"},
                      "ttl_days": {"type": "number"}, "confirm_permanent": {"type": "boolean"}},
       "required": ["id"]})
async def memory_update(ctx: ToolContext, id: str, content: str | None = None, importance: float | None = None,
                        tier: str | None = None, pinned: bool | None = None, ttl_days: float | None = None,
                        confirm_permanent: bool = False):
    cur = ctx.memory.get(id)
    if cur and cur.pinned and not confirm_permanent and (content or pinned is False or tier in ("short", "archive")):
        return (f"ERROR: [{id}] is a permanent memory. Ask the user to confirm this change, then call again with "
                "confirm_permanent=true.")
    if tier == "permanent":
        pinned = True
    res = await ctx.memory.aupdate(id, content, importance=importance, tier=tier, pinned=pinned, ttl_days=ttl_days)
    if ctx.emit:
        ctx.emit("memory", {"action": "update", **res})
    return res


@tool("memory_forget", "memory",
      "Remove memories the user wants gone. Give `id`, `ids`, or a `query` (optionally narrowed by tier/kind/tag). "
      "A query first returns a PREVIEW of what would be removed; show it to the user, then call again with "
      "confirm=true (and the ids). Default archives (still searchable with include_archive); hard=true deletes. "
      "Permanent memories are skipped unless confirm_permanent=true (only when the user named them).",
      {"properties": {"id": {"type": "string"}, "ids": {"type": "array", "items": {"type": "string"}},
                      "query": {"type": "string"},
                      "tier": {"type": "string", "enum": ["core", "short", "long", "permanent"]},
                      "kind": {"type": "string", "enum": KIND_ENUM}, "tag": {"type": "string"},
                      "confirm": {"type": "boolean"}, "hard": {"type": "boolean"},
                      "confirm_permanent": {"type": "boolean"}}})
def memory_forget(ctx: ToolContext, id: str | None = None, ids: list | None = None, query: str | None = None,
                  tier: str | None = None, kind: str | None = None, tag: str | None = None, confirm: bool = False,
                  hard: bool = False, confirm_permanent: bool = False):
    if id and not ids and not query:
        return ctx.memory.forget(id, hard=hard, force=confirm_permanent)
    targets = list(ids or []) + ([id] if id else [])
    if not targets and not (query or tier or kind or tag):
        return "ERROR: give id, ids or a query/tier/kind/tag filter"
    found = ctx.memory.select(query=query, ids=targets or None, tier=tier, kind=kind, tag=tag, limit=50)
    if not found:
        return "no matching memories"
    if not confirm and not targets:
        return {"preview": [m.brief() for m in found],
                "next": "Show this list to the user. If they agree, call memory_forget(ids=[...], confirm=true)."}
    done, skipped = [], []
    for m in found:
        if m.pinned and not confirm_permanent:
            skipped.append(m.id)
            continue
        done.append(ctx.memory.forget(m.id, hard=hard, force=confirm_permanent)["id"])
    out = {"status": "deleted" if hard else "archived", "ids": done}
    if skipped:
        out["skipped_permanent"] = skipped
        out["note"] = "permanent memories need the user's explicit confirmation (confirm_permanent=true)"
    if ctx.emit:
        ctx.emit("memory", {"action": "forget", **out})
    return out


@tool("memory_compress", "memory",
      "Shrink memory: groups related memories (by query/tier/kind/tag, or all), merges each group into one shorter "
      "statement without losing numbers, paths or names, and supersedes the originals (history kept). "
      "dry_run=true (default) only previews; apply with dry_run=false. Permanent memories are left alone unless "
      "include_pinned=true. target_chars: if still above it, lists low-value forget candidates (never removes them).",
      {"properties": {"query": {"type": "string"}, "ids": {"type": "array", "items": {"type": "string"}},
                      "tier": {"type": "string", "enum": ["core", "short", "long", "permanent"]},
                      "kind": {"type": "string", "enum": KIND_ENUM}, "tag": {"type": "string"},
                      "target_chars": {"type": "integer"}, "include_pinned": {"type": "boolean"},
                      "dry_run": {"type": "boolean"}}})
async def memory_compress(ctx: ToolContext, query: str | None = None, ids: list | None = None, tier: str | None = None,
                          kind: str | None = None, tag: str | None = None, target_chars: int | None = None,
                          include_pinned: bool = False, dry_run: bool = True):
    from ..memory.compress import compress, render
    rep = await compress(ctx.memory, _runtime_aux(ctx), query=query, ids=ids, tier=tier, kind=kind, tag=tag,
                         target_chars=target_chars, include_pinned=include_pinned or tier == "permanent",
                         dry_run=dry_run)
    if not rep["clusters"]:
        rep["note"] = "nothing to merge safely (no related or redundant memories in the selection)"
    if ctx.emit and rep["applied"]:
        ctx.emit("memory", {"action": "compress", "saved_chars": rep["chars_before"] - rep["chars_after"]})
    return render(rep)


@tool("memory_stats", "memory",
      "Memory capacity per tier (permanent/core/short/long/archive counts and characters), core prompt usage, and "
      "short-term memories expiring soon. Use before compressing or when the user asks what you remember.",
      {"properties": {}})
def memory_stats(ctx: ToolContext):
    return ctx.memory.stats()


@tool("memory_maintain", "memory",
      "Tidy memory now (the same upkeep that also runs automatically): expire short-term memories (keeping the "
      "reused ones), archive stale unimportant ones, and merge related memories when memory is large. Never "
      "touches permanent or core memories and never deletes. Use when the user asks to 整理記憶 / clean up memory.",
      {"properties": {}})
async def memory_maintain(ctx: ToolContext):
    from ..memory.maintain import maintain
    rt = ctx.state.get("runtime")
    if rt is None:
        return "ERROR: runtime unavailable"
    return await maintain(rt, reason="tool", force=True, emit=ctx.emit)


@tool("memory_restore", "memory",
      "Undo a merge or an update: give the id of the memory that replaced others (from memory_compress, the "
      "maintenance report or memory_update). The replaced memories come back and that one is archived.",
      {"properties": {"id": {"type": "string"}}, "required": ["id"]})
def memory_restore(ctx: ToolContext, id: str):
    return ctx.memory.restore(id)


@tool("session_search", "memory",
      "Full-text search over all past conversations (episodic memory). Returns snippets with session ids.",
      {"properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]})
def session_search(ctx: ToolContext, query: str, k: int = 8):
    return ctx.memory.session_search(query, k) or "no matches"


@tool("session_read", "memory", "Read messages around a position in a past session (from session_search).",
      {"properties": {"session_id": {"type": "string"}, "around": {"type": "integer"},
                      "window": {"type": "integer"}}, "required": ["session_id"]})
def session_read(ctx: ToolContext, session_id: str, around: int = 0, window: int = 6):
    return ctx.memory.session_read(session_id, around, window) or "nothing found"


@tool("working_memory", "memory",
      "Your scratchpad for the CURRENT task (L1). It is shown to you every turn, so keep goal/plan/progress here for "
      "long tasks. actions: set(key, value) | append(key, value) | remove(key) | clear | view. "
      "Suggested keys: goal, plan, done, facts, next.",
      {"properties": {"action": {"type": "string", "enum": ["set", "append", "remove", "clear", "view"]},
                      "key": {"type": "string"}, "value": {"type": "string"}}, "required": ["action"]})
def working_memory(ctx: ToolContext, action: str, key: str | None = None, value: str | None = None):
    wm = ctx.working
    if action == "set" and key:
        wm[key] = value or ""
    elif action == "append" and key:
        cur = wm.get(key, "")
        wm[key] = (cur + "\n" if cur else "") + (value or "")
    elif action == "remove" and key:
        wm.pop(key, None)
    elif action == "clear":
        wm.clear()
    elif action != "view":
        return "ERROR: invalid action/key"
    if ctx.emit:
        ctx.emit("working_memory", dict(wm))
    out = json.dumps(wm, ensure_ascii=False, indent=2) if wm else "(empty)"
    size, limit = sum(len(str(v)) for v in wm.values()), int(ctx.config.get("memory.working_char_limit", 3000))
    if size > limit // 2:
        out += (f"\n[note: working memory is {size} characters; keep it to the key points (goal, plan, next, key "
                "facts) and put long results in files - it is condensed automatically when the context fills up]")
    return out
