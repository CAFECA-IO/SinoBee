"""Deterministic checks of what a task produced, run before SinoBee may call the task done.

No model is involved: files are parsed, pages are opened in headless Chromium the way the document viewer shows
them, and formulas and diagrams are run through the same KaTeX and mermaid the user's screen uses. Each problem is
reported with the file and enough detail to fix it; the agent gets the list and has to fix it before it finishes.

Checks
  every file      exists, not empty, no control characters left by JSON escaping (\\f, \\b … in text files)
  .html .htm      opens without script errors; local images, scripts, styles and data files load; images decode;
                  formulas render (KaTeX errors) and mermaid diagrams draw; the page is not blank
  .md             relative images and links point to files that exist; every $…$ / $$…$$ formula and every
                  ```mermaid block parses (after the same automatic fixes the chat view applies)
  .json           parses
  .csv .tsv       every row has the same number of columns as the header
  .svg            is well-formed XML
Browser checks need Playwright's Chromium; without it they are skipped and the file checks still run.
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

log = logging.getLogger("sinobee.qa")

MAX_FILES = 20
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
TEXT_EXT = {".md", ".markdown", ".html", ".htm", ".svg", ".json", ".csv", ".tsv", ".txt", ".yaml", ".yml", ".srt"}
MD_REF = re.compile(r"(!?)\[[^\]]*\]\((<[^>]+>|[^)\s]+)")
FENCE = re.compile(r"```[\s\S]*?```")
MERMAID = re.compile(r"```mermaid[^\n]*\n([\s\S]*?)```")
MATH = re.compile(r"\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)"
                  r"|(?<![\w$])\$(?![\s$])((?:\\.|[^$\\\n])+?)(?<!\s)\$(?!\d)")
EXTERNAL = re.compile(r"^(?:[a-z][\w+.-]*:|//|#)", re.I)
STATIC = Path(__file__).resolve().parents[1] / "server" / "static"
HOST = "http://sinobee-qa.local/"


def _issue(path: Path, problem: str, detail: str = "") -> dict:
    return {"path": str(path), "name": path.name, "problem": problem, "detail": detail[:400]}


def _ctrl(path: Path, text: str) -> list[dict]:
    m = CTRL.search(text)
    if not m:
        return []
    line = text.count("\n", 0, m.start()) + 1
    near = text[max(0, m.start() - 20):m.start() + 20].replace("\n", " ")
    return [_issue(path, f"第 {line} 行有控制字元 {m.group(0)!r}（通常是 LaTeX 的反斜線在 JSON 裡被吃掉，例如 \\f→換頁）",
                   repr(near))]


def check_file(path: Path) -> list[dict]:
    """Checks that need no browser."""
    if not path.is_file():
        return [_issue(path, "檔案不存在")]
    if path.stat().st_size == 0:
        return [_issue(path, "檔案是空的")]
    ext = path.suffix.lower()
    if ext not in TEXT_EXT or path.stat().st_size > 8 * 1024 * 1024:
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    out = _ctrl(path, text) if ext not in (".csv", ".tsv") else []
    if ext == ".json":
        try:
            json.loads(text)
        except ValueError as e:
            out.append(_issue(path, "JSON 無法解析", str(e)))
    elif ext in (".csv", ".tsv"):
        rows = list(csv.reader(text.splitlines(), delimiter="\t" if ext == ".tsv" else ","))
        if rows:
            n = len(rows[0])
            bad = [i + 1 for i, r in enumerate(rows) if r and len(r) != n][:5]
            if bad:
                out.append(_issue(path, f"有 {len(bad)} 列以上的欄位數和標題列（{n} 欄）不一致", f"第 {bad} 列"))
    elif ext == ".svg":
        import xml.etree.ElementTree as ET
        try:
            ET.fromstring(text)
        except ET.ParseError as e:
            out.append(_issue(path, "SVG 不是正確的 XML", str(e)))
    elif ext in (".md", ".markdown"):
        body = FENCE.sub("", text)
        missing = []
        for bang, ref in MD_REF.findall(body):
            ref = ref.strip("<>")
            if not ref or EXTERNAL.match(ref) or ref.startswith(("/api/", "data:")):
                continue
            raw = unquote(ref.split("#")[0].split("?")[0])
            target = Path(raw).expanduser() if raw.startswith(("/", "~")) else path.parent / raw
            if raw and not target.exists():
                missing.append(("圖片" if bang else "連結") + " " + ref)
        if missing:
            out.append(_issue(path, f"{len(missing)} 個相對路徑找不到檔案", "；".join(missing[:6])))
    return out


def _formulas(text: str) -> list[tuple[str, bool]]:
    body = FENCE.sub("", re.sub(r"`[^`\n]*`", "", text))
    return [((m.group(1) or m.group(2) or m.group(3) or m.group(4)), bool(m.group(1) or m.group(2)))
            for m in MATH.finditer(body)][:200]


def _mmd_fix_js() -> str:
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    try:
        return page[page.index("const MMD_SHAPES"):page.index("let MERMAID")]
    except ValueError:
        return "function mmdFix(s){return s}"


PROBE = """async ([formulas, diagrams]) => {
  const out = [];
  for (const [tex, display] of formulas) {
    try { katex.renderToString(sbMath.fix(tex), {displayMode: display, throwOnError: true, strict: 'ignore'}); }
    catch (e) { out.push(['公式無法解析', (display ? '$$' : '$') + tex.slice(0, 120) + (display ? '$$' : '$') + ' → ' + String(e.message || e).slice(0, 160)]); }
  }
  if (diagrams.length && window.mermaid) {
    mermaid.initialize({startOnLoad: false, securityLevel: 'strict'});
    for (const src of diagrams) {
      try { await mermaid.parse(src); }
      catch (e) { try { await mermaid.parse(mmdFix(src)); }
        catch (e2) { out.push(['Mermaid 圖表語法錯誤', src.split('\\n')[0].slice(0, 60) + ' → ' + String(e2.message || e2).split('\\n').slice(0, 3).join(' ').slice(0, 200)]); } }
    }
  }
  return out;
}"""

PAGE_PROBE = """() => {
  const out = [];
  const imgs = [...document.images].filter(i => i.complete && i.naturalWidth === 0 && i.getAttribute('src'));
  if (imgs.length) out.push(['圖片無法顯示', imgs.slice(0, 5).map(i => i.getAttribute('src')).join('、')]);
  const ke = [...document.querySelectorAll('.katex-error')];
  if (ke.length) out.push([`${ke.length} 個公式無法排版`, ke.slice(0, 3).map(e => (e.getAttribute('title') || e.textContent).slice(0, 120)).join('；')]);
  const mm = [...document.querySelectorAll('svg')].filter(s => /Syntax error in (text|graph)/i.test(s.textContent || ''));
  if (mm.length) out.push(['Mermaid 圖表語法錯誤', `${mm.length} 張圖畫不出來`]);
  // a drawing much larger than its own frame: the camera was not fitted (seen: a 3494 x 2568 drawing in an
  // 800 x 600 viewBox spilled over the whole page)
  const spill = [];
  for (const s of document.querySelectorAll('svg[viewBox]')) {
    const vb = (s.getAttribute('viewBox') || '').trim().split(/[\\s,]+/).map(Number);
    if (vb.length !== 4 || !(vb[2] > 40 && vb[3] > 40)) continue;
    let b; try { b = s.getBBox(); } catch (e) { continue; }
    const over = Math.max(vb[0] - b.x, b.x + b.width - vb[0] - vb[2]) / vb[2];
    const overY = Math.max(vb[1] - b.y, b.y + b.height - vb[1] - vb[3]) / vb[3];
    if (over > 0.25 || overY > 0.25) spill.push(`${Math.round(b.width)}×${Math.round(b.height)} 畫在 ${vb[2]}×${vb[3]} 的畫框裡`);
  }
  if (spill.length) out.push(['圖形超出自己的畫框', spill.slice(0, 3).join('；') + '：鏡頭沒有對準物體（fitProjection 的點）或尺寸單位錯誤']);
  const text = (document.body && document.body.innerText || '').trim();
  const visual = document.querySelectorAll('svg, canvas, img, video').length;
  if (!text && !visual) out.push(['頁面是空白的', '沒有文字也沒有圖形']);
  return out;
}"""


FIG_PROBE = r"""() => {
  // the drawings on a page (svg, canvas, img, video) big enough to judge, with the text that says what they show
  const out = [];
  const seen = new Set();
  const capOf = el => {
    const fig = el.closest('figure');
    const fc = fig && fig.querySelector('figcaption');
    if (fc && fc.innerText.trim()) return fc.innerText.trim();
    let n = el;
    for (let up = 0; n && up < 6; up++, n = n.parentElement) {
      let s = n.previousElementSibling;
      for (let k = 0; s && k < 4; k++, s = s.previousElementSibling) {
        if (/^H[1-4]$/.test(s.tagName) && s.innerText.trim()) return s.innerText.trim();
        const h = s.querySelector && s.querySelector('h1,h2,h3,h4');
        if (h && h.innerText.trim()) return h.innerText.trim();
      }
    }
    return el.getAttribute('aria-label') || el.getAttribute('alt') || el.getAttribute('title') || '';
  };
  for (const el of document.querySelectorAll('svg, canvas, img, video')) {
    if (el.tagName.toLowerCase() === 'svg' && el.parentElement && el.parentElement.closest('svg')) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 200 || r.height < 120) continue;
    const box = el.closest('figure') || el;
    if (seen.has(box)) continue;
    seen.add(box);
    const b = box.getBoundingClientRect();
    out.push({x: Math.max(0, b.left + scrollX), y: Math.max(0, b.top + scrollY),
              w: Math.min(b.width, 1600), h: Math.min(b.height, 1400),
              tag: el.tagName.toLowerCase(), caption: capOf(el).slice(0, 300)});
    if (out.length >= 6) break;
  }
  return out;
}"""


async def _figure_shots(pg, page_path: Path, figures: list) -> None:
    """A full-page screenshot and one per drawing; a failure here only loses the pictures, never the page checks."""
    try:
        height = min(int(await pg.evaluate("document.documentElement.scrollHeight")), 3600)
        figures.append({"path": str(page_path), "label": f"{page_path.name} 全頁", "caption": "",
                        "image": await pg.screenshot(type="jpeg", quality=60, full_page=True,
                                                     clip={"x": 0, "y": 0, "width": 1280, "height": max(height, 100)})})
        for n, f in enumerate(await pg.evaluate(FIG_PROBE), 1):
            await pg.evaluate(f"window.scrollTo(0, {max(0, f['y'] - 100)})")
            await pg.wait_for_timeout(400)          # let an animation draw a frame
            figures.append({"path": str(page_path), "label": f"{page_path.name} 圖 {n}（{f['tag']}）",
                            "caption": f["caption"],
                            "image": await pg.screenshot(type="jpeg", quality=75, full_page=True,
                                                         clip={"x": f["x"], "y": f["y"], "width": max(f["w"], 1),
                                                               "height": max(f["h"], 1)})})
    except Exception as e:  # noqa: BLE001
        log.info("figure screenshots failed for %s: %s", page_path, e)


async def _browser_checks(pages: list[Path], docs: list[tuple[Path, list, list]], shots: dict | None = None,
                          figures: list | None = None) -> list[dict]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return []
    from ..server.files_api import MATH_DIR, wants_math, with_math
    issues: list[dict] = []
    vendor = STATIC / "vendor"
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                for page_path in pages:
                    root = page_path.parent.resolve()
                    errors, failed = [], []
                    ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
                    pg = await ctx.new_page()
                    pg.on("pageerror", lambda e, errors=errors: errors.append(str(e)[:200]))

                    def make_route(root=root, failed=failed):
                        async def route(r):
                            url = r.request.url
                            if not url.startswith(HOST):
                                return await r.abort()                 # the network is not part of the check
                            rel = unquote(urlparse(url).path.lstrip("/"))
                            if rel.startswith(MATH_DIR + "/"):
                                f = (vendor / rel[len(MATH_DIR) + 1:]).resolve()
                                return await (r.fulfill(path=str(f)) if f.is_file() and vendor in f.parents else r.abort())
                            f = (root / rel).resolve()
                            if not f.is_file() or (root != f.parent and root not in f.parents):
                                failed.append(rel)
                                return await r.fulfill(status=404, body="not found")
                            if f.suffix.lower() in (".html", ".htm"):
                                body = f.read_text(encoding="utf-8", errors="replace")
                                if wants_math(body):
                                    body = with_math(body, f"/{MATH_DIR}/")
                                return await r.fulfill(body=body, content_type="text/html; charset=utf-8")
                            return await r.fulfill(path=str(f))
                        return route
                    await pg.route("**/*", make_route())
                    try:
                        await pg.goto(HOST + page_path.name, wait_until="load", timeout=20000)
                        await pg.wait_for_timeout(1500)
                        found = await pg.evaluate(PAGE_PROBE)
                        if shots is not None:          # what the page looks like, for the acceptance review
                            shots[str(page_path)] = await pg.screenshot(type="jpeg", quality=70)
                        if figures is not None:        # every drawing on its own, for the visual judgment
                            await _figure_shots(pg, page_path, figures)
                    except Exception as e:  # noqa: BLE001
                        found = [["頁面無法開啟", str(e)[:200]]]
                    await ctx.close()
                    if errors:
                        issues.append(_issue(page_path, f"頁面執行時有 {len(errors)} 個 JavaScript 錯誤", "；".join(errors[:3])))
                    missing = [x for x in dict.fromkeys(failed) if not x.endswith("favicon.ico")]
                    if missing:
                        issues.append(_issue(page_path, f"{len(missing)} 個本機檔案載入失敗", "、".join(missing[:6])))
                    issues += [_issue(page_path, p, d) for p, d in found]
                if docs:
                    pg = await browser.new_page()
                    scripts = "".join(f"<script>{(vendor / n).read_text(encoding='utf-8')}</script>"
                                      for n in ("katex.min.js", "sb-math.js"))
                    need_mmd = any(d for _, _, d in docs)
                    mmd = f"<script>{(vendor / 'mermaid.min.js').read_text(encoding='utf-8')}</script>" if need_mmd else ""
                    await pg.set_content(f"<html><body>{scripts}{mmd}<script>{_mmd_fix_js()}</script></body></html>")
                    for path, formulas, diagrams in docs:
                        try:
                            found = await pg.evaluate(PROBE, [formulas, diagrams])
                        except Exception as e:  # noqa: BLE001
                            log.info("doc probe failed for %s: %s", path, e)
                            continue
                        issues += [_issue(path, p, d) for p, d in found]
            finally:
                await browser.close()
    except Exception as e:  # noqa: BLE001 - no Chromium: the file checks still count
        log.info("browser checks skipped: %s", e)
    return issues


async def check(paths: list[Path], shots: dict | None = None, figures: list | None = None) -> list[dict]:
    """All problems found in the given files (deduplicated, at most 20 files). `shots` (optional) receives a
    screenshot of each HTML page (path -> JPEG bytes); `figures` (optional) receives a full-page screenshot and one
    per drawing of each page: {"path", "label", "caption", "image"}."""
    files = [p for p in dict.fromkeys(Path(x) for x in paths)][:MAX_FILES]
    issues: list[dict] = []
    pages, docs = [], []
    for p in files:
        issues += await asyncio.to_thread(check_file, p)
        if not p.is_file() or p.stat().st_size > 8 * 1024 * 1024:
            continue
        ext = p.suffix.lower()
        if ext in (".html", ".htm"):
            pages.append(p)
        elif ext in (".md", ".markdown"):
            text = p.read_text(encoding="utf-8", errors="replace")
            formulas, diagrams = _formulas(text), MERMAID.findall(text)[:30]
            if formulas or diagrams:
                docs.append((p, formulas, diagrams))
    if pages or docs:
        issues += await _browser_checks(pages, docs, shots, figures)
    return issues


def report(issues: list[dict], limit: int = 12) -> str:
    """The list for the model (and the result card)."""
    lines = [f"- {i['name']}：{i['problem']}" + (f"（{i['detail']}）" if i.get("detail") else "") for i in issues[:limit]]
    more = len(issues) - limit
    return "\n".join(lines) + (f"\n- …另有 {more} 個問題" if more > 0 else "")
