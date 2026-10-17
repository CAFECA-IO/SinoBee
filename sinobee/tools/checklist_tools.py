"""task_checklist: the acceptance checklist of a multi-step task.

The agent writes, at the start, what "done" means as checkable items; the list is re-attached at the end of every
request (so it stays in view after 50 steps and survives compaction) and SinoBee will not let the task finish while
items are open. Marking an item done needs evidence (a command's output, a file path, a screenshot, a URL).
The list lives in the session state and on the blackboard (kind=plan, key=checklist) so an interrupted task resumes
with it.
"""
from __future__ import annotations

import json
import time

from .registry import ToolContext, tool

STATUSES = ("todo", "doing", "done", "dropped")
MAX_ITEMS = 20


def get(ctx: ToolContext) -> list[dict]:
    return ctx.state.setdefault("checklist", [])


def open_items(items: list[dict]) -> list[dict]:
    return [i for i in items if i.get("status") in ("todo", "doing")]


def save(ctx: ToolContext) -> None:
    items = get(ctx)
    if ctx.emit:
        ctx.emit("checklist", {"items": items})
    scope = ctx.state.get("board_scope")
    if not scope:
        return
    try:
        from ..work.hub import get_hub
        rt = ctx.state.get("_runtime")
        if rt is not None:
            get_hub(rt).store.board_write(scope, "plan", json.dumps(items, ensure_ascii=False), key="checklist",
                                          author="checklist")
    except Exception:  # noqa: BLE001 - the in-memory list is what the agent uses
        pass


def render(items: list[dict]) -> str:
    if not items:
        return ""
    mark = {"todo": "[ ]", "doing": "[~]", "done": "[x]", "dropped": "[-]"}
    lines = []
    for i in items:
        line = f"{i['id']}. {mark.get(i['status'], '[ ]')} {i['text']}"
        if i.get("check"):
            line += f" — check: {i['check']}"
        if i.get("evidence"):
            line += f" — evidence: {i['evidence'][:160]}"
        lines.append(line)
    left = len(open_items(items))
    return ("<task_checklist>\nAcceptance checklist for this task (" + (f"{left} open" if left else "all done")
            + "). Keep it current with task_checklist; mark an item done only with evidence; add items when the "
            "user asks for more.\n" + "\n".join(lines) + "\n</task_checklist>")


@tool("task_checklist", "core",
      "The acceptance checklist of a multi-step task: what must be true for the result to be right (e.g. 'the "
      "report has a cover image', 'every number cites a source', 'the page opens without errors'), each with how "
      "to check it. Write it before the real work (action=set), update items as you go (action=update with "
      "status doing/done/dropped; done needs evidence: command output, file path, screenshot or URL), add items "
      "when the user asks for more. SinoBee shows it to you every step and will not finish the task while items "
      "are open.",
      {"properties": {
          "action": {"type": "string", "enum": ["set", "add", "update", "view"]},
          "items": {"type": "array", "maxItems": MAX_ITEMS, "description": "set/add: the items", "items": {
              "type": "object", "properties": {
                  "text": {"type": "string", "description": "a checkable outcome, not an activity"},
                  "check": {"type": "string", "description": "how it is verified"}}, "required": ["text"]}},
          "id": {"type": "integer", "description": "update: the item number"},
          "status": {"type": "string", "enum": list(STATUSES)},
          "evidence": {"type": "string", "description": "update to done: what shows it is true"},
          "note": {"type": "string", "description": "update to dropped: why"}},
       "required": ["action"]})
def task_checklist(ctx: ToolContext, action: str, items: list | None = None, id: int | None = None,  # noqa: A002
                   status: str | None = None, evidence: str = "", note: str = ""):
    cur = get(ctx)
    if action in ("set", "add"):
        new = []
        for it in items or []:
            if isinstance(it, str):
                it = {"text": it}
            text = str((it or {}).get("text") or "").strip()
            if text:
                new.append({"text": text[:200], "check": str(it.get("check") or "").strip()[:200]})
        if not new:
            return "ERROR: give items with a text each."
        if action == "set":
            cur.clear()
        for n in new:
            if len(cur) >= MAX_ITEMS:
                break
            cur.append({"id": len(cur) + 1, "status": "todo", "evidence": "", "updated": time.time(), **n})
        save(ctx)
        return render(cur)
    if action == "update":
        item = next((i for i in cur if i["id"] == id), None)
        if item is None:
            return f"ERROR: no item {id}; the list has {len(cur)} items."
        if status not in STATUSES:
            return f"ERROR: status must be one of {', '.join(STATUSES)}."
        if status == "done" and not evidence.strip():
            return "ERROR: marking an item done needs evidence (the command output, file path, screenshot or URL that shows it)."
        if status == "dropped" and not (note or evidence).strip():
            return "ERROR: say why the item is dropped (note)."
        item.update(status=status, evidence=(evidence or note).strip()[:300], updated=time.time())
        save(ctx)
        return render(cur)
    return render(cur) or "No checklist yet: write one with action=set."
