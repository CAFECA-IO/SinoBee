"""Persistent state for background work: jobs (what to do and when), runs (each execution), the blackboard
(shared notes of agents working on the same thing) and approval requests from unattended runs.

Everything lives in $SINOBEE_HOME/work.db (SQLite, WAL) so the server can be restarted at any time.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, prompt TEXT, workflow TEXT, inputs TEXT DEFAULT '{}',
  schedule TEXT DEFAULT '{}', enabled INTEGER DEFAULT 1, created_by TEXT DEFAULT 'user', created_at REAL,
  updated_at REAL, next_run_at REAL, last_run_at REAL, last_status TEXT, notes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY, job_id TEXT, name TEXT, kind TEXT, trigger TEXT, status TEXT, created_at REAL, started_at REAL,
  finished_at REAL, session_id TEXT, summary TEXT, error TEXT, cost_usd REAL DEFAULT 0, state TEXT DEFAULT '{}',
  activity TEXT DEFAULT '', parent_session TEXT);
CREATE INDEX IF NOT EXISTS runs_job ON runs(job_id, created_at);
CREATE INDEX IF NOT EXISTS runs_status ON runs(status);
CREATE TABLE IF NOT EXISTS board(
  id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, kind TEXT NOT NULL, key TEXT, author TEXT,
  content TEXT NOT NULL, status TEXT DEFAULT '', created_at REAL, updated_at REAL);
CREATE INDEX IF NOT EXISTS board_scope ON board(scope, id);
CREATE TABLE IF NOT EXISTS approvals(
  id TEXT PRIMARY KEY, run_id TEXT, session_id TEXT, tool TEXT, arguments TEXT, reason TEXT, status TEXT,
  created_at REAL, decided_at REAL);
"""

ACTIVE = ("queued", "running", "waiting")


def now() -> float:
    return time.time()


class WorkStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def _exec(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.db.execute(sql, params)
            self.db.commit()
            return cur

    def _all(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------ jobs
    @staticmethod
    def _job(d: dict | None) -> dict | None:
        if not d:
            return None
        d["inputs"] = json.loads(d.get("inputs") or "{}")
        d["schedule"] = json.loads(d.get("schedule") or "{}")
        d["enabled"] = bool(d["enabled"])
        return d

    def create_job(self, *, name: str, kind: str, prompt: str = "", workflow: str = "", inputs: dict | None = None,
                   schedule: dict | None = None, enabled: bool = True, created_by: str = "user",
                   next_run_at: float | None = None, notes: str = "") -> dict:
        jid = "job_" + uuid.uuid4().hex[:10]
        t = now()
        self._exec("INSERT INTO jobs(id,name,kind,prompt,workflow,inputs,schedule,enabled,created_by,created_at,"
                   "updated_at,next_run_at,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (jid, name, kind, prompt, workflow, json.dumps(inputs or {}, ensure_ascii=False),
                    json.dumps(schedule or {}, ensure_ascii=False), int(enabled), created_by, t, t, next_run_at, notes))
        return self.job(jid)

    def update_job(self, jid: str, **fields) -> dict | None:
        if not fields:
            return self.job(jid)
        for k in ("inputs", "schedule"):
            if k in fields:
                fields[k] = json.dumps(fields[k] or {}, ensure_ascii=False)
        if "enabled" in fields:
            fields["enabled"] = int(bool(fields["enabled"]))
        fields["updated_at"] = now()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), jid))
        return self.job(jid)

    def job(self, jid: str) -> dict | None:
        rows = self._all("SELECT * FROM jobs WHERE id=?", (jid,))
        return self._job(rows[0]) if rows else None

    def jobs(self) -> list[dict]:
        return [self._job(d) for d in self._all("SELECT * FROM jobs ORDER BY created_at DESC")]

    def due_jobs(self, at: float) -> list[dict]:
        return [self._job(d) for d in self._all(
            "SELECT * FROM jobs WHERE enabled=1 AND next_run_at IS NOT NULL AND next_run_at<=? ORDER BY next_run_at",
            (at,))]

    def delete_job(self, jid: str) -> None:
        self._exec("DELETE FROM jobs WHERE id=?", (jid,))
        self._exec("DELETE FROM board WHERE scope=?", (f"job:{jid}",))

    # ------------------------------------------------------------------ runs
    @staticmethod
    def _run(d: dict | None) -> dict | None:
        if not d:
            return None
        d["state"] = json.loads(d.get("state") or "{}")
        return d

    def create_run(self, *, job_id: str | None, name: str, kind: str, trigger: str, state: dict | None = None,
                   parent_session: str | None = None) -> dict:
        rid = "run_" + uuid.uuid4().hex[:10]
        self._exec("INSERT INTO runs(id,job_id,name,kind,trigger,status,created_at,state,parent_session) "
                   "VALUES(?,?,?,?,?,?,?,?,?)",
                   (rid, job_id, name, kind, trigger, "queued", now(), json.dumps(state or {}, ensure_ascii=False),
                    parent_session))
        return self.run(rid)

    def update_run(self, rid: str, **fields) -> None:
        if "state" in fields:
            fields["state"] = json.dumps(fields["state"], ensure_ascii=False, default=str)
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), rid))

    def run(self, rid: str) -> dict | None:
        rows = self._all("SELECT * FROM runs WHERE id=?", (rid,))
        return self._run(rows[0]) if rows else None

    def runs(self, *, job_id: str | None = None, status: tuple[str, ...] | None = None, limit: int = 50,
             before: float | None = None) -> list[dict]:
        sql, params = "SELECT * FROM runs WHERE 1=1", []
        if job_id:
            sql += " AND job_id=?"
            params.append(job_id)
        if status:
            sql += f" AND status IN ({','.join('?' * len(status))})"
            params += list(status)
        if before:
            sql += " AND created_at<?"
            params.append(before)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [self._run(d) for d in self._all(sql, params)]

    def session_runs(self, session_id: str, kind: str = "chat", limit: int = 200) -> list[dict]:
        """A conversation's turns as runs, oldest first."""
        rows = self._all("SELECT * FROM runs WHERE session_id=? AND kind=? ORDER BY created_at DESC LIMIT ?",
                         (session_id, kind, limit))
        return [self._run(d) for d in reversed(rows)]

    def active_run(self, job_id: str) -> dict | None:
        rows = self.runs(job_id=job_id, status=ACTIVE, limit=1)
        return rows[0] if rows else None

    def interrupt_active(self) -> list[dict]:
        """At startup: runs that were in flight when the server stopped."""
        rows = self.runs(status=ACTIVE, limit=1000)
        for r in rows:
            self.update_run(r["id"], status="interrupted", activity="", error="SinoBee 重新啟動時中斷")
        self._exec("UPDATE approvals SET status='expired', decided_at=? WHERE status='pending'", (now(),))
        return rows

    # ------------------------------------------------------------------ blackboard
    def board_write(self, scope: str, kind: str, content, *, key: str | None = None, author: str = "",
                    status: str = "") -> dict:
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
        t = now()
        with self._lock:
            if key:   # a keyed entry is replaced (latest value wins); history stays in other scopes' runs
                row = self.db.execute("SELECT id FROM board WHERE scope=? AND kind=? AND key=?",
                                      (scope, kind, key)).fetchone()
                if row:
                    self.db.execute("UPDATE board SET content=?, author=?, status=?, updated_at=? WHERE id=?",
                                    (text, author, status, t, row["id"]))
                    self.db.commit()
                    return self.board_get(row["id"])
            cur = self.db.execute("INSERT INTO board(scope,kind,key,author,content,status,created_at,updated_at) "
                                  "VALUES(?,?,?,?,?,?,?,?)", (scope, kind, key, author, text, status, t, t))
            self.db.commit()
            return self.board_get(cur.lastrowid)

    def board_get(self, bid: int) -> dict | None:
        rows = self._all("SELECT * FROM board WHERE id=?", (bid,))
        return rows[0] if rows else None

    def board_read(self, scope: str, *, kinds: list[str] | None = None, key: str | None = None,
                   limit: int = 50) -> list[dict]:
        sql, params = "SELECT * FROM board WHERE scope=?", [scope]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params += kinds
        if key:
            sql += " AND key=?"
            params.append(key)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return list(reversed(self._all(sql, params)))

    def board_set_status(self, bid: int, status: str) -> None:
        self._exec("UPDATE board SET status=?, updated_at=? WHERE id=?", (status, now(), bid))

    def board_scopes_open_plans(self) -> list[dict]:
        """Plan items not done yet, across all scopes (what SinoBee intends to do)."""
        return self._all("SELECT * FROM board WHERE kind='plan' AND status NOT IN ('done','dropped') "
                         "ORDER BY updated_at DESC LIMIT 100")

    # ------------------------------------------------------------------ approvals
    def add_approval(self, *, run_id: str | None, session_id: str, tool: str, arguments: dict, reason: str) -> dict:
        aid = "apv_" + uuid.uuid4().hex[:8]
        self._exec("INSERT INTO approvals(id,run_id,session_id,tool,arguments,reason,status,created_at) "
                   "VALUES(?,?,?,?,?,?,?,?)", (aid, run_id, session_id, tool,
                                                json.dumps(arguments, ensure_ascii=False, default=str)[:20000],
                                                reason, "pending", now()))
        return self.approval(aid)

    def approval(self, aid: str) -> dict | None:
        rows = self._all("SELECT * FROM approvals WHERE id=?", (aid,))
        if not rows:
            return None
        d = rows[0]
        d["arguments"] = json.loads(d["arguments"] or "{}")
        return d

    def approvals(self, status: str = "pending") -> list[dict]:
        out = self._all("SELECT * FROM approvals WHERE status=? ORDER BY created_at", (status,))
        for d in out:
            d["arguments"] = json.loads(d["arguments"] or "{}")
        return out

    def decide_approval(self, aid: str, ok: bool) -> None:
        self._exec("UPDATE approvals SET status=?, decided_at=? WHERE id=?", ("approved" if ok else "denied", now(), aid))
