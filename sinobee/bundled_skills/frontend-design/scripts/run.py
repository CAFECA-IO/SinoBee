#!/usr/bin/env python3
"""Check a web page the way a visitor meets it, on a phone and on a desktop.

    run.py check <page.html> [--shots DIR]

Opens the page in headless Chromium at 390×844 and 1280×900 (and in dark colour scheme) and reports:
  horizontal scrolling on a phone, text below 4.5:1 contrast (3:1 for large text), text under 12 px,
  images without alt text, missing <html lang>, missing viewport meta, resources from the network
  (pages SinoBee shows are opened offline), script errors, and whether a dark scheme exists.
--shots writes phone.png, desktop.png and dark.png to look at.

JSON out; exit 0 ok, 1 problems, 2 usage / no browser.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROBE = r"""() => {
  const parse = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x)); return {r:p[0], g:p[1], b:p[2], a: p.length > 3 ? p[3] : 1}; };
  const lin = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const L = c => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
  const bgOf = el => { for (let n = el; n; n = n.parentElement) { const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0.5) return c; if (getComputedStyle(n).backgroundImage !== 'none') return null; }
    const b = parse(getComputedStyle(document.documentElement).backgroundColor);
    return b && b.a > 0.5 ? b : {r:255, g:255, b:255, a:1}; };
  const low = [], tiny = [];
  const seen = new Set();
  const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
  let node, n = 0;
  while ((node = walker.nextNode()) && n < 3000) {
    const t = node.textContent.trim(); if (!t) continue;
    const el = node.parentElement; if (!el || seen.has(el)) continue; seen.add(el); n++;
    const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
    if (cs.visibility === 'hidden' || cs.display === 'none' || r.width === 0 || r.height === 0) continue;
    if (el.closest('svg')) continue;
    const fg = parse(cs.color), bg = bgOf(el); const size = parseFloat(cs.fontSize);
    if (size < 12 && t.length > 2) tiny.push(`${t.slice(0, 30)} (${size}px)`);
    if (!fg || !bg) continue;
    const a = L(fg), b = L(bg); const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    const large = size >= 24 || (size >= 18.66 && parseInt(cs.fontWeight) >= 700);
    if (ratio < (large ? 3 : 4.5)) low.push(`「${t.slice(0, 30)}」 ${ratio.toFixed(2)}:1`);
  }
  const noalt = [...document.images].filter(i => !i.hasAttribute('alt')).map(i => i.getAttribute('src') || '').slice(0, 5);
  return {overflow: document.documentElement.scrollWidth - window.innerWidth,
          low: low.slice(0, 8), tiny: tiny.slice(0, 8), noalt,
          lang: document.documentElement.getAttribute('lang') || '',
          viewport: !!document.querySelector('meta[name=viewport]'),
          dark: [...document.styleSheets].some(s => { try { return [...s.cssRules].some(r => (r.media && /prefers-color-scheme:\s*dark/.test(r.media.mediaText))); } catch (e) { return false; } })};
}"""


def out(d, code=0):
    print(json.dumps(d, ensure_ascii=False, indent=1))
    sys.exit(code)


def check(page: Path, shots: Path | None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        out({"status": "error", "error": "playwright is not installed",
             "fix": f"{sys.executable} -m pip install playwright && {sys.executable} -m playwright install chromium"}, 2)
    problems, notes, network, errors = [], [], [], []
    url = page.resolve().as_uri()
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        try:
            for name, vp, scheme in (("phone", {"width": 390, "height": 844}, "light"),
                                     ("desktop", {"width": 1280, "height": 900}, "light"),
                                     ("dark", {"width": 1280, "height": 900}, "dark")):
                ctx = b.new_context(viewport=vp, color_scheme=scheme, device_scale_factor=1)
                pg = ctx.new_page()
                pg.on("pageerror", lambda e: errors.append(str(e)[:160]))

                def route(r):
                    u = r.request.url
                    if u.startswith(("file:", "data:", "blob:")):
                        return r.continue_()
                    network.append(u[:120])
                    return r.abort()
                pg.route("**/*", route)
                pg.goto(url, wait_until="load", timeout=20000)
                pg.wait_for_timeout(600)
                f = pg.evaluate(PROBE)
                if name == "phone" and f["overflow"] > 2:
                    problems.append(f"the page scrolls sideways on a phone (390 px): {f['overflow']} px too wide - "
                                    "find the fixed-width element (tables, pre, wide images, min-width)")
                if name != "phone" and f["low"]:
                    problems.append(f"{name}: text with too little contrast: " + "; ".join(f["low"][:5]))
                if name == "desktop":
                    if f["tiny"]:
                        notes.append("text under 12 px: " + "; ".join(f["tiny"][:5]))
                    if f["noalt"]:
                        problems.append("images without alt text: " + ", ".join(f["noalt"]))
                    if not f["lang"]:
                        problems.append("<html> has no lang attribute (screen readers and fonts pick the wrong language)")
                    if not f["viewport"]:
                        problems.append('no <meta name="viewport" content="width=device-width, initial-scale=1">: '
                                        "phones show a zoomed-out desktop page")
                    if not f["dark"]:
                        notes.append("no prefers-color-scheme: dark styles (fine if the page sets its own background)")
                if shots:
                    shots.mkdir(parents=True, exist_ok=True)
                    pg.screenshot(path=str(shots / f"{name}.png"), full_page=True)
                ctx.close()
        finally:
            b.close()
    if network:
        problems.append(f"{len(set(network))} resource(s) load from the network and are missing offline: "
                        + ", ".join(sorted(set(network))[:4]) + " - inline them or save them next to the page")
    if errors:
        problems.append("script errors: " + "; ".join(list(dict.fromkeys(errors))[:3]))
    out({"status": "fix" if problems else "ok", "problems": problems, "notes": notes,
         "shots": [str(shots / f"{n}.png") for n in ("phone", "desktop", "dark")] if shots else []},
        1 if problems else 0)


def main(a):
    if len(a) >= 2 and a[0] == "check":
        check(Path(a[1]), Path(a[a.index("--shots") + 1]) if "--shots" in a else None)
    out({"status": "error", "error": __doc__}, 2)


if __name__ == "__main__":
    main(sys.argv[1:])
