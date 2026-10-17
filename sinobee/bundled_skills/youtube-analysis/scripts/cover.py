"""Cover image in the SinoBee.ai look: deep navy, a 45° honeycomb of teal hexagonal prisms, a halftone dot field
from navy through teal to mint, the bee mark, and the video's thumbnail on a card.

Input:  <folder>/cover.json  {"title", "tagline", "category", "points": [..3], "verdict": {"label", "tone"}?}
        <folder>/source.json (written by fetch) for channel, duration, date, views, chapters, thumbnail
Output: <folder>/cover.svg and <folder>/cover.png (1600 x 900). PNG needs Playwright's Chromium (SinoBee's Docker
        image has it) or cairosvg; without either only the SVG is written.
"""
from __future__ import annotations

import base64
import html
import json
import math
import random
from pathlib import Path

from categories import CATEGORIES

W, H = 1600, 900
INK = "#e3f3ec"
MUTED = "#86a0a4"
LINE = "#1a2b42"
ACCENT = "#6fe3b0"
TEAL = "#2fb3a0"
NAVY = "#060d1b"
PANEL = "#0b1627"
FONT = "'Noto Sans TC','Noto Sans CJK TC','PingFang TC','Microsoft JhengHei','Inter',system-ui,sans-serif"
MONO = "'JetBrains Mono','DejaVu Sans Mono',ui-monospace,Menlo,monospace"
TONES = {"ok": ACCENT, "warn": "#ffc56b", "err": "#ff7d88", "info": "#9fd4ff"}


def char_w(ch: str) -> float:
    o = ord(ch)
    if o >= 0x2E80 or 0xFF00 <= o <= 0xFFEF:
        return 1.0
    if ch == " ":
        return 0.3
    if ch.isupper() or ch in "MW@%&":
        return 0.64
    return 0.54


def wrap(text: str, size: float, width: float, lines: int) -> list[str]:
    """Greedy wrap by estimated glyph widths; English words stay whole; the last line gets an ellipsis."""
    tokens, buf = [], ""
    for ch in text.strip():
        if ord(ch) >= 0x2E80:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        elif ch == " ":
            if buf:
                tokens.append(buf)
            tokens.append(" ")
            buf = ""
        else:
            buf += ch
    if buf:
        tokens.append(buf)
    # a word wider than the line (a URL, a long name) is cut into pieces that fit
    fitted = []
    for tok in tokens:
        while sum(char_w(c) for c in tok) * size > width and len(tok) > 1:
            n = 1
            while n < len(tok) and sum(char_w(c) for c in tok[: n + 1]) * size <= width:
                n += 1
            fitted.append(tok[:n])
            tok = tok[n:]
        fitted.append(tok)
    tokens = fitted
    out, cur, cur_w = [], "", 0.0
    for tok in tokens:
        w = sum(char_w(c) for c in tok) * size
        if cur_w + w > width and cur.strip():
            out.append(cur.rstrip())
            cur, cur_w = ("" if tok == " " else tok), (0 if tok == " " else w)
        else:
            cur += tok
            cur_w += w
    if cur.strip():
        out.append(cur.rstrip())
    # kinsoku: closing punctuation never starts a line; it hangs at the end of the previous one
    for i in range(1, len(out)):
        while out[i] and out[i][0] in "，。、！？：；」』）》〉,.!?:;)]%":
            out[i - 1] += out[i][0]
            out[i] = out[i][1:].lstrip()
    out = [ln for ln in out if ln]
    if len(out) > lines:
        out = out[:lines]
        last = out[-1]
        while last and sum(char_w(c) for c in last + "…") * size > width:
            last = last[:-1]
        out[-1] = last.rstrip() + "…"
    return out


def fit_title(text: str, width: float, max_lines: int = 3) -> tuple[float, list[str]]:
    for size in (72, 66, 60):
        lines = wrap(text, size, width, 99)
        if len(lines) <= 2:
            return size, lines
    for size in (60, 56, 52, 48):
        lines = wrap(text, size, width, 99)
        if len(lines) <= max_lines:
            return size, lines
    return 48, wrap(text, 48, width, max_lines)


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def clock(seconds: float) -> str:
    s = int(round(seconds or 0))
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def count(n) -> str:
    if not n:
        return ""
    n = int(n)
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f} 億"
    if n >= 10_000_000:
        return f"{n / 10_000:,.0f} 萬"
    if n >= 10_000:
        return f"{n / 10_000:.1f} 萬".replace(".0 ", " ")
    return f"{n:,}"


# ───────────── motifs ─────────────

K = math.sqrt(0.5)  # the honeycomb is seen at 45°: depth foreshortened by cos 45°


def hex_pts(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    return [(cx + r * math.cos(math.radians(60 * i - 90)), cy + r * math.sin(math.radians(60 * i - 90)) * K) for i in range(6)]


def prism(cx: float, cy: float, r: float, depth: float, alpha: float, strong: bool = False) -> str:
    v = hex_pts(cx, cy, r)
    poly = lambda pts: "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + "Z"  # noqa: E731
    out = []
    for i, (a, b) in enumerate([(v[1], v[2]), (v[2], v[3]), (v[3], v[4])]):
        face = [a, b, (b[0], b[1] + depth), (a[0], a[1] + depth)]
        out.append(f'<path d="{poly(face)}" fill="{ACCENT}" fill-opacity="{alpha * (0.5 + i * 0.1):.3f}" '
                   f'stroke="{ACCENT}" stroke-opacity="{alpha * 1.4:.3f}" stroke-width="1"/>')
    out.append(f'<path d="{poly(v)}" fill="{ACCENT}" fill-opacity="{alpha * (0.6 if strong else 0.35):.3f}" '
               f'stroke="{ACCENT}" stroke-opacity="{min(1, alpha * (3 if strong else 2)):.3f}" stroke-width="{1.8 if strong else 1.1}"/>')
    return "".join(out)


def honeycomb(seed: int) -> str:
    rnd = random.Random(seed)
    r = 46
    dx, dy = r * math.sqrt(3), r * 1.5 * K
    cells = []
    for row in range(-1, 22):
        for col in range(-1, 12):
            cx = 760 + col * dx + (dx / 2 if row % 2 else 0)
            cy = -20 + row * dy * 1.32
            # fade in from the left edge of the honeycomb and keep the card area calm
            fx = (cx - 760) / 840
            if fx < 0.02 or rnd.random() < 0.18:
                continue
            alpha = 0.05 + 0.16 * fx * rnd.random()
            lift = rnd.random() * 10
            cells.append((cy, prism(cx, cy - lift, r * (0.9 + rnd.random() * 0.06), 12, alpha)))
    return "".join(svg for _, svg in sorted(cells))


def halftone(seed: int) -> str:
    """dot field from navy (top-left, nothing) through teal to mint (bottom-left corner)"""
    rnd = random.Random(seed + 7)
    stops = [(0, (28, 95, 116)), (0.45, (47, 179, 160)), (0.8, (127, 232, 182)), (1, (201, 247, 222))]

    def color(t: float) -> str:
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if t <= p1:
                k = (t - p0) / (p1 - p0)
                return "#%02x%02x%02x" % tuple(round(a + (b - a) * k) for a, b in zip(c0, c1))
        return "#c9f7de"
    dots = []
    step = 14
    for y in range(700, H + step, step):
        for x in range(0, 900, step):
            d = math.hypot(x / 980, (H - y) / 190)
            t = max(0.0, 1 - d)
            if t <= 0.02:
                continue
            rr = step * 0.42 * t ** 0.8 * (0.75 + 0.5 * rnd.random())
            if rr < 0.5:
                continue
            dots.append(f'<circle cx="{x + (step / 2 if (y // step) % 2 else 0)}" cy="{y}" r="{rr:.1f}" fill="{color(t)}" '
                        f'fill-opacity="{0.08 + 0.32 * t:.2f}"/>')
    return "".join(dots)


BEE = (f'<g transform="translate({{x}} {{y}}) scale(2.2)"><ellipse cx="12" cy="14" rx="5" ry="6" fill="url(#bee)"/>'
       f'<path d="M7.5 12h9M7.5 15.5h9" stroke="{NAVY}" stroke-width="1.8" fill="none"/>'
       f'<path d="M9 8c-3-4-7-2-6 1s5 2 6 1M15 8c3-4 7-2 6 1s-5 2-6 1" stroke="{ACCENT}" stroke-width="1.8" fill="none"/></g>')


def svg(folder: Path) -> str:
    spec = json.loads((folder / "cover.json").read_text(encoding="utf-8"))
    src = json.loads((folder / "source.json").read_text(encoding="utf-8"))
    cat = spec.get("category")
    if cat not in CATEGORIES:
        raise ValueError(f"cover.json category must be one of: {', '.join(CATEGORIES)}")
    zh, en, _ = CATEGORIES[cat]
    seed = sum(map(ord, src.get("id", "x")))
    left, col = 96, 700
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
             "<defs>",
             f'<linearGradient id="bee" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{TEAL}"/><stop offset="1" stop-color="#7fe8b6"/></linearGradient>',
             f'<radialGradient id="glow" cx="0.78" cy="0.28" r="0.62"><stop offset="0" stop-color="#0f3a3f" stop-opacity="0.95"/><stop offset="1" stop-color="{NAVY}" stop-opacity="0"/></radialGradient>',
             '<linearGradient id="shade" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#060d1b" stop-opacity="1"/><stop offset="0.5" stop-color="#060d1b" stop-opacity="0.92"/><stop offset="0.62" stop-color="#060d1b" stop-opacity="0"/></linearGradient>',
             f'<linearGradient id="rail" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{TEAL}"/><stop offset="1" stop-color="#7fe8b6"/></linearGradient>',
             '<clipPath id="card"><rect x="868" y="214" width="636" height="358" rx="18"/></clipPath>',
             '<filter id="soft" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="22"/></filter>',
             "</defs>",
             f'<rect width="{W}" height="{H}" fill="{NAVY}"/>',
             f'<rect width="{W}" height="{H}" fill="url(#glow)"/>',
             honeycomb(seed),
             f'<rect width="{W}" height="{H}" fill="url(#shade)"/>',
             halftone(seed)]
    # eyebrow: bee mark, brand and the report kind
    parts.append(BEE.format(x=left - 4, y=74))
    parts.append(f'<text x="{left + 56}" y="114" font-family="{FONT}" font-size="30" font-weight="700" fill="{INK}">SinoBee.ai</text>')
    parts.append(f'<text x="{left + 224}" y="113" font-family="{MONO}" font-size="20" letter-spacing="2" fill="{ACCENT}">YOUTUBE 影片分析</text>')
    # category chip
    chip = f"{zh} · {en}"
    chip_w = sum(char_w(c) for c in chip) * 24 + 44
    parts.append(f'<rect x="{left}" y="170" width="{chip_w:.0f}" height="46" rx="23" fill="#0f2a35" stroke="{ACCENT}" stroke-opacity="0.55"/>')
    parts.append(f'<text x="{left + 22}" y="201" font-family="{FONT}" font-size="24" font-weight="600" fill="{ACCENT}">{esc(chip)}</text>')
    # title and tagline
    size, lines = fit_title(spec["title"], col)
    y = 262 + size
    for line in lines:
        parts.append(f'<text x="{left}" y="{y:.0f}" font-family="{FONT}" font-size="{size}" font-weight="800" fill="{INK}" letter-spacing="0.5">{esc(line)}</text>')
        y += size * 1.2
    y += 10
    for line in wrap(spec.get("tagline", ""), 30, col, 2):
        parts.append(f'<text x="{left}" y="{y:.0f}" font-family="{FONT}" font-size="30" fill="{MUTED}">{esc(line)}</text>')
        y += 42
    # key points with hexagon bullets
    y += 22
    for point in (spec.get("points") or [])[:3]:
        if y > 800:
            break
        bullet = [(left + 12 + 10 * math.cos(math.radians(60 * i - 90)), y - 9 + 10 * math.sin(math.radians(60 * i - 90))) for i in range(6)]
        parts.append('<path d="M' + "L".join(f"{a:.1f} {b:.1f}" for a, b in bullet) + f'Z" fill="{ACCENT}" fill-opacity="0.18" stroke="{ACCENT}" stroke-width="2"/>')
        for i, line in enumerate(wrap(point, 26, col - 40, 2)):
            parts.append(f'<text x="{left + 40}" y="{y + i * 36:.0f}" font-family="{FONT}" font-size="26" fill="{INK}" fill-opacity="0.9">{esc(line)}</text>')
        y += 36 * min(2, len(wrap(point, 26, col - 40, 2))) + 18
    # thumbnail card
    parts.append(f'<rect x="878" y="236" width="636" height="358" rx="18" fill="#000" fill-opacity="0.55" filter="url(#soft)"/>')
    thumb = folder / (src.get("thumbnail_file") or "thumbnail.jpg")
    if thumb.exists():
        data = base64.b64encode(thumb.read_bytes()).decode()
        parts.append(f'<image href="data:image/jpeg;base64,{data}" x="868" y="214" width="636" height="358" preserveAspectRatio="xMidYMid slice" clip-path="url(#card)"/>')
    else:
        parts.append(f'<rect x="868" y="214" width="636" height="358" rx="18" fill="{PANEL}"/>')
    parts.append(f'<rect x="868" y="214" width="636" height="358" rx="18" fill="none" stroke="{ACCENT}" stroke-opacity="0.7" stroke-width="2"/>')
    # play mark on the card
    parts.append(f'<g transform="translate(1186 393)"><circle r="44" fill="{NAVY}" fill-opacity="0.72" stroke="{ACCENT}" stroke-width="2"/>'
                 f'<path d="M-13 -20L22 0L-13 20Z" fill="{ACCENT}"/></g>')
    verdict = spec.get("verdict")
    if verdict and verdict.get("label"):
        tone = TONES.get(verdict.get("tone", "info"), TONES["info"])
        text = f"查核：{verdict['label']}"
        vw = sum(char_w(c) for c in text) * 24 + 40
        parts.append(f'<rect x="{1504 - vw:.0f}" y="160" width="{vw:.0f}" height="42" rx="10" fill="{NAVY}" stroke="{tone}" stroke-width="2"/>')
        parts.append(f'<text x="{1504 - vw + 20:.0f}" y="189" font-family="{FONT}" font-size="24" font-weight="700" fill="{tone}">{esc(text)}</text>')
    # channel and numbers under the card
    meta = [src.get("channel") or src.get("uploader") or "", clock(src.get("duration") or 0), src.get("date") or "",
            (count(src.get("view_count")) + " 次觀看") if src.get("view_count") else ""]
    meta_line = " · ".join(m for m in meta if m)
    parts.append(f'<text x="868" y="636" font-family="{FONT}" font-size="24" fill="{INK}" fill-opacity="0.92">{esc(wrap(meta_line, 24, 636, 1)[0] if meta_line else "")}</text>')
    # timeline with chapter ticks
    duration = src.get("duration") or 0
    x0, x1, ty = 868, 1504, 690
    parts.append(f'<rect x="{x0}" y="{ty - 3}" width="{x1 - x0}" height="6" rx="3" fill="{LINE}"/>')
    parts.append(f'<rect x="{x0}" y="{ty - 3}" width="{x1 - x0}" height="6" rx="3" fill="url(#rail)" fill-opacity="0.85"/>')
    chapters = src.get("chapters") or []
    for c in chapters:
        if duration and 0 < c["start"] < duration:
            x = x0 + (x1 - x0) * c["start"] / duration
            parts.append(f'<rect x="{x - 1.5:.1f}" y="{ty - 11}" width="3" height="22" rx="1.5" fill="{NAVY}"/>')
            parts.append(f'<rect x="{x - 1:.1f}" y="{ty - 10}" width="2" height="20" rx="1" fill="{ACCENT}" fill-opacity="0.7"/>')
    parts.append(f'<text x="{x0}" y="{ty + 40}" font-family="{MONO}" font-size="18" fill="{MUTED}">0:00</text>')
    parts.append(f'<text x="{x1}" y="{ty + 40}" text-anchor="end" font-family="{MONO}" font-size="18" fill="{MUTED}">{clock(duration)}</text>')
    if chapters:
        parts.append(f'<text x="{(x0 + x1) / 2}" y="{ty + 40}" text-anchor="middle" font-family="{FONT}" font-size="18" fill="{MUTED}">{len(chapters)} 個章節</text>')
    parts.append("</svg>")
    return "".join(parts)


def render(folder: Path) -> dict:
    markup = svg(folder)
    (folder / "cover.svg").write_text(markup, encoding="utf-8")
    png = folder / "cover.png"
    how = None
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            page.set_content(f'<html><body style="margin:0;background:{NAVY}">{markup}</body></html>')
            page.wait_for_timeout(250)
            page.screenshot(path=str(png), clip={"x": 0, "y": 0, "width": W, "height": H})
            browser.close()
        how = "chromium"
    except Exception as e:  # noqa: BLE001
        err = str(e)
        try:
            import cairosvg
            cairosvg.svg2png(bytestring=markup.encode(), write_to=str(png), output_width=W, output_height=H)
            how = "cairosvg"
        except Exception:  # noqa: BLE001
            return {"cover": "cover.svg", "png": None, "note": f"no PNG renderer ({err[:160]}); the report can use cover.svg"}
    return {"cover": "cover.png", "svg": "cover.svg", "renderer": how}
