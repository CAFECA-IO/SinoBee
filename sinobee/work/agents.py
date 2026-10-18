"""Sub-agents: the main agent (or a workflow node) hands an independent subtask to a child agent with its own
context, a role (tools + instructions) and the parent's blackboard. Children cannot delegate or schedule work.

Roles:
  researcher  web / browser / reading files - finds and cites information
  analyst     databases / files / shell - computes numbers, tables and charts
  reviewer    read-only - checks the work of others against the goal and the evidence on the board
  writer      files - turns the findings on the board into a finished document
  general     every tool the parent has (except delegation and scheduling)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid

log = logging.getLogger("sinobee.work")

# never available to a sub-agent: no recursion, no scheduling, no changes to long-term memory or skills
CHILD_DENY = {"delegate", "job_create", "job_cancel", "run_workflow", "memory_save", "memory_update", "memory_forget",
              "memory_compress", "memory_maintain", "memory_restore",
              "skill_create", "skill_patch", "skill_fork", "skill_write_file", "skill_rollback", "skill_feedback", "quality_rule"}
BOARD_TOOLS = {"board_read", "board_write"}

ROLES: dict[str, dict] = {
    "researcher": {
        "label": "研究員",
        "tools": {"web_search", "web_fetch", "browser", "read_file", "list_dir", "search_files", "memory_search",
                  "session_search", "session_read", "skill_search", "skill_view", "working_memory"} | BOARD_TOOLS,
        "prompt": "You are a RESEARCHER sub-agent. Find accurate, current information for your subtask. Prefer primary "
                  "sources, note the URL of every fact, and say when sources disagree or are missing. Write each key "
                  "finding to the blackboard with board_write(kind='finding', content=..., key=<short topic>) "
                  "including its source URL.",
        "iterations": 25,
    },
    "analyst": {
        "label": "資料分析師",
        "tools": {"db_list", "db_schema", "db_query", "read_file", "write_file", "list_dir", "search_files", "shell",
                  "web_fetch", "memory_search", "skill_search", "skill_view", "skill_run", "working_memory",
                  "usage_stats"} | BOARD_TOOLS,
        "prompt": "You are a DATA ANALYST sub-agent. Work from real data: query databases read-only, compute with Python "
                  "in the shell when needed, and never invent numbers. Record each result (numbers, tables, chart "
                  "paths) on the blackboard with board_write(kind='finding', ...).",
        "iterations": 30,
    },
    "reviewer": {
        "label": "審查員",
        "tools": {"read_file", "list_dir", "search_files", "web_search", "web_fetch", "db_list", "db_schema", "db_query",
                  "memory_search", "skill_view", "working_memory"} | BOARD_TOOLS,
        "prompt": "You are a REVIEWER sub-agent. Check the work on the blackboard (and any files it points to) against "
                  "the goal: correctness, missing evidence, contradictions, unsupported claims, numbers that do not add "
                  "up. You do not change files. Write each problem with board_write(kind='issue', content=...) and end "
                  "with a verdict line: 'VERDICT: pass' or 'VERDICT: revise - <reason>'.",
        "iterations": 15,
    },
    "planner": {
        "label": "企劃",
        "tools": {"read_file", "list_dir", "search_files", "skill_view", "web_search", "web_fetch"} | BOARD_TOOLS,
        "prompt": "You are a PLANNER sub-agent. Produce the requested plan in exactly the requested format (usually "
                  "ONE JSON object) as your final answer, with every required field filled with concrete, specific "
                  "content. Do not write files and do not ask questions: make reasonable creative decisions yourself.",
        "iterations": 8,
    },
    "writer": {
        "label": "撰稿員",
        "tools": {"read_file", "write_file", "edit_file", "list_dir", "search_files", "skill_view", "working_memory"}
        | BOARD_TOOLS,
        "prompt": "You are a WRITER sub-agent. Turn the findings and decisions on the blackboard into the requested "
                  "document. Keep every number and claim traceable to a finding (cite sources). Save the document with "
                  "write_file and record it with board_write(kind='artifact', content=<path and one-line summary>).",
        "iterations": 20,
    },
    "general": {
        "label": "通用助手",
        "tools": None,
        "prompt": "You are a sub-agent working on one part of a larger task. Stay within your subtask and record "
                  "important results on the blackboard with board_write.",
        "iterations": 30,
    },
}

CHILD_RULES = """# Sub-agent rules
- You were given ONE subtask by a coordinating agent. Do only that; do not ask the user questions (there is no user
  in this conversation) - if something is missing, state the assumption you made or report what is missing.
- The blackboard is shared with the coordinator and the other sub-agents: read it first (board_read) to avoid
  duplicate work, and write your results to it.
- Finish with a short report (at most about 300 words): what you found or did, the evidence, and anything left
  open. Put details, tables and long excerpts on the blackboard instead of in the report."""


def role_names() -> list[str]:
    return list(ROLES)


def tool_filter_for(role: str):
    spec = ROLES.get(role) or ROLES["general"]
    allowed = spec["tools"]

    def ok(tool) -> bool:
        if tool.name in CHILD_DENY:
            return False
        return allowed is None or tool.name in allowed
    return ok


def extract_json(text: str):
    """The first JSON object/array in a model reply (```json fences allowed), or None."""
    m = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", text or "", re.S)
    cands = [m.group(1)] if m else []
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = (text or "").find(opener), (text or "").rfind(closer)
        if 0 <= i < j:
            cands.append(text[i:j + 1])
    for c in cands:
        try:
            return json.loads(c)
        except ValueError:
            continue
    return None


async def run_subagent(rt, *, role: str, task: str, board_scope: str, parent_session: str | None = None,
                       approve=None, emit=None, context: str = "", hub=None, title: str | None = None,
                       max_iterations: int | None = None, owner: str | None = None, provider=None) -> dict:
    """Runs one child agent to completion; returns {role, task, status, result, session_id, cost_usd}."""
    from ..agent import Agent
    spec = ROLES.get(role) or ROLES["general"]
    sid = "sub-" + uuid.uuid4().hex[:10]
    label = spec["label"]

    def child_emit(kind: str, data: dict):
        if emit and kind in ("tool_call", "tool_result", "assistant", "turn_end", "approval_request"):
            brief = {"sub_session": sid, "role": role, "role_label": label, "event": kind}
            if kind == "tool_call":
                brief["tool"] = data.get("name")
            elif kind == "assistant" and data.get("final"):
                brief["text"] = (data.get("text") or "")[:400]
            elif kind == "turn_end":
                brief["status"] = data.get("status")
            emit("subagent", brief)

    agent = Agent(rt, sid, platform="subagent", emit=child_emit, approve=approve, tool_filter=tool_filter_for(role),
                  system_extra=spec["prompt"] + "\n\n" + CHILD_RULES, board_scope=board_scope,
                  max_iterations=max_iterations or spec["iterations"], title=title or f"[{label}] {task[:60]}",
                  learn=False, depth=1, provider=provider)
    if hub is not None:
        hub.children[sid] = (agent, owner or parent_session)
    prompt = f"Subtask ({label}):\n{task}"
    if context:
        prompt += f"\n\nContext from the coordinator:\n{context}"
    if emit:
        emit("subagent", {"sub_session": sid, "role": role, "role_label": label, "event": "start", "task": task[:300],
                          "parent_session": parent_session})
    try:
        text = await agent.run(prompt)
        cost = (getattr(agent, "last_turn_usage", None) or {}).get("cost_usd", 0)
        status = (getattr(agent, "last_status", None) or {}).get("status", "done")
    except Exception as e:  # noqa: BLE001
        log.exception("sub-agent %s failed", sid)
        text, status, cost = f"（子代理失敗：{e}）", "error", 0
    finally:
        if hub is not None:
            hub.children.pop(sid, None)
    return {"role": role, "role_label": label, "task": task, "status": status, "result": text, "session_id": sid,
            "cost_usd": cost}


async def run_many(rt, tasks: list[dict], *, board_scope: str, parent_session: str | None, approve=None, emit=None,
                   hub=None, limit: int = 3, owner: str | None = None) -> list[dict]:
    sem = asyncio.Semaphore(max(1, limit))

    async def one(t: dict):
        async with sem:
            return await run_subagent(rt, role=t.get("role") or "general", task=t["task"], board_scope=board_scope,
                                      parent_session=parent_session, approve=approve, emit=emit,
                                      context=t.get("context") or "", hub=hub, owner=owner)
    return await asyncio.gather(*(one(t) for t in tasks))
