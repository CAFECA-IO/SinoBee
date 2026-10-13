"""Memory tools exposed to the model (L0 core, L1 working, L2 long-term, L3 episodes)."""
from __future__ import annotations

import json

from .registry import ToolContext, tool

KIND_ENUM = ["fact", "preference", "lesson", "environment", "profile", "procedure"]


@tool("memory_save", "memory",
      "Save something worth remembering across sessions: user preferences/profile, environment facts, lessons "
      "learned, corrections. tier=core only for small, always-relevant facts (who the user is, standing rules); "
      "everything else tier=long (recalled automatically when relevant). Do NOT store secrets or task-transient details.",
      {"properties": {
          "content": {"type": "string", "description": "one self-contained statement"},
          "kind": {"type": "string", "enum": KIND_ENUM},
          "tier": {"type": "string", "enum": ["core", "long"]},
          "tags": {"type": "array", "items": {"type": "string"}},
          "importance": {"type": "number", "description": "0..1 (default 0.5; 0.8+ for standing preferences)"},
      }, "required": ["content"]})
async def memory_save(ctx: ToolContext, content: str, kind: str = "fact", tier: str = "long",
                      tags: list | None = None, importance: float = 0.5):
    if ctx.config.get("memory.write_approval"):
        pid = ctx.memory.stage("memory_save", {"content": content, "kind": kind, "tier": tier, "tags": tags,
                                               "importance": importance})
        return f"staged for user approval (pending id {pid})"
    res = await ctx.memory.asave(content, kind=kind, tier=tier, tags=tags, importance=importance,
                                 source=ctx.session_id)
    if ctx.emit:
        ctx.emit("memory", {"action": "save", **res})
    return res


@tool("memory_search", "memory",
      "Search long-term memory (hybrid keyword + semantic). Relevant memories are already auto-recalled each turn; "
      "use this for deeper or different queries.",
      {"properties": {"query": {"type": "string"}, "k": {"type": "integer"},
                      "include_archive": {"type": "boolean"}}, "required": ["query"]})
async def memory_search(ctx: ToolContext, query: str, k: int = 8, include_archive: bool = False):
    tiers = ("core", "long", "archive") if include_archive else ("core", "long")
    hits = await ctx.memory.asearch(query, k=k, tiers=tiers)
    return [m.brief() for m in hits] or "no matching memories"


@tool("memory_update", "memory",
      "Update a memory by id (ids are shown as [mxxxx]). Changing content creates a new version and keeps history. "
      "Use to correct outdated facts, change importance, or move between core/long tiers.",
      {"properties": {"id": {"type": "string"}, "content": {"type": "string"},
                      "importance": {"type": "number"}, "tier": {"type": "string", "enum": ["core", "long"]}},
       "required": ["id"]})
async def memory_update(ctx: ToolContext, id: str, content: str | None = None, importance: float | None = None,
                        tier: str | None = None):
    return await ctx.memory.aupdate(id, content, importance=importance, tier=tier)


@tool("memory_forget", "memory", "Archive (default) or permanently delete a memory by id.",
      {"properties": {"id": {"type": "string"}, "hard": {"type": "boolean"}}, "required": ["id"]})
def memory_forget(ctx: ToolContext, id: str, hard: bool = False):
    return ctx.memory.forget(id, hard=hard)


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
    return json.dumps(wm, ensure_ascii=False, indent=2) if wm else "(empty)"
