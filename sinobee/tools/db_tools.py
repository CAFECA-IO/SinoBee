"""`database` toolset: let the agent browse and query the user's saved database connections.

Passwords never reach the model: connections are referred to by id or name, and credentials are
read from secrets.json inside the driver. Reads are free; writes always need the user's approval,
even in auto mode, and only work on connections where the user enabled writing.
"""
from __future__ import annotations

from .registry import ToolContext, tool

_SERVICES: dict = {}


def service(config):
    from ..db import DbService
    key = str(config.home)
    if key not in _SERVICES:
        _SERVICES[key] = DbService(config)
    else:
        _SERVICES[key].config = config
    return _SERVICES[key]


def md_table(columns: list[str], rows: list[list], max_rows: int = 50) -> str:
    if not columns:
        return ""
    esc = lambda v: "NULL" if v is None else str(v).replace("|", "\\|").replace("\n", " ")[:200]
    out = ["| " + " | ".join(map(esc, columns)) + " |", "|" + "---|" * len(columns)]
    out += ["| " + " | ".join(esc(v) for v in r) + " |" for r in rows[:max_rows]]
    return "\n".join(out)


@tool("db_list", "database",
      "List the user's saved database connections (PostgreSQL, MySQL/MariaDB, SQLite): id, name, type, "
      "host/database and whether writing is allowed. Passwords are never shown. If the user wants a new "
      "connection, ask them to add it in the Data tab (or paste a connection string) instead of typing passwords here.")
def db_list(ctx: ToolContext):
    conns = service(ctx.config).listing()
    if not conns:
        return "No saved database connections. Ask the user to add one in the 資料 (Data) tab."
    return [{k: c[k] for k in ("id", "name", "kind", "label", "allow_write")} for c in conns]


@tool("db_schema", "database",
      "Browse a saved connection's structure. Without `table`: schemas and their tables/views (with row "
      "estimates). With `schema` + `table`: columns (type, nullable, primary key), indexes and foreign keys. "
      "Always look at the schema before writing SQL.",
      {"properties": {"connection": {"type": "string", "description": "connection id or name"},
                      "schema": {"type": "string"}, "table": {"type": "string"}},
       "required": ["connection"]})
async def db_schema(ctx: ToolContext, connection: str, schema: str | None = None, table: str | None = None):
    svc = service(ctx.config)
    if table:
        c = svc.get(connection)
        if not schema:
            tree = await svc.schema_tree(c.id)
            schema = next((s["name"] for s in tree["schemas"] if any(t["name"] == table for t in s["tables"])), None)
            if not schema:
                return f"table {table} not found"
        return {"schema": schema, "table": table, **(await svc.table_info(c.id, schema, table))}
    tree = await svc.schema_tree(connection)
    out = {"server": tree["version"][:120], "schemas": []}
    for s in tree["schemas"]:
        if schema and s["name"] != schema:
            continue
        tables = s["tables"][:300]
        out["schemas"].append({"name": s["name"], "tables": [
            f"{t['name']}{' (view)' if t['kind'] == 'view' else ''}"
            f"{' ~' + str(t['rows_estimate']) + ' rows' if t['rows_estimate'] else ''}" for t in tables],
            **({"more_tables": len(s["tables"]) - 300} if len(s["tables"]) > 300 else {})})
    return out


@tool("db_query", "database",
      "Run ONE read-only SQL statement (SELECT / WITH / EXPLAIN / SHOW / DESCRIBE) on a saved connection and get "
      "the rows as a markdown table. Writes are refused here (use db_execute). Default limit 100 rows; aggregate "
      "in SQL rather than fetching many rows. Show the user the SQL you ran.",
      {"properties": {"connection": {"type": "string", "description": "connection id or name"},
                      "sql": {"type": "string"},
                      "limit": {"type": "integer", "description": "max rows to return (default 100, max 1000)"}},
       "required": ["connection", "sql"]})
async def db_query(ctx: ToolContext, connection: str, sql: str, limit: int = 100):
    from ..db import classify
    svc = service(ctx.config)
    c = svc.get(connection)
    v = classify(sql, c.kind)
    if not v.is_read:
        return f"ERROR: not a read-only statement ({v.reason}). Use db_execute for writes."
    res = await svc.query(c.id, sql, limit=max(1, min(int(limit or 100), 1000)), source="agent")
    note = f"{res['row_count']} rows{' (truncated, more exist)' if res['truncated'] else ''}, {res['elapsed_ms']} ms"
    if ctx.emit:
        ctx.emit("db_result", {"connection": c.id, "sql": sql, "columns": res["columns"],
                               "rows": res["rows"][:200], "truncated": res["truncated"]})
    return f"{note}\n\n{md_table(res['columns'], res['rows'])}" + (
        f"\n\n(showing 50 of {res['row_count']} rows)" if res["row_count"] > 50 else "")


def _write_reason(args: dict) -> str:
    return f"寫入資料庫 {args.get('connection', '')}：{str(args.get('sql', ''))[:300]}"


@tool("db_execute", "database",
      "Run ONE statement that changes data or structure (INSERT, UPDATE, DELETE, CREATE, ALTER …) on a saved "
      "connection. Only works if the user enabled writing for that connection, and the user must approve every "
      "call. Prefer a WHERE clause and check the affected rows with db_query first.",
      {"properties": {"connection": {"type": "string"}, "sql": {"type": "string"}},
       "required": ["connection", "sql"]},
      writes=True, dangerous=_write_reason, always_confirm=True)
async def db_execute(ctx: ToolContext, connection: str, sql: str):
    res = await service(ctx.config).query(connection, sql, allow_write=True, source="agent")
    if res["columns"]:
        return f"{res['row_count']} rows\n\n{md_table(res['columns'], res['rows'])}"
    return f"done: {res['statement']}, {res['affected'] if res['affected'] is not None else 'unknown'} rows affected"
