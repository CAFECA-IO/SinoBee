"""Skill tools: discover, activate, author (spec-compliant), fork, run, rate, roll back."""
from __future__ import annotations

import shlex

from .approval import shell_danger
from .registry import ToolContext, tool
from .shell import shell


@tool("skill_search", "skills", "Search installed Agent Skills (procedural memory) by task description.",
      {"properties": {"query": {"type": "string"}, "n": {"type": "integer"}}, "required": ["query"]})
def skill_search(ctx: ToolContext, query: str, n: int = 8):
    hits = ctx.skills.search(query, n)
    if not hits:
        return "no matching skills"
    return "\n".join(ctx.skills._line(s) + f"  [score {sc:.2f}]" for s, sc in hits)


@tool("skill_view", "skills",
      "Activate a skill: load its full instructions. ALWAYS activate a matching skill before starting a task it "
      "covers. Pass path to read one of its bundled files (references/..., scripts/..., assets/...).",
      {"properties": {"name": {"type": "string", "description": "skill name from <available_skills>"},
                      "path": {"type": "string"},
                      "reload": {"type": "boolean", "description": "load again even if already active"}},
       "required": ["name"]})
def skill_view(ctx: ToolContext, name: str, path: str | None = None, reload: bool = False):
    if path:
        return ctx.skills.view(name, path)
    active = ctx.state.setdefault("skills_used", set())
    if name in active and not reload:
        return f"skill '{name}' is already active in this session (its instructions are above)."
    content = ctx.skills.view(name)
    active.add(name)
    pre = ctx.skills.preapprovals(name)
    if pre:
        ctx.state.setdefault("preapproved", []).extend(pre)
    return content


SKILL_HINT = ("Full SKILL.md following the Agent Skills spec: frontmatter with ONLY name, description (what + when, "
              "<=1024 chars), optional license/compatibility/allowed-tools, and metadata (string values: version, "
              "sinobee-tags, sinobee-triggers, sinobee-requires-tools). Body: # When to Use, # Procedure (numbered), "
              "# Gotchas, # Verification. Unknown top-level fields are moved into metadata automatically.")


@tool("skill_create", "skills",
      "Save a reusable procedure as a new Agent Skill after completing a non-trivial task (multi-step workflows, "
      "fixes for tricky errors, site-specific navigation, user-corrected approaches) or when the user asks you to "
      "learn/remember something. Write general rules, not an incident log. New skills start as 'experimental'.",
      {"properties": {"name": {"type": "string", "description": "lowercase a-z0-9 and single hyphens, e.g. invoice-export"},
                      "content": {"type": "string", "description": SKILL_HINT},
                      "scope": {"type": "string", "enum": ["global", "project"],
                                "description": "project = only for the current project (.sinobee/skills)"}},
       "required": ["name", "content"]})
def skill_create(ctx: ToolContext, name: str, content: str, scope: str = "global"):
    res = ctx.skills.create(name, content, scope=scope, actor="agent")
    ctx.state.setdefault("skills_created", set()).add(name)
    if ctx.emit:
        ctx.emit("skill", {"action": "create", **res})
    return res


@tool("skill_patch", "skills",
      "Improve an existing skill with an exact string replacement in its SKILL.md (old_string must match once). "
      "Builtin/verified/community skills are read-only for you: use skill_fork first.",
      {"properties": {"name": {"type": "string"}, "old_string": {"type": "string"},
                      "new_string": {"type": "string"}}, "required": ["name", "old_string", "new_string"]})
def skill_patch(ctx: ToolContext, name: str, old_string: str, new_string: str):
    res = ctx.skills.patch(name, old_string, new_string, actor="agent")
    if ctx.emit:
        ctx.emit("skill", {"action": "patch", **res})
    return res


@tool("skill_fork", "skills",
      "Copy a read-only skill (builtin/verified/community) into an editable skill you can patch.",
      {"properties": {"name": {"type": "string"}, "new_name": {"type": "string"},
                      "scope": {"type": "string", "enum": ["global", "project"]}}, "required": ["name"]})
def skill_fork(ctx: ToolContext, name: str, new_name: str | None = None, scope: str = "global"):
    return ctx.skills.fork(name, new_name, scope=scope, actor="agent")


@tool("skill_write_file", "skills",
      "Add or replace a bundled file in a skill: references/<topic>.md (loaded on demand), scripts/<tool>.py|.sh "
      "(non-interactive, --help, clear errors), assets/<file>.",
      {"properties": {"name": {"type": "string"}, "path": {"type": "string"}, "content": {"type": "string"}},
       "required": ["name", "path", "content"]})
def skill_write_file(ctx: ToolContext, name: str, path: str, content: str):
    return ctx.skills.write_file(name, path, content, actor="agent")


def _run_danger(args: dict) -> str | None:
    return shell_danger(str(args.get("args", "")))


@tool("skill_run", "skills", "Execute a script bundled in a skill (e.g. scripts/extract.py). "
      "Python runs with python3, .sh with bash, .js with node.",
      {"properties": {"name": {"type": "string"}, "script": {"type": "string", "description": "e.g. scripts/fetch.py"},
                      "args": {"type": "string", "description": "command-line arguments"},
                      "timeout": {"type": "integer"}}, "required": ["name", "script"]},
      writes=True, dangerous=_run_danger)
async def skill_run(ctx: ToolContext, name: str, script: str, args: str = "", timeout: int = 300):
    s = ctx.skills.get(name)
    pol = s.policy
    if pol.scripts == "deny" or s.disabled:
        return f"ERROR: scripts of '{name}' cannot run (trust: {s.trust}; {s.reason})"
    target = (s.path / script).resolve()
    if not str(target).startswith(str(s.path.resolve()) + "/") or not target.is_file():
        return f"ERROR: script {script} not found in skill {name}"
    if pol.scripts == "confirm" and ctx.config.get("permissions.approval") != "auto":
        ok = bool(ctx.approve) and await ctx.approve(
            "skill_run", {"skill": name, "trust": s.trust, "script": script, "args": args},
            f"runs a script from a {s.trust} skill that has not been reviewed")
        if not ok:
            return "ERROR: user did not approve running this unreviewed skill script"
    runner = {"py": "python3", "sh": "bash", "js": "node"}.get(target.suffix.lstrip("."), "")
    cmd = f"{runner} {shlex.quote(str(target))} {args}".strip()
    ctx.state.setdefault("skills_used", set()).add(name)
    return await shell(ctx, cmd, timeout=timeout, cwd=None)


@tool("skill_feedback", "skills",
      "Report whether a skill worked for the task just done (drives ranking, revision flags and promotion of "
      "experimental skills).",
      {"properties": {"name": {"type": "string"}, "success": {"type": "boolean"}, "note": {"type": "string"}},
       "required": ["name", "success"]})
def skill_feedback(ctx: ToolContext, name: str, success: bool, note: str = ""):
    ctx.state.setdefault("skills_rated", set()).add(name)
    return ctx.skills.feedback(name, success, note)


@tool("skill_rollback", "skills", "Restore a previous version of a skill you are allowed to edit.",
      {"properties": {"name": {"type": "string"}, "version": {"type": "integer"}}, "required": ["name"]})
def skill_rollback(ctx: ToolContext, name: str, version: int | None = None):
    return ctx.skills.rollback(name, version, actor="agent")
