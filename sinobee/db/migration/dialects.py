"""DDL and values for the target engine: same engine = keep native definitions; another engine = translate.

Every lossy or manual decision is reported as a note: {"level": "info"|"warn", "table", "column", "message"}.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import json
import re
import uuid

from ..drivers import qident
from .schema import Column, ForeignKey, Schema, Table, View

PG_IDENT = 63
MYSQL_IDENT = 64


def q(kind: str, name: str) -> str:
    return qident(kind, name)


def _short(name: str, limit: int) -> str:
    if len(name.encode()) <= limit:
        return name
    import hashlib
    h = hashlib.sha1(name.encode()).hexdigest()[:6]
    return name.encode()[: limit - 7].decode(errors="ignore") + "_" + h


class Notes(list):
    def add(self, level: str, message: str, table: str = "", column: str = "") -> None:
        item = {"level": level, "table": table, "column": column, "message": message}
        if item not in self:
            self.append(item)


# ------------------------------------------------------------------ type mapping
def map_type(c: Column, src: str, dst: str, notes: Notes, table: str, in_index: bool = False) -> str:
    """Column type in the target dialect."""
    if src == dst and dst != "sqlite":
        return c.native
    if dst == "postgres":
        return _to_pg(c, notes, table)
    if dst == "mysql":
        return _to_mysql(c, src, notes, table, in_index)
    return _to_sqlite(c, notes, table)


def _to_pg(c: Column, notes: Notes, table: str) -> str:
    t, u = c.type, c.unsigned
    if t in ("tinyint", "smallint"):
        return "integer" if (u and t == "smallint") else "smallint"
    if t == "mediumint":
        return "integer"
    if t == "int":
        return "bigint" if u else "integer"
    if t == "bigint":
        if u:
            notes.add("info", "BIGINT UNSIGNED 改用 numeric(20,0)（PostgreSQL 沒有無號整數）", table, c.name)
            return "numeric(20,0)"
        return "bigint"
    if t == "decimal":
        return f"numeric({c.precision},{c.scale or 0})" if c.precision else "numeric"
    if t == "float":
        return "real"
    if t == "double":
        return "double precision"
    if t == "bool":
        return "boolean"
    if t == "char":
        return f"char({c.length})" if c.length else "char(1)"
    if t == "varchar":
        return f"varchar({c.length})" if c.length else "text"
    if t == "text":
        return "text"
    if t in ("binary", "varbinary", "blob"):
        return "bytea"
    if t == "date":
        return "date"
    if t == "time":
        notes.add("info", "TIME 只能存 00:00:00–24:00:00；超出範圍的值（MySQL 允許 ±838 小時）會改存 NULL 並列出", table, c.name)
        return f"time({c.precision})" if c.precision else "time"
    if t == "datetime":
        return f"timestamp({c.precision})" if c.precision else "timestamp"
    if t == "timestamp":
        return f"timestamp({c.precision})" if c.precision else "timestamp"
    if t == "timestamptz":
        return f"timestamptz({c.precision})" if c.precision else "timestamptz"
    if t == "year":
        return "smallint"
    if t == "json":
        return "jsonb"
    if t == "enum":
        return "text"
    if t == "set":
        notes.add("info", "SET 改存逗號分隔的文字", table, c.name)
        return "text"
    if t == "bit":
        return "bigint" if (c.length or 1) > 1 else "boolean"
    if t == "uuid":
        return "uuid"
    if t == "interval":
        return "interval"
    notes.add("info" if c.native.upper() in ("TIME", "") or c.type == "other" and not c.native else "warn",
              f"類型 {c.native or '（未宣告）'} 沒有對應的 PostgreSQL 類型，改存文字", table, c.name)
    return "text"


def _to_mysql(c: Column, src: str, notes: Notes, table: str, in_index: bool) -> str:
    t = c.type
    if t in ("tinyint", "smallint", "mediumint", "int", "bigint"):
        base = {"tinyint": "tinyint", "smallint": "smallint", "mediumint": "mediumint", "int": "int",
                "bigint": "bigint"}[t]
        return base + (" unsigned" if c.unsigned else "")
    if t == "decimal":
        if c.precision:
            return f"decimal({min(c.precision, 65)},{min(c.scale or 0, 30)})"
        notes.add("info", "不限精度的 numeric 改用 DECIMAL(65,30)", table, c.name)
        return "decimal(65,30)"
    if t == "float":
        return "float"
    if t == "double":
        return "double"
    if t == "bool":
        return "tinyint(1)"
    if t == "char":
        return f"char({min(c.length or 1, 255)})"
    if t == "varchar":
        if c.length and c.length <= 16383:
            return f"varchar({c.length})"
        return "varchar(768)" if in_index else "longtext"
    if t == "text":
        if in_index:
            notes.add("info", "有索引的 text 欄位改用 VARCHAR(768)（MySQL 索引長度限制）", table, c.name)
            return "varchar(768)"
        return {"tiny": "tinytext", "medium": "mediumtext", "long": "longtext"}.get(c.size_class, "longtext"
                                                                                      if src != "mysql" else "text")
    if t in ("binary", "varbinary"):
        return f"{t}({c.length})" if c.length else "longblob"
    if t == "blob":
        return "varbinary(3072)" if in_index else "longblob"
    if t == "date":
        return "date"
    if t == "time":
        return f"time({min(c.precision or 0, 6)})" if c.precision else "time"
    if t in ("datetime", "timestamp"):
        return f"datetime({min(c.precision, 6)})" if c.precision else "datetime"
    if t == "timestamptz":
        notes.add("info", "含時區的時間改存為 UTC 的 DATETIME", table, c.name)
        return f"datetime({min(c.precision or 6, 6)})"
    if t == "year":
        return "year"
    if t == "json":
        return "json"
    if t == "enum":
        return "enum(" + ",".join("'" + v.replace("'", "''") + "'" for v in c.values) + ")" if c.values else "varchar(255)"
    if t == "set":
        return "set(" + ",".join("'" + v.replace("'", "''") + "'" for v in c.values) + ")"
    if t == "bit":
        return f"bit({c.length or 1})"
    if t == "uuid":
        return "char(36)"
    if t == "interval":
        return "varchar(64)"
    if t == "array":
        notes.add("info", "陣列改存成 JSON", table, c.name)
        return "json"
    notes.add("warn", f"類型 {c.native} 沒有對應的 MySQL 類型，改存文字", table, c.name)
    return "longtext"


def _to_sqlite(c: Column, notes: Notes | None = None, table: str = "") -> str:
    t = c.type
    if notes is not None and t == "decimal" and (c.precision or 99) > 15:
        notes.add("info", "SQLite 以浮點數儲存 DECIMAL，超過 15 位有效數字會失去精度", table, c.name)
    if t in ("tinyint", "smallint", "mediumint", "int", "bigint", "bool", "year", "bit"):
        return "INTEGER"
    if t in ("float", "double"):
        return "REAL"
    if t in ("binary", "varbinary", "blob"):
        return "BLOB"
    if t == "decimal":
        return f"DECIMAL({c.precision},{c.scale or 0})" if c.precision else "DECIMAL"
    if t in ("date", "time", "datetime", "timestamp", "timestamptz"):
        return {"date": "DATE", "time": "TIME"}.get(t, "DATETIME")
    if t == "json":
        return "JSON"
    if t in ("char", "varchar") and c.length:
        return f"VARCHAR({c.length})"
    return "TEXT"


# ------------------------------------------------------------------ defaults
def map_default(c: Column, src: str, dst: str, notes: Notes, table: str) -> str | None:
    if c.default is None:
        return None
    d = c.default.strip()
    if src == dst:
        return d
    low = d.lower()
    if c.default_is_expr:
        if re.fullmatch(r"(current_timestamp|now|localtimestamp)(\s*\(\s*\d*\s*\))?|getdate\(\)", low):
            return "CURRENT_TIMESTAMP"
        if low in ("current_date", "curdate()"):
            return "CURRENT_DATE"
        if low.startswith("nextval("):
            return None
        if re.fullmatch(r"(uuid|gen_random_uuid|uuid_generate_v4)\(\)", low):
            if dst == "postgres":
                return "gen_random_uuid()"
            if dst == "mysql":
                return "(uuid())"
        notes.add("warn", f"預設值運算式 {d} 無法自動轉換，未設定預設值", table, c.name)
        return None
    if src == "postgres":
        d = re.sub(r"::[\w\s\".\[\]]+$", "", d)        # 'x'::character varying -> 'x'
    if re.fullmatch(r"b'[01]*'", d):
        bits = int(d[2:-1] or "0", 2)
        return ("true" if bits else "false") if dst == "postgres" and (c.length or 1) == 1 else str(bits)
    if dst == "postgres" and c.type == "bool" and d in ("0", "1"):
        return "false" if d == "0" else "true"
    if dst != "postgres" and low in ("true", "false"):
        return "1" if low == "true" else "0"
    if dst == "postgres" and c.type in ("tinyint", "smallint", "int", "bigint", "decimal", "float", "double") \
            and re.fullmatch(r"'(-?\d+(\.\d+)?)'", d):
        return d[1:-1]
    if dst == "mysql" and c.type in ("text", "blob", "json") and d.startswith("'"):
        return f"({d})"     # MySQL 8 allows literal defaults on TEXT/BLOB/JSON only as expressions
    if src == "mysql" and dst != "mysql" and "\\" in d:
        d = d.replace("\\\\", "\\")
    return d


# ------------------------------------------------------------------ DDL
def create_table(t: Table, sch: Schema, dst: str, notes: Notes, *, with_fks: bool = False) -> str:
    """CREATE TABLE without secondary indexes and foreign keys (added after the data), except:
    the same-engine MySQL copy keeps SHOW CREATE TABLE as is, and SQLite gets its foreign keys inline."""
    src = sch.kind
    if src == dst == "mysql" and t.native_ddl:
        return re.sub(r"\s+AUTO_INCREMENT=\d+", "", t.native_ddl)
    if src == dst == "sqlite" and t.native_ddl:
        return t.native_ddl
    indexed = {c for i in t.indexes for c in i.columns} | set(t.primary_key) | {c for f in t.foreign_keys for c in f.columns}
    lines = []
    single_int_pk = len(t.primary_key) == 1 and (t.column(t.primary_key[0]) or Column("", "", "")).type in (
        "tinyint", "smallint", "mediumint", "int", "bigint")
    for c in t.columns:
        typ = map_type(c, src, dst, notes, t.name, c.name in indexed)
        if c.generated:
            notes.add("info", "計算欄位改為一般欄位並複製目前的值", t.name, c.name) if src != dst else None
            if src == dst == "postgres":
                lines.append(f"{q(dst, c.name)} {typ} GENERATED ALWAYS AS ({c.generated}) STORED")
                continue
        parts = [q(dst, c.name), typ]
        if c.auto_increment and not c.generated:
            if dst == "postgres":
                if src == "postgres" and "nextval(" in (c.default or ""):
                    parts[1] = {"integer": "integer", "bigint": "bigint", "smallint": "smallint"}.get(typ, typ)
                if typ in ("smallint", "integer", "bigint"):
                    parts.append("GENERATED BY DEFAULT AS IDENTITY")
                elif typ == "numeric(20,0)":
                    notes.add("warn", "BIGINT UNSIGNED 自動遞增無法使用 IDENTITY；新增資料時需自行給值", t.name, c.name)
            elif dst == "mysql":
                parts.append("NOT NULL AUTO_INCREMENT")
            elif dst == "sqlite" and single_int_pk and c.name == t.primary_key[0]:
                parts[1] = "INTEGER"
        if not c.nullable and not (dst == "mysql" and c.auto_increment):
            parts.append("NOT NULL")
        dflt = map_default(c, src, dst, notes, t.name) if not c.auto_increment else None
        if dflt is not None:
            parts.append(f"DEFAULT {dflt}")
        if c.on_update_now and dst != "mysql":
            notes.add("warn", "ON UPDATE CURRENT_TIMESTAMP 沒有自動轉換（需要時請另建觸發器）", t.name, c.name)
        if c.type == "enum" and c.values and dst in ("postgres", "sqlite") and not (src == dst):
            vals = ", ".join("'" + v.replace("'", "''") + "'" for v in c.values)
            parts.append(f"CHECK ({q(dst, c.name)} IN ({vals}))")
        if c.comment and dst == "mysql":
            parts.append("COMMENT '" + c.comment.replace("\\", "\\\\").replace("'", "''") + "'")
        if dst == "sqlite" and single_int_pk and c.name == t.primary_key[0]:
            parts.append("PRIMARY KEY" + (" AUTOINCREMENT" if c.auto_increment and src == "sqlite"
                                          and "AUTOINCREMENT" in t.native_ddl.upper() else ""))
        lines.append(" ".join(parts))
    if t.primary_key and not (dst == "sqlite" and single_int_pk):
        lines.append("PRIMARY KEY (" + ", ".join(q(dst, c) for c in t.primary_key) + ")")
    if src == dst:
        lines += [f"CHECK ({ck})" for ck in t.checks]
    elif t.checks and dst == "sqlite":
        notes.add("warn", f"{len(t.checks)} 個 CHECK 限制沒有轉換（SQLite 無法事後加入）：" + "；".join(t.checks)[:300], t.name)
    if dst == "sqlite" or with_fks:
        lines += [fk_clause(f, dst) for f in fks_kept(t, sch, notes)]
    body = ",\n  ".join(lines)
    tail = ""
    if dst == "mysql":
        tail = " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
        if t.comment:
            tail += " COMMENT='" + t.comment.replace("\\", "\\\\").replace("'", "''") + "'"
    return f"CREATE TABLE {q(dst, t.name)} (\n  {body}\n){tail}"


def translate_expr(expr: str, src: str, dst: str) -> str | None:
    """A SQL expression in another dialect (sqlglot), or None when it cannot be translated safely."""
    try:
        import sqlglot
        out = sqlglot.transpile(f"SELECT {expr}", read=src, write=dst)[0]
    except Exception:  # noqa: BLE001
        return None
    return out[len("SELECT "):] if out.upper().startswith("SELECT ") else None


def create_types(sch: Schema, dst: str) -> list[str]:
    """PostgreSQL enum types (same-engine copies keep them as types)."""
    if not (sch.kind == dst == "postgres"):
        return []
    out = []
    for name, values in sch.enums.items():
        vals = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
        out.append(f"DO $$ BEGIN CREATE TYPE {q(dst, name)} AS ENUM ({vals}); "
                   "EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    return out


def table_comments(t: Table, dst: str) -> list[str]:
    if dst != "postgres":
        return []
    out = []
    if t.comment:
        out.append(f"COMMENT ON TABLE {q(dst, t.name)} IS '" + t.comment.replace("'", "''") + "'")
    for c in t.columns:
        if c.comment:
            out.append(f"COMMENT ON COLUMN {q(dst, t.name)}.{q(dst, c.name)} IS '" + c.comment.replace("'", "''") + "'")
    return out


def fks_kept(t: Table, sch: Schema, notes: Notes) -> list[ForeignKey]:
    """Foreign keys whose referenced table is part of this migration."""
    out = []
    for f in t.foreign_keys:
        if (f.ref_schema and f.ref_schema != sch.name) or not sch.table(f.ref_table):
            notes.add("warn", f"外鍵 {f.name} 參照的 {f.ref_table} 不在這次遷移的範圍內，沒有建立", t.name)
            continue
        out.append(f)
    return out


def fk_clause(f: ForeignKey, dst: str) -> str:
    cols = ", ".join(q(dst, c) for c in f.columns)
    rcols = ", ".join(q(dst, c) for c in f.ref_columns)
    s = f"CONSTRAINT {q(dst, f.name)} FOREIGN KEY ({cols}) REFERENCES {q(dst, f.ref_table)} ({rcols})"
    if (f.on_delete or "NO ACTION").upper() != "NO ACTION":
        s += f" ON DELETE {f.on_delete.upper()}"
    if f.on_update and f.on_update.upper() not in ("NO ACTION",):
        s += f" ON UPDATE {f.on_update.upper()}"
    return s


def post_data_ddl(t: Table, sch: Schema, dst: str, notes: Notes, used_names: set[str]) -> list[str]:
    """Secondary indexes (after the data), for engines that got a bare CREATE TABLE."""
    src = sch.kind
    if src == dst and dst in ("mysql", "sqlite"):
        if dst == "sqlite":
            return [i.native for i in t.indexes if i.native]
        return []
    out = []
    pk = set(t.primary_key)
    for i in t.indexes:
        if src == dst == "postgres" and i.native:
            out.append(i.native.replace(f" ON {q('postgres', sch.name)}.", " ON "))
            continue
        if i.kind == "fulltext":
            notes.add("warn", f"全文索引 {i.name} 沒有轉換（{'PostgreSQL 請改用 tsvector + GIN' if dst == 'postgres' else '請手動建立'}）", t.name)
            continue
        if i.kind in ("spatial", "other") or not i.columns:
            notes.add("warn", f"索引 {i.name}（{i.kind}）沒有自動轉換", t.name)
            continue
        if set(i.columns) == pk and i.unique:
            continue
        if any(i.columns == f.columns for f in t.foreign_keys) and not i.unique and dst == "mysql":
            pass   # MySQL would create it for the FK anyway; keep the name
        if i.prefixes and dst != "mysql":
            notes.add("info", f"索引 {i.name} 的前綴長度（{', '.join(f'{k}({v})' for k, v in i.prefixes.items())}）改為整欄索引", t.name)
        limit = PG_IDENT if dst == "postgres" else MYSQL_IDENT
        name = i.name if dst == "mysql" else f"{t.name}_{i.name}"
        name = _short(name, limit)
        n, k = name, 2
        while n in used_names:
            n, k = _short(f"{name}_{k}", limit), k + 1
        used_names.add(n)
        cols = ", ".join(q(dst, c) + (f"({i.prefixes[c]})" if dst == "mysql" and c in i.prefixes else "") for c in i.columns)
        out.append(f"CREATE {'UNIQUE ' if i.unique else ''}INDEX {q(dst, n)} ON {q(dst, t.name)} ({cols})")
    return out


def check_ddl(t: Table, sch: Schema, dst: str, notes: Notes) -> list[str]:
    """CHECK constraints translated to another engine, added after the data (a failure is only reported)."""
    if sch.kind == dst or dst == "sqlite":
        return []
    out = []
    for ck in t.checks:
        expr = translate_expr(ck, sch.kind, dst)
        if expr:
            out.append(f"ALTER TABLE {q(dst, t.name)} ADD CHECK ({expr})")
        else:
            notes.add("warn", f"CHECK 限制沒有轉換：{ck[:200]}", t.name)
    return out


def foreign_key_ddl(t: Table, sch: Schema, dst: str) -> list[str]:
    if dst == "sqlite" or (sch.kind == dst == "mysql"):
        return []      # SQLite: inline in CREATE TABLE; MySQL same-engine: inside SHOW CREATE TABLE
    out = []
    for f in fks_kept(t, sch, Notes()):
        # constraint names only need to be unique per table (PostgreSQL) / per schema for FKs (MySQL)
        name = f.name if sch.kind == "postgres" or dst == "postgres" else f"{t.name}_{f.name}" \
            if not f.name.startswith(t.name) else f.name
        f2 = ForeignKey(_short(name, PG_IDENT if dst == "postgres" else MYSQL_IDENT),
                        f.columns, f.ref_table, f.ref_columns, f.on_delete, f.on_update)
        out.append(f"ALTER TABLE {q(dst, t.name)} ADD {fk_clause(f2, dst)}")
    return out


def sequence_reset(t: Table, dst: str) -> list[str]:
    """After the copy, continue auto-increment values after the copied maximum."""
    if dst != "postgres":
        return []
    out = []
    for c in t.columns:
        if c.auto_increment and not c.generated:
            out.append(f"SELECT setval(pg_get_serial_sequence('{q(dst, t.name)}', '{c.name}'), "
                       f"COALESCE((SELECT MAX({q(dst, c.name)}) FROM {q(dst, t.name)}), 0) + 1, false) "
                       f"WHERE pg_get_serial_sequence('{q(dst, t.name)}', '{c.name}') IS NOT NULL")
    return out


def create_view(v: View, sch: Schema, dst: str, notes: Notes) -> str | None:
    src = sch.kind
    if src == dst:
        if dst == "mysql":
            return f"CREATE VIEW {q(dst, v.name)} AS {v.sql}"
        return f"CREATE VIEW {q(dst, v.name)} AS {v.sql}"
    try:
        import sqlglot
        read = {"mysql": "mysql", "postgres": "postgres", "sqlite": "sqlite"}[src]
        write = {"mysql": "mysql", "postgres": "postgres", "sqlite": "sqlite"}[dst]
        body = sqlglot.transpile(v.sql, read=read, write=write, pretty=False)[0]
    except Exception as e:  # noqa: BLE001
        notes.add("warn", f"檢視表 {v.name} 無法自動轉換語法（{str(e)[:120]}），請手動建立", v.name)
        return None
    return f"CREATE VIEW {q(dst, v.name)} AS {body}"


# ------------------------------------------------------------------ values
class ValueIssues(dict):
    """Values changed while copying: (table, column, message) -> [rows, examples]."""

    def bump(self, table: str, column: str, message: str, example=None) -> None:
        k = (table, column, message)
        item = self.setdefault(k, [0, []])
        item[0] += 1
        if example is not None and len(item[1]) < 3:
            item[1].append(str(example)[:80])

    def as_list(self) -> list[dict]:
        return [{"table": t, "column": c, "message": m, "rows": n, "examples": ex} for (t, c, m), (n, ex) in self.items()]


def converters(t: Table, src: str, dst: str, issues: ValueIssues, dst_types: dict[str, str]):
    """One function per copied column: source driver value -> value for the target driver."""
    out = []
    for c in t.copy_columns:
        out.append(_converter(c, src, dst, issues, t.name, dst_types.get(c.name, "")))
    return out


def _converter(c: Column, src: str, dst: str, issues: ValueIssues, table: str, dst_type: str):
    t = c.type
    name = c.name
    zero = re.compile(r"^0000-00-00")

    def zero_date(v):
        if isinstance(v, str) and zero.match(v):
            issues.bump(table, name, "0000-00-00 日期改存 NULL")
            return None
        return v

    if src == dst == "mysql":
        if t in ("date", "datetime", "timestamp"):
            return lambda v: v   # zero dates are copied as they are (the target session allows them)
        return None
    if dst == "postgres":
        if t in ("date", "datetime", "timestamp"):
            return zero_date
        if t == "time":
            def time_(v):
                if isinstance(v, _dt.timedelta):
                    secs = v.total_seconds()
                    if 0 <= secs <= 86400:
                        if secs == 86400:
                            return _dt.time(0, 0)
                        s = int(secs)
                        return _dt.time(s // 3600, s // 60 % 60, s % 60, v.microseconds)
                    secs_i = int(secs)
                    issues.bump(table, name, "超出 TIME 範圍（00:00–24:00）的值改存 NULL",
                                f"{'-' if secs_i < 0 else ''}{abs(secs_i) // 3600}:{abs(secs_i) // 60 % 60:02d}:{abs(secs_i) % 60:02d}")
                    return None
                return v
            return time_
        if t == "bit":
            if dst_type == "boolean":
                return lambda v: None if v is None else bool(int.from_bytes(v, "big") if isinstance(v, (bytes, bytearray)) else v)
            return lambda v: int.from_bytes(v, "big") if isinstance(v, (bytes, bytearray)) else v
        if t in ("text", "varchar", "char", "enum", "set"):
            def txt(v):
                if isinstance(v, str) and "\x00" in v:
                    issues.bump(table, name, "文字中的 NUL 字元（\\x00）已移除（PostgreSQL 不允許）")
                    return v.replace("\x00", "")
                if isinstance(v, (bytes, bytearray)):
                    return bytes(v).decode("utf-8", "replace")
                return v
            return txt
        if t == "json":
            def js(v):
                if v is None or isinstance(v, str):
                    return v.replace("\\u0000", "") if isinstance(v, str) else v
                return json.dumps(v, ensure_ascii=False)
            return js
        if t == "bool" and src == "sqlite":
            return lambda v: None if v is None else bool(v)
        if t == "decimal" and src == "sqlite":
            return lambda v: None if v is None else decimal.Decimal(str(v))
        return None
    if dst == "mysql":
        if t == "timestamptz":
            return lambda v: v.astimezone(_dt.timezone.utc).replace(tzinfo=None) if isinstance(v, _dt.datetime) and v.tzinfo else v
        if t == "time":
            return lambda v: v.replace(tzinfo=None) if isinstance(v, _dt.time) and v.tzinfo else v
        if t == "uuid":
            return lambda v: str(v) if isinstance(v, uuid.UUID) else v
        if t == "array":
            return lambda v: json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (list, tuple)) else v
        if t == "json":
            return lambda v: v if v is None or isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        if t == "interval":
            def iv(v):
                if isinstance(v, _dt.timedelta):
                    secs = int(v.total_seconds())
                    days, rest = divmod(abs(secs), 86400)
                    sign = "-" if secs < 0 else ""
                    hms = f"{rest // 3600:02d}:{rest // 60 % 60:02d}:{rest % 60:02d}"
                    return f"{sign}{days} days {hms}" if days else f"{sign}{hms}"
                return None if v is None else str(v)
            return iv
        if t == "bool" and src == "sqlite":
            return lambda v: None if v is None else int(bool(v))
        if t in ("other",):
            return lambda v: v if v is None or isinstance(v, (str, bytes, int, float)) else str(v)
        return None
    # sqlite
    def lite(v):
        if isinstance(v, int) and not isinstance(v, bool) and not -2**63 <= v < 2**63:
            issues.bump(table, name, "超出 SQLite 整數範圍（64 位元）的值會變成浮點近似值", v)
            return str(v)
        if v is None or isinstance(v, (int, float, str, bytes)):
            if isinstance(v, str) and zero.match(v) and t in ("date", "datetime", "timestamp"):
                return v
            return v
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, decimal.Decimal):
            return str(v)
        if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
            return v.isoformat(sep=" ") if isinstance(v, _dt.datetime) else v.isoformat()
        if isinstance(v, _dt.timedelta):
            secs = int(v.total_seconds())
            sign = "-" if secs < 0 else ""
            secs = abs(secs)
            return f"{sign}{secs // 3600:02d}:{secs // 60 % 60:02d}:{secs % 60:02d}"
        if isinstance(v, (bytearray, memoryview)):
            return bytes(v)
        if isinstance(v, uuid.UUID):
            return str(v)
        if isinstance(v, (dict, list, tuple)):
            return json.dumps(v, ensure_ascii=False, default=str)
        return str(v)
    if t == "bit":
        return lambda v: int.from_bytes(v, "big") if isinstance(v, (bytes, bytearray)) else lite(v)
    return lite


# ------------------------------------------------------------------ SQL literals (export to a .sql file)
def literal(v, dst: str) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return ("TRUE" if v else "FALSE") if dst == "postgres" else ("1" if v else "0")
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return f"'{v}'" if dst == "postgres" else "NULL"
        return repr(v)
    if isinstance(v, decimal.Decimal):
        return str(v) if v.is_finite() else ("'NaN'" if dst == "postgres" else "NULL")
    if isinstance(v, (bytes, bytearray, memoryview)):
        h = bytes(v).hex()
        return {"postgres": f"'\\x{h}'::bytea", "mysql": f"X'{h}'" if h else "''", "sqlite": f"X'{h}'"}[dst]
    if isinstance(v, _dt.datetime):
        return "'" + v.isoformat(sep=" ") + "'"
    if isinstance(v, (_dt.date, _dt.time)):
        return "'" + v.isoformat() + "'"
    if isinstance(v, _dt.timedelta):
        secs = int(v.total_seconds())
        sign = "-" if secs < 0 else ""
        secs = abs(secs)
        return f"'{sign}{secs // 3600:02d}:{secs // 60 % 60:02d}:{secs % 60:02d}'"
    if isinstance(v, (dict, list, tuple)):
        v = json.dumps(v, ensure_ascii=False, default=str)
    s = str(v)
    if dst == "mysql":
        s = s.replace("\\", "\\\\").replace("'", "\\'").replace("\x00", "\\0").replace("\n", "\\n").replace("\r", "\\r")
        return f"'{s}'"
    return "'" + s.replace("'", "''") + "'"
