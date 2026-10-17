"""Documents that skills make (Markdown reports, interactive HTML pages) as share bundles, plus the description
and the 1200×630 thumbnail that social platforms show for a share link (Open Graph / X cards).

A bundle is the entry file and the files it uses, kept at their paths relative to the entry's folder:
Markdown images and links, HTML src/href/srcset/poster, CSS url(), and quoted relative file names in scripts
that exist on disk. Only files inside the entry's folder are taken (never ../), so a report next to unrelated
files shares just what it shows. Text files go through the same redaction as conversations.
"""
from __future__ import annotations

import base64
import html as htmlmod
import math
import mimetypes
import random
import re
from pathlib import Path
from urllib.parse import unquote

DOC_EXT = {".md", ".markdown", ".html", ".htm", ".svg", ".xhtml"}
TEXT_EXT = {".md", ".markdown", ".html", ".htm", ".svg", ".xhtml", ".css", ".js", ".mjs", ".json", ".txt", ".csv"}
MAX_FILES = 40
MAX_FILE = 25 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024

MD_REF = re.compile(r"!?\[[^\]]*\]\((<[^>]+>|[^)\s]+)")
HTML_REF = re.compile(r"""\b(?:src|href|poster|data-src)\s*=\s*(?:"([^"#]+)|'([^'#]+)'|([^\s"'>#]+))""", re.I)
SRCSET = re.compile(r"""\bsrcset\s*=\s*["']([^"']+)""", re.I)
CSS_URL = re.compile(r"""url\(\s*["']?([^"')]+)""", re.I)
JS_STR = re.compile(r"""["'`]((?:\./)?[\w\-./%]+\.(?:png|jpe?g|gif|webp|svg|json|csv|txt|mp4|webm|mp3|wav|glb|gltf|bin|css|js|mjs|wasm|woff2?))["'`]""", re.I)
EXTERNAL = re.compile(r"^(?:[a-z][\w+.-]*:|//|#)", re.I)


class DocError(ValueError):
    pass


def _refs(path: Path, text: str) -> list[str]:
    ext = path.suffix.lower()
    if ext in (".md", ".markdown"):
        return [m.group(1).strip("<>") for m in MD_REF.finditer(text)]
    out: list[str] = []
    if ext in (".html", ".htm", ".xhtml", ".svg"):
        out += [m.group(1) or m.group(2) or m.group(3) for m in HTML_REF.finditer(text)]
        for m in SRCSET.finditer(text):
            out += [p.strip().split(" ")[0] for p in m.group(1).split(",") if p.strip()]
    if ext in (".html", ".htm", ".xhtml", ".svg", ".css"):
        out += [m.group(1) for m in CSS_URL.finditer(text)]
    if ext in (".html", ".htm", ".xhtml", ".js", ".mjs"):
        out += [m.group(1) for m in JS_STR.finditer(text)]
    return out


def collect(entry: Path) -> list[dict]:
    """[{name: path relative to the entry's folder, path, mime, size}], entry first."""
    root = entry.parent.resolve()
    files: list[dict] = []
    seen: set[Path] = set()
    total = 0
    queue = [entry.resolve()]
    while queue and len(files) < MAX_FILES:
        p = queue.pop(0)
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        size = p.stat().st_size
        if size > MAX_FILE or total + size > MAX_TOTAL:
            continue
        total += size
        rel = p.relative_to(root).as_posix()
        files.append({"name": rel, "path": str(p), "mime": mimetypes.guess_type(p.name)[0] or "application/octet-stream", "size": size})
        if p.suffix.lower() not in TEXT_EXT or p.suffix.lower() in (".json", ".txt", ".csv") or size > 8 * 1024 * 1024:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for ref in _refs(p, text):
            ref = unquote(ref.split("?")[0].split("#")[0]).strip()
            if not ref or EXTERNAL.match(ref) or ref.startswith(("/", "~", "data:")):
                continue
            try:
                q = (p.parent / ref).resolve()
            except (OSError, ValueError):
                continue
            if q.is_file() and q not in seen and str(q).startswith(str(root) + "/") and not q.name.startswith("."):
                queue.append(q)
    return files


# ───────────── title and description ─────────────
def _plain(md: str) -> str:
    t = re.sub(r"```[\s\S]*?```", " ", md)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[*_`>#|]+", " ", t)
    t = re.sub(r"(?<![A-Za-z0-9])\$\$?([^$\n]+?)\$\$?", lambda m: _tex_text(m.group(1)), t)
    return re.sub(r"\s+", " ", t).strip()


TEX = {"circ": "°", "times": "×", "cdot": "·", "pm": "±", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "neq": "≠", "approx": "≈",
       "to": "→", "rightarrow": "→", "infty": "∞", "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "Delta": "Δ", "theta": "θ",
       "lambda": "λ", "mu": "μ", "pi": "π", "sigma": "σ", "Sigma": "Σ", "omega": "ω", "Omega": "Ω", "sqrt": "√", "sum": "Σ", "degree": "°"}


def _tex_text(tex: str) -> str:
    """A formula as plain text for previews: $120^\\circ$ → 120°, \\frac{P}{\\omega} → P/ω."""
    t = re.sub(r"\^\s*\{?\\circ\}?", "°", tex)
    t = re.sub(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"\1/\2", t)
    t = re.sub(r"\\([A-Za-z]+)", lambda m: TEX.get(m.group(1), ""), t)
    t = re.sub(r"[{}]|\\[,;!: ]", "", t)
    return t.replace("^", "").replace("_", "")


def clip(s: str, n: int = 160) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip("，。、,.;； ") + "…"


def describe_md(text: str) -> tuple[str, str]:
    title = next((m.group(1).strip() for m in re.finditer(r"^#\s+(.+)$", text, re.M)), "")
    quote = re.search(r"^>\s?(.+(?:\n>\s?.+)*)", text, re.M)
    if quote:
        desc = _plain(re.sub(r"^>\s?", "", quote.group(1), flags=re.M))
    else:
        paras = [p for p in re.split(r"\n\s*\n", re.sub(r"^#.*$", "", text, flags=re.M)) if _plain(p) and not p.lstrip().startswith(("|", "!", "```", "-"))]
        desc = _plain(paras[0]) if paras else ""
    return _plain(title), clip(desc)


def describe_html(text: str) -> tuple[str, str]:
    def meta(name: str) -> str:
        m = re.search(r"""<meta[^>]+(?:name|property)\s*=\s*["']%s["'][^>]*content\s*=\s*["']([^"']*)""" % re.escape(name), text, re.I) or \
            re.search(r"""<meta[^>]+content\s*=\s*["']([^"']*)["'][^>]*(?:name|property)\s*=\s*["']%s["']""" % re.escape(name), text, re.I)
        return htmlmod.unescape(m.group(1)).strip() if m else ""
    t = re.search(r"<title[^>]*>([\s\S]*?)</title>", text, re.I)
    title = meta("og:title") or (htmlmod.unescape(t.group(1)).strip() if t else "")
    desc = meta("og:description") or meta("description")
    if not desc:
        body = re.sub(r"<(script|style|svg|noscript)[\s\S]*?</\1>", " ", text, flags=re.I)
        blocks = re.sub(r"</?(?:p|div|li|dd|dt|h[1-6]|figcaption|section|article|br|tr|span|ul|ol|dl|small)\b[^>]*>", "\n", body, flags=re.I)
        lines = [re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", x))).strip() for x in blocks.split("\n")]
        # a sentence, not a row of labels: prefer the first long line that has sentence punctuation
        desc = next((x for x in lines if len(x) >= 40 and re.search(r"[。！？]|[.!?](\s|$)", x)), "") or max(lines, key=len, default="")
    return re.sub(r"\s+", " ", title)[:160], clip(desc)


def describe(entry: Path) -> tuple[str, str]:
    text = entry.read_text(encoding="utf-8", errors="replace")
    title, desc = describe_md(text) if entry.suffix.lower() in (".md", ".markdown") else describe_html(text)
    return title or entry.stem, desc


def describe_chat(messages: list[dict]) -> str:
    first = next((m["text"] for m in messages if m.get("role") == "assistant" and (m.get("text") or "").strip()), "")
    return clip(_plain(first))


# ───────────── thumbnail (1200 × 630, the SinoBee.ai look) ─────────────
W, H = 1200, 630
INK, MUTED, ACCENT, TEAL, NAVY = "#e3f3ec", "#86a0a4", "#6fe3b0", "#2fb3a0", "#060d1b"
FONT = "'Noto Sans TC','Noto Sans CJK TC','PingFang TC','Microsoft JhengHei','Inter',system-ui,sans-serif"
MONO = "'JetBrains Mono','DejaVu Sans Mono',ui-monospace,Menlo,monospace"


def _cw(ch: str) -> float:
    o = ord(ch)
    return 1.0 if o >= 0x2E80 else 0.3 if ch == " " else 0.64 if ch.isupper() else 0.55


def _wrap(text: str, size: float, width: float, lines: int) -> list[str]:
    out, cur, w = [], "", 0.0
    for tok in re.findall(r"[A-Za-z0-9][\w.'’-]*|\s+|.", text.strip()):
        tw = sum(_cw(c) for c in tok) * size
        if w + tw > width and cur.strip():
            out.append(cur.rstrip())
            cur, w = ("" if tok.isspace() else tok), (0 if tok.isspace() else tw)
        else:
            cur, w = cur + tok, w + tw
    if cur.strip():
        out.append(cur.rstrip())
    for i in range(1, len(out)):
        while out[i] and out[i][0] in "，。、！？：；」』）》,.!?:;)]%":
            out[i - 1], out[i] = out[i - 1] + out[i][0], out[i][1:].lstrip()
    out = [x for x in out if x]
    if len(out) > lines:
        out, last = out[:lines], out[lines - 1]
        while last and sum(_cw(c) for c in last + "…") * size > width:
            last = last[:-1]
        out[-1] = last.rstrip() + "…"
    return out


def _e(s: str) -> str:
    return htmlmod.escape(s, quote=True)


def _honeycomb(seed: int, x0: float) -> str:
    rnd, r, k = random.Random(seed), 34, math.sqrt(0.5)
    dx, dy, cells = r * math.sqrt(3), r * 1.5 * k * 1.32, []
    for row in range(-1, 22):
        for col in range(-1, 14):
            cx, cy = x0 + col * dx + (dx / 2 if row % 2 else 0), -10 + row * dy
            fx = (cx - x0) / (W - x0 + 1)
            if fx < 0.03 or rnd.random() < 0.2:
                continue
            a = 0.05 + 0.15 * fx * rnd.random()
            pts = [(cx + r * math.cos(math.radians(60 * i - 90)), cy + r * math.sin(math.radians(60 * i - 90)) * k) for i in range(6)]
            d = "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + "Z"
            side = "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in [pts[1], pts[2], pts[3], pts[4], (pts[4][0], pts[4][1] + 9), (pts[3][0], pts[3][1] + 9), (pts[2][0], pts[2][1] + 9), (pts[1][0], pts[1][1] + 9)]) + "Z"
            cells.append(f'<path d="{side}" fill="{ACCENT}" fill-opacity="{a * 0.55:.3f}"/><path d="{d}" fill="{ACCENT}" fill-opacity="{a * 0.4:.3f}" stroke="{ACCENT}" stroke-opacity="{min(1, a * 2):.3f}"/>')
    return "".join(cells)


def _halftone(seed: int) -> str:
    rnd, dots, step = random.Random(seed + 3), [], 12
    for y in range(470, H + step, step):
        for x in range(0, 700, step):
            t = max(0.0, 1 - math.hypot(x / 760, (H - y) / 150))
            rr = step * 0.42 * t ** 0.8 * (0.75 + 0.5 * rnd.random())
            if t > 0.02 and rr >= 0.5:
                g = (round(47 + 150 * t), round(140 + 100 * t), round(150 + 60 * t))
                dots.append(f'<circle cx="{x + (step / 2 if (y // step) % 2 else 0)}" cy="{y}" r="{rr:.1f}" fill="#%02x%02x%02x" fill-opacity="{0.08 + 0.3 * t:.2f}"/>' % g)
    return "".join(dots)


def card_svg(title: str, description: str, label: str, image: bytes | None = None, image_mime: str = "image/png") -> str:
    """The share card: brand, kind label, title and description on the left; the page itself (a screenshot or the
    report's first image) on a card at the right when there is one."""
    seed = sum(map(ord, title or "x"))
    has_img = bool(image)
    col = 560 if has_img else 1000
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}"><defs>',
         f'<linearGradient id="bee" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{TEAL}"/><stop offset="1" stop-color="#7fe8b6"/></linearGradient>',
         f'<radialGradient id="glow" cx="0.8" cy="0.25" r="0.65"><stop offset="0" stop-color="#0f3a3f" stop-opacity="0.95"/><stop offset="1" stop-color="{NAVY}" stop-opacity="0"/></radialGradient>',
         f'<linearGradient id="shade" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{NAVY}"/><stop offset="{0.5 if has_img else 0.62}" stop-color="{NAVY}" stop-opacity="0.9"/><stop offset="{0.62 if has_img else 0.8}" stop-color="{NAVY}" stop-opacity="0"/></linearGradient>',
         '<clipPath id="card"><rect x="660" y="132" width="460" height="366" rx="16"/></clipPath>',
         '<filter id="soft" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="18"/></filter></defs>',
         f'<rect width="{W}" height="{H}" fill="{NAVY}"/><rect width="{W}" height="{H}" fill="url(#glow)"/>',
         _honeycomb(seed, 560), f'<rect width="{W}" height="{H}" fill="url(#shade)"/>', _halftone(seed),
         f'<g transform="translate(66 52) scale(1.8)"><ellipse cx="12" cy="14" rx="5" ry="6" fill="url(#bee)"/>'
         f'<path d="M7.5 12h9M7.5 15.5h9" stroke="{NAVY}" stroke-width="1.8" fill="none"/>'
         f'<path d="M9 8c-3-4-7-2-6 1s5 2 6 1M15 8c3-4 7-2 6 1s-5 2-6 1" stroke="{ACCENT}" stroke-width="1.8" fill="none"/></g>',
         f'<text x="114" y="90" font-family="{FONT}" font-size="26" font-weight="700" fill="{INK}">SinoBee.ai</text>']
    if label:
        lw = sum(_cw(c) for c in label) * 17 + 32
        p.append(f'<rect x="262" y="66" width="{lw:.0f}" height="32" rx="16" fill="#0f2a35" stroke="{ACCENT}" stroke-opacity="0.55"/>'
                 f'<text x="278" y="88" font-family="{FONT}" font-size="17" font-weight="600" fill="{ACCENT}">{_e(label)}</text>')
    size = 56
    for size in (60, 54, 48, 42, 38):
        lines = _wrap(title, size, col, 99)
        if len(lines) <= 3:
            break
    lines = _wrap(title, size, col, 3)
    y = 168 + size
    for ln in lines:
        p.append(f'<text x="70" y="{y:.0f}" font-family="{FONT}" font-size="{size}" font-weight="800" fill="{INK}">{_e(ln)}</text>')
        y += size * 1.22
    y += 14
    for ln in _wrap(description, 25, col, 4 if y < 400 else 3):
        if y > 560:
            break
        p.append(f'<text x="70" y="{y:.0f}" font-family="{FONT}" font-size="25" fill="{MUTED}">{_e(ln)}</text>')
        y += 38
    if has_img:
        data = base64.b64encode(image).decode()
        p.append('<rect x="668" y="150" width="460" height="366" rx="16" fill="#000" fill-opacity="0.55" filter="url(#soft)"/>'
                 '<rect x="660" y="132" width="460" height="366" rx="16" fill="#0b1627"/>'
                 f'<image href="data:{image_mime};base64,{data}" x="660" y="132" width="460" height="366" preserveAspectRatio="xMidYMid slice" clip-path="url(#card)"/>'
                 f'<rect x="660" y="132" width="460" height="366" rx="16" fill="none" stroke="{ACCENT}" stroke-opacity="0.7" stroke-width="2"/>')
    p.append(f'<text x="70" y="596" font-family="{MONO}" font-size="16" letter-spacing="1.5" fill="{MUTED}">sinobee.ai</text></svg>')
    return "".join(p)


def _browser():
    from playwright.sync_api import sync_playwright
    return sync_playwright()


def render_png(markup: str) -> bytes | None:
    try:
        with _browser() as pw:
            b = pw.chromium.launch()
            try:
                pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
                pg.set_content(f'<html><body style="margin:0;background:{NAVY}">{markup}</body></html>')
                pg.wait_for_timeout(200)
                return pg.screenshot(type="png", clip={"x": 0, "y": 0, "width": W, "height": H})
            finally:
                b.close()
    except Exception:  # noqa: BLE001 - no Chromium: try cairosvg
        try:
            import cairosvg
            return cairosvg.svg2png(bytestring=markup.encode(), output_width=W, output_height=H)
        except Exception:  # noqa: BLE001
            return None


def screenshot(page: Path) -> bytes | None:
    """What the HTML page looks like (its first screen), for the card. Only local files load."""
    try:
        with _browser() as pw:
            b = pw.chromium.launch()
            try:
                pg = b.new_page(viewport={"width": 1280, "height": 1018}, device_scale_factor=1)
                pg.route("**/*", lambda r: r.continue_() if r.request.url.startswith(("file:", "data:", "blob:")) else r.abort())
                pg.goto(page.resolve().as_uri(), wait_until="load", timeout=15000)
                pg.wait_for_timeout(1200)
                # the figure itself (the biggest svg / canvas / video / img on the first screens), not the page margins
                box = pg.evaluate("""() => { let best = null, area = 0;
                  for (const el of document.querySelectorAll('svg, canvas, video, img, figure')) {
                    const r = el.getBoundingClientRect(); if (r.top > 1400 || r.width < 240 || r.height < 160) continue;
                    if (el.tagName.toLowerCase() === 'svg' && el.parentElement && el.parentElement.closest('svg')) continue;
                    const a = r.width * r.height; if (a > area) { area = a; best = {x: r.left, y: r.top + scrollY, width: r.width, height: r.height}; } }
                  return best; }""")
                if box and box["width"] * box["height"] > 120000:
                    pad = 16
                    clip = {"x": max(0, box["x"] - pad), "y": max(0, box["y"] - pad), "width": min(1280, box["width"] + 2 * pad),
                            "height": min(1600, box["height"] + 2 * pad)}
                    return pg.screenshot(type="jpeg", quality=85, clip=clip, full_page=True)
                return pg.screenshot(type="jpeg", quality=82)
            finally:
                b.close()
    except Exception:  # noqa: BLE001
        return None


def first_image(files: list[dict]) -> dict | None:
    """A Markdown report's first image (youtube-analysis puts its cover first)."""
    return next((f for f in files[1:] if f["mime"] in ("image/png", "image/jpeg", "image/webp") and f["size"] <= 8 * 1024 * 1024), None)


def fit_cover(img: bytes, mime: str) -> bytes | None:
    """A ready-made cover (already a designed 16:9 picture) is cropped to 1200×630 as is."""
    data = base64.b64encode(img).decode()
    markup = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
              f'<image href="data:{mime};base64,{data}" width="{W}" height="{H}" preserveAspectRatio="xMidYMid slice"/></svg>')
    return render_png(markup)


def thumbnail(*, title: str, description: str, label: str, entry: Path | None = None, files: list[dict] | None = None,
              image: tuple[bytes, str] | None = None) -> bytes | None:
    if entry is not None:
        ext = entry.suffix.lower()
        if ext in (".md", ".markdown"):
            f = first_image(files or [])
            if f:
                img = (Path(f["path"]).read_bytes(), f["mime"])
                if re.search(r"cover|封面|thumb", Path(f["name"]).stem, re.I):
                    return fit_cover(*img)
                image = image or img
        elif ext in (".html", ".htm", ".xhtml", ".svg"):
            shot = screenshot(entry)
            if shot:
                image = (shot, "image/jpeg")
    return render_png(card_svg(title, description, label, *(image or (None, "image/png"))))
