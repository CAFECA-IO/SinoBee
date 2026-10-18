"""Look at what was made before calling it done.

The deterministic checks (qa.py) catch script errors, broken files and drawings that spill out of their frame, but
not a figure that is blank, unreadable, mislabelled, in the wrong language or showing something other than what its
caption claims. The thorium run of 2026-10-19 delivered a page whose only figure covered the whole page, and the
Roadster run animated a car whose thrust arrows pointed the wrong way; both passed every structural check.

Here every produced page is screenshotted (the full page, then each drawing on its own, with the heading or caption
that says what it shows), image files are added as they are, the first pages of produced PDF / Word / PowerPoint / Excel files are
rendered (LibreOffice + pdftoppm when installed), and a vision model judges each picture against the
request. Only blocking problems go back to the agent; a judgment that cannot run (no vision model, timeout) is
reported as skipped, never as passed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from pathlib import Path

log = logging.getLogger("sinobee.visual")

IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
             ".gif": "image/gif"}
MAX_SHOTS = 10
DOC_EXT = {".pdf", ".docx", ".pptx", ".xlsx"}
MAX_IMAGE = 4 * 1024 * 1024

RUBRIC = """You are checking the pictures a task produced, as the user will see them, before the task may be called done.

USER REQUEST:
{query}

The user's language is {lang}. For each numbered picture below decide whether it is acceptable. A picture is NOT
acceptable (severity "blocking") when any of these is true:
1. it is blank, black, cut off, or a drawing covers / overflows the page or other content;
2. text or labels overlap, are clipped, or are too small to read at the size shown;
3. it does not show what its caption or heading says it shows, or shows something unrelated to the request;
4. the content is physically or technically wrong in a way visible in the picture: forces, arrows, flows or motion
   pointing the wrong way, impossible geometry or proportions, axes without units, numbers on the picture that
   contradict each other or the caption, a curve with the wrong shape for what it claims (e.g. a pressure that
   rises while the tank empties);
5. it still shows template, placeholder or example text, or visible text is in a language other than {lang}
   (names, units and code may stay as they are).
Smaller problems that do not mislead the user are "minor". Judge only what is visible; do not guess about what is
off-screen. Be specific: say what is wrong and where.

Reply with JSON only:
{{"items": [{{"shot": 1, "ok": true|false, "severity": "blocking"|"minor"|"", "problem": "...", "fix": "..."}}]}}
Write problem and fix in {lang}."""


def image_files(paths: list[Path]) -> list[dict]:
    """Produced image files, as pictures to judge."""
    out = []
    for p in paths:
        p = Path(p)
        mime = IMAGE_EXT.get(p.suffix.lower())
        if not mime or not p.is_file() or p.stat().st_size > MAX_IMAGE:
            continue
        out.append({"path": str(p), "label": p.name, "caption": "", "image": p.read_bytes(), "mime": mime})
    return out


async def collect(paths: list[Path]) -> list[dict]:
    """Screenshots of every produced page (full page + each drawing) and the produced image files."""
    from . import qa
    figures: list = []
    pages = [Path(p) for p in paths if Path(p).suffix.lower() in (".html", ".htm")][:3]
    svgs = [Path(p) for p in paths if Path(p).suffix.lower() == ".svg"][:3]
    if pages:
        await qa.check(pages, figures=figures)
    for svg in svgs:
        figures += await _render_svg(svg)
    docs = [Path(p) for p in paths if Path(p).suffix.lower() in DOC_EXT][:3]
    for doc in docs:
        figures += await asyncio.to_thread(_render_doc, doc)
    figures += image_files(paths)
    seen, out = set(), []
    for f in figures:
        h = hashlib.sha256(f["image"]).hexdigest()
        if h not in seen:
            seen.add(h)
            out.append({**f, "sha": h})
    return out[:MAX_SHOTS]


async def _render_svg(path: Path) -> list[dict]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return []
    try:
        import base64
        data = base64.b64encode(path.read_bytes()).decode()
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                pg = await browser.new_page(viewport={"width": 1280, "height": 900})
                await pg.set_content(f'<body style="margin:0;background:#fff"><img id="i" style="max-width:1280px" '
                                     f'src="data:image/svg+xml;base64,{data}"></body>')
                await pg.wait_for_timeout(300)
                img = await pg.locator("#i").screenshot(type="jpeg", quality=75)
            finally:
                await browser.close()
        return [{"path": str(path), "label": path.name, "caption": "", "image": img}]
    except Exception as e:  # noqa: BLE001
        log.info("svg render failed for %s: %s", path, e)
        return []


def _render_doc(path: Path) -> list[dict]:
    """The first pages of a produced PDF / Word / PowerPoint / Excel file, as the reader will see them."""
    import tempfile
    from ..docread import render_pages
    with tempfile.TemporaryDirectory() as tmp:
        try:
            pages = render_pages(path, Path(tmp), pages=4)
        except Exception as e:  # noqa: BLE001 - no LibreOffice / poppler: those pages are not judged
            log.info("page render failed for %s: %s", path, e)
            return []
        return [{"path": str(path), "label": f"{path.name} 第 {n} 頁", "caption": "", "image": p.read_bytes(),
                 "mime": "image/png"} for n, p in enumerate(pages, 1)]


def parse(text: str) -> list[dict]:
    m = re.search(r"\{[\s\S]*\}", text or "")
    if not m:
        raise ValueError("no JSON in the visual judgment")
    data = json.loads(m.group(0))
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("visual judgment has no items")
    return [i for i in items if isinstance(i, dict)]


def to_issues(shots: list[dict], items: list[dict]) -> list[dict]:
    """Blocking verdicts as gate issues (same shape as qa issues)."""
    out = []
    for it in items:
        try:
            n = int(it.get("shot", 0))
        except (TypeError, ValueError):
            continue
        if it.get("ok") is not False or str(it.get("severity", "")).lower() != "blocking" or not 1 <= n <= len(shots):
            continue
        s = shots[n - 1]
        out.append({"path": s["path"], "name": s["label"], "problem": "視覺檢查：" + str(it.get("problem", "")).strip()[:300],
                    "detail": str(it.get("fix", "")).strip()[:300]})
    return out


def message(query: str, lang: str, shots: list[dict]):
    from ..types import ImagePart, Message, TextPart
    parts: list = [TextPart(RUBRIC.format(query=query[:2000], lang=lang))]
    for n, s in enumerate(shots, 1):
        cap = f"；圖說／標題：「{s['caption']}」" if s.get("caption") else ""
        parts += [TextPart(f"Picture {n}: {s['label']}{cap}"),
                  ImagePart.from_bytes(s["image"], s.get("mime", "image/jpeg"))]
    return Message("user", parts)


async def judge(models: list, query: str, lang: str, shots: list[dict], deadline: float | None,
                on_fallback=None) -> list[dict]:
    """Ask the first model that answers in time; raises when none does (the caller reports it as skipped)."""
    msg = message(query, lang, shots)
    last: Exception | None = None
    for i, model in enumerate(models):
        try:
            resp = await asyncio.wait_for(model.chat([msg], None, temperature=0, max_tokens=1500), deadline)
            return parse(resp.message.text())
        except Exception as e:  # noqa: BLE001
            last = e
            if on_fallback and i + 1 < len(models):
                on_fallback(model, e)
    raise last or RuntimeError("no vision model")
