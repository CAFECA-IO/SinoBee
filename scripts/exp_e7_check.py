#!/usr/bin/env python3
"""E7's automatic checks of the built pages (needs playwright): the evaluation's offline browser (evals/render.py)
photographs each page and records script errors, motion, controls and phone overflow; then the wind-speed slider is
moved to its maximum and the power readout must change.

    python3 scripts/exp_e7_check.py <run dir> [<run dir> ...]     # dirs made by exp_e7_e6.py; adds "checks" per run
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sinobee.evals import render as R  # noqa: E402

SLIDER_JS = """async () => {
  const r = document.querySelector('input[type=range]');
  if (!r) return {slider: false};
  const text = () => document.body.innerText.replace(/\\s+/g, ' ');
  const before = text();
  r.value = r.max || 100;
  r.dispatchEvent(new Event('input', {bubbles: true}));
  r.dispatchEvent(new Event('change', {bubbles: true}));
  await new Promise(res => setTimeout(res, 700));
  const after = text();
  return {slider: true, readout_changed: before !== after};
}"""


async def slider(ws: Path, page: str) -> dict:
    from playwright.async_api import async_playwright

    async def serve(route):
        url = route.request.url
        if not url.startswith(R.ORIGIN + "/"):
            return await route.abort()
        p = (ws / url[len(R.ORIGIN) + 1:].split("?")[0]).resolve()
        if not str(p).startswith(str(ws.resolve())) or not p.is_file():
            return await route.fulfill(status=404, body="")
        await route.fulfill(status=200, body=p.read_bytes(), content_type="text/html" if p.suffix == ".html" else None)
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        try:
            ctx = await b.new_context(viewport={"width": 1280, "height": 800})
            await ctx.route("**/*", serve)
            pg = await ctx.new_page()
            await pg.goto(f"{R.ORIGIN}/{page}", wait_until="load", timeout=15000)
            await pg.wait_for_timeout(500)
            return await pg.evaluate(SLIDER_JS)
        except Exception as e:  # noqa: BLE001
            return {"slider": None, "error": str(e)[:200]}
        finally:
            await b.close()


async def main(dirs):
    for d in map(Path, dirs):
        st = json.loads((d / "state.json").read_text(encoding="utf-8"))
        for run in st["runs"]:
            ws = d / run["id"]
            page = ws / "wind_turbine.html"
            if not page.exists():
                run["checks"] = {"page": False}
                continue
            cap = await R.capture(ws, [page], ws)
            facts = (cap.get("pages") or {}).get("wind_turbine.html") or {}
            run["checks"] = {"page": True, "errors": facts.get("errors"), "animates": facts.get("animates"),
                             "controls": facts.get("controls"), "blank": facts.get("blank"),
                             "phone_overflow": facts.get("phone_overflow"), "skipped": cap.get("skipped"),
                             **await slider(ws, "wind_turbine.html")}
            print(run["id"], json.dumps(run["checks"], ensure_ascii=False))
        (d / "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
