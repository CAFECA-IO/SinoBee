#!/usr/bin/env python3
"""Quality check: contact sheets you can look at (read_file the .jpg) plus automatic signals.

  qc.py clip <video> [--out sheet.jpg] [--frames 9]
  qc.py project <folder>              # every rendered unit -> qc/<unit>.jpg, and the final film if present

Signals: duration vs. the shot plan, resolution, frozen stretches (freezedetect), black frames (blackdetect),
loudness (LUFS) and peak level. Visual problems (hands, faces, identity drift, physics) need your eyes on the sheet.
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from vpcommon import die, emit, load_project, media_info, need, resolve, unit_seconds, units


def sheet(video: Path, out: Path, n: int = 9) -> Path:
    need("ffmpeg")
    info = media_info(video)
    dur = info.get("duration") or 0
    if not dur:
        die(f"{video} has no duration (not a video?)")
    cols = 3 if n >= 6 else n
    rows = (n + cols - 1) // cols
    step = dur / (n + 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    w = 480
    vf = (f"fps=1/{step:.4f},scale={w}:-2,"
          f"drawtext=text='%{{pts\\:hms}}':x=8:y=8:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.5,"
          f"tile={cols}x{rows}")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{step / 2:.3f}", "-i", str(video), "-vf", vf,
                        "-frames:v", "1", "-q:v", "3", str(out)], capture_output=True, text=True)
    if r.returncode != 0:   # drawtext needs libfreetype; fall back to a sheet without timestamps
        vf = f"fps=1/{step:.4f},scale={w}:-2,tile={cols}x{rows}"
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{step / 2:.3f}", "-i", str(video), "-vf", vf,
                            "-frames:v", "1", "-q:v", "3", str(out)], capture_output=True, text=True)
        if r.returncode != 0:
            die(f"could not build a contact sheet: {r.stderr.strip()[-300:]}")
    return out


def signals(video: Path) -> dict:
    info = media_info(video)
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(video), "-vf",
                        "freezedetect=n=0.003:d=0.8,blackdetect=d=0.3:pix_th=0.08",
                        "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    log = r.stderr
    freezes = [float(x) for x in re.findall(r"freeze_duration: ([\d.]+)", log)]
    blacks = [float(x) for x in re.findall(r"black_duration:([\d.]+)", log)]
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", log)
    peak = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", log)
    out = {"duration": round(info.get("duration", 0), 2), "size": [info.get("width"), info.get("height")],
           "fps": info.get("fps"), "has_audio": bool(info.get("audio")),
           "frozen_seconds": round(sum(freezes), 2), "black_seconds": round(sum(blacks), 2)}
    if lufs:
        out["loudness_lufs"] = float(lufs[-1])
    if peak:
        out["true_peak_dbfs"] = float(peak[-1])
    flags = []
    if out["frozen_seconds"] > 0.8:
        flags.append("frozen stretch - motion stalled; regenerate or trim")
    if out["black_seconds"] > 0.3:
        flags.append("black frames")
    if out.get("loudness_lufs") is not None and out["loudness_lufs"] < -40:
        flags.append("almost silent")
    if out.get("true_peak_dbfs") is not None and out["true_peak_dbfs"] > -0.5:
        flags.append("audio clipping")
    out["flags"] = flags
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("clip")
    c.add_argument("video")
    c.add_argument("--out")
    c.add_argument("--frames", type=int, default=9)
    pj = sub.add_parser("project")
    pj.add_argument("folder")
    a = p.parse_args()
    if a.cmd == "clip":
        v = Path(a.video)
        out = Path(a.out) if a.out else v.with_suffix(".sheet.jpg")
        emit({"ok": True, "sheet": str(sheet(v, out, a.frames)), **signals(v)})
        return
    proj, path = load_project(a.folder)
    root = path.parent
    res = []
    for u in units(proj):
        st = proj.get("renders", {}).get(u["id"], {})
        clip = resolve(root, st.get("clip"))
        if not clip or not clip.exists():
            res.append({"unit": u["id"], "status": st.get("status", "pending")})
            continue
        s = signals(clip)
        need_s = unit_seconds(proj, u)
        if s["duration"] + 0.05 < need_s:
            s["flags"].append(f"clip shorter than planned {need_s:.1f}s")
        res.append({"unit": u["id"], "clip": st["clip"], "sheet": str(sheet(clip, root / "qc" / f"{u['id']}.jpg")
                                                                         .relative_to(root)), **s})
    final = root / "out" / "final.mp4"
    out = {"ok": True, "units": res}
    if final.exists():
        out["final"] = {"sheet": str(sheet(final, root / "qc" / "final.jpg", 12).relative_to(root)),
                        **signals(final)}
    emit(out)


if __name__ == "__main__":
    main()
