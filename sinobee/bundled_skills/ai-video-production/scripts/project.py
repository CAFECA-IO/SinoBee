#!/usr/bin/env python3
"""Video project: create, lock timing to audio, build prompts, check against the pipeline checklist.

  project.py init <folder> --genre documentary --title "…" [--aspect 16:9] [--language zh-TW] [--intent "…"]
  project.py set <folder> subjects.S1.name 凌風 subjects.S1.appearance "…" shots.SH02.duration 3.5
  project.py merge <folder> plan.json                      # bulk-fill: a JSON file shaped like project.json
  project.py remove <folder> subjects.S1                   # drop a placeholder subject/environment/shot
  project.py timing <folder> [--pad 0.2] [--step 0.5]     # audio-first: shot length from its audio file
  project.py prompts <folder>                              # build one H3 prompt per unit -> prompts/<unit>.txt
  project.py validate <folder>                             # checklist; exit 1 when a shot may not be rendered
  project.py plan <folder>                                 # units, timing, frames, render status

All content lives in project.json (never in side files). All output is JSON. next.py <folder> says what to do next.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from prompting import unit_prompt
from vpcommon import (ANGLES, CAMERA_MOVES, GENRE_DEFAULTS, GENRES, H3_MAX_FRAMES, H3_FPS, PROFILE_DIMS, SHOT_SIZES,
                      canvas, die, duration, emit, frames_for_seconds, load_project, normalize_argv, resolve, save_project,
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
        "target_quality": "final",
        "style": {"look": g["look"], "palette": [], "lut": None, "camera": g["camera"],
                  "motion_strength": g["motion_strength"], "ambience": "", "music": "", "negative": "",
                  "bgm_file": None, "voice": ""},
        "script": {"format": g["script_format"], "body": ""},
        "subjects": [{"id": "S1", "name": "", "alias": "", "kind": "person", "appearance": "", "wardrobe": "",
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


TEMPLATES = {
    "subjects": {"name": "", "alias": "", "kind": "person", "appearance": "", "wardrobe": "", "capability_ceiling": "",
                 "negative_boundary": "", "linguistic_blueprint": "", "kinematic_signature": "", "voice": "",
                 "sheet": [], "sheet_seed": None},
    "environments": {"name": "", "description": "", "light": "", "palette": "", "materials": "", "master": None,
                     "master_seed": None},
    "shots": {"group": None, "scene": "", "env": None, "subjects": [], "duration": 3.0, "narration": "",
              "dialogue": [], "audio_file": None, "shot_size": "medium", "angle": "eye level", "lens": "50mm",
              "focus": "", "composition": "", "action": "", "performance": "", "camera_move": "static", "sfx": "",
              "music": "", "first_frame": None, "last_frame": None, "motion_strength": None},
}
ID_PREFIX = {"subjects": "S", "environments": "E", "shots": "SH"}
ALIASES = {"characters": "subjects", "character": "subjects", "cast": "subjects", "roles": "subjects",
           "locations": "environments", "location": "environments", "setting": "environments",
           "settings": "environments", "scenes": "shots", "shot_list": "shots", "storyboard": "shots",
           "palette": "style.palette", "look": "style.look", "script_body": "script.body"}


def new_item(kind: str, iid: str) -> dict:
    return {"id": iid, **json.loads(json.dumps(TEMPLATES[kind]))}


def parse_value(v: str):
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return v


def set_path(proj: dict, dotted: str, value) -> str:
    """subjects.S2.appearance / shots.SH03.duration / style.palette / script.body; list items by id (or index),
    created from a template when the id is new."""
    dotted = ALIASES.get(dotted.split(".")[0], dotted.split(".")[0]) + dotted[len(dotted.split(".")[0]):]
    keys = dotted.split(".")
    cur = proj
    for n, k in enumerate(keys[:-1]):
        nxt = keys[n + 1]
        if isinstance(cur, list):
            item = next((x for x in cur if isinstance(x, dict) and x.get("id") == k), None)
            if item is None and k.isdigit() and int(k) < len(cur):
                item = cur[int(k)]
            if item is None:
                kind = keys[n - 1] if n else ""
                if kind not in TEMPLATES:
                    die(f"no item {k} in {'.'.join(keys[:n])}")
                item = new_item(kind, k)
                cur.append(item)
            cur = item
        else:
            if k not in cur or cur[k] is None:
                cur[k] = [] if k in TEMPLATES else {}
            cur = cur[k]
        if isinstance(cur, (str, int, float)) and nxt:
            die(f"{'.'.join(keys[:n + 1])} is a value, not an object")
    last = keys[-1]
    if isinstance(cur, list):
        die(f"{dotted}: address a list item by id, e.g. subjects.S1.name")
    cur[last] = value
    return dotted


def merge_items(dst: list, src: list, kind: str, notes: list) -> None:
    for i, item in enumerate(src):
        if not isinstance(item, dict):
            notes.append(f"{kind}[{i}] is not an object - skipped")
            continue
        n = len(dst) + 1
        iid = str(item.get("id") or (f"SH{n:02d}" if kind == "shots" else f"{ID_PREFIX[kind]}{n}"))
        cur = next((x for x in dst if x.get("id") == iid), None)
        if cur is None:
            cur = new_item(kind, iid)
            dst.append(cur)
        unknown = [k for k in item if k not in TEMPLATES[kind] and k != "id"]
        if unknown:
            notes.append(f"{kind}.{iid}: kept unknown fields {', '.join(unknown)} (the pipeline ignores them)")
        cur.update({k: v for k, v in item.items() if k != "id"})


def cmd_set(a) -> None:
    proj, path = load_project(a.folder)
    if len(a.pairs) % 2:
        die("set needs pairs: <field.path> <value> [<field.path> <value> …]")
    done = []
    for k, v in zip(a.pairs[::2], a.pairs[1::2]):
        if v.startswith("@"):
            v = Path(v[1:]).read_text(encoding="utf-8")
        done.append(set_path(proj, k, parse_value(v)))
    save_project(proj, path)
    emit({"ok": True, "set": done, "next": "next.py " + a.folder})


def cmd_merge(a) -> None:
    proj, path = load_project(a.folder)
    src = a.source
    if src.strip().startswith("{"):
        data = json.loads(src)
    else:
        f = Path(src) if Path(src).exists() else path.parent / src
        if not f.exists():
            die(f"{src} not found")
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            die(f"{f} is not JSON ({e}); write it in the shape of project.json (see assets/example-*.json)")
    notes = []
    for k, v in data.items():
        target = ALIASES.get(k, k)
        if "." in target:
            set_path(proj, target, v)
            notes.append(f"{k} -> {target}")
            continue
        if target != k:
            notes.append(f"{k} -> {target}")
        if target in TEMPLATES:
            if isinstance(v, dict):
                v = [{"id": kk, **vv} if isinstance(vv, dict) else vv for kk, vv in v.items()]
            merge_items(proj.setdefault(target, []), v, target, notes)
        elif target in ("style", "script", "models", "renders") and isinstance(v, dict):
            proj.setdefault(target, {}).update(v)
        elif target == "script" and isinstance(v, str):
            proj.setdefault("script", {})["body"] = v
        elif target in proj or target in ("language_name", "comfyui_url"):
            proj[target] = v
        else:
            notes.append(f"unknown top-level field {k} ignored")
    save_project(proj, path)
    emit({"ok": True, "notes": notes, "counts": {k: len(proj.get(k, [])) for k in TEMPLATES},
          "next": "next.py " + a.folder})


def cmd_remove(a) -> None:
    proj, path = load_project(a.folder)
    kind, _, iid = a.path.partition(".")
    kind = ALIASES.get(kind, kind)
    if kind not in TEMPLATES or not iid:
        die("remove <folder> subjects.<id> | environments.<id> | shots.<id>")
    before = len(proj.get(kind, []))
    proj[kind] = [x for x in proj.get(kind, []) if x.get("id") != iid]
    if len(proj[kind]) == before:
        die(f"no {a.path}")
    if kind == "subjects":
        for sh in proj.get("shots", []):
            sh["subjects"] = [x for x in sh.get("subjects", []) if x != iid]
    save_project(proj, path)
    emit({"ok": True, "removed": a.path})


def check(proj: dict, root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warns: list[str] = []
    subj = {s["id"]: s for s in proj.get("subjects", [])}
    envs = {e["id"]: e for e in proj.get("environments", [])}
    genre = proj.get("genre", "film")
    style = proj.get("style", {})

    # Phase 1 - script & audio-first timing
    if not proj.get("shots"):
        errors.append("[腳本] shots is empty - add shots (project.py set <folder> shots.SH01.action \"…\" or merge)")
    for s in proj.get("shots", []):
        sid = s["id"]
        if not s.get("duration") or float(s["duration"]) <= 0:
            errors.append(f"[腳本] shots.{sid}.duration is missing (seconds)")
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
        errors.append("[腳本] script.body (a text field inside project.json) is empty - put the script text there "
                      "with project.py set <folder> script.body \"…\" or merge; separate script files are ignored")

    # Phase 2 - profiling matrix
    for sid, s in subj.items():
        if not s.get("appearance"):
            errors.append(f"[側寫] subjects.{sid}.appearance is empty")
        missing = [d for d in PROFILE_DIMS if not str(s.get(d) or "").strip()]
        if missing:
            errors.append(f"[側寫] subjects.{sid}: profiling matrix incomplete - fill " +
                          ", ".join(f"subjects.{sid}.{m}" for m in missing))

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
            errors.append(f"[資產] environments.{eid}: fill environments.{eid}.description and "
                          f"environments.{eid}.light (key light direction, time of day)")
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
            errors.append(f"[構圖] shots.{sid}.shot_size is missing")
        elif s["shot_size"].lower() not in SHOT_SIZES:
            warns.append(f"[構圖] {sid}: shot_size '{s['shot_size']}' is not standard ({', '.join(SHOT_SIZES)})")
        if not s.get("angle"):
            errors.append(f"[構圖] shots.{sid}.angle is missing")
        elif s["angle"].lower() not in ANGLES:
            warns.append(f"[構圖] {sid}: angle '{s['angle']}' is not standard ({', '.join(ANGLES)})")
        if not s.get("lens"):
            warns.append(f"[構圖] {sid}: lens missing (e.g. 24mm wide, 50mm, 85mm portrait)")
        if not s.get("action"):
            errors.append(f"[構圖] shots.{sid}.action is missing (what happens, in English)")
        for k in ("first_frame", "last_frame"):
            if s.get(k) and not resolve(root, s[k]).exists():
                errors.append(f"[構圖] {sid}: {k} {s[k]} missing")

    # common slip: content written to side files instead of project.json
    stray = sorted(p.name for p in root.iterdir() if p.is_file() and p.suffix.lower() in (".txt", ".md", ".body")
                   or p.is_file() and p.suffix == ".json" and p.name not in ("project.json", "plan.json"))
    if stray:
        warns.append(f"[專案] {', '.join(stray)} are not read by the pipeline - move their content into project.json "
                     "(project.py merge <folder> <file.json>, or project.py set)")
    used = {i for s in proj.get("shots", []) for i in s.get("subjects", [])}
    for sid, s in subj.items():
        if sid not in used and not s.get("appearance"):
            errors.append(f"[側寫] subjects.{sid} is an unused empty placeholder - remove it "
                          f"(project.py remove <folder> subjects.{sid})")

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
    st = sub.add_parser("set", help="set fields: set <folder> subjects.S1.name 凌風 shots.SH02.duration 3.5")
    st.add_argument("folder")
    st.add_argument("pairs", nargs="+")
    mg = sub.add_parser("merge", help="merge a JSON file (or inline JSON) shaped like project.json")
    mg.add_argument("folder")
    mg.add_argument("source")
    rm = sub.add_parser("remove", help="remove subjects.<id> / environments.<id> / shots.<id>")
    rm.add_argument("folder")
    rm.add_argument("path")
    normalize_argv(["init", "set", "merge", "remove", "timing", "prompts", "validate", "plan"], "project.py")
    a = p.parse_args()
    {"set": cmd_set, "merge": cmd_merge, "remove": cmd_remove, "init": cmd_init, "timing": cmd_timing, "prompts": cmd_prompts, "validate": cmd_validate,
     "plan": cmd_plan}[a.cmd](a)


if __name__ == "__main__":
    main()
