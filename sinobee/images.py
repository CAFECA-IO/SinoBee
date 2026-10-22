"""Pictures sent to a model must fit what the providers accept.

Anthropic rejects any side over 8000 px (and, with more than 20 images in one request, over 2000 px) and any image
over 5 MB; other providers have similar limits. A full-page screenshot is easily 1280 x 9000. So every picture that
goes to a model passes through here:

- a tall picture is cut into strips of at most TILE px (top to bottom, at most MAX_TILES strips) so text stays legible;
- a picture still too large (too wide, or taller than MAX_TILES strips) is scaled down keeping its proportions;
- a strip whose encoded bytes are still too many is re-encoded as JPEG.

`fit()` does the cutting; `parts()` turns one picture into message parts (a label before each strip);
`fit_content()` rewrites a tool result's parts in place; `shrink()` is the stricter pass used when a provider
rejected a request for its images anyway (agent/loop.py retries once with it).
Without Pillow nothing can be resized: a picture within the limits goes as it is, one beyond them becomes a note.
"""
from __future__ import annotations

import base64
import io
import logging
import math
import struct

from .types import ImagePart, Message, TextPart

log = logging.getLogger(__name__)

TILE = 2000            # longest side of one strip / picture
MAX_TILES = 4          # strips per picture; taller ones are scaled down first
MAX_BYTES = 3_500_000  # raw bytes per strip (base64 grows it by a third; providers cap around 5 MB)
HARD_SIDE = 8000       # what the strictest provider accepts at all

# what a provider says when the pictures were the problem (agent/errors.py explains it, agent/loop.py retries)
IMAGE_ERROR = (r"image[^\n]{0,80}?(?:dimensions?|size|pixels|too (?:large|big)|exceeds?)|"
               r"(?:dimensions?|size)[^\n]{0,40}?image|image\.source|image_url[^\n]{0,60}?(?:large|size|invalid)|"
               r"Could not process image|invalid image|image data")


def size_of(raw: bytes) -> tuple[int, int] | None:
    """Width and height from the header alone (PNG, JPEG, GIF, WebP), or None."""
    try:
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", raw[16:24])
        if raw[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", raw[6:10])
        if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            if raw[12:16] == b"VP8X":
                return 1 + int.from_bytes(raw[24:27], "little"), 1 + int.from_bytes(raw[27:30], "little")
            if raw[12:16] == b"VP8 ":
                w, h = struct.unpack("<HH", raw[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if raw[12:16] == b"VP8L":
                b = int.from_bytes(raw[21:25], "little")
                return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
        if raw[:2] == b"\xff\xd8":
            i = 2
            while i < len(raw) - 9:
                if raw[i] != 0xFF:
                    i += 1
                    continue
                marker = raw[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", raw[i + 5:i + 9])
                    return w, h
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                i += 2 + struct.unpack(">H", raw[i + 2:i + 4])[0]
    except (struct.error, IndexError):
        return None
    return None


def _encode(img, mime: str) -> tuple[bytes, str]:
    """PNG for a PNG (screenshots stay crisp) unless that is too many bytes; JPEG otherwise."""
    if mime == "image/png":
        buf = io.BytesIO()
        img.save(buf, "PNG", compress_level=3)   # fast; size is checked below
        if buf.tell() <= MAX_BYTES:
            return buf.getvalue(), "image/png"
    if img.mode not in ("RGB", "L"):
        from PIL import Image
        bg = img.convert("RGBA")
        flat = Image.new("RGB", bg.size, (255, 255, 255))
        flat.paste(bg, mask=bg.split()[-1])
        img = flat
    for q in (85, 70, 55):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True)
        if buf.tell() <= MAX_BYTES:
            break
    return buf.getvalue(), "image/jpeg"


def fit(raw: bytes, mime: str = "image/png", *, tile: int = TILE, max_tiles: int = MAX_TILES
        ) -> tuple[list[tuple[bytes, str]], dict]:
    """[(bytes, mime), …] strips top to bottom, plus {"size": (w, h), "scaled": factor, "tiles": n, "note": …}.
    An empty list means the picture cannot be sent (too large and no Pillow to make it smaller)."""
    dims = size_of(raw)
    info: dict = {"size": dims, "scaled": 1.0, "tiles": 1}
    fits = dims is not None and max(dims) <= tile and len(raw) <= MAX_BYTES
    if fits:
        return [(raw, mime)], info
    try:
        from PIL import Image
    except ImportError:
        if dims is not None and max(dims) <= HARD_SIDE and len(raw) <= MAX_BYTES:
            return [(raw, mime)], info          # over our comfort size but inside what providers accept
        info["note"] = "圖片太大，且沒有安裝 Pillow 無法縮小（pip install pillow）"
        return [], info
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        if getattr(img, "n_frames", 1) > 1:     # an animation: its first frame
            img.seek(0)
            img = img.convert("RGBA")
        if img.mode not in ("RGB", "RGBA", "L"):
            img = img.convert("RGBA")
    except Exception as e:  # noqa: BLE001
        info["note"] = f"無法讀取圖片：{e}"
        return [], info
    w, h = img.size
    info["size"] = (w, h)
    out_mime = "image/png" if mime in ("image/png", "image/gif", "image/webp") else "image/jpeg"
    s = min(1.0, tile / w)                       # width fits one strip
    if h * s > tile * max_tiles:                 # too tall even in max_tiles strips: smaller still
        s = tile * max_tiles / h
    if s < 1.0:
        img = img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
        info["scaled"] = round(s, 3)
    w2, h2 = img.size
    n = max(1, math.ceil(h2 / tile))
    info["tiles"] = n
    step = math.ceil(h2 / n)
    strips = []
    for k in range(n):
        top = k * step
        part = img.crop((0, top, w2, min(h2, top + step))) if n > 1 else img
        strips.append(_encode(part, out_mime))
    return strips, info


def parts(raw: bytes, mime: str, label: str = "") -> list:
    """One picture as message parts: the label, then each strip (with its place in the picture when cut)."""
    strips, info = fit(raw, mime)
    if not strips:
        return [TextPart(f"{label}\n[{info.get('note', '圖片無法傳送')}]".strip())]
    n = len(strips)
    w, h = info.get("size") or (0, 0)
    out: list = []
    if label:
        out.append(TextPart(label))
    if n == 1:
        if info["scaled"] < 1:
            out.append(TextPart(f"（原圖 {w}×{h}，已等比縮小為 {info['scaled']:.0%} 再傳送）"))
        out.append(ImagePart.from_bytes(*strips[0]))
        return out
    note = f"，並縮小為 {info['scaled']:.0%}" if info["scaled"] < 1 else ""
    out.append(TextPart(f"（原圖 {w}×{h}，太長，自上而下切成 {n} 段{note}依序傳送）"))
    for k, (b, m) in enumerate(strips, 1):
        out += [TextPart(f"第 {k}/{n} 段"), ImagePart.from_bytes(b, m)]
    return out


def _raw(p: ImagePart) -> bytes:
    return base64.b64decode(p.data)


def fit_content(content: list) -> list:
    """A tool result's parts with every picture made to fit (strips labelled). Pictures already small enough are
    passed through untouched (no decode beyond the header)."""
    if not any(isinstance(p, ImagePart) for p in content):
        return content
    out: list = []
    for p in content:
        if not isinstance(p, ImagePart):
            out.append(p)
            continue
        try:
            out += parts(_raw(p), p.media_type)
        except Exception as e:  # noqa: BLE001 - a picture that cannot be fitted must not lose the whole result
            log.warning("image fit failed: %s", e)
            out.append(p)
    return out


def shrink(messages: list[Message], side: int = 1200, keep_last: int = 4) -> int:
    """The provider refused the pictures anyway: every picture becomes one smaller image (no strips) of at most
    `side` px, and only the last `keep_last` pictures stay at all. Returns how many pictures were changed."""
    changed = 0
    seen = 0
    for m in reversed(messages):
        if not isinstance(m.content, list):
            continue
        new = []
        for p in reversed(m.content):
            if not isinstance(p, ImagePart):
                new.append(p)
                continue
            seen += 1
            if seen > keep_last:
                new.append(TextPart("[圖片已移除：模型不接受這麼多或這麼大的圖片]"))
                changed += 1
                continue
            try:
                strips, info = fit(_raw(p), p.media_type, tile=side, max_tiles=1)
            except Exception:  # noqa: BLE001
                strips, info = [], {}
            if not strips:
                new.append(TextPart("[圖片已移除：無法縮小到模型接受的尺寸]"))
                changed += 1
            elif strips[0][0] != _raw(p):
                new.append(ImagePart.from_bytes(*strips[0]))
                changed += 1
            else:
                new.append(p)
        m.content = list(reversed(new))
    return changed
