"""Skill evaluation endpoints (the evaluation panel): evaluation skills, suites, campaigns, results and publishing.

  GET  /api/evals/evaluators?skill=          evaluation skills for a skill, with their 4 security checks
  GET  /api/evals/suites?skill=              suites of a skill (this computer)
  GET  /api/evals/suites/{ref}               a suite: inputs, questions, checks
  POST /api/evals/suites/generate            {skill, session_id, turn_id?, evaluator} -> {job}; events suite_progress/_done
  GET  /api/evals/suites/jobs/{job}
  GET  /api/evals/candidates?session_id=     models to pick from, the task's own combination (the baseline), judges
  POST /api/evals/estimate                   {suite, combos, k, judge, judge2, session_id} -> money and time
  POST /api/evals/campaigns                  create and start (or queue); events campaign_progress / campaign_done
  GET  /api/evals/campaigns?session_id=      GET /api/evals/campaigns/{id}   GET …/{id}/results
  POST /api/evals/campaigns/{id}/stop        POST …/{id}/resume              POST …/{id}/publish {visibility}
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time

from fastapi import FastAPI, HTTPException

from .. import evals as E
from ..evals import authoring as A
from ..evals import campaign as C
from ..evals import sfs

log = logging.getLogger("sinobee.server.evals")


def register(app: FastAPI, rt_getter, broadcast=None) -> None:
    jobs: dict[str, dict] = {}

    def emit(kind, data):
        if broadcast:
            try:
                broadcast(kind, data)
            except Exception:  # noqa: BLE001
                log.debug("broadcast failed", exc_info=True)

    def bad(e: Exception, code: int = 400):
        raise HTTPException(code, str(e)) from None

    # ------------------------------------------------------------------ evaluation skills and suites
    @app.get("/api/evals/evaluators")
    async def evaluators(skill: str):
        rt = rt_getter()
        rt.skills.refresh()
        counts: dict[str, int] = {}
        for m in A.list_suites(rt.config.home, skill):
            counts[m.get("evaluator") or ""] = counts.get(m.get("evaluator") or "", 0) + 1
        out = []
        for s in rt.skills.evaluators(skill):
            st = A.security_status(rt.config.home, s)
            out.append({"name": s.name, "version": s.version, "description": s.description, "trust": s.trust,
                        "trust_label": s.info()["trust_label"], "targets": s.targets, "default": s.name == "eval-authoring",
                        "security": {k: st[k] for k in ("static", "behavior", "suite", "review", "passed", "total",
                                                        "blocked")},
                        "suites": counts.get(s.name, 0)})
        return {"skill": skill, "evaluators": out}

    @app.get("/api/evals/suites")
    async def suites(skill: str, version: str | None = None):
        rt = rt_getter()
        items = A.list_suites(rt.config.home, skill)
        camps = C.listing(rt.config.home, limit=500)
        for m in items:
            m["same_version"] = not version or m.get("skill_version") == version
            m["campaigns"] = sum(1 for c in camps if c.get("suite_hash") == m["hash"])
        return {"skill": skill, "suites": items}

    @app.get("/api/evals/suites/jobs/{job}")
    async def suite_job(job: str):
        if job not in jobs:
            raise HTTPException(404, "no such job")
        return {k: v for k, v in jobs[job].items() if k != "task"}

    @app.get("/api/evals/suites/{ref}")
    async def suite_detail(ref: str):
        try:
            return A.describe(rt_getter().config.home, ref)
        except E.EvalError as e:
            bad(e, 404)

    @app.post("/api/evals/suites/generate")
    async def suite_generate(body: dict):
        rt = rt_getter()
        skill, sid = str(body.get("skill") or ""), str(body.get("session_id") or "")
        evaluator = str(body.get("evaluator") or "eval-authoring")
        if not skill or not sid:
            raise HTTPException(400, "skill and session_id are required")
        try:
            mat = A.task_material(rt, sid, body.get("turn_id"))
            ev = rt.skills.get(evaluator)
        except Exception as e:  # noqa: BLE001
            bad(e)
        if A.security_status(rt.config.home, ev)["blocked"]:
            raise HTTPException(400, f"評鑑技能 {evaluator} 沒有通過安全性評鑑，不能使用")
        job = "sg-" + secrets.token_hex(4)
        info = {"job": job, "skill": skill, "session_id": sid, "evaluator": evaluator, "status": "running",
                "started": time.time(), "hash": None, "problems": [], "violations": [], "steps": 0}
        jobs[job] = info

        def progress(kind, data):
            if kind == "tool_call":
                info["steps"] += 1
                emit("suite_progress", {"job": job, "session_id": sid, "steps": info["steps"],
                                        "tool": data.get("name")})

        async def go():
            try:
                res = await A.generate_suite(rt, evaluator, skill, request=mat["request"], outputs=mat["outputs"],
                                             answer=mat["answer"], session_id=sid, turn_id=mat["turn_id"],
                                             emit=progress)
                info.update(status=res["status"], hash=res.get("hash"), problems=res.get("problems") or [],
                            violations=res.get("violations") or [], rounds=res.get("rounds"))
            except Exception as e:  # noqa: BLE001
                log.exception("suite generation failed")
                info.update(status="error", problems=[str(e)])
            info["finished"] = time.time()
            emit("suite_done", {k: v for k, v in info.items() if k != "task"})
        info["task"] = asyncio.ensure_future(go())
        return {k: v for k, v in info.items() if k != "task"}

    # ------------------------------------------------------------------ combinations
    @app.get("/api/evals/candidates")
    async def candidates(session_id: str | None = None, turn_id: int | None = None):
        rt = rt_getter()
        cfg = rt.config
        mine = [cfg.get(f"model.{r}") for r in ("default", "thinking", "reviewer", "auxiliary")]
        mine += list(cfg.get("model.fallbacks") or [])
        models: list[str] = []
        for m in mine:
            if m and "/" in str(m) and m not in models:
                models.append(m)
        try:
            from ..providers import catalog
            cat = await asyncio.wait_for(catalog.catalog(cfg), timeout=8)
            for p in cat.get("providers") or []:
                if p.get("status") != "ready":
                    continue
                for m in p.get("models") or []:
                    if m.get("kind") == "chat" and m.get("recommended") and m.get("spec") not in models:
                        models.append(m["spec"])
        except Exception:  # noqa: BLE001
            pass
        base = None
        if session_id:
            from ..skills import package
            rep = package._report(cfg.home, session_id, turn_id)
            if rep and (rep.get("models") or {}).get("execution"):
                mm = rep["models"]
                base = {"thinking": mm.get("thinking") or mm["execution"], "execution": mm["execution"],
                        "skills": rep.get("skills") or [], "turn_id": rep.get("turn_id")}
                for m in (base["thinking"], base["execution"]):
                    if m not in models:
                        models.insert(0, m)
        judge = cfg.get("model.reviewer") or cfg.get("model.thinking") or cfg.get("model.default")
        return {"models": models[:16], "baseline": base, "judge": judge}

    @app.post("/api/evals/estimate")
    async def estimate(body: dict):
        rt = rt_getter()
        combos = [{**c, "trials": int(body.get("k") or C.MIN_TRIALS)} for c in body.get("combos") or []]
        try:
            return await asyncio.to_thread(C.estimate, rt, str(body.get("suite") or ""), combos,
                                           judge=body.get("judge"), judge2=body.get("judge2"),
                                           session_id=body.get("session_id"), turn_id=body.get("turn_id"))
        except (E.EvalError, KeyError) as e:
            bad(e)

    @app.get("/api/evals/tested")
    async def tested(skill: str, suite: str):
        rt = rt_getter()
        try:
            from ..evals.runner import _bench_task
            _n, tasks = E.load(suite, home=rt.config.home, skills=rt.skills)
            bench = E.bench_meta(suite, rt.config.home).get("bench") or {}
            h = sfs.suite_hash([_bench_task(t) for t in tasks], bench)
        except E.EvalError as e:
            bad(e)
        return {"suite_hash": h, "tested": await asyncio.to_thread(C.remote_tested, rt.config, skill, h)}

    # ------------------------------------------------------------------ campaigns
    @app.post("/api/evals/campaigns")
    async def campaign_new(body: dict):
        rt = rt_getter()
        try:
            c = await asyncio.to_thread(
                C.new, rt, suite=str(body.get("suite") or ""), combos=body.get("combos") or [],
                k=int(body.get("k") or C.MIN_TRIALS), judge=body.get("judge") or None,
                judge2=body.get("judge2") or None, budget_usd=body.get("budget_usd"),
                session_id=body.get("session_id"), turn_id=body.get("turn_id"),
                rerun_tested=bool(body.get("rerun_tested")))
        except E.EvalError as e:
            bad(e)
        status = C.QUEUE.submit(rt_getter, c["id"], emit)
        return {**C.get(rt.config.home, c["id"]), "queue": status}

    @app.get("/api/evals/campaigns")
    async def campaign_list(session_id: str | None = None):
        rt = rt_getter()
        return {"campaigns": C.listing(rt.config.home, session_id), "running": C.QUEUE.current}

    @app.get("/api/evals/campaigns/{cid}")
    async def campaign_get(cid: str):
        try:
            return C.get(rt_getter().config.home, cid)
        except E.EvalError as e:
            bad(e, 404)

    @app.get("/api/evals/campaigns/{cid}/results")
    async def campaign_results(cid: str):
        try:
            return await asyncio.to_thread(C.results, rt_getter(), cid)
        except E.EvalError as e:
            bad(e, 404)

    @app.post("/api/evals/campaigns/{cid}/stop")
    async def campaign_stop(cid: str):
        try:
            return C.QUEUE.stop(rt_getter().config.home, cid)
        except E.EvalError as e:
            bad(e, 404)

    @app.post("/api/evals/campaigns/{cid}/resume")
    async def campaign_resume(cid: str, body: dict | None = None):
        rt = rt_getter()
        try:
            c = C.get(rt.config.home, cid)
            if (body or {}).get("budget_usd") is not None:
                c["budget_usd"] = round(max(0.0, float(body["budget_usd"])), 2)
                C.save(rt.config.home, c)
        except (E.EvalError, ValueError) as e:
            bad(e)
        return {"queue": C.QUEUE.submit(rt_getter, cid, emit), **C.summary(C.get(rt.config.home, cid))}

    @app.post("/api/evals/campaigns/{cid}/publish")
    async def campaign_publish(cid: str, body: dict | None = None):
        rt = rt_getter()
        try:
            return await asyncio.to_thread(C.publish, rt, cid, str((body or {}).get("visibility") or "public"))
        except E.EvalError as e:
            bad(e)
        except Exception as e:  # noqa: BLE001 - sinobee.ai / Skill Hub errors
            bad(e, 502)
