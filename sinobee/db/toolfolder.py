"""Local tool folders: ~/.sinobee/tools/<connection id>/

    TOOL.json     the tool definition (sinobee.tool/v1) — never credentials
    queries.json  saved read-only queries [{title, sql}]
    notes.json    table / column notes {"schema.table": "...", "schema.table.column": "..."}
    SKILL.md      how an agent should use this database (generated, or written by the user / agent)

The folder is what gets registered on sinobee.ai; SKILL.md is also installed as a local skill
(`db-<id>`) so the agent knows the database before it starts querying.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .guard import classify
from .store import Connection

MAX_QUERIES = 50
ENGINE = {"postgres": "PostgreSQL", "mysql": "MySQL / MariaDB", "sqlite": "SQLite"}


def root(config) -> Path:
    p = Path(config.home) / "tools"
    p.mkdir(parents=True, exist_ok=True)
    return p


def folder(config, c: Connection) -> Path:
    p = root(config) / c.id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except (OSError, json.JSONDecodeError):
        return default


def _write(p: Path, data) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


# ---------- saved queries ----------
def queries(config, c: Connection) -> list[dict]:
    return _read(folder(config, c) / "queries.json", [])


def save_query(config, c: Connection, title: str, sql: str) -> list[dict]:
    title, sql = (title or "").strip()[:120], (sql or "").strip()[:4000]
    if not title or not sql:
        raise ValueError("查詢需要標題與 SQL")
    v = classify(sql, c.kind)
    if not v.is_read:
        raise ValueError(f"只能儲存唯讀查詢（{v.reason or v.statement}）")
    items = [q for q in queries(config, c) if q.get("title") != title]
    items.insert(0, {"title": title, "sql": sql})
    items = items[:MAX_QUERIES]
    _write(folder(config, c) / "queries.json", items)
    return items


def delete_query(config, c: Connection, title: str) -> list[dict]:
    items = [q for q in queries(config, c) if q.get("title") != title]
    _write(folder(config, c) / "queries.json", items)
    return items


# ---------- notes ----------
def notes(config, c: Connection) -> dict:
    return _read(folder(config, c) / "notes.json", {})


def set_notes(config, c: Connection, new: dict, replace: bool = False) -> dict:
    cur = {} if replace else notes(config, c)
    for k, v in (new or {}).items():
        k = str(k).strip()[:300]
        if not k:
            continue
        if v in (None, ""):
            cur.pop(k, None)
        else:
            cur[k] = str(v).strip()[:1000]
    _write(folder(config, c) / "notes.json", cur)
    return cur


# ---------- SKILL.md ----------
def skill_name(c: Connection) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", c.id.lower()).strip("-") or "database"
    return f"db-{s}"[:64].strip("-")


def default_skill(c: Connection, *, description: str = "", tables: list[dict] | None = None,
                  qs: list[dict] | None = None, nts: dict | None = None) -> str:
    name = skill_name(c)
    what = description.strip() or f"{c.name} ({ENGINE.get(c.kind, c.kind)})"
    desc = f"How to query the {c.name} database ({ENGINE.get(c.kind, c.kind)}): tables, notes and saved queries. " \
           f"Use when the user asks about data in {c.name}."
    lines = [f"---\nname: {name}\ndescription: {json.dumps(desc, ensure_ascii=False)}\n---\n",
             f"# {c.name}\n", f"{what}\n",
             f"Query it with the `database` tools using connection `{c.id}`: look at the structure with "
             f"`db_schema`, then run read-only SQL with `db_query`. "
             + ("Writes need `db_execute` and the user's approval every time." if c.allow_write
                else "This connection is read-only.") + "\n"]
    if tables:
        lines.append("## Tables\n")
        for t in tables[:60]:
            key = f"{t.get('schema')}.{t['name']}" if t.get("schema") else t["name"]
            cols = ", ".join(x["name"] for x in (t.get("columns") or [])[:30])
            note = (nts or {}).get(key)
            lines.append(f"- `{key}`" + (f" — {note}" if note else "") + (f"  \n  columns: {cols}" if cols else ""))
        lines.append("")
    col_notes = {k: v for k, v in (nts or {}).items() if k.count(".") >= 2}
    if col_notes:
        lines.append("## Column notes\n")
        lines += [f"- `{k}`: {v}" for k, v in sorted(col_notes.items())[:100]]
        lines.append("")
    if qs:
        lines.append("## Saved queries\n")
        for q in qs[:MAX_QUERIES]:
            lines.append(f"### {q['title']}\n\n```sql\n{q['sql']}\n```\n")
    return "\n".join(lines).rstrip() + "\n"


def skill_md(config, c: Connection) -> str | None:
    p = folder(config, c) / "SKILL.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def manifest(config, c: Connection) -> dict | None:
    return _read(folder(config, c) / "TOOL.json", None)


def write(config, c: Connection, *, manifest: dict | None = None, skill: str | None = None,
          custom_skill: bool = False, description: str = "", tables: list[dict] | None = None,
          skills=None) -> Path:
    """Write / refresh the folder. A SKILL.md the user or agent wrote is kept unless `skill` replaces it."""
    f = folder(config, c)
    meta = _read(f / ".meta.json", {})
    if manifest is not None:
        _write(f / "TOOL.json", manifest)
    if skill:
        text, meta["custom_skill"] = skill, bool(custom_skill)
    elif not meta.get("custom_skill") or not (f / "SKILL.md").exists():
        text = default_skill(c, description=description or (manifest or {}).get("description", ""),
                             tables=tables if tables is not None else (manifest or {}).get("tables"),
                             qs=queries(config, c), nts=notes(config, c))
    else:
        text = None
    if text:
        (f / "SKILL.md").write_text(text, encoding="utf-8")
    _write(f / ".meta.json", meta)
    if skills is not None and (f / "SKILL.md").exists():
        install_skill(skills, c, (f / "SKILL.md").read_text(encoding="utf-8"))
    return f


def install_skill(skills, c: Connection, text: str) -> None:
    """Make SKILL.md visible to the agent as a local skill (best effort)."""
    name = skill_name(c)
    body = re.sub(r"(?m)^name:\s*.*$", f"name: {name}", text, count=1)
    try:
        skills.create(name, body, actor="user", overwrite=True, source=f"tool:{c.id}")
    except Exception:  # noqa: BLE001 - an invalid user-written SKILL.md must not break publishing
        pass


def remove(config, c_id: str, skills=None) -> None:
    import shutil
    shutil.rmtree(root(config) / c_id, ignore_errors=True)
    if skills is not None:
        try:
            skills.delete(f"db-{c_id}")
        except Exception:  # noqa: BLE001
            pass
