"""Database migration jobs: copy a database (structure + data) to another database or to a .sql file.

Phases: connect -> read structure -> create tables -> copy rows -> indexes -> foreign keys -> views -> verify.

- The source is only read, inside one consistent snapshot (MySQL: START TRANSACTION WITH CONSISTENT SNAPSHOT,
  PostgreSQL: REPEATABLE READ), page by page along the primary key.
- Progress is saved after every committed batch, so an interrupted job resumes where it stopped.
- Afterwards every table is verified: row counts, plus the first and last rows compared value by value.
- Nothing secret is stored in the job file: connections are referenced by id.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import decimal
import gzip
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from ..drivers import DbError, Session
from ..store import Connection
from . import dialects as D
from .schema import Schema, Table, introspect

log = logging.getLogger("sinobee.db.migration")
BATCH = 2000
SAMPLE = 50


class MigrationError(Exception):
    pass


# ------------------------------------------------------------------ writers
class Writer:
    kind = ""

    def execute(self, sql: str) -> None: ...
    def table_exists(self, name: str) -> bool: return False
    def row_count(self, name: str) -> int | None: return None
    def drop(self, name: str, view: bool = False) -> None: ...
    def insert(self, table: str, cols: list[str], rows: list[tuple]) -> None: ...
    def delete_after(self, table: str, pk: list[str], key: tuple, casts=None) -> None: ...
    def truncate(self, table: str) -> None: ...
    def fetch(self, sql: str) -> list[tuple]: return []
    def begin_load(self, same_engine: bool) -> None: ...
    def end_load(self) -> None: ...
    def close(self) -> None: ...


class DbWriter(Writer):
    def __init__(self, s: Session, schema: str):
        self.s, self.kind, self.schema = s, s.kind, schema
        self.raw = s.raw

    def _cur(self):
        return self.raw.cursor()

    def execute(self, sql: str) -> None:
        cur = self._cur()
        cur.execute(sql)
        cur.close()
        if self.kind == "sqlite":
            self.raw.commit()

    def fetch(self, sql: str, params=()) -> list[tuple]:
        cur = self._cur()
        cur.execute(sql, tuple(params)) if params else cur.execute(sql)
        out = [tuple(r) for r in cur.fetchall()]
        cur.close()
        return out

    def table_exists(self, name: str) -> bool:
        if self.kind == "postgres":
            return bool(self.fetch("SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
                                   (self.schema, name)))
        if self.kind == "mysql":
            return bool(self.fetch("SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() "
                                   "AND table_name = %s", (name,)))
        return bool(self.fetch("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?", (name,)))

    def row_count(self, name: str) -> int | None:
        return int(self.fetch(f"SELECT COUNT(*) FROM {D.q(self.kind, name)}")[0][0])

    def drop(self, name: str, view: bool = False) -> None:
        what = "VIEW" if view else "TABLE"
        cascade = " CASCADE" if self.kind == "postgres" else ""
        self.execute(f"DROP {what} IF EXISTS {D.q(self.kind, name)}{cascade}")

    def truncate(self, table: str) -> None:
        self.execute(f"DELETE FROM {D.q(self.kind, table)}")

    def delete_after(self, table: str, pk: list[str], key: tuple, casts: list[str] | None = None) -> None:
        p = "?" if self.kind == "sqlite" else "%s"
        cond = _key_cond(self.kind, pk, p, casts)
        cur = self._cur()
        cur.execute(f"DELETE FROM {D.q(self.kind, table)} WHERE {cond}", tuple(key))
        cur.close()
        if self.kind == "sqlite":
            self.raw.commit()

    def insert(self, table: str, cols: list[str], rows: list[tuple]) -> None:
        if not rows:
            return
        if self.kind == "postgres":
            names = ", ".join(D.q("postgres", c) for c in cols)
            cur = self._cur()
            with cur.copy(f"COPY {D.q('postgres', table)} ({names}) FROM STDIN") as cp:
                for r in rows:
                    cp.write_row(r)
            cur.close()
            return
        p = "?" if self.kind == "sqlite" else "%s"
        names = ", ".join(D.q(self.kind, c) for c in cols)
        sql = f"INSERT INTO {D.q(self.kind, table)} ({names}) VALUES ({', '.join([p] * len(cols))})"
        cur = self._cur()
        try:   # one transaction per batch, so a failed batch leaves nothing behind
            if self.kind == "mysql":
                self.raw.begin()
            cur.executemany(sql, rows)
            self.raw.commit()
        except Exception:
            try:
                self.raw.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            cur.close()

    def begin_load(self, same_engine: bool) -> None:
        if self.kind == "mysql":
            mode = "NO_AUTO_VALUE_ON_ZERO,NO_ENGINE_SUBSTITUTION" if same_engine else \
                "STRICT_ALL_TABLES,NO_AUTO_VALUE_ON_ZERO,NO_ENGINE_SUBSTITUTION"
            for stmt in ("SET SESSION FOREIGN_KEY_CHECKS = 0", "SET SESSION UNIQUE_CHECKS = 0",
                         f"SET SESSION sql_mode = '{mode}'", "SET SESSION time_zone = '+00:00'"):
                self.execute(stmt)
        elif self.kind == "postgres":
            self.execute(f"CREATE SCHEMA IF NOT EXISTS {D.q('postgres', self.schema)}")
            self.execute(f"SET search_path TO {D.q('postgres', self.schema)}")
            self.execute("SET TIME ZONE 'UTC'")
        else:
            self.execute("PRAGMA foreign_keys = OFF")

    def end_load(self) -> None:
        if self.kind == "mysql":
            self.execute("SET SESSION FOREIGN_KEY_CHECKS = 1")
            self.execute("SET SESSION UNIQUE_CHECKS = 1")


class SqliteFileWriter(DbWriter):
    """A new (or existing) SQLite file opened read-write directly."""

    def __init__(self, path: str):
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        self.path = p
        self.kind, self.schema = "sqlite", "main"
        self.raw = sqlite3.connect(str(p), check_same_thread=False, timeout=30)
        self.raw.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        try:
            self.raw.commit()
            self.raw.close()
        except Exception:  # noqa: BLE001
            pass


class SqlFileWriter(Writer):
    """Writes the migration as a SQL script for `dialect` (optionally gzip-compressed)."""

    def __init__(self, path: str, dialect: str):
        self.kind = dialect
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = gzip.open(self.path, "wt", encoding="utf-8") if self.path.suffix == ".gz" else \
            open(self.path, "w", encoding="utf-8")
        self.counts: dict[str, int] = {}

    def write(self, text: str) -> None:
        self.f.write(text)

    def execute(self, sql: str) -> None:
        self.write(sql.rstrip().rstrip(";") + ";\n")

    def drop(self, name: str, view: bool = False) -> None:
        self.execute(f"DROP {'VIEW' if view else 'TABLE'} IF EXISTS {D.q(self.kind, name)}"
                     + (" CASCADE" if self.kind == "postgres" else ""))

    def row_count(self, name: str) -> int | None:
        return self.counts.get(name, 0)

    def insert(self, table: str, cols: list[str], rows: list[tuple]) -> None:
        if not rows:
            return
        names = ", ".join(D.q(self.kind, c) for c in cols)
        head = f"INSERT INTO {D.q(self.kind, table)} ({names}) VALUES\n"
        for i in range(0, len(rows), 500):
            chunk = rows[i:i + 500]
            self.write(head + ",\n".join("(" + ", ".join(D.literal(v, self.kind) for v in r) + ")" for r in chunk) + ";\n")
        self.counts[table] = self.counts.get(table, 0) + len(rows)

    def begin_load(self, same_engine: bool) -> None:
        self.write(f"-- SinoBee database migration script ({self.kind}), {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        if self.kind == "mysql":
            self.write("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS = 0;\nSET UNIQUE_CHECKS = 0;\n"
                       "SET sql_mode = 'NO_AUTO_VALUE_ON_ZERO,NO_ENGINE_SUBSTITUTION';\nSET time_zone = '+00:00';\n\n")
        elif self.kind == "postgres":
            self.write("SET client_encoding = 'UTF8';\nSET standard_conforming_strings = on;\nSET TIME ZONE 'UTC';\n\n")
        else:
            self.write("PRAGMA foreign_keys = OFF;\nBEGIN TRANSACTION;\n\n")

    def end_load(self) -> None:
        if self.kind == "mysql":
            self.write("\nSET FOREIGN_KEY_CHECKS = 1;\nSET UNIQUE_CHECKS = 1;\n")
        elif self.kind == "sqlite":
            self.write("\nCOMMIT;\n")

    def close(self) -> None:
        try:
            self.f.close()
        except Exception:  # noqa: BLE001
            pass


def _key_cond(kind: str, exprs: list[str], p: str, casts: list[str] | None = None) -> str:
    """(k1, k2) > (?, ?) - `exprs` are quoted column names (or ctid / rowid); PostgreSQL params get casts."""
    exprs = [e if e in ("ctid", "rowid") or e[:1] in ('"', "`") else D.q(kind, e) for e in exprs]
    ps = [f"{p}::{c}" if kind == "postgres" and c else p for c in (casts or [""] * len(exprs))]
    if len(exprs) == 1:
        return f"{exprs[0]} > {ps[0]}"
    return "(" + ", ".join(exprs) + ") > (" + ", ".join(ps) + ")"


# ------------------------------------------------------------------ normalisation for verification
def norm(v, typ: str = ""):
    if v is None:
        return None
    if typ == "decimal" and isinstance(v, (int, float, str)) and not isinstance(v, bool):
        try:
            v = decimal.Decimal(repr(v) if isinstance(v, float) else str(v))
        except decimal.InvalidOperation:
            return str(v)
    elif typ in ("float", "double") and isinstance(v, (int, str, decimal.Decimal)) and not isinstance(v, bool):
        try:
            v = float(v)
        except ValueError:
            return str(v)
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, decimal.Decimal):
        if not v.is_finite():
            return str(v)
        if v == v.to_integral_value():
            return int(v)
        return format(v.normalize(), "f")
    if isinstance(v, float):
        return float(f"{v:.6g}")
    if isinstance(v, int):
        return v
    if isinstance(v, _dt.datetime):
        if v.tzinfo:
            v = v.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        return v.isoformat(sep=" ")
    if isinstance(v, (_dt.date, _dt.time)):
        if isinstance(v, _dt.time) and v.tzinfo:
            v = v.replace(tzinfo=None)
        return v.isoformat()
    if isinstance(v, _dt.timedelta):
        secs = int(v.total_seconds())
        return f"{'-' if secs < 0 else ''}{abs(secs) // 3600:02d}:{abs(secs) // 60 % 60:02d}:{abs(secs) % 60:02d}"
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    s = str(v)
    if typ == "json":
        try:
            return json.dumps(json.loads(s), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        except ValueError:
            return s
    if typ in ("decimal",):
        try:
            return norm(decimal.Decimal(s))
        except decimal.InvalidOperation:
            return s
    if typ in ("float", "double"):
        try:
            return norm(float(s))
        except ValueError:
            return s
    if typ in ("int", "bigint", "smallint", "tinyint", "mediumint", "year", "bit", "bool"):
        try:
            return int(s)
        except ValueError:
            return s
    if typ == "char":
        return s.rstrip(" ")
    if re.fullmatch(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(\.\d+)?", s):
        return s.replace("T", " ")
    return s


# ------------------------------------------------------------------ the job
@dataclasses.dataclass
class TableState:
    name: str
    rows_estimate: int | None = None
    rows_copied: int = 0
    status: str = "pending"        # pending | created | copying | copied | done | skipped | failed
    last_key: list | None = None
    keyset: bool = True
    error: str = ""
    source_rows: int | None = None
    target_rows: int | None = None
    sample_mismatches: int = 0
    seconds: float = 0.0


class Job:
    def __init__(self, manager: "MigrationManager", data: dict):
        self.m = manager
        self.d = data
        self.cancel_flag = threading.Event()
        self.thread: threading.Thread | None = None
        self.tables: dict[str, TableState] = {t["name"]: TableState(**t) for t in data.get("tables", [])}

    # ---------- persistence ----------
    @property
    def id(self) -> str:
        return self.d["id"]

    def save(self) -> None:
        self.d["tables"] = [dataclasses.asdict(t) for t in self.tables.values()]
        self.d["updated_at"] = time.time()
        self.m.save(self)

    def public(self) -> dict:
        d = dict(self.d)
        d["tables"] = [dataclasses.asdict(t) for t in self.tables.values()]
        done = sum(t.rows_copied for t in self.tables.values())
        total = sum(max(t.rows_estimate or 0, t.rows_copied) for t in self.tables.values()) or None
        d["progress"] = {"rows_copied": done, "rows_estimate": total,
                         "tables_done": sum(1 for t in self.tables.values() if t.status in ("done", "copied", "skipped")),
                         "tables_total": len(self.tables)}
        return d

    def log(self, message: str, level: str = "info") -> None:
        self.d.setdefault("log", []).append({"at": time.time(), "level": level, "message": message})
        self.d["log"] = self.d["log"][-200:]
        log.info("migration %s: %s", self.id, message)

    def phase(self, name: str) -> None:
        self.d["phase"] = name
        self.save()

    # ---------- run ----------
    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name=f"sinobee-migration-{self.id}", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        self.d["status"] = "running"
        self.d.setdefault("started_at", time.time())
        self.d["error"] = ""
        self.save()
        src_s = writer = None
        try:
            src_c = self.m.connection(self.d["source"]["id"], timeout=0)
            src_s = self.m.session(src_c, read_only=True)
            src_s.__enter__()
            self._snapshot(src_s)
            self.phase("schema")
            schema_name = self.d["source"].get("schema") or self._default_schema(src_s)
            sch = introspect(src_s, schema_name, self.d["options"].get("tables") or None)
            writer = self._open_writer()
            same = sch.kind == writer.kind
            notes, issues = D.Notes(self.d.get("notes") or []), D.ValueIssues()
            self._plan_tables(sch)
            writer.begin_load(same)
            self._create(sch, writer, notes)
            self.phase("copy")
            for t in sch.tables:
                st = self.tables[t.name]
                if st.status in ("copied", "done", "skipped"):
                    continue
                self._copy_table(src_s, sch, t, writer, issues, notes)
                if self.cancel_flag.is_set():
                    raise MigrationError("已停止")
            self.phase("indexes")
            used: set[str] = set()
            for t in sch.tables:
                for stmt in D.post_data_ddl(t, sch, writer.kind, notes, used) + D.sequence_reset(t, writer.kind) \
                        + D.check_ddl(t, sch, writer.kind, notes):
                    self._ddl(writer, stmt, t.name, notes)
            self.phase("foreign_keys")
            for t in sch.tables:
                for stmt in D.foreign_key_ddl(t, sch, writer.kind):
                    self._ddl(writer, stmt, t.name, notes)
            if self.d["options"].get("views", True):
                self.phase("views")
                for v in sch.views:
                    stmt = D.create_view(v, sch, writer.kind, notes)
                    if stmt:
                        if self.d["options"].get("if_exists") == "replace" or isinstance(writer, SqlFileWriter):
                            writer.drop(v.name, view=True)
                        self._ddl(writer, stmt, v.name, notes, what="檢視表")
            writer.end_load()
            for r in sch.routines:
                notes.add("warn", f"{ {'procedure': '預存程序', 'function': '函式', 'trigger': '觸發器', 'event': '排程事件'}.get(r['kind'], r['kind'])} "
                                  f"{r['name']} 需要手動建立", r.get("table", ""))
            self.d["routines"] = sch.routines
            self.d["notes"] = list(notes)
            self.d["value_issues"] = issues.as_list()
            self.phase("verify")
            self._verify(src_s, sch, writer)
            self.d["status"] = "done"
            self.d["phase"] = "done"
            self.d["finished_at"] = time.time()
            self.log("完成")
        except Exception as e:  # noqa: BLE001
            msg = str(e) or type(e).__name__
            if self.cancel_flag.is_set():
                self.d["status"], self.d["error"] = "stopped", "已依要求停止，可以稍後繼續"
            else:
                self.d["status"], self.d["error"] = "failed", msg[:1000]
                log.exception("migration %s failed", self.id)
            self.log(self.d["error"], "error")
        finally:
            for x in (writer,):
                try:
                    x and x.close()
                except Exception:  # noqa: BLE001
                    pass
            if src_s is not None:
                try:
                    src_s.__exit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass
            self.save()

    # ---------- steps ----------
    def _default_schema(self, s: Session) -> str:
        if s.kind == "mysql":
            if not s.c.database:
                raise MigrationError("來源連線沒有指定資料庫")
            return s.c.database
        if s.kind == "postgres":
            return "public"
        return "main"

    def _snapshot(self, s: Session) -> None:
        cur = s.raw.cursor()
        if s.kind == "mysql":
            cur.execute("SET SESSION time_zone = '+00:00'")
            cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        elif s.kind == "postgres":
            cur.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cur.execute("SET TIME ZONE 'UTC'")
        cur.close()

    def _open_writer(self) -> Writer:
        tgt = self.d["target"]
        if tgt["type"] == "sqlfile":
            mode_resume = any(t.status not in ("pending",) for t in self.tables.values())
            if mode_resume:
                for t in self.tables.values():   # a script is always written from the start
                    t.status, t.rows_copied, t.last_key = "pending", 0, None
            return SqlFileWriter(tgt["path"], tgt["dialect"])
        if tgt["type"] == "sqlite":
            return SqliteFileWriter(tgt["path"])
        c = self.m.connection(tgt["id"], timeout=0)
        s = self.m.session(c, read_only=False)
        s.__enter__()
        w = DbWriter(s, tgt.get("schema") or ("public" if c.kind == "postgres" else c.database or "main"))
        orig_close = w.close

        def close():
            orig_close()
            s.__exit__(None, None, None)
        w.close = close
        return w

    def _plan_tables(self, sch: Schema) -> None:
        for t in sch.tables:
            if t.name not in self.tables:
                self.tables[t.name] = TableState(t.name, t.rows_estimate, keyset=bool(t.primary_key))
        self.save()

    def _create(self, sch: Schema, w: Writer, notes: D.Notes) -> None:
        self.phase("create")
        replace = self.d["options"].get("if_exists") == "replace" or isinstance(w, SqlFileWriter)
        pending = [t for t in sch.tables if self.tables[t.name].status == "pending"]
        if not replace and not isinstance(w, SqlFileWriter):
            clash = [t.name for t in pending if w.table_exists(t.name)]
            if clash:
                raise MigrationError("目的地已經有同名資料表：" + "、".join(clash[:10]) +
                                     ("…" if len(clash) > 10 else "") + "。請改選「覆蓋既有資料表」或換一個目的地。")
        if replace and pending:
            for v in sch.views:
                w.drop(v.name, view=True)
            for t in reversed(pending):
                w.drop(t.name)
        for stmt in D.create_types(sch, w.kind) if pending else []:
            w.execute(stmt)
        for t in sch.tables:
            D.fks_kept(t, sch, notes)
        for t in pending:
            stmt = D.create_table(t, sch, w.kind, notes)
            try:
                w.execute(stmt)
                for c in D.table_comments(t, w.kind):
                    w.execute(c)
            except Exception as e:  # noqa: BLE001
                raise MigrationError(f"建立資料表 {t.name} 失敗：{str(e).splitlines()[0][:300]}") from None
            self.tables[t.name].status = "created"
        self.d["notes"] = list(notes)
        self.save()

    def _ddl(self, w: Writer, stmt: str, table: str, notes: D.Notes, what: str = "") -> None:
        try:
            w.execute(stmt)
        except Exception as e:  # noqa: BLE001
            msg = str(e).strip().splitlines()[0][:300] if str(e).strip() else type(e).__name__
            notes.add("warn", f"{what or '結構'}未建立：{msg}（SQL：{stmt[:200]}）", table)
            if w.kind == "postgres":
                try:
                    w.raw.rollback()
                except Exception:  # noqa: BLE001
                    pass

    def _columns(self, t: Table, src: str, dst: str):
        return [c for c in t.columns if not c.generated or src != dst]

    def _copy_table(self, s: Session, sch: Schema, t: Table, w: Writer, issues: D.ValueIssues, notes: D.Notes) -> None:
        st = self.tables[t.name]
        t0 = time.time()
        cols = self._columns(t, sch.kind, w.kind)
        names = [c.name for c in cols]
        dst_types = {c.name: D.map_type(c, sch.kind, w.kind, D.Notes(), t.name) for c in cols}
        convs = [D._converter(c, sch.kind, w.kind, issues, t.name, dst_types[c.name]) for c in cols]
        has_conv = any(convs)
        qsrc = lambda n: D.q(sch.kind, n)   # noqa: E731
        p = "?" if sch.kind == "sqlite" else "%s"
        # page along the primary key; without one: PostgreSQL ctid / SQLite rowid; MySQL streams the table
        if t.primary_key and all(k in names for k in t.primary_key):
            key_exprs = [qsrc(k) for k in t.primary_key]
            casts = [t.column(k).native if sch.kind == "postgres" else "" for k in t.primary_key]
            extra = 0
        elif sch.kind == "postgres":
            key_exprs, casts, extra = ["ctid"], ["tid"], 1
        elif sch.kind == "sqlite":
            key_exprs, casts, extra = ["rowid"], [""], 1
        else:
            key_exprs, casts, extra = [], [], 0
        st.keyset = bool(key_exprs)
        if st.status == "copying" and not isinstance(w, SqlFileWriter):   # resume
            if key_exprs and st.last_key is not None and not extra:
                w.delete_after(t.name, t.primary_key, tuple(_unjsonkey(v) for v in st.last_key),
                               [dst_types[k] if w.kind == "postgres" else "" for k in t.primary_key])
            else:   # no primary key: start the table again
                w.truncate(t.name)
                st.rows_copied, st.last_key = 0, None
        st.status = "copying"
        self.save()
        batch = int(self.d["options"].get("batch_size") or BATCH)
        sel = ", ".join(([key_exprs[0]] if extra else []) + [qsrc(n) for n in names])
        table_ref = qsrc(t.name)

        def convert(rows):
            if not has_conv:
                return [tuple(r[extra:]) for r in rows]
            return [tuple(f(v) if f else v for f, v in zip(convs, r[extra:])) for r in rows]

        skip_bad = bool(self.d["options"].get("skip_bad_rows"))
        key_names = t.primary_key if not extra and t.primary_key else []

        def describe(row) -> str:
            vals = row[extra:]
            if key_names:
                return "、".join(f"{k}={vals[names.index(k)]!r}" for k in key_names)[:120]
            return f"{key_exprs[0] if extra else '列'}={row[0]!r}" if extra else "（沒有主鍵）"

        def write(rows):
            conv = convert(rows)
            try:
                w.insert(t.name, names, conv)
                return len(conv)
            except Exception as e:  # noqa: BLE001
                first_error = str(e).strip().splitlines()[0][:300] if str(e).strip() else type(e).__name__
                if isinstance(w, SqlFileWriter):
                    raise MigrationError(f"寫入 {t.name} 失敗：{first_error}") from None
            # find the rows the target refuses (one by one), then stop or skip them
            ok = 0
            for raw_row, row in zip(rows, conv):
                try:
                    w.insert(t.name, names, [row])
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    err = str(e).strip().splitlines()[0][:300] if str(e).strip() else type(e).__name__
                    if not skip_bad:
                        raise MigrationError(f"寫入 {t.name} 失敗（{describe(raw_row)}）：{err}。"
                                             "可修正來源資料後繼續，或勾選「略過無法寫入的列」") from None
                    bad = self.d.setdefault("skipped_rows", {}).setdefault(t.name, {"count": 0, "examples": []})
                    bad["count"] += 1
                    if len(bad["examples"]) < 20:
                        bad["examples"].append({"row": describe(raw_row), "error": err})
            return ok

        last_save = time.time()
        key = [_unjsonkey(v) for v in st.last_key] if st.last_key is not None else None
        if key_exprs:
            order = ", ".join(key_exprs)
            key_pos = [0] if extra else [names.index(k) for k in t.primary_key]
            while not self.cancel_flag.is_set():
                if key is None:
                    sql, params = f"SELECT {sel} FROM {table_ref} ORDER BY {order} LIMIT {batch}", ()
                else:
                    sql = f"SELECT {sel} FROM {table_ref} WHERE {_key_cond(sch.kind, key_exprs, p, casts)} " \
                          f"ORDER BY {order} LIMIT {batch}"
                    params = tuple(key)
                cur = s.raw.cursor()
                cur.execute(sql, params) if params else cur.execute(sql)
                rows = cur.fetchall()
                cur.close()
                if not rows:
                    break
                st.rows_copied += write(rows)
                key = [rows[-1][i] if not extra else str(rows[-1][0]) for i in key_pos]
                st.last_key = [_jsonkey(v) for v in key]
                if time.time() - last_save > 1.5:
                    self.save()
                    last_save = time.time()
                if len(rows) < batch:
                    break
        else:
            cur = self._stream_cursor(s)
            cur.execute(f"SELECT {sel} FROM {table_ref}")
            try:
                while not self.cancel_flag.is_set():
                    rows = cur.fetchmany(batch)
                    if not rows:
                        break
                    st.rows_copied += write(rows)
                    if time.time() - last_save > 1.5:
                        self.save()
                        last_save = time.time()
            finally:
                try:
                    cur.close()
                except Exception:  # noqa: BLE001
                    pass
        st.seconds = round(st.seconds + time.time() - t0, 1)
        if self.cancel_flag.is_set():
            self.save()
            return
        st.status = "copied"
        self.log(f"{t.name}：已複製 {st.rows_copied:,} 列（{st.seconds} 秒）")
        self.save()

    def _stream_cursor(self, s: Session):
        if s.kind == "mysql":
            import pymysql.cursors
            return s.raw.cursor(pymysql.cursors.SSCursor)
        return s.raw.cursor()

    def _verify(self, s: Session, sch: Schema, w: Writer) -> None:
        file_target = isinstance(w, SqlFileWriter)
        for t in sch.tables:
            st = self.tables[t.name]
            if st.status not in ("copied", "done"):
                continue
            try:
                cur = s.raw.cursor()
                cur.execute(f"SELECT COUNT(*) FROM {D.q(sch.kind, t.name)}")
                st.source_rows = int(cur.fetchone()[0])
                cur.close()
                st.target_rows = w.row_count(t.name)
                if not file_target and t.primary_key:
                    st.sample_mismatches = self._sample(s, sch, t, w)
            except Exception as e:  # noqa: BLE001
                st.error = f"驗證失敗：{str(e).splitlines()[0][:200]}"
            st.status = "done"
            self.save()

    def _sample(self, s: Session, sch: Schema, t: Table, w: DbWriter) -> int:
        """Compares the first and last SAMPLE rows (by primary key) value by value; returns differing rows."""
        cols = self._columns(t, sch.kind, w.kind)
        names = [c.name for c in cols]
        if not all(k in names for k in t.primary_key):
            return 0
        types = [c.type for c in cols]
        kpos = [names.index(k) for k in t.primary_key]
        issues = D.ValueIssues()
        dst_types = {c.name: D.map_type(c, sch.kind, w.kind, D.Notes(), t.name) for c in cols}
        convs = [D._converter(c, sch.kind, w.kind, issues, t.name, dst_types[c.name]) for c in cols]
        skipped = {e["row"] for e in (self.d.get("skipped_rows") or {}).get(t.name, {}).get("examples", [])}
        mism = 0
        for direction in ("ASC", "DESC"):
            order = ", ".join(f"{D.q(sch.kind, k)} {direction}" for k in t.primary_key)
            cur = s.raw.cursor()
            cur.execute(f"SELECT {', '.join(D.q(sch.kind, n) for n in names)} FROM {D.q(sch.kind, t.name)} "
                        f"ORDER BY {order} LIMIT {SAMPLE}")
            src_rows = [tuple(f(v) if f else v for f, v in zip(convs, r)) for r in cur.fetchall()]
            cur.close()
            if not src_rows:
                continue
            p = "?" if w.kind == "sqlite" else "%s"
            cond = " OR ".join("(" + " AND ".join(f"{D.q(w.kind, k)} = {p}" for k in t.primary_key) + ")"
                               for _ in src_rows)
            params = [r[i] for r in src_rows for i in kpos]
            dst = {tuple(norm(r[i], types[i]) for i in kpos): r for r in w.fetch(
                f"SELECT {', '.join(D.q(w.kind, n) for n in names)} FROM {D.q(w.kind, t.name)} WHERE {cond}", params)}
            for a in src_rows:
                key = tuple(norm(a[i], types[i]) for i in kpos)
                b = dst.get(key)
                if b is None:
                    if "、".join(f"{k}={a[i]!r}" for k, i in zip(t.primary_key, kpos))[:120] not in skipped:
                        mism += 1
                        self.log(f"{t.name} 抽樣比對：目的地缺少 {dict(zip(t.primary_key, key))}", "warn")
                    continue
                diff = [(n, norm(x, ty), norm(y, ty)) for n, x, y, ty in zip(names, a, b, types) if norm(x, ty) != norm(y, ty)]
                if diff:
                    mism += 1
                    if mism <= 3:
                        self.log(f"{t.name} 抽樣比對不一致：{diff[:3]}", "warn")
        return mism

def _unjsonkey(v):
    if isinstance(v, dict) and "hex" in v:
        return bytes.fromhex(v["hex"])
    return v


def _jsonkey(v):
    """Primary key values as JSON for the job file (restored with the right type by the driver's param handling)."""
    if isinstance(v, (int, str)) or v is None:
        return v
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"hex": bytes(v).hex()}
    if isinstance(v, uuid.UUID):
        return str(v)
    return str(v)


# ------------------------------------------------------------------ manager
class MigrationManager:
    """Jobs live in $SINOBEE_HOME/db/migrations/<id>.json; one job runs at a time per source connection."""

    def __init__(self, service):
        self.svc = service
        self.dir = Path(service.config.home) / "db" / "migrations"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.exports = Path(service.config.home) / "exports"
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        for p in sorted(self.dir.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if d.get("status") in ("running", "queued"):
                d["status"], d["error"] = "interrupted", "SinoBee 重新啟動時中斷，可以繼續"
            self.jobs[d["id"]] = Job(self, d)

    # ---------- helpers ----------
    def connection(self, cid: str, timeout: int | None = None) -> Connection:
        c = self.svc.get(cid)
        return dataclasses.replace(c, timeout_s=timeout) if timeout is not None else c

    def session(self, c: Connection, read_only: bool) -> Session:
        return self.svc.session(c, read_only=read_only)

    def save(self, job: Job) -> None:
        p = self.dir / f"{job.id}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(job.d, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(p)

    def list(self) -> list[dict]:
        return sorted((j.public() for j in self.jobs.values()), key=lambda d: d.get("created_at", 0), reverse=True)

    def get(self, jid: str) -> Job:
        j = self.jobs.get(jid)
        if not j:
            raise MigrationError("找不到這個遷移工作")
        return j

    # ---------- target spec ----------
    def _target(self, spec: dict, source: Connection) -> dict:
        typ = spec.get("type")
        if typ == "connection":
            c = self.svc.get(str(spec.get("id") or ""))
            if c.id == source.id and (spec.get("schema") or "") in ("", source.database):
                raise MigrationError("目的地不能和來源是同一個資料庫")
            return {"type": "connection", "id": c.id, "kind": c.kind, "label": c.name,
                    "schema": str(spec.get("schema") or "") or None}
        if typ == "sqlite":
            path = str(spec.get("path") or "").strip()
            if not path:
                raise MigrationError("請指定 SQLite 檔案路徑")
            return {"type": "sqlite", "kind": "sqlite", "path": str(Path(path).expanduser()), "label": Path(path).name}
        if typ == "sqlfile":
            dialect = spec.get("dialect") or source.kind
            if dialect not in ("mysql", "postgres", "sqlite"):
                raise MigrationError("SQL 檔的語法需為 mysql、postgres 或 sqlite")
            path = str(spec.get("path") or "").strip()
            if not path:
                stamp = time.strftime("%Y%m%d-%H%M%S")
                name = re.sub(r"[^\w.-]+", "_", source.database or source.name)
                path = str(self.exports / (f"{name}-{dialect}-{stamp}.sql" + (".gz" if spec.get("gzip", True) else "")))
            return {"type": "sqlfile", "kind": dialect, "dialect": dialect, "path": str(Path(path).expanduser()),
                    "label": Path(path).name}
        raise MigrationError("目的地類型需為 connection、sqlite 或 sqlfile")

    # ---------- plan / start / control ----------
    def plan(self, source_id: str, target: dict, options: dict | None = None) -> dict:
        """Reads the source structure and reports what the migration would do (nothing is written)."""
        options = options or {}
        src = self.connection(source_id, timeout=0)
        tgt = self._target(target, src)
        with self.session(src, read_only=True) as s:
            schema = (options.get("schema") or self._source_schema(s))
            sch = introspect(s, schema, options.get("tables") or None)
        notes = D.Notes()
        dst = tgt["kind"]
        used: set[str] = set()
        for t in sch.tables:
            D.create_table(t, sch, dst, notes)
            D.post_data_ddl(t, sch, dst, notes, used)
            D.check_ddl(t, sch, dst, notes)
            D.fks_kept(t, sch, notes)
        for v in sch.views:
            D.create_view(v, sch, dst, notes)
        for r in sch.routines:
            notes.add("warn", f"{ {'procedure': '預存程序', 'function': '函式', 'trigger': '觸發器', 'event': '排程事件'}.get(r['kind'], r['kind'])} "
                              f"{r['name']} 需要手動建立", r.get("table", ""))
        if sch.kind == "mysql" and dst != "mysql" and any(c.type == "timestamp" for t in sch.tables for c in t.columns):
            notes.add("info", "MySQL 的 TIMESTAMP 會以 UTC 時間寫入")
        conflicts: list[str] = []
        if tgt["type"] == "connection":
            c = self.connection(tgt["id"], timeout=0)
            try:
                with self.session(c, read_only=True) as ts:
                    w = DbWriter(ts, tgt.get("schema") or ("public" if c.kind == "postgres" else c.database))
                    conflicts = [t.name for t in sch.tables if w.table_exists(t.name)]
                    target_version = ts.version()
            except DbError as e:
                raise MigrationError(f"目的地連線失敗：{e}") from None
        else:
            target_version = ""
        if tgt["type"] == "sqlite" and Path(tgt["path"]).exists():
            w = SqliteFileWriter(tgt["path"])
            conflicts = [t.name for t in sch.tables if w.table_exists(t.name)]
            w.close()
        return {
            "source": {"id": src.id, "name": src.name, "kind": sch.kind, "schema": sch.name, "version": sch.version},
            "target": {**tgt, "version": target_version},
            "tables": [{"name": t.name, "rows_estimate": t.rows_estimate, "columns": len(t.columns),
                        "primary_key": t.primary_key, "indexes": len(t.indexes), "foreign_keys": len(t.foreign_keys)}
                       for t in sch.tables],
            "views": [v.name for v in sch.views],
            "routines": sch.routines,
            "notes": list(notes),
            "conflicts": conflicts,
            "same_engine": sch.kind == dst,
        }

    def _source_schema(self, s: Session) -> str:
        if s.kind == "mysql":
            if not s.c.database:
                raise MigrationError("來源連線沒有指定資料庫，請在連線設定填入資料庫名稱")
            return s.c.database
        return "public" if s.kind == "postgres" else "main"

    def start(self, source_id: str, target: dict, options: dict | None = None) -> dict:
        options = dict(options or {})
        if options.get("if_exists") not in (None, "fail", "replace"):
            raise MigrationError("if_exists 需為 fail 或 replace")
        src = self.connection(source_id)
        tgt = self._target(target, src)
        with self._lock:
            busy = [j for j in self.jobs.values() if j.d.get("status") == "running"
                    and (j.d["source"]["id"] == src.id or (tgt["type"] == "connection" and j.d["target"].get("id") == tgt.get("id")))]
            if busy:
                raise MigrationError("這個連線已經有遷移工作在執行")
            jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
            d = {"id": jid, "created_at": time.time(), "status": "queued", "phase": "connect",
                 "source": {"id": src.id, "name": src.name, "kind": src.kind, "schema": options.get("schema") or None},
                 "target": tgt, "options": {"tables": options.get("tables") or None,
                                            "views": options.get("views", True) is not False,
                                            "if_exists": options.get("if_exists") or "fail",
                                            "batch_size": int(options.get("batch_size") or BATCH),
                                            "skip_bad_rows": bool(options.get("skip_bad_rows"))},
                 "tables": [], "notes": [], "log": []}
            job = Job(self, d)
            self.jobs[jid] = job
            job.save()
        job.start()
        return job.public()

    def resume(self, jid: str, options: dict | None = None) -> dict:
        job = self.get(jid)
        if options and "skip_bad_rows" in options:
            job.d["options"]["skip_bad_rows"] = bool(options["skip_bad_rows"])
        if job.d.get("status") == "running":
            raise MigrationError("這個工作正在執行")
        if job.d.get("status") == "done":
            raise MigrationError("這個工作已經完成")
        job.cancel_flag.clear()
        job.start()
        return job.public()

    def stop(self, jid: str) -> dict:
        job = self.get(jid)
        job.cancel_flag.set()
        return job.public()

    def delete(self, jid: str) -> None:
        job = self.get(jid)
        if job.d.get("status") == "running":
            raise MigrationError("請先停止這個工作")
        (self.dir / f"{jid}.json").unlink(missing_ok=True)
        self.jobs.pop(jid, None)
