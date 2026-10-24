"""Keys a task may be given, and what a conversation's tools did (security plan stage 2).

    GET    /api/task-secrets                 the keys you keep for tasks (never their values)
    PUT    /api/task-secrets/{name}          {value?, env, domains, note}   add or change one (value kept if omitted)
    DELETE /api/task-secrets/{name}
    GET    /api/audit/{sid}?limit=           the conversation's tool record (audit.py) and its sandbox's connections
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException


def register(app: FastAPI, rt) -> None:
    from .. import audit, task_secrets

    @app.get("/api/task-secrets")
    async def list_keys():
        return task_secrets.listing(rt().config)

    @app.put("/api/task-secrets/{name}")
    async def put_key(name: str, body: dict):
        try:
            return task_secrets.save(rt().config, name, value=(body.get("value") or None), env=body.get("env"),
                                     domains=body.get("domains"), note=body.get("note"))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/task-secrets/{name}")
    async def delete_key(name: str):
        task_secrets.delete(rt().config, name)
        return {"ok": True}

    @app.get("/api/audit/{sid}")
    async def audit_log(sid: str, limit: int = 500):
        cfg = rt().config
        entries = audit.read(cfg, sid, max(1, min(int(limit), 5000)))
        net = []
        try:
            from ..sandbox.manager import manager_for
            m = manager_for(cfg)
            await m.connection_log()
            net = [{k: e.get(k) for k in ("t", "host", "port", "method", "allowed", "reason")}
                   for e in m.connections if e.get("conversation") == sid][-limit:]
        except Exception:  # noqa: BLE001
            net = []
        return {"entries": entries, "network": net}
