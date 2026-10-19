"""Publish evaluations on sinobee.ai (/benchmarks/evals): one report per model combination.

A report is about one thing under test - a skill's thinking + execution model combination, or (model evaluation)
one model doing everything - on one suite, graded by one judge. Runs of the same combination are merged, so the
trials can be run interleaved with the other combinations (scripts/bench-pilot.sh) and still be published as one
report with n trials. It carries every number sinobee.ai recomputes: each trial's yes/no answers with evidence,
score, time, cost, tokens by role and steps, plus the suite and input hashes, the judge's calibration and the
creativity comparison (`sinobee eval compare`).

    sinobee eval publish <run id> [<run id> ...]
"""
from __future__ import annotations

import re
import time

from .. import account
from . import evals_dir
from .compare import key

TEXT_LIMIT = 40_000           # characters of produced text shown per report


class PublishError(Exception):
    pass


def _grade(g: dict) -> dict:
    out = {"ok": bool(g.get("ok")), "detail": str(g.get("detail") or "")[:300]}
    if g.get("evidence"):
        out["evidence"] = str(g["evidence"])[:300]
    if g.get("votes"):
        out["votes"] = list(g["votes"])[:2]
    if "judge2_ok" in g:
        out["judge2_ok"] = bool(g["judge2_ok"])
    return out


def _outputs(home, doc: dict, task_id: str, t: dict, budget: list[int]) -> list[dict]:
    base = evals_dir(home) / "results" / doc["id"] / re.sub(r"[^\w.-]+", "-", task_id) / f"t{t.get('trial')}" \
        if home is not None else None
    out = []
    for o in t.get("outputs") or []:
        item = {"path": o["path"], "kind": o.get("kind", "text"), "bytes": o.get("bytes", 0)}
        if o.get("kind") == "text" and budget[0] > 0 and base is not None:
            try:
                text = (base / o["path"]).read_text(encoding="utf-8", errors="replace")[:budget[0]]
                item["text"] = text
                budget[0] -= len(text)
            except OSError:
                pass
        out.append(item)
    return out


def _trial(home, doc: dict, task_id: str, t: dict, n: int, budget: list[int] | None) -> dict:
    return {"trial": n, "run": doc["id"], "q": t.get("q", 0), "crit_fail": bool(t.get("crit_fail")),
            "qualified": bool(t.get("qualified")), "status": t.get("status"), "started": t.get("started"),
            "duration_s": t.get("duration_s") or 0, "cost_usd": t.get("cost_usd") or 0,
            "input_tokens": t.get("input_tokens") or 0, "output_tokens": t.get("output_tokens") or 0,
            "steps": t.get("steps") or 0, "tool_calls": t.get("tool_calls") or 0, "roles": t.get("roles") or {},
            "steps_trace": (t.get("steps_trace") or [])[:150], "error": (t.get("error") or None),
            "grades": [_grade(g) for g in t.get("grades") or []], "answer": str(t.get("answer") or "")[:1500],
            "outputs": _outputs(home, doc, task_id, t, budget) if budget is not None else
            [{k: o.get(k) for k in ("path", "kind", "bytes")} for o in t.get("outputs") or []]}


def payloads(runs: list[dict], home=None) -> list[dict]:
    if not runs:
        raise PublishError("no runs")
    bad = [r["id"] for r in runs if r.get("status") != "done"]
    if bad:
        raise PublishError(f"runs not finished: {', '.join(bad)}")
    if any(not r.get("bench") or "suite_hash" not in r for r in runs):
        raise PublishError("only runs of an evaluation suite (with a bench block) can be published")
    for field, what in (("suite", "suites"), ("suite_hash", "suite versions"), ("judge", "judges")):
        vals = {r.get(field) for r in runs}
        if len(vals) > 1:
            raise PublishError(f"runs come from different {what}: {', '.join(map(str, vals))}")
    groups: dict[str, list[dict]] = {}
    for r in sorted(runs, key=lambda r: r.get("started") or 0):
        groups.setdefault(key(r), []).append(r)
    out = []
    for docs in groups.values():
        first = docs[0]
        bench = first.get("bench") or {}
        tids = [t["id"] for t in first.get("tasks") or []]
        if any([t["id"] for t in d.get("tasks") or []] != tids for d in docs):
            raise PublishError("runs of one combination have different tasks")
        tasks = []
        for i, tid in enumerate(tids):
            spec = first["tasks"][i]
            trials, n = [], 0
            for d in docs:
                for t in d["tasks"][i]["trials"]:
                    n += 1
                    trials.append(_trial(home, d, tid, t, n, [TEXT_LIMIT] if n == 1 else None))
            tasks.append({"id": tid, "title": spec.get("title") or tid, "prompt": spec.get("prompt", ""),
                          "questions": spec.get("questions") or [], "weights": spec.get("weights") or {},
                          "trials": trials})
        agree = [0, 0]
        for d in docs:
            a = (d.get("totals") or {}).get("judge2_agree")
            if a:
                agree = [agree[0] + a[0], agree[1] + a[1]]
        checks = [d.get("judge_check") for d in docs if d.get("judge_check")]
        crea = next((d.get("creativity") for d in docs if d.get("creativity")), None)
        out.append({
            "kind": bench.get("kind") or "model", "subject": bench.get("skill") or bench.get("domain") or first.get("suite"),
            "subject_version": str(bench.get("version") or ""), "title": first.get("title") or first.get("suite"),
            "summary": bench.get("summary") or "", "suite": first.get("suite"), "suite_hash": first["suite_hash"],
            "input_hash": first.get("input_hash"), "thinking": first.get("thinking") or first.get("model"),
            "execution": first.get("execution") or first.get("model"), "judge": first.get("judge"),
            "judge2": first.get("judge2"), "judge2_agree": agree if agree[1] else None,
            "judge_check": {"total": sum(c["total"] for c in checks), "correct": sum(c["correct"] for c in checks)}
            if checks else None,
            "threshold": float(bench.get("threshold") or 70), "client_version": first.get("sinobee_version"),
            "tested_at": min(d.get("started") or time.time() for d in docs),
            "tested_until": max(d.get("finished") or d.get("started") or 0 for d in docs),
            "runs": [d["id"] for d in docs], "tasks": tasks,
            "creativity": {k: crea[k] for k in ("wins", "total", "judge", "combos") if k in crea} if crea else None,
        })
    return out


def publish(config, runs: list[dict]) -> list[dict]:
    data = payloads(runs, home=config.home)
    acc = account.account(config)
    if not acc:
        raise PublishError("sign in to sinobee.ai first (Settings > SinoBee.ai account, or `sinobee login`)")
    if acc.get("scopes") and "evals:write" not in acc["scopes"]:
        raise PublishError("this sign-in cannot publish evaluations: sign out and sign in again")
    res = []
    for body in data:
        try:
            r = account.send(config, "POST", f"{account.base_url(config)}/api/v1/evals", scope="evals:write",
                             json=body, timeout=60)
        except account.AccountError as e:
            raise PublishError(str(e)) from None
        if r.status_code >= 400:
            raise PublishError(f"sinobee.ai HTTP {r.status_code}: {r.text[:300]}")
        res.append(r.json())
    return res
