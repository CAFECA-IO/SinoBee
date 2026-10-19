"""The thinking model's jobs. SinoBee splits a task between two roles instead of a "main" and a "stronger" model:

  thinking model  (model.thinking)  plans before any step runs, advises when the execution gets stuck, judges the
                                    result (acceptance review, unless model.reviewer is set) and diagnoses a failure.
                                    It never calls tools and never takes a step of the task.
  execution model (model.default)   runs every step of the task - tool calls, files, answers - from the first step
                                    to the last, in the main agent and in sub-agents.

Neither replaces the other during a task. When the thinking model decides that the execution model cannot do the
task (or the execution stays stuck after the advice allowed), the task ends as failed and a failure report with
the work log and a diagnosis is saved for improving the system (agent/failures.py). Without model.thinking the
execution model also does the thinking - in separate calls with these prompts, never mixed into its steps.
"""
from __future__ import annotations

import json
import re

PLAN = """You are the THINKING model of an AI agent. Another model - the EXECUTION model - will carry out the task
step by step with tools; you do not call tools. Before it starts, think the task through and write the plan it
will follow.

USER REQUEST:
{request}

{context}
TOOLS THE EXECUTION MODEL HAS: {tools}
{skills}
Write, in the user's language:
- understanding: what the user actually wants, in one or two sentences (state assumptions where it is ambiguous)
- approach: how to do it - which skill or tools, in what order, and why
- steps: 3-10 concrete steps the execution model can follow, each a single action or check
- checklist: 2-8 acceptance items - OUTCOMES that must be true for the result to be right (not activities), each
  with how it is checked
- risks: the traps most likely to make the result wrong (e.g. numbers that must come from the source, a format the
  user asked for)
- ask_user: a question only the user can answer and without which the work would likely be wasted, else null
Keep it short; do not do the task.
Reply with JSON only:
{{"understanding": "...", "approach": "...", "steps": ["..."], "checklist": [{{"text": "...", "check": "..."}}],
  "risks": ["..."], "ask_user": null}}"""

CONSULT = """[SinoBee - THINKING model] You are the thinking model supervising the EXECUTION model whose work is
above. It is stuck: {why}. You do not call tools. Read the whole conversation and every tool result, then reply in
the user's language with:
(1) what is actually going wrong,
(2) the next 2-4 steps to take, with the exact tools / commands / arguments when you can,
(3) what it must stop doing.
Then decide whether the execution model can still finish this task. Choose stop when it has already received advice
and keeps failing the same way, when the task needs an ability it has shown it lacks (e.g. it cannot produce a
valid tool call, keeps misreading results, cannot follow the procedure), or when a missing tool, permission or
external condition blocks it. Otherwise choose continue.
End with exactly one last line:
DECISION: continue
or
DECISION: stop - <one sentence: why the execution model cannot finish, in the user's language>"""

DIAGNOSE = """You are the THINKING model of an AI agent. A task FAILED. Diagnose it for the engineers who improve the
system: what went wrong and what change would most likely make it succeed next time. Judge only from the record.

{report}

Categories:
  execution_capability  the execution model was not able to do it (invalid tool calls, lost track, ignored results,
                        could not follow the procedure) although tools and instructions were adequate
  skill_defect          a skill's instructions, scripts or examples were wrong, missing or misleading
  missing_tool          a tool, permission or integration the task needs does not exist or was not enabled
  environment           a dependency, network, file system or service failed
  unclear_request       the request was ambiguous or contradictory and should have been clarified
  external_blocker      captcha, login, quota, paywall, outage - nothing the agent could fix
  system_bug            SinoBee itself misbehaved (crash, wrong guard, bad prompt)
Reply with JSON only:
{{"category": "<one of the above>", "summary": "<what happened, 1-2 sentences, user's language>",
  "failed_at": "<the step or moment it went wrong>", "evidence": ["<quotes from the record>"],
  "improvements": [{{"target": "model | skill:<name> | tool:<name> | prompt | settings | sinobee", "change": "<concrete change>"}}],
  "retry_advice": "<what the user can do now, user's language>"}}"""


def _json(text: str) -> dict:
    m = re.search(r"\{[\s\S]*\}", text or "")
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def parse_plan(text: str) -> dict | None:
    data = _json(text)
    steps = [str(s).strip() for s in data.get("steps") or [] if str(s).strip()][:10]
    items = []
    for it in data.get("checklist") or []:
        if isinstance(it, str):
            it = {"text": it}
        if isinstance(it, dict) and str(it.get("text") or "").strip():
            items.append({"text": str(it["text"]).strip()[:200], "check": str(it.get("check") or "").strip()[:200]})
    if not steps and not data.get("approach"):
        return None
    ask = data.get("ask_user")
    return {"understanding": str(data.get("understanding") or "").strip()[:600],
            "approach": str(data.get("approach") or "").strip()[:1200], "steps": steps, "checklist": items[:8],
            "risks": [str(r).strip()[:300] for r in data.get("risks") or [] if str(r).strip()][:6],
            "ask_user": str(ask).strip()[:400] if ask and str(ask).strip().lower() not in ("null", "none") else None}


def render_plan(plan: dict, model_label: str) -> str:
    lines = [f"[思維模型的計畫]（{model_label}）以下是開工前想好的做法，請依此執行；執行中發現計畫有誤時，說明理由後調整。"]
    if plan.get("understanding"):
        lines.append(f"理解：{plan['understanding']}")
    if plan.get("approach"):
        lines.append(f"做法：{plan['approach']}")
    if plan.get("steps"):
        lines.append("步驟：\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(plan["steps"], 1)))
    if plan.get("risks"):
        lines.append("注意：\n" + "\n".join(f"- {r}" for r in plan["risks"]))
    if plan.get("ask_user"):
        lines.append(f"開始前先用 ask_user 問使用者：{plan['ask_user']}")
    if plan.get("checklist"):
        lines.append("驗收清單已依計畫建立（task_checklist），完成每一項都要附證據。")
    return "\n".join(lines)


DECISION = re.compile(r"DECISION:\s*(continue|stop)\s*(?:[-–—:]\s*(.*))?\s*$", re.I)


def parse_decision(text: str) -> tuple[str, str, str]:
    """(advice without the decision line, "continue" | "stop", reason)."""
    text = (text or "").strip()
    lines = text.splitlines()
    for i in range(len(lines) - 1, max(-1, len(lines) - 4), -1):
        m = DECISION.search(lines[i].strip().strip("*`"))
        if m:
            advice = "\n".join(lines[:i]).strip()
            return advice, m.group(1).lower(), (m.group(2) or "").strip()
    return text, "continue", ""


def parse_diagnosis(text: str) -> dict | None:
    data = _json(text)
    if not data.get("category") and not data.get("summary"):
        return None
    cats = ("execution_capability", "skill_defect", "missing_tool", "environment", "unclear_request",
            "external_blocker", "system_bug")
    cat = str(data.get("category") or "").strip()
    return {"category": cat if cat in cats else "unknown", "summary": str(data.get("summary") or "")[:600],
            "failed_at": str(data.get("failed_at") or "")[:400],
            "evidence": [str(e)[:300] for e in data.get("evidence") or []][:6],
            "improvements": [i for i in data.get("improvements") or [] if isinstance(i, dict)][:6],
            "retry_advice": str(data.get("retry_advice") or "")[:400]}
