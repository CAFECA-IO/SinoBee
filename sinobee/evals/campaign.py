"""Evaluation campaigns: run one suite on several thinking + execution model combinations, within a budget, and
answer "which combination should I use this skill with?" - the fastest, the cheapest, the best and the most creative.

    c = new(rt, suite="suite:4b7d…", combos=[{"thinking": "anthropic/claude-opus-5-5",
                                              "execution": "anthropic/claude-sonnet-5", "baseline": True}, …],
            k=3, judge="gemini/gemini-pro-latest", judge2="anthropic/claude-sonnet-5", budget_usd=12)
    QUEUE.submit(rt_getter, c["id"], emit)          # background; one campaign at a time, the rest wait
    results(rt, c["id"])                            # the four answers and the checks before submitting
    await publish(rt, c["id"], "public")            # one report per combination on sinobee.ai

A trial is one pass over every input of the suite (runner.run with k=1). Trials are interleaved - trial 1 of every
combination, then trial 2, … - so a slow afternoon of one provider does not land on one combination only, and a
campaign stopped by its budget still has comparable numbers. The baseline (the combination the task was done with)
is run k times on the suite like the others: the task itself had a different input. Before each trial the money
already spent (contestants and judges) plus what the trial is expected to cost must stay within the budget.
Combinations that already have n >= k trials on sinobee.ai for the same suite are skipped unless asked.
When the trials are done, the qualified results are compared pairwise for creativity (compare.py).

Campaigns live in $SINOBEE_HOME/evals/campaigns/<id>.json; a stopped campaign resumes where it left off.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import statistics
import time
import uuid
from pathlib import Path

from .. import privacy
from . import EvalError, bench_meta, evals_dir, load, result
from . import sfs

log = logging.getLogger("sinobee.evals")

MIN_TRIALS = 3
MAX_COMBOS = 12
DEFAULT_BUDGET_CAP = 20.0
BUDGET_FACTOR = 1.5
THINKING_SHARE = 0.25         # share of a task's tokens spent by the thinking model (when it is not the executor)
OUTPUT_SHARE = 0.15           # share of tokens that are output
JUDGE_IN, JUDGE_OUT = 8000, 250
ANCHOR_IN, ANCHOR_OUT = 1500, 80
DEFAULT_TASK_TOKENS, DEFAULT_TASK_SECONDS = 300_000, 240
CANARY = re.compile(r"\.invalid\b|sk-sbx|SBXK|SBX0|SBX-OBEY")


def campaigns_dir(home) -> Path:
    d = evals_dir(home) / "campaigns"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(home, cid: str) -> Path:
    if not re.fullmatch(r"[\w-]{6,60}", cid or ""):
        raise EvalError("不正確的評鑑編號")
    return campaigns_dir(home) / f"{cid}.json"


def get(home, cid: str) -> dict:
    try:
        return json.loads(_path(home, cid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise EvalError(f"找不到評鑑 {cid}") from None


def save(home, c: dict) -> dict:
    c["updated"] = time.time()
    p = _path(home, c["id"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return c


def listing(home, session_id: str | None = None, limit: int = 30) -> list[dict]:
    out = []
    for f in sorted(campaigns_dir(home).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if session_id and c.get("session_id") != session_id:
            continue
        out.append(summary(c))
        if len(out) >= limit:
            break
    return out


def summary(c: dict) -> dict:
    return {k: c.get(k) for k in ("id", "skill", "skill_version", "suite", "suite_hash", "status", "session_id",
                                  "created", "updated", "budget_usd", "spent", "progress", "error", "k")}


def ckey(thinking: str, execution: str) -> str:
    return f"{thinking}+{execution}"


# ---------------------------------------------------------------- planning

def schedule(combos: list[dict]) -> list[list[int]]:
    """[combo index, trial number] in interleaved order: every combination's trial 1, then trial 2, …"""
    out = []
    rounds = max((int(c.get("trials") or 0) for c in combos), default=0)
    for r in range(1, rounds + 1):
        for i, c in enumerate(combos):
            if not c.get("skip") and r <= int(c.get("trials") or 0):
                out.append([i, r])
    return out


def _price(config, spec: str | None) -> dict | None:
    from ..pricing import price_for
    if not spec or "/" not in spec:
        return None
    prov, _, model = spec.partition("/")
    price, _src = price_for(prov, model, config.get("pricing") or {})
    return price


def _cost(config, spec: str | None, tokens_in: float, tokens_out: float) -> float | None:
    from ..pricing import cost_usd
    price = _price(config, spec)
    if price is None:
        return None
    return float(cost_usd(price, int(tokens_in), int(tokens_out)) or 0.0)


def _history(home, suite_hash: str | None) -> dict:
    """Per-task tokens and seconds seen in earlier runs: of this suite (by execution model), and of any suite."""
    by_model: dict[str, list[tuple[int, float]]] = {}
    same: list[tuple[int, float]] = []
    speed: dict[str, list[float]] = {}
    for f in sorted((evals_dir(home) / "results").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:200]:
        if f.name.startswith("compare-"):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ex = d.get("execution") or d.get("model") or ""
        for t in d.get("tasks") or []:
            for x in t.get("trials") or []:
                tok = int(x.get("input_tokens") or 0) + int(x.get("output_tokens") or 0)
                sec = float(x.get("duration_s") or 0)
                if x.get("error") or not sec:
                    continue
                speed.setdefault(ex, []).append(sec)
                if suite_hash and d.get("suite_hash") == suite_hash:
                    same.append((tok, sec))
                    by_model.setdefault(ex, []).append((tok, sec))
    return {"same": same, "by_model": by_model, "speed": {k: statistics.median(v) for k, v in speed.items() if v}}


def _task_basis(rt, session_id: str | None, turn_id=None) -> tuple[int, float] | None:
    if not session_id:
        return None
    from ..skills import package
    rep = package._report(rt.config.home, session_id, turn_id)
    if not rep:
        return None
    tok = int((rep.get("efficiency") or {}).get("tokens") or 0)
    sec = float((rep.get("time") or {}).get("total_s") or rep.get("duration_s") or 0)
    return (tok, sec) if tok else None


def estimate(rt, suite: str, combos: list[dict], *, judge: str | None = None, judge2: str | None = None,
             session_id: str | None = None, turn_id=None) -> dict:
    """Expected money and time of the trials still to run (contestants plus judges)."""
    home = rt.config.home
    _name, tasks = load(suite, home=home, skills=rt.skills)
    meta = bench_meta(suite, home)
    bench = meta.get("bench") or {}
    from .runner import _bench_task
    hist = _history(home, sfs.suite_hash([_bench_task(t) for t in tasks], bench) if bench else None)
    basis = "default"
    base_tok, base_sec = DEFAULT_TASK_TOKENS, DEFAULT_TASK_SECONDS
    if hist["same"]:
        base_tok = int(statistics.median(t for t, _ in hist["same"]))
        base_sec = statistics.median(s for _, s in hist["same"])
        basis = "suite"
    else:
        tb = _task_basis(rt, session_id, turn_id)
        if tb:
            base_tok, base_sec = tb[0], tb[1] or DEFAULT_TASK_SECONDS
            basis = "task"
    overall = statistics.median(hist["speed"].values()) if hist["speed"] else None
    n_tasks = len(tasks)
    llm = sum(1 for g in tasks[0]["graders"] if g.get("type") == "llm") if tasks else 0
    votes = 3 if bench else 1
    j_in, j_out = n_tasks * llm * votes * JUDGE_IN, n_tasks * llm * votes * JUDGE_OUT
    judge_spec = judge or getattr(rt.judge(), "label", None)
    per_trial_judge = (_cost(rt.config, judge_spec, j_in, j_out) or 0.0)
    if judge2:
        per_trial_judge += _cost(rt.config, judge2, j_in / votes, j_out / votes) or 0.0
    anchors = len(bench.get("anchors") or [])
    anchor_usd = _cost(rt.config, judge_spec, anchors * ANCHOR_IN, anchors * ANCHOR_OUT) or 0.0
    rows, total_usd, total_s, unpriced = [], anchor_usd, 0.0, set()
    for c in combos:
        th, ex = c.get("thinking") or c["execution"], c["execution"]
        own = hist["by_model"].get(ex)
        tok = int(statistics.median(t for t, _ in own)) if own else base_tok
        sec = statistics.median(s for _, s in own) if own else base_sec * (
            hist["speed"].get(ex, overall) / overall if overall and hist["speed"].get(ex) else 1.0)
        th_tok = tok * THINKING_SHARE if th != ex else 0
        ex_tok = tok - th_tok
        usd = 0.0
        for spec, t in ((th, th_tok), (ex, ex_tok)):
            if not t:
                continue
            v = _cost(rt.config, spec, t * (1 - OUTPUT_SHARE), t * OUTPUT_SHARE)
            if v is None:
                unpriced.add(spec)
            usd += v or 0.0
        n = 0 if c.get("skip") else int(c.get("trials") or 0)
        trial_usd = n_tasks * usd + per_trial_judge
        rows.append({"key": ckey(th, ex), "trials": n, "usd_per_trial": round(trial_usd, 4),
                     "seconds_per_trial": round(n_tasks * sec, 1), "usd": round(n * trial_usd, 4),
                     "seconds": round(n * n_tasks * sec, 1)})
        total_usd += n * trial_usd
        total_s += n * n_tasks * sec
    budget = min(DEFAULT_BUDGET_CAP, max(1.0, round(BUDGET_FACTOR * total_usd, 2)))
    return {"combos": rows, "usd": round(total_usd, 4), "seconds": round(total_s, 1), "basis": basis,
            "tasks": n_tasks, "judge_usd_per_trial": round(per_trial_judge, 4), "unpriced": sorted(unpriced),
            "trials": sum(r["trials"] for r in rows), "suggested_budget": budget}


def remote_tested(config, skill: str | None, suite_hash: str | None) -> dict[str, int]:
    """Trials already published on sinobee.ai for this suite, by combination (nothing when offline)."""
    if not skill or not suite_hash:
        return {}
    try:
        import httpx
        from .. import account
        r = httpx.get(f"{account.base_url(config)}/api/v1/evals",
                      params={"kind": "skill", "subject": skill, "suite_hash": suite_hash}, timeout=8)
        items = r.json().get("items") or [] if r.status_code == 200 else []
    except Exception:  # noqa: BLE001 - offline or an older sinobee.ai: just nothing to skip
        return {}
    out: dict[str, int] = {}
    for it in items:
        if it.get("suiteHash") != suite_hash:
            continue
        k = ckey(it.get("thinkingModel") or "", it.get("executionModel") or "")
        out[k] = max(out.get(k, 0), int(it.get("trials") or 0))
    return out


def new(rt, *, suite: str, combos: list[dict], k: int = MIN_TRIALS, judge: str | None = None,
        judge2: str | None = None, budget_usd: float | None = None, session_id: str | None = None, turn_id=None,
        rerun_tested: bool = False, tested: dict[str, int] | None = None) -> dict:
    home = rt.config.home
    _name, tasks = load(suite, home=home, skills=rt.skills)
    meta = bench_meta(suite, home)
    bench = meta.get("bench") or {}
    if not bench:
        raise EvalError("只有評鑑題組（有 bench 區塊）可以建立評鑑")
    from .runner import _bench_task
    suite_hash = sfs.suite_hash([_bench_task(t) for t in tasks], bench)
    k = max(1, min(int(k or MIN_TRIALS), 10))
    seen, clean = set(), []
    for c in combos or []:
        ex = str(c.get("execution") or "").strip()
        th = str(c.get("thinking") or ex).strip()
        if not ex or "/" not in ex or "/" not in th:
            raise EvalError(f"組合的模型要寫成 provider/model：{c}")
        key_ = ckey(th, ex)
        if key_ in seen:
            continue
        seen.add(key_)
        clean.append({"thinking": th, "execution": ex, "baseline": bool(c.get("baseline")), "trials": k, "skip": False})
    if not clean:
        raise EvalError("至少選一個組合")
    if len(clean) > MAX_COMBOS:
        raise EvalError(f"一次最多 {MAX_COMBOS} 個組合")
    tested = remote_tested(rt.config, bench.get("skill"), suite_hash) if tested is None else tested
    for c in clean:
        n = int(tested.get(ckey(c["thinking"], c["execution"])) or 0)
        c["published_trials"] = n
        if n >= k and not rerun_tested and not c["baseline"]:
            c["skip"] = True
    est = estimate(rt, suite, clean, judge=judge, judge2=judge2, session_id=session_id, turn_id=turn_id)
    budget = float(budget_usd) if budget_usd not in (None, "") else est["suggested_budget"]
    cid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    plan = schedule(clean)
    c = {"id": cid, "created": time.time(), "status": "ready", "session_id": session_id, "turn_id": turn_id,
         "skill": bench.get("skill"), "skill_version": "", "suite": suite, "suite_hash": suite_hash,
         "evaluator": bench.get("evaluator"), "title": meta.get("title"), "k": k, "judge": judge, "judge2": judge2,
         "budget_usd": round(max(0.0, budget), 2), "combos": clean, "plan": plan, "next": 0, "items": [],
         "spent": {"contestants": 0.0, "judges": 0.0, "total": 0.0}, "estimate": est,
         "progress": {"done": 0, "total": len(plan)}, "elapsed_s": 0.0, "creativity": None, "published": []}
    try:
        c["skill_version"] = rt.skills.get(c["skill"]).version if c["skill"] else ""
    except Exception:  # noqa: BLE001
        pass
    if not plan:
        c["status"] = "done"
    return save(home, c)


# ---------------------------------------------------------------- running

def _judge_cost(config, doc: dict) -> float:
    u = doc.get("judge_usage") or {}
    spec = doc.get("judge") if doc.get("judge") and "/" in str(doc.get("judge")) else None
    return _cost(config, spec, u.get("input_tokens", 0), u.get("output_tokens", 0)) or 0.0


def _expected(c: dict, ci: int) -> float:
    rows = (c.get("estimate") or {}).get("combos") or []
    return float(rows[ci]["usd_per_trial"]) if ci < len(rows) else 0.0


async def execute(rt, cid: str, emit=None) -> dict:
    """Run the campaign's remaining trials, then the creativity comparison. Stops (keeping what is done) when the
    budget would be exceeded; a cancel marks it stopped so it can be resumed."""
    from .runner import run
    home = rt.config.home
    c = get(home, cid)
    if c["status"] in ("done", "published"):
        return c
    c.update(status="running", error=None)
    save(home, c)
    started = time.time()
    base_elapsed = float(c.get("elapsed_s") or 0)

    def tell(kind: str, **extra):
        if emit:
            try:
                emit(kind, {"id": cid, "session_id": c.get("session_id"), "status": c["status"],
                            "progress": c["progress"], "spent": c["spent"], "budget_usd": c["budget_usd"], **extra})
            except Exception:  # noqa: BLE001
                log.debug("campaign emit failed", exc_info=True)
    tell("campaign_progress")
    try:
        while c["next"] < len(c["plan"]):
            ci, trial = c["plan"][c["next"]]
            combo = c["combos"][ci]
            if c["spent"]["total"] + _expected(c, ci) > c["budget_usd"] + 1e-9:
                c["status"] = "budget"
                c["error"] = (f"已花費 ${c['spent']['total']:.2f}，下一次試驗預估 ${_expected(c, ci):.2f}，"
                              f"會超過預算 ${c['budget_usd']:.2f}；已完成的結果都保留")
                break
            tell("campaign_progress", running={"combo": ci, "trial": trial})
            doc = await run(rt, c["suite"], k=1, model=combo["execution"],
                            thinking=combo["thinking"] if combo["thinking"] != combo["execution"] else None,
                            judge=c.get("judge"), judge2=c.get("judge2"),
                            anchors=not any(it.get("judge_check") for it in c["items"]))
            t = doc.get("totals") or {}
            contestant = float(t.get("cost_usd") or 0)
            judges = _judge_cost(rt.config, doc)
            scores = [x.get("q") for task in doc.get("tasks") or [] for x in task.get("trials") or []]
            item = {"combo": ci, "trial": trial, "run": doc["id"], "status": doc.get("status"),
                    "q": round(sum(s or 0 for s in scores) / len(scores), 2) if scores else None,
                    "tasks": [{"id": task["id"], "q": (task.get("trials") or [{}])[0].get("q"),
                               "qualified": (task.get("trials") or [{}])[0].get("qualified")}
                              for task in doc.get("tasks") or []],
                    "qualified": bool(scores) and all(x.get("qualified") for task in doc.get("tasks") or []
                                                      for x in task.get("trials") or []),
                    "cost_usd": round(contestant, 5), "judge_usd": round(judges, 5),
                    "duration_s": t.get("duration_s"), "judge_check": doc.get("judge_check")}
            c["items"].append(item)
            c["spent"]["contestants"] = round(c["spent"]["contestants"] + contestant, 5)
            c["spent"]["judges"] = round(c["spent"]["judges"] + judges, 5)
            c["spent"]["total"] = round(c["spent"]["contestants"] + c["spent"]["judges"], 5)
            c["next"] += 1
            c["progress"]["done"] = c["next"]
            c["elapsed_s"] = round(base_elapsed + time.time() - started, 1)
            save(home, c)
            tell("campaign_progress", item=item)
        else:
            c["status"] = "comparing"
            save(home, c)
            tell("campaign_progress")
            c["creativity"] = await _creativity(rt, c)
            c["status"] = "done"
    except asyncio.CancelledError:
        c["status"] = "stopped"
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("campaign failed")
        c["status"], c["error"] = "error", f"{type(e).__name__}: {e}"
    finally:
        c["elapsed_s"] = round(base_elapsed + time.time() - started, 1)
        save(home, c)
        tell("campaign_done" if c["status"] != "running" else "campaign_progress", error=c.get("error"))
    return c


async def _creativity(rt, c: dict) -> dict | None:
    from .compare import compare
    docs = _docs(rt.config.home, c)
    groups = {}
    for d in docs:
        if any(x.get("qualified") for t in d.get("tasks") or [] for x in t.get("trials") or []):
            groups.setdefault(ckey(d.get("thinking") or d.get("model"), d.get("execution") or d.get("model")), d)
    if len(groups) < 2:
        return None
    try:
        res = await compare(rt, docs, c.get("judge"))
    except EvalError as e:
        return {"error": str(e)}
    jcost = _cost(rt.config, c.get("judge"), (res.get("judge_usage") or {}).get("input_tokens", 0),
                  (res.get("judge_usage") or {}).get("output_tokens", 0)) or 0.0
    c["spent"]["judges"] = round(c["spent"]["judges"] + jcost, 5)
    c["spent"]["total"] = round(c["spent"]["contestants"] + c["spent"]["judges"], 5)
    return {"id": res["id"], "scores": res["scores"], "pairs": len(res.get("pairs") or [])}


def _docs(home, c: dict) -> list[dict]:
    out = []
    for it in c.get("items") or []:
        try:
            d = result(home, it["run"])
        except EvalError:
            continue
        if d.get("status") == "done":
            out.append(d)
    return out


class Queue:
    """One campaign runs at a time; the others wait their turn (they all use the same models and the same money)."""

    def __init__(self):
        self.current: str | None = None
        self.task: asyncio.Task | None = None
        self.waiting: list[tuple] = []

    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    def submit(self, rt_getter, cid: str, emit=None) -> str:
        rt = rt_getter()
        c = get(rt.config.home, cid)
        if c["status"] in ("done", "published"):
            return c["status"]
        if self.busy():
            if cid == self.current or any(w[1] == cid for w in self.waiting):
                return "running" if cid == self.current else "queued"
            self.waiting.append((rt_getter, cid, emit))
            c["status"] = "queued"
            save(rt.config.home, c)
            if emit:
                emit("campaign_progress", {"id": cid, "session_id": c.get("session_id"), "status": "queued",
                                           "progress": c["progress"], "spent": c["spent"],
                                           "budget_usd": c["budget_usd"]})
            return "queued"
        self.current = cid
        self.task = asyncio.ensure_future(self._run(rt_getter, cid, emit))
        return "running"

    async def _run(self, rt_getter, cid, emit):
        try:
            await execute(rt_getter(), cid, emit)
        except asyncio.CancelledError:
            pass
        finally:
            self.current, self.task = None, None
            if self.waiting:
                g, nxt, em = self.waiting.pop(0)
                self.submit(g, nxt, em)

    def stop(self, home, cid: str) -> dict:
        if cid == self.current and self.task is not None:
            self.task.cancel()
        self.waiting = [w for w in self.waiting if w[1] != cid]
        c = get(home, cid)
        if c["status"] in ("queued", "ready"):
            c["status"] = "stopped"
            save(home, c)
        return c


QUEUE = Queue()


# ---------------------------------------------------------------- results

def _row(rep: dict) -> dict:
    """reportStats of sinobee.ai: per-task trials are the observations; trial i over the suite = mean of tasks."""
    tasks = rep["tasks"]
    k = min((len(t["trials"]) for t in tasks), default=0)
    trial_q = [sum(t["trials"][i]["q"] for t in tasks) / len(tasks) for i in range(k)]
    allx = [x for t in tasks for x in t["trials"]]
    ok = [x for x in allx if x["qualified"]]
    f = sfs.fair(trial_q)
    cost = sum(x["cost_usd"] for x in allx)
    durs = [x["duration_s"] for x in ok]
    crea = rep.get("creativity") or {}
    return {"thinking": rep["thinking"], "execution": rep["execution"], "key": ckey(rep["thinking"], rep["execution"]),
            "trials": k, "mean": f["mean"], "se": f["se"], "fair": f["fair"], "observations": len(allx),
            "qualified": len(ok), "crit_fails": sum(1 for x in allx if x["crit_fail"]),
            "median_s": statistics.median(durs) if durs else None, "cost_per_pass": cost / len(ok) if ok else None,
            "total_cost": cost, "creativity_wins": crea.get("wins"), "creativity_total": crea.get("total"),
            "runs": rep.get("runs") or []}


def board(rows: list[dict]) -> dict:
    """The four answers (port of sinobee.ai bench-evals.board): eligible combinations only."""
    for r in rows:
        r["rate"] = r["qualified"] / (r["observations"] or 1)
        r["creativity"] = (r["creativity_wins"] or 0) / r["creativity_total"] if r.get("creativity_total") else None
        r["creativity_se"] = math.sqrt(r["creativity"] * (1 - r["creativity"]) / r["creativity_total"]) \
            if r["creativity"] is not None else 0.0
        r["out"] = ("trial" if r["trials"] < MIN_TRIALS else "critical" if r["crit_fails"] / (r["observations"] or 1) > 1 / 3
                    else "rate" if r["rate"] < 2 / 3 else None)
        r["eligible"] = r["out"] is None
    E = [r for r in rows if r["eligible"]]

    def val(r, a):
        return {"speed": r["median_s"], "cost": r["cost_per_pass"], "quality": r["fair"], "creativity": r["creativity"]}[a]

    def tie(a, w, c):
        if a == "speed":
            return (c["median_s"] or math.inf) / (w["median_s"] or 1) < 1.1
        if a == "cost":
            if not w["cost_per_pass"]:
                return c["cost_per_pass"] == 0
            return (c["cost_per_pass"] if c["cost_per_pass"] is not None else math.inf) / w["cost_per_pass"] < 1.1
        if a == "quality":
            return abs(w["mean"] - c["mean"]) < 2 * math.hypot(w["se"], c["se"])
        return (w["creativity"] or 0) - (c["creativity"] or 0) < 2 * math.hypot(w["creativity_se"], c["creativity_se"])
    awards = {}
    for a, low in (("speed", True), ("cost", True), ("quality", False), ("creativity", False)):
        has = [r for r in E if val(r, a) is not None]
        rank = sorted(has, key=lambda r: val(r, a) if low else -val(r, a))
        w = rank[0] if rank else None
        awards[a] = {"winner": w["key"] if w else None, "value": val(w, a) if w else None,
                     "tied": [r["key"] for r in rank[1:] if tie(a, w, r)] if w else [],
                     "rank": [r["key"] for r in rank]}
    return {"rows": rows, "awards": awards}


def _scan_outputs(home, docs: list[dict]) -> list[dict]:
    """Personal data or keys in what the trials produced (sandbox canaries excluded: they are fake)."""
    found = []
    for d in docs:
        root = evals_dir(home) / "results" / d["id"]
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.stat().st_size > 1_000_000:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for f in privacy.scan(text):
                if f.kind in ("secret", "email", "phone", "national_id", "card") and not CANARY.search(f.value):
                    found.append({"file": p.relative_to(root).as_posix(), "run": d["id"], "kind": f.kind})
    return found


def _skill_published(rt, name: str | None) -> bool:
    try:
        s = rt.skills.get(name)
    except Exception:  # noqa: BLE001
        return False
    return s.trust in ("verified",) or str(s.source or "").startswith("hub:")


def checks(rt, c: dict, docs: list[dict], rep_rows: list[dict]) -> list[dict]:
    """The checks before submitting: status ok | warn | fail (a fail blocks a public report)."""
    from .authoring import find, read_package
    out = []
    try:
        _s, texts = read_package(find(rt.config.home, c["suite"]))
        leaks = sum(len(privacy.scan(t)) for t in texts.values())
        out.append({"id": "inputs", "status": "ok" if not leaks else "fail",
                    "label": f"測試輸入已去識別（{len(texts)} 個檔案）", "detail": f"掃描 {leaks} 處"})
    except EvalError:
        out.append({"id": "inputs", "status": "warn", "label": "這份題組不是評鑑技能出的題，無法檢查測試輸入", "detail": ""})
    found = _scan_outputs(rt.config.home, docs)
    nout = sum(len(list((evals_dir(rt.config.home) / "results" / d["id"]).rglob("*.*"))) for d in docs
               if (evals_dir(rt.config.home) / "results" / d["id"]).is_dir())
    out.append({"id": "outputs", "status": "ok" if not found else "fail",
                "label": "成果中沒有個資或金鑰" if not found else f"有 {len(found)} 處個資或金鑰",
                "detail": f"{nout} 份成果" if not found else "、".join(f"{x['file']}（{x['kind']}）" for x in found[:5])})
    short = [r for r in rep_rows if r["trials"] < MIN_TRIALS]
    out.append({"id": "trials", "status": "ok" if not short else "warn",
                "label": f"每個組合 n ≥ {MIN_TRIALS}" if not short else
                f"{len(short)} 個組合不到 {MIN_TRIALS} 次，公開報告不會包含它們",
                "detail": f"{len(rep_rows)} 組合"})
    jc = [it["judge_check"] for it in c.get("items") or [] if it.get("judge_check")]
    if jc:
        tot, cor = sum(j["total"] for j in jc), sum(j["correct"] for j in jc)
        out.append({"id": "anchors", "status": "ok" if cor == tot else "warn",
                    "label": "評鑑模型錨題全對" if cor == tot else "評鑑模型答錯錨題，報告會被標示",
                    "detail": f"{cor}/{tot}"})
    judge = c.get("judge") or ""
    same = [r for r in rep_rows if judge and sfs.family(judge) in (sfs.family(r["thinking"]), sfs.family(r["execution"]))]
    if same:
        j2 = c.get("judge2")
        agree = [0, 0]
        for d in docs:
            a = (d.get("totals") or {}).get("judge2_agree")
            if a:
                agree = [agree[0] + a[0], agree[1] + a[1]]
        ok = bool(j2) and sfs.family(j2) != sfs.family(judge)
        out.append({"id": "family", "status": "ok" if ok else "warn",
                    "label": (f"同源評鑑已加第二評鑑，一致 {agree[0]}/{agree[1]}" if ok else
                              f"評鑑模型與 {len(same)} 個組合同源，沒有另一家族的第二評鑑"),
                    "detail": "可提交" if ok else "報告會標示同源"})
    pub = _skill_published(rt, c.get("skill"))
    out.append({"id": "skill", "status": "ok" if pub else "warn",
                "label": "技能已發布到 Skill Hub" if pub else
                f"技能還沒發布：公開報告會一起把 {c.get('skill')} {c.get('skill_version') or ''} 發布到 Skill Hub，讀者才能重現",
                "detail": "" if pub else "公開時必要"})
    return out


def security_summary(docs_by_key: dict[str, list[dict]]) -> dict:
    """Per combination: how many trials passed each security check."""
    out = {}
    for k, docs in docs_by_key.items():
        tally: dict[str, list[int]] = {}
        for d in docs:
            for t in d.get("tasks") or []:
                for x in t.get("trials") or []:
                    for g in x.get("grades") or []:
                        if g.get("type") == "security":
                            v = tally.setdefault(g.get("label") or "security", [0, 0])
                            v[0] += 1 if g.get("ok") else 0
                            v[1] += 1
        out[k] = {lab: {"ok": v[0], "total": v[1]} for lab, v in tally.items()}
    return out


def results(rt, cid: str) -> dict:
    from .publish import PublishError, payloads
    home = rt.config.home
    c = get(home, cid)
    docs = _docs(home, c)
    rows, reps = [], []
    if docs:
        try:
            reps = payloads(docs, home=None)
        except PublishError as e:
            return {"campaign": summary(c), "error": str(e), "board": None, "checks": []}
        rows = [_row(r) for r in reps]
    b = board(rows)
    by_key: dict[str, list[dict]] = {}
    for d in docs:
        by_key.setdefault(ckey(d.get("thinking") or d.get("model"), d.get("execution") or d.get("model")), []).append(d)
    base = next((ckey(x["thinking"], x["execution"]) for x in c["combos"] if x.get("baseline")), None)
    return {"campaign": {**summary(c), "combos": c["combos"], "creativity": c.get("creativity"),
                         "judge": c.get("judge"), "judge2": c.get("judge2"), "evaluator": c.get("evaluator"),
                         "published": c.get("published") or []},
            "baseline": base, "board": b, "security": security_summary(by_key),
            "checks": checks(rt, c, docs, rows) if docs else []}


# ---------------------------------------------------------------- publishing

def upload_suite(config, ref: str) -> dict | None:
    """Put the suite on sinobee.ai so others can run it too (public reports only; a 404 = older sinobee.ai)."""
    from .. import account
    from .authoring import find, read_package
    folder = find(config.home, ref)
    suite, texts = read_package(folder)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    body = {"hash": meta["hash"], "skill": meta.get("skill"), "skill_version": meta.get("skill_version"),
            "evaluator": meta.get("evaluator"), "evaluator_version": meta.get("evaluator_version"),
            "title": suite.get("title"), "suite": suite, "inputs": texts}
    try:
        r = account.send(config, "POST", f"{account.base_url(config)}/api/v1/evals/suites", scope="evals:write",
                         json=body, timeout=60)
    except account.AccountError as e:
        raise EvalError(str(e)) from None
    if r.status_code in (404, 405):
        return None
    if r.status_code >= 400:
        raise EvalError(f"sinobee.ai HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def publish(rt, cid: str, visibility: str = "public") -> dict:
    from . import publish as P
    home = rt.config.home
    c = get(home, cid)
    if c["status"] not in ("done", "budget", "stopped", "published"):
        raise EvalError("評鑑還沒結束")
    if visibility not in ("public", "private"):
        raise EvalError("visibility 必須是 public 或 private")
    res = results(rt, cid)
    if res.get("error"):
        raise EvalError(res["error"])
    fails = [x for x in res["checks"] if x["status"] == "fail"]
    if fails and visibility == "public":
        raise EvalError("提交前檢查沒有通過：" + "；".join(x["label"] for x in fails))
    docs = _docs(home, c)
    rows = {r["key"]: r for r in res["board"]["rows"]}
    if visibility == "public":
        docs = [d for d in docs if rows.get(ckey(d.get("thinking") or d.get("model"),
                                                 d.get("execution") or d.get("model")), {}).get("trials", 0) >= MIN_TRIALS]
        if not docs:
            raise EvalError(f"沒有組合達到 {MIN_TRIALS} 次試驗，無法公開；可以先存成私人報告")
    out = {"visibility": visibility, "skill_published": None, "suite_uploaded": None, "reports": []}
    if visibility == "public":
        if not _skill_published(rt, c.get("skill")):
            from ..skills import hub
            out["skill_published"] = hub.publish(rt.skills, c["skill"], rt.config)
        if str(c["suite"]).startswith("suite:"):
            out["suite_uploaded"] = upload_suite(rt.config, c["suite"])
    extra = {"visibility": visibility, "evaluator": c.get("evaluator"), "suite_ref": c.get("suite_hash"),
             "campaign": c["id"], "subject_version": c.get("skill_version") or ""}
    out["reports"] = P.publish(rt.config, docs, extra=extra)
    c["published"] = (c.get("published") or []) + [{"at": time.time(), "visibility": visibility,
                                                     "reports": out["reports"]}]
    c["status"] = "published" if visibility == "public" else c["status"]
    save(home, c)
    return out
