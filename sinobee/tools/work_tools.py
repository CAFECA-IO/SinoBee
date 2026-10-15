"""Work tools: the shared blackboard, sub-agents, fixed workflows and scheduled (recurring / persistent) jobs."""
from __future__ import annotations

import asyncio
import json
import time

from .registry import ToolContext, tool

BOARD_KINDS = ["goal", "plan", "finding", "decision", "issue", "question", "artifact", "note"]


def _hub(ctx: ToolContext):
    from ..work.hub import get_hub
    rt = ctx.state.get("runtime")
    if rt is None:
        raise RuntimeError("work tools need the SinoBee runtime")
    return get_hub(rt)


def _scope(ctx: ToolContext) -> str:
    return ctx.state.get("board_scope") or f"session:{ctx.session_id}"


def _author(ctx: ToolContext) -> str:
    agent = ctx.state.get("agent")
    return ("sub:" if ctx.state.get("depth") else "main:") + (getattr(agent, "session_id", "") or ctx.session_id)


# ------------------------------------------------------------------ blackboard
@tool("board_write", "work",
      "Write to the shared blackboard of the current work (seen by you, your sub-agents and later runs of the same "
      "job). kinds: goal, plan (one item per entry; status todo/doing/done), finding (a fact with its source), "
      "decision, issue, question, artifact (a file you produced), note (state to carry to the next run; use a key). "
      "An entry with the same kind+key is replaced.",
      {"properties": {"kind": {"type": "string", "enum": BOARD_KINDS}, "content": {"type": "string"},
                      "key": {"type": "string", "description": "optional stable name, e.g. 'last_order_id'"},
                      "status": {"type": "string", "description": "for plan items: todo | doing | done | dropped"}},
       "required": ["kind", "content"]})
async def board_write(ctx: ToolContext, kind: str, content: str, key: str | None = None, status: str = ""):
    if kind not in BOARD_KINDS:
        return f"unknown kind {kind}; use one of {', '.join(BOARD_KINDS)}"
    if kind == "plan" and not status:
        status = "todo"
    e = _hub(ctx).store.board_write(_scope(ctx), kind, content[:8000], key=key or None, author=_author(ctx),
                                    status=status)
    _hub(ctx).notify("board", {"scope": _scope(ctx)})
    return {"id": e["id"], "kind": kind, "key": key, "status": status}


@tool("board_read", "work",
      "Read the shared blackboard of the current work (newest last). Filter by kind and/or key.",
      {"properties": {"kind": {"type": "string", "enum": BOARD_KINDS + ["node_output", "snapshot"]},
                      "key": {"type": "string"}, "limit": {"type": "integer"}}})
async def board_read(ctx: ToolContext, kind: str | None = None, key: str | None = None, limit: int = 30):
    rows = _hub(ctx).store.board_read(_scope(ctx), kinds=[kind] if kind else None, key=key, limit=max(1, min(limit, 100)))
    return [{"id": r["id"], "kind": r["kind"], "key": r["key"], "status": r["status"], "author": r["author"],
             "content": r["content"][:4000],
             "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["updated_at"]))} for r in rows] or "（黑板是空的）"


# ------------------------------------------------------------------ sub-agents
def _role_help() -> str:
    from ..work.agents import ROLES
    return "; ".join(f"{k} ({v['label']})" for k, v in ROLES.items())


@tool("delegate", "work",
      "Hand independent subtasks to sub-agents that run in parallel, each with its own context and a role: "
      "researcher (web research with sources), analyst (databases/files/shell numbers), reviewer (read-only check "
      "of the work), writer (turns findings into a document), general. Use it when a task splits into parts that do "
      "not depend on each other (e.g. research 4 competitors, analyse 3 tables) or to get an independent review. "
      "Sub-agents share your blackboard: give each a precise, self-contained task. Returns each sub-agent's report.",
      {"properties": {"tasks": {"type": "array", "maxItems": 8, "items": {"type": "object", "properties": {
          "role": {"type": "string", "enum": ["researcher", "analyst", "reviewer", "writer", "general"]},
          "task": {"type": "string", "description": "self-contained instructions incl. the expected output"},
          "context": {"type": "string", "description": "facts the sub-agent needs (it cannot see this chat)"}},
          "required": ["role", "task"]}}}, "required": ["tasks"]})
async def delegate(ctx: ToolContext, tasks: list):
    from ..work.agents import run_many
    if ctx.state.get("depth"):
        return "sub-agents cannot delegate further"
    tasks = [t for t in (tasks or []) if isinstance(t, dict) and str(t.get("task") or "").strip()][:8]
    if not tasks:
        return "no tasks given"
    hub = _hub(ctx)
    scope = _scope(ctx)
    hub.store.board_write(scope, "decision", "委派子代理：" + "；".join(f"{t.get('role')}: {t['task'][:80]}" for t in tasks),
                          author=_author(ctx))
    results = await run_many(ctx.state["runtime"], tasks, board_scope=scope, parent_session=ctx.session_id,
                             approve=ctx.approve, emit=ctx.emit, hub=hub,
                             limit=int(ctx.config.get("work.max_parallel_subagents", 3)),
                             owner=ctx.state.get("run_id") or ctx.session_id)
    return [{"role": r["role"], "task": r["task"][:200], "status": r["status"], "report": r["result"][:6000],
             "session": r["session_id"]} for r in results]


# ------------------------------------------------------------------ workflows
@tool("run_workflow", "work",
      "Run a fixed, multi-step workflow (a graph of sub-agents and built-in actions). Available workflows are "
      "listed by job_list(workflows=true). Example: research-report {topic}, db-health {connection}, "
      "db-migrate {source, target_type, ...}. With wait=true (default) returns the result when it finishes.",
      {"properties": {"name": {"type": "string"}, "inputs": {"type": "object"}, "wait": {"type": "boolean"}},
       "required": ["name"]})
async def run_workflow(ctx: ToolContext, name: str, inputs: dict | None = None, wait: bool = True):
    hub = _hub(ctx)
    run = hub.start_workflow(name, inputs or {}, trigger="chat", parent_session=ctx.session_id)
    if ctx.emit:
        ctx.emit("status", {"message": f"流程「{run['name']}」開始（{run['id']}），可在「工作」頁查看進度"})
    if not wait:
        return {"run_id": run["id"], "status": "started"}
    deadline = time.time() + 3 * 3600
    while time.time() < deadline:
        await asyncio.sleep(2)
        r = hub.store.run(run["id"])
        if r["status"] not in ("queued", "running", "waiting"):
            out = {"run_id": r["id"], "status": r["status"], "summary": r["summary"], "error": r["error"]}
            arts = [b["content"] for b in hub.store.board_read(f"run:{r['id']}", kinds=["artifact"])]
            if arts:
                out["artifacts"] = arts
            return out
    return {"run_id": run["id"], "status": "still running"}


# ------------------------------------------------------------------ jobs
@tool("job_create", "work",
      "Create a background job that runs later, repeatedly or keeps going between runs: a task described in words "
      "(kind=task, prompt) or a workflow (kind=workflow, workflow, inputs). schedule: {type: manual|once|interval|"
      "daily|cron, at (ISO time, once), every (seconds >= 60, interval), time 'HH:MM' + optional days [0=Mon..6] "
      "(daily), expr (cron, local time)}. Runs that need approval pause until the user approves in the 工作 tab. "
      "The user must confirm creating a job.",
      {"properties": {"name": {"type": "string"}, "kind": {"type": "string", "enum": ["task", "workflow"]},
                      "prompt": {"type": "string"}, "workflow": {"type": "string"}, "inputs": {"type": "object"},
                      "schedule": {"type": "object"}}, "required": ["name", "schedule"]},
      dangerous=lambda a: f"建立背景工作「{a.get('name', '')}」（{json.dumps(a.get('schedule') or {}, ensure_ascii=False)}）",
      always_confirm=True)
async def job_create(ctx: ToolContext, name: str, schedule: dict, kind: str = "task", prompt: str = "",
                     workflow: str = "", inputs: dict | None = None):
    job = _hub(ctx).create_job({"name": name, "kind": kind, "prompt": prompt, "workflow": workflow,
                                "inputs": inputs or {}, "schedule": schedule}, created_by="agent")
    return {"id": job["id"], "name": job["name"], "schedule": job["schedule_text"],
            "next_runs": [time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) for t in job["upcoming"]]}


@tool("job_list", "work",
      "What SinoBee is doing in the background: jobs with their schedules and next runs, runs in progress, recent "
      "results and pending approvals. workflows=true also lists the available workflows and their inputs.",
      {"properties": {"workflows": {"type": "boolean"}}})
async def job_list(ctx: ToolContext, workflows: bool = False):
    hub = _hub(ctx)
    ov = hub.overview(history_limit=10)
    fmt = lambda t: time.strftime("%m-%d %H:%M", time.localtime(t)) if t else None   # noqa: E731
    out = {
        "jobs": [{"id": j["id"], "name": j["name"], "kind": j["kind"], "schedule": j["schedule_text"],
                  "enabled": j["enabled"], "next": [fmt(t) for t in j["upcoming"]], "last_status": j["last_status"]}
                 for j in ov["jobs"]],
        "running": [{"run": r["id"], "name": r["name"], "status": r["status"], "activity": r["activity"]}
                    for r in ov["running"]],
        "pending_approvals": [{"run": a["run_id"], "tool": a["tool"], "reason": a["reason"]} for a in ov["approvals"]],
        "recent": [{"name": h["name"], "status": h["status"], "at": fmt(h.get("finished_at") or h["created_at"]),
                    "summary": (h.get("summary") or "")[:200]} for h in ov["history"] if h["type"] == "run"],
    }
    if workflows:
        out["workflows"] = [{"name": w["name"], "title": w["title"], "description": w["description"],
                             "inputs": w["inputs"]} for w in hub.workflows().values()]
    return out


@tool("job_cancel", "work",
      "Pause, delete or stop background work: action=pause|resume|delete for a job id, or action=stop for a run id.",
      {"properties": {"id": {"type": "string"}, "action": {"type": "string", "enum": ["pause", "resume", "delete", "stop"]}},
       "required": ["id", "action"]},
      dangerous=lambda a: f"{ {'pause': '暫停', 'resume': '恢復', 'delete': '刪除', 'stop': '停止'}.get(a.get('action'), a.get('action'))}背景工作 {a.get('id')}"
      if a.get("action") in ("delete", "stop") else None)
async def job_cancel(ctx: ToolContext, id: str, action: str):
    hub = _hub(ctx)
    if action == "stop":
        hub.cancel(id)
        return "stopping"
    if action == "delete":
        hub.delete_job(id)
        return "deleted"
    job = hub.update_job(id, {"enabled": action == "resume"})
    return {"id": job["id"], "enabled": job["enabled"]}
