"""A neutral description of a database schema, read from MySQL/MariaDB, PostgreSQL or SQLite.

Each column keeps its `native` type (used as-is when the target is the same engine) and a neutral `type`
(used to translate it for a different engine).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..drivers import Session, qident

NEUTRAL = ("tinyint", "smallint", "mediumint", "int", "bigint", "decimal", "float", "double", "bool", "char",
           "varchar", "text", "binary", "varbinary", "blob", "date", "time", "datetime", "timestamp", "timestamptz",
           "year", "json", "enum", "set", "bit", "uuid", "interval", "array", "other")


@dataclass
class Column:
    name: str
    type: str                       # neutral type (NEUTRAL)
    native: str                     # the source engine's type, e.g. "int(10) unsigned", "character varying(80)"
    nullable: bool = True
    length: int | None = None       # char / varchar / binary / bit length
    precision: int | None = None    # decimal precision, or fractional seconds for time types
    scale: int | None = None
    unsigned: bool = False
    values: list[str] = field(default_factory=list)     # enum / set members
    default: str | None = None      # SQL text of the default in the source dialect (None = no default)
    default_is_expr: bool = False   # e.g. CURRENT_TIMESTAMP, uuid(), nextval(...)
    auto_increment: bool = False    # AUTO_INCREMENT / identity / serial / INTEGER PRIMARY KEY
    generated: str | None = None    # expression of a generated (computed) column
    on_update_now: bool = False     # MySQL "ON UPDATE CURRENT_TIMESTAMP"
    comment: str = ""
    size_class: str = ""            # tiny / medium / long for text and blob


@dataclass
class Index:
    name: str
    columns: list[str]
    unique: bool = False
    kind: str = "btree"             # btree | fulltext | spatial | other
    prefixes: dict[str, int] = field(default_factory=dict)
    native: str = ""                # PostgreSQL CREATE INDEX statement (same-engine copies)


@dataclass
class ForeignKey:
    name: str
    columns: list[str]
    ref_table: str
    ref_columns: list[str]
    on_delete: str = "NO ACTION"
    on_update: str = "NO ACTION"
    ref_schema: str = ""


@dataclass
class Table:
    name: str
    columns: list[Column]
    primary_key: list[str] = field(default_factory=list)
    indexes: list[Index] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    comment: str = ""
    rows_estimate: int | None = None
    native_ddl: str = ""            # SHOW CREATE TABLE / sqlite_master.sql

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def copy_columns(self) -> list[Column]:
        return [c for c in self.columns if not c.generated]


@dataclass
class View:
    name: str
    sql: str                        # the SELECT
    native_ddl: str = ""
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Schema:
    kind: str
    name: str
    version: str
    tables: list[Table] = field(default_factory=list)
    views: list[View] = field(default_factory=list)
    routines: list[dict] = field(default_factory=list)   # {kind, name, definition} not migrated automatically
    flavor: str = ""                # "mariadb" for MariaDB
    enums: dict[str, list[str]] = field(default_factory=dict)   # PostgreSQL enum types used by the tables

    def table(self, name: str) -> Table | None:
        return next((t for t in self.tables if t.name == name), None)


def _rows(s: Session, sql: str, params=()) -> list[tuple]:
    cur = s.raw.cursor()
    cur.execute(sql, tuple(params)) if params else cur.execute(sql)
    out = cur.fetchall()
    cur.close()
    return [tuple(r) for r in out]


def _int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _enum_values(column_type: str) -> list[str]:
    m = re.search(r"\((.*)\)", column_type, re.S)
    if not m:
        return []
    return [v.replace("''", "'").replace("\\\\", "\\") for v in re.findall(r"'((?:[^']|'')*)'", m.group(1))]


# ------------------------------------------------------------------ MySQL / MariaDB
def _mysql_neutral(data_type: str, column_type: str) -> tuple[str, str]:
    dt = data_type.lower()
    ct = column_type.lower()
    size = ""
    if dt in ("tinytext", "mediumtext", "longtext"):
        size, dt = dt[:-4], "text"
    elif dt in ("tinyblob", "mediumblob", "longblob"):
        size, dt = dt[:-4], "blob"
    mapping = {"integer": "int", "dec": "decimal", "numeric": "decimal", "fixed": "decimal", "real": "double",
               "double precision": "double", "boolean": "bool", "character": "char", "nchar": "char",
               "nvarchar": "varchar", "geometry": "other", "point": "other", "linestring": "other",
               "polygon": "other", "multipoint": "other", "multilinestring": "other", "multipolygon": "other",
               "geometrycollection": "other", "inet4": "varchar", "inet6": "varchar"}
    dt = mapping.get(dt, dt)
    if dt == "tinyint" and ct.startswith("tinyint(1)") and "unsigned" not in ct:
        dt = "tinyint"     # kept numeric: tinyint(1) often holds values other than 0/1
    if dt not in NEUTRAL:
        dt = "other"
    return dt, size


def _mysql_default(raw, extra: str, mariadb: bool, data_type: str) -> tuple[str | None, bool]:
    """(default SQL, is_expression) from information_schema.COLUMNS (MySQL and MariaDB differ)."""
    extra = (extra or "").lower()
    if raw is None:
        return None, False
    s = str(raw)
    if mariadb:
        if s == "NULL":
            return None, False
        if s.startswith("'") and s.endswith("'"):
            return s, False                               # quoted literal
        if re.fullmatch(r"-?\d+(\.\d+)?([eE][-+]?\d+)?", s) or re.fullmatch(r"b'[01]*'", s):
            return s, False
        return s, True                                    # expression, e.g. current_timestamp(3)
    if "default_generated" in extra or re.fullmatch(r"(current_timestamp|now|localtimestamp)(\(\d*\))?", s, re.I):
        return s, True
    if re.fullmatch(r"b'[01]*'", s):
        return s, False
    if data_type in ("tinyint", "smallint", "mediumint", "int", "bigint", "decimal", "float", "double", "year",
                     "bool", "bit") and re.fullmatch(r"-?\d+(\.\d+)?([eE][-+]?\d+)?", s):
        return s, False
    return "'" + s.replace("\\", "\\\\").replace("'", "''") + "'", False


def introspect_mysql(s: Session, db: str, tables: list[str] | None = None) -> Schema:
    version = str(_rows(s, "SELECT VERSION()")[0][0])
    mariadb = "mariadb" in version.lower()
    sch = Schema("mysql", db, version, flavor="mariadb" if mariadb else "mysql")
    trows = _rows(s, "SELECT table_name, table_type, table_rows, table_comment FROM information_schema.tables "
                     "WHERE table_schema = %s ORDER BY table_name", (db,))
    want = set(tables) if tables else None
    cols: dict[str, list] = {}
    for r in _rows(s, "SELECT table_name, column_name, data_type, column_type, is_nullable, column_default, extra, "
                      "character_maximum_length, numeric_precision, numeric_scale, datetime_precision, column_comment, "
                      "generation_expression FROM information_schema.columns WHERE table_schema = %s "
                      "ORDER BY table_name, ordinal_position", (db,)):
        cols.setdefault(r[0], []).append(r)
    json_cols = set()
    if mariadb:   # MariaDB's JSON is LONGTEXT + CHECK (json_valid(col))
        for tname, clause in _rows(s, "SELECT table_name, check_clause FROM information_schema.check_constraints "
                                      "WHERE constraint_schema = %s", (db,)):
            m = re.fullmatch(r"\s*json_valid\(`([^`]+)`\)\s*", clause or "", re.I)
            if m:
                json_cols.add((tname, m.group(1)))
    idx: dict[str, dict] = {}
    for tname, iname, col, non_unique, sub, itype, seq in _rows(
            s, "SELECT table_name, index_name, column_name, non_unique, sub_part, index_type, seq_in_index "
               "FROM information_schema.statistics WHERE table_schema = %s ORDER BY table_name, index_name, seq_in_index",
            (db,)):
        d = idx.setdefault(tname, {}).setdefault(iname, {"cols": [], "unique": str(non_unique) == "0",
                                                         "type": (itype or "").lower(), "prefix": {}})
        if col is None:       # functional index (MySQL 8): cannot be copied column-wise
            d["type"] = "functional"
            continue
        d["cols"].append(col)
        if sub:
            d["prefix"][col] = int(sub)
    fks: dict[str, dict] = {}
    for tname, cname, col, rschema, rtable, rcol, upd, dele in _rows(
            s, "SELECT k.table_name, k.constraint_name, k.column_name, k.referenced_table_schema, k.referenced_table_name, "
               "k.referenced_column_name, r.update_rule, r.delete_rule FROM information_schema.key_column_usage k "
               "JOIN information_schema.referential_constraints r ON r.constraint_schema = k.constraint_schema "
               "AND r.constraint_name = k.constraint_name AND r.table_name = k.table_name "
               "WHERE k.table_schema = %s AND k.referenced_table_name IS NOT NULL "
               "ORDER BY k.table_name, k.constraint_name, k.ordinal_position", (db,)):
        f = fks.setdefault(tname, {}).setdefault(cname, ForeignKey(cname, [], rtable, [], dele or "NO ACTION",
                                                                     upd or "NO ACTION", rschema or ""))
        f.columns.append(col)
        f.ref_columns.append(rcol)
    checks: dict[str, list[str]] = {}
    try:
        for tname, clause in _rows(s, "SELECT tc.table_name, cc.check_clause FROM information_schema.table_constraints tc "
                                      "JOIN information_schema.check_constraints cc ON cc.constraint_schema = tc.constraint_schema "
                                      "AND cc.constraint_name = tc.constraint_name WHERE tc.table_schema = %s "
                                      "AND tc.constraint_type = 'CHECK'", (db,)):
            if not re.fullmatch(r"\s*json_valid\(`[^`]+`\)\s*", clause or "", re.I):
                checks.setdefault(tname, []).append(clause)
    except Exception:  # noqa: BLE001 - MySQL < 8.0.16 has no check_constraints
        pass

    for tname, ttype, est, tcomment in trows:
        if want is not None and tname not in want:
            continue
        if "VIEW" in str(ttype).upper():
            ddl = _rows(s, f"SHOW CREATE VIEW {qident('mysql', db)}.{qident('mysql', tname)}")[0][1]
            body = re.sub(r"^.*?\bVIEW\s+`(?:[^`]|``)+`(?:\.`(?:[^`]|``)+`)?\s+AS\s+", "", ddl, count=1, flags=re.S | re.I)
            body = body.replace(f"{qident('mysql', db)}.", "")
            sch.views.append(View(tname, body, native_ddl=re.sub(r"DEFINER=\S+\s+", "", ddl)))
            continue
        columns = []
        pk: list[str] = []
        for (_t, name, data_type, column_type, nullable, default, extra, clen, nprec, nscale, dprec, comment,
             gen) in cols.get(tname, []):
            neutral, size = _mysql_neutral(data_type, column_type)
            if (tname, name) in json_cols:
                neutral = "json"
            dflt, is_expr = _mysql_default(default, extra, mariadb, neutral)
            c = Column(name=name, type=neutral, native=column_type, nullable=nullable == "YES",
                       unsigned="unsigned" in column_type.lower(), comment=comment or "", size_class=size,
                       auto_increment="auto_increment" in (extra or "").lower(),
                       on_update_now="on update" in (extra or "").lower(),
                       generated=gen or None if "generated" in (extra or "").lower() else None)
            if neutral in ("char", "varchar", "binary", "varbinary"):
                c.length = _int(clen)
            elif neutral == "bit":
                m = re.search(r"\((\d+)\)", column_type)
                c.length = int(m.group(1)) if m else 1
            elif neutral == "decimal":
                c.precision, c.scale = _int(nprec), _int(nscale)
            elif neutral in ("time", "datetime", "timestamp"):
                c.precision = _int(dprec) or 0
            elif neutral in ("enum", "set"):
                c.values = _enum_values(column_type)
            if not c.generated:
                c.default, c.default_is_expr = dflt, is_expr
            columns.append(c)
        tidx = idx.get(tname, {})
        if "PRIMARY" in tidx:
            pk = tidx["PRIMARY"]["cols"]
        indexes = [Index(n, d["cols"], d["unique"],
                         "fulltext" if d["type"] == "fulltext" else "spatial" if d["type"] == "spatial"
                         else "other" if d["type"] == "functional" else "btree", d["prefix"])
                   for n, d in tidx.items() if n != "PRIMARY"]
        native = _rows(s, f"SHOW CREATE TABLE {qident('mysql', db)}.{qident('mysql', tname)}")[0][1]
        sch.tables.append(Table(tname, columns, pk, indexes, list(fks.get(tname, {}).values()), checks.get(tname, []),
                                tcomment or "", _int(est), native))
    for kind, name, body in _rows(s, "SELECT routine_type, routine_name, routine_definition FROM information_schema.routines "
                                     "WHERE routine_schema = %s", (db,)):
        sch.routines.append({"kind": str(kind).lower(), "name": name, "definition": body or ""})
    for name, table, timing, event, body in _rows(
            s, "SELECT trigger_name, event_object_table, action_timing, event_manipulation, action_statement "
               "FROM information_schema.triggers WHERE trigger_schema = %s", (db,)):
        sch.routines.append({"kind": "trigger", "name": name, "table": table,
                             "definition": f"{timing} {event} ON {table}: {body}"})
    try:
        for (name,) in _rows(s, "SELECT event_name FROM information_schema.events WHERE event_schema = %s", (db,)):
            sch.routines.append({"kind": "event", "name": name, "definition": ""})
    except Exception:  # noqa: BLE001
        pass
    _view_deps(sch)
    return sch


# ------------------------------------------------------------------ PostgreSQL
_PG_MAP = {"smallint": "smallint", "integer": "int", "bigint": "bigint", "numeric": "decimal", "real": "float",
           "double precision": "double", "boolean": "bool", "character": "char", "character varying": "varchar",
           "text": "text", "bytea": "blob", "date": "date", "time without time zone": "time",
           "time with time zone": "time", "timestamp without time zone": "datetime",
           "timestamp with time zone": "timestamptz", "json": "json", "jsonb": "json", "uuid": "uuid",
           "interval": "interval", "ARRAY": "array", "bit": "bit", "bit varying": "bit", "money": "decimal",
           "citext": "text", "inet": "varchar", "cidr": "varchar", "macaddr": "varchar", "xml": "text"}


def introspect_postgres(s: Session, schema: str, tables: list[str] | None = None) -> Schema:
    version = str(_rows(s, "SELECT version()")[0][0])
    sch = Schema("postgres", schema, version)
    want = set(tables) if tables else None
    enums: dict[str, list[str]] = {}
    for tname, label in _rows(s, "SELECT t.typname, e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid "
                                 "ORDER BY t.typname, e.enumsortorder"):
        enums.setdefault(tname, []).append(label)
    cols: dict[str, list] = {}
    for r in _rows(s, "SELECT c.table_name, c.column_name, c.data_type, c.udt_name, c.is_nullable, c.column_default, "
                      "c.character_maximum_length, c.numeric_precision, c.numeric_scale, c.datetime_precision, "
                      "c.is_identity, c.is_generated, c.generation_expression, "
                      "format_type(a.atttypid, a.atttypmod), col_description(a.attrelid, a.attnum) "
                      "FROM information_schema.columns c "
                      "JOIN pg_attribute a ON a.attrelid = (quote_ident(c.table_schema)||'.'||quote_ident(c.table_name))::regclass "
                      "AND a.attname = c.column_name "
                      "WHERE c.table_schema = %s ORDER BY c.table_name, c.ordinal_position", (schema,)):
        cols.setdefault(r[0], []).append(r)
    trows = _rows(s, "SELECT c.relname, c.relkind, c.reltuples::bigint, obj_description(c.oid, 'pg_class') "
                     "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                     "WHERE n.nspname = %s AND c.relkind IN ('r','p','v','m') ORDER BY c.relname", (schema,))
    pks: dict[str, list[str]] = {}
    uniq_constraints: dict[str, set[str]] = {}
    for tname, cname, ctype, cols_ in _rows(
            s, "SELECT cl.relname, con.conname, con.contype, "
               "array_to_string(ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(n, o) "
               "JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n ORDER BY k.o), ',') "
               "FROM pg_constraint con JOIN pg_class cl ON cl.oid = con.conrelid JOIN pg_namespace n ON n.oid = cl.relnamespace "
               "WHERE n.nspname = %s AND con.contype IN ('p','u')", (schema,)):
        if ctype == "p":
            pks[tname] = cols_.split(",")
        else:
            uniq_constraints.setdefault(tname, set()).add(cname)
    idx: dict[str, list[Index]] = {}
    for tname, iname, uniq, primary, cols_, idef, method, exprs in _rows(
            s, "SELECT t.relname, i.relname, ix.indisunique, ix.indisprimary, "
               "array_to_string(ARRAY(SELECT a.attname FROM unnest(ix.indkey) WITH ORDINALITY k(n, o) "
               "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.n ORDER BY k.o), ','), "
               "pg_get_indexdef(ix.indexrelid), am.amname, ix.indexprs IS NOT NULL OR ix.indpred IS NOT NULL "
               "FROM pg_index ix JOIN pg_class t ON t.oid = ix.indrelid JOIN pg_class i ON i.oid = ix.indexrelid "
               "JOIN pg_namespace n ON n.oid = t.relnamespace JOIN pg_am am ON am.oid = i.relam WHERE n.nspname = %s",
            (schema,)):
        if primary:
            continue
        idx.setdefault(tname, []).append(Index(iname, cols_.split(",") if cols_ else [], bool(uniq),
                                               "btree" if method == "btree" and not exprs else "other", native=idef))
    fks: dict[str, dict] = {}
    for tname, cname, cols_, rschema, rtable, rcols, upd, dele in _rows(
            s, "SELECT cl.relname, con.conname, "
               "array_to_string(ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(n, o) "
               "JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n ORDER BY k.o), ','), "
               "rn.nspname, rc.relname, "
               "array_to_string(ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(n, o) "
               "JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n ORDER BY k.o), ','), "
               "con.confupdtype, con.confdeltype FROM pg_constraint con JOIN pg_class cl ON cl.oid = con.conrelid "
               "JOIN pg_namespace n ON n.oid = cl.relnamespace JOIN pg_class rc ON rc.oid = con.confrelid "
               "JOIN pg_namespace rn ON rn.oid = rc.relnamespace WHERE n.nspname = %s AND con.contype = 'f'", (schema,)):
        rule = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}
        fks.setdefault(tname, {})[cname] = ForeignKey(cname, cols_.split(","), rtable, rcols.split(","),
                                                      rule.get(dele, "NO ACTION"), rule.get(upd, "NO ACTION"),
                                                      rschema if rschema != schema else "")
    checks: dict[str, list[str]] = {}
    for tname, cdef in _rows(s, "SELECT cl.relname, pg_get_constraintdef(con.oid) FROM pg_constraint con "
                                "JOIN pg_class cl ON cl.oid = con.conrelid JOIN pg_namespace n ON n.oid = cl.relnamespace "
                                "WHERE n.nspname = %s AND con.contype = 'c'", (schema,)):
        m = re.match(r"CHECK \((.*)\)$", cdef, re.S)
        checks.setdefault(tname, []).append(m.group(1) if m else cdef)

    for tname, relkind, est, tcomment in trows:
        if want is not None and tname not in want:
            continue
        if relkind in ("v", "m"):
            body = _rows(s, "SELECT pg_get_viewdef(%s::regclass, true)", (f"{qident('postgres', schema)}.{qident('postgres', tname)}",))[0][0]
            sch.views.append(View(tname, body.strip().rstrip(";")))
            continue
        columns = []
        for (_t, name, data_type, udt, nullable, default, clen, nprec, nscale, dprec, is_identity, is_generated,
             gen_expr, fmt, comment) in cols.get(tname, []):
            neutral = _PG_MAP.get(data_type, "other")
            if data_type == "USER-DEFINED" and udt in enums:
                neutral = "enum"
            elif data_type == "USER-DEFINED" and udt == "citext":
                neutral = "text"
            c = Column(name=name, type=neutral, native=fmt, nullable=nullable == "YES", comment=comment or "")
            if neutral == "enum":
                c.values = enums[udt]
                sch.enums[udt] = enums[udt]
            if neutral in ("char", "varchar", "bit"):
                c.length = _int(clen)
            elif neutral == "decimal" and data_type != "money":
                c.precision, c.scale = _int(nprec), _int(nscale)
            elif neutral in ("time", "datetime", "timestamptz"):
                c.precision = _int(dprec)
            if is_identity == "YES" or (default or "").startswith("nextval("):
                c.auto_increment = True
            elif is_generated == "ALWAYS":
                c.generated = gen_expr
            elif default is not None:
                c.default = default
                c.default_is_expr = not re.fullmatch(r"('(?:[^']|'')*'(::[\w\s\".\[\]]+)?|-?\d+(\.\d+)?|true|false)", default)
            columns.append(c)
        sch.tables.append(Table(tname, columns, pks.get(tname, []), idx.get(tname, []),
                                list(fks.get(tname, {}).values()), checks.get(tname, []), tcomment or "", _int(est)))
    for kind, name in _rows(s, "SELECT CASE p.prokind WHEN 'p' THEN 'procedure' ELSE 'function' END, p.proname "
                               "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = %s "
                               "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')",
                            (schema,)):
        sch.routines.append({"kind": kind, "name": name, "definition": ""})
    for name, table in _rows(s, "SELECT t.tgname, c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND NOT t.tgisinternal",
                             (schema,)):
        sch.routines.append({"kind": "trigger", "name": name, "table": table, "definition": ""})
    _view_deps(sch)
    return sch


# ------------------------------------------------------------------ SQLite
def _sqlite_neutral(decl: str) -> str:
    d = (decl or "").lower()
    if "int" in d:
        return "bigint"
    if any(x in d for x in ("char", "clob", "text")):
        return "text"
    if "blob" in d or not d:
        return "blob" if "blob" in d else "other"
    if any(x in d for x in ("real", "floa", "doub")):
        return "double"
    if "bool" in d:
        return "bool"
    if "datetime" in d or "timestamp" in d:
        return "datetime"
    if d.startswith("date"):
        return "date"
    if "json" in d:
        return "json"
    if any(x in d for x in ("dec", "num")):
        return "decimal"
    return "other"


def introspect_sqlite(s: Session, schema: str = "main", tables: list[str] | None = None) -> Schema:
    version = str(_rows(s, "SELECT sqlite_version()")[0][0])
    sch = Schema("sqlite", schema, "SQLite " + version)
    want = set(tables) if tables else None
    q = qident("sqlite", schema)
    for typ, name, sql in _rows(s, f"SELECT type, name, sql FROM {q}.sqlite_master WHERE type IN ('table','view') "
                                   "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name"):
        if want is not None and name not in want:
            continue
        if typ == "view":
            body = re.sub(r"^\s*CREATE\s+(TEMP|TEMPORARY\s+)?VIEW\s+(IF\s+NOT\s+EXISTS\s+)?(\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|\S+)\s+AS\s+",
                          "", sql or "", flags=re.I | re.S)
            sch.views.append(View(name, body, native_ddl=sql or ""))
            continue
        t = qident("sqlite", name)
        info = _rows(s, f"PRAGMA {q}.table_xinfo({t})")
        autoinc = bool(re.search(r"AUTOINCREMENT", sql or "", re.I))
        pk_cols = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
        columns = []
        for r in info:
            _cid, cname, decl, notnull, default, pk = r[:6]
            hidden = r[6] if len(r) > 6 else 0
            c = Column(cname, _sqlite_neutral(decl), decl or "", nullable=not notnull and not pk)
            if hidden in (2, 3):
                c.generated = "(generated)"
            if default is not None:
                c.default = str(default)
                c.default_is_expr = not re.fullmatch(r"('(?:[^']|'')*'|-?\d+(\.\d+)?|NULL)", str(default), re.I)
            if len(pk_cols) == 1 and pk and (decl or "").upper() == "INTEGER":
                c.auto_increment = autoinc or True    # INTEGER PRIMARY KEY is the rowid
            m = re.search(r"\((\d+)(?:\s*,\s*(\d+))?\)", decl or "")
            if m and c.type in ("decimal",):
                c.precision, c.scale = int(m.group(1)), int(m.group(2) or 0)
            elif m and "char" in (decl or "").lower():
                c.type, c.length = "varchar", int(m.group(1))
            columns.append(c)
        indexes = []
        idx_sql = dict(_rows(s, f"SELECT name, sql FROM {q}.sqlite_master WHERE type = 'index' AND tbl_name = ?", (name,)))
        for row in _rows(s, f"PRAGMA {q}.index_list({t})"):
            iname, uniq, origin = row[1], row[2], row[3] if len(row) > 3 else "c"
            if origin == "pk":
                continue
            icols = [x[2] for x in _rows(s, f"PRAGMA {q}.index_info({qident('sqlite', iname)})")]
            if any(c is None for c in icols):
                continue   # expression index
            indexes.append(Index(iname if origin == "c" else f"{name}_{'_'.join(icols)}_key", icols, bool(uniq),
                                 native=idx_sql.get(iname) or ""))
        fkd: dict[int, ForeignKey] = {}
        for r in _rows(s, f"PRAGMA {q}.foreign_key_list({t})"):
            fid, _seq, rtable, frm, to, on_upd, on_del = r[:7]
            f = fkd.setdefault(fid, ForeignKey(f"fk_{name}_{fid}", [], rtable, [], on_del or "NO ACTION",
                                               on_upd or "NO ACTION"))
            f.columns.append(frm)
            f.ref_columns.append(to)
        tbl = Table(name, columns, pk_cols, indexes, list(fkd.values()), native_ddl=sql or "")
        try:
            tbl.rows_estimate = int(_rows(s, f"SELECT COUNT(*) FROM {q}.{t}")[0][0])
        except Exception:  # noqa: BLE001
            pass
        sch.tables.append(tbl)
    for name, tbl in _rows(s, f"SELECT name, tbl_name FROM {q}.sqlite_master WHERE type = 'trigger'"):
        sch.routines.append({"kind": "trigger", "name": name, "table": tbl, "definition": ""})
    _view_deps(sch)
    return sch


def introspect(s: Session, schema: str, tables: list[str] | None = None) -> Schema:
    return {"mysql": introspect_mysql, "postgres": introspect_postgres, "sqlite": introspect_sqlite}[s.kind](s, schema, tables)


def _view_deps(sch: Schema) -> None:
    """Order views so that a view comes after the views it selects from."""
    names = {v.name for v in sch.views}
    for v in sch.views:
        v.depends_on = [n for n in names if n != v.name and re.search(
            r"(?<![\w$])[`\"\[]?" + re.escape(n) + r"[`\"\]]?(?![\w$])", v.sql)]
    ordered, seen = [], set()

    def visit(v: View, stack=()):
        if v.name in seen or v.name in stack:
            return
        for d in v.depends_on:
            dep = next((x for x in sch.views if x.name == d), None)
            if dep:
                visit(dep, (*stack, v.name))
        seen.add(v.name)
        ordered.append(v)
    for v in sch.views:
        visit(v)
    sch.views = ordered
