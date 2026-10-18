"""quality_rule: turn a problem the user pointed out into a check that runs on the files of every later task.

Use it when the user says something like "以後報告都要附參考來源" / "never leave TODO in the files" / "公式的反斜線
又不見了": a rule with a regular expression that must (require) or must not (forbid) appear in matching files.
Adding or removing a rule changes how every later task is checked, so it asks the user first (unless approvals are
automatic). Rules are listed and switched in 設定 → 品質.
"""
from __future__ import annotations

from .registry import ToolContext, tool


def _fmt(r: dict) -> str:
    return (f"{r['id']} [{'on' if r.get('enabled', True) else 'off'}] {r['title']} — {r['kind']} /{r['pattern']}/"
            f"{r.get('flags') or ''} in {', '.join(r['files'])} (hits {r.get('hits', 0)})")


@tool("quality_rule", "core",
      "Quality rules: checks SinoBee runs on the files of EVERY later task before it may finish. Add one when the "
      "user reports a mistake that a regular expression can catch in the output files and wants it never to happen "
      "again (e.g. forbid a leftover 'TODO' or a raw '$$', require a '參考來源' section in reports). "
      "action=add needs title, pattern, kind (forbid|require), files (globs like *.md); the user is asked to confirm. "
      "list shows the rules; enable/disable/remove take an id. Test the pattern on a real file first.",
      {"properties": {
          "action": {"type": "string", "enum": ["add", "list", "enable", "disable", "remove"]},
          "title": {"type": "string", "description": "short name in the user's language"},
          "kind": {"type": "string", "enum": ["forbid", "require"]},
          "pattern": {"type": "string", "description": "Python regular expression"},
          "flags": {"type": "string", "description": "any of i, m, s"},
          "files": {"type": "array", "items": {"type": "string"}, "description": "globs, default *.md and *.html"},
          "message": {"type": "string", "description": "what is wrong when it fires, user's language"},
          "id": {"type": "string"}},
       "required": ["action"]})
async def quality_rule(ctx: ToolContext, action: str, title: str = "", kind: str = "forbid", pattern: str = "",
                       flags: str = "", files: list | None = None, message: str = "", id: str = ""):  # noqa: A002
    from ..agent import rules as R
    home = ctx.config.home
    if action == "list":
        rules = R.load(home)
        return "\n".join(_fmt(r) for r in rules) or "No quality rules yet."
    auto = ctx.config.get("permissions.approval") == "auto"
    try:
        if action == "add":
            rule = R.validate({"title": title, "kind": kind, "pattern": pattern, "flags": flags, "files": files,
                               "message": message, "source": {"type": "agent", "session": ctx.session_id}})
            if not auto:
                ok = bool(ctx.approve) and await ctx.approve(
                    "quality_rule", {"title": rule["title"], "kind": rule["kind"], "pattern": rule["pattern"],
                                     "files": rule["files"]},
                    "adds a check that runs on the files of every later task")
                if not ok:
                    return "ERROR: user did not approve adding this rule"
            r = R.add(home, rule)
            if ctx.emit:
                ctx.emit("rule_added", {"rule": r})
            return "Added: " + _fmt(r)
        if not id:
            return "ERROR: give the rule id (see action=list)"
        if action in ("enable", "disable"):
            return _fmt(R.update(home, id, enabled=action == "enable"))
        if action == "remove":
            if not auto:
                ok = bool(ctx.approve) and await ctx.approve("quality_rule", {"remove": id},
                                                             "removes a quality check")
                if not ok:
                    return "ERROR: user did not approve removing this rule"
            R.remove(home, id)
            return f"Removed {id}."
    except R.RuleError as e:
        return f"ERROR: {e}"
    return "ERROR: unknown action"
