#!/usr/bin/env python3
"""Autopilot - the skill's entry point. Runs every mechanical step of the pipeline by itself.

  run.py <folder or folder/project.json> [--budget 240] [--draft-only]

What you (the agent) do:
  1. Write <folder>/plan.json: the film's content in the shape of project.json
     (script.body, subjects, environments, style, shots - copy assets/example-film-duel.json).
  2. Call this script with the folder. Call it again with the same folder whenever it answers
     status "working" (renders take minutes) until status is "done".

What it does on each call, as far as its time budget allows: create/merge the project, narration audio
(TTS) and audio-locked timing, prompts, reference sheets, draft renders, QC (one automatic retake for broken
clips), final renders, assembly with mixing/subtitles, final QC.

Answers (JSON, field `status`):
  needs_content - fields are missing: fix plan.json (see `missing`), call again
  working       - renders are running on the GPU server: call again (same args) in a minute or two
  blocked       - the renderer is unreachable: tell the user what to start (see `what`)
  ready_for_final - (only with --until final) drafts are done; `preview` is a draft film; approve, then call
                  again without --until
  done          - `film` is the finished video: deliver it, and look at `review` images if you can
  error         - a step failed: read `error`, fix the cause (usually project.json content), call again
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from vpcommon import emit, strip_script_prefix  # noqa: E402

STEP_SECONDS = {"tts.py": 60, "project.py": 5, "qc.py": 40, "assemble.py": 240, "sheet": 120, "master": 60,
                "keyframe": 60, "render": 10, "collect": 10}


def script(name: str, *args: str, timeout: float = 1800) -> tuple[int, dict]:
    r = subprocess.run([sys.executable, str(HERE / name), *args], capture_output=True, text=True, timeout=timeout)
    try:
        out = json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        out = {"ok": False, "error": (r.stdout + r.stderr)[-1500:]}
    if r.returncode and "error" not in out:
        out["error"] = (r.stderr or r.stdout)[-1500:]
    return r.returncode, out


def finish(status: str, folder: str, log: list, **extra) -> None:
    again = {"tool": "skill_run", "name": "ai-video-production", "script": "scripts/run.py", "args": folder}
    msg = {"status": status, "folder": folder, "steps_done": log, **extra}
    if status in ("working", "needs_content", "error"):
        msg["call_again"] = again
    emit(msg)
    sys.exit(0 if status in ("done", "working", "needs_content", "ready_for_final") else 1)


def resolve_folder(arg: str) -> Path:
    p = Path(arg).expanduser()
    if p.name in ("project.json", "plan.json"):
        p = p.parent
    return p


def main() -> None:
    strip_script_prefix()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folder", nargs="?")
    p.add_argument("--budget", type=float, default=240, help="seconds to work before answering (fits skill_run)")
    p.add_argument("--draft-only", action="store_true", help="deliver draft quality (faster)")
    p.add_argument("--until", choices=["final"], help="stop before the final-quality render (status ready_for_final, "
                                                     "with a draft preview film) so a person can approve the cost")
    a = p.parse_args()
    if not a.folder:
        print(__doc__)
        return
    root = resolve_folder(a.folder)
    F = str(root)
    deadline = time.time() + a.budget
    log: list[str] = []

    def left() -> float:
        return deadline - time.time()

    proj_file, plan_file = root / "project.json", root / "plan.json"
    if not proj_file.exists():
        if not plan_file.exists():
            finish("needs_content", F, log, what=(
                f"Create {F}/plan.json with the film's content (shape: assets/example-film-duel.json - title, "
                "genre, aspect, language, style, script.body, subjects with the 4 profiling fields, environments, "
                "shots), then call this again with the same folder."), missing=["plan.json"])
        try:
            plan = json.loads(plan_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            finish("needs_content", F, log, what=f"plan.json is not valid JSON: {e}. Fix it and call again.",
                   missing=["plan.json (invalid JSON)"])
        args = ["init", F, "--genre", str(plan.get("genre") or "film"), "--aspect", str(plan.get("aspect") or "16:9"),
                "--language", str(plan.get("language") or "zh-TW")]
        if plan.get("title"):
            args += ["--title", str(plan["title"])]
        code, out = script("project.py", *args)
        if code:
            finish("error", F, log, error=out.get("error"))
        log.append("init")
    proj = json.loads(proj_file.read_text(encoding="utf-8"))
    if plan_file.exists() and plan_file.stat().st_mtime > float(proj.get("plan_merged_at") or 0):
        try:
            plan = json.loads(plan_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            finish("needs_content", F, log, what=f"plan.json is not valid JSON: {e}. Fix it and call again.",
                   missing=["plan.json (invalid JSON)"])
        code, out = script("project.py", "merge", F, str(plan_file))
        if code:
            finish("error", F, log, error=out.get("error"))
        drop_placeholders(F, proj_file, plan)
        if (root / "kit.lock.json").exists():      # a production kit: keep its characters, places and style
            _, lk = script("kit.py", "lock", F)
            if lk.get("notes"):
                log.append("kit lock: " + "; ".join(lk["notes"][:8]))
        script("project.py", "set", F, "plan_merged_at", str(time.time()))
        log.append("merge plan.json" + (" (notes: " + "; ".join(out.get("notes", [])) + ")" if out.get("notes") else ""))
    if a.draft_only:
        script("project.py", "set", F, "target_quality", "draft")

    while True:
        code, n = script("next.py", F, timeout=120)
        if code or not n:
            finish("error", F, log, error=n.get("error") or "next.py failed")
        if n.get("done"):
            qc = root / "qc"
            kit = export_kit(root, log)
            finish("done", F, log, film=n.get("film"), subtitles=n.get("subtitles"), kit=kit,
                   review=sorted(str(x) for x in qc.glob("*.jpg")) if qc.exists() else [],
                   what="Deliver the film AND the production kit (kit: zip with script, characters, places, style, "
                        "music, voices, shot list and reference images, for the next film) to the user. Look at the "
                        "review images (read_file) to describe it; "
                        "to change a shot, edit plan.json and call again.", warnings=n.get("warnings"))
        phase, call = n.get("phase", ""), n.get("call") or {}
        if a.until == "final" and phase.startswith("5 final render"):
            preview = root / "out" / "draft.mp4"
            newest = max((p.stat().st_mtime for p in (root / "clips").glob("*.mp4")), default=0)
            if not preview.exists() or preview.stat().st_mtime < newest:
                code, out = script("assemble.py", F, "--out", "out/draft.mp4", "--delivery", "720p", "--subs", "burn",
                                   timeout=1800)
                if code:   # burned subtitles need libass; a preview without them is fine
                    code, out = script("assemble.py", F, "--out", "out/draft.mp4", "--delivery", "720p", timeout=1800)
                if code:
                    finish("error", F, log, step="draft preview", error=out.get("error"))
                log.append("draft preview out/draft.mp4")
            proj = json.loads(proj_file.read_text(encoding="utf-8"))
            units = [u for u, st in proj.get("renders", {}).items() if st.get("status") == "done"]
            secs = sum(float(s.get("duration") or 0) for s in proj.get("shots", []))
            qc = root / "qc"
            finish("ready_for_final", F, log, preview=str(preview), units=len(units), seconds=round(secs, 1),
                   estimate_minutes=max(5, 10 * len(units)),
                   review=sorted(str(x) for x in qc.glob("*.jpg")) if qc.exists() else [],
                   what="Drafts are done and QC'd. Rendering the final quality takes about "
                        f"{max(5, 10 * len(units))} GPU minutes; call again without --until to continue.")
        if phase.startswith("1-4"):
            finish("needs_content", F, log, missing=n.get("missing"),
                   what=f"Fill these fields in {F}/plan.json (same shape as project.json; ids like S1/E1/SH01 "
                        "update existing items), then call again. Do not write other files.")
        if phase.startswith("blocked"):
            finish("blocked", F, log, what=n.get("what"))
        args = call.get("args", "")
        name = call.get("script", "").split("/")[-1]
        parts = _split(args)
        sub = parts[0] if parts else ""
        est = STEP_SECONDS.get(sub, STEP_SECONDS.get(name, 30))
        if left() < est:
            finish("working", F, log, what=f"next step: {name} {args}. Call again to continue.")
        if name == "tts.py":
            code, out = script("tts.py", *parts, timeout=600)
            if code and "no TTS engine" in str(out.get("error")):
                script("project.py", "set", F, "skip_tts", "true")
                log.append("tts: no engine - keeping planned durations")
                continue
            if code:
                finish("error", F, log, error=out.get("error"))
            log.append(f"tts ({len(out.get('files', []))} files)")
            continue
        if name == "comfy_h3.py" and sub == "sheet":
            parts += ["--views", "front,three-quarter"]
        if name == "comfy_h3.py" and sub == "render":
            parts += ["--no-wait"]
        if name == "comfy_h3.py" and sub == "collect":
            code, out = script(name, *parts, timeout=300)
            if code:
                finish("error", F, log, error=out.get("error"))
            if out.get("downloaded"):
                log.append("downloaded " + ", ".join(d["unit"] for d in out["downloaded"]))
            if out.get("still_rendering"):
                if left() < 40:
                    finish("working", F, log, rendering=out["still_rendering"],
                           what="units are rendering on the GPU server (minutes each). Call again with the same "
                                "folder to continue.")
                time.sleep(20)
            continue
        code, out = script(name, *parts, timeout=max(60, left() + 600))
        if code:
            finish("error", F, log, step=f"{name} {args}", error=out.get("error") or out.get("errors"))
        log.append(f"{name} {sub}".strip())
        if name == "qc.py":   # one automatic retake for mechanically broken clips (frozen, black, too short)
            renders = json.loads(proj_file.read_text(encoding="utf-8")).get("renders", {})
            for u in out.get("units", []):
                if u.get("flags") and not renders.get(u["unit"], {}).get("auto_retakes"):
                    script("project.py", "set", F, f"renders.{u['unit']}.auto_retakes", "1")
                    script("comfy_h3.py", "render", F, "--unit", u["unit"], "--force", "--no-wait")
                    log.append(f"auto-retake {u['unit']} ({'; '.join(u['flags'])})")


def export_kit(root: Path, log: list) -> str | None:
    """The production kit (script, characters, places, style, music, voices, shot language, reference images) is
    exported next to the film so the next video can reuse it. Rebuilt when the project changed."""
    proj_file = root / "project.json"
    existing = sorted((root / "out").glob("*-kit.zip"), key=lambda p: p.stat().st_mtime)
    if existing and existing[-1].stat().st_mtime >= proj_file.stat().st_mtime:
        return str(existing[-1])
    code, out = script("kit.py", "export", str(root), timeout=600)
    if code or not out.get("kit"):
        log.append(f"kit export failed: {str(out.get('error'))[:200]}")
        return None
    log.append("production kit " + Path(out["kit"]).name)
    return out["kit"]


def drop_placeholders(F: str, proj_file: Path, plan: dict) -> None:
    """Remove the empty S1/E1/SH01 that `init` created when the plan uses other ids."""
    proj = json.loads(proj_file.read_text(encoding="utf-8"))
    plan_ids = {k: {str(x.get("id")) for x in (plan.get(k) or []) if isinstance(x, dict)}
                for k in ("subjects", "environments", "shots")}
    for s in proj.get("shots", []):
        if s["id"] not in plan_ids["shots"] and not s.get("action") and not s.get("narration"):
            script("project.py", "remove", F, f"shots.{s['id']}")
    proj = json.loads(proj_file.read_text(encoding="utf-8"))
    used_s = {i for s in proj.get("shots", []) for i in s.get("subjects", [])}
    used_e = {s.get("env") for s in proj.get("shots", [])}
    for s in proj.get("subjects", []):
        if s["id"] not in used_s and s["id"] not in plan_ids["subjects"] and not s.get("appearance"):
            script("project.py", "remove", F, f"subjects.{s['id']}")
    for e in proj.get("environments", []):
        if e["id"] not in used_e and e["id"] not in plan_ids["environments"] and not e.get("description"):
            script("project.py", "remove", F, f"environments.{e['id']}")


def _split(args: str) -> list[str]:
    import shlex
    return shlex.split(args)


if __name__ == "__main__":
    main()
