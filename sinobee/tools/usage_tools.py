"""Let the agent inspect its own token usage and spending."""
from __future__ import annotations

from .registry import ToolContext, tool


@tool("usage_stats", "usage",
      "Report SinoBee's own token usage and cost (USD, based on each vendor's price list). "
      "period: session | today | 7d | 30d | month | all. group_by: none | model | purpose | day | session.",
      {"properties": {"period": {"type": "string", "enum": ["session", "today", "7d", "30d", "month", "all"]},
                      "group_by": {"type": "string", "enum": ["none", "model", "purpose", "day", "session"]}}})
def usage_stats(ctx: ToolContext, period: str = "session", group_by: str = "none"):
    from ..agent.loop import Runtime
    mem = ctx.memory
    if period == "session":
        out = {"period": "this session", **mem.usage_totals(session_id=ctx.session_id)}
    else:
        since = Runtime.period_start(period)
        out = {"period": period, **mem.usage_totals(since)}
        if group_by != "none":
            out["breakdown"] = mem.usage_grouped(group_by, since, limit=20)
    unpriced = mem.unpriced_models()
    if unpriced:
        out["note"] = f"no price configured for: {', '.join(unpriced)} (cost counted as 0; set it in Settings)"
    return out
