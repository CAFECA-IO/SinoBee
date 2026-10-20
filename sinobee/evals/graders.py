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
    judge2: object = None                                   # optional second judge (another model family)
    tokens: int = 0                                         # what the trial spent (input + output)
    steps: int = 0
    trace: list[dict] = field(default_factory=list)         # per-step cost: tokens, retries, failed
    security: dict | None = None                            # the sandbox's record (evals/sandbox.py), when sandboxed


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
        if kind == "efficiency":
            retries = sum(int(s.get("retries") or 0) for s in t.trace)
            failed = sum(1 for s in t.trace if s.get("failed"))
            checks = [("tokens", t.tokens, g.get("max_tokens")), ("steps", t.steps, g.get("max_steps")),
                      ("retries", retries, g.get("max_retries")), ("failed steps", failed, g.get("max_failed"))]
            over = [f"{n} {v} > {lim}" for n, v, lim in checks if lim is not None and v > int(lim)]
            seen = ", ".join(f"{n} {v}" for n, v, lim in checks if lim is not None)
            return not over, ("; ".join(over) if over else seen)
        if kind == "security":
            from .sandbox import grade as security_grade
            return security_grade(str(g.get("check") or ""), t.security)
        if kind == "llm":
            return await _llm(g, t)
    except (KeyError, re.error) as e:
        return False, f"bad grader {g}: {e}"
    return False, f"unknown grader {kind}"


async def grade_full(g: dict, t: Trial) -> dict:
    """A grader's verdict with what a published report shows: for an llm rubric also the votes, the evidence the
    judge quoted and, with a second judge, whether it agreed."""
    if g.get("type") == "llm":
        try:
            return await _llm_full(g, t)
        except KeyError as e:
            return {"ok": False, "detail": f"bad grader {g}: {e}"}
    ok, why = await grade(g, t)
    return {"ok": ok, "detail": why}


JUDGE = """You grade the result of an AI agent's task against a rubric. Be strict: pass only when the result clearly
satisfies the rubric; when the evidence is missing or you are unsure, fail.
RUBRIC: {rubric}
TASK: {prompt}
FILES THE AGENT PRODUCED (excerpts):
{files}
FINAL ANSWER:
{answer}
Reply with JSON only: {{"pass": true|false, "reason": "<one sentence>", "evidence": "<a short quote from the files or
answer that your verdict rests on>"}}"""


def _judge_files(g: dict, t: Trial) -> list[Path]:
    """The files the judge reads: the grader's `files` globs (in that order), else the first files the agent made."""
    if g.get("files"):
        out: list[Path] = []
        for pat in g["files"]:
            out += [p for p in sorted(_files(t, pat)) if p not in out]
        return out[:6]
    return [p for p in t.files if p.is_file()][:6]


JUDGE_TOKENS = 2048        # room for a model that thinks before it answers (its reasoning counts against the limit)
RETRY_NOTE = ("\n\nYour previous reply was empty, cut off or not the JSON asked for. Reply with the JSON object only, "
              "nothing before or after it.")


async def ask_json(judge, prompt: str, key: str, temperature: float = 0) -> dict:
    """A judge's JSON answer that contains `key`. A model that spends its output budget thinking returns nothing
    (gemini-*-pro with a few hundred tokens): ask with room to think, and once more - with more room and a reminder -
    when the answer is missing. {} when the judge still gives none (the caller records a judge error, never a fail
    of the thing under test)."""
    from ..learning.reflector import parse_json
    from ..types import Message
    for i, (extra, tokens) in enumerate((("", JUDGE_TOKENS), (RETRY_NOTE, JUDGE_TOKENS * 4))):
        try:
            resp = await judge.chat([Message("user", prompt + extra)], None, temperature=temperature, max_tokens=tokens)
        except Exception as e:  # noqa: BLE001 - a failed call is retried like an empty one
            if i:
                raise
            import logging
            logging.getLogger("sinobee.evals").warning("judge call failed, retrying: %s", e)
            continue
        data = parse_json(resp.message.text())
        if isinstance(data, dict) and key in data:
            return data
    return {}


async def _ask(judge, prompt: str, temperature: float) -> dict:
    return await ask_json(judge, prompt, "pass", temperature)


def _verdict(data: dict, need_evidence: bool) -> tuple[bool | None, str, str]:
    if "pass" not in data:
        return None, "judge gave no verdict", ""
    ok, reason, ev = bool(data["pass"]), str(data.get("reason") or "")[:300], str(data.get("evidence") or "")[:300]
    if ok and need_evidence and not ev.strip():
        return False, "passed without quoting evidence (counted as a fail)", ""
    return ok, reason, ev


async def _llm_full(g: dict, t: Trial) -> dict:
    """votes > 1: the judge is asked that many times (a little temperature so the votes are independent) and the
    majority wins; a tie fails. require_evidence: a pass without a quote counts as a fail."""
    if t.judge is None:
        return {"ok": False, "detail": "no model to judge with"}
    prompt = JUDGE.format(rubric=g["rubric"][:2000], prompt=t.prompt[:3000],
                          files=excerpts_for(g, t) or "(none)", answer=(t.answer or "")[:5000])
    n = max(1, min(int(g.get("votes") or 1), 5))
    need = bool(g.get("require_evidence"))
    votes = []
    for _ in range(n):
        votes.append(_verdict(await _ask(t.judge, prompt, 0.4 if n > 1 else 0), need))
    valid = [v for v in votes if v[0] is not None]
    if not valid:      # the judge never answered: not the contestant's failure, and the result cannot be trusted
        return {"ok": False, "judge_error": True, "detail": f"評鑑模型沒有給出判定（{n} 次都沒有回應或格式錯誤）"}
    yes = sum(1 for v in valid if v[0])
    ok = yes * 2 > len(valid)                 # the majority of the votes the judge actually gave; a tie fails
    side = [v for v in valid if bool(v[0]) == ok] or valid
    out = {"ok": ok, "detail": (f"{yes}/{len(valid)} 票：" if n > 1 else "") + side[0][1], "evidence": side[0][2]}
    if n > 1:
        out["votes"] = [yes, len(valid)]
    if len(valid) < n:
        out["judge_missing"] = n - len(valid)
    if t.judge2 is not None:
        try:
            v2 = _verdict(await _ask(t.judge2, prompt, 0), need)
            out["judge2_ok"] = bool(v2[0])
        except Exception as e:  # noqa: BLE001 - a second opinion that fails must not fail the grade
            out["judge2_error"] = str(e)[:120]
    return out


def excerpts_for(g: dict, t: Trial) -> str:
    from ..agent.review import excerpts
    return excerpts(_judge_files(g, t))[:14000]


async def _llm(g: dict, t: Trial) -> tuple[bool, str]:
    r = await _llm_full(g, t)
    return r["ok"], r["detail"]
