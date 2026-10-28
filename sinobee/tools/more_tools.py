"""Tools held back from a small local model to keep its prompt short (agent/loop.py, LOCAL_BASE_TOOLS): the model
asks for the ones a task needs, and they are sent from its next step on."""
from __future__ import annotations

from .registry import ToolContext, ToolResult, tool


@tool("more_tools", "core",
      "Make more tools available. Only the common tools are sent to save room; the others are listed in this "
      "description by group. Pass tool names or group names; they are available from your next step.",
      {"properties": {"names": {"type": "array", "items": {"type": "string"},
                                "description": "tool names or group names from this tool's description"}},
       "required": ["names"]})
def more_tools(ctx: ToolContext, names: list | str):
    hidden: dict = ctx.state.get("hidden_tools") or {}
    if isinstance(names, str):
        names = [n.strip() for n in names.split(",")]
    want, unknown = set(), []
    for n in names or []:
        n = str(n).strip()
        if n in hidden:
            want.add(n)
        elif n in set(hidden.values()):
            want |= {t for t, g in hidden.items() if g == n}
        else:
            unknown.append(n)
    if not want:
        return ToolResult.error(f"none of these can be added: {', '.join(unknown) or '(nothing given)'}. "
                                f"Hidden groups: {', '.join(sorted(set(hidden.values()))) or '(none)'}")
    ctx.state.setdefault("tools_shown", set()).update(want)
    note = f" (not found: {', '.join(unknown)})" if unknown else ""
    return f"These tools are available from your next step: {', '.join(sorted(want))}{note}"
