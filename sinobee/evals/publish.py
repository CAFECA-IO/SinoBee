"""Publish evaluation runs as one report on sinobee.ai (/benchmarks).

A report compares models on the same suite: one finished run per model (`sinobee eval run <suite> -m <model>
--judge <model>`), all with the same tasks and judge. It carries what readers need to trust it: the score and
pass rates per model, the time and tokens each spent, the judge model, when it was tested, every grader's verdict
per trial, and the report text written from those results.

    sinobee eval publish <run id> <run id> ... --body report.md
"""
from __future__ import annotations

import time

from .. import account


class PublishError(Exception):
    pass


def _trial(t: dict) -> dict:
    return {k: t.get(k) for k in ("trial", "passed", "status", "steps", "tool_calls", "duration_s", "input_tokens",
                                  "output_tokens", "cache_read_tokens", "cost_usd", "started", "error")} | {
        "grades": [{"label": g.get("label") or g.get("type"), "ok": bool(g.get("ok")),
                    "detail": str(g.get("detail") or "")[:300]} for g in t.get("grades") or []],
        "answer": str(t.get("answer") or "")[:1200], "files": (t.get("files") or [])[:20]}


def payload(runs: list[dict], *, body: str = "", title: str | None = None, summary: str | None = None) -> dict:
    if not runs:
        raise PublishError("no runs")
    bad = [r["id"] for r in runs if r.get("status") != "done"]
    if bad:
        raise PublishError(f"runs not finished: {', '.join(bad)}")
    suites = {r.get("suite") for r in runs}
    if len(suites) > 1:
        raise PublishError(f"runs come from different suites: {', '.join(map(str, suites))}")
    models = [r.get("model") for r in runs]
    if len(set(models)) != len(models):
        raise PublishError("one run per model")
    judges = {r.get("judge") for r in runs}
    if len(judges) > 1:
        raise PublishError(f"runs were graded by different judges: {', '.join(map(str, judges))}")
    tasks = [{t["id"] for t in r.get("tasks") or []} for r in runs]
    if any(x != tasks[0] for x in tasks):
        raise PublishError("runs have different tasks")
    first = runs[0]
    bench = first.get("bench") or {}
    return {
        "kind": bench.get("kind") or "model", "title": title or first.get("title") or first.get("suite"),
        "domain": bench.get("domain") or "", "skill": bench.get("skill"),
        "summary": summary or bench.get("summary") or "", "body": body, "suite": first.get("suite"),
        "judge": first.get("judge"), "client_version": first.get("sinobee_version"),
        "tested_at": min(r.get("started") or time.time() for r in runs),
        "tested_until": max(r.get("finished") or r.get("started") or 0 for r in runs),
        "runs": [{"model": r.get("model"), "solo": bool(r.get("solo")), "run_id": r["id"], "k": r.get("k"),
                  "started": r.get("started"), "finished": r.get("finished"), "totals": r.get("totals") or {},
                  "judge_usage": r.get("judge_usage") or {},
                  "tasks": [{"id": t["id"], "title": t.get("title") or t["id"], "prompt": t.get("prompt", ""),
                             "graders": t.get("graders") or [], "pass_at_k": t.get("pass_at_k"),
                             "pass_hat_k": t.get("pass_hat_k"), "trials": [_trial(x) for x in t.get("trials") or []]}
                            for t in r.get("tasks") or []]} for r in runs],
    }


def publish(config, runs: list[dict], **kw) -> dict:
    data = payload(runs, **kw)
    acc = account.account(config)
    if not acc:
        raise PublishError("sign in to sinobee.ai first (Settings > SinoBee.ai account, or `sinobee login`)")
    if acc.get("scopes") and "evals:write" not in acc["scopes"]:
        raise PublishError("this sign-in cannot publish evaluations: sign out and sign in again")
    try:
        r = account.send(config, "POST", f"{account.base_url(config)}/api/v1/evaluations", scope="evals:write",
                         json=data, timeout=60)
    except account.AccountError as e:
        raise PublishError(str(e)) from None
    if r.status_code >= 400:
        raise PublishError(f"sinobee.ai HTTP {r.status_code}: {r.text[:300]}")
    return r.json()
