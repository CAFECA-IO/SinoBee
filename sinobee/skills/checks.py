"""Skill-declared checks: a skill can say how to verify its own output, and SinoBee runs that check before a task
that used the skill may finish - the skill author knows best what "right" means for a YouTube report or a video.

SKILL.md metadata:
    sinobee-checks: "scripts/run.py check {dir}"        # command, relative to the skill; {file} / {dir} filled in
    sinobee-checks-on: "reports/youtube/*/report.md"     # which produced files it applies to (glob, comma list)

The check passes on exit code 0 unless its output is JSON that reports problems ({"problems": [...]} non-empty, or
"status"/"ok" saying it failed). Results are recorded per skill (skill_checks table) and shown in its stats.
A skill whose scripts are denied - or need approval and approvals are not automatic - is not run.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import re
import shlex
import time
from pathlib import Path

log = logging.getLogger("sinobee.skills.checks")
SCHEMA = """CREATE TABLE IF NOT EXISTS skill_checks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, ts REAL, target TEXT, passed INTEGER, problems TEXT)"""


def spec(skill) -> tuple[str, list[str]] | None:
    cmd = str(skill.metadata.get("sinobee-checks") or "").strip()
    if not cmd:
        return None
    on = skill.metadata.get("sinobee-checks-on") or "*"
    globs = [g.strip() for g in (on if isinstance(on, list) else str(on).split(",")) if str(g).strip()]
    return cmd, globs or ["*"]


def _matches(path: Path, workspace: Path, globs: list[str]) -> bool:
    try:
        rel = str(path.resolve().relative_to(workspace.resolve()))
    except ValueError:
        rel = str(path)
    return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(path.name, g) or fnmatch.fnmatch(str(path), g)
               for g in globs)


def parse(code: int, out: str) -> list[str]:
    """Problems reported by a check's exit code and output."""
    body = (out.split("\n", 1)[1] if "\n" in out else "") if out.startswith("[exit") else out
    data = None
    s, e = body.find("{"), body.rfind("}")
    if s != -1 and e > s:
        try:
            data = json.loads(body[s:e + 1])
        except ValueError:
            data = None
    if isinstance(data, dict):
        probs = data.get("problems")
        if isinstance(probs, list) and probs:
            return [str(p)[:300] for p in probs[:15]]
        if data.get("ok") is False or str(data.get("status", "")).lower() in ("fix", "fail", "failed", "error"):
            return [str(data.get("error") or data.get("message") or data.get("status"))[:300]]
        if code == 0:
            return []
    if code != 0:
        return [f"check exited with code {code}: {body.strip()[-400:]}"]
    return []


def record(store, name: str, target: str, problems: list[str]) -> None:
    if not store:
        return
    with store._lock:
        store.db.execute(SCHEMA)
        store.db.execute("INSERT INTO skill_checks(name, ts, target, passed, problems) VALUES(?,?,?,?,?)",
                         (name, time.time(), target, int(not problems), json.dumps(problems, ensure_ascii=False)))
        store.db.commit()


def summary(store, name: str) -> dict | None:
    if not store:
        return None
    with store._lock:
        store.db.execute(SCHEMA)
        rows = store.db.execute("SELECT passed, ts, problems FROM skill_checks WHERE name=? ORDER BY id DESC LIMIT 50",
                                (name,)).fetchall()
    if not rows:
        return None
    return {"runs": len(rows), "passed": sum(r["passed"] for r in rows),
            "pass_rate": round(sum(r["passed"] for r in rows) / len(rows), 2), "last": rows[0]["ts"],
            "last_problems": json.loads(rows[0]["problems"] or "[]")[:5]}


async def run(ctx, skill_names, paths: list[Path]) -> list[dict]:
    """Run the checks of the skills used in this task on the files they apply to; issues in agent/qa.py shape."""
    from ..tools.shell import shell
    issues = []
    cfg = ctx.config
    for name in sorted(set(skill_names)):
        s = ctx.skills.skills.get(name) if ctx.skills else None
        sp = spec(s) if s else None
        if not sp:
            continue
        pol = s.policy
        if s.disabled or pol.scripts == "deny" or (pol.scripts == "confirm" and cfg.get("permissions.approval") != "auto"):
            continue
        cmd_tpl, globs = sp
        targets = [p for p in paths if p.is_file() and _matches(p, cfg.workspace, globs)]
        per_dir = "{dir}" in cmd_tpl and "{file}" not in cmd_tpl
        if per_dir:        # a folder check runs once per folder, however many of its files the task wrote
            targets = list({p.parent: p for p in targets}.values())
        for target in list(dict.fromkeys(targets))[:5]:
            parts = cmd_tpl.split(None, 1)
            script = (s.path / parts[0]).resolve()
            if not str(script).startswith(str(s.path.resolve()) + "/") or not script.is_file():
                log.warning("skill %s declares a missing check script %s", name, parts[0])
                break
            args = (parts[1] if len(parts) > 1 else "").replace("{file}", shlex.quote(str(target))) \
                .replace("{dir}", shlex.quote(str(target.parent)))
            runner = {"py": "python3", "sh": "bash", "js": "node"}.get(script.suffix.lstrip("."), "")
            out = await shell(ctx, f"{runner} {shlex.quote(str(script))} {args}".strip(), timeout=180)
            m = re.match(r"\[exit (-?\d+)", out)
            code = int(m.group(1)) if m else 0
            problems = parse(code, out)
            try:
                record(ctx.skills.store, name, str(target), problems)
            except Exception:  # noqa: BLE001
                log.debug("could not record skill check", exc_info=True)
            for p in problems:
                issues.append({"path": str(target.parent if per_dir else target),
                               "name": (target.parent.name + "/") if per_dir else target.name,
                               "problem": f"技能 {name} 的檢查未通過：{p}",
                               "detail": "", "skill": name})
    return issues
