"""Database browser: saved connections, read-only queries, schema browsing (PostgreSQL, MySQL/MariaDB, SQLite).

Used by the Web UI (/api/db/*) and the agent's `database` toolset. All blocking driver work runs in a
worker thread; one operation per connection at a time.
"""
from __future__ import annotations

import asyncio
import re
import time

from .drivers import DbError, Session, preview_sql
from .guard import classify
from .store import KINDS, Connection, ConnectionStore, validate

__all__ = ["DbService", "DbError", "Connection", "ConnectionStore", "classify", "KINDS", "validate",
           "parse_dsn", "redact_dsn"]

DSN_RE = re.compile(
    r"\b(postgres(?:ql)?|mysql|mariadb)://([^:/@\s]+)(?::([^@\s]+))?@([A-Za-z0-9._\-\[\]]+)(?::(\d+))?(?:/([A-Za-z0-9_\-.]*))?"
    r"(?:\?(\S*))?", re.I)


def parse_dsn(text: str) -> dict | None:
    """postgres://user:pass@host:5432/db?sslmode=require -> connection fields (password included)."""
    m = DSN_RE.search(text or "")
    if not m:
        return None
    scheme, user, pw, host, port, db, query = m.groups()
    kind = "postgres" if scheme.lower().startswith("postgres") else "mysql"
    out = {"kind": kind, "user": user, "password": pw or "", "host": host,
           "port": int(port) if port else KINDS[kind], "database": db or ""}
    sm = re.search(r"sslmode=([\w-]+)", query or "")
    if sm:
        out["sslmode"] = sm.group(1)
    return out


def redact_dsn(text: str) -> str:
    return DSN_RE.sub(lambda m: m.group(0).replace(":" + m.group(3) + "@", ":••••@") if m.group(3) else m.group(0), text)


class DbService:
    def __init__(self, config):
        self.config = config
        self.store = ConnectionStore(config.home, config.secrets)
        from . import tls
        tls.set_home(config.home)
        self._locks: dict[str, asyncio.Lock] = {}
        self._running: dict[str, Session] = {}

    # ---------- helpers ----------
    def _lock(self, cid: str) -> asyncio.Lock:
        return self._locks.setdefault(cid, asyncio.Lock())

    def get(self, ref: str) -> Connection:
        c = self.store.find(ref)
        if not c:
            raise DbError(f"找不到連線「{ref}」")
        return c

    def session(self, c: Connection, read_only: bool = True, password: str | None = None,
                ssh: tuple | None = None) -> Session:
        """A (blocking) session with the stored secrets; pins the bastion host key on first use."""
        pw = password if password is not None else self.store.password(c)
        ssh = ssh if ssh is not None else self.store.ssh_secret(c)

        def pin(fp: str):
            c.ssh_fingerprint = fp
            if self.store.get(c.id):
                self.store.update_meta(c.id, ssh_fingerprint=fp)
        return Session(c, pw, read_only=read_only, ssh=ssh, on_pin=pin)

    async def _with_session(self, c: Connection, fn, read_only: bool = True, password: str | None = None,
                            ssh: tuple | None = None):
        def work():
            with self.session(c, read_only, password, ssh) as s:
                self._running[c.id] = s
                try:
                    return fn(s)
                finally:
                    self._running.pop(c.id, None)

        async with self._lock(c.id):
            try:
                return await asyncio.wait_for(asyncio.to_thread(work), timeout=c.timeout_s + 20)
            except asyncio.TimeoutError:
                self.cancel(c.id)
                raise DbError(f"操作逾時（{c.timeout_s} 秒）") from None

    def cancel(self, cid: str) -> bool:
        s = self._running.get(cid)
        if s:
            s.cancel()
            return True
        return False

    def listing(self) -> list[dict]:
        return [c.public(bool(self.store.password(c)), bool(self.store.ssh_secret(c)[0])) for c in self.store.list()]

    # ---------- connection management ----------
    async def test(self, data: dict, password: str | None = None, cid: str | None = None,
                   ssh_secret: str | None = None, ssh_passphrase: str | None = None) -> dict:
        clean = validate(data)
        existing = self.store.get(cid) if cid else None
        if existing and password in (None, ""):
            password = self.store.password(existing)
        if existing and not ssh_secret:
            ssh_secret, stored_pass = self.store.ssh_secret(existing)
            ssh_passphrase = ssh_passphrase if ssh_passphrase is not None else stored_pass
        if existing and clean.get("ssh_host") == existing.ssh_host and \
                int(clean.get("ssh_port") or 22) == int(existing.ssh_port or 22):
            clean["ssh_fingerprint"] = existing.ssh_fingerprint
        c = Connection(id=cid or "_test", name=clean.get("name") or "test",
                       **{k: v for k, v in clean.items() if k != "name"})
        t0 = time.time()

        def probe(s: Session):
            return {"version": s.version(), "schemas": len(s.schemas())}

        # a test never pins: the fingerprint is shown and saved with the connection
        def work():
            with Session(c, password or "", ssh=(ssh_secret, ssh_passphrase)) as s:
                return probe(s), (s.tunnel.fingerprint if s.tunnel else "")
        async with self._lock(c.id):
            try:
                info, fp = await asyncio.wait_for(asyncio.to_thread(work), timeout=c.timeout_s + 30)
            except asyncio.TimeoutError:
                raise DbError(f"操作逾時（{c.timeout_s} 秒）") from None
        if c.ssh_host and cid != "_test" and existing and not existing.ssh_fingerprint and fp:
            self.store.update_meta(existing.id, ssh_fingerprint=fp)
        return {"ok": True, "latency_ms": int((time.time() - t0) * 1000), "ssh_fingerprint": fp or None, **info}

    @property
    def migrations(self):
        if getattr(self, "_migrations", None) is None:
            from .migration.runner import MigrationManager
            self._migrations = MigrationManager(self)
        return self._migrations

    def retrust(self, ref: str) -> Connection:
        """Forget the pinned bastion host key (after the user checked why it changed)."""
        c = self.get(ref)
        from .tunnel import POOL
        POOL.close(c.id)
        return self.store.update_meta(c.id, ssh_fingerprint="")

    # ---------- browsing ----------
    async def schema_tree(self, ref: str) -> dict:
        c = self.get(ref)

        def tree(s: Session):
            out = []
            for sch in s.schemas():
                out.append({"name": sch, "tables": s.tables(sch)})
            return {"version": s.version(), "schemas": out}
        return await self._with_session(c, tree)

    async def table_info(self, ref: str, schema: str, table: str) -> dict:
        c = self.get(ref)
        return await self._with_session(c, lambda s: s.columns(schema, table))

    async def preview(self, ref: str, schema: str, table: str, page: int = 0, page_size: int = 100,
                      sort: str | None = None, desc: bool = False, filters: list | None = None,
                      count: bool = True) -> dict:
        c = self.get(ref)

        def run(s: Session):
            cols = [x["name"] for x in s.columns(schema, table)["columns"]]
            sql, params, csql, cparams = preview_sql(s, schema, table, cols, page, page_size, sort, desc, filters)
            res = s.run(sql, params, limit=page_size)
            res["sql"] = sql
            if count:
                try:
                    res["total"] = int(s.run(csql, cparams, limit=1)["rows"][0][0])
                except DbError:
                    res["total"] = None
            return res
        return await self._with_session(c, run)

    async def query(self, ref: str, sql: str, limit: int | None = None, allow_write: bool = False,
                    source: str = "ui") -> dict:
        """Run SQL. Reads always; writes only when the connection allows it AND the caller confirmed."""
        c = self.get(ref)
        verdict = classify(sql, c.kind)
        if verdict.kind == "invalid":
            raise DbError(verdict.reason)
        write = not verdict.is_read
        if write and not c.allow_write:
            raise DbError(f"這個連線是唯讀的：{verdict.reason}。如需寫入，請在連線設定開啟「允許寫入」。")
        if write and not allow_write:
            raise DbError(f"需要確認才能執行寫入：{verdict.reason}")
        limit = max(1, min(int(limit or c.row_limit), 10000))
        t0 = time.time()
        try:
            res = await self._with_session(c, lambda s: s.run(sql, limit=limit), read_only=not write)
            ok, err = True, None
        except DbError as e:
            res, ok, err = None, False, str(e)
        self.store.add_history(c.id, {"sql": sql[:4000], "at": time.time(), "ok": ok, "error": err,
                                      "rows": res["row_count"] if res else None, "write": write, "source": source,
                                      "ms": int((time.time() - t0) * 1000)})
        if not ok:
            raise DbError(err)
        return {**res, "statement": verdict.statement, "write": write}
