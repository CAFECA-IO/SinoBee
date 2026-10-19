"""Milestones: a large task is planned by the thinking model as a graph of milestones and carried out milestone by
milestone (phase 2 of the workflow proposal).

  plan       the thinking model's plan has 2-8 milestones when the task is large: goal, acceptance outcomes,
             dependencies, outputs, and whether it is independent (parallel) - plus FACTS, the values every part
             must use the same way (units, names, key numbers), and the task type
  confirm    the plan waits for the user when the request is ambiguous (the plan has a question) or sub-agents
             would run in parallel; a clear task shows the plan and starts. Scheduled runs and resumes never wait
  sequential milestones run in the main agent one after another; each starts with a FRESH CONTEXT: the finished
             milestone's messages leave the model context (they stay in the transcript and the failure report)
             and only its summary, its outputs and the facts are carried on
  parallel   a milestone that needs nothing from the others goes to a sub-agent (one level: sub-agents never
             delegate); it writes facts to its own staging area, merged when it finishes - a value that disagrees
             with the shared one is decided by the thinking model
  corrections from the user apply at the next milestone boundary (default) or at once (interrupt and re-plan:
             the current milestone is closed, files are already in the turn's snapshot, finished milestones are
             kept, only the affected sub-agents are stopped). Neither is a "stop": the task is not user_stopped

The plan lives on the blackboard (kind=plan, key=milestones), in the run record and in the UI card, so a resumed
task continues from the first unfinished milestone.
"""
from __future__ import annotations

import json
import re
import time

STATUSES = ("todo", "doing", "done", "delegated", "failed", "dropped")
MAX_MILESTONES = 8
ROLES = ("researcher", "analyst", "writer", "general")
TASK_TYPES = ("research", "coding", "document", "data", "media", "other")
BOARD_KEY = "milestones"


def _ids(text: str) -> str:
    return re.sub(r"[^\w-]", "", str(text or ""))[:12]


def _parse_list(raw) -> list[dict]:
    ms, seen = [], set()
    for i, m in enumerate((raw or [])[:MAX_MILESTONES], 1):
        if not isinstance(m, dict) or not str(m.get("goal") or m.get("title") or "").strip():
            continue
        mid = _ids(m.get("id")) or f"m{i}"
        while mid in seen:
            mid += "x"
        seen.add(mid)
        role = str(m.get("role") or "general").lower()
        try:
            est = max(1, min(200, int(m.get("estimate_steps") or 10)))
        except (TypeError, ValueError):
            est = 10
        ms.append({"id": mid, "title": str(m.get("title") or m.get("goal"))[:80].strip(),
                   "goal": str(m.get("goal") or m.get("title"))[:400].strip(),
                   "acceptance": [str(a)[:200].strip() for a in (m.get("acceptance") or []) if str(a).strip()][:3],
                   "depends_on": [_ids(d) for d in (m.get("depends_on") or []) if _ids(d)],
                   "outputs": [str(o)[:160].strip() for o in (m.get("outputs") or []) if str(o).strip()][:6],
                   "parallel": bool(m.get("parallel")), "role": role if role in ROLES else "general",
                   "estimate_steps": est, "status": "todo", "summary": "", "result_outputs": []})
    for m in ms:                                    # a milestone that depends on others is not independent
        if m["depends_on"]:
            m["parallel"] = False
    if ms:
        ms[-1]["parallel"] = False                  # the last milestone integrates: it stays with the coordinator
    return ms


def parse(data: dict) -> dict | None:
    """The milestone part of a plan reply ({milestones, facts, task_type}); None when it is not a milestone plan
    (fewer than 2 milestones)."""
    raw = data.get("milestones") if isinstance(data, dict) else None
    ms = _parse_list(raw) if isinstance(raw, list) else []
    if len(ms) < 2:
        return None
    ids = {m["id"] for m in ms}
    for m in ms:
        m["depends_on"] = [d for d in m["depends_on"] if d in ids and d != m["id"]]
    facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
    tt = str(data.get("task_type") or "other").lower()
    return {"milestones": ms, "facts": {str(k)[:60]: str(v)[:300] for k, v in list(facts.items())[:30]},
            "task_type": tt if tt in TASK_TYPES else "other", "created": time.time(), "feedback": [],
            "confirmed": None, "revision": 1}


class Plan:
    def __init__(self, data: dict):
        self.data = data

    # ------------------------------------------------------------------ state
    @property
    def milestones(self) -> list[dict]:
        return self.data["milestones"]

    def get(self, mid: str) -> dict | None:
        return next((m for m in self.milestones if m["id"] == mid), None)

    def done_ids(self) -> set:
        return {m["id"] for m in self.milestones if m["status"] in ("done", "dropped")}

    def ready(self, m: dict) -> bool:
        return m["status"] == "todo" and all(d in self.done_ids() for d in m["depends_on"])

    def current(self) -> dict | None:
        return next((m for m in self.milestones if m["status"] == "doing"), None)

    def next_sequential(self) -> dict | None:
        """The next milestone the main agent works on: the first todo one that is not given to a sub-agent."""
        return next((m for m in self.milestones if m["status"] == "todo" and not m["parallel"]), None)

    def parallel_ready(self) -> list[dict]:
        return [m for m in self.milestones if m["parallel"] and self.ready(m)]

    def waiting_on(self, m: dict) -> list[dict]:
        return [self.get(d) for d in m["depends_on"] if self.get(d) and self.get(d)["status"] == "delegated"]

    def finished(self) -> bool:
        return all(m["status"] in ("done", "dropped") for m in self.milestones)

    def uses_subagents(self) -> bool:
        return any(m["parallel"] for m in self.milestones)

    def progress(self) -> tuple[int, int]:
        return len(self.done_ids()), len(self.milestones)

    # ------------------------------------------------------------------ text for the model and the UI
    def panel(self, facts: dict) -> str:
        """The milestone part of the per-step tail."""
        cur = self.current()
        lines = ["<milestones>", "This task runs as milestones (the thinking model's plan). Work ONLY on the current "
                 "milestone. When its acceptance outcomes are true and checked, call milestone(action='done', "
                 "summary=..., outputs=[...]) - the context is then cleared for the next milestone and only your "
                 "summary, the outputs and the facts are kept, so the summary must hold what later milestones need."]
        mark = {"todo": "[ ]", "doing": "[>]", "done": "[x]", "delegated": "[~ sub-agent]", "failed": "[!]",
                "dropped": "[-]"}
        for m in self.milestones:
            dep = f" (after {', '.join(m['depends_on'])})" if m["depends_on"] else ""
            lines.append(f"{mark.get(m['status'], '[ ]')} {m['id']} {m['title']}{dep}")
            if m["status"] == "done" and m.get("summary"):
                lines.append(f"    summary: {m['summary'][:600]}")
                if m.get("result_outputs"):
                    lines.append(f"    outputs: {', '.join(m['result_outputs'][:6])}")
        if cur:
            lines += [f"CURRENT: {cur['id']} - {cur['goal']}"]
            if cur["acceptance"]:
                lines.append("Acceptance: " + "; ".join(cur["acceptance"]))
            if cur["outputs"]:
                lines.append("Expected outputs: " + "; ".join(cur["outputs"]))
        elif self.finished():
            lines.append("All milestones are done: deliver the final result to the user.")
        if facts:
            lines.append("FACTS every part must use exactly (board kind=fact; change one only with "
                         "board_write(kind='fact', key=..., content=...) and say why):")
            lines += [f"- {k}: {v}" for k, v in list(facts.items())[:30]]
        lines.append("</milestones>")
        return "\n".join(lines)

    def card(self, facts: dict, phase: str) -> dict:
        return {"phase": phase, "task_type": self.data.get("task_type"), "revision": self.data.get("revision", 1),
                "milestones": [{k: m.get(k) for k in ("id", "title", "goal", "acceptance", "depends_on", "outputs",
                                                      "parallel", "role", "estimate_steps", "status", "summary",
                                                      "result_outputs", "sub_session")}
                               for m in self.milestones],
                "facts": facts, "done": self.progress()[0], "total": self.progress()[1],
                "feedback": [f for f in self.data.get("feedback", []) if not f.get("applied")]}

    def describe(self) -> str:
        """The plan as the user reads it before confirming."""
        out = []
        for m in self.milestones:
            who = f"（子代理・{m['role']}，與其他里程碑並行）" if m["parallel"] else ""
            dep = f"（接在 {', '.join(m['depends_on'])} 之後）" if m["depends_on"] else ""
            out.append(f"{m['id']}. {m['title']}{who}{dep}")
        return "\n".join(out)

    # ------------------------------------------------------------------ changes
    def apply_replan(self, new: dict) -> list[str]:
        """Replace the milestones that are not finished by the re-plan; finished ones are kept as they are.
        Returns the ids of delegated milestones the re-plan cancels."""
        keep = [m for m in self.milestones if m["status"] in ("done", "dropped")]
        running = {m["id"]: m for m in self.milestones if m["status"] == "delegated"}
        cancel = [c for c in new.get("cancel") or [] if c in running]
        fresh = _parse_list(new.get("milestones") or [])
        kept_ids = {m["id"] for m in keep}
        out = list(keep)
        for m in fresh:
            if m["id"] in kept_ids:
                continue
            if m["id"] in running and m["id"] not in cancel:
                out.append(running[m["id"]])            # still running and not cancelled: unchanged
                continue
            m["depends_on"] = [d for d in m["depends_on"] if d in kept_ids or d in {x["id"] for x in fresh}]
            out.append(m)
        for rid, m in running.items():                  # a running sub-agent the re-plan neither keeps nor cancels
            if rid not in {x["id"] for x in out} and rid not in cancel:
                out.insert(len(keep), m)
        if out and out[-1]["status"] == "todo":
            out[-1]["parallel"] = False
        self.data["milestones"] = out
        self.data["revision"] = self.data.get("revision", 1) + 1
        return cancel

    def to_json(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, text: str) -> "Plan | None":
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("milestones"), list):
            return None
        return cls(data)


# ------------------------------------------------------------------ the shared facts table on the blackboard
def facts(store, scope: str) -> dict:
    rows = store.board_read(scope, kinds=["fact"], limit=200)
    return {r["key"]: r["content"] for r in rows if r.get("key") and not r["key"].startswith("stage:")}


def staged(store, scope: str, sub_session: str) -> dict:
    pre = f"stage:{sub_session}:"
    rows = store.board_read(scope, kinds=["fact"], limit=200)
    return {r["key"][len(pre):]: r["content"] for r in rows if (r.get("key") or "").startswith(pre)
            and r.get("status") != "merged"}


def write_facts(store, scope: str, values: dict, author: str) -> None:
    for k, v in (values or {}).items():
        store.board_write(scope, "fact", str(v), key=str(k)[:60], author=author)


def merge_plan(store, scope: str, sub_session: str) -> tuple[dict, list[dict]]:
    """(facts to add, conflicts) from a sub-agent's staging area."""
    shared = facts(store, scope)
    add, conflicts = {}, []
    for k, v in staged(store, scope, sub_session).items():
        if k not in shared:
            add[k] = v
        elif _norm(shared[k]) != _norm(v):
            conflicts.append({"key": k, "shared": shared[k], "proposed": v})
    return add, conflicts


def mark_merged(store, scope: str, sub_session: str) -> None:
    pre = f"stage:{sub_session}:"
    for r in store.board_read(scope, kinds=["fact"], limit=200):
        if (r.get("key") or "").startswith(pre):
            store.board_set_status(r["id"], "merged")


def _norm(v: str) -> str:
    return re.sub(r"\s+", "", str(v)).lower()


def save(store, scope: str, plan: Plan) -> None:
    store.board_write(scope, "plan", plan.to_json(), key=BOARD_KEY, author="milestones",
                      status="done" if plan.finished() else "doing")


def load(store, scope: str) -> Plan | None:
    rows = store.board_read(scope, kinds=["plan"], key=BOARD_KEY, limit=1)
    return Plan.from_json(rows[-1]["content"]) if rows else None
