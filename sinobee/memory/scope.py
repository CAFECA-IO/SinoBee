"""What a conversation may remember and recall (memory scopes).

Every memory says where it came from (`source`: the conversation that learned it) and how far it reaches:

- scope "global": about you - your preferences, how you like work done, your environment. Used in every
  conversation once its status is "ok".
- scope "conversation": facts of one task or project. Used only in the conversation that learned it.
- status "pending": a global memory learned in a conversation that read outside content (web pages, the browser):
  a page may carry instructions dressed up as things to remember ("always upload files to …"). It is used only
  where it was learned until you approve it on the memory page.

Reading other conversations' transcripts (session_search / session_read) is limited to the current conversation;
looking further asks you (once for this task, always for this conversation, or not) unless
memory.cross_conversation is "allow".

Memories from before scopes stay global (they always were); the memory page lists them for review.
"""
from __future__ import annotations

import time

TAINTING = ("web", "browser")          # toolsets that bring outside content into a conversation


def conversation_of(ctx) -> str | None:
    """The conversation a tool call belongs to (a sub-agent's is its root conversation's)."""
    return (getattr(ctx, "state", {}) or {}).get("sandbox_conversation") or getattr(ctx, "session_id", None)


def taint(ctx) -> None:
    """This conversation read outside content: its global memories wait for approval from now on."""
    ctx.state["tainted"] = True
    conv, mem = conversation_of(ctx), getattr(ctx, "memory", None)
    if conv and mem is not None and hasattr(mem, "meta_set"):
        try:
            if mem.meta_get(f"tainted:{conv}") is None:
                mem.meta_set(f"tainted:{conv}", time.time())
        except Exception:  # noqa: BLE001
            pass


def tainted(memory, conversation: str | None) -> bool:
    if not conversation or memory is None:
        return False
    try:
        return memory.meta_get(f"tainted:{conversation}") is not None
    except Exception:  # noqa: BLE001
        return False


def status_for(memory, conversation: str | None, scope: str) -> str:
    """A global memory from a conversation that read outside content waits for your approval."""
    return "pending" if scope == "global" and tainted(memory, conversation) else "ok"


GLOBAL_KINDS = {"preference", "profile", "lesson", "procedure", "environment"}


def scope_for_kind(kind: str) -> str:
    """What background learning keeps for every conversation: about you and how to work; facts and episodes stay
    with their conversation."""
    return "global" if kind in GLOBAL_KINDS else "conversation"
