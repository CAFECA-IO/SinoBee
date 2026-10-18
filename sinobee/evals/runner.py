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
from pathlib import Path

from ..config import Config
from . import EvalError, evals_dir, load
from .graders import Trial, grade

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


async def run_trial(rt, task: dict, trial: int, *, keep: bool = False) -> dict:
    from ..agent.loop import Agent
    tmp = Path(tempfile.mkdtemp(prefix="sinobee-eval-"))
    home, ws = tmp / "home", tmp / "workspace"
    home.mkdir()
    ws.mkdir()
    started = time.time()
    out = {"task": task["id"], "trial": trial, "passed": False, "grades": [], "status": "error", "steps": 0,
           "tool_calls": 0, "cost_usd": 0.0, "duration_s": 0.0}
    child = None
    try:
        if rt.config.skills_dir.is_dir():         # the user's skills, as a copy the trial may not change for real
            shutil.copytree(rt.config.skills_dir, home / "skills", dirs_exist_ok=True, symlinks=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules"))
        overrides = {"learning.enabled": False, "permissions.approval": "auto", "agent.snapshots": False,
                     "server.token": None}
        if task.get("toolsets"):
            overrides["permissions.toolsets"] = list(task["toolsets"])
        cfg = EvalConfig(rt.config, home, ws, overrides)
        _setup(ws, (task.get("setup") or {}).get("files") or {})
        before = _scan(ws)
        child = child_runtime(rt, cfg)
        if child.provider_error:
            raise EvalError(f"model not available: {child.provider_error}")
        events: dict = {"tools": [], "end": {}}

        def emit(kind, data):
            if kind == "tool_call":
                events["tools"].append(data.get("name"))
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
        judge = rt.strong() or rt.provider
        t = Trial(prompt=task["prompt"], workspace=ws, answer=answer or end.get("text", ""), status=status,
                  tools=events["tools"], files=changed, base_home=rt.config.home, judge=judge)
        for g in task["graders"]:
            ok, why = await grade(g, t)
            out["grades"].append({"type": g["type"], "ok": ok, "detail": why})
        out.update(passed=all(x["ok"] for x in out["grades"]), status=status, steps=end.get("steps", 0),
                   tool_calls=len(events["tools"]), cost_usd=round((end.get("usage") or {}).get("cost_usd") or 0, 5),
                   answer=(answer or "")[:1500], files=[str(p.relative_to(ws)) for p in changed][:30])
    except EvalError as e:
        out["error"] = str(e)
    except Exception as e:  # noqa: BLE001 - one broken trial must not stop the run
        log.exception("eval trial failed")
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        out["duration_s"] = round(time.time() - started, 1)
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
            "duration_s": round(sum(t["duration_s"] for t in trials), 1)}


async def run(rt, target: str = "core", *, k: int | None = None, task_ids: list[str] | None = None, emit=None,
              keep: bool = False) -> dict:
    home = rt.config.home
    suite, tasks = load(target, home=home, skills=rt.skills)
    if task_ids:
        tasks = [t for t in tasks if t["id"] in task_ids]
        if not tasks:
            raise EvalError(f"no task {', '.join(task_ids)} in {suite}")
    rid = time.strftime("%Y%m%d-%H%M%S") + "-" + re.sub(r"[^\w-]+", "-", suite)[-30:].strip("-")
    path = evals_dir(home) / "results" / f"{rid}.json"
    doc = {"id": rid, "suite": suite, "status": "running", "started": time.time(), "k": k,
           "model": getattr(rt.provider, "label", ""), "progress": {"done": 0, "total": len(tasks)}, "tasks": []}

    def save():
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    save()
    try:
        for task in tasks:
            n = int(k or task["trials"])
            if emit:
                emit("eval_progress", {"id": rid, "task": task["id"], "done": doc["progress"]["done"],
                                       "total": len(tasks)})
            trials = [await run_trial(rt, task, i + 1, keep=keep) for i in range(n)]
            doc["tasks"].append({"id": task["id"], "kind": task["kind"], "prompt": task["prompt"][:300],
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
