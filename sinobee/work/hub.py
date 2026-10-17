"""WorkHub: runs jobs and workflows in the background, schedules them, keeps the blackboard and the approval
queue, and answers "what has SinoBee done, what is it doing, what will it do".

One hub per Runtime (`rt.work`). The scheduler only runs inside the server (`hub.start()`); the CLI and tools can
still create jobs and read state through the same store.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from . import schedule as S
from .store import ACTIVE, WorkStore

log = logging.getLogger("sinobee.work")
TICK = 10


class WorkError(Exception):
    pass


TASK_PREAMBLE = """[SinoBee 背景工作] 這是背景執行的工作「{name}」（{sched}；觸發：{trigger}）。
沒有人即時看著這段對話：不要提問，直接完成任務；需要核准的操作會暫停等使用者在「工作」頁核准。
這個工作有自己的長期黑板（下方 <context> 會顯示），請用 board_write(kind='note', key=...) 記下下次執行需要延續的狀態
（例如上次看到的最新編號、已處理的項目），用 kind='plan' 記下之後打算做的事。
{previous}
任務：
{task}"""


class WorkHub:
    def __init__(self, rt):
        self.rt = rt
        self.chat_resumer = None        # set by the web server: (session_id, text) -> starts a turn
        self.chat_canceller = None      # set by the web server: (session_id) -> stops that turn
        self.store = WorkStore(Path(rt.config.home) / "work.db")
        self.listeners: list = []
        self.tasks: dict[str, asyncio.Task] = {}
        self.cancel_flags: set[str] = set()
        self.futures: dict[str, asyncio.Future] = {}
        self.children: dict[str, tuple] = {}        # live agents: session id -> (Agent, owner run id / session)
        self._loop_task: asyncio.Task | None = None
        self._wf_cache: tuple[float, dict] | None = None

    # ------------------------------------------------------------------ events
    def notify(self, kind: str, data: dict | None = None) -> None:
        for fn in list(self.listeners):
            try:
                fn("work", {"what": kind, **(data or {})})
            except Exception:  # noqa: BLE001
                log.exception("work listener failed")

    def cancelled(self, run_id: str) -> bool:
        return run_id in self.cancel_flags

    # ------------------------------------------------------------------ workflows
    def workflows(self) -> dict[str, dict]:
        from .graph import catalog
        if self._wf_cache and time.time() - self._wf_cache[0] < 5:
            return self._wf_cache[1]
        wfs = catalog(Path(self.rt.config.home))
        # fixed workflows shipped inside skills (metadata sinobee-workflow), only from skills allowed to run scripts
        from .graph import WorkflowError, load_file
        for s in list(getattr(self.rt.skills, "skills", {}).values()):
            wf_decl = getattr(s, "workflow", None)
            if not wf_decl or s.disabled or s.policy.scripts == "deny":
                continue
            try:
                wf = load_file(wf_decl[1])
            except WorkflowError as e:
                log.warning("workflow of skill %s skipped: %s", s.name, e)
                continue
            wf.update(skill=s.name, bundled=s.trust == "builtin")
            wfs.setdefault(wf["name"], wf)
        self._wf_cache = (time.time(), wfs)
        return wfs

    def workflow(self, name: str) -> dict:
        wf = self.workflows().get(name)
        if not wf:
            raise WorkError(f"找不到流程「{name}」")
        return wf

    def check_inputs(self, wf: dict, inputs: dict) -> dict:
        out = {}
        for k, spec in (wf.get("inputs") or {}).items():
            v = (inputs or {}).get(k, spec.get("default"))
            if spec.get("required") and v in (None, ""):
                raise WorkError(f"流程「{wf['title']}」需要「{spec.get('label') or k}」")
            if spec.get("type") == "connection" and v:
                from ..tools.db_tools import service
                from ..db.drivers import DbError
                try:
                    v = service(self.rt.config).get(str(v)).id
                except DbError as e:
                    raise WorkError(str(e)) from None
            if spec.get("type") == "bool":
                v = bool(v)
            if spec.get("type") == "file" and v:
                f = Path(str(v)).expanduser()
                f = f if f.is_absolute() else Path(self.rt.config.workspace) / f
                if not f.exists():
                    raise WorkError(f"找不到檔案「{v}」（{spec.get('label') or k}）")
                v = str(f)
            out[k] = v
        return out

    # ------------------------------------------------------------------ jobs
    def create_job(self, spec: dict, created_by: str = "user") -> dict:
        name = str(spec.get("name") or "").strip()[:120]
        kind = spec.get("kind") or ("workflow" if spec.get("workflow") else "task")
        if kind not in ("task", "workflow"):
            raise WorkError("kind 需為 task 或 workflow")
        prompt = str(spec.get("prompt") or "").strip()
        inputs = spec.get("inputs") or {}
        if kind == "task" and not prompt:
            raise WorkError("請描述要做的事")
        if kind == "workflow":
            wf = self.workflow(str(spec.get("workflow") or ""))
            inputs = self.check_inputs(wf, inputs)
            name = name or wf["title"]
        sched = S.validate(spec.get("schedule"))
        nxt = S.next_run(sched, time.time())
        job = self.store.create_job(name=name or prompt[:40], kind=kind, prompt=prompt,
                                    workflow=spec.get("workflow") or "", inputs=inputs, schedule=sched,
                                    enabled=spec.get("enabled", True) is not False, created_by=created_by,
                                    next_run_at=nxt, notes=str(spec.get("notes") or "")[:500])
        self.notify("jobs")
        return self.public_job(job)

    def update_job(self, jid: str, spec: dict) -> dict:
        job = self.store.job(jid)
        if not job:
            raise WorkError("找不到工作")
        fields = {}
        if "name" in spec:
            fields["name"] = str(spec["name"]).strip()[:120] or job["name"]
        if "prompt" in spec and job["kind"] == "task":
            fields["prompt"] = str(spec["prompt"]).strip()
        if "inputs" in spec and job["kind"] == "workflow":
            fields["inputs"] = self.check_inputs(self.workflow(job["workflow"]), spec["inputs"])
        if "schedule" in spec:
            fields["schedule"] = S.validate(spec["schedule"])
        if "enabled" in spec:
            fields["enabled"] = bool(spec["enabled"])
        sched = fields.get("schedule", job["schedule"])
        enabled = fields.get("enabled", job["enabled"])
        if "schedule" in fields or "enabled" in fields:
            fields["next_run_at"] = S.next_run(sched, time.time(), job.get("last_run_at") if sched.get("type") != "once"
                                               else None) if enabled else None
        job = self.store.update_job(jid, **fields)
        self.notify("jobs")
        return self.public_job(job)

    def delete_job(self, jid: str) -> None:
        active = self.store.active_run(jid)
        if active:
            self.cancel(active["id"])
        self.store.delete_job(jid)
        self.notify("jobs")

    def public_job(self, job: dict) -> dict:
        d = dict(job)
        d["schedule_text"] = S.describe(job["schedule"])
        d["upcoming"] = S.upcoming(job["schedule"], time.time(), 3, job.get("last_run_at")) if job["enabled"] else []
        if job.get("next_run_at") and job["enabled"]:
            seen, merged = set(), []
            for t in sorted([job["next_run_at"], *d["upcoming"]]):
                if int(t // 60) not in seen:   # the stored next run and the computed one can differ by seconds
                    seen.add(int(t // 60))
                    merged.append(t)
            d["upcoming"] = merged[:3]
        if job["kind"] == "workflow":
            wf = self.workflows().get(job["workflow"])
            d["workflow_title"] = wf["title"] if wf else job["workflow"]
        return d

    # ------------------------------------------------------------------ runs
    def run_job(self, jid: str, trigger: str = "manual") -> dict:
        job = self.store.job(jid)
        if not job:
            raise WorkError("找不到工作")
        active = self.store.active_run(jid)
        if active:
            raise WorkError("這個工作正在執行中")
        run = self.store.create_run(job_id=jid, name=job["name"], kind=job["kind"], trigger=trigger)
        self.store.update_job(jid, last_run_at=time.time())
        self._launch(run)
        return run

    def start_workflow(self, name: str, inputs: dict, *, trigger: str = "chat", parent_session: str | None = None) -> dict:
        wf = self.workflow(name)
        inputs = self.check_inputs(wf, inputs)
        run = self.store.create_run(job_id=None, name=wf["title"], kind="workflow", trigger=trigger,
                                    state={"workflow": name, "inputs": inputs}, parent_session=parent_session)
        self._launch(run)
        return run

    def _launch(self, run: dict) -> None:
        if self.running_count() >= int(self.rt.config.get("work.max_parallel_runs", 2)):
            self.notify("run", {"run_id": run["id"]})     # stays queued; the scheduler starts it later
            return
        self.store.update_run(run["id"], status="running", started_at=time.time(), activity="準備中")
        task = asyncio.get_event_loop().create_task(self._execute(run["id"]))
        self.tasks[run["id"]] = task
        task.add_done_callback(lambda _t, rid=run["id"]: self.tasks.pop(rid, None))
        self.notify("run", {"run_id": run["id"]})

    def running_count(self) -> int:
        return sum(1 for t in self.tasks.values() if not t.done())

    async def _execute(self, rid: str) -> None:
        run = self.store.run(rid)
        job = self.store.job(run["job_id"]) if run.get("job_id") else None
        status, summary, error, cost = "done", "", "", 0.0
        try:
            if run["kind"] == "workflow":
                from .graph import WorkflowRun
                name = (job or {}).get("workflow") or run["state"].get("workflow")
                inputs = (job or {}).get("inputs") if job else run["state"].get("inputs") or {}
                wr = WorkflowRun(self, run, self.workflow(name), inputs or {}, job)
                summary = await wr.execute()
                cost = sum(n.get("cost_usd") or 0 for n in wr.state["nodes"].values())
            else:
                summary, cost, status = await self._task(run, job)
        except asyncio.CancelledError:
            status, error = "cancelled", "已停止"
        except Exception as e:  # noqa: BLE001
            log.exception("run %s failed", rid)
            status, error = "failed", str(e)[:1000]
        finally:
            self.cancel_flags.discard(rid)
            for aid, fut in list(self.futures.items()):
                a = self.store.approval(aid)
                if a and a["run_id"] == rid and not fut.done():
                    fut.set_result(False)
        self.store.update_run(rid, status=status, finished_at=time.time(), summary=(summary or "")[:4000],
                              error=error, cost_usd=cost, activity="")
        if job:
            self.store.update_job(job["id"], last_status=status)
            if job["schedule"].get("type") == "once":
                self.store.update_job(job["id"], enabled=False, next_run_at=None)
        self.notify("run", {"run_id": rid, "finished": True, "status": status})
        self._start_queued()

    async def _task(self, run: dict, job: dict | None) -> tuple[str, float, str]:
        from ..agent import Agent
        sid = f"job-{run['id']}"
        self.store.update_run(run["id"], session_id=sid)
        prev = ""
        if job:
            last = [r for r in self.store.runs(job_id=job["id"], limit=5) if r["id"] != run["id"] and r["summary"]]
            if last:
                prev = f"上次執行（{time.strftime('%Y-%m-%d %H:%M', time.localtime(last[0]['created_at']))}，" \
                       f"{last[0]['status']}）的結果摘要：\n{last[0]['summary'][:1500]}\n"
        prompt = TASK_PREAMBLE.format(name=run["name"], sched=S.describe((job or {}).get("schedule") or {}),
                                      trigger=run["trigger"], previous=prev, task=(job or {}).get("prompt") or "")
        agent = Agent(self.rt, sid, platform="job", approve=self.approver(run), emit=self.run_emitter(run["id"]),
                      board_scope=f"job:{job['id']}" if job else f"run:{run['id']}", title=f"[工作] {run['name']}")
        agent.ctx.state["run_id"] = run["id"]
        self.children[sid] = (agent, run["id"])
        try:
            text = await agent.run(prompt)
        finally:
            self.children.pop(sid, None)
        st = (getattr(agent, "last_status", None) or {}).get("status", "done")
        status = "done" if st == "done" else "failed" if st in ("error", "incomplete", "blocked") else st
        cost = (getattr(agent, "last_turn_usage", None) or {}).get("cost_usd", 0) or 0
        return text, cost, status

    def cancel(self, rid: str) -> None:
        self.cancel_flags.add(rid)
        run = self.store.run(rid)
        for sid, (child, owner) in list(self.children.items()):
            if owner == rid:
                child.cancel()
        for aid, fut in list(self.futures.items()):
            a = self.store.approval(aid)
            if a and a["run_id"] == rid and not fut.done():
                fut.set_result(False)
        if run and run["kind"] == "chat" and run["status"] in ACTIVE and self.chat_canceller and run.get("session_id"):
            self.chat_canceller(run["session_id"])        # the turn ends itself and records "cancelled"
            self.notify("run", {"run_id": rid})
            return
        t = self.tasks.get(rid)
        if t and not t.done():
            asyncio.get_event_loop().call_later(5, lambda: (not t.done()) and t.cancel())
        elif run and run["status"] in ACTIVE:
            self.store.update_run(rid, status="cancelled", finished_at=time.time(), activity="")
        self.notify("run", {"run_id": rid})

    def _start_queued(self) -> None:
        for r in reversed(self.store.runs(status=("queued",), limit=20)):
            if self.running_count() >= int(self.rt.config.get("work.max_parallel_runs", 2)):
                return
            self._launch(r)

    # ------------------------------------------------------------------ approvals (unattended runs pause here)
    def approver(self, run: dict):
        async def approve(tool: str, args: dict, reason: str) -> bool:
            a = self.store.add_approval(run_id=run["id"], session_id=run.get("session_id") or "", tool=tool,
                                        arguments=args, reason=reason)
            fut = asyncio.get_event_loop().create_future()
            self.futures[a["id"]] = fut
            self.store.update_run(run["id"], status="waiting", activity=f"等待你核准：{reason}"[:200])
            self.notify("approval", {"approval_id": a["id"], "run_id": run["id"], "tool": tool, "reason": reason,
                                     "name": run.get("name")})
            try:
                ok = await fut
            finally:
                self.futures.pop(a["id"], None)
            if self.store.run(run["id"])["status"] == "waiting":
                self.store.update_run(run["id"], status="running", activity="繼續執行")
            self.notify("run", {"run_id": run["id"]})
            return ok
        return approve

    def decide(self, aid: str, ok: bool) -> dict:
        a = self.store.approval(aid)
        if not a or a["status"] != "pending":
            raise WorkError("這個核准請求已不存在或已處理")
        self.store.decide_approval(aid, ok)
        fut = self.futures.get(aid)
        if fut and not fut.done():
            fut.set_result(ok)
        elif a.get("run_id"):         # the run was interrupted while waiting: resume it so it asks again
            run = self.store.run(a["run_id"])
            if run and run["status"] == "interrupted":
                self.resume(run["id"])
        self.notify("approval", {"approval_id": aid, "decided": ok})
        return self.store.approval(aid)

    def resume(self, rid: str) -> dict:
        run = self.store.run(rid)
        if not run:
            raise WorkError("找不到這次執行")
        if run["status"] in ACTIVE:
            raise WorkError("這次執行仍在進行中")
        if run["kind"] == "chat":      # a conversation continues in its session from where it stopped
            if not self.chat_resumer or not run.get("session_id"):
                raise WorkError("這段對話需要在網頁介面中續跑")
            req = (run.get("state") or {}).get("input") or run.get("name") or ""
            self.chat_resumer(run["session_id"],
                              "[續跑] 上一次的任務在中途被中斷了。已完成的步驟都在這段對話裡：先確認做到哪裡，"
                              f"從中斷處接著完成，不要重做已完成的部分。原本的要求：{req}")
            self.store.update_run(rid, summary=(run.get("summary") or "") + "（已續跑）")
            return self.store.run(rid)
        if run["kind"] == "task":      # a task starts over in a new run (same job, same blackboard)
            if run.get("job_id"):
                return self.run_job(run["job_id"], trigger="retry")
            raise WorkError("這次執行無法重新開始")
        self.store.update_run(rid, status="queued", error="", finished_at=None)
        self._launch(self.store.run(rid))
        return self.store.run(rid)

    def run_emitter(self, rid: str):
        last = {"t": 0.0}

        def emit(kind: str, data: dict):
            text = None
            if kind == "tool_call":
                text = f"執行 {data.get('name')}"
            elif kind == "thinking":
                text = "思考中"
            elif kind == "subagent":
                ev = data.get("event")
                who = data.get("role_label") or data.get("role")
                text = {"start": f"{who}開始：{(data.get('task') or '')[:60]}", "tool_call": f"{who}執行 {data.get('tool')}",
                        "turn_end": f"{who}完成"}.get(ev)
            if text and time.time() - last["t"] > 0.7:
                last["t"] = time.time()
                self.store.update_run(rid, activity=text[:200])
                self.notify("activity", {"run_id": rid, "activity": text[:200]})
        return emit

    # ------------------------------------------------------------------ scheduler
    def start(self) -> None:
        if self._loop_task:
            return
        for r in self.store.interrupt_active():
            if r["kind"] == "workflow":
                log.info("resuming interrupted workflow run %s", r["id"])
                try:
                    self.resume(r["id"])
                except WorkError:
                    pass
        now = time.time()
        for j in self.store.jobs():         # catch up schedules that passed while SinoBee was off
            if j["enabled"] and j["next_run_at"] is None and j["schedule"].get("type") not in ("manual",):
                self.store.update_job(j["id"], next_run_at=S.next_run(j["schedule"], now, j.get("last_run_at")))
        self.seed_system_jobs()
        self._loop_task = asyncio.get_event_loop().create_task(self._loop())

    def seed_system_jobs(self) -> None:
        """Built-in schedules, created once (a user who deletes or edits one keeps that choice)."""
        cfg, mem = self.rt.config, self.rt.memory
        if not cfg.get("memory.schedule", True) or mem.meta_get("seed.memory-maintenance"):
            return
        try:
            hh, mm = str(cfg.get("memory.schedule_time", "03:17")).split(":")[:2]
            job = self.create_job({"name": "記憶整理", "kind": "workflow", "workflow": "memory-maintenance",
                                   "schedule": {"type": "daily", "time": f"{int(hh):02d}:{int(mm):02d}"},
                                   "notes": "SinoBee 內建：每天整理一次記憶，可修改時間或停用。"}, created_by="system")
            mem.meta_set("seed.memory-maintenance", job["id"])
        except Exception:  # noqa: BLE001 - never block start-up
            log.exception("could not create the memory maintenance job")

    async def stop(self) -> None:
        if self._loop_task:
            self._loop_task.cancel()
        for t in list(self.tasks.values()):
            t.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            await asyncio.sleep(TICK)

    def tick(self, at: float | None = None) -> list[str]:
        at = at or time.time()
        started = []
        for job in self.store.due_jobs(at):
            nxt = S.next_run(job["schedule"], at, at) if job["schedule"].get("type") != "once" else None
            if self.store.active_run(job["id"]):
                self.store.update_job(job["id"], next_run_at=nxt)     # still running: skip this occurrence
                continue
            self.store.update_job(job["id"], next_run_at=nxt)
            try:
                run = self.run_job(job["id"], trigger="schedule")
                started.append(run["id"])
            except WorkError:
                continue
        self._start_queued()
        return started

    # ------------------------------------------------------------------ overview (past / now / next)
    def overview(self, chat_running: list[dict] | None = None, history_limit: int = 30) -> dict:
        now = time.time()
        active = self.store.runs(status=ACTIVE, limit=50)
        live = {c["session_id"]: c for c in chat_running or []}
        chat_sessions = {r["session_id"] for r in active if r["kind"] == "chat"}
        # chat turns that have a run record are shown as runs (with live step info); older ones as before
        chat_running = [c for c in chat_running or [] if c["session_id"] not in chat_sessions]
        jobs = [self.public_job(j) for j in self.store.jobs()]
        upcoming = []
        for j in jobs:
            if j["upcoming"]:   # one line per job: its next run, plus the ones after it
                upcoming.append({"at": j["upcoming"][0], "later": j["upcoming"][1:], "job_id": j["id"],
                                 "name": j["name"], "kind": j["kind"], "schedule_text": j["schedule_text"]})
        upcoming.sort(key=lambda x: x["at"])
        plans = []
        for b in self.store.board_scopes_open_plans()[:30]:
            plans.append({"scope": b["scope"], "content": b["content"][:300], "status": b["status"] or "todo",
                          "updated_at": b["updated_at"], "author": b["author"]})
        history = [{"type": "run", **{k: r[k] for k in ("id", "job_id", "name", "kind", "trigger", "status", "created_at",
                                                     "started_at", "finished_at", "summary", "error", "cost_usd",
                                                     "session_id")}}
                   for r in self.store.runs(limit=history_limit) if r["status"] not in ACTIVE]
        first_chat_run = min((r["created_at"] for r in self.store.runs(limit=500) if r["kind"] == "chat"), default=None)
        for t in self.rt.memory.recent_turns(history_limit):
            if first_chat_run is not None and t["created_at"] >= first_chat_run - 1:
                continue
            history.append({"type": "chat", "id": f"turn-{t['id']}", "session_id": t["session_id"],
                            "name": t["title"] or "對話", "status": t["status"], "created_at": t["created_at"],
                            "finished_at": t["created_at"], "duration_s": t["duration_s"], "cost_usd": t["cost_usd"],
                            "tool_calls": t["tool_calls"], "skills": t["skills"]})
        history.sort(key=lambda x: x.get("finished_at") or x["created_at"], reverse=True)
        return {
            "now": now,
            "running": [{**self.public_run(r, brief=True),
                         **({"step": live[r["session_id"]].get("step"), "activity": live[r["session_id"]].get("activity")
                             or r["activity"]} if r["kind"] == "chat" and r.get("session_id") in live else {})}
                        for r in active],
            "chat_running": chat_running or [],
            "approvals": self.store.approvals("pending"),
            "upcoming": upcoming[:30],
            "plans": plans,
            "jobs": jobs,
            "history": history[:history_limit],
        }

    def public_run(self, run: dict, brief: bool = False) -> dict:
        d = dict(run)
        st = d.get("state") or {}
        if st.get("nodes"):
            nodes = st["nodes"]
            d["progress"] = {"done": sum(1 for n in nodes.values() if n["status"] in ("done", "skipped")),
                             "total": len(nodes)}
        if d.get("kind") == "chat":
            d["progress_steps"] = len(st.get("steps") or [])
        if brief:
            d["state"] = {"levels": st.get("levels"), "nodes": {k: {kk: v.get(kk) for kk in ("status", "title", "type")}
                                                                for k, v in (st.get("nodes") or {}).items()}}
        return d

    def run_detail(self, rid: str) -> dict:
        run = self.store.run(rid)
        if not run:
            raise WorkError("找不到這次執行")
        d = self.public_run(run)
        d["board"] = self.store.board_read(f"run:{rid}", limit=200)
        if run.get("job_id"):
            d["job_board"] = self.store.board_read(f"job:{run['job_id']}", limit=50)
        d["approvals"] = [a for a in self.store.approvals("pending") if a["run_id"] == rid]
        sessions = []
        for n in (run["state"].get("nodes") or {}).values():
            for s in n.get("sessions") or []:
                sessions.append(s)
        d["sessions"] = sessions
        return d

    # ------------------------------------------------------------------ blackboard helpers
    def board_brief(self, scope: str, limit: int = 15) -> str:
        rows = self.store.board_read(scope, limit=limit)
        rows = [r for r in rows if r["kind"] != "node_output" and r["key"] != "checklist"] or \
            [r for r in rows[-5:] if r["key"] != "checklist"]       # the checklist has its own place in the tail
        if not rows:
            return ""
        lines = []
        for r in rows:
            content = r["content"]
            if len(content) > 300:
                content = content[:300] + "…"
            tag = r["kind"] + (f"[{r['key']}]" if r["key"] else "") + (f"({r['status']})" if r["status"] else "")
            lines.append(f"- #{r['id']} {tag} by {r['author'] or '?'}: {content}")
        return "blackboard (shared notes for this work; read more with board_read):\n" + "\n".join(lines)


def get_hub(rt) -> WorkHub:
    hub = getattr(rt, "_work_hub", None)
    if hub is None:
        hub = WorkHub(rt)
        rt._work_hub = hub
    return hub


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)
