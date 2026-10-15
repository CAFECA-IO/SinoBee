#!/usr/bin/env python3
"""Audio-first: turn each shot's narration (voice-over) and dialogue (guide track) into audio files.

  tts.py check                                   # which TTS engines are available
  tts.py <folder> [--engine auto|edge|say|espeak] [--voice NAME] [--rate +0%] [--only SH01,SH02] [--force]

Narration  -> audio/<shot>_vo.wav     (audio_role "voiceover": mixed into the final film)
Dialogue   -> audio/<shot>_guide.wav  (audio_role "guide": only locks timing; H3 voices the line on camera)
Then run: project.py timing <folder>
Engines: edge-tts (natural neural voices, needs internet), macOS `say`, espeak-ng (robotic, offline).
A human recording works too: put it in audio/ and set the shot's audio_file yourself.
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

from vpcommon import die, duration, emit, load_project, need, save_project

VOICES = {  # language prefix -> (edge narrator, edge alt, macOS say, espeak)
    "zh-tw": ("zh-TW-HsiaoChenNeural", "zh-TW-YunJheNeural", "Meijia", "cmn"),
    "zh-cn": ("zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "Tingting", "cmn"),
    "zh": ("zh-TW-HsiaoChenNeural", "zh-TW-YunJheNeural", "Meijia", "cmn"),
    "en": ("en-US-AriaNeural", "en-US-GuyNeural", "Samantha", "en-us"),
    "ja": ("ja-JP-NanamiNeural", "ja-JP-KeitaNeural", "Kyoko", "ja"),
    "ko": ("ko-KR-SunHiNeural", "ko-KR-InJoonNeural", "Yuna", "ko"),
}


def edge_available() -> bool:
    if shutil.which("edge-tts"):
        return True
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


def engines() -> dict:
    return {"edge": edge_available(), "say": bool(shutil.which("say")),
            "espeak": bool(shutil.which("espeak-ng") or shutil.which("espeak"))}


def voices_for(lang: str) -> tuple:
    lang = (lang or "zh-TW").lower()
    return VOICES.get(lang) or VOICES.get(lang.split("-")[0]) or VOICES["en"]


def to_wav(src: Path, dst: Path) -> None:
    need("ffmpeg")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-ar", "48000", "-ac", "1", str(dst)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        die(f"ffmpeg could not convert {src}: {r.stderr.strip()[-300:]}")


def synth(engine: str, text: str, voice: str, rate: str, dst: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if engine == "edge":
            out = tmp / "a.mp3"
            if shutil.which("edge-tts"):
                r = subprocess.run(["edge-tts", "--voice", voice, f"--rate={rate}", "--text", text,
                                    "--write-media", str(out)], capture_output=True, text=True)
                if r.returncode != 0:
                    die(f"edge-tts failed: {r.stderr.strip()[-300:]}")
            else:
                import edge_tts
                asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(str(out)))
        elif engine == "say":
            out = tmp / "a.aiff"
            r = subprocess.run(["say", "-v", voice, "-o", str(out), text], capture_output=True, text=True)
            if r.returncode != 0:
                die(f"say failed: {r.stderr.strip()[-300:]}")
        else:
            out = tmp / "a.wav"
            exe = shutil.which("espeak-ng") or shutil.which("espeak")
            subprocess.run([exe, "-v", voice, "-w", str(out), text], check=True, capture_output=True)
        to_wav(out, dst)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folder")
    p.add_argument("--engine", default="auto", choices=["auto", "edge", "say", "espeak"])
    p.add_argument("--voice", help="narrator voice (default: style.voice or the language default)")
    p.add_argument("--rate", default="+0%", help="edge-tts speaking rate, e.g. -10%")
    p.add_argument("--only", help="comma-separated shot ids")
    p.add_argument("--force", action="store_true", help="re-create existing files")
    a = p.parse_args()
    if a.folder == "check":
        emit({"ok": True, "engines": engines()})
        return
    avail = engines()
    engine = a.engine
    if engine == "auto":
        engine = next((e for e in ("edge", "say", "espeak") if avail[e]), None)
        if not engine:
            die("no TTS engine: install edge-tts (python package) or espeak-ng, or record the narration and set "
                "each shot's audio_file")
    elif not avail[engine]:
        die(f"TTS engine {engine} is not available ({avail})")
    proj, path = load_project(a.folder)
    root = path.parent
    (root / "audio").mkdir(exist_ok=True)
    lang_v = voices_for(proj.get("language", "zh-TW"))
    idx = {"edge": 0, "say": 2, "espeak": 3}[engine]
    narrator = a.voice or (proj.get("style") or {}).get("voice") or lang_v[idx]
    subj = {s["id"]: s for s in proj.get("subjects", [])}
    only = set(a.only.split(",")) if a.only else None
    made = []
    for s in proj.get("shots", []):
        if only and s["id"] not in only:
            continue
        if s.get("audio_file") and s.get("audio_role") == "recorded":
            continue
        if s.get("narration"):
            dst, role, text, voice = root / "audio" / f"{s['id']}_vo.wav", "voiceover", s["narration"], narrator
        elif s.get("dialogue"):
            lines = [d.get("line", "") for d in s["dialogue"] if d.get("line")]
            first = subj.get(s["dialogue"][0].get("subject"), {})
            alt = lang_v[1] if engine == "edge" else lang_v[idx]
            dst, role, text, voice = root / "audio" / f"{s['id']}_guide.wav", "guide", "，".join(lines), \
                first.get("voice") or alt
        else:
            continue
        if not dst.exists() or a.force:
            synth(engine, text, voice, a.rate, dst)
        s["audio_file"] = str(dst.relative_to(root))
        s["audio_role"] = role
        made.append({"shot": s["id"], "file": s["audio_file"], "role": role, "seconds": round(duration(dst), 2)})
    save_project(proj, path)
    emit({"ok": True, "engine": engine, "voice": narrator, "files": made,
          "next": "project.py timing " + str(root)})


if __name__ == "__main__":
    main()
