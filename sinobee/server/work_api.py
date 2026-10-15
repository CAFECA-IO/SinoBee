"""Background work endpoints (/api/work/*): overview (past / now / next), jobs, runs, workflows, approvals."""
from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException

from ..work import schedule as S
from ..work.graph import WorkflowError
from ..work.hub import WorkError, get_hub


def register(app: FastAPI, rt_getter, chat_running) -> None:
    def hub():
        return get_hub(rt_getter())

    def guard(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except (WorkError, WorkflowError, S.ScheduleError, ValueError) as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/work")
    async def overview(history: int = 40):
        return hub().overview(chat_running=chat_running(), history_limit=max(5, min(history, 200)))

    @app.get("/api/work/workflows")
    async def workflows():
        from ..work.graph import levels
        return {"workflows": [{"name": w["name"], "title": w["title"], "description": w["description"],
                               "inputs": w["inputs"], "bundled": w.get("bundled", False),
                               "nodes": [{"id": n["id"], "title": n.get("title") or n["id"], "type": n["type"],
                                          "role": n.get("role"), "needs": n.get("needs") or []} for n in w["nodes"]],
                               "levels": levels(w)} for w in hub().workflows().values()]}

    @app.post("/api/work/workflows/{name}/run")
    async def run_workflow(name: str, body: dict):
        return guard(hub().start_workflow, name, body.get("inputs") or {}, trigger="manual")

    @app.post("/api/work/schedule/preview")
    async def preview(body: dict):
        s = guard(S.validate, body.get("schedule"))
        return {"schedule": s, "text": S.describe(s), "upcoming": S.upcoming(s, time.time(), 5)}

    @app.post("/api/work/jobs")
    async def create_job(body: dict):
        job = guard(hub().create_job, body, "user")
        if body.get("run_now"):
            guard(hub().run_job, job["id"], "manual")
        return job

    @app.patch("/api/work/jobs/{jid}")
    async def update_job(jid: str, body: dict):
        return guard(hub().update_job, jid, body)

    @app.delete("/api/work/jobs/{jid}")
    async def delete_job(jid: str):
        guard(hub().delete_job, jid)
        return {"ok": True}

    @app.post("/api/work/jobs/{jid}/run")
    async def run_job(jid: str):
        return guard(hub().run_job, jid, "manual")

    @app.get("/api/work/jobs/{jid}")
    async def job_detail(jid: str):
        h = hub()
        job = h.store.job(jid)
        if not job:
            raise HTTPException(404, "找不到工作")
        return {**h.public_job(job), "runs": h.store.runs(job_id=jid, limit=50),
                "board": h.store.board_read(f"job:{jid}", limit=100)}

    @app.get("/api/work/runs/{rid}")
    async def run_detail(rid: str):
        return guard(hub().run_detail, rid)

    @app.post("/api/work/runs/{rid}/stop")
    async def stop(rid: str):
        guard(hub().cancel, rid)
        return {"ok": True}

    @app.post("/api/work/runs/{rid}/resume")
    async def resume(rid: str):
        return guard(hub().resume, rid)

    @app.post("/api/work/approvals/{aid}")
    async def decide(aid: str, body: dict):
        return guard(hub().decide, aid, bool(body.get("ok")))

    @app.get("/api/work/board")
    async def board(scope: str, limit: int = 100):
        return {"entries": hub().store.board_read(scope, limit=max(1, min(limit, 500)))}
