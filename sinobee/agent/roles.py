"""Elephant and goldfish (the Elephant-Goldfish Model, google/elephant-goldfish-model): the THINKING model is the
elephant - it designs the plan and advises, from a compact record of what matters (plan, decisions, results) - and
must be able to do that. It does not have to be the model that runs the steps.

thinking_fitness()  whether the thinking model can play the elephant: not the execution model's own small local model,
                    and a window of at least agent.thinking_min_window. A weak elephant is not refused (offline and
                    private use are legitimate): SinoBee warns, consults it with a short brief instead of the whole
                    conversation (consult_brief), and checks the plan by program (concretize_outputs).
concretize_outputs  every milestone output gets a file path: a small execution model then writes its result where
                    the evidence check finds it, instead of into its reply (which is not evidence)."""
from __future__ import annotations

import re

PATH_RE = re.compile(r"[\w\-./]+\.(md|txt|html?|svg|png|jpe?g|json|csv|py|js|ts|pdf|xlsx|docx|pptx|mp4)\b", re.I)


def thinking_fitness(rt) -> dict:
    """{"weak": bool, "reasons": [str], "thinker": label, "window": tokens} for the run's thinking model."""
    from .context import context_window
    thinker, executor = rt.thinker(), rt.provider
    t_label, e_label = str(getattr(thinker, "label", "")), str(getattr(executor, "label", ""))
    floor = int(rt.config.get("agent.thinking_min_window", 32768) or 0)
    window, _ = context_window(rt.config, t_label)
    reasons = []
    is_local = rt.is_local(thinker)
    if is_local and (thinker is executor or t_label == e_label):
        reasons.append(f"思維模型沿用本地執行模型 {t_label}：規劃與請教的品質受限於同一個小模型")
    elif is_local:
        reasons.append(f"思維模型 {t_label} 是本地模型：能力可能不足以規劃複雜任務")
    if floor and window < floor:
        reasons.append(f"思維模型的 context window {window:,} 小於建議的 {floor:,} tokens")
    return {"weak": bool(reasons), "reasons": reasons, "thinker": t_label, "window": window}


def _ext(text: str) -> str:
    t = text.lower()
    if "svg" in t:
        return "svg"
    if re.search(r"html|page|網頁|interactive|互動|diagram|figure|visual|圖", t):
        return "html"
    if re.search(r"csv|table of data|資料表", t):
        return "csv"
    return "md"


def concretize_outputs(plan: dict) -> int:
    """concretize_milestones() for a plan from thinking.parse_plan."""
    return concretize_milestones(((plan or {}).get("milestones") or {}).get("milestones") or [])


def concretize_milestones(ms: list[dict]) -> int:
    """Give every milestone output without a file path one (m1-1.md, m2-1.html ...), and every milestone without an
    acceptance outcome one about its file. Returns how many paths were added. The model may still choose another
    path - the evidence then names it."""
    added = 0
    for m in ms:
        files = []
        outs = m.get("outputs") or [m.get("title") or m.get("goal") or m["id"]]
        new = []
        for i, o in enumerate(outs, 1):
            found = PATH_RE.search(str(o))
            if found:
                files.append(found.group(0))
                new.append(o)
                continue
            path = f"{m['id']}-{i}.{_ext(str(o) + ' ' + str(m.get('goal') or ''))}"
            files.append(path)
            new.append(f"{o} → save as {path}")
            added += 1
        m["outputs"] = new[:6]
        m["output_files"] = files[:6]
        if not m.get("acceptance"):
            m["acceptance"] = [f"{', '.join(m['output_files'])} written"]
    return added


def consult_brief(agent, stats: dict) -> str:
    """What the elephant needs to advise, without the whole conversation: the request, the plan, the milestones,
    the checklist, and the recent steps with their results."""
    from . import failures
    plan = stats.get("plan") or {}
    parts = [f"REQUEST:\n{(stats.get('query') or '')[:2000]}"]
    if plan.get("understanding") or plan.get("approach"):
        parts.append(f"PLAN:\n{plan.get('understanding', '')}\n{plan.get('approach', '')}".strip())
    msr = getattr(agent, "_msr", None)
    if msr:
        parts.append(msr.panel())
    tail = agent._checklist_tail(stats, stats.get("step", 0))
    if tail:
        parts.append(tail)
    rows = failures.work_log(agent.history[-80:], [])[-25:]
    work = "\n".join(f"{r['tool']} {r['args'][:160]} -> {'ok' if r.get('ok', True) else 'FAILED'}: "
                     f"{(r.get('result') or '')[:240]}" if "tool" in r else f"NOTE {r['note'][:240]}" for r in rows)
    parts.append("RECENT STEPS (tool, arguments -> result):\n" + (work or "(none)"))
    last = next((m.text() for m in reversed(agent.history) if m.role == "assistant" and m.text().strip()), "")
    if last:
        parts.append(f"LAST REPLY OF THE EXECUTION MODEL:\n{last[:1500]}")
    return "\n\n".join(parts)
