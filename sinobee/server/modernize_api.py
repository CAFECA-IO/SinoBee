"""現代化 panel endpoints (code-modernization skill): progress, rule review deck, brief approval, proof sign-off and
the system's REPORT.html, all read from / written to analysis/<system>/ in the workspace.

The logic lives in the bundled skill (scripts/sinobee_panel.py) so the files it writes are exactly the ones the skill's
steps read; this module only loads it (built-in code, never a user or Hub copy) and maps errors to HTTP.
REPORT.html is served with a sandboxing CSP: its scripts run in an opaque origin and cannot call SinoBee's API.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

log = logging.getLogger("sinobee.server.modernize")
PANEL = Path(__file__).resolve().parent.parent / "bundled_skills" / "code-modernization" / "scripts" / "sinobee_panel.py"
_MOD: dict = {}


def panel():
    mtime = PANEL.stat().st_mtime
    if _MOD.get("mtime") != mtime:
        spec = importlib.util.spec_from_file_location("sinobee_code_modernization_panel", PANEL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD.update(mtime=mtime, mod=mod)
    return _MOD["mod"]


def register(app: FastAPI, rt_getter) -> None:
    def ws() -> Path:
        return Path(rt_getter().config.workspace)

    async def call(fn, *args):
        p = panel()
        try:
            return await asyncio.to_thread(getattr(p, fn), str(ws()), *args)
        except p.PanelError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/modernize/systems")
    async def modernize_systems():
        p = panel()
        return {"systems": await asyncio.to_thread(p.systems, str(ws()))}

    @app.get("/api/modernize/{system}")
    async def modernize_detail(system: str):
        return await call("detail", system)

    @app.post("/api/modernize/{system}/review")
    async def modernize_review(system: str, body: dict):
        return await call("review", system, str(body.get("rule") or ""), str(body.get("verdict") or ""),
                          str(body.get("note") or ""))

    @app.post("/api/modernize/{system}/sign")
    async def modernize_sign(system: str, body: dict):
        what = body.get("what")
        if what == "brief":
            return await call("sign_brief", system, str(body.get("name") or ""), str(body.get("covers") or ""))
        if what == "proof":
            return await call("sign_proof", system, str(body.get("name") or ""), str(body.get("role") or ""),
                              str(body.get("decision") or ""))
        raise HTTPException(400, "what: brief | proof")

    @app.get("/api/modernize/{system}/report")
    async def modernize_report(system: str):
        p = panel()
        if not p.NAME.fullmatch(system):
            raise HTTPException(400, "bad system name")
        f = ws() / "analysis" / system / "REPORT.html"
        if not f.is_file() or f.is_symlink():
            raise HTTPException(404, "no REPORT.html yet")
        return FileResponse(f, media_type="text/html", headers={
            "Content-Security-Policy": "sandbox allow-scripts allow-popups; default-src 'self' 'unsafe-inline' data:",
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
