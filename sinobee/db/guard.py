"""Decide whether a SQL statement only reads.

Layer 1 of the read-only protection (layer 2 is the database itself: read-only transactions,
`mode=ro` for SQLite). A statement is "read" only if it is a single SELECT / set operation / WITH
over reads, EXPLAIN (without ANALYZE) of a read, SHOW, DESCRIBE or a non-assigning PRAGMA, and calls
none of the known side-effect functions. Everything else counts as a write.
"""
from __future__ import annotations

import logging
import re

logging.getLogger("sqlglot").setLevel(logging.ERROR)

DIALECT = {"postgres": "postgres", "mysql": "mysql", "sqlite": "sqlite"}

# functions that act outside the read-only transaction (server control, file access, remote links)
SIDE_EFFECT_FUNCS = {
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "pg_rotate_logfile", "pg_read_file",
    "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "lo_import", "lo_export", "lo_unlink", "dblink",
    "dblink_exec", "pg_sleep", "set_config", "pg_advisory_lock", "pg_advisory_xact_lock",
    "load_file", "sleep", "benchmark", "get_lock", "release_lock",
    "load_extension", "writefile", "readfile", "edit",
}
READ_PRAGMAS = {"table_info", "table_xinfo", "index_list", "index_info", "index_xinfo", "foreign_key_list",
                "database_list", "table_list", "collation_list", "function_list", "compile_options", "user_version",
                "schema_version", "page_count", "page_size", "encoding", "integrity_check", "quick_check"}
STATEMENT_WORDS = {"insert", "update", "delete", "merge", "upsert", "replace", "create", "drop", "alter", "truncate",
                   "rename", "grant", "revoke", "comment", "attach", "detach", "vacuum", "reindex", "analyze",
                   "cluster", "copy", "call", "do", "execute", "prepare", "lock", "unlock", "set", "reset", "load",
                   "import", "handler", "install", "uninstall", "kill", "shutdown", "flush", "optimize", "repair",
                   "refresh", "begin", "commit", "rollback", "savepoint", "release", "start", "use", "listen",
                   "notify", "discard", "checkpoint", "security", "declare", "fetch", "move", "close"}
WRITE_WORDS = re.compile(
    r"\b(insert|update|delete|merge|upsert|replace|create|drop|alter|truncate|rename|grant|revoke|comment|"
    r"attach|detach|vacuum|reindex|analyze|cluster|copy|call|do|execute|prepare|lock|unlock|set|reset|"
    r"load|import|handler|install|uninstall|kill|shutdown|flush|optimize|repair|refresh)\b", re.I)


class Verdict:
    def __init__(self, kind: str, reason: str = "", statement: str = ""):
        self.kind = kind            # read | write | invalid
        self.reason = reason
        self.statement = statement  # SELECT, UPDATE, ...

    @property
    def is_read(self) -> bool:
        return self.kind == "read"

    def as_dict(self) -> dict:
        return {"kind": self.kind, "reason": self.reason, "statement": self.statement}


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"(--|#)[^\n]*", " ", sql)


def _fallback(sql: str) -> Verdict:
    body = _strip_comments(sql).strip().rstrip(";").strip()
    if ";" in body:
        return Verdict("invalid", "一次只能執行一個語句")
    first = (body.split(None, 1) or [""])[0].lower()
    masked = re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`", "''", body)
    if first in ("select", "with", "show", "describe", "desc", "values", "table") and not WRITE_WORDS.search(masked):
        return Verdict("read", statement=first.upper())
    if first == "explain" and not re.search(r"\banalyze\b", masked, re.I) and not WRITE_WORDS.search(masked[7:]):
        return Verdict("read", statement="EXPLAIN")
    if first == "pragma":
        m = re.match(r"pragma\s+(?:\w+\.)?(\w+)\s*(\(\s*['\"]?[\w.]+['\"]?\s*\))?\s*$", body, re.I)
        if m and "=" not in masked and m.group(1).lower() in READ_PRAGMAS:
            return Verdict("read", statement="PRAGMA")
        return Verdict("write", "這個 PRAGMA 可能修改資料庫設定", "PRAGMA")
    if first not in STATEMENT_WORDS:
        return Verdict("invalid", "無法辨識的語句")
    return Verdict("write", f"{first.upper() or '這個語句'} 會修改資料或設定", first.upper())


def classify(sql: str, kind: str = "postgres") -> Verdict:
    if not sql or not sql.strip():
        return Verdict("invalid", "沒有 SQL 語句")
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:  # pragma: no cover - sqlglot is a dependency, the regex is a safety net
        return _fallback(sql)

    body = sql.strip().rstrip(";")
    first = (_strip_comments(body).split(None, 1) or [""])[0].lower()
    if first in ("explain",):
        rest = re.sub(r"^\s*explain\s+", "", _strip_comments(body), flags=re.I)
        if re.match(r"\(?\s*analy[sz]e\b", rest, re.I):
            return Verdict("write", "EXPLAIN ANALYZE 會真的執行語句", "EXPLAIN")
        inner = classify(re.sub(r"^\((?:[^()]|\([^()]*\))*\)\s*", "", rest), kind)
        return Verdict("read", statement="EXPLAIN") if inner.is_read else inner
    if first in ("show", "describe", "desc"):
        return _fallback(body)
    if first == "pragma":
        return _fallback(body)

    try:
        stmts = [s for s in sqlglot.parse(body, read=DIALECT.get(kind, "postgres")) if s is not None]
    except Exception as e:  # noqa: BLE001 - sqlglot raises several error types
        fb = _fallback(body)
        if fb.is_read:
            return Verdict("invalid", f"無法解析這個語句：{str(e).splitlines()[0][:200]}")
        return fb
    if len(stmts) != 1:
        return Verdict("invalid", "一次只能執行一個語句")
    st = stmts[0]
    name = st.key.upper()
    writes = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.TruncateTable,
              exp.Command, exp.Set, exp.Use, exp.Grant, exp.Into, exp.Copy, exp.LoadData, exp.Analyze)
    writes = tuple(t for t in writes if isinstance(t, type))
    if isinstance(st, writes):
        return Verdict("write", f"{name} 會修改資料或設定", name)
    if not isinstance(st, (exp.Select, exp.SetOperation, exp.Values, exp.Table, exp.Describe, exp.Show)
                      if hasattr(exp, "Show") else (exp.Select, exp.SetOperation, exp.Values, exp.Table, exp.Describe)):
        if first in STATEMENT_WORDS:
            return Verdict("write", f"{name} 不是唯讀查詢", name)
        return Verdict("invalid", "無法辨識的語句")
    for node in st.walk():
        if isinstance(node, writes):
            return Verdict("write", f"查詢中含有 {node.key.upper()}（會修改資料）", name)
        if isinstance(node, exp.Lock):
            return Verdict("write", "SELECT … FOR UPDATE 會鎖定資料列", name)
        if isinstance(node, (exp.Func, exp.Anonymous)):
            fname = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if fname in SIDE_EFFECT_FUNCS:
                return Verdict("write", f"{fname}() 會影響伺服器或檔案", name)
    return Verdict("read", statement="SELECT" if isinstance(st, exp.Select) else name)
