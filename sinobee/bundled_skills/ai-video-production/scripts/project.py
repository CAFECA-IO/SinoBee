#!/usr/bin/env python3
"""Video project: create, lock timing to audio, build prompts, check against the pipeline checklist.

  project.py init <folder> --genre documentary --title "…" [--aspect 16:9] [--language zh-TW] [--intent "…"]
  project.py timing <folder> [--pad 0.2] [--step 0.5]     # audio-first: shot length from its audio file
  project.py prompts <folder>                              # build one H3 prompt per unit -> prompts/<unit>.txt
  project.py validate <folder>                             # checklist; exit 1 when a shot may not be rendered
  project.py plan <folder>                                 # units, timing, frames, render status

All output is JSON. Edit project.json directly (it is the single source of truth) and re-run these commands.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from prompting import unit_prompt
from vpcommon import (ANGLES, CAMERA_MOVES, GENRE_DEFAULTS, GENRES, H3_MAX_FRAMES, H3_FPS, PROFILE_DIMS, SHOT_SIZES,
                      canvas, die, duration, emit, frames_for_seconds, load_project, resolve, save_project,
                      shots_by_id, unit_seconds, unit_state, units)

FOLDERS = ("audio", "keyframes", "refs", "clips", "prompts", "out", "qc")


def cmd_init(a) -> None:
    root = Path(a.folder).expanduser()
    if (root / "project.json").exists() and not a.force:
        die(f"{root}/project.json already exists (use --force to overwrite)")
    if a.genre not in GENRES:
        die(f"genre must be one of {', '.join(GENRES)}")
    g = GENRE_DEFAULTS[a.genre]
    for f in FOLDERS:
        (root / f).mkdir(parents=True, exist_ok=True)
    proj = {
        "title": a.title or root.name,
        "intent": a.intent or "",
        "genre": a.genre,
        "language": a.language,
        "aspect": a.aspect,
        "fps": g["fps"],
        "delivery": "1080p",
        "quality": "draft",
        "style": {"look": g["look"], "palette": [], "lut": None, "camera": g["camera"],
                  "motion_strength": g["motion_strength"], "ambience": "", "music": "", "negative": "",
                  "bgm_file": None, "voice": ""},
        "script": {"format": g["script_format"], "body": ""},
        "subjects": [{"id": "S1", "name": "", "kind": "person", "appearance": "", "wardrobe": "",
                      "capability_ceiling": "", "negative_boundary": "", "linguistic_blueprint": "",
                      "kinematic_signature": "", "voice": "", "sheet": []}],
        "environments": [{"id": "E1", "name": "", "description": "", "light": "", "palette": "", "materials": "",
                          "master": None}],
        "shots": [{"id": "SH01", "group": "SC01", "scene": "", "env": "E1", "subjects": ["S1"], "duration": 5.0,
                   "narration": "", "dialogue": [], "audio_file": None, "shot_size": "medium", "angle": "eye level",
                   "lens": "35mm", "focus": "", "composition": "", "action": "", "performance": "",
                   "camera_move": "static", "sfx": "", "music": "", "first_frame": None, "last_frame": None,
                   "motion_strength": None}],
        "renders": {},
    }
    save_project(proj, root / "project.json")
    emit({"ok": True, "project": str(root / "project.json"), "folders": list(FOLDERS),
          "next": "fill script/subjects/environments/shots in project.json, then tts.py -> project.py timing"})


def cmd_timing(a) -> None:
    proj, path = load_project(a.folder)
    root = path.parent
    changed = []
    for s in proj.get("shots", []):
        f = resolve(root, s.get("audio_file"))
        if not f:
            continue
        if not f.exists():
            die(f"shot {s['id']}: audio_file {f} not found")
        d = duration(f)
        s["audio_duration"] = round(d, 3)
        locked = math.ceil((d + a.pad) / a.step) * a.step
        if s.get("duration") != locked:
            changed.append({"shot": s["id"], "audio": round(d, 2), "duration": locked, "was": s.get("duration")})
        s["duration"] = locked
    save_project(proj, path)
    out = []
    for u in units(proj):
        secs = unit_seconds(proj, u)
        out.append({"unit": u["id"], "shots": u["shots"], "seconds": round(secs, 2),
                    "frames": frames_for_seconds(secs), "too_long": secs > H3_MAX_FRAMES / H3_FPS})
    emit({"ok": True, "changed": changed, "units": out})


def cmd_prompts(a) -> None:
    proj, path = load_project(a.folder)
    root = path.parent
    (root / "prompts").mkdir(exist_ok=True)
    done = []
    for u in units(proj):
        if a.unit and u["id"] != a.unit:
            continue
        st = unit_state(proj, u["id"])
        p = unit_prompt(proj, {**u, "summary": st.get("summary")})
        if st.get("prompt") != p:
            if st.get("status") == "done":
                st["stale"] = True          # rendered from an older prompt; re-render with --force
            st["prompt"] = p
        (root / "prompts" / f"{u['id']}.txt").write_text(p, encoding="utf-8")
        done.append({"unit": u["id"], "file": f"prompts/{u['id']}.txt", "chars": len(p)})
    save_project(proj, path)
    emit({"ok": True, "prompts": done})


def check(proj: dict, root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warns: list[str] = []
    subj = {s["id"]: s for s in proj.get("subjects", [])}
    envs = {e["id"]: e for e in proj.get("environments", [])}
    genre = proj.get("genre", "film")
    style = proj.get("style", {})

    # Phase 1 - script & audio-first timing
    if not proj.get("shots"):
        errors.append("[腳本] no shots")
    for s in proj.get("shots", []):
        sid = s["id"]
        if not s.get("duration") or float(s["duration"]) <= 0:
            errors.append(f"[腳本] {sid}: no duration")
        spoken = bool(s.get("narration") or s.get("dialogue"))
        if spoken and not s.get("audio_file"):
            warns.append(f"[腳本] {sid}: has narration/dialogue but no audio_file - duration is a guess "
                         "(run tts.py, then project.py timing)")
        if s.get("audio_file"):
            f = resolve(root, s["audio_file"])
            if not f or not f.exists():
                errors.append(f"[腳本] {sid}: audio_file {s['audio_file']} missing")
            elif s.get("duration") and duration(f) > float(s["duration"]) + 0.05:
                errors.append(f"[腳本] {sid}: audio ({duration(f):.2f}s) longer than the shot ({s['duration']}s) "
                              "- run project.py timing")
    for u in units(proj):
        secs = unit_seconds(proj, u)
        if secs > H3_MAX_FRAMES / H3_FPS + 0.01:
            errors.append(f"[腳本] unit {u['id']}: {secs:.1f}s exceeds H3's ~15s per generation - split the group")
    if not (proj.get("script") or {}).get("body"):
        warns.append("[腳本] script.body is empty - write the script in the genre's format for the user to review")

    # Phase 2 - profiling matrix
    for sid, s in subj.items():
        if not s.get("appearance"):
            errors.append(f"[側寫] subject {sid}: appearance is empty")
        missing = [d for d in PROFILE_DIMS if not str(s.get(d) or "").strip()]
        if missing:
            errors.append(f"[側寫] subject {sid}: profiling matrix incomplete ({', '.join(missing)})")

    # Phase 3 - asset anchors
    appear: dict[str, set] = {}
    for s in proj.get("shots", []):
        for i in s.get("subjects", []):
            appear.setdefault(i, set()).add(s.get("group") or s["id"])
            if i not in subj:
                errors.append(f"[資產] {s['id']}: unknown subject {i}")
        if s.get("env") and s["env"] not in envs:
            errors.append(f"[資產] {s['id']}: unknown environment {s['env']}")
        if not s.get("env"):
            warns.append(f"[資產] {s['id']}: no environment - lighting may jump between shots")
    for eid, e in envs.items():
        if not e.get("description") or not e.get("light"):
            errors.append(f"[資產] environment {eid}: needs description and light (key light direction, time of day)")
        if e.get("master") and not resolve(root, e["master"]).exists():
            errors.append(f"[資產] environment {eid}: master {e['master']} missing")
    for sid, groups in appear.items():
        sheet = (subj.get(sid) or {}).get("sheet") or []
        for f in sheet:
            if not resolve(root, f).exists():
                errors.append(f"[資產] subject {sid}: sheet image {f} missing")
        if len(groups) > 1 and not sheet:
            warns.append(f"[資產] subject {sid} appears in {len(groups)} units but has no subject sheet - "
                         "make one (comfy_h3.py sheet) and start units from keyframes for consistency")
    if not style.get("palette"):
        warns.append("[資產] style.palette is empty - set a palette so color stays consistent across shots")

    # Phase 4 - shot grammar
    for s in proj.get("shots", []):
        sid = s["id"]
        if not s.get("shot_size"):
            errors.append(f"[構圖] {sid}: shot_size missing")
        elif s["shot_size"].lower() not in SHOT_SIZES:
            warns.append(f"[構圖] {sid}: shot_size '{s['shot_size']}' is not standard ({', '.join(SHOT_SIZES)})")
        if not s.get("angle"):
            errors.append(f"[構圖] {sid}: angle missing")
        elif s["angle"].lower() not in ANGLES:
            warns.append(f"[構圖] {sid}: angle '{s['angle']}' is not standard ({', '.join(ANGLES)})")
        if not s.get("lens"):
            warns.append(f"[構圖] {sid}: lens missing (e.g. 24mm wide, 50mm, 85mm portrait)")
        if not s.get("action"):
            errors.append(f"[構圖] {sid}: action missing")
        for k in ("first_frame", "last_frame"):
            if s.get(k) and not resolve(root, s[k]).exists():
                errors.append(f"[構圖] {sid}: {k} {s[k]} missing")

    # Phase 5 - dynamics
    band = {"documentary": 0.4, "vlog": 0.4, "explainer": 0.4}.get(genre)
    for s in proj.get("shots", []):
        m = s.get("motion_strength") if s.get("motion_strength") is not None else style.get("motion_strength")
        if band and m is not None and float(m) > band:
            warns.append(f"[動態] {s['id']}: motion_strength {m} is high for {genre} (keep 0.2-0.4)")
        cam = (s.get("camera_move") or "").lower()
        if not cam:
            warns.append(f"[動態] {s['id']}: camera_move missing")
        elif not any(c in cam for c in CAMERA_MOVES):
            warns.append(f"[動態] {s['id']}: camera_move '{cam}' uses no standard move ({', '.join(CAMERA_MOVES)})")
    return errors, warns


def cmd_validate(a) -> None:
    proj, path = load_project(a.folder)
    errors, warns = check(proj, path.parent)
    emit({"ok": not errors, "errors": errors, "warnings": warns})
    sys.exit(0 if not errors else 1)


def cmd_plan(a) -> None:
    proj, path = load_project(a.folder)
    w, h = canvas(proj.get("aspect", "16:9"), 1.0)
    sb = shots_by_id(proj)
    rows, total = [], 0.0
    for u in units(proj):
        secs = unit_seconds(proj, u)
        total += secs
        st = proj.get("renders", {}).get(u["id"], {})
        first = sb[u["shots"][0]]
        rows.append({"unit": u["id"], "shots": u["shots"], "seconds": round(secs, 2),
                     "frames": frames_for_seconds(secs), "first_frame": first.get("first_frame"),
                     "status": st.get("status", "pending"), "clip": st.get("clip"), "seed": st.get("seed")})
    emit({"ok": True, "title": proj.get("title"), "genre": proj.get("genre"), "aspect": proj.get("aspect"),
          "canvas_final": [w, h], "total_seconds": round(total, 2), "units": rows})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("folder")
    i.add_argument("--genre", default="film")
    i.add_argument("--title")
    i.add_argument("--aspect", default="16:9")
    i.add_argument("--language", default="zh-TW")
    i.add_argument("--intent")
    i.add_argument("--force", action="store_true")
    t = sub.add_parser("timing")
    t.add_argument("folder")
    t.add_argument("--pad", type=float, default=0.2, help="seconds of air after the audio")
    t.add_argument("--step", type=float, default=0.5, help="round shot length up to this step")
    pr = sub.add_parser("prompts")
    pr.add_argument("folder")
    pr.add_argument("--unit")
    for n in ("validate", "plan"):
        sub.add_parser(n).add_argument("folder")
    a = p.parse_args()
    {"init": cmd_init, "timing": cmd_timing, "prompts": cmd_prompts, "validate": cmd_validate,
     "plan": cmd_plan}[a.cmd](a)


if __name__ == "__main__":
    main()
