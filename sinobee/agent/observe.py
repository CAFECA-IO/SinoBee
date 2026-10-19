"""Observation and tuning (phase 4 of the workflow proposal).

  stats(...)   what the stop rules, milestones, budgets and model adaptations actually did over a period: control
               log counts by event and trigger, the thinking model's decisions, every safety-valve stop with its
               failure report, budget checkpoint verdicts, caps and partial deliveries, milestone events, model
               parameters learned, and the shape of the turns (steps, statuses, > 60 steps)
  tune(...)    recommendations for K, N per file type and the budget's synthetic factor, each from data: replayed
               turns (agent/replay.py), the live control log (how often a rule fired while the auxiliary model saw
               real progress) and the recorded usage. A recommendation is only made with enough samples; it says
               what it is based on, and apply() writes it to the settings
"""
from __future__ import annotations

import math
import time
from collections import Counter

from . import budget as B
from . import progress as P

MIN_TURNS = 20           # replayed turns before K / N are recommended
MIN_STREAKS = 10         # file-only streaks of a kind before its N is recommended


def _pct(values: list[float], q: float) -> float:
    return B._pct(values, q)


def stats(home, store, days: float = 30) -> dict:
    since = time.time() - days * 86400
    log = [e for e in P.read_log(home, limit=200000) if (e.get("t") or 0) >= since]
    by = Counter((e.get("event"), e.get("trigger")) for e in log)
    consults = [e for e in log if e.get("event") == "consult"]
    sem = [e.get("semantic") for e in consults if isinstance(e.get("semantic"), dict)]
    reports = {e.get("run_id") or e.get("session_id"): e.get("failure_id") for e in log if e.get("event") == "report"}
    safety = [{"t": e.get("t"), "session_id": e.get("session_id"), "run_id": e.get("run_id"), "detail": e.get("detail"),
               "failure_id": reports.get(e.get("run_id") or e.get("session_id"))}
              for e in log if e.get("trigger") == "safety_valve" and e.get("event") == "stop"]
    budget = [e for e in log if e.get("event") == "budget"]
    days_seen = Counter(time.strftime("%Y-%m-%d", time.localtime(e.get("t") or 0)) for e in log
                        if e.get("event") in ("consult_requested", "stop"))
    runs = [r for r in store.runs(limit=2000) if r.get("kind") == "chat" and (r.get("created_at") or 0) >= since]
    steps = [int(((r.get("state") or {}).get("usage") or {}).get("steps") or len((r.get("state") or {}).get("steps") or []))
             for r in runs]
    try:
        from ..providers import compat
        caps = compat._load()
    except Exception:  # noqa: BLE001
        caps = {}
    return {
        "days": days, "entries": len(log),
        "counts": [{"event": ev, "trigger": tr, "label": P.TRIGGERS.get(tr, tr), "n": n} for (ev, tr), n in by.most_common()],
        "per_day": sorted(days_seen.items()),
        "consults": {"n": len(consults), "decisions": dict(Counter(e.get("decision") for e in consults)),
                     "by_trigger": dict(Counter(e.get("trigger") for e in consults)),
                     "semantic_checked": len(sem), "semantic_not_spinning": sum(1 for s in sem if not s.get("spinning"))},
        "safety_valve": safety,
        "budget": {"checkpoints": dict(Counter(e.get("decision") for e in budget if str(e.get("trigger")).startswith("budget_") and e.get("trigger") not in ("budget_cap", "budget_extended"))),
                   "caps": sum(1 for e in budget if e.get("trigger") == "budget_cap"),
                   "extended": sum(1 for e in budget if e.get("trigger") == "budget_extended"),
                   "partial_deliveries": sum(1 for e in log if e.get("trigger") == "partial_delivery")},
        "milestones": dict(Counter(e.get("trigger") for e in log if e.get("event") == "milestone")),
        "models_adapted": {m: v.get("drop") for m, v in caps.items()},
        "turns": {"n": len(runs), "status": dict(Counter(r.get("status") for r in runs)),
                  "steps_p50": round(_pct(steps, .5)) if steps else 0, "steps_p90": round(_pct(steps, .9)) if steps else 0,
                  "steps_max": max(steps) if steps else 0, "over_60": sum(1 for s in steps if s > 60),
                  "planned": sum(1 for r in runs if ((r.get("state") or {}).get("usage") or {}).get("planned")),
                  "milestones": sum(1 for r in runs if ((r.get("state") or {}).get("usage") or {}).get("milestones"))},
    }


def _streaks(trace: list[dict]) -> list[tuple[str, int]]:
    """(kind, length) of every run of file-only steps in a replayed turn."""
    out, cur, kind = [], 0, None
    for t in trace:
        if t.get("file_only"):
            cur, kind = t["file_only"], t.get("kind") or kind
        elif cur:
            out.append((kind or "other", cur))
            cur, kind = 0, None
    if cur:
        out.append((kind or "other", cur))
    return out


def tune(home, store, memory, config, last: int = 200) -> dict:
    from . import replay
    rows = replay.replay_sessions(memory, config, last=last)
    ok = [r for r in rows if r.get("status") in ("done", None)]
    bad = [r for r in rows if r.get("status") in ("failed", "error", "incomplete")]
    pcfg = P.cfg(config)
    recs = []
    # ---- K: the longest run without progress in turns that finished well should stay below it
    k_now = int(pcfg["k"])
    good_np = [r["max_no_progress"] for r in ok]
    sims = {}
    for k in (8, 10, 12, 15, 20, 25, 30):
        sims[k] = {"ok_flagged": sum(1 for v in good_np if v >= k),
                   "bad_flagged": sum(1 for r in bad if r["max_no_progress"] >= k)}
    log = P.read_log(home, limit=200000)
    np_consults = [e for e in log if e.get("event") == "consult" and e.get("trigger") == "no_progress"]
    np_false = sum(1 for e in np_consults if isinstance(e.get("semantic"), dict) and not e["semantic"].get("spinning"))
    if len(ok) >= MIN_TURNS:
        p99 = _pct(good_np, .99)
        k_new = max(8, min(30, int(math.ceil(p99)) + 3))
        if np_consults and np_false / len(np_consults) > 0.3:      # the rule often fired on real progress: loosen
            k_new = min(30, max(k_new, k_now + 5))
        recs.append({"key": "agent.progress.k", "current": k_now, "suggested": k_new, "n": len(ok),
                     "confidence": "high" if len(ok) >= 100 else "medium",
                     "evidence": f"{len(ok)} 個順利完成的回合中最長的無進度連續段 p99 = {p99:.1f} 步（最長 {max(good_np)}）；"
                                 f"K={k_new} 時這些回合有 {sims.get(k_new, {}).get('ok_flagged', sum(1 for v in good_np if v >= k_new))} 個會被誤判。"
                                 + (f"實際的 {len(np_consults)} 次「無進度」請教中，輔助模型認為其實有進展的有 {np_false} 次。" if np_consults else ""),
                     "simulation": sims})
    else:
        recs.append({"key": "agent.progress.k", "current": k_now, "suggested": None, "n": len(ok),
                     "confidence": "none", "evidence": f"只有 {len(ok)} 個順利完成的回合可重放（需要 {MIN_TURNS}），先維持 {k_now}",
                     "simulation": sims})
    # ---- N per file type: the file-only streaks of turns that finished well
    streaks: dict[str, list[int]] = {}
    for r in ok:
        for kind, n in _streaks(r.get("trace") or []):
            streaks.setdefault("document" if kind == "document" else ("code" if kind == "code" else "other"), []).append(n)
    for kind, key in (("code", "n_code"), ("document", "n_document"), ("other", "n_other")):
        cur = int(pcfg[key])
        vals = streaks.get(kind, [])
        if len(vals) >= MIN_STREAKS:
            new = max(2, min(15, int(math.ceil(_pct(vals, .95))) + 1))
            recs.append({"key": f"agent.progress.{key}", "current": cur, "suggested": new, "n": len(vals),
                         "confidence": "high" if len(vals) >= 50 else "medium",
                         "evidence": f"順利完成的回合中 {kind} 檔案的「只有改檔」連續段 {len(vals)} 段，p95 = "
                                     f"{_pct(vals, .95):.1f} 步（最長 {max(vals)}）"})
        else:
            recs.append({"key": f"agent.progress.{key}", "current": cur, "suggested": None, "n": len(vals),
                         "confidence": "none", "evidence": f"{kind} 只有 {len(vals)} 段資料（需要 {MIN_STREAKS}），先維持 {cur}"})
    # ---- budget: how far real usage was from the synthetic estimate
    bcfg = B._cfg(config)
    usage = B.records(store, limit=2000)
    steps_total = sum(int(u.get("steps") or 0) for u in usage)
    per_tok = (sum(float(u.get("tokens") or 0) for u in usage) / steps_total) if steps_total else bcfg["default_step_tokens"]
    ratios = [float(u["tokens"]) / (int(u["est_steps"]) * per_tok) for u in usage
              if u.get("est_steps") and u.get("tokens") and per_tok]
    f_now = float(bcfg["synthetic_factor"])
    if len(ratios) >= int(bcfg["min_samples"]):
        f_new = round(max(1.2, min(4.0, _pct(ratios, .9))), 1)
        recs.append({"key": "agent.budget.synthetic_factor", "current": f_now, "suggested": f_new, "n": len(ratios),
                     "confidence": "medium" if len(ratios) < 50 else "high",
                     "evidence": f"{len(ratios)} 個有估計步數的回合：實際用量 ÷（估計步數 × 平均每步 {per_tok:,.0f} tokens）的 "
                                 f"p50 = {_pct(ratios, .5):.2f}、p90 = {_pct(ratios, .9):.2f}"})
    else:
        recs.append({"key": "agent.budget.synthetic_factor", "current": f_now, "suggested": None, "n": len(ratios),
                     "confidence": "none", "evidence": f"只有 {len(ratios)} 個有估計步數的回合（需要 {bcfg['min_samples']}），先維持 {f_now:g}"})
    types = Counter(u.get("task_type") for u in usage)
    baselines = {t: B.baseline(store, config, t, None) for t in types}
    for r in recs:
        r["apply"] = r["suggested"] is not None and r["suggested"] != r["current"]
    return {"turns_replayed": len(rows), "turns_ok": len(ok), "turns_bad": len(bad), "recommendations": recs,
            "baselines": {t: {"source": b["source"], "n": b["n"], "ref_tokens": b.get("ref_tokens"),
                              "ref_cost": b.get("ref_cost"), **({"p50": b["tokens"]["p50"], "p90": b["tokens"]["p90"]}
                                                                if b["source"] == "history" else {})}
                          for t, b in baselines.items()},
            "cap": B.hard_cap(store, config)}


TUNABLE = {"agent.progress.k", "agent.progress.n_code", "agent.progress.n_document", "agent.progress.n_other",
           "agent.budget.synthetic_factor"}


def apply(config, values: dict) -> dict:
    """Write chosen recommendations to the settings ({key: value}); only the tunable keys."""
    updates: dict = {}
    applied = {}
    for key, v in (values or {}).items():
        if key not in TUNABLE or v is None:
            continue
        _, group, name = key.split(".")
        updates.setdefault("agent", {}).setdefault(group, {})[name] = float(v) if name == "synthetic_factor" else int(v)
        applied[key] = updates["agent"][group][name]
    if updates:
        config.save_gui_settings(updates)
    return applied
