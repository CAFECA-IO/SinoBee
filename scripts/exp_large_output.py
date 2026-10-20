#!/usr/bin/env python3
"""Experiments for the proposal "complex large tasks no longer stop half-way" (SinoBee, 2026-10-20).

Resumable: every invocation advances the experiment for at most ~170 s and saves its state, so it can run where a
process may not outlive one command.

    python3 scripts/exp_large_output.py init  <dir> [model]   # writes the run plan
    python3 scripts/exp_large_output.py step  <dir>           # repeat until it prints "all runs done"
    python3 scripts/exp_large_output.py report <dir>

E1 (U3): ONE call that must write the whole page in one write_file, max_tokens 16384: default thinking vs
           reasoning_effort "low".
E2 (U2): a small agent loop that builds the page with write_file / edit_file (/ append_file), as SinoBee's loop
           does today (phase 0: on finish=length, the "write in parts" nudge and twice the limit, up to 32768)
           vs with the parts rule stated up front and an append tool (U2).
E3 (U2 revised): rules - fine sections, one per call, a reminder when a call is too big or near the limit - vs
           SinoBee driving the fill: one skeleton call, then one fresh call per placeholder with the file only.
Each call waits at most CALL_WAIT s (a longer one is recorded as a timeout, as the original failure was).
"""
import asyncio
import html.parser
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sinobee.agent.loop import TRUNCATED_NUDGE  # noqa: E402
from sinobee.config import Config  # noqa: E402
from sinobee.providers import build_provider  # noqa: E402
from sinobee.providers.openai_compat import to_openai_messages  # noqa: E402
from sinobee.types import Message, ToolCall, ToolSpec  # noqa: E402

BUDGET, START_BY, CALL_WAIT = 170, 95, 125
MAX_TURNS = 14
TASK = ("用動畫展與工程圖示介紹風力發電機組成與原理，並描述各項參數。Create wind_turbine.html: one self-contained "
        "interactive page with an isometric SVG of a wind turbine (blades, hub, nacelle cut-away showing main shaft, "
        "gearbox, generator, yaw system, tower), CSS/JS rotor animation, a wind-speed slider that changes the rotor "
        "speed and a live power readout P = 0.5*rho*A*v^3*Cp, a clickable info panel for every component, and a "
        "table of typical parameters. Then reply with a short summary.")
ONE_CALL = TASK + " Write the whole file with ONE write_file call."
BASE_SYSTEM = ("You are SinoBee, an agent that completes tasks with tools in a workspace folder. Use the tools to "
               "create the files; when the work is done, reply with a short summary in Traditional Chinese.")
U2_RULE = ("\n\nLARGE OUTPUTS: never put more than about 12,000 characters into one tool call. For a file larger than "
           "that, first write_file a skeleton (structure, styles, and placeholder comments such as "
           "<!-- SECTION: svg --> for the parts still to come), then fill one section per call with edit_file "
           "(replace its placeholder) or append_file. Keep each call's reasoning short.")
SECTION_RE = re.compile(r"<!--\s*SECTION:\s*([^>]+?)\s*-->|/\*\s*SECTION:\s*(.+?)\s*\*/")
OVER = 30000
SKELETON = ("\n\nWork in sections. First write_file wind_turbine.html as a SKELETON only: the document structure, the "
            "styles, and one placeholder per part still to write - <!-- SECTION: name --> in HTML/SVG, /* SECTION: name */ "
            "inside <script> - with one section per component or feature (e.g. tower, nacelle, rotor, gearbox, generator, "
            "yaw, slider, power readout, info panel, parameter table, animation logic), so each section is small.")
RULE3 = ("\n\nLARGE OUTPUTS: fill ONE section per tool call: edit_file replacing exactly one placeholder with that "
         "section's content. Never write or rewrite the whole file again once the skeleton exists. When no placeholder "
         "is left, stop and reply with a short summary.")
WRITE = ToolSpec("write_file", "Create or overwrite a file with the given content.",
                 {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                  "required": ["path", "content"]})
EDIT = ToolSpec("edit_file", "Replace an exact string in a file (old_string must occur once).",
                {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string"},
                                                  "new_string": {"type": "string"}},
                 "required": ["path", "old_string", "new_string"]})
APPEND = ToolSpec("append_file", "Append content to the end of a file (creates it).",
                  {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                   "required": ["path", "content"]})


# ---------------------------------------------------------------- state
def load(d):
    return json.loads((Path(d) / "state.json").read_text(encoding="utf-8"))


def save(d, st):
    p = Path(d) / "state.json"
    p.with_suffix(".tmp").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    p.with_suffix(".tmp").replace(p)


def enc(m: Message) -> dict:
    return {"role": m.role, "text": m.text(), "tool_call_id": m.tool_call_id, "name": m.name,
            "calls": [{"id": c.id, "name": c.name, "arguments": c.arguments, "extra": c.extra} for c in m.tool_calls]}


def dec(x: dict) -> Message:
    return Message(x["role"], x["text"], [ToolCall(c["id"], c["name"], c["arguments"], c.get("extra"))
                                          for c in x.get("calls") or []],
                   tool_call_id=x.get("tool_call_id"), name=x.get("name"))


# ---------------------------------------------------------------- checks of the page
class _Tags(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.bad = [], 0
    VOID = {"br", "hr", "img", "input", "meta", "link", "path", "circle", "rect", "line", "polygon", "polyline",
            "ellipse", "stop", "use", "source", "col", "area", "base", "wbr", "animate", "animateTransform"}

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                self.bad += 1
        else:
            self.bad += 1


def page_checks(text: str) -> dict:
    t = text or ""
    p = _Tags()
    try:
        p.feed(t)
    except Exception:  # noqa: BLE001
        p.bad += 99
    return {"chars": len(t), "svg": "<svg" in t, "slider": bool(re.search(r"type=['\"]range", t)),
            "script": "<script" in t, "power": bool(re.search(r"0\.5|Cp|cp", t)),
            "placeholders_left": len(SECTION_RE.findall(t)), "unclosed": len(p.stack) + p.bad,
            "closed_html": t.rstrip().lower().endswith("</html>")}


# ---------------------------------------------------------------- one model call
async def call(p, messages, tools, max_tokens, effort=None):
    """One streamed call through SinoBee's provider; the timeout is CALL_WAIT."""
    t = time.monotonic()
    if effort:
        payload = {"model": p.model, "messages": to_openai_messages(messages, True, True),
                   "max_completion_tokens": max_tokens, "stream": True, "stream_options": {"include_usage": True},
                   "reasoning_effort": effort}
        if tools:
            payload["tools"] = [{"type": "function", "function": {"name": x.name, "description": x.description,
                                                                  "parameters": x.parameters}} for x in tools]
        try:
            data = await asyncio.wait_for(p._post(f"{p.base_url}/chat/completions", payload, p._headers(),
                                                  timeout=CALL_WAIT), CALL_WAIT + 5)
        except Exception as e:  # noqa: BLE001
            return None, round(time.monotonic() - t, 1), f"{type(e).__name__}: {str(e)[:200]}"
        ch = (data.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        calls = [ToolCall(c.get("id") or f"c{i}", (c.get("function") or {}).get("name", ""),
                          _args((c.get("function") or {}).get("arguments")), c.get("extra_content"))
                 for i, c in enumerate(msg.get("tool_calls") or [])]
        u = data.get("usage") or {}
        return ({"msg": Message("assistant", msg.get("content") or "", calls), "finish": ch.get("finish_reason"),
                 "out": u.get("completion_tokens"), "total": u.get("total_tokens")}, round(time.monotonic() - t, 1), None)
    p.timeout = CALL_WAIT
    try:
        r = await asyncio.wait_for(p._chat(messages, tools, temperature=None, max_tokens=max_tokens), CALL_WAIT + 5)
    except Exception as e:  # noqa: BLE001
        return None, round(time.monotonic() - t, 1), f"{type(e).__name__}: {str(e)[:200]}"
    return ({"msg": r.message, "finish": r.stop_reason, "out": r.usage.output_tokens, "total": None},
            round(time.monotonic() - t, 1), None)


def _args(raw):
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_raw": raw}


# ---------------------------------------------------------------- E1
async def e1(p, run):
    effort = "low" if run["cond"] == "low" else None
    r, s, err = await call(p, [Message("user", ONE_CALL)], [WRITE], 16384, effort)
    row = {"seconds": s, "error": err}
    if r:
        c = next((c for c in r["msg"].tool_calls if c.name == "write_file"), None)
        content = (c.arguments.get("content") if c else "") or ""
        row.update(finish=r["finish"], out_tokens=r["out"], total_tokens=r["total"], wrote=bool(c),
                   **{f"page_{k}": v for k, v in page_checks(content).items()})
    run.update(done=True, result=row)


# ---------------------------------------------------------------- E2
def apply_tool(ws: Path, c: ToolCall) -> str:
    a = c.arguments or {}
    path = ws / Path(str(a.get("path") or "out.html")).name
    if c.name == "write_file":
        path.write_text(str(a.get("content") or ""), encoding="utf-8")
        return f"wrote {path.name} ({len(str(a.get('content') or ''))} chars)"
    if c.name == "append_file":
        with path.open("a", encoding="utf-8") as f:
            f.write(str(a.get("content") or ""))
        return f"appended to {path.name} (now {path.stat().st_size} bytes)"
    if c.name == "edit_file":
        if not path.exists():
            return "ERROR: file does not exist"
        t = path.read_text(encoding="utf-8")
        old = str(a.get("old_string") or "")
        if not old or t.count(old) != 1:
            return f"ERROR: old_string found {t.count(old) if old else 0} times (must be exactly once)"
        path.write_text(t.replace(old, str(a.get("new_string") or ""), 1), encoding="utf-8")
        return f"edited {path.name} (now {path.stat().st_size} bytes)"
    return f"ERROR: unknown tool {c.name}"


async def e2_turn(p, run, ws: Path):
    u2 = run["cond"] == "u2"
    tools = [WRITE, EDIT, APPEND] if u2 else [WRITE, EDIT]
    msgs = [dec(x) for x in run["messages"]]
    request = [Message("system", BASE_SYSTEM + (U2_RULE if u2 else ""))] + msgs
    if run.get("nudge"):
        request = request + [Message("user", TRUNCATED_NUDGE)]
    r, s, err = await call(p, request, tools, run["max_tokens"])
    log = {"turn": run["turns"] + 1, "seconds": s, "max_tokens": run["max_tokens"], "error": err}
    run["turns"] += 1
    run["seconds"] = round(run.get("seconds", 0) + s, 1)
    if err:
        log["kind"] = "timeout" if "Timeout" in err or "timed out" in err else "error"
        run["log"].append(log)
        run.update(done=True, outcome="stopped: " + log["kind"])
        return
    m = r["msg"]
    log.update(finish=r["finish"], out_tokens=r["out"], calls=[c.name for c in m.tool_calls],
               arg_chars=max([len(json.dumps(c.arguments, ensure_ascii=False)) for c in m.tool_calls] or [0]))
    run["log"].append(log)
    if not m.tool_calls and not m.text().strip():
        trunc = str(r["finish"]).lower() in ("length", "max_tokens")
        run["empty"] = run.get("empty", 0) + 1
        run["truncations"] = run.get("truncations", 0) + (1 if trunc else 0)
        if trunc:
            run["max_tokens"] = min(run["max_tokens"] * 2, 32768)
        run["nudge"] = True
        if run["empty"] > 2:
            run.update(done=True, outcome="stopped: 3 empty replies")
        return
    run["nudge"] = False
    run["messages"].append(enc(m))
    if not m.tool_calls:
        run.update(done=True, outcome="finished")
        return
    for c in m.tool_calls:
        run["messages"].append(enc(Message("tool", apply_tool(ws, c), tool_call_id=c.id, name=c.name)))
    if run["turns"] >= MAX_TURNS:
        run.update(done=True, outcome="stopped: turn limit")


def finish_e2(run, ws: Path):
    f = ws / "wind_turbine.html"
    files = sorted(ws.glob("*.html"))
    target = f if f.exists() else (files[0] if files else None)
    run["page"] = page_checks(target.read_text(encoding="utf-8")) if target else {"chars": 0}
    run["max_arg_chars"] = max([x.get("arg_chars", 0) for x in run["log"]] or [0])


# ---------------------------------------------------------------- E3
WARN_OVER = ("[SinoBee notice] Your last tool call carried {n} characters - more than one section. Fill only ONE "
             "section (one placeholder) in the next call.")
WARN_NEAR = ("[SinoBee notice] Your last reply used {pct}% of the output limit and nearly ran out. Keep the next call to "
             "one small section and keep your reasoning short.")


def page_of(ws: Path) -> str:
    f = ws / "wind_turbine.html"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def note_size(run, m, out_tokens):
    """The feedback of revised U2 (rule condition): over-limit -> reminder in the next request; near the limit too."""
    big = max([len(str((c.arguments or {}).get("content") or (c.arguments or {}).get("new_string") or ""))
               for c in m.tool_calls] or [0])
    tips = []
    if big > OVER:
        tips.append(WARN_OVER.format(n=big))
    if out_tokens and out_tokens > 0.7 * run["max_tokens"]:
        tips.append(WARN_NEAR.format(pct=round(100 * out_tokens / run["max_tokens"])))
    run["tips"] = tips


async def e3_rule_turn(p, run, ws: Path):
    """Revised U2 as rules: fine sections, one per call, reminders when a call is too big or near the limit."""
    msgs = [dec(x) for x in run["messages"]]
    request = [Message("system", BASE_SYSTEM + SKELETON + RULE3)] + msgs
    tail = list(run.get("tips") or []) + ([TRUNCATED_NUDGE] if run.get("nudge") else [])
    if tail:
        request = request + [Message("user", "\n\n".join(tail))]
    r, s, err = await call(p, request, [WRITE, EDIT], run["max_tokens"])
    await _record(run, r, s, err, ws, rule=True)


async def e3_driven_turn(p, run, ws: Path):
    """SinoBee drives: one skeleton call, then one fresh call per placeholder with the file and that section only."""
    page = page_of(ws)
    todo = [a or b for a, b in SECTION_RE.findall(page)]
    if not page:
        request = [Message("system", BASE_SYSTEM + SKELETON),
                   Message("user", TASK + "\n\nNow write ONLY the skeleton with write_file; do not fill the sections yet.")]
        if run.get("nudge"):
            request.append(Message("user", TRUNCATED_NUDGE))
        tools = [WRITE]
        run["current"] = None
    elif todo:
        skipped = set(run.get("skipped") or [])
        name = next((t for t in todo if t not in skipped), None)
        if name is None:
            run.update(done=True, outcome="stopped: sections not filled")
            return
        run["current"] = name
        mark = next(m.group(0) for m in SECTION_RE.finditer(page) if (m.group(1) or m.group(2)) == name)
        request = [Message("system", BASE_SYSTEM),
                   Message("user", f"TASK: {TASK}\n\nCURRENT FILE wind_turbine.html:\n```html\n{page}\n```\n\n"
                                   f"Fill ONLY the section '{name}': call edit_file once with old_string exactly "
                                   f"{json.dumps(mark)} and new_string the complete content of that section, consistent "
                                   "with the ids, classes and functions used elsewhere in the file.")]
        if run.get("nudge"):
            request.append(Message("user", TRUNCATED_NUDGE))
        tools = [EDIT]
    else:
        run.update(done=True, outcome="finished")
        return
    r, s, err = await call(p, request, tools, run["max_tokens"])
    before = len(todo)
    await _record(run, r, s, err, ws, rule=False)
    if not run["done"] and page and run.get("current") and len(SECTION_RE.findall(page_of(ws))) >= before \
            and not run.get("nudge"):
        run["misses"] = run.get("misses", {})
        run["misses"][run["current"]] = run["misses"].get(run["current"], 0) + 1
        if run["misses"][run["current"]] >= 2:
            run.setdefault("skipped", []).append(run["current"])
    if not run["done"] and page_of(ws) and not SECTION_RE.findall(page_of(ws)):
        run.update(done=True, outcome="finished")


async def _record(run, r, s, err, ws: Path, rule: bool):
    log = {"turn": run["turns"] + 1, "seconds": s, "max_tokens": run["max_tokens"], "error": err,
           "section": run.get("current")}
    run["turns"] += 1
    run["seconds"] = round(run.get("seconds", 0) + s, 1)
    if err:
        log["kind"] = "timeout" if "Timeout" in err or "timed out" in err else "error"
        run["log"].append(log)
        run.update(done=True, outcome="stopped: " + log["kind"])
        return
    m = r["msg"]
    log.update(finish=r["finish"], out_tokens=r["out"], calls=[c.name for c in m.tool_calls],
               arg_chars=max([len(json.dumps(c.arguments, ensure_ascii=False)) for c in m.tool_calls] or [0]))
    run["log"].append(log)
    if not m.tool_calls and not m.text().strip():
        trunc = str(r["finish"]).lower() in ("length", "max_tokens")
        run["empty"] = run.get("empty", 0) + 1
        run["truncations"] = run.get("truncations", 0) + (1 if trunc else 0)
        if trunc:
            run["max_tokens"] = min(run["max_tokens"] * 2, 32768)
        run["nudge"] = True
        if run["empty"] > 2:
            run.update(done=True, outcome="stopped: 3 empty replies")
        return
    run["nudge"] = False
    if rule:
        note_size(run, m, r["out"])
        run["messages"].append(enc(m))
        if not m.tool_calls:
            run.update(done=True, outcome="finished")
            return
        for c in m.tool_calls:
            run["messages"].append(enc(Message("tool", apply_tool(ws, c), tool_call_id=c.id, name=c.name)))
    else:
        for c in m.tool_calls:
            log.setdefault("results", []).append(apply_tool(ws, c)[:120])
    if run["turns"] >= run.get("max_turns", MAX_TURNS):
        run.update(done=True, outcome="stopped: turn limit")


# ---------------------------------------------------------------- driver
def init(d, model, only=None):
    Path(d).mkdir(parents=True, exist_ok=True)
    runs = []
    for i in range(3):
        for cond in ("default", "low"):
            runs.append({"id": f"e1-{cond}-{i + 1}", "exp": "e1", "cond": cond, "done": False})
    for i in range(3):
        for cond in ("phase0", "u2"):
            runs.append({"id": f"e2-{cond}-{i + 1}", "exp": "e2", "cond": cond, "done": False, "turns": 0,
                         "max_tokens": 16384, "messages": [enc(Message("user", TASK))], "log": []})
    for i in range(3):
        for cond in ("rule", "driven"):
            runs.append({"id": f"e3-{cond}-{i + 1}", "exp": "e3", "cond": cond, "done": False, "turns": 0,
                         "max_tokens": 16384, "max_turns": 24, "messages": [enc(Message("user", TASK))], "log": []})
    if only:
        runs = [r for r in runs if r["id"] in only]
    save(d, {"model": model, "created": time.time(), "runs": runs})
    print(f"planned {len(runs)} runs in {d}")


async def step(d):
    st = load(d)
    p = build_provider(st["model"], Config.load())
    p.stream = True
    t0 = time.monotonic()
    while time.monotonic() - t0 < START_BY:
        run = next((r for r in st["runs"] if not r["done"]), None)
        if run is None:
            print("all runs done")
            return
        if run["exp"] == "e1":
            await e1(p, run)
        else:
            ws = Path(d) / run["id"]
            ws.mkdir(exist_ok=True)
            if run["exp"] == "e3":
                await (e3_rule_turn if run["cond"] == "rule" else e3_driven_turn)(p, run, ws)
            else:
                await e2_turn(p, run, ws)
            if run["done"]:
                finish_e2(run, ws)
        save(d, st)
        print(run["id"], "done" if run["done"] else f"turn {run['turns']}",
              json.dumps(run.get("result") or (run["log"][-1] if run.get("log") else {}), ensure_ascii=False)[:300])
    left = sum(1 for r in st["runs"] if not r["done"])
    print(f"{left} run(s) left")


def report(d):
    dirs = [Path(d)] if (Path(d) / "state.json").exists() else sorted(x for x in Path(d).iterdir() if (x / "state.json").exists())
    st = {"model": load(dirs[0])["model"], "runs": [r for x in dirs for r in load(x)["runs"]]}
    out = {"model": st["model"], "e1": [], "e2": []}
    for r in st["runs"]:
        if r["exp"] == "e1":
            out["e1"].append({"id": r["id"], **(r.get("result") or {})})
        else:
            out["e2"].append({"id": r["id"], "outcome": r.get("outcome"), "turns": r.get("turns"),
                              "seconds": r.get("seconds"), "truncations": r.get("truncations", 0),
                              "max_arg_chars": r.get("max_arg_chars"), "page": r.get("page"),
                              "log": [{k: v for k, v in x.items() if k in ("turn", "seconds", "finish", "calls",
                                                                           "arg_chars", "max_tokens", "error")}
                                      for x in r.get("log") or []]})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    cmd, d = sys.argv[1], sys.argv[2]
    if cmd == "init":
        init(d, sys.argv[3] if len(sys.argv) > 3 else Config.load().get("model.default"), sys.argv[4:] or None)
    elif cmd == "step":
        asyncio.run(step(d))
    else:
        report(d)
