#!/usr/bin/env python3
"""Tell the agent exactly what to do next for a video project (run it after every step).

  next.py <folder>

Prints JSON: the current phase, what is missing, and `call` - the exact skill_run call to make next (or the
fields to fill). Follow it until `done` is true; then deliver out/final.mp4.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

from project import check
from prompting import unit_prompt
from vpcommon import (PROJECT_FILE, emit, load_project, resolve, shots_by_id, strip_script_prefix, units)

SKILL = "ai-video-production"


def call(script: str, args: str, timeout: int | None = None) -> dict:
    c = {"tool": "skill_run", "name": SKILL, "script": f"scripts/{script}", "args": args}
    if timeout:
        c["timeout"] = timeout
    return c


def reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/system_stats", timeout=4):
            return True
    except Exception:  # noqa: BLE001
        return False


def newer(a: Path, b: Path) -> bool:
    return a.exists() and b.exists() and a.stat().st_mtime >= b.stat().st_mtime


def out(phase: str, what: str, nxt: dict | None = None, **extra) -> None:
    emit({"ok": True, "done": False, "phase": phase, "what": what, "call": nxt, **extra,
          "after": "run next.py again"})
    sys.exit(0)


def main() -> None:
    strip_script_prefix()
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    folder = sys.argv[1]
    if not (Path(folder) / PROJECT_FILE).exists() and not folder.endswith(".json"):
        out("0 brief", "no project yet - create it (pick genre/aspect/language from the user's intent)",
            call("project.py", f'init {folder} --genre film --title "<title>" --aspect 16:9 --language zh-TW '
                               f'--intent "<the user\'s words>"'))
    proj, path = load_project(folder)
    root = path.parent
    F = folder
    errors, warns = check(proj, root)

    # Phases 1-4: content. Everything goes into project.json fields.
    audio_err = [e for e in errors if "audio" in e and "longer than the shot" in e]
    content = [e for e in errors if e not in audio_err]
    if content:
        out("1-4 plan: fill project.json",
            "fill these fields in project.json (not in separate files). Fastest: write ONE file "
            f"{F}/plan.json shaped like project.json (subjects, environments, style, script.body, shots - see "
            "assets/example-documentary.json via skill_view path=assets/example-documentary.json) and merge it; "
            "small fixes: project.py set <folder> <field.path> <value>",
            call("project.py", f"merge {F} plan.json"), missing=content[:25],
            alt=call("project.py", f'set {F} subjects.S1.appearance "…" subjects.S1.capability_ceiling "…"'),
            reference="references/project-schema.md, references/profiling-matrix.md, references/shot-grammar.md")

    # Phase 1: audio-first timing
    sb = shots_by_id(proj)
    spoken = [s["id"] for s in proj.get("shots", []) if (s.get("narration") or s.get("dialogue"))
              and not s.get("audio_file")]
    if spoken and not proj.get("skip_tts"):
        out("1 audio-first", f"shots {', '.join(spoken)} have narration/dialogue but no audio yet. If tts.py says "
            "no engine is installed, run `project.py set " + F + " skip_tts true` and keep the durations you chose",
            call("tts.py", F, 600))
    if audio_err:
        out("1 audio-first", "shot lengths are shorter than their audio - lock them to the audio",
            call("project.py", f"timing {F}"), errors=audio_err)

    # Phase 5 prompts
    stale = [u["id"] for u in units(proj)
             if proj.get("renders", {}).get(u["id"], {}).get("prompt")
             != unit_prompt(proj, {**u, "summary": proj.get("renders", {}).get(u["id"], {}).get("summary")})]
    if stale:
        out("5 prompts", "build the H3 prompts from project.json (one per unit)", call("project.py", f"prompts {F}"),
            units=stale)

    # renderer
    from comfy_h3 import url_for
    url = url_for(proj, None)
    if not reachable(url):
        out("blocked: renderer", f"ComfyUI is not reachable at {url}. Tell the user to start ComfyUI on the GPU "
            "server and open the tunnel (ssh -N -L 8188:127.0.0.1:8188 <user>@<server>), or set COMFYUI_URL; "
            "then call again. Do not continue without it.", None)

    # Phase 3 anchors for recurring subjects / locations
    groups: dict[str, set] = {}
    envs_used: dict[str, set] = {}
    for u in units(proj):
        for sid in u["shots"]:
            for i in sb[sid].get("subjects", []):
                groups.setdefault(i, set()).add(u["id"])
            if sb[sid].get("env"):
                envs_used.setdefault(sb[sid]["env"], set()).add(u["id"])
    for s in proj.get("subjects", []):
        if len(groups.get(s["id"], ())) > 1 and not s.get("sheet") and not proj.get("skip_sheets"):
            out("3 assets", f"subject {s['id']} recurs in several units - build its reference sheet, then LOOK at "
                "each image with read_file (regenerate with another --seed if it does not match the profile)",
                call("comfy_h3.py", f"sheet {F} --subject {s['id']}", 1800))
    for e in proj.get("environments", []):
        if len(envs_used.get(e["id"], ())) > 1 and not e.get("master") and not proj.get("skip_sheets"):
            out("3 assets", f"location {e['id']} recurs - build its empty establishing still, then look at it",
                call("comfy_h3.py", f"master {F} --env {e['id']}", 1800))

    # Phase 5 render
    renders = proj.get("renders", {})
    queued = [u for u, st in renders.items() if st.get("status") == "queued"]
    if queued:
        out("5 render", "renders are queued in ComfyUI - download finished ones (repeat until none is left)",
            call("comfy_h3.py", f"collect {F}", 120), queued=queued)
    todo = [u["id"] for u in units(proj)
            if renders.get(u["id"], {}).get("status") != "done" or renders.get(u["id"], {}).get("stale")]
    if todo:
        q = "draft" if not any(st.get("quality") in ("final", "best") for st in renders.values()) else "final"
        out("5 render", f"render {len(todo)} unit(s) at {q} quality (several minutes each). If the tool times "
            "out, the job keeps running: run next.py again later", call("comfy_h3.py", f"render {F} --all --quality {q}",
                                                                         3600 * max(1, len(todo) // 3 + 1)),
            units=todo)

    # QC every clip
    for u in units(proj):
        clip = resolve(root, renders[u["id"]].get("clip"))
        sheet = root / "qc" / f"{u['id']}.jpg"
        if not newer(sheet, clip):
            out("5 QC", "build contact sheets, then read_file EVERY qc/<unit>.jpg and check it against "
                "references/checklist.md. For a bad unit: fix its shot fields, then "
                f"`comfy_h3.py render {F} --unit <id> --force` and run next.py again", call("qc.py", f"project {F}"))

    # draft -> final
    target = proj.get("target_quality", "final")
    if target != "draft" and any(st.get("quality") == "draft" for st in renders.values()):
        out("5 final render", "drafts passed QC - render the final quality (or `project.py set "
            f"{F} target_quality draft` to deliver the drafts)",
            call("comfy_h3.py", f"render {F} --all --quality final --force", 3600 * max(1, len(renders) // 2 + 1)))

    # Phase 6
    final = root / "out" / "final.mp4"
    newest = max((resolve(root, st.get("clip")).stat().st_mtime for st in renders.values()
                  if resolve(root, st.get("clip")) and resolve(root, st.get("clip")).exists()), default=0)
    if not final.exists() or final.stat().st_mtime < newest:
        grade = {"documentary": "none", "vlog": "none"}.get(proj.get("genre"), "film")
        out("6 assemble", "assemble the film on the timeline (add --bgm <file> if the user gave music, "
            "--subs burn for social video)", call("assemble.py", f"{F} --grade {grade}", 1800))
    qcf = root / "qc" / "final.jpg"
    if not newer(qcf, final):
        out("6 QC", "check the final film: read_file qc/final.jpg, confirm duration and loudness",
            call("qc.py", f"project {F}"))
    emit({"ok": True, "done": True, "phase": "deliver", "film": str(final), "subtitles": str(final.with_suffix(".srt")),
          "what": "deliver out/final.mp4 to the user with a two-line summary (what it shows, length, how to change it)",
          "warnings": warns[:10]})


if __name__ == "__main__":
    main()
