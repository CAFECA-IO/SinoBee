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


# ------------------------------------------------------------------ memory upkeep
@action("memory.maintain")
async def memory_maintain(ctx: ActionContext, **_):
    """Scheduled memory upkeep (consolidate, expire short-term, merge when large). No model unless merging."""
    from ..memory.maintain import maintain
    ctx.activity("整理記憶中")
    rep = await maintain(ctx.rt, reason="schedule", force=True)
    c = rep.get("consolidation", {})
    comp = rep.get("compression") or {}
    lines = [f"短期記憶轉長期 {len(c.get('kept_short', []))} 則、到期封存 {len(c.get('expired_short', []))} 則",
             f"久未使用封存 {len(c.get('archived', []))} 則、升為核心 {len(c.get('promoted', []))} 則、"
             f"合併重複 {len(c.get('merged', []))} 則"]
    if comp:
        lines.append(f"合併相關記憶 {len(comp.get('merged', []))} 組，省下 {comp.get('saved_chars', 0)} 字")
        if comp.get("forget_candidates"):
            lines.append(f"建議檢查是否刪除：{'、'.join(comp['forget_candidates'][:10])}")
    if rep.get("core_over_limit"):
        lines.append("注意：永久的核心記憶已超過核心容量上限")
    after = rep.get("after", {})
    lines.append("目前：" + "、".join(f"{n} {after.get(k, {}).get('count', 0)} 則" for k, n in
                                    (("permanent", "永久"), ("core", "核心"), ("short", "短期"), ("long", "長期"))))
    return {"summary": "\n".join(lines), **rep}


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


# ------------------------------------------------------------------ skills with an autopilot entry script
def _skill(ctx: ActionContext, name: str):
    s = ctx.rt.skills.skills.get(name)
    if s is None or not s.entry:
        from .graph import WorkflowError
        raise WorkflowError(f"技能「{name}」不存在或沒有入口腳本")
    return s


async def _run_entry(ctx: ActionContext, skill, args: list[str], timeout: float = 3600) -> dict:
    """Runs the skill's entry script in the workspace; returns its JSON answer."""
    import sys as _sys
    ws = Path(ctx.rt.config.workspace)
    ws.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(_sys.executable, str(skill.path / skill.entry), *args, cwd=str(ws),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        raise
    text = out.decode("utf-8", "replace")
    try:
        return json.loads(text[text.find("{"):]) if "{" in text else {"status": "error", "error": err.decode()[-800:]}
    except json.JSONDecodeError:
        return {"status": "error", "error": (text + err.decode("utf-8", "replace"))[-800:]}


def _record_files(ctx: ActionContext, folder: str, out: dict, keys=("film", "preview", "subtitles")) -> list[str]:
    ws = Path(ctx.rt.config.workspace)
    paths = []
    for k in keys:
        v = out.get(k)
        if isinstance(v, str) and v:
            p = Path(v) if Path(v).is_absolute() else ws / v
            if p.exists():
                paths.append(str(p))
                ctx.hub.store.board_write(ctx.board_scope, "artifact", {"path": str(p), "title": k},
                                          key=k, author="skill.autopilot")
    for v in (out.get("review") or [])[:12]:
        p = Path(v) if Path(v).is_absolute() else ws / v
        if p.exists():
            ctx.hub.store.board_write(ctx.board_scope, "artifact", {"path": str(p), "title": p.name},
                                      key=f"review:{p.name}", author="skill.autopilot")
    return paths


@action("skill.plan")
async def skill_plan(ctx: ActionContext, skill: str, intent: str, folder: str = "", example: str = "",
                     guide: str = "", rounds: int = 3, **extra):
    """A planner sub-agent writes <folder>/plan.json for the skill; the skill's entry checks it and the planner fixes
    whatever is missing (up to `rounds` times). Only this node needs a model's judgement."""
    from .agents import extract_json, run_subagent
    from .graph import WorkflowError
    s = _skill(ctx, skill)
    ws = Path(ctx.rt.config.workspace)
    if not folder:
        folder = f"videos/{dt.datetime.now().strftime('%Y%m%d-%H%M')}-{_slug(intent)[:24] or 'film'}"
    root = ws / folder
    root.mkdir(parents=True, exist_ok=True)
    example_text = (s.path / example).read_text(encoding="utf-8") if example and (s.path / example).is_file() else ""
    hints = "\n".join(f"- {k}: {v}" for k, v in extra.items() if v not in (None, "", False))
    plan, missing = None, []
    for i in range(int(rounds)):
        ctx.activity(f"撰寫企劃（第 {i + 1} 輪）")
        if plan is None:
            task = (f"Write the complete plan for this request: {intent}\n"
                    + (f"User preferences:\n{hints}\n" if hints else "")
                    + (f"\nRules:\n{guide}\n" if guide else "")
                    + "\nReturn ONLY one JSON object with exactly the same structure as this example (same keys, "
                      "same kinds of values; replace every value with content for THIS request; keep the four "
                      "profiling fields for every subject; actions in English; dialogue/narration in the user's "
                      f"language):\n```json\n{example_text}\n```")
        else:
            task = ("Your plan was checked and these fields are missing or wrong:\n- " + "\n- ".join(missing)
                    + "\nReturn the whole corrected JSON object (only the JSON):\n```json\n"
                    + json.dumps(plan, ensure_ascii=False, indent=1) + "\n```")
        res = await run_subagent(ctx.rt, role="planner", task=task, board_scope=ctx.board_scope,
                                 parent_session=ctx.run.get("session_id"), hub=ctx.hub, owner=ctx.run["id"],
                                 title=f"[{ctx.run.get('name')}] 企劃", emit=ctx.hub.run_emitter(ctx.run["id"]),
                                 approve=ctx.hub.approver(ctx.run))
        ctx.node_state.setdefault("sessions", []).append(res["session_id"])
        data = extract_json(res.get("result") or "")
        if not isinstance(data, dict):
            missing = ["the answer was not a JSON object - return only the JSON object"]
            continue
        plan = data
        plan["intent"] = intent            # the user's own words, not the example's
        (root / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        out = await _run_entry(ctx, s, [folder, "--budget", "3"], timeout=300)
        if out.get("status") == "needs_content":
            missing = [str(m) for m in out.get("missing") or []] or ["plan.json is incomplete"]
            continue
        if out.get("status") in ("error",):
            missing = [str(out.get("error"))[:500]]
            continue
        shots = plan.get("shots") or []
        secs = sum(float(x.get("duration") or 0) for x in shots if isinstance(x, dict))
        summary = f"企劃完成：{plan.get('title') or intent}（{len(shots)} 個鏡頭，約 {secs:.0f} 秒）"
        ctx.hub.store.board_write(ctx.board_scope, "decision", summary, key="plan", author="skill.plan")
        ctx.hub.store.board_write(ctx.board_scope, "artifact", {"path": str(root / "plan.json"), "title": "企劃"},
                                  key="plan.json", author="skill.plan")
        return {"folder": folder, "title": plan.get("title"), "shots": len(shots), "seconds": round(secs, 1),
                "summary": summary}
    raise WorkflowError("企劃沒有通過檢查：" + "；".join(missing)[:600])


@action("skill.autopilot")
async def skill_autopilot(ctx: ActionContext, skill: str, folder: str, until: str = "", draft_only: bool = False,
                          accept: list | None = None, **_):
    """Calls the skill's entry script until it reports one of `accept` (default: done); records produced files."""
    from .graph import WorkflowError
    s = _skill(ctx, skill)
    accept = accept or (["ready_for_final", "done"] if until == "final" else ["done"])
    args = [folder, "--budget", "600"] + (["--until", until] if until else []) + (["--draft-only"] if draft_only else [])
    blocked_since = None
    while True:
        if ctx.hub.cancelled(ctx.run["id"]):
            raise asyncio.CancelledError()
        out = await _run_entry(ctx, s, args, timeout=1800)
        st = out.get("status")
        steps = out.get("steps_done") or []
        if steps:
            ctx.activity(f"{steps[-1]}")
        if st in accept:
            _record_files(ctx, folder, out)
            film = out.get("film") or out.get("preview")
            return {**{k: v for k, v in out.items() if k not in ("call_again",)},
                    "summary": f"{'草稿預覽' if st == 'ready_for_final' else '影片'}：{film}" if film else st}
        if st == "working":
            ctx.activity("GPU 渲染中：" + ", ".join(out.get("rendering") or []) if out.get("rendering") else
                         (out.get("what") or "處理中")[:120])
            await asyncio.sleep(10)
            continue
        if st == "blocked":       # e.g. ComfyUI not reachable: wait a while (the user may be starting it)
            blocked_since = blocked_since or time.time()
            ctx.activity("等待渲染伺服器：" + str(out.get("what", ""))[:120])
            if time.time() - blocked_since > 1800:
                raise WorkflowError(str(out.get("what") or "renderer unreachable"))
            await asyncio.sleep(60)
            continue
        raise WorkflowError(f"{st}: {out.get('error') or out.get('missing') or out.get('what')}"[:800])
