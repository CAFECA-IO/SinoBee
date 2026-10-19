"""Failure reports: when a task does not finish, everything needed to understand why is kept for improving the
system - the request, the thinking model's plan and advice, the checklist, every step the execution model took
(tool, arguments, result or error), the quality checks, the models and the cost - plus the thinking model's
diagnosis (category, where it failed, evidence, concrete improvements).

    $SINOBEE_HOME/failures/<id>.json

Secrets that are stored in SinoBee are removed from the record. Reports are listed in Settings > Quality and with
`sinobee failures`; one can be turned into a regression eval so the fix is measured.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger("sinobee.failures")
MAX_REPORTS = 300
SAFE = re.compile(r"^[\w.-]{1,80}$")


def _dir(home) -> Path:
    p = Path(home) / "failures"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _scrub(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(s, "[secret]")
    return text


def work_log(messages, secrets: list[str], limit: int = 120) -> list[dict]:
    """Every tool step of the turn: the call, and whether it worked (error text or a short result)."""
    calls, out = {}, []
    for m in messages:
        for c in getattr(m, "tool_calls", None) or []:
            calls[c.id] = c
            args = json.dumps(c.arguments or {}, ensure_ascii=False, default=str)
            out.append({"id": c.id, "tool": c.name, "args": _scrub(args[:600], secrets)})
        if m.role == "tool" and m.tool_call_id in calls:
            text = m.text()
            err = text.lstrip().startswith(("ERROR", "REPEATED CALL")) or text.startswith("[exit ") and \
                not text.startswith("[exit 0")
            for row in reversed(out):
                if row["id"] == m.tool_call_id:
                    row["ok"] = not err
                    row["result"] = _scrub(text[:700 if err else 240], secrets)
                    break
        elif m.role == "user" and isinstance(m.content, str) and m.content.startswith("["):
            # system notes in the history: thinking-model advice, quality-gate fix lists, user steering
            out.append({"note": _scrub(m.content[:900], secrets)})
    for row in out:
        row.pop("id", None)
    return out[-limit:]


def build(agent, *, status: str, error_info: dict | None, final_text: str, query: str, stats: dict,
          turn_start: int, turn_id, usage: dict, steps: int, duration: float) -> dict:
    from ..tools import checklist_tools
    try:
        secrets = agent.config.secrets.all_values()
    except Exception:  # noqa: BLE001
        secrets = []
    rt = agent.rt
    thinker = rt.thinking()
    return {
        "id": time.strftime("%Y%m%d-%H%M%S") + f"-{agent.session_id[:8]}-{int(time.time() * 1000) % 1000:03d}",
        "created": time.time(), "session_id": agent.session_id, "turn_id": turn_id, "status": status,
        "reason": error_info or {}, "request": _scrub(query[:4000], secrets), "final_answer": _scrub(final_text[:2000], secrets),
        "models": {"execution": getattr(agent._prov, "label", ""), "thinking": getattr(thinker, "label", None),
                   "reviewer": getattr(rt.reviewer(), "label", None)},
        "skills": sorted(set(stats.get("explicit_skills") or []) | set(agent.ctx.state.get("skills_used", set()))),
        "plan": stats.get("plan"), "consults": stats.get("consult_log", []),
        "checklist": checklist_tools.get(agent.ctx), "steps": steps, "tool_calls": stats.get("tool_calls", 0),
        "errors": stats.get("errors", 0), "last_error": _scrub(stats.get("last_error", ""), secrets),
        "quality": {"gate_rounds": stats.get("gate_rounds"), "qa_issues": (stats.get("qa_issues") or [])[:20],
                    "review": stats.get("review"), "level": (stats.get("quality") or [None])[0],
                    "skipped": stats.get("quality_skipped") or []},
        "control_log": stats.get("control_log") or [],
        "partial": stats.get("partial"),
        "budget": agent._guard.summary() if getattr(agent, "_guard", None) else None,
        "progress": (stats.get("progress_trace") or [])[-80:],
        "thresholds": agent._progress.thresholds() if getattr(agent, "_progress", None) else {},
        "usage": usage, "duration_s": duration,
        "work_log": work_log(agent._turn_messages(turn_start) if hasattr(agent, "_turn_messages") else agent.history[turn_start:], secrets),
        "milestones": agent._msr.plan.card({}, status) if getattr(agent, "_msr", None) else None,
        "diagnosis": None,
    }


def save(home, report: dict) -> dict:
    d = _dir(home)
    (d / f"{report['id']}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for old in files[:-MAX_REPORTS]:
        old.unlink(missing_ok=True)
    return report


def get(home, rid: str) -> dict:
    if not SAFE.match(rid or ""):
        raise KeyError(rid)
    try:
        return json.loads((_dir(home) / f"{rid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise KeyError(rid) from None


def delete(home, rid: str) -> None:
    if not SAFE.match(rid or ""):
        raise KeyError(rid)
    p = _dir(home) / f"{rid}.json"
    if not p.is_file():
        raise KeyError(rid)
    p.unlink()


def listing(home, limit: int = 50) -> list[dict]:
    out = []
    for f in sorted(_dir(home).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        dg = r.get("diagnosis") or {}
        out.append({"id": r["id"], "created": r.get("created"), "status": r.get("status"),
                    "summary": (r.get("reason") or {}).get("summary", ""), "request": (r.get("request") or "")[:160],
                    "models": r.get("models"), "skills": r.get("skills"), "category": dg.get("category"),
                    "diagnosis": dg.get("summary"), "session_id": r.get("session_id"), "regression": r.get("regression"),
                    "trigger": next((e.get("trigger") for e in reversed(r.get("control_log") or [])
                                     if e.get("event") == "stop"), (r.get("reason") or {}).get("trigger"))})
    return out


def summary_text(r: dict, limit: int = 30000) -> str:
    """The record as the thinking model reads it for the diagnosis."""
    parts = [f"REQUEST: {r.get('request', '')}", f"STATUS: {r.get('status')} - {json.dumps(r.get('reason'), ensure_ascii=False)}",
             f"MODELS: {json.dumps(r.get('models'), ensure_ascii=False)}", f"SKILLS: {', '.join(r.get('skills') or [])}"]
    if r.get("plan"):
        parts.append("PLAN: " + json.dumps(r["plan"], ensure_ascii=False)[:2500])
    if r.get("checklist"):
        parts.append("CHECKLIST: " + json.dumps(r["checklist"], ensure_ascii=False)[:2000])
    if r.get("consults"):
        parts.append("THINKING-MODEL ADVICE: " + json.dumps(r["consults"], ensure_ascii=False)[:3000])
    if r.get("control_log"):
        from .progress import describe
        parts.append("CONTROL DECISIONS (what stopped or questioned the execution, with the exact trigger):\n" +
                     "\n".join(describe(e) for e in r["control_log"][-20:]))
    if r.get("progress"):
        parts.append("PROGRESS PER STEP (✓ progress, · none):\n" + "\n".join(
            f"#{t['step']} {'✓' if t.get('progress') else '·'} {t.get('why', '')[:160]}" for t in r["progress"][-30:]))
    q = r.get("quality") or {}
    if q.get("qa_issues") or q.get("review") or q.get("skipped"):
        parts.append("QUALITY: " + json.dumps(q, ensure_ascii=False, default=str)[:3000])
    lines = []
    for i, row in enumerate(r.get("work_log") or [], 1):
        if "note" in row:
            lines.append(f"{i}. NOTE {row['note'][:500]}")
        else:
            lines.append(f"{i}. {row['tool']} {row['args'][:300]} -> {'ok' if row.get('ok', True) else 'FAILED'}: "
                         f"{(row.get('result') or '')[:400]}")
    parts.append("WORK LOG:\n" + "\n".join(lines))
    parts.append(f"FINAL ANSWER: {r.get('final_answer', '')[:1500]}")
    text = "\n\n".join(parts)
    return text if len(text) <= limit else text[:limit // 3] + "\n…(middle of the log cut)…\n" + text[-2 * limit // 3:]


async def diagnose(rt, home, rid: str, emit=None) -> dict | None:
    """The thinking model's diagnosis, added to the saved report (runs in the background after the task)."""
    from ..types import Message
    from .thinking import DIAGNOSE, parse_diagnosis
    try:
        r = get(home, rid)
    except KeyError:
        return None
    model = rt.thinker()
    try:
        resp = await model.chat([Message("user", DIAGNOSE.format(report=summary_text(r)))], None, temperature=0,
                                max_tokens=1200)
        dg = parse_diagnosis(resp.message.text())
    except Exception as e:  # noqa: BLE001
        log.info("failure diagnosis failed: %s", e)
        dg = None
    r["diagnosis"] = dg or {"category": "unknown", "summary": "（思維模型無法完成診斷）", "evidence": [],
                            "improvements": [], "failed_at": "", "retry_advice": ""}
    r["diagnosis"]["model"] = getattr(model, "label", "")
    save(home, r)
    if emit:
        emit("failure_diagnosed", {"id": rid, "session_id": r.get("session_id"), "diagnosis": r["diagnosis"]})
    return r["diagnosis"]


def to_regression(home, rid: str) -> dict:
    """A regression eval from the failed task: it passes when the task is done and meets the diagnosis' bar."""
    from ..evals import add_regression
    r = get(home, rid)
    dg = r.get("diagnosis") or {}
    rubric = "The task the user asked for is actually completed and the answer reports it truthfully."
    if dg.get("summary"):
        rubric += f" It previously failed because: {dg['summary']} - the result must not fail that way."
    task = add_regression(home, {"prompt": r["request"], "graders": [{"type": "status", "is": "done"},
                                                                      {"type": "llm", "rubric": rubric}],
                                 "source": {"type": "failure", "ref": rid}})
    r["regression"] = task["id"]
    save(home, r)
    return task
