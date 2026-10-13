"""Skill management endpoints: list with trust labels, approve, disable, install, export, project trust, learning."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from ..skills import LABELS, hub
from ..skills.manager import SkillError


def _row(mgr, s, effective: bool) -> dict:
    return {**s.info(), "effective": effective, "stats": mgr.stats(s.name), "versions": mgr.versions(s.name)
            if effective else [], "policy": {"visible": s.policy.visible, "scripts": s.policy.scripts,
                                             "allowed_tools": s.policy.allowed_tools, "agent_edit": s.policy.agent_edit}}


def register(app: FastAPI, rt_getter) -> None:
    def mgr():
        return rt_getter().skills

    def guard(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except SkillError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/skills")
    async def list_skills():
        m = mgr()
        m.refresh()
        rows = [_row(m, s, True) for s in sorted(m.skills.values(), key=lambda s: s.name)]
        rows += [_row(m, s, False) for s in m.shadowed]
        return {"skills": rows, "labels": LABELS,
                "project": {"dir": str(m.project_dir) if m.project_dir else None,
                            "trusted": bool(m.project_dir) and m.projects.is_trusted(m.project_dir)},
                "diagnostics": m.diagnostics[:200],
                "pending": [p for p in rt_getter().memory.pending() if p["kind"].startswith("skill_")],
                "auto_create": rt_getter().config.get("skills.auto_create")}

    @app.get("/api/skills/{name}")
    async def skill_detail(name: str, path: str | None = None):
        m = mgr()
        s = guard(m.find, name, path)
        return {**_row(m, s, s in m.skills.values()), "content": s.location.read_text(encoding="utf-8"),
                "files": s.files()}

    @app.post("/api/skills/{name}/approve")
    async def approve(name: str, body: dict | None = None):
        return guard(mgr().approve, name, (body or {}).get("path"))

    @app.post("/api/skills/{name}/disable")
    async def disable(name: str, body: dict):
        rt = rt_getter()
        cur = set(rt.config.get("skills.disabled") or [])
        cur.add(name) if body.get("disabled", True) else cur.discard(name)
        rt.config.save_gui_settings({"skills": {"disabled": sorted(cur)}})
        rt.reload()
        return {"disabled": sorted(cur)}

    @app.post("/api/skills/{name}/rollback")
    async def rollback(name: str, version: int | None = None):
        return guard(mgr().rollback, name, version, actor="user")

    @app.delete("/api/skills/{name}")
    async def delete(name: str):
        return guard(mgr().delete, name)

    @app.post("/api/skills/install")
    async def install(body: dict):
        rt = rt_getter()
        return guard(hub.install, rt.skills, body.get("ref", ""), config=rt.config, scope=body.get("scope", "global"),
                     trust=body.get("trust") if body.get("trust") in ("local", "community") else None,
                     force=bool(body.get("force")))

    @app.get("/api/skills/{name}/export")
    async def export(name: str):
        out = Path(tempfile.mkdtemp(prefix="sinobee-export-"))
        bundle, _ = guard(hub.export, mgr(), name, out)
        return FileResponse(bundle, filename=bundle.name, media_type="application/gzip")

    @app.post("/api/skills/project/trust")
    async def project_trust(body: dict):
        m = mgr()
        return guard(m.trust_project) if body.get("trusted", True) else guard(m.untrust_project)

    @app.post("/api/sessions/{sid}/learn")
    async def learn(sid: str):
        from ..learning import learn_from_session
        rep = await learn_from_session(rt_getter(), sid)
        return rep


def register_feedback(app: FastAPI, rt_getter, broadcast) -> None:
    import json as _json

    from fastapi.responses import Response

    from ..learning import learn_from_feedback

    @app.get("/api/sessions/{sid}/turns")
    async def session_turns(sid: str):
        return rt_getter().memory.turns(sid)

    @app.post("/api/feedback")
    async def feedback(body: dict):
        rt = rt_getter()
        turn = rt.memory.turn(int(body.get("turn_id") or 0))
        if not turn:
            raise HTTPException(404, "turn not found")
        rating = 1 if int(body.get("rating", 0)) > 0 else -1
        tags = [str(t)[:30] for t in (body.get("tags") or [])][:8]
        comment = str(body.get("comment") or "")[:2000]
        prev = rt.memory.latest_feedback(turn["id"])
        if prev:   # editing (e.g. adding a comment): update the record, don't double-count skill outcomes
            fid = prev["id"]
            rt.memory.update_feedback(fid, rating, tags or prev["tags"], comment or prev["comment"] or "")
            rate_skills = rating != prev["rating"]
            if not (rate_skills or (comment and comment != prev["comment"]) or (tags and tags != prev["tags"])):
                return {"feedback_id": fid, "status": "unchanged"}
        else:
            fid = rt.memory.add_feedback(turn["id"], turn["session_id"], rating, tags, comment)
            rate_skills = True
        rt.spawn(learn_from_feedback(rt, fid, turn, rating, tags, comment, emit=broadcast, rate_skills=rate_skills))
        return {"feedback_id": fid, "status": "recorded"}

    @app.get("/api/feedback/stats")
    async def feedback_stats(days: int = 30):
        import time as _t
        return rt_getter().memory.feedback_stats(_t.time() - max(1, min(days, 3650)) * 86400)

    @app.get("/api/feedback/export")
    async def feedback_export():
        rows = rt_getter().memory.feedback_dataset()
        body = "\n".join(_json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else "")
        return Response(body, media_type="application/x-ndjson",
                        headers={"Content-Disposition": 'attachment; filename="sinobee-feedback.jsonl"'})
