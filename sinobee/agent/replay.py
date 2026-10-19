"""Replay stored conversations through the progress tracker (agent/progress.py) to see what the stop rules would
have done: which steps counted as progress, where the thinking model would have been consulted, and whether a
task would have come near the safety valve.

It reads only the stored transcript (no model is called), so it can be run on any SinoBee home - including a copy -
to tune K / N on real work before or after a change:

    sinobee progress-replay --last 50            # trigger distribution over the latest 50 turns
    sinobee progress-replay --session <id> --steps

Limits of a replay: files written by commands (a build script, node, python) are invisible here as they are in a
live turn; edit_file on a file the turn did not write first is counted as a change of unknown size; the thinking
model's advice is assumed to change nothing, so a replayed "stop" is an upper bound.
"""
from __future__ import annotations

from collections import Counter

from . import progress as P

TURN_STARTS = ("[自動續跑]", "[續跑]")
WEB_TOOLS = {"web_search", "web_fetch", "browser"}


def _is_error(text: str) -> bool:
    t = (text or "").lstrip()
    if "user did not approve" in t[:300]:
        return False
    return t.startswith(("ERROR", "REPEATED CALL")) or (t.startswith("[exit ") and not t.startswith("[exit 0"))


def turns(messages, whole: bool = False) -> list[dict]:
    """Split a transcript into turns: a turn starts at a message the user typed (or a resume). whole=True: the
    messages are one recorded turn (its first message may be a skill invocation or have attachments)."""
    out, cur = [], None
    if whole and messages:
        cur = {"start": 0, "request": messages[0].text()[:300], "steps": [], "final": ""}
        out.append(cur)
    for i, m in enumerate(messages):
        if whole:
            pass
        elif m.role == "user" and isinstance(m.content, str) and \
                (not m.content.startswith("[") or m.content.startswith(TURN_STARTS)):
            cur = {"start": i, "request": m.content[:300], "steps": [], "final": ""}
            out.append(cur)
            continue
        if cur is None:
            continue
        if m.role == "assistant":
            if m.tool_calls:
                cur["steps"].append([[c.name, c.arguments or {}, None, False, c.id] for c in m.tool_calls])
            elif m.text().strip():
                cur["final"] = m.text()[:300]
        elif m.role == "tool" and cur["steps"]:
            for row in cur["steps"][-1]:
                if row[4] == m.tool_call_id:
                    row[2] = m.text()
                    row[3] = _is_error(row[2])
    for t in out:
        t["steps"] = [[(n, a, r or "", e) for n, a, r, e, _ in step] for step in t["steps"]]
    return out


class _Files:
    """The files as the turn wrote them (write_file content, edit_file replacements)."""

    def __init__(self):
        self.text: dict[str, str] = {}

    def apply(self, name, args, ok):
        if not ok or not isinstance(args, dict) or not args.get("path"):
            return
        p = str(args["path"])
        if name in ("write_file", "skill_write_file") and isinstance(args.get("content"), str):
            self.text[p] = args["content"]
        elif name == "edit_file" and p in self.text and isinstance(args.get("old_string"), str):
            n = -1 if args.get("replace_all") else 1
            self.text[p] = self.text[p].replace(args["old_string"], str(args.get("new_string", "")), n)

    def read(self, p):
        if p in self.text:
            return self.text[p]
        raise FileNotFoundError(p)


def _checklist(items: list[dict], args: dict, ok: bool) -> None:
    if not ok or not isinstance(args, dict):
        return
    act = args.get("action")
    if act in ("set", "add"):
        new = [{"text": (it if isinstance(it, str) else (it or {}).get("text", "")), "status": "todo"}
               for it in args.get("items") or []]
        if act == "set":
            items.clear()
        for it in new:
            items.append({"id": len(items) + 1, **it})
    elif act == "update":
        for it in items:
            if it["id"] == args.get("id") and args.get("status"):
                it["status"] = args["status"]


def replay_turn(turn: dict, config=None, consult_max: int = 2) -> dict:
    """The tracker's verdict for every step and the decisions the loop would take (advice assumed not to help)."""
    files = _Files()
    tr = P.Tracker(config, read_file=files.read)
    items: list[dict] = []
    tr.set_checklist(items)
    consults, consult_at, decisions = 0, -99, []
    stop = None
    max_np = 0

    def ask(step, trigger, detail):
        nonlocal consults, consult_at, stop
        if consults >= consult_max:
            stop = stop or {"step": step, "trigger": "advice_exhausted", "after": trigger, "detail": detail}
            return
        if step - consult_at < 5:
            return
        consults += 1
        consult_at = step
        decisions.append({"step": step, "trigger": trigger, "detail": detail})
        if trigger in ("no_progress", "loop_repeat", "loop_alternating", "file_oscillation", "repeated_error",
                       "exploration_stall", "long_wait"):
            tr.advised(step)

    for n, step in enumerate(turn["steps"], 1):
        if n > tr.safety_max:
            stop = stop or {"step": n, "trigger": "safety_valve", "detail": f"{n} 步"}
            break
        if tr.health_steps and n > 1 and (n - 1) % tr.health_steps == 0:
            if items and any(i["status"] in ("todo", "doing") for i in items):
                ask(n, "health_steps", f"已執行 {n - 1} 步，驗收清單未完成")
            elif not items and not tr.produced_within(tr.health_steps):
                ask(n, "health_steps", f"已執行 {n - 1} 步，沒有驗收清單，最近 {tr.health_steps} 步沒有產出")
        for name, args, text, err in step:
            files.apply(name, args, not err)
            if name == "task_checklist":
                _checklist(items, args, not err)
        v = tr.observe(n, step, items)
        max_np = max(max_np, tr.no_progress)
        if v.get("loop"):
            ask(n, v["loop"]["trigger"], v["loop"]["detail"])
        elif tr.no_progress >= tr.k:
            ask(n, "no_progress", f"連續 {tr.no_progress} 步沒有進度（K={tr.k}）")
        if stop:
            break
    tools = Counter(name for step in turn["steps"] for name, *_ in step)
    return {"request": turn["request"], "steps": len(turn["steps"]), "decisions": decisions, "stop": stop,
            "max_no_progress": max_np, "progress_steps": sum(1 for t in tr.trace if t["progress"]),
            "web_share": round(sum(tools[t] for t in WEB_TOOLS) / max(1, sum(tools.values())), 2),
            "kinds": dict(Counter(f["kind"] for f in tr.files.values())), "trace": tr.trace,
            "thresholds": tr.thresholds()}


def _recorded_turns(memory, sid: str) -> list[dict]:
    """Turns as SinoBee recorded them (turns table: start/end message index), so messages the user typed while a
    turn ran stay in that turn; messages after the last record (a turn that was interrupted) form one more turn.
    Without records the transcript is split at the user's messages."""
    msgs = memory.load_messages(sid)
    with memory._lock:
        idx = [r["idx"] for r in memory.db.execute("SELECT idx FROM messages WHERE session_id=? ORDER BY idx", (sid,))]
        recs = [dict(r) for r in memory.db.execute(
            "SELECT start_idx, end_idx, status FROM turns WHERE session_id=? ORDER BY id", (sid,))]
    if not recs:
        return turns(msgs)
    pos = {v: i for i, v in enumerate(idx)}
    out, last = [], -1
    for r in recs:
        a = pos.get(r["start_idx"], 0)
        b = pos.get(r["end_idx"], len(msgs) - 1)
        part = turns(msgs[a:b + 1], whole=True) or [{"start": 0, "request": "", "steps": [], "final": ""}]
        merged = {"request": part[0]["request"], "final": part[-1]["final"], "status": r["status"],
                  "steps": [s for t in part for s in t["steps"]]}
        out.append(merged)
        last = max(last, b)
    rest = turns(msgs[last + 1:])
    for t in rest:
        t["status"] = "unrecorded"
    return out + rest


def replay_sessions(memory, config=None, *, session: str | None = None, last: int = 50) -> list[dict]:
    with memory._lock:
        if session:
            sids = [session]
        else:
            rows = memory.db.execute(
                "SELECT session_id, MAX(id) AS m FROM messages GROUP BY session_id ORDER BY m DESC LIMIT ?",
                (max(1, last * 3),)).fetchall()
            sids = [r["session_id"] for r in rows]
    out = []
    for sid in sids:                                   # newest conversations first, newest turns first
        ts = _recorded_turns(memory, sid)
        for i in range(len(ts) - 1, -1, -1):
            if not ts[i]["steps"]:
                continue
            r = replay_turn(ts[i], config, int(config.get("agent.consult_max", 2)) if config is not None else 2)
            out.append({"session_id": sid, "turn": i + 1, "status": ts[i].get("status"), **r})
            if not session and len(out) >= last:
                return out
    return out


def summary(rows: list[dict]) -> dict:
    trig = Counter(d["trigger"] for r in rows for d in r["decisions"])
    stops = Counter(r["stop"]["trigger"] for r in rows if r["stop"])
    np_turns = [r for r in rows if any(d["trigger"] == "no_progress" for d in r["decisions"])]
    return {"turns": len(rows), "steps_total": sum(r["steps"] for r in rows),
            "over_60_steps": sum(1 for r in rows if r["steps"] > 60), "max_steps": max((r["steps"] for r in rows), default=0),
            "consults": dict(trig), "stops": dict(stops),
            "no_progress_turns": len(np_turns),
            "no_progress_turns_web_heavy": sum(1 for r in np_turns if r["web_share"] >= 0.5),
            "near_safety_valve": [f"{r['session_id']}#{r['turn']}" for r in rows
                                  if r["steps"] >= 0.8 * r["thresholds"]["safety_max_steps"]]}
