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

TEXT_LIMIT = 40_000           # characters of produced text sent inline (older sinobee.ai pages show only this)
UPLOAD_LIMIT = 20_000_000     # bytes of trial outputs uploaded per report (design R2); beyond: listed, not uploaded


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


def trial_base(home, run_id: str, task_id: str, trial):
    return evals_dir(home) / "results" / run_id / re.sub(r"[^\w.-]+", "-", task_id) / f"t{trial}" \
        if home is not None else None


def _outputs(home, doc: dict, task_id: str, t: dict, budget: list[int] | None) -> list[dict]:
    """Every output of the trial with its hash (the file as graded; the file itself is uploaded apart, by hash);
    the first trial also carries its text inline for older sinobee.ai pages."""
    from . import outputs as O
    base = trial_base(home, doc["id"], task_id, t.get("trial"))
    out = []
    for o in t.get("outputs") or []:
        item = {"path": o["path"], "kind": o.get("kind", "text"), "bytes": o.get("bytes", 0)}
        if base is not None:
            try:
                data = (base / o["path"]).read_bytes()
                item.update(bytes=len(data), sha256=O.sha256(data))
            except OSError:
                item["missing"] = True
        if budget is None:
            out.append(item)
            continue
        if o.get("kind") == "text" and budget[0] > 0 and base is not None:
            try:
                text = (base / o["path"]).read_text(encoding="utf-8", errors="replace")[:budget[0]]
                item["text"] = text
                budget[0] -= len(text)
            except OSError:
                pass
        out.append(item)
    return out


def _where(home, doc: dict, task_id: str, t: dict) -> list:
    """Where each grade's quoted evidence is in the trial's text outputs ({file, start, end} or None)."""
    from . import outputs as O
    base = trial_base(home, doc["id"], task_id, t.get("trial"))
    texts = {}
    if base is not None:
        for o in t.get("outputs") or []:
            if O.kind_of(o["path"]) in ("html", "markdown", "text", "csv", "svg"):
                try:
                    texts[o["path"]] = (base / o["path"]).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
    return [O.locate(str(g.get("evidence") or ""), texts) if g.get("evidence") and texts else None
            for g in t.get("grades") or []]


def _trial(home, doc: dict, task_id: str, t: dict, n: int, budget: list[int] | None) -> dict:
    return {"trial": n, "run": doc["id"], "run_trial": t.get("trial") or 1, "q": t.get("q", 0), "crit_fail": bool(t.get("crit_fail")),
            "qualified": bool(t.get("qualified")), "status": t.get("status"), "started": t.get("started"),
            "duration_s": t.get("duration_s") or 0, "cost_usd": t.get("cost_usd") or 0,
            "input_tokens": t.get("input_tokens") or 0, "output_tokens": t.get("output_tokens") or 0,
            "steps": t.get("steps") or 0, "tool_calls": t.get("tool_calls") or 0, "roles": t.get("roles") or {},
            "steps_trace": (t.get("steps_trace") or [])[:150], "error": (t.get("error") or None),
            "grades": [{**_grade(g), **({"where": {k: w[k] for k in ("file", "start", "end")}} if w else {})}
                       for g, w in zip(t.get("grades") or [], _where(home, doc, task_id, t) + [None] * 999)],
            "answer": str(t.get("answer") or "")[:1500],
            "outputs": _outputs(home, doc, task_id, t, budget)}


def payloads(runs: list[dict], home=None, judge_errors_ok: bool = False) -> list[dict]:
    if not runs:
        raise PublishError("no runs")
    bad = [r["id"] for r in runs if r.get("status") != "done"]
    if bad:
        raise PublishError(f"runs not finished: {', '.join(bad)}")
    if any(not r.get("bench") or "suite_hash" not in r for r in runs):
        raise PublishError("only runs of an evaluation suite (with a bench block) can be published")
    bad_judge = [r["id"] for r in runs if (r.get("totals") or {}).get("judge_errors")
                 or any(g.get("judge_error") for t in r.get("tasks") or [] for x in t.get("trials") or []
                        for g in x.get("grades") or [])]
    if bad_judge and not judge_errors_ok:
        raise PublishError("the judge gave no verdict on some questions of runs " + ", ".join(bad_judge)
                           + " - their scores are not the contestants'; run them again (the judge now has room to think)")
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


def publish(config, runs: list[dict], extra: dict | None = None) -> list[dict]:
    """extra: fields added to every report (a campaign's visibility, evaluation skill, suite reference)."""
    data = payloads(runs, home=config.home)
    for body in data:
        for k, v in (extra or {}).items():
            if v not in (None, "") or k not in body:
                body[k] = v
    acc = account.account(config)
    if not acc:
        raise PublishError("sign in to sinobee.ai first (Settings > SinoBee.ai account, or `sinobee login`)")
    if acc.get("scopes") and "evals:write" not in acc["scopes"]:
        raise PublishError("this sign-in cannot publish evaluations: sign out and sign in again")
    res = []
    for body in data:
        upload_outputs(config, body)
        try:
            r = account.send(config, "POST", f"{account.base_url(config)}/api/v1/evals", scope="evals:write",
                             json=body, timeout=60)
        except account.AccountError as e:
            raise PublishError(str(e)) from None
        if r.status_code >= 400:
            raise PublishError(f"sinobee.ai HTTP {r.status_code}: {r.text[:300]}")
        res.append(r.json())
    return res


def upload_outputs(config, body: dict) -> dict:
    """Put the trial outputs a report lists on sinobee.ai, by sha256, before the report (design R2): the report page
    shows them next to the verdicts. Up to UPLOAD_LIMIT bytes per report - the main output of every trial first;
    what does not fit (or an older sinobee.ai without the endpoint) is marked not uploaded and stays listed."""
    from . import outputs as O
    files = []
    for task in body.get("tasks") or []:
        for t in task.get("trials") or []:
            base = trial_base(config.home, t["run"], task["id"], t.get("run_trial") or 1) if t.get("run") else None
            for i, o in enumerate(t.get("outputs") or []):
                files.append((0 if i == 0 else 1, o, base))
    sent, used, seen = 0, 0, set()
    url = f"{account.base_url(config)}/api/v1/evals/outputs"
    gone = False
    for _rank, o, base in sorted(files, key=lambda x: x[0]):
        sha = o.get("sha256")
        if not sha or o.get("missing") or base is None:
            o["uploaded"] = False
            continue
        if sha in seen:
            o["uploaded"] = True
            continue
        if gone or used + int(o.get("bytes") or 0) > UPLOAD_LIMIT:
            o["uploaded"] = False
            continue
        try:
            data = (base / o["path"]).read_bytes()
        except OSError:
            o["uploaded"] = False
            continue
        if O.sha256(data) != sha:
            o["uploaded"] = False
            continue
        try:
            r = account.send(config, "PUT", f"{url}/{sha}", scope="evals:write", content=data,
                             headers={"Content-Type": "application/octet-stream"}, timeout=120)
        except account.AccountError:
            o["uploaded"] = False
            continue
        if r.status_code == 404:                         # an older sinobee.ai: the report goes without them
            gone = True
            o["uploaded"] = False
            continue
        o["uploaded"] = r.status_code < 300
        if o["uploaded"]:
            seen.add(sha)
            used += len(data)
            sent += 1
    return {"uploaded": sent, "bytes": used}

