"""Web UI + REST + WebSocket server."""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from ..agent import Agent, Runtime
from ..config import Config
from ..tools import desktop as desktop_tools
from . import account_api, db_api, evals_api, share_api, files_api, local_api, quality_api, settings_api, skills_api, work_api

log = logging.getLogger("sinobee.server")
STATIC = Path(__file__).resolve().parent / "static"


_BUILD: list = []


def _build() -> str:
    """A short build id: $SINOBEE_BUILD, else the git commit the code runs from, else ''."""
    if not _BUILD:
        b = os.environ.get("SINOBEE_BUILD", "").strip()
        if not b:
            try:
                import subprocess
                b = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).resolve().parents[2],
                                   capture_output=True, text=True, timeout=3).stdout.strip()
            except Exception:  # noqa: BLE001
                b = ""
        _BUILD.append(b[:12])
    return _BUILD[0]


def create_app(runtime: Runtime | None = None) -> FastAPI:
    state: dict = {"rt": runtime, "agents": {}}

    @asynccontextmanager
    async def lifespan(_app):
        hub = None
        if rt().config.get("work.scheduler", True):
            from ..work.hub import get_hub
            hub = get_hub(rt())
            hub.listeners.append(lambda k, d: broadcast(k, d))
            hub.start()
        yield
        if hub:
            await hub.stop()
        from ..tools import browser
        await browser.shutdown()
        if state["rt"]:
            await state["rt"].drain(30)
        if state.get("local"):
            await state["local"].stop_server()   # only stops an `ollama serve` that SinoBee started

    app = FastAPI(title="SinoBee", version="0.2.0", lifespan=lifespan)

    def rt() -> Runtime:
        if state["rt"] is None:
            state["rt"] = Runtime(Config.load())
        return state["rt"]

    def check_token(token: str | None):
        expected = rt().config.server_token()
        if expected and not secrets.compare_digest(str(token or ""), str(expected)):
            raise HTTPException(401, "invalid token")

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            token = request.headers.get("x-sinobee-token") or request.query_params.get("token")
            try:
                check_token(token)
            except HTTPException as e:
                return JSONResponse({"detail": e.detail}, status_code=e.status_code)
        return await call_next(request)

    settings_api.register(app, rt)
    skills_api.register(app, rt)
    files_api.register(app, rt)
    db_api.register(app, rt)
    account_api.register(app, rt)
    share_api.register(app, rt)

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})  # an update shows on reload

    def _mode_and_usage(r) -> dict:
        """P1 (plan "模型分配與備援機制"): the bottom-left card - version, who pays now, today's use for that mode."""
        from .. import __version__, billing
        primary = r.provider.label
        local_prov = ("ollama", "lmstudio", "vllm", "llamacpp")
        st = billing.active(r.config) if primary.startswith("sinobee/") else None
        if primary.startswith("sinobee/"):
            mode = (st or {}).get("source") or billing.SUBSCRIPTION
        else:
            mode = "local" if primary.split("/", 1)[0] in local_prov else billing.OWN
        since = r.period_start("today")
        rows = r.memory.usage_grouped("model", since, limit=50)
        own = [x for x in rows if not str(x["key"]).startswith("sinobee/")]
        is_local = lambda k: str(k).split("/", 1)[0] in local_prov  # noqa: E731
        pool = r.provider.pool() if hasattr(r.provider, "pool") else []
        return {
            "version": __version__, "build": _build(),
            "preference": r.config.get("model.preference") or "balanced", "pin": r.config.get("model.pin") or None,
            "mode": mode, "fallback_local": bool(pool) and all(p.provider_name in local_prov for p in pool),
            "billing": {"source": (st or {}).get("source"), "until": (st or {}).get("until"),
                        "points": billing.points(r.config), "auto_points": bool(r.config.get("billing.auto_points")),
                        "on_exhausted": r.config.get("billing.on_exhausted") or "ask"},
            "today_own": {"cost_usd": round(sum(x["cost_usd"] or 0 for x in own if not is_local(x["key"])), 6),
                          "tokens": sum(x["input_tokens"] + x["output_tokens"] for x in own if not is_local(x["key"])),
                          "local_tokens": sum(x["input_tokens"] + x["output_tokens"] for x in own if is_local(x["key"])),
                          "models": [{"model": x["key"], "cost_usd": x["cost_usd"], "local": is_local(x["key"]),
                                      "tokens": x["input_tokens"] + x["output_tokens"]} for x in own[:6]]},
        }

    @app.get("/api/status")
    async def status():
        r = rt()
        return {
            "model": r.provider.label,
            "fallbacks": [p.label for p in r.provider.providers[1:]] if hasattr(r.provider, "providers") else [],
            "toolsets": r.config.get("permissions.toolsets"),
            "approval": r.config.get("permissions.approval"),
            "desktop": desktop_tools.available(),
            "vnc_url": os.environ.get("SINOBEE_VNC_URL", ""),
            "skills": len(r.skills.skills),
            "memories": len(r.memory.list(limit=100000)),
            "needs_setup": settings_api.settings_payload(r)["needs_setup"],
            "running": [{"session_id": sid, "started": v["started"], "step": v["step"], "activity": v["activity"]}
                        for sid, v in state.get("running", {}).items()],
            "provider_error": r.provider_error,
            "today": r.memory.usage_totals(r.period_start("today")),
            "currency": r.config.get("usage.currency", "USD"),
            "currency_rate": r.config.get("usage.currency_rate"),
            **_mode_and_usage(r),
        }

    @app.get("/api/sessions")
    async def sessions():
        return rt().memory.sessions(100)

    @app.get("/api/sessions/{sid}")
    async def session(sid: str):
        return [m.to_dict() for m in rt().memory.load_messages(sid)]

    @app.get("/api/sessions/{sid}/milestones")
    async def session_milestones(sid: str):
        """The conversation's milestone plan (agent/milestones.py), if its task runs as milestones."""
        from ..agent import milestones as M
        from ..work.hub import get_hub
        store = get_hub(rt()).store
        plan = M.load(store, f"session:{sid}")
        if not plan:
            return {"plan": None}
        agent = state["agents"].get(sid)
        running = bool(agent is not None and getattr(agent, "_msr", None) and sid in state.get("running", {}))
        return {"plan": plan.card(M.facts(store, f"session:{sid}"), "running" if running else "saved"),
                "running": running}

    @app.post("/api/sessions/{sid}/milestones/feedback")
    async def session_milestone_feedback(sid: str, body: dict):
        """A correction to the plan while the task runs: mode "boundary" (next milestone) or "now" (interrupt and
        re-plan; finished milestones are kept, only affected sub-agents stop, the task is not user-stopped)."""
        text = str(body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "text is required")
        agent = state["agents"].get(sid)
        if agent is None or sid not in state.get("running", {}) or \
                not agent.milestone_feedback(text, "now" if body.get("mode") == "now" else "boundary"):
            raise HTTPException(409, "this conversation has no running milestone task")
        broadcast("steer", {"session_id": sid, "text": text, "milestone_mode": body.get("mode") or "boundary"})
        return {"ok": True}

    @app.get("/api/sessions/{sid}/interrupted")
    async def session_interrupted(sid: str):
        """The conversation's newest turn, when it was interrupted (restart / crash) and has not finished since."""
        from ..work.hub import get_hub
        runs = [r for r in get_hub(rt()).store.runs(limit=300) if r["kind"] == "chat" and r.get("session_id") == sid]
        r = runs[0] if runs else None
        st = (r or {}).get("state") or {}
        # the newest turn ended without a turn record (interrupted, dismissed, crashed): its state comes from the run
        rec = get_hub(rt()).recovery
        model = None
        if r and r["status"] == "failed" and st.get("model_error"):     # U5: resumed after a temporary model error
            ar = st.get("auto_resume") or {}
            model = {"resumed": bool(ar.get("resumed")), "reason": ar.get("reason")} if ar else rec.model_plan(r)
        if not r or st.get("turn_id") or r["status"] not in ("interrupted", "cancelled", "failed"):
            return {"interrupted": False, **({"model_resume": model, "run_id": r["id"]} if model else {})}
        ok, why = rec.eligible(r) if r["status"] == "interrupted" else (False, "")
        return {"interrupted": True, "status": r["status"], "run_id": r["id"], "error": r.get("error") or "",
                "auto_resume": st.get("auto_resume"), "user_stopped": bool(st.get("user_stopped")),
                "at": r.get("created_at"), "will_resume": ok, "why_not": why, "model_resume": model,
                "steps": len(st.get("steps") or []), "artifacts": (st.get("artifacts") or [])[-12:]}

    @app.get("/api/sessions/{sid}/artifacts")
    async def session_artifacts(sid: str):
        """Files produced during a conversation, per tool call, for re-drawing the links when it is reopened."""
        from ..agent import artifacts
        cfg = rt().config
        calls, seen, out = {}, set(), []
        for m in rt().memory.load_messages(sid):
            if m.role == "user" and not m.text().startswith("[使用者在執行中補充指示]"):
                seen = set()        # per turn, as when it ran: a file changed again is shown again
            for c in m.tool_calls:
                calls[c.id] = (c.name, c.arguments or {})
            if m.role == "tool" and m.tool_call_id in calls:
                text = m.text()
                if text.lstrip().startswith("ERROR"):
                    continue
                name, args = calls[m.tool_call_id]
                files = artifacts.collect(cfg, name, args, text, seen)
                if files:
                    out.append({"id": m.tool_call_id, "tool": name, "files": files})
        return out

    @app.get("/api/memories")
    async def memories(tier: str | None = None, q: str | None = None):
        r = rt()
        if q:
            hits = await r.memory.asearch(q, k=30, tiers=("core", "short", "long", "archive"), touch=False)
            return [m.brief() for m in hits]
        if tier == "permanent":
            return [m.brief() for m in r.memory.list(None, 2000) if m.pinned][:500]
        return [m.brief() for m in r.memory.list(tier, 500)]

    @app.get("/api/memories/stats")
    async def memory_stats():
        m = rt().memory
        rep = m.meta_get("maintain.last_report") or {}
        return {**m.stats(), "last_maintain": {"at": rep.get("at"), "reason": rep.get("reason"),
                                               "compression": rep.get("compression")}}

    @app.post("/api/memories/maintain")
    async def memory_maintain():
        from ..memory.maintain import maintain
        return await maintain(rt(), reason="manual", force=True)

    @app.post("/api/memories/{mid}/restore")
    async def memory_restore(mid: str):
        try:
            return rt().memory.restore(mid)
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/memories")
    async def add_memory(body: dict):
        try:
            return await rt().memory.asave(body["content"], kind=body.get("kind", "fact"),
                                           tier=body.get("tier", "long"), importance=float(body.get("importance", 0.7)),
                                           tags=body.get("tags"), pinned=bool(body.get("pinned")),
                                           ttl_days=body.get("ttl_days"))
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.patch("/api/memories/{mid}")
    async def edit_memory(mid: str, body: dict):
        """The user's own edits: pin / unpin (permanent), change tier, importance or content."""
        try:
            return await rt().memory.aupdate(mid, body.get("content"), importance=body.get("importance"),
                                             tier=body.get("tier"), pinned=body.get("pinned"),
                                             ttl_days=body.get("ttl_days"))
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/memories/compress")
    async def compress_memories(body: dict):
        from ..memory.compress import compress
        r = rt()
        rep = await compress(r.memory, r.aux, query=body.get("query"), tier=body.get("tier"), tag=body.get("tag"),
                             ids=body.get("ids"), target_chars=body.get("target_chars"),
                             include_pinned=bool(body.get("include_pinned")), dry_run=body.get("dry_run", True))
        return rep

    @app.delete("/api/memories/{mid}")
    async def forget(mid: str, hard: bool = False, force: bool = False):
        try:
            # the user clicked it in the UI, so that is the explicit confirmation a permanent memory needs
            return rt().memory.forget(mid, hard, force=force)
        except Exception as e:
            raise HTTPException(404, str(e))

    @app.get("/api/pending")
    async def pending():
        return rt().memory.pending()

    @app.post("/api/pending/{pid}/{decision}")
    async def decide(pid: str, decision: str):
        r = rt()
        item = r.memory.pop_pending(pid)
        if not item:
            raise HTTPException(404, "not found")
        if decision != "approve":
            return {"status": "rejected"}
        p = item["payload"]
        if item["kind"] == "memory_save":
            return r.memory.save(p["content"], kind=p.get("kind", "fact"), tier=p.get("tier", "long"),
                                 tags=p.get("tags"), importance=float(p.get("importance", 0.5)),
                                 ttl_days=p.get("ttl_days"))
        res = r.skills.apply_pending(item)
        if item["kind"] == "skill_create" and res.get("name") in r.skills.skills:
            r.skills.approve(res["name"])      # the user adopted the suggestion -> trusted as local
        return res

    # ---------------------------------------------------------------- live task hub (shared by all browser tabs)
    state.setdefault("clients", set())
    state.setdefault("running", {})      # sid -> {started, step, activity, task}
    state.setdefault("approvals", {})    # rid -> (future, payload)
    state.setdefault("questions", {})    # rid -> (future, payload): ask_user waiting for the user's choice

    def broadcast(kind: str, data: dict) -> None:
        item = {"type": kind, **data}
        for q in list(state["clients"]):
            q.put_nowait(item)

    def track(sid: str, kind: str, data: dict) -> None:
        run = state["running"].get(sid)
        if not run:
            return
        if kind == "thinking":
            run.update(step=data.get("step", run["step"]), activity="思考中", tool=None)
        elif kind == "tool_call":
            run.update(activity="執行工具", tool=data.get("name"))
        elif kind == "tool_result":
            run.update(activity="思考中", tool=None)
        elif kind == "approval_request":
            run.update(activity="等待你核准", tool=data.get("tool"))
        elif kind == "ask":
            run.update(activity="等待你回覆", tool=None)

    def run_snapshot() -> list[dict]:
        return [{"session_id": sid, "started": r["started"], "step": r["step"], "activity": r["activity"],
                 "tool": r.get("tool")} for sid, r in state["running"].items()]

    def get_agent(sid: str | None) -> Agent:
        agents = state["agents"]
        agent = agents.get(sid) if sid else None
        if agent is None:
            agent = Agent(rt(), sid, platform="web")
            agents[agent.session_id] = agent
        sid_ = agent.session_id

        def emit(k, d):
            track(sid_, k, d)
            broadcast(k, {**d, "session_id": sid_})
        agent.emit_fn = emit

        async def approve(name: str, args: dict, reason: str) -> bool:
            rid = secrets.token_hex(4)
            fut = asyncio.get_running_loop().create_future()
            payload = {"request_id": rid, "tool": name, "arguments": args, "reason": reason, "session_id": sid_}
            state["approvals"][rid] = (fut, payload)
            emit("approval_request", payload)
            try:
                return await asyncio.wait_for(fut, timeout=900)
            except asyncio.TimeoutError:
                return False
            finally:
                state["approvals"].pop(rid, None)
                if sid_ in state["running"]:
                    state["running"][sid_].update(activity="思考中", tool=None)
        agent.ctx.approve = approve

        async def ask(question: str, options: list, multi: bool, allow_text: bool) -> dict | None:
            rid = secrets.token_hex(4)
            fut = asyncio.get_running_loop().create_future()
            payload = {"request_id": rid, "question": question, "options": options, "multi": multi,
                       "allow_text": allow_text, "session_id": sid_}
            state["questions"][rid] = (fut, payload)
            emit("ask", payload)
            try:
                return await asyncio.wait_for(fut, timeout=1800)
            except asyncio.TimeoutError:
                broadcast("ask_resolved", {"request_id": rid, "session_id": sid_, "timeout": True})
                return None
            finally:
                state["questions"].pop(rid, None)
                if sid_ in state["running"]:
                    state["running"][sid_].update(activity="思考中", tool=None)
        agent.ctx.ask = ask
        return agent

    def answer_question(rid: str | None, choices, text: str) -> bool:
        entry = state["questions"].get(rid or "")
        if not entry or entry[0].done():
            return False
        labels = {o["label"] for o in entry[1]["options"]}
        picked = [c for c in (choices or []) if c in labels][: (len(labels) if entry[1]["multi"] else 1)]
        ans = {"choices": picked, "text": str(text or "").strip()[:2000]}
        entry[0].set_result(ans)
        broadcast("ask_resolved", {"request_id": rid, "session_id": entry[1]["session_id"], **ans})
        return True

    async def start_turn(sid_hint: str | None, text: str, client_ref: str | None = None,
                         mode: str = "steer") -> dict:
        agent = get_agent(sid_hint)
        sid = agent.session_id
        if sid in state["running"]:
            if mode == "interrupt":            # stop what is running, then do this instead
                task = state["running"][sid].get("task")
                await cancel_turn(sid)
                if task:
                    try:
                        await asyncio.wait_for(asyncio.shield(task), 15)
                    except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                agent._inbox.clear()
            else:                              # keep going, but take the new information into account
                waiting = next((rid for rid, (f, p) in state["questions"].items() if p["session_id"] == sid and not f.done()), None)
                if waiting and answer_question(waiting, [], text):   # a question is open: what they typed answers it
                    return {"session_id": sid, "answered": True}
                agent.steer(text)
                broadcast("steer", {"session_id": sid, "text": text, "client_ref": client_ref})
                return {"session_id": sid, "steered": True}
        broadcast("session", {"session_id": sid, "client_ref": client_ref})
        state["running"][sid] = {"started": time.time(), "step": 0, "activity": "準備中", "tool": None}

        async def go():
            try:
                await agent.run(text)
            except asyncio.CancelledError:
                agent._persist()
                rec = getattr(agent, "_run_rec", None)
                if rec:                        # force-stopped mid-step: record it as stopped by the user
                    try:
                        rec.finish("stopped", "（已強制停止）", None, 0)
                    except Exception:  # noqa: BLE001
                        log.exception("could not record the stopped run")
                broadcast("turn_end", {"session_id": sid, "status": "stopped", "text": "（已強制停止）",
                                       "error": None, "usage": {}})
            except Exception as e:
                log.exception("run failed")
                from ..agent.errors import explain_error
                info = explain_error(e)
                broadcast("turn_end", {"session_id": sid, "status": "error", "text": "", "usage": {}, "error": info})
                rec = getattr(agent, "_run_rec", None)
                if rec and rec.rid:            # a crash is a failure, not an interruption to resume
                    try:
                        rec.finish("error", "", info, 0)
                    except Exception:  # noqa: BLE001
                        log.exception("could not record the failed run")
            finally:
                state["running"].pop(sid, None)
                broadcast("run_state", {"running": run_snapshot()})
                if agent._inbox:               # typed while the last step was finishing: run it as the next turn
                    pending = "\n".join(agent._inbox)
                    agent._inbox.clear()
                    asyncio.create_task(start_turn(sid, pending))
        state["running"][sid]["task"] = asyncio.create_task(go())
        broadcast("run_state", {"running": run_snapshot()})
        return {"session_id": sid}

    async def cancel_turn(sid: str) -> None:
        agent = state["agents"].get(sid)
        run = state["running"].get(sid)
        if agent:
            agent.cancel()
        for rid, (fut, payload) in list(state["approvals"].items()):
            if payload["session_id"] == sid and not fut.done():
                fut.set_result(False)
        if run and run.get("task"):
            await asyncio.sleep(3)             # give the loop a chance to stop cleanly between steps
            if not run["task"].done():
                run["task"].cancel()           # stuck in a long model/tool call -> force stop

    from ..work.hub import get_hub as _get_hub
    _hub = _get_hub(rt())
    _hub.chat_resumer = lambda sid, text: asyncio.create_task(start_turn(sid, text))
    _hub.chat_canceller = lambda sid: asyncio.create_task(cancel_turn(sid))
    _hub.chat_alive = lambda sid: sid in state["running"]

    skills_api.register_feedback(app, rt, lambda k, d: broadcast(k, d))
    work_api.register(app, rt, lambda: run_snapshot())
    quality_api.register(app, rt, lambda k, d: broadcast(k, d))
    evals_api.register(app, rt, lambda k, d: broadcast(k, d))
    state["local"] = local_api.register(app, rt, lambda k, d: broadcast(k, d))

    @app.get("/api/running")
    async def running():
        return run_snapshot()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        token = websocket.query_params.get("token")
        expected = rt().config.server_token()
        if expected and not secrets.compare_digest(str(token or ""), str(expected)):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        outbox: asyncio.Queue = asyncio.Queue()
        state["clients"].add(outbox)

        async def sender():
            while True:
                item = await outbox.get()
                try:
                    await websocket.send_json(item)
                except Exception:
                    pass

        sender_task = asyncio.create_task(sender())
        # a reloaded page immediately learns what is still running and what is waiting for approval
        outbox.put_nowait({"type": "hello", "running": run_snapshot(),
                           "approvals": [p for _, p in state["approvals"].values()],
                           "questions": [p for _, p in state["questions"].values()]})
        try:
            while True:
                msg = await websocket.receive_json()
                kind = msg.get("type")
                if kind == "message":
                    res = await start_turn(msg.get("session_id"), msg.get("text", ""), msg.get("client_ref"),
                                           msg.get("mode") or "steer")
                    if res.get("error"):
                        outbox.put_nowait({"type": "notice", **res})
                elif kind == "approve":
                    entry = state["approvals"].get(msg.get("request_id"))
                    if entry and not entry[0].done():
                        entry[0].set_result(bool(msg.get("ok")))
                        broadcast("approval_resolved", {"request_id": msg.get("request_id"), "ok": bool(msg.get("ok")),
                                                        "session_id": entry[1]["session_id"]})
                elif kind == "answer":
                    answer_question(msg.get("request_id"), msg.get("choices"), msg.get("text") or "")
                elif kind == "cancel":
                    asyncio.create_task(cancel_turn(msg.get("session_id")))
                elif kind == "ping":
                    outbox.put_nowait({"type": "pong"})
        except WebSocketDisconnect:
            pass
        finally:
            state["clients"].discard(outbox)
            sender_task.cancel()

    return app
