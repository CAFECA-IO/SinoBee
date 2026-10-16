#!/usr/bin/env python3
"""Phase 6: assemble the film on the Phase 1 timeline - trim, conform, grade, upscale, interpolate, mix, subtitle.

  assemble.py <folder> [--delivery 1080p] [--fps 24] [--grade none|film|warm|cool|vivid] [--lut file.cube]
                       [--bgm music.mp3] [--bgm-db -20] [--ambience-db auto] [--xfade 0.0]
                       [--subs srt|burn|none] [--loudness -14] [--allow-missing] [--out out/final.mp4]

- Each unit's clip is cut to its planned length (sum of its shots), scaled to the delivery size (lanczos +
  light sharpening) and conformed to the delivery fps (motion-compensated interpolation when fps > 24).
- One LUT / grade is applied to every clip so color stays uniform.
- Audio: H3's own sound (ambience, effects, on-camera dialogue) + voice-over files placed at their shots +
  optional BGM; ambience and music duck under the voice-over; loudness normalised (default -14 LUFS).
- Subtitles from narration/dialogue: out/final.srt (and burned in with --subs burn).
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from vpcommon import (strip_script_prefix, die, emit, load_project, media_info, need, output_size, resolve, run, shot_seconds,
                      shots_by_id, unit_seconds, units)

GRADES = {
    "none": "",
    "film": "eq=contrast=1.06:saturation=0.92:gamma=0.98,colorbalance=rs=0.03:bs=-0.03:rh=0.02:bh=-0.02,"
            "noise=alls=4:allf=t",
    "warm": "colorbalance=rs=0.06:gs=0.01:bs=-0.05:rm=0.04:bm=-0.03,eq=saturation=1.04",
    "cool": "colorbalance=rs=-0.04:bs=0.06:bm=0.03,eq=saturation=0.96",
    "vivid": "eq=contrast=1.08:saturation=1.18",
}


def has_filter(name: str) -> bool:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return f" {name} " in r.stdout


def fmt_ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def timeline(proj: dict, xfade: float) -> list[dict]:
    """[{unit, start, seconds, shots:[{id, start, end}]}] on the final timeline."""
    out, t = [], 0.0
    sb = shots_by_id(proj)
    for i, u in enumerate(units(proj)):
        secs = unit_seconds(proj, u)
        start = t
        shots, st = [], start
        for sid in u["shots"]:
            d = shot_seconds(sb[sid])
            shots.append({"id": sid, "start": st, "end": st + d})
            st += d
        out.append({"unit": u["id"], "start": start, "seconds": secs, "shots": shots})
        t = start + secs - (xfade if i < len(units(proj)) - 1 else 0)
    return out


def conform(src: Path | None, dst: Path, secs: float, w: int, h: int, fps: int, vf_extra: str, label: str) -> None:
    if src is None:   # placeholder slate for a unit that is not rendered yet
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x202020:s={w}x{h}:r={fps}:d={secs}",
               "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{secs}"]
        vf = f"drawtext=text='{label}':x=(w-tw)/2:y=(h-th)/2:fontsize={h // 14}:fontcolor=white" \
            if has_filter("drawtext") else "null"
    else:
        info = media_info(src)
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src)]
        if not info.get("audio"):
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        cmd += ["-t", f"{secs}"]
        motion = (f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
                  if fps > round(info.get("fps") or 24) else f"fps={fps}")
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},setsar=1,"
              f"{motion},unsharp=5:5:0.4:5:5:0.0" + (f",{vf_extra}" if vf_extra else ""))
        if info.get("duration", 0) + 0.05 < secs:   # short clip: hold the last frame
            vf += f",tpad=stop_mode=clone:stop_duration={secs - info['duration'] + 0.1:.2f}"
    cmd += ["-vf", vf, "-af", "aresample=48000,aformat=channel_layouts=stereo,apad",
            "-map", "0:v:0", "-map", "1:a:0" if (src is None or not media_info(src).get("audio")) else "0:a:0",
            "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-t", f"{secs}", str(dst)]
    run(cmd)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folder")
    p.add_argument("--delivery", choices=["720p", "1080p", "1440p", "2160p"])
    p.add_argument("--fps", type=int)
    p.add_argument("--grade", choices=list(GRADES), default="none")
    p.add_argument("--lut", help=".cube LUT (default: style.lut)")
    p.add_argument("--bgm", help="music file (default: style.bgm_file)")
    p.add_argument("--bgm-db", type=float, default=-20)
    p.add_argument("--ambience-db", default="auto", help="gain of H3's own audio; auto = 0 dB, -8 dB under voice-over")
    p.add_argument("--xfade", type=float, default=0.0, help="cross-dissolve seconds between units (0 = hard cut)")
    p.add_argument("--subs", choices=["srt", "burn", "none"], default="srt")
    p.add_argument("--loudness", type=float, default=-14.0)
    p.add_argument("--allow-missing", action="store_true", help="grey slates for units not rendered yet (animatic)")
    p.add_argument("--out", default="out/final.mp4")
    strip_script_prefix()
    a = p.parse_args()
    need("ffmpeg")
    proj, path = load_project(a.folder)
    root = path.parent
    style = proj.get("style") or {}
    w, h = output_size(proj.get("aspect", "16:9"), a.delivery or proj.get("delivery", "1080p"))
    fps = a.fps or int(proj.get("fps") or 24)
    lut = resolve(root, a.lut or style.get("lut"))
    if lut and not lut.exists():
        die(f"LUT {lut} not found")
    vf_extra = ",".join(x for x in [f"lut3d=file='{lut}'" if lut else "", GRADES[a.grade]] if x)
    tl = timeline(proj, a.xfade)
    missing = [t["unit"] for t in tl
               if not (resolve(root, proj.get("renders", {}).get(t["unit"], {}).get("clip")) or Path("/nonexistent"))
               .exists()]
    if missing and not a.allow_missing:
        die(f"units not rendered yet: {', '.join(missing)} (render them, or --allow-missing for an animatic)")
    out = root / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        parts = []
        for i, t in enumerate(tl):
            clip = resolve(root, proj.get("renders", {}).get(t["unit"], {}).get("clip"))
            dst = tmp / f"p{i:03d}.mp4"
            conform(clip if clip and clip.exists() else None, dst, t["seconds"], w, h, fps, vf_extra, t["unit"])
            parts.append(dst)
        joined = tmp / "joined.mp4"
        if a.xfade <= 0 or len(parts) == 1:
            lst = tmp / "list.txt"
            lst.write_text("".join(f"file '{p}'\n" for p in parts))
            run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                 str(joined)])
        else:
            ins, fc, off = [], [], 0.0
            for p_ in parts:
                ins += ["-i", str(p_)]
            v, au = "[0:v]", "[0:a]"
            for i in range(1, len(parts)):
                off += tl[i - 1]["seconds"] - a.xfade
                fc.append(f"{v}[{i}:v]xfade=transition=fade:duration={a.xfade}:offset={off:.3f}[v{i}]")
                fc.append(f"{au}[{i}:a]acrossfade=d={a.xfade}[a{i}]")
                v, au = f"[v{i}]", f"[a{i}]"
            run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(fc), "-map", v, "-map", au,
                 "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", "-c:a", "aac",
                 "-b:a", "192k", str(joined)])
        total = media_info(joined).get("duration") or sum(t["seconds"] for t in tl)

        # voice-over placed at its shot, ambience/music ducked under it, loudness normalised
        sb = shots_by_id(proj)
        vo = []
        for t in tl:
            for s in t["shots"]:
                shot = sb[s["id"]]
                f = resolve(root, shot.get("audio_file"))
                if f and f.exists() and shot.get("audio_role", "voiceover") in ("voiceover", "recorded"):
                    vo.append((f, s["start"] + 0.1))
        bgm = resolve(root, a.bgm or style.get("bgm_file"))
        if bgm and not bgm.exists():
            die(f"BGM {bgm} not found")
        amb_db = (-8.0 if vo else 0.0) if a.ambience_db == "auto" else float(a.ambience_db)
        ins = ["-i", str(joined)]
        fc = [f"[0:a]volume={amb_db}dB[amb]"]
        beds = ["[amb]"]
        idx = 1
        if bgm:
            ins += ["-stream_loop", "-1", "-i", str(bgm)]
            fc.append(f"[{idx}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,volume={a.bgm_db}dB,"
                      f"afade=t=in:d=1.5,afade=t=out:st={max(0.0, total - 2.5):.3f}:d=2.5[bgm]")
            beds.append("[bgm]")
            idx += 1
        bed = "[bed]"
        fc.append(f"{''.join(beds)}amix=inputs={len(beds)}:normalize=0:duration=first[bed]" if len(beds) > 1
                  else "[amb]anull[bed]")
        if vo:
            vlabels = []
            for f, start in vo:
                ins += ["-i", str(f)]
                ms = int(start * 1000)
                fc.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms}[vo{idx}]")
                vlabels.append(f"[vo{idx}]")
                idx += 1
            fc.append(f"{''.join(vlabels)}amix=inputs={len(vlabels)}:normalize=0,apad,atrim=0:{total:.3f}[vo]"
                      if len(vlabels) > 1 else f"{vlabels[0]}apad,atrim=0:{total:.3f}[vo]")
            fc.append("[vo]asplit=2[vok][vom]")
            fc.append("[bed][vok]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[ducked]")
            fc.append("[ducked][vom]amix=inputs=2:normalize=0:duration=first[mix]")
            bed = "[mix]"
        fc.append(f"{bed}loudnorm=I={a.loudness}:TP=-1.5:LRA=11,aresample=48000[aout]")

        srt = out.with_suffix(".srt")
        cues = []
        for t in tl:
            for s in t["shots"]:
                shot = sb[s["id"]]
                text = shot.get("narration") or " ".join(d.get("line", "") for d in shot.get("dialogue") or [])
                if text.strip():
                    cues.append((s["start"] + 0.1, min(s["end"], total) - 0.05, text.strip()))
        if cues and a.subs != "none":
            srt.write_text("\n".join(f"{i}\n{fmt_ts(b)} --> {fmt_ts(e)}\n{txt}\n" for i, (b, e, txt) in
                                     enumerate(cues, 1)), encoding="utf-8")
        vmap = ["-map", "0:v:0", "-c:v", "copy"]
        if a.subs == "burn" and cues:
            if not has_filter("subtitles"):
                die("this ffmpeg has no subtitles filter (libass); use --subs srt")
            vmap = ["-map", "0:v:0", "-vf", f"subtitles='{srt}':force_style='FontSize=20,Outline=1,MarginV=28'",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p"]
        run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(fc), *vmap, "-map", "[aout]",
             "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)])
    info = media_info(out)
    emit({"ok": True, "out": str(out.relative_to(root)), "duration": round(info.get("duration", 0), 2),
          "size": [info.get("width"), info.get("height")], "fps": info.get("fps"),
          "subtitles": str(srt.relative_to(root)) if cues and a.subs != "none" else None,
          "voiceover_clips": len(vo), "bgm": bool(bgm), "placeholders": missing,
          "next": "qc.py project " + a.folder})


if __name__ == "__main__":
    main()
