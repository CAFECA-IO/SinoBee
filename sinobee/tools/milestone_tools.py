"""milestone: closing the current milestone of a task that runs as milestones (agent/milestones.py)."""
from __future__ import annotations

from .registry import ToolContext, tool


@tool("milestone", "core",
      "For a task that runs as milestones (a <milestones> block is shown each step): action=done closes the current "
      "milestone once its acceptance outcomes are true and checked - give a summary of what was done, the results "
      "and what later milestones need, and the output files. The context is then cleared for the next milestone, "
      "so put everything that must carry on into the summary or a file. action=drop skips a milestone that is no "
      "longer needed (say why). action=view shows the plan.",
      {"properties": {"action": {"type": "string", "enum": ["done", "drop", "view"]},
                      "id": {"type": "string", "description": "the milestone id (default: the current one)"},
                      "summary": {"type": "string", "description": "done: what was done, results, what later "
                                                                  "milestones need; drop: why"},
                      "outputs": {"type": "array", "items": {"type": "string"},
                                  "description": "files (paths) or facts this milestone produced"}},
       "required": ["action"]})
async def milestone(ctx: ToolContext, action: str, id: str = "", summary: str = "",  # noqa: A002
                    outputs: list | None = None):
    agent = ctx.state.get("agent")
    runner = getattr(agent, "_msr", None)
    if runner is None:
        return "ERROR: this task does not run as milestones."
    if action == "done":
        return runner.tool_done(id, summary, outputs or [])
    if action == "drop":
        return runner.tool_drop(id or (runner.plan.current() or {}).get("id", ""), summary)
    return runner.panel()
