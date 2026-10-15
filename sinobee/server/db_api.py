"""Database browser endpoints (/api/db/*). Passwords are accepted on create/update/test only and are
never returned."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from ..db import DbError, parse_dsn
from ..tools.db_tools import service


def register(app: FastAPI, rt_getter) -> None:
    def svc():
        return service(rt_getter().config)

    async def guard(coro):
        try:
            return await coro
        except (DbError, ValueError) as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/db/connections")
    async def connections():
        return {"connections": svc().listing()}

    @app.post("/api/db/connections")
    async def save(body: dict):
        try:
            c = svc().store.save(body, password=(body.get("password") or None), cid=body.get("id") or None,
                                 keep_password=not body.get("clear_password"),
                                 ssh_secret=(body.get("ssh_secret") or None),
                                 ssh_passphrase=body.get("ssh_passphrase") if body.get("ssh_secret") else None)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        if body.get("ssh_fingerprint") and c.ssh_host and not c.ssh_fingerprint:
            # pinned from the "test connection" the user just looked at
            c = svc().store.update_meta(c.id, ssh_fingerprint=str(body["ssh_fingerprint"])[:120])
        return {"connection": c.public(bool(svc().store.password(c)), bool(svc().store.ssh_secret(c)[0]))}

    @app.delete("/api/db/connections/{cid}")
    async def delete(cid: str):
        if not svc().store.delete(cid):
            raise HTTPException(404, "找不到連線")
        from ..db import toolfolder
        toolfolder.remove(rt_getter().config, cid, rt_getter().skills)
        return {"ok": True}

    @app.post("/api/db/test")
    async def test(body: dict):
        return await guard(svc().test(body, body.get("password"), body.get("id") or None,
                                      body.get("ssh_secret") or None, body.get("ssh_passphrase")))

    @app.post("/api/db/{cid}/retrust")
    async def retrust(cid: str):
        try:
            c = svc().retrust(cid)
        except DbError as e:
            raise HTTPException(404, str(e)) from None
        return {"connection": c.public(bool(svc().store.password(c)), bool(svc().store.ssh_secret(c)[0]))}

    # ---------- migrations ----------
    from ..db.migration.runner import MigrationError

    async def mig(fn, *a, **kw):
        import asyncio
        try:
            return await asyncio.to_thread(fn, *a, **kw)
        except (MigrationError, DbError, ValueError) as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/db/migrations")
    async def migrations():
        return {"jobs": svc().migrations.list()}

    @app.post("/api/db/migrations/plan")
    async def migration_plan(body: dict):
        return await mig(svc().migrations.plan, str(body.get("source") or ""), body.get("target") or {},
                         body.get("options") or {})

    @app.post("/api/db/migrations")
    async def migration_start(body: dict):
        if body.get("confirm") is not True:
            raise HTTPException(400, "開始遷移需要確認（confirm: true）")
        return await mig(svc().migrations.start, str(body.get("source") or ""), body.get("target") or {},
                         body.get("options") or {})

    @app.get("/api/db/migrations/{jid}")
    async def migration_get(jid: str):
        try:
            return svc().migrations.get(jid).public()
        except MigrationError as e:
            raise HTTPException(404, str(e)) from None

    @app.post("/api/db/migrations/{jid}/stop")
    async def migration_stop(jid: str):
        return await mig(svc().migrations.stop, jid)

    @app.post("/api/db/migrations/{jid}/resume")
    async def migration_resume(jid: str, body: dict | None = None):
        return await mig(svc().migrations.resume, jid, (body or {}).get("options") or {})

    @app.delete("/api/db/migrations/{jid}")
    async def migration_delete(jid: str):
        await mig(svc().migrations.delete, jid)
        return {"ok": True}

    @app.get("/api/db/migrations/{jid}/download")
    async def migration_download(jid: str):
        from pathlib import Path
        from fastapi.responses import FileResponse
        try:
            job = svc().migrations.get(jid)
        except MigrationError as e:
            raise HTTPException(404, str(e)) from None
        tgt = job.d.get("target") or {}
        if tgt.get("type") not in ("sqlfile", "sqlite") or job.d.get("status") != "done":
            raise HTTPException(400, "只有完成的 SQL 檔或 SQLite 檔可以下載")
        p = Path(tgt["path"])
        if not p.is_file():
            raise HTTPException(404, "檔案已不存在")
        media = "application/gzip" if p.suffix == ".gz" else "application/sql" if tgt["type"] == "sqlfile" \
            else "application/vnd.sqlite3"
        return FileResponse(str(p), media_type=media, filename=p.name)

    @app.post("/api/db/parse")
    async def parse(body: dict):
        d = parse_dsn(str(body.get("text") or ""))
        return {"found": bool(d), "connection": d}

    @app.get("/api/db/{cid}/tree")
    async def tree(cid: str):
        return await guard(svc().schema_tree(cid))

    @app.get("/api/db/{cid}/table")
    async def table(cid: str, schema: str, table: str):
        return await guard(svc().table_info(cid, schema, table))

    @app.post("/api/db/{cid}/preview")
    async def preview(cid: str, body: dict):
        return await guard(svc().preview(cid, body["schema"], body["table"], int(body.get("page", 0)),
                                         int(body.get("page_size", 100)), body.get("sort") or None,
                                         bool(body.get("desc")), body.get("filters") or []))

    @app.post("/api/db/{cid}/query")
    async def query(cid: str, body: dict):
        return await guard(svc().query(cid, str(body.get("sql") or ""), body.get("limit"),
                                       allow_write=bool(body.get("confirm_write"))))

    @app.post("/api/db/{cid}/classify")
    async def classify_sql(cid: str, body: dict):
        from ..db import classify
        try:
            c = svc().get(cid)
        except DbError as e:
            raise HTTPException(404, str(e)) from None
        return {**classify(str(body.get("sql") or ""), c.kind).as_dict(), "allow_write": c.allow_write}

    @app.post("/api/db/{cid}/cancel")
    async def cancel(cid: str):
        return {"cancelled": svc().cancel(cid)}

    @app.get("/api/db/{cid}/queries")
    async def saved_queries(cid: str):
        from ..db import toolfolder
        try:
            return {"queries": toolfolder.queries(rt_getter().config, svc().get(cid))}
        except DbError as e:
            raise HTTPException(404, str(e)) from None

    @app.post("/api/db/{cid}/queries")
    async def save_query(cid: str, body: dict):
        from ..db import toolfolder
        try:
            return {"queries": toolfolder.save_query(rt_getter().config, svc().get(cid), str(body.get("title") or ""),
                                                     str(body.get("sql") or ""))}
        except (DbError, ValueError) as e:
            raise HTTPException(400, str(e)) from None

    @app.delete("/api/db/{cid}/queries")
    async def delete_query(cid: str, title: str):
        from ..db import toolfolder
        try:
            return {"queries": toolfolder.delete_query(rt_getter().config, svc().get(cid), title)}
        except DbError as e:
            raise HTTPException(404, str(e)) from None

    @app.get("/api/db/{cid}/history")
    async def history(cid: str):
        return {"history": svc().store.history(cid)}

    # ---------- sinobee.ai: register as a tool, install tools, vault ----------
    from .. import account
    from ..db import hub as dbhub
    from ..vault import VaultClient, VaultError

    def hub_guard(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except (dbhub.HubToolError, VaultError, account.AccountError) as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/api/db/{cid}/publish")
    async def publish(cid: str, body: dict):
        try:
            c = svc().get(cid)
            return await dbhub.publish(rt_getter().config, svc(), c, visibility=str(body.get("visibility") or "PRIVATE"),
                                       sync_secret=bool(body.get("sync_secret")),
                                       include_schema=body.get("include_schema", True) is not False,
                                       include_host=body.get("include_host", True) is not False,
                                       title=body.get("title") or None, description=str(body.get("description") or ""),
                                       skills=rt_getter().skills)
        except (DbError, dbhub.HubToolError, VaultError, account.AccountError) as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/hub/tools")
    async def hub_tools(mine: bool = True, q: str = ""):
        import asyncio
        cfg = rt_getter().config
        if mine:
            return {"items": await asyncio.to_thread(hub_guard, dbhub.mine, cfg)}
        return {"items": await asyncio.to_thread(hub_guard, dbhub.search, cfg, q)}

    @app.post("/api/hub/tools/install")
    async def hub_install(body: dict):
        import asyncio
        return await asyncio.to_thread(hub_guard, dbhub.install, rt_getter().config, svc(), str(body.get("ref") or ""),
                                       rt_getter().skills)

    @app.get("/api/vault")
    async def vault_status():
        import asyncio
        cfg = rt_getter().config
        if not account.token(cfg):
            return {"signed_in": False, "exists": False, "unlocked": False}
        st = await asyncio.to_thread(hub_guard, lambda: VaultClient(cfg).status())
        return {"signed_in": True, **st}

    @app.post("/api/vault/unlock")
    async def vault_unlock(body: dict):
        """Creates the vault on first use, otherwise unlocks it. The passphrase is used once and dropped."""
        import asyncio
        p = str(body.get("passphrase") or "")
        return await asyncio.to_thread(hub_guard, lambda: VaultClient(rt_getter().config).setup(p))

    @app.get("/api/vault/devices")
    async def vault_devices():
        import asyncio
        return {"items": await asyncio.to_thread(hub_guard, lambda: VaultClient(rt_getter().config).devices())}

    @app.post("/api/vault/devices/{device_id}/approve")
    async def vault_approve(device_id: str):
        import asyncio
        await asyncio.to_thread(hub_guard, lambda: VaultClient(rt_getter().config).approve_device(device_id))
        return {"ok": True}

    @app.post("/api/vault/lock")
    async def vault_lock():
        VaultClient(rt_getter().config).lock()
        return {"ok": True}
