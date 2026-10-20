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
        "plan": stats.get("plan"), "plan_failed": stats.get("plan_failed"),
        "thinking_skipped": (stats.get("thinking_skipped") or [])[:10], "consults": stats.get("consult_log", []),
        "checklist": checklist_tools.get(agent.ctx), "steps": steps, "tool_calls": stats.get("tool_calls", 0),
        "errors": stats.get("errors", 0), "last_error": _scrub(stats.get("last_error", ""), secrets),
        "quality": {"gate_rounds": stats.get("gate_rounds"), "qa_issues": (stats.get("qa_issues") or [])[:20],
                    "review": stats.get("review"), "level": (stats.get("quality") or [None])[0],
                    "skipped": stats.get("quality_skipped") or []},
        "control_log": stats.get("control_log") or [],
        "empty_replies": stats.get("empty_replies") or [],
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
    from .progress import cost_text
    """The record as the thinking model reads it for the diagnosis."""
    parts = [f"REQUEST: {r.get('request', '')}", f"STATUS: {r.get('status')} - {json.dumps(r.get('reason'), ensure_ascii=False)}",
             f"MODELS: {json.dumps(r.get('models'), ensure_ascii=False)}", f"SKILLS: {', '.join(r.get('skills') or [])}"]
    if r.get("plan"):
        parts.append("PLAN: " + json.dumps(r["plan"], ensure_ascii=False)[:2500])
    elif r.get("plan_failed"):
        parts.append(f"PLAN: none - the thinking model gave no usable plan ({r['plan_failed']}), so the task ran "
                     "without a plan or milestones")
    if r.get("thinking_skipped"):
        parts.append("THINKING-MODEL CALLS THAT FAILED: " + "; ".join(map(str, r["thinking_skipped"]))[:800])
    if r.get("checklist"):
        parts.append("CHECKLIST: " + json.dumps(r["checklist"], ensure_ascii=False)[:2000])
    if r.get("consults"):
        parts.append("THINKING-MODEL ADVICE: " + json.dumps(r["consults"], ensure_ascii=False)[:3000])
    if r.get("control_log"):
        from .progress import describe
        parts.append("CONTROL DECISIONS (what stopped or questioned the execution, with the exact trigger):\n" +
                     "\n".join(describe(e) for e in r["control_log"][-20:]))
    if r.get("empty_replies"):
        parts.append("EMPTY REPLIES (the model answered nothing; finish=length means its output budget ran out first):\n"
                     + "\n".join(f"#{e.get('step')} finish={e.get('finish')} max_tokens={e.get('max_tokens')} "
                                  f"output_tokens={e.get('output_tokens')} {e.get('seconds')}s" for e in r["empty_replies"][-10:]))
    if r.get("progress"):
        parts.append("PROGRESS PER STEP (✓ progress, · none):\n" + "\n".join(
            f"#{t['step']} {'✓' if t.get('progress') else '·'} {t.get('why', '')[:160]}{cost_text(t.get('cost'))}"
            for t in r["progress"][-30:]))
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


RULES = [
    (r"(temperature|top_p|top_k|max_tokens|max_completion_tokens|tool_choice|parallel_tool_calls|reasoning_effort)"
     r"\W{0,3}[^\n]{0,60}?(deprecated|not supported|unsupported|not allowed|no longer supported)|"
     r"(unsupported|unknown|unrecognized) (parameter|argument|field)",
     "provider_compat", "模型 {model} 不接受 SinoBee 送出的參數{param}，第一個模型呼叫就被拒絕",
     {"target": "sinobee", "change": "供應商層對不接受的參數自動適應（providers/compat.py 已處理 temperature）；"
                                     "其他參數需要在對應的 provider 依模型拿掉或改名"},
     "重試一次：SinoBee 已記住這個模型的限制。若仍失敗，暫時換用其他模型並回報給開發者。"),
    (r"SinoBee\.ai quota|HTTP 429|rate.?limit|RESOURCE_EXHAUSTED|insufficient_quota", "external_blocker",
     "模型服務的額度或速率限制用完了", {"target": "settings", "change": "設定備援模型，或提高額度"},
     "等額度恢復，或改用備援模型。"),
    (r"HTTP 40[13]|invalid[_ ]api[_ ]key|unauthorized|sign-in expired", "environment",
     "模型服務的金鑰或登入無效", {"target": "settings", "change": "重新設定金鑰或登入 SinoBee.ai"},
     "到設定重新貼上金鑰或重新登入後再試。"),
    (r"timed out: (?:ReadTimeout|ReadError)", "execution_capability", "模型在時限內沒有回完：一次要產生的內容太長",
     {"target": "skill", "change": "大型檔案分段寫入（先寫骨架，再逐段 edit_file），不要一次寫完整頁"},
     "重試時請它分段寫檔。"),
    (r"network error|ConnectError|timed out|Timeout|HTTP 50[234]", "environment", "連不到模型服務或服務暫時故障",
     {"target": "settings", "change": "設定備援模型，網路不穩時自動切換"}, "確認網路與供應商狀態後重試。"),
    (r"context.{0,20}(length|window)|maximum context|prompt is too long", "system_bug", "對話內容超過模型的上下文長度",
     {"target": "sinobee", "change": "檢查這個模型的上下文上限設定與壓縮門檻"}, "開新對話，或把上下文上限設成模型實際值。"),
]


def rule_diagnosis(r: dict) -> dict:
    """A diagnosis from the record alone, when no model can write one (e.g. the model itself is what failed)."""
    reason = r.get("reason") or {}
    text = "\n".join(str(x) for x in (reason.get("summary"), reason.get("detail"), r.get("last_error"),
                                        r.get("final_answer")) if x)
    kind = reason.get("kind")
    models = r.get("models") or {}
    for rx, cat, summary, imp, advice in RULES:
        m = re.search(rx, text, re.I)
        if m:
            pm = re.search(r"(temperature|top_p|top_k|max_tokens|max_completion_tokens|tool_choice|"
                           r"parallel_tool_calls|reasoning_effort)", text)
            mm = re.search(r"([\w.-]+/[\w.:/-]+) HTTP", text)
            return {"category": cat, "summary": summary.format(model=(mm.group(1) if mm else models.get("execution") or ""),
                                                               param=f" {pm.group(1)}" if pm else ""),
                    "failed_at": f"第 {r.get('steps') or 1} 步的模型呼叫", "evidence": [m.group(0)[:200]],
                    "improvements": [imp], "retry_advice": advice}
    by_kind = {"safety_valve": ("system_bug", "單一 Agent 達到 300 步安全閥，代表進度偵測或規劃有缺陷"),
               "budget": ("external_blocker", "達到每個任務的花費上限，任務暫停"),
               "capability": ("execution_capability", reason.get("summary") or "執行模型無法完成這個任務")}
    if kind in by_kind:
        cat, summary = by_kind[kind]
        return {"category": cat, "summary": summary, "failed_at": "", "evidence": [str(reason.get("detail") or "")[:200]],
                "improvements": [], "retry_advice": reason.get("hint") or ""}
    return {"category": "unknown", "summary": reason.get("summary") or "（沒有模型可以診斷，紀錄中也找不到已知的原因）",
            "failed_at": "", "evidence": [], "improvements": [], "retry_advice": reason.get("hint") or ""}


async def diagnose(rt, home, rid: str, emit=None) -> dict | None:
    """The thinking model's diagnosis, added to the saved report (runs in the background after the task). When
    the thinking model cannot do it, the execution model tries; when no model can (the model itself is what
    failed), a diagnosis from the record (rule_diagnosis) - never an empty "unknown"."""
    from ..types import Message
    from .thinking import DIAGNOSE, parse_diagnosis
    try:
        r = get(home, rid)
    except KeyError:
        return None
    tried, dg, errors = set(), None, []
    for model in (rt.thinker(), rt.provider):
        label = getattr(model, "label", "")
        if model is None or label in tried:
            continue
        tried.add(label)
        try:
            resp = await model.chat([Message("user", DIAGNOSE.format(report=summary_text(r)))], None, temperature=0,
                                    max_tokens=1200)
            dg = parse_diagnosis(resp.message.text())
        except Exception as e:  # noqa: BLE001
            log.info("failure diagnosis with %s failed: %s", label, e)
            errors.append(f"{label}: {str(e)[:160]}")
            dg = None
        if dg:
            dg["model"] = label
            break
    if not dg:
        dg = rule_diagnosis(r)
        dg["model"] = "rules"
        if errors:
            dg["note"] = "模型無法完成診斷（" + "；".join(errors) + "），以下依紀錄判斷"
    r["diagnosis"] = dg
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
