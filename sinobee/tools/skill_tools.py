"""Skill tools: discover, activate, author (spec-compliant), fork, run, rate, roll back."""
from __future__ import annotations

import re
import shlex

from .approval import shell_danger
from ..types import ToolResult
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
        s = ctx.skills.get(name)
        qs = f"\nQuick start: {s.quickstart}" if s.quickstart else ""
        return (f"skill '{name}' is already active in this session.{qs}\n"
                "If its instructions are no longer in view, call skill_view again with reload=true.")
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


@tool("skill_run", "skills",
      "Run a skill's script. `script` is a file INSIDE the skill (e.g. scripts/run.py) - never a project file or "
      "folder; put commands, folders and files in `args`. Skills marked [run: …] have an entry script: call "
      "skill_run(name, args=\"<input>\") without `script`. Python runs with python3, .sh with bash, .js with node. "
      "A non-zero exit is reported as an error.",
      {"properties": {"name": {"type": "string"},
                      "script": {"type": "string", "description": "a file of the skill, e.g. scripts/run.py; "
                                                                  "omit to run the skill's entry script"},
                      "args": {"type": "string", "description": "command-line arguments: command, folders, files"},
                      "timeout": {"type": "integer"}}, "required": ["name"]},
      writes=True, dangerous=_run_danger)
async def skill_run(ctx: ToolContext, name: str, script: str = "", args: str = "", timeout: int = 300):
    s = ctx.skills.get(name)
    pol = s.policy
    if pol.scripts == "deny" or s.disabled:
        return ToolResult.error(f"scripts of '{name}' cannot run (trust: {s.trust}; {s.reason})")
    root = s.path.resolve()
    target = (s.path / script).resolve() if script else None
    if target is None or not str(target).startswith(str(root) + "/") or not target.is_file():
        if s.entry:   # the natural call "run the skill on my input" works: input goes to the entry script
            if script:
                args = f"{shlex.quote(script)} {args}".strip()
            target = (s.path / s.entry).resolve()
            script = s.entry
        else:
            scripts = sorted(f for f in s.files() if f.startswith("scripts/") and
                             f.rsplit(".", 1)[-1] in ("py", "sh", "js"))
            return ToolResult.error(
                f"script {script!r} not found in skill {name}. `script` must be one of the skill's own scripts "
                f"({', '.join(scripts) or 'none'}); put the command and file paths in `args`, e.g. "
                f"skill_run(name=\"{name}\", script=\"{scripts[0] if scripts else 'scripts/<file>.py'}\", "
                f"args=\"<command> <folder>\")")
    if pol.scripts == "confirm" and ctx.config.get("permissions.approval") != "auto":
        ok = bool(ctx.approve) and await ctx.approve(
            "skill_run", {"skill": name, "trust": s.trust, "script": script, "args": args},
            f"runs a script from a {s.trust} skill that has not been reviewed")
        if not ok:
            return ToolResult.error("user did not approve running this unreviewed skill script")
    runner = {"py": "python3", "sh": "bash", "js": "node"}.get(target.suffix.lstrip("."), "")
    cmd = f"{runner} {shlex.quote(str(target))} {args}".strip()
    ctx.state.setdefault("skills_used", set()).add(name)
    out = await shell(ctx, cmd, timeout=timeout, cwd=None)
    m = re.match(r"\[exit (-?\d+)", out)
    if m and m.group(1) != "0":
        return ToolResult(f"ERROR: {script} exited with code {m.group(1)}\n{out}", True)
    return out


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
