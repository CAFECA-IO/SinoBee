"""Register database connections as tools on sinobee.ai, and install tools from it.

A registered tool (TOOL.json, schema sinobee.tool/v1) says what to connect to and how — never who:
the user name and password stay in secrets.json, or, for private tools with sync on, in the user's
end-to-end encrypted vault (pack `db:<connection id>`), which sinobee.ai cannot read.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict

import httpx

from .. import account
from ..vault import VaultClient, VaultError
from .store import Connection

MAX_TABLES = 100


class HubToolError(RuntimeError):
    pass


def tool_name(c: Connection) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (c.name or "").lower()).strip("-")
    if not s:
        s = re.sub(r"[^a-z0-9]+", "-", (c.database or c.kind).lower()).strip("-") or "database"
    return s[:64].strip("-")


def bump(version: str | None) -> str:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", version or "")
    return f"{m[1]}.{m[2]}.{int(m[3]) + 1}" if m else "1.0.0"


def manifest_for(c: Connection, *, version: str, title: str | None = None, description: str = "",
                 include_host: bool = True, tables: list[dict] | None = None, vault_pack: str | None = None,
                 queries: list[dict] | None = None, notes: dict | None = None, skill: str | None = None) -> dict:
    db: dict = {"engine": c.kind, "readOnly": not c.allow_write, "rowLimit": c.row_limit, "timeoutS": c.timeout_s}
    if c.kind == "sqlite":
        db["file"] = os.path.basename(c.path)[:255]
    else:
        if include_host:
            db.update(host=c.host, port=int(c.port or 0) or None)
        if c.database:
            db["database"] = c.database
        db["sslmode"] = c.sslmode
    db = {k: v for k, v in db.items() if v is not None}
    m = {"schema": "sinobee.tool/v1", "name": tool_name(c), "version": version, "kind": "database",
         "title": (title or c.name or tool_name(c))[:80], "description": description[:2000], "database": db}
    if tables:
        m["tables"] = tables[:MAX_TABLES]
    if queries:
        m["queries"] = [{"title": q["title"], "sql": q["sql"]} for q in queries[:50]]
    if notes:
        m["notes"] = {k: v for k, v in list(notes.items())[:500]}
    if skill:
        m["skill"] = skill[:20000]
    if vault_pack:
        m["vaultPack"] = vault_pack
    return m


async def schema_snapshot(service, c: Connection) -> list[dict]:
    """Table and column names (no data) for the manifest."""
    def snap(s):
        out = []
        for sch in s.schemas():
            for t in s.tables(sch):
                if len(out) >= MAX_TABLES:
                    return out
                cols = s.columns(sch, t["name"]).get("columns", [])
                out.append({"schema": sch, "name": t["name"],
                            "columns": [{"name": x["name"], "type": str(x.get("type") or "")[:64]} for x in cols[:200]]})
        return out
    return await service._with_session(c, snap)


def _client(config, method: str, path: str, scope: str | None, **kw) -> httpx.Response:
    try:
        return httpx.request(method, f"{account.base_url(config)}{path}",
                             headers=account.auth_headers(config, scope) if scope else account.UA, timeout=60, **kw)
    except httpx.HTTPError as e:
        raise HubToolError(f"無法連線到 sinobee.ai：{e}") from None


def _err(r: httpx.Response) -> str:
    try:
        j = r.json()
        issues = "; ".join(f"{'.'.join(map(str, i.get('path', [])))}: {i.get('message')}" for i in (j.get("issues") or [])[:5])
        return (j.get("error") or j.get("detail") or r.text[:300]) + (f"（{issues}）" if issues else "")
    except ValueError:
        return f"HTTP {r.status_code}"


async def publish(config, service, c: Connection, *, visibility: str = "PRIVATE", sync_secret: bool = False,
                  include_schema: bool = True, include_host: bool = True, title: str | None = None,
                  description: str = "", skills=None) -> dict:
    from . import toolfolder
    visibility = visibility.upper()
    if visibility not in ("PRIVATE", "PUBLIC"):
        raise HubToolError("visibility 需為 PRIVATE 或 PUBLIC")
    if sync_secret and visibility == "PUBLIC":
        raise HubToolError("公開工具不能同步帳號密碼")
    tables = await schema_snapshot(service, c) if include_schema else None
    # the local tool folder (saved queries, notes, SKILL.md) is what gets registered
    toolfolder.write(config, c, description=description, tables=tables or [], skills=skills)
    pack = None
    prev_pack = (c.tool or {}).get("vault_pack")
    if visibility == "PUBLIC" and prev_pack:
        # going public: the encrypted credentials leave the cloud first
        try:
            VaultClient(config).delete(prev_pack)
        except VaultError as e:
            raise HubToolError(f"無法刪除雲端的加密連線資料：{e}") from None
    if sync_secret:
        pack = f"db:{c.id}"
        data = {k: v for k, v in asdict(c).items() if k != "tool"}
        try:
            VaultClient(config).put(pack, {"type": "db-connection", "connection": data,
                                           "password": service.store.password(c) or ""})
        except VaultError as e:
            raise HubToolError(str(e)) from None
    version = bump((c.tool or {}).get("version"))
    m = manifest_for(c, version=version, title=title, description=description, include_host=include_host,
                     tables=tables, vault_pack=pack, queries=toolfolder.queries(config, c),
                     notes=toolfolder.notes(config, c), skill=toolfolder.skill_md(config, c))
    r = _client(config, "POST", "/api/v1/tools", "tools:write", json={"manifest": m, "visibility": visibility})
    if r.status_code == 409 and "already exists" in r.text:
        # someone published this version from another machine: take the next one
        m["version"] = bump(m["version"])
        r = _client(config, "POST", "/api/v1/tools", "tools:write", json={"manifest": m, "visibility": visibility})
    if r.status_code >= 400:
        raise HubToolError(f"登錄失敗：{_err(r)}")
    res = r.json()
    url = f"{account.base_url(config)}/tools/{res['id']}"
    toolfolder.write(config, c, manifest=m)
    service.store.update_meta(c.id, tool={"id": res["id"], "name": m["name"], "version": m["version"],
                                          "visibility": visibility, "status": res["status"], "url": url,
                                          "vault_pack": pack})
    return {**res, "url": url, "visibility": visibility, "synced": bool(pack)}


def mine(config) -> list[dict]:
    r = _client(config, "GET", "/api/v1/tools/mine", "tools:write")
    if r.status_code >= 400:
        raise HubToolError(_err(r))
    return r.json().get("items", [])


def search(config, q: str = "") -> list[dict]:
    r = _client(config, "GET", "/api/v1/tools", None, params={"q": q})
    if r.status_code >= 400:
        raise HubToolError(_err(r))
    return r.json().get("items", [])


def install(config, service, ref: str, skills=None) -> dict:
    """Add a tool as a local connection. Own private tools with a vault pack get their credentials back
    (vault must be unlocked); everything else asks the user for credentials."""
    ref = ref.strip().removeprefix("tool:").strip("/")
    if not re.match(r"^[A-Za-z0-9x_-]+/[a-z0-9-]+$", ref):
        raise HubToolError("工具名稱格式：<publisher>/<name>")
    scope = "tools:write" if account.token(config) else None
    r = _client(config, "GET", f"/api/v1/tools/{ref}", scope)
    if r.status_code == 404:
        raise HubToolError(f"找不到工具 {ref}（私人工具需要用擁有者帳號登入）")
    if r.status_code >= 400:
        raise HubToolError(_err(r))
    t = r.json()
    m = t["manifest"]
    d = m["database"]
    password = None
    restored = None
    if m.get("vaultPack"):
        try:
            pack = VaultClient(config).get(m["vaultPack"])
            restored = pack.get("connection") or {}
            password = pack.get("password") or None
        except VaultError as e:
            raise HubToolError(f"{e}（這個私人工具的連線資料在保險箱裡）") from None
    if restored:
        data = {k: restored.get(k) for k in ("kind", "name", "host", "port", "database", "user", "sslmode", "path",
                                             "allow_write", "row_limit", "timeout_s")}
    else:
        data = {"kind": d["engine"], "name": m.get("title") or m["name"], "host": d.get("host") or "",
                "port": d.get("port"), "database": d.get("database") or "", "sslmode": d.get("sslmode") or "prefer",
                "path": d.get("file") or "", "row_limit": d.get("rowLimit"), "timeout_s": d.get("timeoutS"),
                "allow_write": False}
        if d["engine"] == "sqlite" and not os.path.isabs(data["path"] or ""):
            data["path"] = os.path.join(str(config.workspace), data["path"] or "database.sqlite")
        if d["engine"] != "sqlite" and not data["host"]:
            data["host"] = "localhost"
    existing = next((c for c in service.store.list() if (c.tool or {}).get("id") == t["id"]), None)
    try:
        conn = service.store.save({k: v for k, v in data.items() if v is not None}, password=password,
                                  cid=existing.id if existing else None)
    except ValueError as e:
        raise HubToolError(str(e)) from None
    service.store.update_meta(conn.id, tool={"id": t["id"], "name": m["name"], "version": t["version"],
                                             "visibility": t["visibility"], "installed": True,
                                             "url": f"{account.base_url(config)}/tools/{t['id']}"})
    # the tool folder comes along: saved queries, notes and the SKILL.md the publisher wrote
    from . import toolfolder
    for q in reversed(m.get("queries") or []):
        try:
            toolfolder.save_query(config, conn, q["title"], q["sql"])
        except ValueError:
            pass
    if m.get("notes"):
        toolfolder.set_notes(config, conn, m["notes"])
    toolfolder.write(config, conn, manifest=m, skill=m.get("skill"), custom_skill=bool(m.get("skill")),
                     description=m.get("description", ""), skills=skills)
    _client(config, "POST", f"/api/v1/tools/{t['id']}/install", scope)
    return {"connection": conn.id, "name": conn.name, "restored_credentials": bool(password or (restored or {}).get("user")),
            "needs_credentials": d["engine"] != "sqlite" and not password, "version": t["version"]}
