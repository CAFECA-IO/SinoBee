"""The screen of a trial's web page, as its user would see it (evaluation report design R3, 2026-10-21).

A judge that only reads source code cannot tell whether a page draws anything, whether its animation moves or
whether it fits a phone: the anatomy run of 2026-10-21 passed "has interactive or animation controls" from code
alone. After a trial, every HTML output (up to two) is opened in a headless browser that can reach nothing but the
trial's own files - a fake origin serves the trial folder, every other request is refused, as the trial ran without
a network - and is photographed:

    desktop-load    1280x800, half a second after load
    desktop-later   the same view three seconds later (an animation shows as a difference)
    phone           390x844 (overflow shows)

Facts for the deterministic `page` grader come from the same visit: script errors, whether the picture changed,
visible controls, whether anything is drawn, horizontal overflow on a phone. The same pictures go to the judge for
rubrics marked `screens: true` and into the report's 評鑑截圖 tab, so the reader sees what the judge saw.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
from pathlib import Path

log = logging.getLogger("sinobee.evals")

ORIGIN = "http://trial.sinobee.invalid"
MAX_PAGES = 2
SHOTS = (("desktop-load", "桌面 · 載入後 0.5 秒", 1280, 800, 500),
         ("desktop-later", "桌面 · 3 秒後", 1280, 800, 3000),
         ("phone", "手機 · 載入後 1 秒", 390, 844, 1000))

FACTS_JS = """() => {
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 4 && r.height > 4 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity !== 0; };
  const controls = [...document.querySelectorAll('button, input, select, textarea, [role=button], [role=slider], a[href], [onclick]')].filter(vis).length;
  const drawn = [...document.querySelectorAll('svg, canvas, img, video')].filter(vis).length;
  const text = (document.body && document.body.innerText || '').trim().length;
  return {controls, drawn, text, scroll_w: document.documentElement.scrollWidth, view_w: innerWidth};
}"""


def html_outputs(files: list[Path], ws: Path) -> list[Path]:
    pages = [p for p in files if p.suffix.lower() in (".html", ".htm") and p.is_file()]
    pages.sort(key=lambda p: (p.name.lower() not in ("index.html", "index.htm"), len(p.parts), str(p)))
    return pages[:MAX_PAGES]


async def capture(ws: Path, files: list[Path], out_dir: Path) -> dict:
    """Photograph the trial's pages into out_dir/_shots; {"shots": [...], "pages": {path: facts}} or a reason why
    not ({"skipped": "..."}) - a page that could not be photographed is never counted as passing."""
    ws = ws.resolve()
    pages = html_outputs(files, ws)
    if not pages:
        return {"shots": [], "pages": {}}
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"shots": [], "pages": {}, "skipped": "playwright 沒有安裝，無法截圖"}
    shots_dir = out_dir / "_shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    out: dict = {"shots": [], "pages": {}}

    async def serve(route):
        url = route.request.url
        if not url.startswith(ORIGIN + "/"):
            return await route.abort()                   # no network: only the trial's own files
        rel = url[len(ORIGIN) + 1:].split("?", 1)[0].split("#", 1)[0]
        from urllib.parse import unquote
        p = (ws / unquote(rel)).resolve()
        try:
            p.relative_to(ws)
        except ValueError:
            return await route.abort()
        if not p.is_file():
            return await route.fulfill(status=404, body="")
        await route.fulfill(status=200, body=p.read_bytes(),
                            content_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream")

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                for page_path in pages:
                    rel = page_path.resolve().relative_to(ws).as_posix()
                    facts: dict = {"errors": []}
                    frames: dict[str, str] = {}
                    for sid, label, w, h, wait in SHOTS:
                        if sid == "desktop-later":
                            continue                     # taken in the same visit as desktop-load
                        ctx = await browser.new_context(viewport={"width": w, "height": h}, java_script_enabled=True,
                                                        service_workers="block")
                        await ctx.route("**/*", serve)
                        pg = await ctx.new_page()
                        if sid == "desktop-load":
                            pg.on("pageerror", lambda e: facts["errors"].append(str(e)[:300]))
                            # a script error counts; a resource the offline browser refused does not (the trial
                            # ran offline too): it is listed apart
                            pg.on("console", lambda m: m.type == "error" and (
                                facts.setdefault("blocked", []).append(m.text[:200]) if m.text.startswith("Failed to load resource")
                                else facts["errors"].append(m.text[:300])))
                        try:
                            await pg.goto(f"{ORIGIN}/{rel}", wait_until="load", timeout=15000)
                        except Exception as e:  # noqa: BLE001 - a page that does not load is a fact too
                            facts["errors"].append(f"載入失敗：{str(e)[:200]}")
                        visits = [(sid, label, wait)] + ([("desktop-later", SHOTS[1][1], SHOTS[1][4] - wait)]
                                                         if sid == "desktop-load" else [])
                        for vid, vlabel, vwait in visits:
                            await pg.wait_for_timeout(vwait)
                            img = await pg.screenshot(type="jpeg", quality=72)
                            frames[vid] = hashlib.sha256(img).hexdigest()
                            name = f"{_slug(rel)}-{vid}.jpg"
                            (shots_dir / name).write_bytes(img)
                            out["shots"].append({"path": f"_shots/{name}", "page": rel, "id": vid, "label": vlabel,
                                                 "viewport": [w, h], "bytes": len(img)})
                            if vid == "desktop-load":
                                facts.update(await pg.evaluate(FACTS_JS))
                            if vid == "phone":
                                ph = await pg.evaluate(FACTS_JS)
                                facts["phone_overflow"] = max(0, int(ph["scroll_w"]) - int(ph["view_w"]))
                        await ctx.close()
                    facts["animates"] = frames.get("desktop-load") != frames.get("desktop-later")
                    facts["blank"] = not facts.get("drawn") and int(facts.get("text") or 0) < 20
                    out["pages"][rel] = facts
            finally:
                await browser.close()
    except Exception as e:  # noqa: BLE001 - no browser binary, a crash: say so, grade nothing as passed
        log.warning("page capture failed: %s", e)
        out["skipped"] = f"截圖失敗：{str(e)[:200]}"
    return out


def _slug(rel: str) -> str:
    import re
    return re.sub(r"[^\w.-]+", "-", rel)[:80]


CHECKS = {"no_errors": "頁面載入沒有程式錯誤", "animates": "畫面會動（3 秒內有變化）",
          "controls": "有可操作的控制元件", "not_blank": "畫面不是空白", "fits_phone": "手機寬度不會左右溢出"}


def grade(g: dict, render: dict | None) -> tuple[bool, str]:
    """The deterministic `page` grader: a fact of the trial's page as the browser saw it."""
    check = str(g.get("check") or "")
    if check not in CHECKS:
        return False, f"unknown page check {check!r}"
    if not render or render.get("skipped"):
        return False, (render or {}).get("skipped") or "沒有網頁成果可以檢查"
    pages = render.get("pages") or {}
    if g.get("path"):
        import fnmatch
        pages = {k: v for k, v in pages.items() if fnmatch.fnmatch(k, g["path"])}
    if not pages:
        return False, "沒有網頁成果可以檢查"
    rel, f = next(iter(pages.items()))
    if check == "no_errors":
        errs = f.get("errors") or []
        return not errs, (f"{rel}：沒有錯誤" if not errs else f"{rel}：{len(errs)} 個錯誤，第一個：{errs[0][:160]}")
    if check == "animates":
        return bool(f.get("animates")), f"{rel}：{'0.5 秒與 3 秒的畫面不同' if f.get('animates') else '0.5 秒與 3 秒的畫面完全相同'}"
    if check == "controls":
        lo = int(g.get("min", 1))
        n = int(f.get("controls") or 0)
        return n >= lo, f"{rel}：看得到 {n} 個控制元件（至少 {lo}）"
    if check == "not_blank":
        return not f.get("blank"), f"{rel}：{f.get('drawn', 0)} 個圖形、{f.get('text', 0)} 個字"
    ov = int(f.get("phone_overflow") or 0)
    return ov <= int(g.get("max_overflow", 8)), f"{rel}：手機寬度溢出 {ov}px"


def images_for(render: dict | None, base: Path, limit: int = 4) -> list[tuple[str, bytes]]:
    """The pictures a `screens: true` rubric is judged with: (label, jpeg bytes), numbered as the judge sees them."""
    out = []
    for s in (render or {}).get("shots") or []:
        try:
            out.append((f"{s['page']} · {s['label']}", (base / s["path"]).read_bytes()))
        except OSError:
            continue
        if len(out) >= limit:
            break
    return out
