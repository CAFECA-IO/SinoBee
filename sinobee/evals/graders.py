"""Graders: each takes the finished trial and says pass / fail with a short reason.

Deterministic graders come first and are preferred; the `llm` grader (a rubric judged by the reviewer model) is for
what a regex cannot see, and is told to fail when unsure.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Trial:
    prompt: str
    workspace: Path
    answer: str = ""
    status: str = ""
    tools: list[str] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)       # files the agent made or changed
    base_home: Path | None = None                          # the real SinoBee home (quality rules live there)
    judge: object = None                                    # provider for llm grading


def _flags(g: dict) -> int:
    return sum({"i": re.I, "m": re.M, "s": re.S}[c] for c in str(g.get("flags") or "") if c in "ims")


def _files(t: Trial, pattern: str) -> list[Path]:
    root = t.workspace.resolve()
    hits = [p for p in root.glob(pattern) if p.is_file()] if any(c in pattern for c in "*?[") else \
        ([root / pattern] if (root / pattern).is_file() else [])
    return [p for p in hits if str(p.resolve()).startswith(str(root))]


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


async def grade(g: dict, t: Trial) -> tuple[bool, str]:
    kind = g["type"]
    try:
        if kind == "file_exists":
            found = _files(t, g["path"])
            return bool(found), f"{g['path']}: {'found' if found else 'missing'}"
        if kind in ("file_regex", "file_not_regex"):
            found = _files(t, g["path"])
            if not found:
                return False, f"{g['path']}: missing"
            rx = re.compile(g["pattern"], _flags(g))
            matched = [p.name for p in found if rx.search(_read(p))]
            if kind == "file_regex":
                return len(matched) == len(found), f"/{g['pattern']}/ in {len(matched)}/{len(found)} files"
            return not matched, ("not found" if not matched else f"found in {', '.join(matched[:5])}")
        if kind in ("answer_regex", "answer_not_regex"):
            m = re.search(g["pattern"], t.answer or "", _flags(g))
            ok = bool(m) if kind == "answer_regex" else not m
            return ok, (f"matched {m.group(0)[:80]!r}" if m else "no match")
        if kind == "status":
            want = g.get("is", "done")
            return t.status == want, f"status {t.status} (want {want})"
        if kind == "tool_used":
            n = t.tools.count(g["name"])
            lo, hi = int(g.get("min", 1)), g.get("max")
            ok = n >= lo and (hi is None or n <= int(hi))
            return ok, f"{g['name']} used {n}×"
        if kind == "qa_clean":
            from ..agent import qa, rules
            paths = _files(t, g["path"]) if g.get("path") else [p for p in t.files if p.is_file()]
            if not paths:
                return bool(g.get("allow_none", True)), "no files to check"
            issues = await qa.check(paths)
            if t.base_home:
                issues += rules.check(t.base_home, paths)
            return not issues, ("clean" if not issues else qa.report(issues, limit=5))
        if kind == "rule":
            from ..agent import rules
            rule = next((r for r in rules.load(t.base_home) if r.get("id") == g["rule_id"]), None) if t.base_home else None
            if rule is None:
                return True, f"rule {g['rule_id']} no longer exists (skipped)"
            paths = [p for p in t.files if p.is_file()]
            issues = rules.check(t.base_home, paths, rules=[{**rule, "enabled": True}])
            return not issues, ("not violated" if not issues else issues[0]["detail"][:200])
        if kind == "command":
            proc = await asyncio.create_subprocess_exec(
                "/bin/bash", "-lc", g["cmd"], cwd=str(t.workspace), stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=int(g.get("timeout", 120)))
            except asyncio.TimeoutError:
                proc.kill()
                return False, "command timed out"
            want = int(g.get("exit", 0))
            text = out.decode("utf-8", "replace").strip()
            return proc.returncode == want, f"exit {proc.returncode}: {text[-200:]}"
        if kind == "llm":
            return await _llm(g, t)
    except (KeyError, re.error) as e:
        return False, f"bad grader {g}: {e}"
    return False, f"unknown grader {kind}"


JUDGE = """You grade the result of an AI agent's task against a rubric. Be strict: pass only when the result clearly
satisfies the rubric; when the evidence is missing or you are unsure, fail.
RUBRIC: {rubric}
TASK: {prompt}
FILES THE AGENT PRODUCED (excerpts):
{files}
FINAL ANSWER:
{answer}
Reply with JSON only: {{"pass": true|false, "reason": "<one sentence>"}}"""


def _judge_files(g: dict, t: Trial) -> list[Path]:
    """The files the judge reads: the grader's `files` globs (in that order), else the first files the agent made."""
    if g.get("files"):
        out: list[Path] = []
        for pat in g["files"]:
            out += [p for p in sorted(_files(t, pat)) if p not in out]
        return out[:6]
    return [p for p in t.files if p.is_file()][:6]


async def _llm(g: dict, t: Trial) -> tuple[bool, str]:
    from ..agent.review import excerpts
    from ..learning.reflector import parse_json
    from ..types import Message
    if t.judge is None:
        return False, "no model to judge with"
    prompt = JUDGE.format(rubric=g["rubric"][:2000], prompt=t.prompt[:3000],
                          files=excerpts(_judge_files(g, t))[:14000] or "(none)",
                          answer=(t.answer or "")[:5000])
    resp = await t.judge.chat([Message("user", prompt)], None, temperature=0, max_tokens=400)
    data = parse_json(resp.message.text())
    if "pass" not in data:
        return False, "judge gave no verdict"
    return bool(data["pass"]), str(data.get("reason") or "")[:300]
