"""Quality endpoints: quality rules (/api/rules), evaluations (/api/evals) and per-turn file snapshots."""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, HTTPException

from ..agent import rules as R
from ..agent import snapshots as S
from .. import evals as E

log = logging.getLogger("sinobee.server.quality")


def register(app: FastAPI, rt_getter, broadcast=None) -> None:
    state: dict = {"eval_task": None}

    def home():
        return rt_getter().config.home

    # ------------------------------------------------------------------ rules
    @app.get("/api/rules")
    async def rules_list():
        return {"rules": R.load(home())}

    @app.post("/api/rules")
    async def rules_add(body: dict):
        try:
            return R.add(home(), {**body, "source": {"type": "user"}})
        except R.RuleError as e:
            raise HTTPException(400, str(e)) from None

    @app.patch("/api/rules/{rid}")
    async def rules_update(rid: str, body: dict):
        allowed = {k: body[k] for k in ("enabled", "title", "message", "files", "pattern", "kind", "flags") if k in body}
        try:
            return R.update(home(), rid, **allowed)
        except R.RuleError as e:
            raise HTTPException(400, str(e)) from None

    @app.delete("/api/rules/{rid}")
    async def rules_delete(rid: str):
        try:
            R.remove(home(), rid)
        except R.RuleError as e:
            raise HTTPException(404, str(e)) from None
        return {"ok": True}

    # ------------------------------------------------------------------ evals
    @app.get("/api/evals")
    async def evals_overview():
        rt = rt_getter()
        running = state["eval_task"] is not None and not state["eval_task"].done()
        return {"suites": E.suites(home(), rt.skills), "runs": E.results(home(), 20), "running": running,
                "regressions": E.regressions(home())}

    @app.get("/api/evals/runs/{rid}")
    async def evals_run(rid: str):
        try:
            return E.result(home(), rid)
        except E.EvalError as e:
            raise HTTPException(404, str(e)) from None

    @app.post("/api/evals/run")
    async def evals_start(body: dict):
        from ..evals.runner import run
        if state["eval_task"] is not None and not state["eval_task"].done():
            raise HTTPException(409, "an evaluation is already running")
        rt = rt_getter()
        suite = str(body.get("suite") or "core")
        k = body.get("k")
        k = max(1, min(int(k), 10)) if k else None
        tasks = body.get("tasks") or None
        try:     # fail fast on a bad suite before starting in the background
            E.load(suite, home=home(), skills=rt.skills)
        except E.EvalError as e:
            raise HTTPException(400, str(e)) from None

        def emit(kind, data):
            if broadcast:
                broadcast(kind, data)

        async def go():
            try:
                await run(rt, suite, k=k, task_ids=tasks, emit=emit)
            except Exception:  # noqa: BLE001
                log.exception("evaluation failed")

        state["eval_task"] = asyncio.ensure_future(go())
        return {"ok": True, "suite": suite}

    @app.post("/api/evals/stop")
    async def evals_stop():
        t = state["eval_task"]
        if t is None or t.done():
            return {"ok": False}
        t.cancel()
        return {"ok": True}

    @app.delete("/api/evals/regressions/{tid}")
    async def evals_reg_delete(tid: str):
        try:
            E.remove_regression(home(), tid)
        except E.EvalError as e:
            raise HTTPException(404, str(e)) from None
        return {"ok": True}

    # ------------------------------------------------------------------ failure reports
    from ..agent import failures as F

    @app.get("/api/failures")
    async def failures_list(limit: int = 50):
        return {"failures": F.listing(home(), max(1, min(limit, 300)))}

    @app.get("/api/failures/{rid}")
    async def failures_get(rid: str):
        try:
            return F.get(home(), rid)
        except KeyError:
            raise HTTPException(404, "no such failure report") from None

    @app.delete("/api/failures/{rid}")
    async def failures_delete(rid: str):
        try:
            F.delete(home(), rid)
        except KeyError:
            raise HTTPException(404, "no such failure report") from None
        return {"ok": True}

    @app.post("/api/failures/{rid}/regression")
    async def failures_regression(rid: str):
        try:
            return F.to_regression(home(), rid)
        except KeyError:
            raise HTTPException(404, "no such failure report") from None
        except E.EvalError as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/api/failures/{rid}/diagnose")
    async def failures_diagnose(rid: str):
        dg = await F.diagnose(rt_getter(), home(), rid, emit=broadcast)
        if dg is None:
            raise HTTPException(404, "no such failure report")
        return dg

    # ------------------------------------------------------------------ snapshots
    @app.get("/api/sessions/{sid}/snapshots/{key}")
    async def snapshot_info(sid: str, key: str):
        try:
            data = S.info(home(), sid, key)
        except S.SnapshotError as e:
            raise HTTPException(400, str(e)) from None
        if not data:
            raise HTTPException(404, "no snapshot")
        return data

    @app.post("/api/sessions/{sid}/snapshots/{key}/restore")
    async def snapshot_restore(sid: str, key: str):
        try:
            return await asyncio.to_thread(S.restore, home(), sid, key)
        except S.SnapshotError as e:
            raise HTTPException(404, str(e)) from None
