"""Run reports: every top-level task, finished or not, leaves a report of what it cost and what it produced.

    $SINOBEE_HOME/run_reports/<id>.json

A failure report (agent/failures.py) explains one failed task in depth; a run report is kept for all of them, so the
ones that spent the most for the least can be found even when they "finished":

  cost      tokens, money, share of the SinoBee.ai 6-hour quota, time on models and on tools, calls sent again
  output    files written or produced, acceptance items done, steps that produced something
  steps     the cost of every step (agent/loop.py _step_cost) next to the progress verdict, and the most expensive
  waste     tokens per unit of output (files + items done); a task with nothing to show ranks first

Listed in Settings > Quality ("任務效率") and with `sinobee runs`.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from . import failures

log = logging.getLogger("sinobee.runs")
MAX_REPORTS = 500
SAFE = re.compile(r"^[\w.-]{1,80}$")


def _dir(home) -> Path:
    p = Path(home) / "run_reports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def outputs(agent, stats: dict) -> dict:
    trace = stats.get("progress_trace") or []
    written = set()
    tr = getattr(agent, "_progress", None)
    if tr is not None:
        written = {rec.get("path") or real for real, rec in tr.files.items() if rec.get("history")}
    produced = {a.get("path") or a.get("name") for a in stats.get("artifacts") or [] if isinstance(a, dict)}
    ws = Path(str(agent.config.workspace)).resolve()

    def short(f: str) -> str:
        try:
            return str(Path(f).resolve().relative_to(ws))
        except (ValueError, OSError):
            return f
    files = sorted({short(f) for f in written | produced if f})
    from ..tools import checklist_tools
    items = checklist_tools.get(agent.ctx)
    done = sum(1 for i in items if i.get("status") == "done")
    return {"files": files[:50], "n_files": len(files), "checklist_done": done, "checklist_total": len(items),
            "output_steps": sum(1 for t in trace if t.get("output"))}


def step_costs(trace: list[dict]) -> list[dict]:
    return [{"step": t.get("step"), "progress": t.get("progress"), "produced": bool(t.get("output")),
             "failed": t.get("failed", False), "why": (t.get("why") or "")[:200], **(t.get("cost") or {})}
            for t in trace]


def efficiency(usage: dict, out: dict) -> dict:
    tokens = int(usage.get("input_tokens", 0) or 0) + int(usage.get("output_tokens", 0) or 0)
    units = out["n_files"] + out["checklist_done"]
    return {"tokens": tokens, "cost_usd": usage.get("cost_usd", 0), "quota_6h_pct": usage.get("quota_6h_pct"),
            "units": units, "tokens_per_output": round(tokens / units) if units else None,
            "no_output": units == 0, "waste": round(tokens / (1 + units))}


def build(agent, *, status: str, error_info: dict | None, final_text: str, query: str, stats: dict,
          turn_start: int, turn_id, usage: dict, steps: int, duration: float, failure_id: str | None) -> dict:
    base = failures.build(agent, status=status, error_info=error_info, final_text=final_text, query=query,
                          stats=stats, turn_start=turn_start, turn_id=turn_id, usage=usage, steps=steps,
                          duration=duration)
    base.pop("diagnosis", None)
    trace = stats.get("progress_trace") or []
    out = outputs(agent, stats)
    sc = step_costs(trace)
    top = sorted((s for s in sc if s.get("tokens") is not None), key=lambda s: -(s.get("tokens") or 0))[:3]
    model_s = round(sum(s.get("model_s") or 0 for s in sc), 1)
    tools_s = round(sum(s.get("tools_s") or 0 for s in sc), 1)
    retries = sum(s.get("retries") or 0 for s in sc)
    base.update({
        "kind": "run", "failure_id": failure_id, "outputs": out, "step_costs": sc[-200:],
        "top_steps": [s["step"] for s in top], "efficiency": efficiency(usage, out),
        "time": {"total_s": duration, "model_s": model_s, "tools_s": tools_s}, "retries": retries,
    })
    base["progress"] = trace[-80:]
    return base


def save(home, report: dict) -> dict:
    d = _dir(home)
    (d / f"{report['id']}.json").write_text(json.dumps(report, ensure_ascii=False, default=str), encoding="utf-8")
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


def row(r: dict) -> dict:
    eff, out = r.get("efficiency") or {}, r.get("outputs") or {}
    top = next((s for s in r.get("step_costs") or [] if r.get("top_steps") and s.get("step") == r["top_steps"][0]), None)
    return {"id": r["id"], "created": r.get("created"), "status": r.get("status"), "session_id": r.get("session_id"),
            "request": (r.get("request") or "")[:160], "model": (r.get("models") or {}).get("execution"),
            "steps": r.get("steps"), "duration_s": r.get("duration_s"), "time": r.get("time"), "retries": r.get("retries", 0),
            "tokens": eff.get("tokens"), "cost_usd": eff.get("cost_usd"), "quota_6h_pct": eff.get("quota_6h_pct"),
            "files": out.get("n_files", 0), "checklist": f"{out.get('checklist_done', 0)}/{out.get('checklist_total', 0)}",
            "output_steps": out.get("output_steps", 0), "no_output": eff.get("no_output"), "waste": eff.get("waste", 0),
            "tokens_per_output": eff.get("tokens_per_output"), "failure_id": r.get("failure_id"),
            "top_step": {k: top.get(k) for k in ("step", "tokens", "model_s", "tools_s", "retries", "why")} if top else None}


def listing(home, *, days: float = 30, sort: str = "waste", limit: int = 50) -> list[dict]:
    since = time.time() - days * 86400
    rows = []
    for f in _dir(home).glob("*.json"):
        if f.stat().st_mtime < since:
            continue
        try:
            rows.append(row(json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, ValueError, KeyError):
            continue
    key = {"waste": lambda r: (not r["no_output"], -(r["waste"] or 0)),
           "tokens": lambda r: -(r["tokens"] or 0),
           "recent": lambda r: -(r["created"] or 0)}.get(sort) or (lambda r: -(r["created"] or 0))
    return sorted(rows, key=key)[:limit]


def summary_text(r: dict) -> str:
    """Plain text for the viewer and the CLI."""
    eff, out, t = r.get("efficiency") or {}, r.get("outputs") or {}, r.get("time") or {}
    lines = [f"任務：{r.get('request', '')[:300]}",
             f"狀態：{r.get('status')}　模型：{(r.get('models') or {}).get('execution')}",
             f"花費：{eff.get('tokens', 0):,} tokens" + (f"・${eff['cost_usd']:.4f}" if eff.get("cost_usd") else "")
             + (f"・6 小時額度 {eff['quota_6h_pct']}%" if eff.get("quota_6h_pct") else ""),
             f"時間：共 {t.get('total_s', 0)} 秒，等模型 {t.get('model_s', 0)} 秒，執行工具 {t.get('tools_s', 0)} 秒；"
             f"重送 {r.get('retries', 0)} 次",
             f"產出：{out.get('n_files', 0)} 個檔案、驗收清單完成 {out.get('checklist_done', 0)}/{out.get('checklist_total', 0)}、"
             f"有產出的步驟 {out.get('output_steps', 0)}/{r.get('steps', 0)}"
             + (f"；每單位產出 {eff['tokens_per_output']:,} tokens" if eff.get("tokens_per_output") else "；沒有任何產出")]
    if out.get("files"):
        lines.append("檔案：" + "、".join(out["files"][:12]))
    if r.get("failure_id"):
        lines.append(f"失敗報告：{r['failure_id']}")
    lines += ["", "【每步花費】（★ 最貴的 3 步；✓ 有進度；◆ 有產出）"]
    top = set(r.get("top_steps") or [])
    for s in r.get("step_costs") or []:
        mark = ("★" if s.get("step") in top else " ") + ("✓" if s.get("progress") else "·") + ("◆" if s.get("produced") else " ")
        cost = f"{s.get('tokens') or 0:>9,} tok  模型 {s.get('model_s') or 0:>5}s  工具 {s.get('tools_s') or 0:>5}s"
        if s.get("retries"):
            cost += f"  重送 {s['retries']}"
        lines.append(f"{mark} #{s.get('step')}  {cost}  {s.get('why', '')[:120]}")
    return "\n".join(lines)
