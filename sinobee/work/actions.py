"""Built-in workflow actions (deterministic steps, no model involved)."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ActionContext:
    hub: object
    run: dict
    job: dict | None
    board_scope: str
    node_state: dict

    @property
    def rt(self):
        return self.hub.rt

    def activity(self, text: str) -> None:
        self.hub.store.update_run(self.run["id"], activity=text)
        self.hub.notify("run", {"run_id": self.run["id"]})


ACTIONS: dict = {}


def action(name: str):
    def deco(fn):
        ACTIONS[name] = fn
        return fn
    return deco


def _db(ctx: ActionContext):
    from ..tools.db_tools import service
    return service(ctx.rt.config)


# ------------------------------------------------------------------ database health
SIZE_SQL = {
    "mysql": ("SELECT table_name, table_rows, data_length + index_length, data_length, index_length, "
              "(SELECT COUNT(*) FROM information_schema.statistics s WHERE s.table_schema = t.table_schema "
              "AND s.table_name = t.table_name AND s.index_name = 'PRIMARY') FROM information_schema.tables t "
              "WHERE table_schema = %s AND table_type = 'BASE TABLE'"),
    "postgres": ("SELECT c.relname, c.reltuples::bigint, pg_total_relation_size(c.oid), pg_relation_size(c.oid), "
                 "pg_indexes_size(c.oid), (SELECT COUNT(*) FROM pg_constraint k WHERE k.conrelid = c.oid AND k.contype = 'p') "
                 "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind IN ('r','p')"),
}


@action("db.health_collect")
async def db_health_collect(ctx: ActionContext, connection: str, schema: str | None = None, **_):
    """Collects table sizes, row estimates, tables without a primary key and growth since the last run of the same job."""
    svc = _db(ctx)
    c = svc.get(connection)
    ctx.activity(f"讀取 {c.name} 的結構與大小")
    t0 = time.time()

    def collect(s):
        version = s.version()
        sch = schema or (c.database if c.kind == "mysql" else "public" if c.kind == "postgres" else "main")
        tables = []
        if c.kind in SIZE_SQL:
            cur = s.raw.cursor()
            cur.execute(SIZE_SQL[c.kind], (sch,))
            for name, rows, total, data, idx, pk in cur.fetchall():
                tables.append({"table": name, "rows": int(rows or 0), "bytes": int(total or 0), "data_bytes": int(data or 0),
                               "index_bytes": int(idx or 0), "has_pk": bool(pk)})
            cur.close()
        else:
            for t in s.tables(sch):
                if t["kind"] != "table":
                    continue
                cols = s.columns(sch, t["name"])["columns"]
                n = s.run(f'SELECT COUNT(*) FROM "{t["name"]}"', limit=1)["rows"][0][0]
                tables.append({"table": t["name"], "rows": int(n), "bytes": None, "has_pk": any(x["pk"] for x in cols)})
        return {"version": version, "schema": sch, "tables": tables}

    from ..db.drivers import DbError
    try:
        data = await svc._with_session(c, collect)
    except DbError as e:
        return {"ok": False, "connection": c.name, "error": str(e), "summary": f"無法連線：{e}"}
    data["latency_ms"] = int((time.time() - t0) * 1000)
    tables = data["tables"]
    prev = None
    if ctx.job:
        rows = ctx.hub.store.board_read(f"job:{ctx.job['id']}", kinds=["snapshot"], key="db_health", limit=1)
        if rows:
            try:
                prev = json.loads(rows[-1]["content"])
            except ValueError:
                prev = None
    growth = []
    if prev:
        before = {t["table"]: t for t in prev.get("tables", [])}
        for t in tables:
            b = before.get(t["table"])
            if b and b.get("rows"):
                pct = (t["rows"] - b["rows"]) / max(1, b["rows"]) * 100
                if abs(pct) >= 20 and abs(t["rows"] - b["rows"]) >= 1000:
                    growth.append({"table": t["table"], "before": b["rows"], "now": t["rows"], "pct": round(pct, 1)})
        added = sorted(set(x["table"] for x in tables) - set(before))
        dropped = sorted(set(before) - set(x["table"] for x in tables))
    else:
        added = dropped = []
    tables.sort(key=lambda t: -(t.get("bytes") or 0) or -t["rows"])
    out = {
        "ok": True, "connection": c.name, "kind": c.kind, "version": data["version"], "schema": data["schema"],
        "latency_ms": data["latency_ms"], "table_count": len(tables),
        "total_bytes": sum(t.get("bytes") or 0 for t in tables), "total_rows": sum(t["rows"] for t in tables),
        "largest": tables[:10], "no_primary_key": [t["table"] for t in tables if not t["has_pk"]][:50],
        "empty_tables": [t["table"] for t in tables if t["rows"] == 0][:50],
        "growth": growth, "added_tables": added, "dropped_tables": dropped,
        "previous_snapshot_at": prev.get("at") if prev else None,
    }
    out["summary"] = (f"{c.name}：{out['table_count']} 個表、約 {out['total_rows']:,} 列"
                      + (f"、{round(out['total_bytes'] / 1048576, 1)} MB" if out["total_bytes"] else "")
                      + (f"；{len(growth)} 個表變化超過 20%" if growth else ""))
    if ctx.job:
        ctx.hub.store.board_write(f"job:{ctx.job['id']}", "snapshot",
                                  {"at": time.time(), "tables": [{"table": t["table"], "rows": t["rows"]} for t in tables]},
                                  key="db_health", author="db.health_collect")
    return out


# ------------------------------------------------------------------ database migration
@action("db.migration_plan")
async def db_migration_plan(ctx: ActionContext, source: str, target: dict, options: dict | None = None, **_):
    mm = _db(ctx).migrations
    plan = await asyncio.to_thread(mm.plan, _db(ctx).get(source).id, target, options or {})
    warn = [n for n in plan["notes"] if n["level"] == "warn"]
    rows = sum(t.get("rows_estimate") or 0 for t in plan["tables"])
    plan["summary"] = (f"{plan['source']['name']} → {plan['target'].get('label')}：{len(plan['tables'])} 個表、約 {rows:,} 列、"
                       f"{len(warn)} 項需要留意" + (f"；目的地已有 {len(plan['conflicts'])} 個同名資料表" if plan["conflicts"] else ""))
    return plan


@action("db.migrate")
async def db_migrate(ctx: ActionContext, source: str, target: dict, options: dict | None = None, **_):
    svc = _db(ctx)
    mm = svc.migrations
    job = await asyncio.to_thread(mm.start, svc.get(source).id, target, options or {})
    jid = job["id"]
    ctx.node_state["migration_id"] = jid
    while True:
        await asyncio.sleep(2)
        if ctx.hub.cancelled(ctx.run["id"]):
            mm.stop(jid)
            raise asyncio.CancelledError()
        d = mm.get(jid).public()
        pr = d["progress"]
        ctx.activity(f"遷移中：{d['phase']}，{pr['rows_copied']:,} 列")
        if d["status"] not in ("running", "queued"):
            break
    if d["status"] != "done":
        raise RuntimeError(d.get("error") or d["status"])
    bad = [t for t in d["tables"] if t["source_rows"] != t["target_rows"] or t["sample_mismatches"]]
    return {"migration_id": jid, "target": d["target"], "tables": d["tables"], "notes": d.get("notes", []),
            "value_issues": d.get("value_issues", []), "skipped_rows": d.get("skipped_rows", {}),
            "summary": f"已遷移 {pr['rows_copied']:,} 列、{len(d['tables'])} 個表；"
                       + ("驗證全部一致" if not bad else f"{len(bad)} 個表驗證有差異")}


# ------------------------------------------------------------------ reports
def _slug(text: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", str(text or "report")).strip("-").lower()
    return s[:60] or "report"


@action("report.write")
async def report_write(ctx: ActionContext, title: str, body, folder: str | None = None, **_):
    """Saves a Markdown report in the workspace (reports/<job or workflow>/<date>.md)."""
    if not isinstance(body, str):
        body = "```json\n" + json.dumps(body, ensure_ascii=False, indent=2, default=str) + "\n```"
    base = Path(ctx.rt.config.workspace) / "reports" / _slug(folder or (ctx.job or {}).get("name") or ctx.run.get("name") or title)
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{dt.datetime.now().strftime('%Y%m%d-%H%M')}.md"
    header = f"# {title}\n\n_{dt.datetime.now().strftime('%Y-%m-%d %H:%M')} · SinoBee 工作 {ctx.run['id']}_\n\n"
    path.write_text(header + body.strip() + "\n", encoding="utf-8")
    ctx.hub.store.board_write(ctx.board_scope, "artifact", {"path": str(path), "title": title}, author="report.write")
    if ctx.job:
        ctx.hub.store.board_write(f"job:{ctx.job['id']}", "artifact", {"path": str(path), "title": title,
                                                                      "run": ctx.run["id"]}, author="report.write")
    return {"path": str(path), "title": title, "summary": f"報告已存到 {path}"}
