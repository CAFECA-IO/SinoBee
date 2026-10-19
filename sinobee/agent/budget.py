"""Task budgets (phase 3 of the workflow proposal): what a task of this type usually costs, soft checkpoints while it
runs, a hard cap per task, and a partial delivery when a task stops before it is done.

  baseline     from real runs of the same task type (the plan's task_type): p50 / p90 of tokens and cost, once
               agent.budget.min_samples runs exist. Before that - cold start, an unknown type - a SYNTHETIC baseline:
               the plan's estimated steps x the average usage per step x 2 (later steps cost more: the context
               grows). The reference R is p90 (history) or the synthetic value
  checkpoints  when tokens or cost pass 50% and 80% of R, the thinking model compares the spending with the
               milestones / checklist done and says on_track, behind or at_risk. Only a warning: the task goes on
               (an at_risk verdict also leaves the execution model a short note)
  hard cap     usage.task_max_usd / usage.task_max_tokens, or automatic: p95 x 1.5 of large tasks (planned with
               milestones) once there is data, else agent.budget.initial_max_task_usd / _tokens. At the cap the task
               pauses and asks to extend it (x1.5) or stop; without an answer (or unattended) it stops
  delivery     a task that ends before it is done (cap, failure, error) hands over what is finished: the
               accepted milestones with their summaries and outputs, the files made, and the list of what is left

Every checkpoint, cap and delivery is a control entry (event "budget").
"""
from __future__ import annotations

import json
import logging
import time

log = logging.getLogger("sinobee.budget")

DEFAULTS = {"enabled": True, "checkpoints": [0.5, 0.8], "min_samples": 8, "synthetic_factor": 2.0,
            "default_step_tokens": 8000, "default_step_cost": 0.02, "initial_max_task_usd": 10.0,
            "initial_max_task_tokens": 4_000_000, "cap_extend": 1.5, "default_steps": 30}

CHECK = """You are the THINKING model of an AI agent. A task is using its budget; judge whether the spending matches the
progress. Do not do the task.

USER REQUEST:
{request}

SPENT: {spent}
REFERENCE BUDGET for a {task_type} task: {reference} ({source})
CROSSED: {pct}% of the reference
MILESTONES: {milestones}
CHECKLIST: {checklist}
STEPS SO FAR: {steps}

Reply with JSON only, texts in the user's language:
{{"verdict": "on_track" | "behind" | "at_risk", "estimate": "<how much more it likely needs, e.g. 'about 40% more'>",
  "note": "<one or two sentences for the user>", "advice": "<one sentence for the agent, only when at_risk>"}}"""


def _cfg(config) -> dict:
    return {**DEFAULTS, **((config.get("agent.budget", {}) if config is not None else {}) or {})}


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def records(store, limit: int = 400) -> list[dict]:
    """Usage of finished chat turns (state.usage, written at the end of every turn)."""
    out = []
    for r in store.runs(limit=limit):
        if r.get("kind") != "chat" or r.get("status") not in ("done", "incomplete"):
            continue
        u = (r.get("state") or {}).get("usage") or {}
        if u.get("steps"):
            out.append(u)
    return out


def baseline(store, config, task_type: str, est_steps: int | None) -> dict:
    cfg = _cfg(config)
    recs = records(store)
    same = [u for u in recs if u.get("task_type") == task_type]
    if len(same) >= int(cfg["min_samples"]):
        tok = [float(u.get("tokens") or 0) for u in same]
        cost = [float(u.get("cost_usd") or 0) for u in same]
        return {"source": "history", "n": len(same), "task_type": task_type,
                "tokens": {"p50": round(_pct(tok, .5)), "p90": round(_pct(tok, .9))},
                "cost": {"p50": round(_pct(cost, .5), 4), "p90": round(_pct(cost, .9), 4)},
                "ref_tokens": round(_pct(tok, .9)), "ref_cost": round(_pct(cost, .9), 4)}
    steps_total = sum(int(u.get("steps") or 0) for u in recs)
    if len(recs) >= 3 and steps_total:
        per_tok = sum(float(u.get("tokens") or 0) for u in recs) / steps_total
        per_cost = sum(float(u.get("cost_usd") or 0) for u in recs) / steps_total
        per_src = f"{len(recs)} 次任務的平均"
    else:
        per_tok, per_cost = float(cfg["default_step_tokens"]), float(cfg["default_step_cost"])
        per_src = "預設值"
    steps = int(est_steps or cfg["default_steps"])
    f = float(cfg["synthetic_factor"])
    return {"source": "synthetic", "n": len(same), "task_type": task_type, "est_steps": steps,
            "per_step": {"tokens": round(per_tok), "cost": round(per_cost, 5), "from": per_src}, "factor": f,
            "ref_tokens": round(steps * per_tok * f), "ref_cost": round(steps * per_cost * f, 4)}


def hard_cap(store, config) -> dict:
    """The per-task cap: the user's setting, else p95 x 1.5 of large (milestone) tasks, else the initial values."""
    cfg = _cfg(config)
    usd, tok = config.get("usage.task_max_usd"), config.get("usage.task_max_tokens")
    if usd or tok:
        return {"source": "setting", "usd": float(usd) if usd else None, "tokens": int(tok) if tok else None}
    large = [u for u in records(store) if u.get("milestones")]
    if len(large) >= int(cfg["min_samples"]):
        return {"source": "large_p95", "n": len(large),
                "usd": round(_pct([float(u.get("cost_usd") or 0) for u in large], .95) * 1.5, 4) or None,
                "tokens": round(_pct([float(u.get("tokens") or 0) for u in large], .95) * 1.5) or None}
    return {"source": "initial", "usd": float(cfg["initial_max_task_usd"]),
            "tokens": int(cfg["initial_max_task_tokens"])}


def estimate_steps(plan: dict | None) -> int | None:
    if not plan:
        return None
    ms = (plan.get("milestones") or {}).get("milestones") if isinstance(plan.get("milestones"), dict) else None
    if ms:
        return sum(int(m.get("estimate_steps") or 10) for m in ms)
    return len(plan.get("steps") or []) * 3 or None


def money(v: float) -> str:
    return f"${v:,.2f}" if v >= 0.1 else f"${v:,.4f}"


class Guard:
    """Per turn: watches the turn's usage against the baseline and the hard cap."""

    def __init__(self, agent, stats: dict):
        self.agent = agent
        self.cfg = _cfg(agent.config)
        plan = stats.get("plan")
        self.task_type = (plan or {}).get("task_type") or "other"
        self.est = estimate_steps(plan)
        from ..work.hub import get_hub
        store = get_hub(agent.rt).store
        self.base = baseline(store, agent.config, self.task_type, self.est)
        self.cap = hard_cap(store, agent.config)
        self.crossed: set[float] = set()
        self.extra_cost = 0.0                      # sub-agents' spending (their own usage scopes)
        self.extended = 0

    def spent(self) -> dict:
        from ..accounting import current_scope
        s = current_scope.get()
        return {"tokens": int(s.input_tokens + s.output_tokens), "cost_usd": round(s.cost_usd + self.extra_cost, 6)}

    def summary(self) -> dict:
        return {"task_type": self.task_type, "baseline": self.base, "cap": self.cap, "spent": self.spent(),
                "checkpoints": sorted(self.crossed), "extended": self.extended}

    def _ratio(self, sp: dict) -> float:
        r = []
        if self.base.get("ref_tokens"):
            r.append(sp["tokens"] / self.base["ref_tokens"])
        if self.base.get("ref_cost"):
            r.append(sp["cost_usd"] / self.base["ref_cost"])
        return max(r) if r else 0.0

    def ref_text(self) -> str:
        b = self.base
        if b["source"] == "history":
            return (f"p50 {b['tokens']['p50']:,} tokens／{money(b['cost']['p50'])}、p90 {b['tokens']['p90']:,} tokens／"
                    f"{money(b['cost']['p90'])}（{b['n']} 次 {self.task_type} 任務）")
        return (f"合成基準 {b['ref_tokens']:,} tokens／{money(b['ref_cost'])}（估計 {b['est_steps']} 步 × 每步 "
                f"{b['per_step']['tokens']:,} tokens／{money(b['per_step']['cost'])}（{b['per_step']['from']}）× "
                f"{b['factor']:g}；{self.task_type} 類任務只有 {b['n']} 筆紀錄）")

    async def check(self, stats: dict) -> str | None:
        """After a step. Returns "cap" when the hard cap is reached (the loop then pauses the task)."""
        if not self.cfg["enabled"]:
            return None
        sp = self.spent()
        cap_hit = (self.cap.get("usd") and sp["cost_usd"] >= self.cap["usd"]) or \
            (self.cap.get("tokens") and sp["tokens"] >= self.cap["tokens"])
        if cap_hit:
            return "cap"
        ratio = self._ratio(sp)
        for cp in sorted(float(c) for c in self.cfg["checkpoints"]):
            if ratio >= cp and cp not in self.crossed:
                self.crossed.add(cp)
                await self._checkpoint(cp, ratio, sp, stats)
                break
        return None

    async def _checkpoint(self, cp: float, ratio: float, sp: dict, stats: dict) -> None:
        from ..tools import checklist_tools
        from ..types import Message
        from . import thinking
        a = self.agent
        msr = getattr(a, "_msr", None)
        ms_txt, ms_user = "(not a milestone task)", ""
        if msr:
            done, total = msr.plan.progress()
            ms_txt = f"{done}/{total} done: " + "; ".join(f"[{m['status']}] {m['id']} {m['title']}"
                                                         for m in msr.plan.milestones)
            ms_user = f"里程碑完成 {done}/{total}"
        items = checklist_tools.get(a.ctx)
        ck = f"{sum(1 for i in items if i['status'] == 'done')}/{len(items)} done" if items else "(none)"
        prompt = CHECK.format(request=(stats.get("query") or "")[:3000], task_type=self.task_type,
                              spent=f"{sp['tokens']:,} tokens, {money(sp['cost_usd'])}", reference=self.ref_text(),
                              source=self.base["source"], pct=int(ratio * 100), milestones=ms_txt, checklist=ck,
                              steps=stats.get("step", 0))
        resp = await a._thinking_call("預算檢查", [Message("user", prompt)], 400, stats)
        data = thinking.parse_json(resp.message.text()) if resp else {}
        verdict = data.get("verdict") if data.get("verdict") in ("on_track", "behind", "at_risk") else "unknown"
        label = {"on_track": "進度符合花費", "behind": "進度落後於花費", "at_risk": "可能超出預算",
                 "unknown": "思維模型沒有評估"}[verdict]
        detail = (f"已用 {sp['tokens']:,} tokens／{money(sp['cost_usd'])}，達參考預算 {int(cp * 100)}%"
                  f"（實際 {int(ratio * 100)}%；{self.ref_text()}）。{ms_user or '驗收清單完成 ' + ck.replace(' done', '')}。"
                  f"思維模型：{label}" + (f"，{data.get('estimate')}" if data.get("estimate") else "")
                  + (f"。{data.get('note')}" if data.get("note") else ""))
        a._control(stats, "budget", f"budget_{int(cp * 100)}", detail, decision=verdict)
        if verdict == "at_risk" and data.get("advice"):
            a.history.append(Message("user", f"[預算提醒] 已用參考預算的 {int(ratio * 100)}%。{data['advice']}"
                                             "（這只是提醒，照計畫繼續；先完成最重要的部分。）"))
            a._persist()

    def extend(self) -> None:
        f = float(self.cfg["cap_extend"])
        self.cap = {**self.cap, "usd": round(self.cap["usd"] * f, 4) if self.cap.get("usd") else None,
                    "tokens": int(self.cap["tokens"] * f) if self.cap.get("tokens") else None, "source": "extended"}
        self.extended += 1

    def cap_text(self) -> str:
        parts = []
        if self.cap.get("usd"):
            parts.append(money(self.cap["usd"]))
        if self.cap.get("tokens"):
            parts.append(f"{self.cap['tokens']:,} tokens")
        src = {"setting": "設定", "large_p95": "大型任務 p95×1.5", "initial": "初始值", "extended": "已追加"}
        return "／".join(parts) + f"（{src.get(self.cap.get('source'), self.cap.get('source'))}）"


# ------------------------------------------------------------------ partial delivery
def partial_delivery(agent, stats: dict, status: str, reason: str) -> dict | None:
    """What a task that ended before it was done can hand over: accepted milestones (summary, outputs), the files
    it made, and what is left. None when nothing was finished."""
    from ..tools import checklist_tools
    msr = getattr(agent, "_msr", None)
    done, left = [], []
    if msr:
        for m in msr.plan.milestones:
            if m["status"] == "done":
                done.append({"id": m["id"], "title": m["title"], "summary": (m.get("summary") or "")[:600],
                             "outputs": m.get("result_outputs") or []})
            elif m["status"] != "dropped":
                left.append({"id": m["id"], "title": m["title"], "goal": m["goal"], "status": m["status"]})
    items = checklist_tools.get(agent.ctx)
    open_items = [i for i in checklist_tools.open_items(items) if not (msr and i.get("milestone"))]
    files, seen = [], set()
    for f in stats.get("artifacts") or []:
        p = f.get("path") if isinstance(f, dict) else str(f)
        if p and p not in seen:
            seen.add(p)
            files.append({"path": p, "name": (f.get("name") if isinstance(f, dict) else None) or p.rsplit("/", 1)[-1]})
    checked = [i for i in items if i.get("status") == "done" and not (msr and i.get("milestone"))]
    if not done and not files and not checked:
        return None
    lines = [f"**部分交付**（任務沒有完成：{reason}）"]
    if done:
        lines.append("\n**已完成並驗收的里程碑**")
        for d in done:
            lines.append(f"- ✓ {d['id']} {d['title']}：{d['summary'] or '（無摘要）'}"
                         + (f"\n  產出：{'、'.join(d['outputs'])}" if d["outputs"] else ""))
    if checked:
        lines.append("\n**已完成的驗收項目**")
        lines += [f"- ✓ {i['text']}" + (f"（{i['evidence'][:120]}）" if i.get("evidence") else "") for i in checked]
    if files:
        lines.append("\n**這次產出的檔案**")
        lines += [f"- [{f['name']}]({f['path']})" for f in files[:20]]
    if left or open_items:
        lines.append("\n**尚未完成**")
        lines += [f"- [ ] {m['id']} {m['title']}：{m['goal']}" for m in left]
        lines += [f"- [ ] {i['text']}" for i in open_items]
    lines.append("\n回覆「繼續」會" + (f"從 {left[0]['id']} 接著做" if left else "接著完成剩下的部分") + "，已完成的部分不會重做。")
    return {"status": status, "reason": reason, "done": done, "checked": [i["text"] for i in checked],
            "files": files, "left": left, "open_items": [i["text"] for i in open_items], "text": "\n".join(lines),
            "t": time.time()}


def usage_record(guard: Guard | None, stats: dict, turn_usage: dict, steps: int) -> dict:
    """What every finished turn records for the baselines (run state.usage)."""
    plan = stats.get("plan") or {}
    return {"task_type": (guard.task_type if guard else plan.get("task_type")) or "other",
            "tokens": int((turn_usage.get("input_tokens") or 0) + (turn_usage.get("output_tokens") or 0)),
            "cost_usd": round(float(turn_usage.get("cost_usd") or 0) + (guard.extra_cost if guard else 0), 6),
            "steps": steps, "milestones": bool(plan.get("milestones")), "planned": bool(plan),
            "est_steps": guard.est if guard else None}


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)
