"""Playtest: use what was built the way a person would, and keep pictures of it (sandbox plan V1, V2, V6).

One `playtest` call runs a whole sequence of mouse and keyboard actions on the sandbox desktop with real timing -
press, hold, drag along a path, type, wait - and takes pictures where the sequence says so: a single shot, a series
of frames (to see motion), or a short video clip. The sequence runs inside the container in one go (PLAY below),
so a game gets its inputs at the right moments; a step-by-step tool loop is far too slow for that.

Each picture is kept as evidence (evidence.py) and, when the step says what it should show (`expect`), a vision
model compares the picture with that expectation. The agent gets the verdicts and the failing pictures back, fixes
what is wrong and plays again; the quality gate (agent/loop.py) asks for a playtest before an interactive page or
game in the sandbox may be called done. With `width: "mobile"` the page is tried at a phone's width as well.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time

log = logging.getLogger("sinobee.playtest")

MAX_STEPS = 200
MAX_SECONDS = 180
MAX_JUDGED = 12

# runs in the sandbox: {"steps", "scale", "keys", "w", "h"} on stdin -> {"shots", "log", "error"} on stdout
PLAY = r'''
import base64, io, json, os, subprocess, sys, time
spec = json.load(sys.stdin)
S = float(spec.get("scale") or 1.0)
KEYS = spec.get("keys") or {}
out = {"shots": [], "log": [], "error": None}
t0 = time.time()

def xdo(*a):
    subprocess.run(["xdotool", *[str(x) for x in a]], capture_output=True, timeout=30)

def key(k):
    return "+".join(KEYS.get(p.strip().lower(), p.strip()) for p in str(k).replace(" ", "").split("+") if p.strip())

def pt(p):
    return int(round(float(p[0]) * S)), int(round(float(p[1]) * S))

def grab(fmt):
    import mss
    from PIL import Image
    with (mss.MSS() if hasattr(mss, "MSS") else mss.mss()) as m:
        g = m.grab(m.monitors[1])
        img = Image.frombytes("RGB", g.size, g.bgra, "raw", "BGRX")
    buf = io.BytesIO()
    if fmt == "jpg":
        img.save(buf, "JPEG", quality=80)
    else:
        img.save(buf, "PNG", compress_level=3)
    return base64.b64encode(buf.getvalue()).decode()

rec = None
try:
    for i, st in enumerate(spec["steps"]):
        out["log"].append([round(time.time() - t0, 2), i])
        if "move" in st:
            x, y = pt(st["move"]); xdo("mousemove", "--sync", x, y)
        elif "click" in st:
            x, y = pt(st["click"]); b = {"left": 1, "middle": 2, "right": 3}.get(st.get("button", "left"), 1)
            xdo("mousemove", "--sync", x, y); xdo("click", "--repeat", int(st.get("count", 1)), "--delay", 90, b)
        elif "down" in st:
            x, y = pt(st["down"]); xdo("mousemove", "--sync", x, y, "mousedown", 1)
        elif "up" in st:
            xdo("mouseup", 1)
        elif "drag" in st:
            pts = [pt(p) for p in st["drag"]]
            step = max(0.01, float(st.get("seconds", 1)) / max(1, len(pts) - 1))
            a = ["mousemove", "--sync", pts[0][0], pts[0][1], "mousedown", 1]
            for x, y in pts[1:]:
                a += ["sleep", "%.3f" % step, "mousemove", x, y]
            xdo(*(a + ["sleep", "0.05", "mouseup", 1]))
        elif "key" in st:
            xdo("key", "--clearmodifiers", key(st["key"]))
        elif "hold" in st:
            k = key(st["hold"]); xdo("keydown", k, "sleep", "%.2f" % float(st.get("seconds", 1)), "keyup", k)
        elif "keydown" in st:
            xdo("keydown", key(st["keydown"]))
        elif "keyup" in st:
            xdo("keyup", key(st["keyup"]))
        elif "type" in st:
            xdo("type", "--delay", 15, "--", st["type"])
        elif "wait" in st:
            time.sleep(min(float(st["wait"]), 60))
        elif "shot" in st:
            out["shots"].append({"i": i, "kind": "shot", "label": st["shot"], "expect": st.get("expect", ""),
                                 "t": round(time.time() - t0, 2), "ext": "png", "images": [grab("png")]})
        elif "frames" in st:
            n = max(2, min(int(st["frames"]), 12)); every = max(0.05, float(st.get("every", 0.25))); imgs = []
            for k in range(n):
                imgs.append(grab("jpg"))
                if k + 1 < n:
                    time.sleep(every)
            out["shots"].append({"i": i, "kind": "frames", "label": st.get("label", "連續畫面"),
                                 "expect": st.get("expect", ""), "every": every, "t": round(time.time() - t0, 2),
                                 "ext": "jpg", "images": imgs})
        elif "record" in st:
            secs = max(1, min(float(st["record"]), 30))
            path = "/tmp/pt-clip-%d.mp4" % i
            rec = (subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-framerate", "10",
                                     "-video_size", "%dx%d" % (spec["w"], spec["h"]), "-i", os.environ.get("DISPLAY", ":99"),
                                     "-t", str(secs), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                                     path]), path, i, st.get("label", "錄影"), st.get("expect", ""), secs)
    if rec:
        proc, path, i, label, expect, secs = rec
        proc.wait(timeout=secs + 20)
        with open(path, "rb") as f:
            out["shots"].append({"i": i, "kind": "clip", "label": label, "expect": expect, "ext": "mp4",
                                 "images": [base64.b64encode(f.read()).decode()]})
        os.remove(path)
except Exception as e:
    out["error"] = "%s: %s" % (type(e).__name__, e)
finally:
    subprocess.run(["xdotool", "mouseup", "1"], capture_output=True)
json.dump(out, sys.stdout)
'''

RUBRIC = """You check a live test of something an AI agent built: it was operated with mouse and keyboard on a desktop,
and pictures were taken at chosen moments. For each numbered picture you get what the test expected to see at that
moment. Decide from the picture alone whether the expectation is met.

GOAL OF THE TEST: {goal}

Rules: judge only what is visible; for a series of frames judge the change across them (first to last) - motion,
direction, something appearing or disappearing; a black, blank or frozen screen where something should happen fails;
wrong direction of motion, forces or arrows fails; text you cannot read is not evidence. Be specific about what you see.

Reply with JSON only:
{{"items": [{{"shot": 1, "ok": true|false, "observed": "what the picture shows, briefly", "problem": "if not ok: what is wrong"}}]}}
Write observed and problem in {lang}."""


def duration(steps: list[dict]) -> float:
    total = 0.0
    for st in steps:
        total += float(st.get("wait", 0) or 0) + float(st.get("seconds", 0) or 0) * (1 if ("hold" in st or "drag" in st) else 0)
        if "frames" in st:
            total += float(st.get("every", 0.25)) * int(st["frames"])
        if "record" in st:
            total = max(total, float(st["record"]))
    return total


def validate(steps) -> str | None:
    if not isinstance(steps, list) or not steps:
        return "steps must be a non-empty list"
    if len(steps) > MAX_STEPS:
        return f"at most {MAX_STEPS} steps per playtest"
    known = {"move", "click", "down", "up", "drag", "key", "hold", "keydown", "keyup", "type", "wait", "shot",
             "frames", "record"}
    for i, st in enumerate(steps):
        if not isinstance(st, dict) or not (known & set(st)):
            return f"step {i + 1} has no action ({', '.join(sorted(known))})"
    if not any(("shot" in st or "frames" in st or "record" in st) for st in steps):
        return "add at least one shot / frames / record step: a playtest is judged by its pictures"
    if duration(steps) > MAX_SECONDS:
        return f"the sequence would take longer than {MAX_SECONDS} s: split it into several playtests"
    return None


def parse(text: str) -> list[dict]:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return list(json.loads(m.group(0)).get("items") or []) if m else []
    except (ValueError, AttributeError):
        return []


async def judge(model, goal: str, lang: str, shots: list[dict]) -> list[dict]:
    """One verdict per judged shot (same order); raises when the model cannot answer."""
    from ..images import parts as image_parts
    from ..types import Message, TextPart
    parts: list = [TextPart(RUBRIC.format(goal=goal or "(not given)", lang=lang))]
    for n, s in enumerate(shots, 1):
        imgs = s["images"]
        if s["kind"] == "frames" and len(imgs) > 6:          # first, last and evenly between
            step = (len(imgs) - 1) / 5
            imgs = [imgs[round(k * step)] for k in range(6)]
        head = f"Picture {n}: {s['label']} ({'frames, first to last' if s['kind'] == 'frames' else 'screenshot'})" \
               f"\nExpected: {s['expect']}"
        mime = "image/jpeg" if s["ext"] == "jpg" else "image/png"
        for k, b64 in enumerate(imgs):
            parts += image_parts(base64.b64decode(b64), mime, head if k == 0 else f"Picture {n}, frame {k + 1}")
    resp = await asyncio.wait_for(model.chat([Message("user", parts)], None, temperature=0, max_tokens=1500), 240)
    items = {int(i.get("shot", 0)): i for i in parse(resp.message.text()) if str(i.get("shot", "")).isdigit()}
    return [items.get(n, {}) for n in range(1, len(shots) + 1)]


async def run(ctx, steps: list[dict], url: str | None = None, width: str = "desktop", goal: str = "") -> dict:
    """Play the sequence in the conversation's sandbox; returns {"id", "shots": [evidence entries], "error", ...}."""
    from ..sandbox import executor_of
    from ..tools.desktop import XDO_KEYS
    from . import evidence
    ex = executor_of(ctx)
    if ex.kind != "sandbox":
        raise RuntimeError("playtest runs in the sandbox (Settings → 權限與安全 → 沙盒)")
    ex._taken()
    page = None
    cdp = None
    if url or width == "mobile":
        from ..tools import browser
        page = await browser._page(ctx)
        if url:
            await page.goto(url if "://" in url else "http://" + url, wait_until="domcontentloaded", timeout=45000)
        await page.bring_to_front()
        if width == "mobile":                    # a phone's width (V6); cleared again after the run
            cdp = await page.context.new_cdp_session(page)
            await cdp.send("Emulation.setDeviceMetricsOverride",
                           {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
        await asyncio.sleep(0.8)
    if page is not None:
        try:
            await page.evaluate(evidence.MASK_JS, evidence.secret_snippets(ctx.config))
        except Exception:  # noqa: BLE001
            pass
    in_w, in_h = await asyncio.to_thread(lambda: ex.input().size())
    max_w = int(ctx.config.get("desktop.screenshot_max_width", 1280))
    scale = in_w / min(in_w, max_w)
    spec = {"steps": steps, "scale": scale, "keys": XDO_KEYS, "w": in_w, "h": in_h}
    pid = f"P{int(time.time() * 1000) % 10_000_000}"
    started = time.time()
    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", "-i", "-e", "DISPLAY=:99", ex.manager.name_of(ex.conversation), "python3", "-c", PLAY,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(spec).encode()), duration(steps) + 90)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("the playtest did not finish in time")
    finally:
        if cdp is not None:
            try:
                await cdp.send("Emulation.clearDeviceMetricsOverride")
            except Exception:  # noqa: BLE001
                pass
    try:
        result = json.loads(out or b"{}")
    except ValueError:
        raise RuntimeError("the playtest script failed: " + (err or b"").decode(errors="replace")[-400:])
    if evidence.taken(ctx):                      # you took over during the run: nothing of it is kept (V5)
        return {"id": pid, "shots": [], "error": "the user took over the desktop during the playtest; "
                                                 "its pictures were not kept", "seconds": round(time.time() - started, 1)}
    store = evidence.store_of(ctx)
    turn = ctx.state.get("turn_id")
    kept = []
    for s in result.get("shots") or []:
        blobs = [(base64.b64decode(b), s["ext"]) for b in s["images"]]
        entry = store.add(s["kind"], s["label"], blobs, turn=turn, source="playtest", expect=s.get("expect") or None,
                          playtest=pid, step=s["i"] + 1, width=width, every=s.get("every"))
        kept.append((entry, s))
    judged = [(e, s) for e, s in kept if s.get("expect") and s["kind"] != "clip"][:MAX_JUDGED]
    verdicts: list[dict] = []
    skipped = None
    if judged and ctx.config.get("model.vision", True):
        rt = ctx.state.get("runtime")
        try:
            from . import language
            want = language.wanted(goal) or "zh-Hant"
            verdicts = await judge(rt.judge(), goal, language.NAMES.get(want, want), [s for _, s in judged])
        except Exception as e:  # noqa: BLE001 - recorded as not judged, never as passed
            skipped = f"{type(e).__name__}: {e}"[:200]
    for (entry, _), v in zip(judged, verdicts):
        if "ok" in v:
            entry.update(ok=bool(v.get("ok")), observed=str(v.get("observed", ""))[:300],
                         problem=str(v.get("problem", ""))[:300])
            store.update(entry["id"], ok=entry["ok"], observed=entry["observed"], problem=entry["problem"])
    summary = {"id": pid, "goal": goal, "url": url, "width": width, "shots": [e for e, _ in kept],
               "error": result.get("error"), "judge_skipped": skipped, "seconds": round(time.time() - started, 1)}
    ctx.state.setdefault("playtests", []).append(summary)
    if ctx.emit:
        ctx.emit("evidence", {"playtest": pid, "items": summary["shots"]})
    return summary
