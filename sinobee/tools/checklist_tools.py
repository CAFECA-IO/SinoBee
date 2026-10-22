"""task_checklist: the acceptance checklist of a multi-step task.

The agent writes, at the start, what "done" means as checkable items; the list is re-attached at the end of every
request (so it stays in view after 50 steps and survives compaction) and SinoBee will not let the task finish while
items are open. Marking an item done needs evidence (a command's output, a file path, a screenshot, a URL).
The list lives in the session state and on the blackboard (kind=plan, key=checklist) so an interrupted task resumes
with it.
"""
from __future__ import annotations

import json
import re
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


PATHISH = re.compile(r"[\w./~-]*[\w-]\.[A-Za-z0-9]{1,5}\b|(?:~|/|\.\.?/)[\w./-]+")
URL = re.compile(r"https?://\S+")


def _tool_outputs(ctx: ToolContext) -> str:
    agent = ctx.state.get("agent")
    hist = getattr(agent, "history", None) or []
    return "\n".join(m.text() for m in hist[-400:] if getattr(m, "role", "") == "tool")


def verifiable(ctx: ToolContext, evidence: str) -> bool:
    """Evidence someone else can check: an existing file, a URL, or text that a tool of this task really returned."""
    ev = (evidence or "").strip()
    if URL.search(ev):
        return True
    from ..agent.evidence import ID_RE, store_of
    cited = {f"E{m.group(1)}" for m in ID_RE.finditer(ev)}
    if cited:
        try:
            if cited & store_of(ctx).ids():
                return True                # a picture SinoBee captured itself
        except Exception:  # noqa: BLE001
            pass
    from ..fileaccess import resolve_file
    for token in PATHISH.findall(ev):
        try:
            if resolve_file(ctx.config, token.strip(".,;:)(")).exists():
                return True
        except Exception:  # noqa: BLE001
            continue
    outputs = _tool_outputs(ctx)
    if not outputs:
        return True                        # no history to compare against (tests, direct calls)
    flat = re.sub(r"\s+", " ", outputs)
    for j, part in enumerate(re.split(r"[`\"“”「」]", ev)):
        part = re.sub(r"\s+", " ", part).strip()
        if len(part) >= (6 if j % 2 else 16) and part in flat:     # quoted output may be short
            return True
    norm = re.sub(r"\s+", " ", ev)
    return any(norm[i:i + 16] in flat for i in range(0, max(1, len(norm) - 15), 4)) if len(norm) >= 16 else False


VISUAL = re.compile(r"截圖|截图|畫面|画面|螢幕|看到|看得到|顯示|显示|視覺|视觉|動畫|动画|操作|遊戲|游戏|按下|點擊|點選|"
                    r"screenshot|screen|visible|visual|looks?|displays?|shows? up|animat|playable|click|press",
                    re.IGNORECASE)


def visual_needs(ctx: ToolContext, item: dict, evidence: str) -> str | None:
    """Sandbox (plan V3): an item about what is seen on screen is done only with an evidence id SinoBee captured.
    Returns an error message, or None when the evidence is fine."""
    try:
        from ..sandbox import executor_kind
        if executor_kind(ctx.config) != "sandbox":
            return None
    except Exception:  # noqa: BLE001
        return None
    if not VISUAL.search(f"{item.get('text', '')} {item.get('check', '')}"):
        return None
    from ..agent.evidence import ID_RE, store_of
    store = store_of(ctx)
    known = store.ids()
    cited = {f"E{m.group(1)}" for m in ID_RE.finditer(evidence or "")}
    good = cited & known
    if not cited:
        return ("ERROR: this item is about what is seen on screen, so its evidence must cite a picture SinoBee "
                "captured: take a screenshot or run playtest, then give its evidence id (e.g. 'E4: the score shows 120').")
    if not good:
        return f"ERROR: {', '.join(sorted(cited))} is not a captured picture of this conversation (known: " \
               f"{', '.join(sorted(known, key=lambda x: int(x[1:]))[-8:]) or 'none yet'})."
    failed = [e for e in good if (store.get(e) or {}).get("ok") is False]
    if failed and len(failed) == len(good):
        return f"ERROR: {', '.join(sorted(failed))} failed its check in playtest; fix the problem and cite passing evidence."
    return None


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
        if status == "done" and not verifiable(ctx, evidence):
            return ("ERROR: the evidence must be checkable: a file path that exists, a URL, or words copied from a tool "
                    "result of this task (for example a line of command output). A description of what you did, or "
                    "'the check passed', is not evidence.")
        if status == "done":
            need = visual_needs(ctx, item, evidence)
            if need:
                return need
        if status == "dropped" and not (note or evidence).strip():
            return "ERROR: say why the item is dropped (note)."
        item.update(status=status, evidence=(evidence or note).strip()[:300], updated=time.time())
        save(ctx)
        return render(cur)
    return render(cur) or "No checklist yet: write one with action=set."
