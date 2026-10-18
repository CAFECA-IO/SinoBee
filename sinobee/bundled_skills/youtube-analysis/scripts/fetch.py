"""Fetch everything the agent needs to "watch" a YouTube video, without downloading the video itself.

  metadata      title, channel, date, duration, views, description, chapters, tags (yt-dlp -J)
  transcript    the best caption track (manual original > auto original > manual/auto in the reader's
                language), as timestamped paragraphs grouped by chapter: transcript.md + transcript.json
  thumbnail     the largest thumbnail as thumbnail.jpg
  frames        YouTube's storyboard mosaics cut into stills (one about every 10 s), a spread of them saved as
                frames/tMMSS.jpg, and 4x4 contact sheets (sheets/sheet-NN.jpg) to look at with read_file

Only yt-dlp (installed on first use when missing) and Pillow (for frames) are needed. Network access to
youtube.com and i.ytimg.com is required.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36"}
ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|live/|embed/)([A-Za-z0-9_-]{11})")


class Blocked(RuntimeError):
    """Something outside the skill is missing (yt-dlp, network); tell the user what."""


def video_id(url: str) -> str:
    m = ID_RE.search(url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url.strip()):
        return url.strip()
    raise ValueError(f"not a YouTube video link: {url}")


def clock(seconds: float) -> str:
    s = int(round(seconds))
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ───────────── yt-dlp ─────────────

def ytdlp() -> list[str]:
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        pass
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--user", "--quiet", "yt-dlp"], capture_output=True, text=True)
    if r.returncode:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--user", "--quiet", "--break-system-packages", "yt-dlp"],
                           capture_output=True, text=True)
    if r.returncode:
        raise Blocked("yt-dlp is not installed and could not be installed: run `pip install yt-dlp` (or add it to the "
                      "image), then try again")
    return [sys.executable, "-m", "yt_dlp"]


def run_ytdlp(args: list[str], timeout: int = 180) -> str:
    base = ytdlp() + ["--no-warnings", "--no-playlist"]
    js = ["--js-runtimes", "node"] if shutil.which("node") and not shutil.which("deno") else []
    for extra in (js, []) if js else ([],):
        r = subprocess.run(base + extra + args, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            return r.stdout
        err = (r.stderr or r.stdout)[-1200:]
        if extra and "js-runtimes" in err:   # an older yt-dlp without the option
            continue
        if "429" in err or "Too Many Requests" in err:
            raise Blocked("YouTube is rate-limiting this network (HTTP 429). Wait a while, or run SinoBee on another "
                          "network, then try again")
        if "Sign in to confirm" in err or "not a bot" in err:
            raise Blocked("YouTube asks this network to sign in (bot check). Try again later or from another network")
        if "Private video" in err or "Video unavailable" in err or "members-only" in err.lower():
            raise ValueError("the video is private, removed or members-only: " + err.strip().splitlines()[-1])
        raise RuntimeError("yt-dlp failed: " + err.strip())
    raise RuntimeError("yt-dlp failed")


def http_get(url: str, timeout: int = 30) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


# ───────────── metadata ─────────────

KEEP = ("id", "title", "channel", "channel_id", "channel_url", "uploader", "upload_date", "timestamp", "duration",
        "view_count", "like_count", "comment_count", "channel_follower_count", "description", "tags", "categories",
        "chapters", "language", "webpage_url", "thumbnail", "live_status", "age_limit", "is_live", "was_live")


def metadata(url: str) -> dict:
    info = json.loads(run_ytdlp(["-J", "--skip-download", url]))
    if info.get("_type") == "playlist":
        raise ValueError("this is a playlist; give one video link")
    return info


def slim(info: dict) -> dict:
    out = {k: info.get(k) for k in KEEP if info.get(k) is not None}
    out["webpage_url"] = out.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}"
    out["chapters"] = [{"start": c.get("start_time", 0), "end": c.get("end_time"), "title": c.get("title", "")}
                       for c in info.get("chapters") or []]
    d = info.get("upload_date") or ""
    out["date"] = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else ""
    out["captions"] = {"manual": sorted((info.get("subtitles") or {}).keys()),
                       "auto": sorted(k for k in (info.get("automatic_captions") or {}) if k.endswith("-orig"))}
    return out


# ───────────── transcript ─────────────

def pick_track(info: dict, want: str) -> tuple[str, str] | None:
    """(language key, kind) of the best caption track. kind: manual | auto | auto-translated"""
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = info.get("automatic_captions") or {}
    orig = (info.get("language") or "").split("-")[0].lower()
    orig_auto = next((k for k in auto if k.endswith("-orig")), None)
    if not orig and orig_auto:
        orig = orig_auto.split("-")[0].lower()
    base = lambda k: k.split("-")[0].lower()  # noqa: E731
    wants = [want, want.split("-")[0], "zh-Hant", "zh-TW", "zh", "en"]
    if orig:
        for k in manual:
            if base(k) == orig:
                return k, "manual"
    for w in wants:
        for k in manual:
            if k.lower() == w.lower() or base(k) == w.lower():
                return k, "manual"
    if orig_auto:
        return orig_auto, "auto"
    if orig and orig in auto:
        return orig, "auto"
    for w in wants:
        if w in auto:
            return w, "auto-translated"
    return None


def parse_json3(raw: str) -> list[dict]:
    data = json.loads(raw)
    segs = []
    for ev in data.get("events") or []:
        if "segs" not in ev:
            continue
        text = "".join(s.get("utf8", "") for s in ev["segs"]).replace("\n", " ").strip()
        if not text:
            continue
        start = ev.get("tStartMs", 0) / 1000
        segs.append({"start": round(start, 2), "end": round(start + ev.get("dDurationMs", 0) / 1000, 2), "text": text})
    # auto captions repeat words across rolling events: drop exact repeats of the previous text
    out = []
    for s in segs:
        if out and s["text"] == out[-1]["text"]:
            out[-1]["end"] = s["end"]
            continue
        out.append(s)
    return out


def parse_vtt(raw: str) -> list[dict]:
    segs, cur = [], None
    for line in raw.splitlines():
        m = re.match(r"(\d+:)?(\d+):(\d+)\.(\d+)\s+-->\s+(\d+:)?(\d+):(\d+)\.(\d+)", line)
        if m:
            t = lambda h, mi, s, ms: int((h or "0:")[:-1]) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000  # noqa: E731
            cur = {"start": t(*m.group(1, 2, 3, 4)), "end": t(*m.group(5, 6, 7, 8)), "text": ""}
            segs.append(cur)
        elif cur is not None and line.strip():
            cur["text"] = (cur["text"] + " " + re.sub(r"<[^>]+>", "", line).strip()).strip()
    out = []
    for s in segs:
        if s["text"] and not (out and out[-1]["text"] == s["text"]):
            out.append(s)
    return out


def transcript(url: str, info: dict, want: str, folder: Path) -> dict:
    track = pick_track(info, want)
    if not track:
        return {"kind": "none", "lang": None, "segments": []}
    key, kind = track
    tmp = folder / ".subs"
    tmp.mkdir(exist_ok=True)
    flag = "--write-subs" if kind == "manual" else "--write-auto-subs"
    run_ytdlp([flag, "--sub-langs", key, "--sub-format", "json3/vtt/best", "--skip-download", "-o", str(tmp / "sub"), url])
    files = sorted(tmp.glob("sub*"))
    if not files:
        return {"kind": "none", "lang": key, "segments": []}
    raw = files[0].read_text(encoding="utf-8", errors="replace")
    segs = parse_json3(raw) if files[0].suffix == ".json3" else parse_vtt(raw)
    shutil.rmtree(tmp, ignore_errors=True)
    return {"kind": kind, "lang": key.removesuffix("-orig"), "segments": segs}


def paragraphs(segs: list[dict], chapters: list[dict], every: float = 45.0) -> list[dict]:
    """Join caption lines into paragraphs of about `every` seconds, breaking at chapter starts."""
    starts = sorted(c["start"] for c in chapters if c["start"] > 0)
    out, cur = [], None
    for s in segs:
        crossed = cur is not None and any(cur["start"] < c <= s["start"] for c in starts)
        long = cur is not None and s["start"] - cur["start"] >= every
        ends = cur is not None and re.search(r"[.!?。！？…」)]$", cur["text"])
        if cur is None or crossed or (long and ends) or (cur and s["start"] - cur["start"] >= every * 1.6):
            cur = {"start": s["start"], "text": ""}
            out.append(cur)
        joiner = "" if re.search(r"[　-鿿]$", cur["text"]) and re.match(r"[　-鿿]", s["text"]) else " "
        cur["text"] = (cur["text"] + joiner + s["text"]).strip()
    return out


def write_transcript(folder: Path, meta: dict, tr: dict) -> dict:
    paras = paragraphs(tr["segments"], meta["chapters"])
    (folder / "transcript.json").write_text(json.dumps({**{k: tr[k] for k in ("kind", "lang")}, "segments": tr["segments"]},
                                                       ensure_ascii=False), encoding="utf-8")
    kind = {"manual": "人工字幕", "auto": "自動字幕（語音辨識，可能有錯字）", "auto-translated": "自動翻譯字幕（非原文）",
            "none": "無字幕"}[tr["kind"]]
    lines = [f"# 逐字稿：{meta.get('title', '')}", "", f"- 來源：{kind}；語言 {tr['lang'] or '—'}",
             f"- 連結：{meta['webpage_url']}", "- 時間戳可直接加在連結後：`&t=<秒數>s`", ""]
    chapters = sorted(meta["chapters"], key=lambda c: c["start"])
    ci = 0
    for p in paras:
        while ci < len(chapters) and chapters[ci]["start"] <= p["start"] + 0.01:
            lines += [f"## [{clock(chapters[ci]['start'])}] {chapters[ci]['title']}", ""]
            ci += 1
        lines += [f"[{clock(p['start'])}] {p['text']}", ""]
    if not paras:
        lines += ["（這支影片沒有可用的字幕。請以標題、說明、章節與畫面截圖分析，並在報告的方法說明裡註明。）", ""]
    (folder / "transcript.md").write_text("\n".join(lines), encoding="utf-8")
    text = " ".join(s["text"] for s in tr["segments"])
    return {"file": "transcript.md", "kind": tr["kind"], "lang": tr["lang"], "chars": len(text), "paragraphs": len(paras)}


# ───────────── thumbnail and frames ─────────────

def thumbnail(info: dict, folder: Path) -> str | None:
    vid = info["id"]
    urls = [f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg", f"https://i.ytimg.com/vi/{vid}/sddefault.jpg",
            f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"]
    for u in urls:
        try:
            data = http_get(u)
        except Exception:  # noqa: BLE001
            continue
        if len(data) > 2000:
            (folder / "thumbnail.jpg").write_bytes(data)
            return "thumbnail.jpg"
    return None


def storyboard(info: dict) -> dict | None:
    boards = [f for f in info.get("formats") or [] if str(f.get("format_id", "")).startswith("sb") and f.get("fragments")]
    if not boards:
        return None
    best = max(boards, key=lambda f: (f.get("width") or 0) * (f.get("height") or 0))
    return {"w": best["width"], "h": best["height"], "rows": best["rows"], "cols": best["columns"],
            "fps": best.get("fps") or 0, "fragments": [{"url": f["url"], "duration": f.get("duration") or 0} for f in best["fragments"]]}


def tiles(info: dict, folder: Path) -> tuple[list[tuple[float, object]], dict | None]:
    """Every storyboard tile as (time, PIL image), from mosaics cached in .storyboard/"""
    from PIL import Image
    sb = storyboard(info)
    if not sb:
        return [], None
    cache = folder / ".storyboard"
    cache.mkdir(exist_ok=True)
    (cache / "board.json").write_text(json.dumps(sb), encoding="utf-8")
    out = []
    per = sb["rows"] * sb["cols"]
    duration = info.get("duration") or 0
    step = 1 / sb["fps"] if sb["fps"] else (duration / max(1, per * len(sb["fragments"])))
    for k, frag in enumerate(sb["fragments"]):
        path = cache / f"m{k:03d}.jpg"
        if not path.exists():
            path.write_bytes(http_get(frag["url"]))
        img = Image.open(path).convert("RGB")
        for i in range(per):
            t = (k * per + i) * step
            if duration and t > duration:
                break
            r, c = divmod(i, sb["cols"])
            box = (c * sb["w"], r * sb["h"], (c + 1) * sb["w"], (r + 1) * sb["h"])
            if box[2] > img.width or box[3] > img.height:
                break
            tile = img.crop(box)
            if tile.getextrema()[0][1] < 8 and k == len(sb["fragments"]) - 1:   # black padding after the end
                continue
            out.append((t, tile))
    return out, sb


def save_frame(tile, t: float, folder: Path, size: tuple[int, int] = (640, 360)) -> str:
    from PIL import Image
    t = int(round(t))
    name = f"frames/t{t // 60:02d}{t % 60:02d}.jpg" if t < 6000 else f"frames/t{t:05d}.jpg"
    (folder / "frames").mkdir(exist_ok=True)
    tile.resize(size, Image.LANCZOS).save(folder / name, quality=88)
    return name


def frames(info: dict, folder: Path, meta: dict, count: int | None = None) -> dict:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return {"count": 0, "note": "Pillow is not installed: no frames (pip install pillow)"}
    try:
        all_tiles, sb = tiles(info, folder)
    except Exception as e:  # noqa: BLE001
        return {"count": 0, "note": f"storyboard unavailable: {e}"}
    if not all_tiles:
        return {"count": 0, "note": "this video has no storyboard"}
    duration = info.get("duration") or all_tiles[-1][0]
    count = count or max(12, min(48, int(duration // 30) or 12))
    times = [duration * (i + 0.5) / count for i in range(count)]
    times += [c["start"] + 3 for c in meta["chapters"]]
    chosen, seen = [], set()
    for t in sorted(times):
        j = min(range(len(all_tiles)), key=lambda i: abs(all_tiles[i][0] - t))
        while j + 1 < len(all_tiles) and max(b for _, b in all_tiles[j][1].getextrema()) < 24:   # skip black frames
            j += 1
        if j not in seen:
            seen.add(j)
            chosen.append(all_tiles[j])
    names = [save_frame(tile, t, folder) for t, tile in chosen]
    # contact sheets: 4 x 4 stills with a time label, for viewing the whole video in a few images
    (folder / "sheets").mkdir(exist_ok=True)
    font = None
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", "/System/Library/Fonts/Menlo.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if Path(f).exists():
            font = ImageFont.truetype(f, 22)
            break
    font = font or ImageFont.load_default()
    sheets = []
    W, H = 400, 225
    for s in range(0, len(chosen), 16):
        part = chosen[s:s + 16]
        sheet = Image.new("RGB", (W * 4, H * ((len(part) + 3) // 4)), (6, 13, 27))
        draw = ImageDraw.Draw(sheet)
        for i, (t, tile) in enumerate(part):
            x, y = (i % 4) * W, (i // 4) * H
            sheet.paste(tile.resize((W - 4, H - 4), Image.LANCZOS), (x + 2, y + 2))
            label = clock(t)
            draw.rectangle([x + 6, y + 6, x + 14 + 14 * len(label), y + 36], fill=(4, 32, 26))
            draw.text((x + 10, y + 8), label, fill=(127, 232, 182), font=font)
        name = f"sheets/sheet-{s // 16 + 1:02d}.jpg"
        sheet.save(folder / name, quality=85)
        sheets.append(name)
    return {"count": len(names), "every_s": round(1 / sb["fps"], 1) if sb and sb["fps"] else None, "files": names,
            "sheets": sheets, "resolution": f"{sb['w']}x{sb['h']} (storyboard stills, enlarged)"}


def frame_at(folder: Path, at: str) -> dict:
    """Save the storyboard still nearest to `at` (mm:ss, h:mm:ss or seconds) for use in the report."""
    parts = [float(p) for p in at.split(":")]
    t = sum(v * 60 ** i for i, v in enumerate(reversed(parts)))
    info = json.loads((folder / ".info.json").read_text(encoding="utf-8"))
    all_tiles, _ = tiles(info, folder)
    if not all_tiles:
        raise ValueError("no storyboard stills for this video")
    tt, tile = min(all_tiles, key=lambda x: abs(x[0] - t))
    return {"status": "ok", "file": save_frame(tile, tt, folder), "time": clock(tt)}


# ───────────── fetch ─────────────

def fetch(url: str, root: Path, want: str = "zh-TW") -> dict:
    vid = video_id(url)
    url = f"https://www.youtube.com/watch?v={vid}"
    info = metadata(url)
    folder = root / vid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".info.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    meta = slim(info)
    tr = transcript(url, info, want, folder)
    meta["transcript"] = write_transcript(folder, meta, tr)
    meta["thumbnail_file"] = thumbnail(info, folder)
    meta["frames"] = frames(info, folder, meta)
    (folder / "source.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"folder": str(folder), "meta": meta}
