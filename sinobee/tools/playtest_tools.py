"""playtest: operate a page, game or app in the sandbox like a person and keep judged pictures (sandbox plan V1)."""
from __future__ import annotations

import base64

from ..types import ImagePart, TextPart
from .registry import ToolContext, ToolResult, tool


def available(config=None) -> bool:
    if config is None:
        return False
    from ..sandbox import executor_kind
    return executor_kind(config) == "sandbox"


@tool("playtest", "desktop",
      "Try what you built the way a user would and keep the pictures as evidence. Runs a whole sequence of mouse "
      "and keyboard steps on the sandbox desktop in one go, with real timing (good for games and anything "
      "real-time), taking pictures where you say. Each picture with `expect` is compared with that expectation by "
      "a vision model; you get the verdicts and evidence ids (E…) back - cite them in the checklist. "
      "Steps (coordinates in screenshot space, take a screenshot first): {\"click\":[x,y]} {\"move\":[x,y]} "
      "{\"down\":[x,y]} {\"up\":true} {\"drag\":[[x,y],...],\"seconds\":1} {\"key\":\"enter\"} "
      "{\"hold\":\"w\",\"seconds\":2} {\"keydown\":\"w\"} {\"keyup\":\"w\"} {\"type\":\"text\"} {\"wait\":0.5} "
      "{\"shot\":\"label\",\"expect\":\"what must be visible\"} "
      "{\"frames\":6,\"every\":0.2,\"label\":\"...\",\"expect\":\"the motion you expect\"} "
      "{\"record\":5,\"label\":\"...\"} (a video clip of the next seconds). "
      "url: open this page in the sandbox browser first; width: \"mobile\" tries it at a phone's width (390 px).",
      {"properties": {
          "steps": {"type": "array", "items": {"type": "object"}},
          "goal": {"type": "string", "description": "what this test should show, in the user's words"},
          "url": {"type": "string"},
          "width": {"type": "string", "enum": ["desktop", "mobile"]},
      }, "required": ["steps", "goal"]}, writes=True, check=available)
async def playtest(ctx: ToolContext, steps: list, goal: str = "", url: str | None = None, width: str = "desktop"):
    from ..agent import playtest as pt
    bad = pt.validate(steps)
    if bad:
        return ToolResult.error(bad)
    try:
        res = await pt.run(ctx, steps, url=url, width=width or "desktop", goal=goal)
    except RuntimeError as e:
        return ToolResult.error(str(e))
    lines = [f"playtest {res['id']} ({res['seconds']} s, {'phone width' if width == 'mobile' else 'desktop'}):"]
    if res.get("error"):
        lines.append(f"! {res['error']}")
    failed = []
    for e in res["shots"]:
        mark = "✓" if e.get("ok") is True else "✕" if e.get("ok") is False else "·"
        what = e.get("problem") if e.get("ok") is False else e.get("observed") or ""
        kind = {"frames": f"{len(e['files'])} frames", "clip": "video"}.get(e["kind"], "shot")
        lines.append(f"{mark} {e['id']} [{kind}] {e['label']}" + (f" — expected: {e['expect']}" if e.get("expect") else "")
                     + (f" — {what}" if what else ""))
        if e.get("ok") is False:
            failed.append(e)
    if res.get("judge_skipped"):
        lines.append(f"(the pictures were not judged: {res['judge_skipped']} - look at them yourself)")
    if failed:
        lines.append("Fix what failed, then play again. Cite only passing evidence ids in the checklist.")
    elif res["shots"]:
        lines.append("Cite these evidence ids (e.g. E3) in the checklist items they prove.")
    out: list = [TextPart("\n".join(lines))]
    from ..agent.evidence import store_of
    store = store_of(ctx)
    for e in (failed or [x for x in res["shots"] if x["kind"] == "shot"][-1:])[:3]:
        name = e["files"][-1]
        p = store.file(name)
        if p and not name.endswith(".mp4") and ctx.config.get("model.vision", True):
            mime = "image/jpeg" if name.endswith(".jpg") else "image/png"
            out += [TextPart(f"{e['id']} {e['label']}"), ImagePart(base64.b64encode(p.read_bytes()).decode(), mime)]
    return out
