"""Blocking database drivers (run them in a worker thread).

Each session is opened for one operation and closed again. Read-only sessions are enforced by the
database itself (layer 2), independently of the SQL guard (layer 1):
  PostgreSQL  default_transaction_read_only=on + statement_timeout
  MySQL       SET SESSION TRANSACTION READ ONLY + max_execution_time / max_statement_time
  SQLite      file opened with mode=ro + PRAGMA query_only + interrupt on timeout
"""
from __future__ import annotations

import base64
import datetime as _dt
import decimal
import math
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from .store import KINDS, Connection

MAX_CELL = 2000


class DbError(Exception):
    pass


def jsonable(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v[:MAX_CELL] + "…" if isinstance(v, str) and len(v) > MAX_CELL else v
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, _dt.timedelta):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        return "0x" + b[:64].hex() + ("…" if len(b) > 64 else "")
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    return str(v)[:MAX_CELL]


def qident(kind: str, name: str) -> str:
    if kind == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return '"' + name.replace('"', '""') + '"'


class Session:
    """One open connection. Use as a context manager."""

    def __init__(self, conn: Connection, password: str | None, read_only: bool = True):
        self.c = conn
        self.kind = conn.kind
        self.read_only = read_only
        self.password = password
        self.raw = None
        self._cancel_info = None

    # ---------- open / close ----------
    def __enter__(self):
        try:
            getattr(self, f"_open_{self.kind}")()
        except DbError:
            raise
        except Exception as e:  # noqa: BLE001 - driver errors differ per backend
            raise DbError(friendly(e, self.c)) from None
        return self

    def __exit__(self, *exc):
        try:
            if self.raw is not None:
                self.raw.close()
        except Exception:  # noqa: BLE001
            pass

    def _open_postgres(self):
        import psycopg
        opts = f"-c statement_timeout={self.c.timeout_s * 1000}"
        if self.read_only:
            opts += " -c default_transaction_read_only=on"
        self.raw = psycopg.connect(host=self.c.host, port=self.c.port or 5432, dbname=self.c.database or None,
                                   user=self.c.user or None, password=self.password or None,
                                   sslmode=self.c.sslmode, connect_timeout=10, options=opts,
                                   application_name="SinoBee", autocommit=True)

    def _open_mysql(self):
        import pymysql
        import ssl as _ssl
        ssl = {"require": {"check_hostname": False, "verify_mode": _ssl.CERT_NONE},
               "verify-ca": {"check_hostname": False, "verify_mode": _ssl.CERT_REQUIRED},
               "verify-full": {"check_hostname": True, "verify_mode": _ssl.CERT_REQUIRED}}.get(self.c.sslmode)
        self.raw = pymysql.connect(host=self.c.host, port=self.c.port or 3306, user=self.c.user or None,
                                   password=self.password or "", database=self.c.database or None,
                                   connect_timeout=10, read_timeout=self.c.timeout_s + 5, charset="utf8mb4",
                                   autocommit=True, ssl=ssl)
        cur = self.raw.cursor()
        if self.read_only:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
        for stmt in (f"SET SESSION max_execution_time = {self.c.timeout_s * 1000}",       # MySQL
                     f"SET SESSION max_statement_time = {self.c.timeout_s}"):            # MariaDB
            try:
                cur.execute(stmt)
                break
            except Exception:  # noqa: BLE001 - the variable exists on one server family only
                continue
        cur.close()

    def _open_sqlite(self):
        p = Path(self.c.path).expanduser()
        if not p.exists():
            raise DbError(f"找不到 SQLite 檔案：{p}")
        mode = "ro" if self.read_only else "rw"
        self.raw = sqlite3.connect(f"file:{quote(str(p))}?mode={mode}", uri=True, check_same_thread=False,
                                   timeout=self.c.timeout_s)
        if self.read_only:
            self.raw.execute("PRAGMA query_only = 1")

    # ---------- cancel (from another thread) ----------
    def cancel(self) -> None:
        try:
            if self.kind == "postgres":
                self.raw.cancel_safe()
            elif self.kind == "sqlite":
                self.raw.interrupt()
            elif self.kind == "mysql":
                import pymysql
                tid = self.raw.thread_id()
                k = pymysql.connect(host=self.c.host, port=self.c.port or 3306, user=self.c.user or None,
                                    password=self.password or "", connect_timeout=5)
                k.cursor().execute(f"KILL QUERY {int(tid)}")
                k.close()
        except Exception:  # noqa: BLE001
            pass

    # ---------- generic execution ----------
    @property
    def param(self) -> str:
        return "?" if self.kind == "sqlite" else "%s"

    def run(self, sql: str, params: tuple | list = (), limit: int | None = None) -> dict:
        limit = limit or self.c.row_limit
        t0 = time.time()
        timer = None
        if self.kind == "sqlite":   # sqlite has no server-side timeout
            timer = threading.Timer(self.c.timeout_s, self.raw.interrupt)
            timer.start()
        try:
            cur = self.raw.cursor()
            cur.execute(sql, tuple(params)) if params else cur.execute(sql)
            cols, rows, truncated = [], [], False
            if cur.description:
                cols = [d[0] for d in cur.description]
                got = cur.fetchmany(limit + 1)
                truncated = len(got) > limit
                rows = [[jsonable(v) for v in r] for r in got[:limit]]
            affected = cur.rowcount if not cur.description else None
            if self.kind == "sqlite" and not self.read_only:
                self.raw.commit()
            cur.close()
        except DbError:
            raise
        except Exception as e:  # noqa: BLE001
            msg = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
            if "interrupt" in msg.lower() or "canceling statement" in msg.lower() or "max_statement_time" in msg.lower() \
                    or "max_execution_time" in msg.lower() or "query execution was interrupted" in msg.lower():
                msg = f"查詢逾時或被中止（上限 {self.c.timeout_s} 秒）"
            if "read-only" in msg.lower() or "readonly" in msg.lower() or "read only" in msg.lower():
                msg = f"資料庫拒絕寫入（唯讀連線）：{msg}"
            raise DbError(msg[:600]) from None
        finally:
            if timer:
                timer.cancel()
        return {"columns": cols, "rows": rows, "truncated": truncated,
                "row_count": len(rows), "affected": affected if affected not in (None, -1) else None,
                "elapsed_ms": int((time.time() - t0) * 1000)}

    # ---------- introspection ----------
    def version(self) -> str:
        q = {"postgres": "SELECT version()", "mysql": "SELECT VERSION()", "sqlite": "SELECT 'SQLite ' || sqlite_version()"}
        return str(self.run(q[self.kind], limit=1)["rows"][0][0])

    def schemas(self) -> list[str]:
        if self.kind == "postgres":
            r = self.run("SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN "
                         "('pg_catalog','information_schema') AND schema_name NOT LIKE 'pg\\_toast%%' "
                         "AND schema_name NOT LIKE 'pg\\_temp%%' ORDER BY schema_name = 'public' DESC, schema_name",
                         limit=500)
            return [x[0] for x in r["rows"]]
        if self.kind == "mysql":
            if self.c.database:
                return [self.c.database]
            r = self.run("SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN "
                         "('mysql','information_schema','performance_schema','sys') ORDER BY schema_name", limit=500)
            return [x[0] for x in r["rows"]]
        return [x[1] for x in self.run("PRAGMA database_list", limit=50)["rows"]]

    def tables(self, schema: str) -> list[dict]:
        p = self.param
        if self.kind == "postgres":
            r = self.run("SELECT t.table_name, t.table_type, c.reltuples::bigint FROM information_schema.tables t "
                         "LEFT JOIN pg_namespace n ON n.nspname = t.table_schema "
                         "LEFT JOIN pg_class c ON c.relname = t.table_name AND c.relnamespace = n.oid "
                         f"WHERE t.table_schema = {p} ORDER BY t.table_type, t.table_name", (schema,), limit=5000)
        elif self.kind == "mysql":
            r = self.run("SELECT table_name, table_type, table_rows FROM information_schema.tables "
                         f"WHERE table_schema = {p} ORDER BY table_type, table_name", (schema,), limit=5000)
        else:
            r = self.run(f"SELECT name, type, NULL FROM {qident('sqlite', schema)}.sqlite_master "
                         "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY type, name",
                         limit=5000)
        out = []
        for name, typ, est in r["rows"]:
            kind = "view" if "view" in str(typ).lower() else "table"
            out.append({"name": name, "kind": kind,
                        "rows_estimate": int(est) if est not in (None, "") and int(float(est)) >= 0 else None})
        return out

    def columns(self, schema: str, table: str) -> dict:
        p = self.param
        cols, indexes, fks = [], [], []
        if self.kind == "postgres":
            r = self.run("SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                         f"WHERE table_schema = {p} AND table_name = {p} ORDER BY ordinal_position", (schema, table), limit=2000)
            pk = {x[0] for x in self.run(
                "SELECT k.column_name FROM information_schema.table_constraints c JOIN information_schema.key_column_usage k "
                "ON k.constraint_name = c.constraint_name AND k.table_schema = c.table_schema "
                f"WHERE c.constraint_type = 'PRIMARY KEY' AND c.table_schema = {p} AND c.table_name = {p}",
                (schema, table), limit=100)["rows"]}
            for name, typ, nullable, default in r["rows"]:
                cols.append({"name": name, "type": typ, "nullable": nullable == "YES", "default": default, "pk": name in pk})
            indexes = [{"name": n, "definition": d} for n, d in self.run(
                f"SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = {p} AND tablename = {p} ORDER BY indexname",
                (schema, table), limit=500)["rows"]]
            fks = [{"column": c, "references": f"{fs}.{ft}({fc})"} for c, fs, ft, fc in self.run(
                "SELECT kcu.column_name, ccu.table_schema, ccu.table_name, ccu.column_name "
                "FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu "
                "ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
                "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name "
                f"WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = {p} AND tc.table_name = {p}",
                (schema, table), limit=500)["rows"]]
        elif self.kind == "mysql":
            r = self.run("SELECT column_name, column_type, is_nullable, column_default, column_key FROM information_schema.columns "
                         f"WHERE table_schema = {p} AND table_name = {p} ORDER BY ordinal_position", (schema, table), limit=2000)
            for name, typ, nullable, default, key in r["rows"]:
                cols.append({"name": name, "type": typ, "nullable": nullable == "YES",
                             "default": None if default == "NULL" else default, "pk": key == "PRI"})
            idx: dict = {}
            for name, col, uniq in self.run(
                    "SELECT index_name, column_name, non_unique FROM information_schema.statistics "
                    f"WHERE table_schema = {p} AND table_name = {p} ORDER BY index_name, seq_in_index",
                    (schema, table), limit=1000)["rows"]:
                idx.setdefault(name, {"name": name, "columns": [], "unique": str(uniq) == "0"})["columns"].append(col)
            indexes = [{"name": v["name"], "definition": ("UNIQUE " if v["unique"] else "") + "(" + ", ".join(v["columns"]) + ")"}
                       for v in idx.values()]
            fks = [{"column": c, "references": f"{rs}.{rt}({rc})"} for c, rs, rt, rc in self.run(
                "SELECT column_name, referenced_table_schema, referenced_table_name, referenced_column_name "
                f"FROM information_schema.key_column_usage WHERE table_schema = {p} AND table_name = {p} "
                "AND referenced_table_name IS NOT NULL", (schema, table), limit=500)["rows"]]
        else:
            s, t = qident("sqlite", schema), qident("sqlite", table)
            for _cid, name, typ, notnull, default, pk in self.run(f"PRAGMA {s}.table_info({t})", limit=2000)["rows"]:
                cols.append({"name": name, "type": typ, "nullable": not notnull, "default": default, "pk": bool(pk)})
            for row in self.run(f"PRAGMA {s}.index_list({t})", limit=500)["rows"]:
                name, uniq = row[1], row[2]
                icols = [x[2] for x in self.run(f"PRAGMA {s}.index_info({qident('sqlite', name)})", limit=100)["rows"]]
                indexes.append({"name": name, "definition": ("UNIQUE " if uniq else "") + "(" + ", ".join(map(str, icols)) + ")"})
            fks = [{"column": r[3], "references": f"{r[2]}({r[4]})"} for r in
                   self.run(f"PRAGMA {s}.foreign_key_list({t})", limit=500)["rows"]]
        if not cols:
            raise DbError(f"找不到資料表 {schema}.{table}")
        return {"columns": cols, "indexes": indexes, "foreign_keys": fks}

    def qualified(self, schema: str, table: str) -> str:
        if self.kind == "mysql" and self.c.database and schema == self.c.database:
            return qident("mysql", table)
        return qident(self.kind, schema) + "." + qident(self.kind, table)


FILTER_OPS = {"=": "=", "!=": "<>", "<": "<", ">": ">", "<=": "<=", ">=": ">=", "contains": "LIKE",
              "is null": "IS NULL", "not null": "IS NOT NULL"}


def preview_sql(s: Session, schema: str, table: str, columns: list[str], page: int = 0, page_size: int = 100,
                sort: str | None = None, desc: bool = False, filters: list[dict] | None = None) -> tuple[str, list, str, list]:
    """Parameterized SELECT for the table preview. Only known column names are ever placed in the SQL."""
    known = set(columns)
    where, params = [], []
    for f in filters or []:
        col, op = f.get("column"), str(f.get("op", "=")).lower()
        if col not in known or op not in FILTER_OPS:
            raise DbError(f"不支援的篩選：{col} {op}")
        c = qident(s.kind, col)
        if op in ("is null", "not null"):
            where.append(f"{c} {FILTER_OPS[op]}")
        elif op == "contains":
            cast = f"CAST({c} AS TEXT)" if s.kind == "postgres" else (f"CAST({c} AS CHAR)" if s.kind == "mysql" else c)
            where.append(f"{cast} {'ILIKE' if s.kind == 'postgres' else 'LIKE'} {s.param}")
            params.append(f"%{f.get('value', '')}%")
        else:
            where.append(f"{c} {FILTER_OPS[op]} {s.param}")
            params.append(f.get("value"))
    base = f"FROM {s.qualified(schema, table)}" + (" WHERE " + " AND ".join(where) if where else "")
    order = ""
    if sort:
        if sort not in known:
            raise DbError(f"未知的欄位：{sort}")
        order = f" ORDER BY {qident(s.kind, sort)} {'DESC' if desc else 'ASC'}"
    page_size = max(1, min(int(page_size), 1000))
    sql = f"SELECT * {base}{order} LIMIT {page_size} OFFSET {max(0, int(page)) * page_size}"
    return sql, params, f"SELECT COUNT(*) {base}", list(params)


def friendly(e: Exception, c: Connection) -> str:
    msg = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    low = msg.lower()
    if "password authentication failed" in low or "access denied" in low:
        return "帳號或密碼錯誤"
    if "could not translate host" in low or "name or service not known" in low or "nodename nor servname" in low \
            or "can't connect" in low or "connection refused" in low or "timeout" in low or "timed out" in low:
        return f"連不到 {c.host}:{c.port or KINDS.get(c.kind)}（確認主機、連接埠與防火牆）"
    if "does not exist" in low and "database" in low or "unknown database" in low:
        return f"找不到資料庫 {c.database}"
    if "ssl" in low:
        return f"SSL 連線失敗：{msg[:200]}"
    return msg[:400]


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()
