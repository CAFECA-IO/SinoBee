"""Shared helpers for the ai-video-production scripts (stdlib only; needs ffmpeg/ffprobe on PATH)."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_FILE = "project.json"

# MiniMax H3 geometry (mirrors ComfyUI comfy_extras/nodes_minimax_h3.py)
H3_FPS = 24
H3_MIN_FRAMES = 124          # ~5.17 s, start of the trained range
H3_MAX_FRAMES = 362          # ~15.08 s, end of the trained range
CANVAS_MULTIPLE = 32
BASE_SHORT_EDGE = 768
MAX_PIXELS = 768 * 1344

GENRES = ("film", "documentary", "vlog", "commercial", "music", "explainer")
SHOT_SIZES = ("extreme close-up", "close-up", "medium close-up", "medium", "medium wide", "wide", "extreme wide",
              "over-the-shoulder", "insert", "pov")
ANGLES = ("eye level", "low angle", "high angle", "dutch angle", "bird's-eye", "worm's-eye", "overhead")
CAMERA_MOVES = ("static", "pan left", "pan right", "tilt up", "tilt down", "push in", "pull out", "zoom in",
                "zoom out", "dolly", "tracking", "truck left", "truck right", "crane up", "crane down", "orbit",
                "handheld", "gimbal follow", "whip pan", "rack focus")
PROFILE_DIMS = ("capability_ceiling", "negative_boundary", "linguistic_blueprint", "kinematic_signature")

# genre -> defaults used by `project.py init` and the prompt builder
GENRE_DEFAULTS = {
    "film": {"script_format": "screenplay", "camera": "smooth dolly and crane moves", "motion_strength": 0.6,
             "look": "Cinematic live-action film, anamorphic lens, shallow depth of field, film grain",
             "fps": 24},
    "documentary": {"script_format": "two-column", "camera": "handheld with natural breathing shake",
                    "motion_strength": 0.3,
                    "look": "Realistic documentary footage, natural available light, observational, true-to-life color",
                    "fps": 24},
    "vlog": {"script_format": "timeline", "camera": "handheld selfie and gimbal follow", "motion_strength": 0.3,
             "look": "Personal vlog footage, phone or compact camera, natural daylight, casual framing",
             "fps": 30},
    "commercial": {"script_format": "screenplay", "camera": "slick gimbal, slider and macro moves",
                   "motion_strength": 0.5,
                   "look": "Premium commercial cinematography, clean lighting, crisp detail, polished grade",
                   "fps": 24},
    "music": {"script_format": "timeline", "camera": "rhythmic dynamic moves cut on the beat",
              "motion_strength": 0.7, "look": "Music video, bold stylized lighting, high contrast", "fps": 24},
    "explainer": {"script_format": "two-column", "camera": "steady locked-off and slow push-ins",
                  "motion_strength": 0.3, "look": "Clean explainer footage, bright even light, simple backgrounds",
                  "fps": 30},
}

ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:3": (4, 3), "21:9": (21, 9), "4:5": (4, 5)}
OUTPUT_SIZES = {"720p": 720, "1080p": 1080, "1440p": 1440, "2160p": 2160}


def die(msg: str, code: int = 2) -> None:
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(code)


def emit(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def project_path(p: str | Path) -> Path:
    p = Path(p).expanduser()
    return p / PROJECT_FILE if p.is_dir() or p.suffix != ".json" else p


def load_project(p: str | Path) -> tuple[dict, Path]:
    path = project_path(p)
    if not path.exists():
        die(f"no project at {path} (create one with: project.py init <folder>)")
    try:
        return json.loads(path.read_text(encoding="utf-8")), path
    except json.JSONDecodeError as e:
        die(f"{path} is not valid JSON: {e}")
    raise AssertionError


def save_project(data: dict, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def resolve(root: Path, rel: str | None) -> Path | None:
    if not rel:
        return None
    p = Path(rel).expanduser()
    return p if p.is_absolute() else root / p


def need(tool: str) -> str:
    exe = shutil.which(tool)
    if not exe:
        die(f"{tool} not found on PATH; install ffmpeg first")
    return exe


def run(cmd: list[str], quiet: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-12:]
        die(f"command failed: {' '.join(cmd[:4])} …\n" + "\n".join(tail))
    return r


def media_info(path: Path) -> dict:
    need("ffprobe")
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type,width,height,r_frame_rate,sample_rate",
                        "-of", "json", str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        return {}
    d = json.loads(r.stdout or "{}")
    out = {"duration": float(d.get("format", {}).get("duration") or 0)}
    for s in d.get("streams", []):
        if s.get("codec_type") == "video" and "width" not in out:
            num, _, den = (s.get("r_frame_rate") or "0/1").partition("/")
            out.update(width=s.get("width"), height=s.get("height"),
                       fps=round(float(num) / float(den or 1), 3) if float(den or 1) else 0)
        elif s.get("codec_type") == "audio":
            out["audio"] = True
    return out


def duration(path: Path) -> float:
    return media_info(path).get("duration", 0.0)


def align_frames(n: int) -> int:
    """H3 frame counts live on a 17k+5 grid at 24 fps."""
    n = max(5, int(n))
    while n % 17 != 5:
        n += 1
    return n


def frames_for_seconds(seconds: float) -> int:
    """Frames to generate for a clip that must last at least `seconds` (clamped to the trained range)."""
    return min(H3_MAX_FRAMES, max(H3_MIN_FRAMES, align_frames(math.ceil(seconds * H3_FPS))))


def canvas(aspect: str, scale: float = 1.0) -> tuple[int, int]:
    """H3 generation canvas: 768 short edge, area <= 768*1344, multiples of 32; scale < 1 for drafts."""
    aw, ah = ASPECTS.get(aspect, (16, 9))
    ratio = aw / ah
    w, h = (BASE_SHORT_EDGE * ratio, BASE_SHORT_EDGE) if ratio >= 1 else (BASE_SHORT_EDGE, BASE_SHORT_EDGE / ratio)
    if w * h > MAX_PIXELS:
        s = math.sqrt(MAX_PIXELS / (w * h))
        w, h = w * s, h * s
    w, h = w * scale, h * scale
    return (max(CANVAS_MULTIPLE, round(w / CANVAS_MULTIPLE) * CANVAS_MULTIPLE),
            max(CANVAS_MULTIPLE, round(h / CANVAS_MULTIPLE) * CANVAS_MULTIPLE))


def output_size(aspect: str, label: str) -> tuple[int, int]:
    """Delivery size, e.g. 16:9 1080p -> 1920x1080, 9:16 1080p -> 1080x1920 (even numbers)."""
    aw, ah = ASPECTS.get(aspect, (16, 9))
    short = OUTPUT_SIZES.get(label, 1080)
    if aw >= ah:
        w, h = short * aw / ah, short
    else:
        w, h = short, short * ah / aw
    return int(round(w / 2) * 2), int(round(h / 2) * 2)


def shots_by_id(proj: dict) -> dict[str, dict]:
    return {s["id"]: s for s in proj.get("shots", [])}


def units(proj: dict) -> list[dict]:
    """Generation units: consecutive shots sharing a `group` are rendered together as one multi-shot H3 clip."""
    out: list[dict] = []
    for s in proj.get("shots", []):
        g = s.get("group") or s["id"]
        if out and out[-1]["id"] == g:
            out[-1]["shots"].append(s["id"])
        else:
            out.append({"id": g, "shots": [s["id"]]})
    return out


def shot_seconds(s: dict) -> float:
    return float(s.get("duration") or 0)


def unit_seconds(proj: dict, unit: dict) -> float:
    sb = shots_by_id(proj)
    return sum(shot_seconds(sb[i]) for i in unit["shots"])


def unit_state(proj: dict, uid: str) -> dict:
    return proj.setdefault("renders", {}).setdefault(uid, {})


# Commands agents often invent -> what to do instead (returned instead of a bare argparse error)
INVENTED = {
    "profiling": "the profiling matrix is data, not a command: fill subjects[] in project.json "
                 "(project.py set <folder> subjects.S1.capability_ceiling \"…\" or project.py merge <folder> plan.json)",
    "profile": "see 'profiling'",
    "subjects": "fill subjects[] with project.py set / merge",
    "script": "write the script text into script.body: project.py set <folder> script.body \"…\"",
    "assets": "fill environments[] and style.palette with project.py set / merge, then comfy_h3.py sheet/master",
    "render": "rendering is comfy_h3.py render <folder> --all --quality draft",
    "generate": "rendering is comfy_h3.py render <folder> --all --quality draft",
    "assemble": "assembly is assemble.py <folder>",
    "qc": "quality check is qc.py project <folder>",
    "tts": "narration audio is tts.py <folder>",
    "next": "next.py <folder> tells you the next step",
}


def normalize_argv(commands: list[str], script: str) -> None:
    """Make agent calls forgiving: drop a repeated script name, accept `<folder> <command>` order,
    and turn an unknown command into a JSON hint instead of an argparse error."""
    argv = sys.argv[1:]
    while argv and (argv[0] in ("python", "python3") or argv[0].endswith(".py")):
        argv = argv[1:]
    if not argv or argv[0] in ("-h", "--help") or argv[0] == "--url":
        sys.argv[1:] = argv
        return
    if argv[0] not in commands:
        if len(argv) > 1 and argv[1] in commands:
            argv[0], argv[1] = argv[1], argv[0]
        else:
            word = argv[0].lower()
            hint = INVENTED.get(word, "")
            if not hint and ((Path(argv[0]) / PROJECT_FILE).exists() or argv[0].endswith(".json")):
                hint = "the first argument must be a command, then the project folder, e.g. " \
                       f"`{script} validate {Path(argv[0]).parent if argv[0].endswith('.json') else argv[0]}`; " \
                       "next.py <folder> prints the exact next call"
            die(f"{script}: unknown command '{argv[0]}'. Commands: {', '.join(commands)}." + (f" {hint}" if hint else ""))
    sys.argv[1:] = argv


def strip_script_prefix() -> None:
    """Drop a script name or interpreter an agent repeated inside the arguments."""
    argv = sys.argv[1:]
    while argv and (argv[0] in ("python", "python3") or argv[0].endswith(".py")):
        argv = argv[1:]
    sys.argv[1:] = argv
