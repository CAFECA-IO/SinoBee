"""Videos made small enough to share: a clip over the SinoBee.ai attachment limit (25 MB) is re-encoded just before
upload - H.264 + AAC, a bitrate that fits the size, a lower resolution when the bitrate is low - and cached, so a
second share of the same file does not encode again.

ffmpeg comes from this computer when it has one, else from the sandbox image (`docker run --network none`), which
always ships it. Neither there: the share says how to get one instead of dropping the video silently.

    ~/.sinobee/cache/share-video/<key>.mp4      key = file path, size, mtime and the size limit
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


class MediaError(Exception):
    pass


def is_video(path: Path, mime: str | None = None) -> bool:
    return (mime or "").startswith("video/") or path.suffix.lower() in VIDEO_EXT


def _runner(config):
    """How to run ffmpeg / ffprobe: (kind, image). kind is "host" or "docker"."""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return "host", None
    if shutil.which("docker"):
        try:
            from .sandbox.manager import image_name
            image = image_name(config)
            ok = subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=20).returncode == 0
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            return "docker", image
    raise MediaError("影片超過 25 MB，要壓縮才能分享，但這台電腦沒有 ffmpeg，也找不到沙盒映像。"
                     "請安裝 ffmpeg（brew install ffmpeg），或啟動 Docker Desktop 並執行 sinobee sandbox build。")


def _cmd(kind, image, tool: str, args: list[str], src: Path, out_dir: Path | None) -> list[str]:
    if kind == "host":
        return [tool, *args]
    mounts = ["-v", f"{src.parent}:/in:ro"] + (["-v", f"{out_dir}:/out"] if out_dir else [])
    user = ["--user", f"{os.getuid()}:{os.getgid()}"] if hasattr(os, "getuid") else []
    fix = {str(src): f"/in/{src.name}"}
    if out_dir:
        fix.update({str(out_dir / n): f"/out/{n}" for n in (a.rsplit("/", 1)[-1] for a in args if a.startswith(str(out_dir)))})
    return ["docker", "run", "--rm", "--network", "none", *user, *mounts, "--entrypoint", tool, image,
            *[fix.get(a, a) for a in args]]


def duration(config, src: Path) -> float:
    kind, image = _runner(config)
    r = subprocess.run(_cmd(kind, image, "ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "json",
                                                     str(src)], src, None), capture_output=True, timeout=120)
    try:
        return float(json.loads(r.stdout or b"{}")["format"]["duration"])
    except (KeyError, ValueError, TypeError):
        raise MediaError(f"讀不出影片長度：{(r.stderr or b'').decode(errors='replace')[-200:]}") from None


def _plan(seconds: float, max_bytes: int) -> tuple[int, int, int]:
    """(video bits/s, audio bits/s, max height) for a clip of this length to fit in max_bytes."""
    budget = max_bytes * 8 * 0.92 / max(seconds, 1.0)          # 8 % for the container and rate swings
    audio = 96_000 if budget > 800_000 else 64_000 if budget > 300_000 else 32_000
    video = int(budget - audio)
    if video < 120_000:
        raise MediaError(f"影片太長（{seconds / 60:.0f} 分鐘），壓到 {max_bytes // 1048576} MB 以下畫面會糊到看不清，"
                         "請先剪短再分享")
    height = 1080 if video >= 4_000_000 else 720 if video >= 1_500_000 else 480 if video >= 600_000 else 360
    return video, audio, height


def shrink_video(config, src: Path, max_bytes: int) -> Path:
    """An .mp4 copy of src no larger than max_bytes (cached). Raises MediaError when it cannot be made."""
    src = Path(src).resolve()
    st = src.stat()
    key = hashlib.sha1(f"{src}|{st.st_size}|{st.st_mtime_ns}|{max_bytes}".encode()).hexdigest()[:16]
    cache = Path(config.home) / "cache" / "share-video"
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"{key}.mp4"
    if out.is_file() and 0 < out.stat().st_size <= max_bytes:
        return out
    kind, image = _runner(config)
    seconds = duration(config, src)
    video, audio, height = _plan(seconds, max_bytes)
    tmp = cache / f"{key}.part.mp4"
    for _ in range(3):
        args = ["-y", "-v", "error", "-i", str(src), "-vf", f"scale=-2:'min({height},ih)'", "-c:v", "libx264",
                "-preset", "veryfast", "-b:v", str(video), "-maxrate", str(int(video * 1.2)),
                "-bufsize", str(video * 2), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", str(audio), "-ac", "2",
                "-movflags", "+faststart", str(tmp)]
        r = subprocess.run(_cmd(kind, image, "ffmpeg", args, src, cache), capture_output=True,
                           timeout=max(120, int(seconds * 4)))
        if r.returncode != 0 or not tmp.is_file():
            raise MediaError("影片壓縮失敗：" + (r.stderr or b"").decode(errors="replace")[-300:])
        size = tmp.stat().st_size
        if size <= max_bytes:
            tmp.replace(out)
            return out
        video = int(video * 0.85 * max_bytes / size)
        if video < 120_000:
            break
    tmp.unlink(missing_ok=True)
    raise MediaError(f"影片壓不到 {max_bytes // 1048576} MB 以下，請先剪短再分享")


def prune(config, keep_mb: int = 500) -> None:
    """Keep the share-video cache under keep_mb (oldest out first)."""
    cache = Path(config.home) / "cache" / "share-video"
    if not cache.is_dir():
        return
    files = sorted((f for f in cache.iterdir() if f.is_file()), key=lambda f: f.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    for f in files:
        if total <= keep_mb * 1048576:
            break
        total -= f.stat().st_size
        f.unlink(missing_ok=True)


def thumbnail(config, src: Path, width: int = 480) -> tuple[Path, dict]:
    """A JPEG frame from about a tenth into the video (at most 3 s in) and {"duration", "width", "height"},
    cached by path, size and mtime: what a video card shows instead of a player."""
    src = Path(src).resolve()
    st = src.stat()
    key = hashlib.sha1(f"{src}|{st.st_size}|{st.st_mtime_ns}|{width}".encode()).hexdigest()[:16]
    cache = Path(config.home) / "cache" / "video-thumbs"
    cache.mkdir(parents=True, exist_ok=True)
    jpg, meta_p = cache / f"{key}.jpg", cache / f"{key}.json"
    if jpg.is_file() and meta_p.is_file():
        try:
            return jpg, json.loads(meta_p.read_text())
        except ValueError:
            pass
    kind, image = _runner(config)
    r = subprocess.run(_cmd(kind, image, "ffprobe", ["-v", "error", "-select_streams", "v:0", "-show_entries",
                                                     "stream=width,height:format=duration", "-of", "json", str(src)],
                            src, None), capture_output=True, timeout=120)
    try:
        info = json.loads(r.stdout or b"{}")
        stream = (info.get("streams") or [{}])[0]
        meta = {"duration": float(info.get("format", {}).get("duration") or 0),
                "width": int(stream.get("width") or 0), "height": int(stream.get("height") or 0)}
    except (ValueError, TypeError):
        raise MediaError("讀不出影片資訊") from None
    at = min(3.0, meta["duration"] / 10) if meta["duration"] else 0
    tmp = cache / f"{key}.part.jpg"
    r = subprocess.run(_cmd(kind, image, "ffmpeg", ["-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1",
                                                    "-vf", f"scale={width}:-2", "-q:v", "4", str(tmp)], src, cache),
                       capture_output=True, timeout=120)
    if r.returncode != 0 or not tmp.is_file():
        raise MediaError("擷取影片畫面失敗：" + (r.stderr or b"").decode(errors="replace")[-200:])
    tmp.replace(jpg)
    meta_p.write_text(json.dumps(meta))
    return jpg, meta
