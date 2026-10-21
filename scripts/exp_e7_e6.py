#!/usr/bin/env python3
"""Experiments E7 and E6 of the proposal "complex large tasks no longer stop half-way" (SinoBee, 2026-10-21).

Resumable like scripts/exp_large_output.py: every `step` advances for at most ~170 s and saves its state.

    python3 scripts/exp_e7_e6.py init  <dir> [model]
    python3 scripts/exp_e7_e6.py step  <dir>              # repeat until "all runs done"
    python3 scripts/exp_e7_e6.py judge <dir> [judge]      # after the pages were checked (exp_e7_check.py)
    python3 scripts/exp_e7_e6.py report <dir>

Both build the wind-turbine page with the section rule SinoBee uses since phase 1 (skeleton, then one section per
edit_file call):

E7 (U3 visual quality): default thinking vs reasoning_effort "low" on every call, output limit 32768 (the phase-1
    default for gemini *-latest), 3 runs each. Quality: screenshots, console errors, animation and the slider
    driving the readout (exp_e7_check.py, run where a browser is), then the thinking model judges the screenshots
    blind, in pairs, both orders.
E6 (U8 write steps): output limit 65536 (the model's max) with low thinking vs 16384 with default thinking, 3 runs
    each: completion, cost per run, and the longest silence (the longest single call - Gemini sends a tool call
    whole, so nothing arrives before it ends).
Each call may take CALL_WAIT s (this machine runs a command for at most 180 s); a longer call counts as a timeout.
"""
import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from exp_large_output import (BASE_SYSTEM, EDIT, RULE3, SECTION_RE, SKELETON, TASK, WRITE, _args, apply_tool,  # noqa: E402
                              dec, enc, load, note_size, page_checks, page_of, save)
from sinobee.agent.loop import TRUNCATED_NUDGE  # noqa: E402
from sinobee.config import Config  # noqa: E402
from sinobee.pricing import cost_usd, price_for  # noqa: E402
from sinobee.providers import build_provider  # noqa: E402
from sinobee.providers.openai_compat import to_openai_messages  # noqa: E402
from sinobee.types import ImagePart, Message, TextPart, ToolCall  # noqa: E402

START_BY = int(os.environ.get("EXP_START_BY", 95))
CALL_WAIT = int(os.environ.get("EXP_CALL_WAIT", 150))     # this machine: a command runs at most 180 s
MAX_TURNS = 26
CONDS = {"e7-default": (32768, None), "e7-low": (32768, "low"), "e6-max-low": (65536, "low"), "e6-16k": (16384, None)}


async def call(p, messages, tools, max_tokens, effort):
    """One streamed call with the same payload for every condition (reasoning_effort only when asked)."""
    t = time.monotonic()
    payload = {"model": p.model, "messages": to_openai_messages(messages, True, True), "max_completion_tokens": max_tokens,
               "stream": True, "stream_options": {"include_usage": True},
               "tools": [{"type": "function", "function": {"name": x.name, "description": x.description,
                                                           "parameters": x.parameters}} for x in tools]}
    if effort:
        payload["reasoning_effort"] = effort
    try:
        data = await asyncio.wait_for(p._post(f"{p.base_url}/chat/completions", payload, p._headers(), timeout=CALL_WAIT),
                                      CALL_WAIT + 5)
    except Exception as e:  # noqa: BLE001
        return None, round(time.monotonic() - t, 1), f"{type(e).__name__}: {str(e)[:200]}"
    ch = (data.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    calls = [ToolCall(c.get("id") or f"c{i}", (c.get("function") or {}).get("name", ""),
                      _args((c.get("function") or {}).get("arguments")), c.get("extra_content"))
             for i, c in enumerate(msg.get("tool_calls") or [])]
    u = data.get("usage") or {}
    return ({"msg": Message("assistant", msg.get("content") or "", calls), "finish": ch.get("finish_reason"),
             "in": u.get("prompt_tokens") or 0, "out": u.get("completion_tokens") or 0}, round(time.monotonic() - t, 1), None)


async def turn(p, run, ws: Path):
    msgs = [dec(x) for x in run["messages"]]
    request = [Message("system", BASE_SYSTEM + SKELETON + RULE3)] + msgs
    tail = list(run.get("tips") or []) + ([TRUNCATED_NUDGE] if run.get("nudge") else [])
    if tail:
        request = request + [Message("user", "\n\n".join(tail))]
    r, s, err = await call(p, request, [WRITE, EDIT], run["max_tokens"], run["effort"])
    log = {"turn": run["turns"] + 1, "seconds": s, "error": err}
    run["turns"] += 1
    run["seconds"] = round(run.get("seconds", 0) + s, 1)
    if err:
        log["kind"] = "timeout" if "Timeout" in err or "timed out" in err else "error"
        run["log"].append(log)
        run.update(done=True, outcome="stopped: " + log["kind"])
        return
    m = r["msg"]
    run["in_tokens"] = run.get("in_tokens", 0) + r["in"]
    run["out_tokens"] = run.get("out_tokens", 0) + r["out"]
    log.update(finish=r["finish"], in_tokens=r["in"], out_tokens=r["out"], calls=[c.name for c in m.tool_calls],
               arg_chars=max([len(json.dumps(c.arguments, ensure_ascii=False)) for c in m.tool_calls] or [0]))
    run["log"].append(log)
    if not m.tool_calls and not m.text().strip():
        run["empty"] = run.get("empty", 0) + 1
        run["truncations"] = run.get("truncations", 0) + int(str(r["finish"]).lower() in ("length", "max_tokens"))
        run["nudge"] = True
        if run["empty"] > 2:
            run.update(done=True, outcome="stopped: 3 empty replies")
        return
    run["nudge"] = False
    note_size(run, m, r["out"])
    run["messages"].append(enc(m))
    if not m.tool_calls:
        run.update(done=True, outcome="finished")
        return
    for c in m.tool_calls:
        run["messages"].append(enc(Message("tool", apply_tool(ws, c), tool_call_id=c.id, name=c.name)))
    if run["turns"] >= MAX_TURNS:
        run.update(done=True, outcome="stopped: turn limit")


def finish(run, ws: Path, model: str):
    page = page_of(ws)
    run["page"] = page_checks(page)
    run["completed"] = run.get("outcome") == "finished" and bool(page) and not SECTION_RE.findall(page)
    run["longest_call_s"] = max([x.get("seconds", 0) for x in run["log"]] or [0])
    prov, _, mid = model.partition("/")
    price, _src = price_for(prov, mid)
    run["cost_usd"] = round(cost_usd(price, run.get("in_tokens", 0), run.get("out_tokens", 0)) or 0, 5) if price else None


def init(d, model):
    Path(d).mkdir(parents=True, exist_ok=True)
    runs = []
    for i in range(3):
        for cond, (mx, effort) in CONDS.items():
            runs.append({"id": f"{cond}-{i + 1}", "exp": cond.split("-")[0], "cond": cond, "done": False, "turns": 0,
                         "max_tokens": mx, "effort": effort, "messages": [enc(Message("user", TASK))], "log": []})
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
        ws = Path(d) / run["id"]
        ws.mkdir(exist_ok=True)
        await turn(p, run, ws)
        if run["done"]:
            finish(run, ws, st["model"])
        save(d, st)
        last = run["log"][-1] if run["log"] else {}
        print(run["id"], run.get("outcome") or f"turn {run['turns']}",
              json.dumps({k: last.get(k) for k in ("seconds", "finish", "out_tokens", "arg_chars", "error")}, ensure_ascii=False))
    print(f"{sum(1 for r in st['runs'] if not r['done'])} run(s) left")


# ---------------------------------------------------------------- blind judging of the screenshots (E7)
JUDGE = """You compare two web pages built for the same request, from screenshots only (you are not told how they were
made). For each page you get a desktop screenshot on load, the same view 3 seconds later, and a phone screenshot.

REQUEST: {task}

Judge what is VISIBLE: an accurate, complete, legible drawing of the wind turbine and its parts (blades, hub, nacelle
cut-away with shaft, gearbox, generator, yaw, tower); motion between the two desktop shots; the wind-speed control
and the power readout; the parameter table; layout, labels, contrast and the phone view. Ignore which page is
first.

Reply with JSON only: {{"winner": "A"|"B"|"tie", "A": <1-10>, "B": <1-10>, "reason": "<two sentences>"}}"""


def _shots(d: Path, rid: str) -> list[bytes]:
    out = []
    for name in ("wind_turbine.html-desktop-load.jpg", "wind_turbine.html-desktop-later.jpg", "wind_turbine.html-phone.jpg"):
        f = d / rid / "_shots" / name
        if f.exists():
            out.append(f.read_bytes())
    return out


async def judge(d, judge_model=None):
    st = load(d)
    cfg = Config.load()
    jm = judge_model or cfg.get("model.thinking") or cfg.get("model.default")
    p = build_provider(jm, cfg)
    pairs = st.setdefault("judging", {"model": jm, "pairs": []})
    if not pairs["pairs"]:
        for i in range(1, 4):
            for first in ("e7-default", "e7-low"):          # both orders
                other = "e7-low" if first == "e7-default" else "e7-default"
                pairs["pairs"].append({"A": f"{first}-{i}", "B": f"{other}-{i}", "done": False})
        save(d, st)
    t0 = time.monotonic()
    for pr in pairs["pairs"]:
        if pr["done"] or time.monotonic() - t0 > START_BY:
            continue
        a, b = _shots(Path(d), pr["A"]), _shots(Path(d), pr["B"])
        if not a or not b:
            pr.update(done=True, result={"winner": "B" if not a and b else "A" if a and not b else "tie",
                                         "reason": "missing screenshots (no page)"})
            save(d, st)
            continue
        parts = [TextPart(JUDGE.format(task=TASK))]
        for label, imgs in (("A", a), ("B", b)):
            parts.append(TextPart(f"PAGE {label}:"))
            parts += [ImagePart.from_bytes(x, "image/jpeg") for x in imgs]
        from sinobee.learning.reflector import parse_json
        try:
            r = await asyncio.wait_for(p.chat([Message("user", parts)], None, temperature=0, max_tokens=8192), CALL_WAIT)
            data = parse_json(r.message.text()) or {}
        except Exception as e:  # noqa: BLE001
            data = {"error": str(e)[:200]}
        pr.update(done="winner" in data, result=data)
        save(d, st)
        print(pr["A"], "vs", pr["B"], json.dumps(data, ensure_ascii=False)[:300])
    print(f"{sum(1 for x in pairs['pairs'] if not x['done'])} pair(s) left")


def report(d):
    st = load(d)
    out = {"model": st["model"], "runs": [], "judging": st.get("judging")}
    for r in st["runs"]:
        out["runs"].append({k: r.get(k) for k in ("id", "outcome", "completed", "turns", "seconds", "longest_call_s",
                                                  "truncations", "in_tokens", "out_tokens", "cost_usd", "page", "checks")})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    random.seed(7)
    cmd, d = sys.argv[1], sys.argv[2]
    if cmd == "init":
        init(d, sys.argv[3] if len(sys.argv) > 3 else Config.load().get("model.default"))
    elif cmd == "step":
        asyncio.run(step(d))
    elif cmd == "judge":
        asyncio.run(judge(d, sys.argv[3] if len(sys.argv) > 3 else None))
    else:
        report(d)
