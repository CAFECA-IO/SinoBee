"""Run eval tasks: every trial in a fresh, throw-away SinoBee (its own home and workspace, approvals automatic,
learning off, nothing written to the user's memory or skills), then grade it.

Metrics per task over k trials:
  pass@k   at least one trial passed     - can the agent do it at all?
  pass^k   every trial passed            - can it be relied on? (what users feel)
A run is compared with the previous finished run of the same suite: tasks that passed then and fail now are listed
as regressions, the reverse as fixed. Cost, steps and time are recorded so a cheaper setup can be compared fairly.
The model calls are real; their cost is added to the user's usage under purpose "eval".
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from ..config import Config
from . import EvalError, bench_meta, evals_dir, load
from . import sfs
from .graders import Trial, grade_full

log = logging.getLogger("sinobee.evals")


class EvalConfig(Config):
    """The user's settings, but with a temporary home and workspace."""

    def __init__(self, base: Config, home: Path, workspace: Path, overrides: dict):
        super().__init__({}, home)
        self.data = copy.deepcopy(base.data)
        self.locked = set(base.locked)
        self._base = base
        self._ws = workspace
        for dotted, value in overrides.items():
            section, _, key = dotted.partition(".")
            self.data.setdefault(section, {})[key] = value

    @property
    def workspace(self) -> Path:
        self._ws.mkdir(parents=True, exist_ok=True)
        return self._ws

    @property
    def project_dir(self) -> Path:
        return self._ws

    @property
    def secrets(self):
        from ..secrets import SecretStore
        return SecretStore(self._base.home / "secrets.json")      # read the user's keys, never written here

    def reload(self) -> None:          # settings are fixed for the length of a trial
        pass


def needs_sandbox(task: dict, bench: dict | None) -> bool:
    """Trials run in the sandbox when the suite asks for it or a grader checks security."""
    return bool((bench or {}).get("sandbox") or task.get("security")
                or any(g.get("type") == "security" for g in task.get("graders") or []))


def network_allowed(rt, task: dict, bench: dict | None) -> bool:
    """The task, the suite or the skill under test (metadata sinobee-network: true) declares network use."""
    if task.get("network") or (bench or {}).get("network"):
        return True
    name = (bench or {}).get("skill")
    try:
        s = rt.skills.skills.get(name) if name and rt.skills else None
    except Exception:  # noqa: BLE001
        s = None
    return bool(s) and str(s.metadata.get("sinobee-network", "")).strip().lower() in ("true", "yes", "1")


def child_runtime(rt, cfg):
    """The throw-away runtime of one trial (tests replace this to use a scripted model)."""
    from ..agent.loop import Runtime
    return Runtime(cfg)


def _setup(ws: Path, files: dict) -> None:
    for rel, spec in (files or {}).items():
        rel_p = Path(rel)
        if rel_p.is_absolute() or ".." in rel_p.parts:
            raise EvalError(f"setup file {rel!r} must be a relative path inside the workspace")
        dest = ws / rel_p
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(spec, str):
            dest.write_text(spec, encoding="utf-8")
        elif isinstance(spec, dict) and "base64" in spec:
            dest.write_bytes(base64.b64decode(spec["base64"]))
        elif isinstance(spec, dict) and "from" in spec:
            shutil.copyfile(spec["from"], dest)
        elif isinstance(spec, dict) and "text" in spec:
            dest.write_text(str(spec["text"]), encoding="utf-8")
        else:
            raise EvalError(f"setup file {rel!r}: give text, {{base64}} or {{from}}")


def _scan(ws: Path) -> dict[Path, tuple[float, int]]:
    out = {}
    for p in ws.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            st = p.stat()
            out[p] = (st.st_mtime, st.st_size)
    return out


def solo(model: str) -> dict:
    """Settings that make one model do everything in a trial - every step, the plan, advice, reviews and auxiliary
    work - so a model evaluation measures that model, not a mix with the user's thinking or reviewer model."""
    return {"model.default": model, "model.fallbacks": [], "model.thinking": None, "model.escalate": None,
            "model.reviewer": None, "model.auxiliary": model}


def combo(thinking: str, execution: str) -> dict:
    """Settings for a skill evaluation's model combination: the thinking model plans, advises and reviews, the
    execution model runs every step (and the auxiliary work), nothing else joins in."""
    return {"model.default": execution, "model.fallbacks": [], "model.thinking": thinking, "model.escalate": None,
            "model.reviewer": thinking, "model.auxiliary": execution}


def judge_provider(rt, spec: str | None, usage: dict):
    """The model that grades llm rubrics: `spec` alone (no fallbacks), else the user's reviewer / thinking model.
    Its tokens are counted in `usage` (they are not part of the contestant's cost)."""
    if not spec:
        return rt.judge()
    from ..providers import build_provider
    from ..providers.base import FallbackChain
    chain = FallbackChain([build_provider(spec, rt.config)])

    def record(provider, u, purpose=None):
        usage["calls"] = usage.get("calls", 0) + 1
        usage["input_tokens"] = usage.get("input_tokens", 0) + u.input_tokens
        usage["output_tokens"] = usage.get("output_tokens", 0) + u.output_tokens
        try:
            rt._record(provider, u, "eval")
        except Exception:  # noqa: BLE001
            pass
    chain.recorder = record
    return chain


TEXT_OUT = {".md", ".txt", ".html", ".htm", ".json", ".csv", ".py", ".js", ".ts", ".css", ".svg", ".yaml", ".yml"}
IMG_OUT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _save_outputs(ws: Path, files: list[Path], globs: list[str] | None, dest: Path) -> list[dict]:
    """Keep what the trial produced (the report shows it, and creativity comparisons read it): the suite's
    `bench.outputs` globs, else the files the agent changed; text up to 300 KB, images up to 3 MB, 12 files."""
    picked: list[Path] = []
    if globs:
        for pat in globs:
            picked += [p for p in sorted(ws.glob(pat)) if p.is_file() and p not in picked]
    else:
        picked = [p for p in files if p.is_file()]
    out = []
    for p in picked:
        ext, size = p.suffix.lower(), p.stat().st_size
        if not ((ext in TEXT_OUT and size <= 300_000) or (ext in IMG_OUT and size <= 3_000_000)):
            continue
        rel = _rel(p, ws)
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest / rel)
        out.append({"path": str(rel), "bytes": size, "kind": "image" if ext in IMG_OUT else "text"})
        if len(out) >= 12:
            break
    return out


def _sig(spec: str | None) -> str:
    return (spec or "").split("/", 1)[-1].lower()


def _role_usage(child, thinking: str | None, execution: str | None) -> dict:
    """Tokens and cost of the trial by role (from the child's usage rows): what the thinking model spent planning
    and reviewing versus what the execution model spent working."""
    out = {"thinking": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
           "execution": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}
    th = _sig(thinking) if thinking and thinking != execution else ""
    try:
        rows = child.memory.db.execute("SELECT model, input_tokens, output_tokens, cost_usd FROM usage").fetchall()
    except Exception:  # noqa: BLE001
        return out
    for r in rows:
        m = str(r["model"] or "").lower()
        role = "thinking" if th and (m == th or th.endswith(m) or m.endswith(th)) else "execution"
        o = out[role]
        o["input_tokens"] += int(r["input_tokens"] or 0)
        o["output_tokens"] += int(r["output_tokens"] or 0)
        o["cost_usd"] = round(o["cost_usd"] + float(r["cost_usd"] or 0), 6)
    return out


def _steps(home: Path, report_id: str | None) -> list[dict]:
    """Each step's tokens, time, retries and whether it failed or produced something (from the run report)."""
    if not report_id:
        return []
    from ..agent import runreport
    try:
        rep = runreport.get(home, report_id)
    except KeyError:
        return []
    return [{"step": s.get("step"), "tokens": int(s.get("tokens") or 0), "wall_s": float(s.get("wall_s") or 0),
             "retries": int(s.get("retries") or 0), "failed": bool(s.get("failed")), "produced": bool(s.get("produced"))}
            for s in (rep.get("step_costs") or [])][:150]


def _rel(p: Path, base: Path) -> Path:
    """p relative to base, whichever way either is spelled (a link such as macOS /var -> /private/var)."""
    try:
        return p.relative_to(base)
    except ValueError:
        return p.resolve().relative_to(base.resolve())


async def run_trial(rt, task: dict, trial: int, *, keep: bool = False, model: str | None = None,
                    judge=None, thinking: str | None = None, judge2=None, bench: dict | None = None,
                    save_dir: Path | None = None, activity=None) -> dict:
    """activity(dict): told about each step of the trial (step number, tool, time) - a campaign shows it live."""
    from ..agent.loop import Agent
    # resolved: on macOS the temp folder /var/folders/... is a link to /private/var/..., and paths from the two
    # spellings never compare equal (every trial on a Mac failed at its end with "is not in the subpath of")
    tmp = Path(tempfile.mkdtemp(prefix="sinobee-eval-")).resolve()
    home, ws = tmp / "home", tmp / "workspace"
    home.mkdir()
    ws.mkdir()
    started = time.time()
    events: dict = {"tools": [], "end": {}}
    out = {"task": task["id"], "trial": trial, "passed": False, "grades": [], "status": "error", "steps": 0,
           "tool_calls": 0, "cost_usd": 0.0, "duration_s": 0.0, "input_tokens": 0, "output_tokens": 0,
           "cache_read_tokens": 0, "started": started}
    child = None
    try:
        if rt.config.skills_dir.is_dir():         # the user's skills, as a copy the trial may not change for real
            shutil.copytree(rt.config.skills_dir, home / "skills", dirs_exist_ok=True, symlinks=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules"))
        overrides = {"learning.enabled": False, "permissions.approval": "auto", "agent.snapshots": False,
                     "server.token": None}
        if task.get("toolsets"):
            overrides["permissions.toolsets"] = list(task["toolsets"])
        if model:
            overrides.update(combo(thinking, model) if thinking and thinking != model else solo(model))
        cfg = EvalConfig(rt.config, home, ws, overrides)
        _setup(ws, (task.get("setup") or {}).get("files") or {})
        sandbox = None
        if needs_sandbox(task, bench):
            from .sandbox import Sandbox
            sandbox = Sandbox(tmp, ws, mode="observe", network=network_allowed(rt, task, bench),
                              extra_roots=[home / "skills"])
            cfg.sandbox = sandbox
            inject = (task.get("security") or {}).get("inject")
            if inject:
                f = ws / inject
                if not f.is_file() or ws.resolve() not in f.resolve().parents:
                    raise EvalError(f"injected input {inject!r} is not a file of the task")
                f.write_text(sandbox.inject(f.read_text(encoding="utf-8")), encoding="utf-8")
        before = _scan(ws)
        child = child_runtime(rt, cfg)
        if child.provider_error:
            raise EvalError(f"model not available: {child.provider_error}")

        def emit(kind, data):
            if kind == "tool_call":
                events["tools"].append(data.get("name"))
                if activity:
                    try:
                        activity({"step": len(events["tools"]), "tool": data.get("name"), "at": time.time()})
                    except Exception:  # noqa: BLE001 - showing progress never breaks a trial
                        pass
            elif kind == "turn_end":
                events["end"] = data

        async def approve(*_a, **_k):
            return True

        agent = Agent(child, platform="eval", emit=emit, approve=approve, learn=False, title=f"eval {task['id']}")
        try:
            answer = await asyncio.wait_for(agent.run(task["prompt"]), timeout=task["timeout"])
            status = (getattr(agent, "last_status", None) or {}).get("status", "done")
        except asyncio.TimeoutError:
            agent.cancel()
            answer, status = "", "timeout"
        await child.drain(10)
        after = _scan(ws)
        changed = [p for p, sig in after.items() if before.get(p) != sig]
        end = events["end"]
        judge = judge if judge is not None else rt.judge()
        usage = end.get("usage") or {}
        trace = _steps(home, end.get("run_report"))
        security = sandbox.report(answer or end.get("text", ""), changed) if sandbox else None
        t = Trial(security=security, prompt=task["prompt"], workspace=ws, answer=answer or end.get("text", ""), status=status,
                  tools=events["tools"], files=changed, base_home=rt.config.home, judge=judge, judge2=judge2,
                  tokens=int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0),
                  steps=int(end.get("steps") or 0), trace=trace)
        tell = _teller(activity)
        tell({"phase": "grade", "graded": 0, "questions": len(task["graders"]), "files": len(changed)})
        for g in task["graders"]:
            r = await grade_full(g, t)
            out["grades"].append({"type": g["type"], "label": sfs.question(g)["label"], **r})
            tell({"phase": "grade", "graded": len(out["grades"]), "questions": len(task["graders"])})
        if security is not None:
            out["security"] = security
        if bench is not None:
            qs = [sfs.question(g) for g in task["graders"]]
            out.update(sfs.trial_score([x["ok"] for x in out["grades"]], qs, sfs.weights(bench, qs),
                                       float(bench.get("threshold") or 70)))
            out["steps_trace"] = trace
            out["roles"] = _role_usage(child, thinking, model)
            if save_dir is not None:
                out["outputs"] = _save_outputs(ws, changed, bench.get("outputs"),
                                               save_dir / re.sub(r"[^\w.-]+", "-", task["id"]) / f"t{trial}")
        out.update(passed=all(x["ok"] for x in out["grades"]), status=status, steps=end.get("steps", 0),
                   tool_calls=len(events["tools"]), cost_usd=round((end.get("usage") or {}).get("cost_usd") or 0, 5),
                   input_tokens=int((end.get("usage") or {}).get("input_tokens") or 0),
                   output_tokens=int((end.get("usage") or {}).get("output_tokens") or 0),
                   cache_read_tokens=int((end.get("usage") or {}).get("cache_read_tokens") or 0),
                   model=getattr(child.provider, "label", ""),
                   answer=(answer or "")[:1500], files=[str(_rel(p, ws)) for p in changed][:30])
    except EvalError as e:
        out["error"] = str(e)
    except Exception as e:  # noqa: BLE001 - one broken trial must not stop the run
        log.exception("eval trial failed")
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        out["duration_s"] = round(time.time() - started, 1)
        end = events.get("end") or {}
        if out.get("error") and end and not out.get("input_tokens"):
            # the trial ran and then something after it failed: its cost still counts (budget, report)
            u = end.get("usage") or {}
            out.update(steps=end.get("steps", 0), cost_usd=round(u.get("cost_usd") or 0, 5),
                       input_tokens=int(u.get("input_tokens") or 0), output_tokens=int(u.get("output_tokens") or 0),
                       cache_read_tokens=int(u.get("cache_read_tokens") or 0),
                       tool_calls=len(events["tools"]))
        if child is not None:
            _copy_usage(child, rt, task["id"])
            try:
                child.memory.db.close()
            except Exception:  # noqa: BLE001
                pass
        if keep:
            out["workdir"] = str(tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    return out


def _copy_usage(child, rt, tid: str) -> None:
    """The trial's model calls cost real money: record them in the user's usage too."""
    try:
        rows = child.memory.db.execute("SELECT * FROM usage").fetchall()
        for r in rows:
            rt.memory.record_usage(session_id=f"eval:{tid}", purpose="eval", provider=r["provider"], model=r["model"],
                                   input_tokens=r["input_tokens"], output_tokens=r["output_tokens"],
                                   cache_read_tokens=r["cache_read_tokens"], cache_write_tokens=r["cache_write_tokens"],
                                   cost_usd=r["cost_usd"], price_source=r["price_source"] or "", ts=r["ts"])
    except Exception:  # noqa: BLE001
        log.debug("could not copy eval usage", exc_info=True)


def _previous(home, suite: str, rid: str) -> dict | None:
    files = sorted((evals_dir(home) / "results").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        if f.stem == rid:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("suite") == suite and data.get("status") == "done":
            return data
    return None


def summarize(tasks: list[dict]) -> dict:
    n = len(tasks) or 1
    trials = [t for x in tasks for t in x["trials"]]
    return {"tasks": len(tasks), "trials": len(trials),
            "pass_at_k": round(sum(x["pass_at_k"] for x in tasks) / n, 3),
            "pass_hat_k": round(sum(x["pass_hat_k"] for x in tasks) / n, 3),
            "trial_pass_rate": round(sum(t["passed"] for t in trials) / (len(trials) or 1), 3),
            "cost_usd": round(sum(t["cost_usd"] for t in trials), 4),
            "avg_steps": round(sum(t["steps"] for t in trials) / (len(trials) or 1), 1),
            "duration_s": round(sum(t["duration_s"] for t in trials), 1),
            "avg_duration_s": round(sum(t["duration_s"] for t in trials) / (len(trials) or 1), 1),
            "input_tokens": sum(t.get("input_tokens", 0) for t in trials),
            "output_tokens": sum(t.get("output_tokens", 0) for t in trials),
            "score": round(100 * sum(sum(g["ok"] for g in t["grades"]) / (len(t["grades"]) or 1) for t in trials)
                           / (len(trials) or 1), 1),
            "judge_errors": sum(1 for t in trials for g in t["grades"] if g.get("judge_error")), **sfs_summary(tasks)}


def trial_scores(tasks: list[dict]) -> list[float]:
    """The score of trial i over the whole suite: the mean of each task's i-th trial (tasks weigh the same)."""
    k = min((len(x["trials"]) for x in tasks), default=0)
    return [round(sum(x["trials"][i].get("q", 0) for x in tasks) / len(tasks), 2) for i in range(k)] if tasks else []


def sfs_summary(tasks: list[dict]) -> dict:
    trials = [t for x in tasks for t in x["trials"]]
    if not trials or any("q" not in t for t in trials):
        return {}
    out = {"sfs": sfs.fair(trial_scores(tasks)),
           "qualified": sum(1 for t in trials if t.get("qualified")), "crit_fails": sum(1 for t in trials if t.get("crit_fail"))}
    pairs = [(g["ok"], g["judge2_ok"]) for t in trials for g in t["grades"] if "judge2_ok" in g]
    if pairs:
        out["judge2_agree"] = [sum(1 for a, b in pairs if a == b), len(pairs)]
    return out


ANCHOR = """You grade a result against a rubric. Be strict: pass only when the result clearly satisfies it.
RUBRIC: {rubric}
RESULT:
{content}
Reply with JSON only: {{"pass": true|false, "reason": "<one sentence>"}}"""


def _teller(activity):
    """activity() that never breaks the evaluation, with the time added."""
    def tell(d: dict) -> None:
        if activity:
            try:
                activity({**d, "at": time.time()})
            except Exception:  # noqa: BLE001 - showing progress never breaks a trial
                log.debug("activity failed", exc_info=True)
    return tell


async def check_judge(judge, anchors: list[dict]) -> dict:
    """Calibration: rubrics with a known answer, asked before the trials are graded. A judge that gets any wrong is
    reported (sinobee.ai marks the evaluation), so a lenient or confused judge cannot pass unnoticed."""
    from .graders import ask_json
    items = []
    for a in anchors or []:
        try:
            got = (await ask_json(judge, ANCHOR.format(rubric=str(a["rubric"])[:2000],
                                                       content=str(a["content"])[:8000]), "pass")).get("pass")
        except Exception as e:  # noqa: BLE001
            got = None
            log.warning("anchor check failed: %s", e)
        items.append({"expect": bool(a.get("expect")), "got": got, "ok": got is not None and bool(got) == bool(a.get("expect"))})
    return {"total": len(items), "correct": sum(1 for i in items if i["ok"]), "items": items}


def _bench_task(task: dict) -> dict:
    """In a published evaluation every llm rubric is voted on three times and must quote its evidence."""
    graders = [{**g, "votes": g.get("votes", 3), "require_evidence": g.get("require_evidence", True)}
               if g.get("type") == "llm" else g for g in task["graders"]]
    return {**task, "graders": graders}


async def run(rt, target: str = "core", *, k: int | None = None, task_ids: list[str] | None = None, emit=None,
              keep: bool = False, model: str | None = None, judge: str | None = None, thinking: str | None = None,
              judge2: str | None = None, anchors: bool = True, activity=None) -> dict:
    """model: the model under test - alone in every role (solo()), or with `thinking` the execution model of a
    combination (combo()); judge: the model that grades llm rubrics; judge2: a second judge (another family) whose
    agreement is reported."""
    if thinking and not model:
        raise EvalError("a thinking model needs an execution model (-m)")
    home = rt.config.home
    suite, tasks = load(target, home=home, skills=rt.skills)
    meta = bench_meta(target, home)
    bench = meta.get("bench") if meta.get("bench") else None
    if bench is not None:
        tasks = [_bench_task(t) for t in tasks]
    if task_ids:
        tasks = [t for t in tasks if t["id"] in task_ids]
        if not tasks:
            raise EvalError(f"no task {', '.join(task_ids)} in {suite}")
    # unique even for two runs in the same second (the second used to overwrite the first and lose its baseline)
    rid = (time.strftime("%Y%m%d-%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}-" + uuid.uuid4().hex[:4] + "-"
           + re.sub(r"[^\w-]+", "-", suite)[-30:].strip("-"))
    path = evals_dir(home) / "results" / f"{rid}.json"
    from .. import __version__
    judge_usage: dict = {}
    jprov = judge_provider(rt, judge, judge_usage)
    j2prov = judge_provider(rt, judge2, judge_usage) if judge2 else None
    execution = model or getattr(rt.provider, "label", "")
    doc = {"id": rid, "suite": suite, "status": "running", "started": time.time(), "k": k,
           "model": execution, "solo": bool(model) and (not thinking or thinking == model),
           "thinking": thinking if thinking and thinking != model else (model or None), "execution": execution,
           "judge": judge or getattr(jprov, "label", "") or None, "judge2": judge2, "judge_usage": judge_usage,
           "sinobee_version": __version__, **meta, "progress": {"done": 0, "total": len(tasks)}, "tasks": []}
    save_dir = None
    if bench is not None:
        doc["suite_hash"] = sfs.suite_hash(tasks, bench)
        doc["input_hash"] = sfs.input_hash(tasks)
        save_dir = evals_dir(home) / "results" / rid

    def save():
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    save()
    try:
        if bench is not None and bench.get("anchors") and anchors:      # a campaign checks the judge once
            _teller(activity)({"phase": "anchors", "anchors": len(bench["anchors"])})
            doc["judge_check"] = await check_judge(jprov, bench["anchors"])
            save()
            if doc["judge_check"]["total"] and all(i["got"] is None for i in doc["judge_check"]["items"]):
                # a judge that answers nothing would fail every rubric question: stop before the trials cost money
                raise EvalError(f"評鑑模型 {doc['judge']} 對 {doc['judge_check']['total']} 題錨題都沒有給出判定"
                                "（沒有回應、被截斷或不是 JSON）；沒有開始試驗。請換一個評鑑模型（--judge）或稍後再試。")
        for ti, task in enumerate(tasks):
            n = int(k or task["trials"])
            if emit:
                emit("eval_progress", {"id": rid, "task": task["id"], "done": doc["progress"]["done"],
                                       "total": len(tasks)})
            act = None
            if activity:
                def act(d, _t=task["id"], _i=ti + 1, _title=str(task.get("title") or task["id"])[:80]):
                    activity({"phase": "run", **d, "task": _t, "title": _title, "task_index": _i, "tasks": len(tasks)})
                act({"step": 0, "tool": None, "at": time.time(), "graded": None})
            trials = [await run_trial(rt, task, i + 1, keep=keep, model=model, judge=jprov, thinking=thinking,
                                      judge2=j2prov, bench=bench, save_dir=save_dir, activity=act) for i in range(n)]
            entry = {}
            if bench is not None:
                qs = [sfs.question(g) for g in task["graders"]]
                entry = {"questions": qs, "weights": sfs.weights(bench, qs)}
            doc["tasks"].append({"id": task["id"], "kind": task["kind"], "prompt": task["prompt"][:1500],
                                 "title": task.get("title") or task["id"], **entry,
                                 "graders": [g.get("label") or g["type"] for g in task["graders"]],
                                 "pass_at_k": any(t["passed"] for t in trials),
                                 "pass_hat_k": all(t["passed"] for t in trials), "trials": trials})
            doc["progress"]["done"] += 1
            doc["totals"] = summarize(doc["tasks"])
            save()
        prev = _previous(home, suite, rid)
        if prev:
            was = {t["id"]: t["pass_at_k"] for t in prev.get("tasks") or []}
            doc["regressions"] = [t["id"] for t in doc["tasks"] if was.get(t["id"]) and not t["pass_at_k"]]
            doc["fixed"] = [t["id"] for t in doc["tasks"] if was.get(t["id"]) is False and t["pass_at_k"]]
            doc["previous"] = {"id": prev["id"], "totals": prev.get("totals")}
        doc["status"] = "done"
    except asyncio.CancelledError:
        doc["status"] = "stopped"
        raise
    except Exception as e:  # noqa: BLE001
        doc["status"], doc["error"] = "error", str(e)
        raise
    finally:
        doc["finished"] = time.time()
        doc["totals"] = summarize(doc["tasks"])
        save()
        if emit:
            emit("eval_done", {"id": rid, "status": doc["status"], "totals": doc["totals"],
                               "regressions": doc.get("regressions", [])})
    return doc


def report(doc: dict) -> str:
    """A terminal-friendly summary."""
    t = doc.get("totals") or {}
    lines = [f"Eval {doc['id']} · {doc['suite']} · {doc.get('model', '')} · {doc['status']}",
             f"pass@k {t.get('pass_at_k', 0):.0%}   pass^k {t.get('pass_hat_k', 0):.0%}   "
             f"trials {t.get('trial_pass_rate', 0):.0%} of {t.get('trials', 0)}   cost ${t.get('cost_usd', 0):.4f}   "
             f"avg steps {t.get('avg_steps', 0)}"]
    for task in doc.get("tasks", []):
        mark = "✓" if task["pass_hat_k"] else ("~" if task["pass_at_k"] else "✕")
        lines.append(f" {mark} {task['id']}")
        for tr in task["trials"]:
            if not tr["passed"]:
                bad = [g for g in tr["grades"] if not g["ok"]]
                why = tr.get("error") or "; ".join(f"{g['type']}: {g['detail']}" for g in bad)[:300]
                lines.append(f"     trial {tr['trial']}: {why}")
    if doc.get("regressions"):
        lines.append("REGRESSIONS (passed last time): " + ", ".join(doc["regressions"]))
    if doc.get("fixed"):
        lines.append("fixed since last run: " + ", ".join(doc["fixed"]))
    return "\n".join(lines)
