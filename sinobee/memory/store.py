"""Layered memory + conversation storage on SQLite (FTS5 + optional embeddings).

Tiers
  core     L0 - small, high-importance; rendered into the system prompt at session start
  long     L2 - unbounded semantic memory; auto-recalled per turn by hybrid retrieval
  archive  decayed memories; searchable on demand only
Episodes (L3) are session transcripts + summaries, also indexed with FTS5.
L1 working memory lives in the agent session (see agent/loop.py).
"""
from __future__ import annotations

import array
import json
import math
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..tools.approval import scan_content

KINDS = {"fact", "preference", "lesson", "environment", "profile", "episode", "procedure"}
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]+")
_WORD = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_\-\.]*")


def tokenize(text: str) -> list[str]:
    """Language-aware tokens: latin words + CJK character bigrams (unigram for single chars)."""
    text = text or ""
    toks = [w.lower().strip(".-") for w in _WORD.findall(text)]
    for run in _CJK.findall(text):
        if len(run) == 1:
            toks.append(run)
        else:
            toks.extend(run[i:i + 2] for i in range(len(run) - 1))
    return [t for t in toks if t]


STOPWORDS = set("""a an the is are was were be been am i me my you your he she it its we our they them their this that
these those of to in on at by for with from as and or but if then so not no do does did can could should would will
shall may might must what which who whom whose when where why how all any some about into over than too very just
please help 的 了 是 我 你 他 她 它 們 在 和 與 或 也 就 都 而 及 嗎 呢 吧 啊 請 幫 一個 這個 那個 什麼 怎麼""".split())


def fts_query(text: str, limit_terms: int = 24) -> str:
    seen, terms = set(), []
    toks = tokenize(text)
    content = [t for t in toks if t not in STOPWORDS]
    for t in content or toks:
        if t not in seen and len(terms) < limit_terms:
            seen.add(t)
            terms.append('"' + t.replace('"', '') + '"')
    return " OR ".join(terms)


def _pack(vec: list[float] | None) -> bytes | None:
    return array.array("f", vec).tobytes() if vec else None


def _unpack(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    a = array.array("f")
    a.frombytes(blob)
    return a.tolist()


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class Memory:
    id: str
    tier: str
    kind: str
    content: str
    tags: list[str]
    importance: float
    confidence: float
    created_at: float
    updated_at: float
    last_access: float
    access_count: int
    superseded_by: str | None = None
    source: str | None = None
    score: float = 0.0

    def brief(self) -> dict:
        return {"id": self.id, "tier": self.tier, "kind": self.kind, "content": self.content,
                "tags": self.tags, "importance": round(self.importance, 2),
                **({"score": round(self.score, 3)} if self.score else {})}


SCHEMA = """
CREATE TABLE IF NOT EXISTS memories(
  id TEXT PRIMARY KEY, tier TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL, tags TEXT DEFAULT '[]',
  importance REAL DEFAULT 0.5, confidence REAL DEFAULT 0.8, created_at REAL, updated_at REAL,
  last_access REAL, access_count INTEGER DEFAULT 0, superseded_by TEXT, source TEXT, embedding BLOB);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(id UNINDEXED, body);
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY, title TEXT, platform TEXT, created_at REAL, updated_at REAL,
  summary TEXT, message_count INTEGER DEFAULT 0, reflected_upto INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, idx INTEGER, role TEXT, data TEXT, text TEXT, created_at REAL);
CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, idx);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(msg_id UNINDEXED, session_id UNINDEXED, body);
CREATE TABLE IF NOT EXISTS skill_stats(
  name TEXT PRIMARY KEY, uses INTEGER DEFAULT 0, successes INTEGER DEFAULT 0, failures INTEGER DEFAULT 0,
  last_used REAL, needs_revision INTEGER DEFAULT 0, notes TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, session_id TEXT, purpose TEXT, provider TEXT, model TEXT,
  input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0,
  cache_write_tokens INTEGER DEFAULT 0, cost_usd REAL, price_source TEXT);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);
CREATE INDEX IF NOT EXISTS usage_session ON usage(session_id);
CREATE TABLE IF NOT EXISTS turns(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, start_idx INTEGER, end_idx INTEGER, status TEXT,
  model TEXT, skills TEXT DEFAULT '[]', tool_calls INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0, duration_s REAL,
  created_at REAL);
CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id);
CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY AUTOINCREMENT, turn_id INTEGER, session_id TEXT, rating INTEGER, tags TEXT DEFAULT '[]',
  comment TEXT, created_at REAL, learned INTEGER DEFAULT 0, learn_report TEXT);
CREATE INDEX IF NOT EXISTS feedback_turn ON feedback(turn_id);
CREATE TABLE IF NOT EXISTS pending_writes(
  id TEXT PRIMARY KEY, kind TEXT, payload TEXT, created_at REAL);
"""


class MemoryError_(Exception):
    pass


class MemoryStore:
    def __init__(self, path: str | Path, *, core_char_limit: int = 1500, half_life_days: float = 30.0,
                 embedder=None):
        self.path = str(path)
        self.core_char_limit = core_char_limit
        self.half_life_days = half_life_days
        self.embedder = embedder  # Provider with async embed()
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ------------------------------------------------------------------ helpers
    def _row(self, r: sqlite3.Row, score: float = 0.0) -> Memory:
        return Memory(r["id"], r["tier"], r["kind"], r["content"], json.loads(r["tags"] or "[]"), r["importance"],
                      r["confidence"], r["created_at"], r["updated_at"], r["last_access"] or r["created_at"],
                      r["access_count"], r["superseded_by"], r["source"], score)

    def _new_id(self) -> str:
        while True:
            mid = "m" + secrets.token_hex(3)[:5]
            if not self.db.execute("SELECT 1 FROM memories WHERE id=?", (mid,)).fetchone():
                return mid

    def _index(self, mid: str, content: str, tags: list[str]) -> None:
        self.db.execute("DELETE FROM memories_fts WHERE id=?", (mid,))
        self.db.execute("INSERT INTO memories_fts(id, body) VALUES(?,?)",
                        (mid, " ".join(tokenize(content + " " + " ".join(tags)))))

    async def _embed(self, text: str) -> list[float] | None:
        if not self.embedder:
            return None
        try:
            return (await self.embedder.embed([text]))[0]
        except Exception:
            return None

    def get(self, mid: str) -> Memory | None:
        with self._lock:
            r = self.db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
        return self._row(r) if r else None

    def core(self) -> list[Memory]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM memories WHERE tier='core' AND superseded_by IS NULL "
                                   "ORDER BY importance DESC, created_at").fetchall()
        return [self._row(r) for r in rows]

    def core_chars(self) -> int:
        return sum(len(m.content) for m in self.core())

    def render_core(self) -> str:
        mems = self.core()
        if not mems:
            return ""
        used = sum(len(m.content) for m in mems)
        lines = [f"[{m.id}] ({m.kind}) {m.content}" for m in mems]
        return (f"CORE MEMORY ({used}/{self.core_char_limit} chars; edit with memory_update / memory_forget by id)\n"
                + "\n".join(lines))

    # ------------------------------------------------------------------ write
    def _near_duplicate(self, content: str, kind: str) -> Memory | None:
        toks = set(tokenize(content))
        if not toks:
            return None
        q = fts_query(content)
        if not q:
            return None
        rows = self.db.execute(
            "SELECT m.* FROM memories_fts f JOIN memories m ON m.id=f.id WHERE memories_fts MATCH ? "
            "AND m.superseded_by IS NULL AND m.tier!='archive' ORDER BY bm25(memories_fts) LIMIT 5", (q,)).fetchall()
        for r in rows:
            other = set(tokenize(r["content"]))
            jac = len(toks & other) / max(1, len(toks | other))
            if jac >= 0.85:
                return self._row(r)
        return None

    def save(self, content: str, *, kind: str = "fact", tier: str = "long", tags: list[str] | None = None,
             importance: float = 0.5, confidence: float = 0.8, source: str | None = None,
             embedding: list[float] | None = None, check: bool = True) -> dict:
        content = (content or "").strip()
        if not content:
            raise MemoryError_("empty memory")
        if check:
            issues = scan_content(content)
            if issues:
                raise MemoryError_("refused to store memory: " + "; ".join(issues))
        kind = kind if kind in KINDS else "fact"
        tier = tier if tier in {"core", "long", "archive"} else "long"
        tags = [t.strip().lower() for t in (tags or []) if t and t.strip()]
        importance = max(0.0, min(1.0, float(importance)))
        now = time.time()
        with self._lock:
            dup = self._near_duplicate(content, kind)
            if dup:
                new_imp = max(dup.importance, importance)
                new_tier = "core" if tier == "core" or dup.tier == "core" else dup.tier
                self.db.execute("UPDATE memories SET importance=?, tier=?, updated_at=?, confidence=MIN(1, confidence+0.05) "
                                "WHERE id=?", (new_imp, new_tier, now, dup.id))
                self.db.commit()
                note = {"id": dup.id, "status": "merged_with_existing", "content": dup.content}
                if new_tier == "core":
                    note["demoted"] = self._enforce_core_limit(protect=dup.id)
                return note
            mid = self._new_id()
            self.db.execute(
                "INSERT INTO memories(id,tier,kind,content,tags,importance,confidence,created_at,updated_at,last_access,"
                "access_count,source,embedding) VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?)",
                (mid, tier, kind, content, json.dumps(tags, ensure_ascii=False), importance, confidence, now, now, now,
                 source, _pack(embedding)))
            self._index(mid, content, tags)
            demoted = self._enforce_core_limit(protect=mid) if tier == "core" else []
            self.db.commit()
        out = {"id": mid, "status": "saved", "tier": tier}
        if demoted:
            out["demoted_to_long_term"] = demoted
        return out

    async def asave(self, content: str, **kw) -> dict:
        kw.setdefault("embedding", await self._embed(content))
        return self.save(content, **kw)

    def _enforce_core_limit(self, protect: str | None = None) -> list[str]:
        """Instead of failing (Hermes), demote least-important core entries to long-term."""
        demoted = []
        while True:
            rows = self.db.execute("SELECT id, content, importance FROM memories WHERE tier='core' AND "
                                   "superseded_by IS NULL ORDER BY importance ASC, last_access ASC").fetchall()
            total = sum(len(r["content"]) for r in rows)
            if total <= self.core_char_limit or len(rows) <= 1:
                break
            victim = next((r for r in rows if r["id"] != protect), None)
            if not victim:
                break
            self.db.execute("UPDATE memories SET tier='long' WHERE id=?", (victim["id"],))
            demoted.append(victim["id"])
        return demoted

    def update(self, mid: str, content: str | None = None, *, importance: float | None = None,
               tier: str | None = None, tags: list[str] | None = None, kind: str | None = None,
               embedding: list[float] | None = None) -> dict:
        """Content changes create a new version (supersede chain); metadata changes are in place."""
        with self._lock:
            old = self.get(mid)
            if not old or old.superseded_by:
                raise MemoryError_(f"memory {mid} not found or already superseded")
            if content and content.strip() != old.content:
                res = self.save(content, kind=kind or old.kind, tier=tier or old.tier, tags=tags or old.tags,
                                importance=old.importance if importance is None else importance,
                                confidence=old.confidence, source=old.source, embedding=embedding)
                if res["id"] != mid:
                    self.db.execute("UPDATE memories SET superseded_by=?, updated_at=? WHERE id=?",
                                    (res["id"], time.time(), mid))
                    self.db.execute("DELETE FROM memories_fts WHERE id=?", (mid,))
                    self.db.commit()
                return {"id": res["id"], "status": "updated", "previous": mid}
            sets, vals = [], []
            if importance is not None:
                sets.append("importance=?"); vals.append(max(0.0, min(1.0, importance)))
            if tier:
                sets.append("tier=?"); vals.append(tier)
            if kind:
                sets.append("kind=?"); vals.append(kind)
            if tags is not None:
                sets.append("tags=?"); vals.append(json.dumps(tags, ensure_ascii=False))
                self._index(mid, old.content, tags)
            if sets:
                sets.append("updated_at=?"); vals.append(time.time())
                self.db.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id=?", (*vals, mid))
                if tier == "core":
                    self._enforce_core_limit(protect=mid)
                self.db.commit()
            return {"id": mid, "status": "updated"}

    async def aupdate(self, mid: str, content: str | None = None, **kw) -> dict:
        if content:
            kw.setdefault("embedding", await self._embed(content))
        return self.update(mid, content, **kw)

    def forget(self, mid: str, hard: bool = False) -> dict:
        with self._lock:
            if not self.get(mid):
                raise MemoryError_(f"memory {mid} not found")
            if hard:
                self.db.execute("DELETE FROM memories WHERE id=?", (mid,))
                self.db.execute("DELETE FROM memories_fts WHERE id=?", (mid,))
            else:  # archived memories stay searchable via tiers=("archive",)
                self.db.execute("UPDATE memories SET tier='archive', updated_at=? WHERE id=?", (time.time(), mid))
            self.db.commit()
        return {"id": mid, "status": "deleted" if hard else "archived"}

    # ------------------------------------------------------------------ read
    def _recency(self, ts: float) -> float:
        age_days = max(0.0, (time.time() - (ts or time.time())) / 86400)
        return 0.5 ** (age_days / max(1e-6, self.half_life_days))

    def search(self, query: str, *, k: int = 8, tiers: tuple[str, ...] = ("core", "long"),
               query_embedding: list[float] | None = None, kinds: list[str] | None = None,
               touch: bool = True, min_score: float = 0.0) -> list[Memory]:
        """Hybrid retrieval: BM25 + cosine + importance + recency + frequency."""
        with self._lock:
            cands: dict[str, tuple[sqlite3.Row, float]] = {}
            q = fts_query(query)
            if q:
                rows = self.db.execute(
                    "SELECT m.*, -bm25(memories_fts) AS s FROM memories_fts f JOIN memories m ON m.id=f.id "
                    "WHERE memories_fts MATCH ? AND m.superseded_by IS NULL LIMIT 60", (q,)).fetchall()
                for r in rows:
                    cands[r["id"]] = (r, float(r["s"]))
            vec_scores: dict[str, float] = {}
            if query_embedding:
                rows = self.db.execute("SELECT * FROM memories WHERE embedding IS NOT NULL AND superseded_by IS NULL").fetchall()
                scored = [(r, cosine(query_embedding, _unpack(r["embedding"]) or [])) for r in rows]
                scored.sort(key=lambda x: x[1], reverse=True)
                for r, s in scored[:60]:
                    vec_scores[r["id"]] = s
                    cands.setdefault(r["id"], (r, 0.0))
            if not cands:
                return []
            max_lex = max((s for _, s in cands.values()), default=0) or 1.0
            results = []
            for mid, (r, lex) in cands.items():
                if r["tier"] not in tiers or (kinds and r["kind"] not in kinds):
                    continue
                lex_n = max(0.0, lex) / max_lex
                vec = vec_scores.get(mid, 0.0)
                if query_embedding:
                    rel = 0.5 * lex_n + 0.5 * max(0.0, vec)
                else:
                    rel = lex_n
                score = (0.75 * rel + 0.12 * r["importance"] + 0.08 * self._recency(r["last_access"])
                         + 0.05 * min(1.0, math.log1p(r["access_count"]) / 3))
                if rel <= 0:
                    continue
                if score >= min_score:
                    results.append(self._row(r, score))
            results.sort(key=lambda m: m.score, reverse=True)
            results = results[:k]
            if touch and results:
                now = time.time()
                self.db.executemany("UPDATE memories SET access_count=access_count+1, last_access=? WHERE id=?",
                                    [(now, m.id) for m in results])
                self.db.commit()
            return results

    async def asearch(self, query: str, **kw) -> list[Memory]:
        kw.setdefault("query_embedding", await self._embed(query))
        return self.search(query, **kw)

    def list(self, tier: str | None = None, limit: int = 200) -> list[Memory]:
        with self._lock:
            if tier:
                rows = self.db.execute("SELECT * FROM memories WHERE tier=? AND superseded_by IS NULL "
                                       "ORDER BY updated_at DESC LIMIT ?", (tier, limit)).fetchall()
            else:
                rows = self.db.execute("SELECT * FROM memories WHERE superseded_by IS NULL "
                                       "ORDER BY tier, importance DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def history(self, mid: str) -> list[Memory]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM memories WHERE superseded_by=?", (mid,)).fetchall()
        out = [self._row(r) for r in rows]
        for m in list(out):
            out.extend(self.history(m.id))
        return out

    # ------------------------------------------------------------------ consolidation
    def consolidate(self, *, archive_after_days: float = 90) -> dict:
        """Decay stale low-value memories, promote frequently used ones, merge exact duplicates."""
        now = time.time()
        report = {"archived": [], "promoted": [], "merged": []}
        with self._lock:
            rows = self.db.execute("SELECT * FROM memories WHERE tier='long' AND superseded_by IS NULL").fetchall()
            for r in rows:
                age = (now - (r["last_access"] or r["created_at"])) / 86400
                if r["importance"] < 0.3 and r["access_count"] < 2 and age > archive_after_days:
                    self.db.execute("UPDATE memories SET tier='archive' WHERE id=?", (r["id"],))
                    report["archived"].append(r["id"])
                elif r["access_count"] >= 5 and r["importance"] >= 0.7 and r["kind"] in ("preference", "profile", "lesson"):
                    if self.core_chars() + len(r["content"]) <= self.core_char_limit:
                        self.db.execute("UPDATE memories SET tier='core' WHERE id=?", (r["id"],))
                        report["promoted"].append(r["id"])
            seen: dict[str, str] = {}
            for r in self.db.execute("SELECT id, content FROM memories WHERE superseded_by IS NULL "
                                     "AND tier!='archive' ORDER BY importance DESC").fetchall():
                key = re.sub(r"\s+", " ", r["content"].strip().lower())
                if key in seen:
                    self.db.execute("UPDATE memories SET superseded_by=? WHERE id=?", (seen[key], r["id"]))
                    self.db.execute("DELETE FROM memories_fts WHERE id=?", (r["id"],))
                    report["merged"].append(r["id"])
                else:
                    seen[key] = r["id"]
            self.db.commit()
        return report

    # ------------------------------------------------------------------ sessions / episodes (L3)
    def ensure_session(self, sid: str, platform: str = "cli", title: str | None = None) -> None:
        now = time.time()
        with self._lock:
            self.db.execute("INSERT OR IGNORE INTO sessions(id,title,platform,created_at,updated_at) VALUES(?,?,?,?,?)",
                            (sid, title, platform, now, now))
            if title:
                self.db.execute("UPDATE sessions SET title=COALESCE(title, ?) WHERE id=?", (title, sid))
            self.db.commit()

    def append_messages(self, sid: str, messages: list) -> None:
        now = time.time()
        with self._lock:
            start = self.db.execute("SELECT COALESCE(MAX(idx)+1, 0) FROM messages WHERE session_id=?",
                                    (sid,)).fetchone()[0]
            for i, m in enumerate(messages):
                text = m.text()
                if m.tool_calls:
                    text += "\n" + "\n".join(f"[tool:{t.name}] {json.dumps(t.arguments, ensure_ascii=False)[:500]}"
                                             for t in m.tool_calls)
                cur = self.db.execute("INSERT INTO messages(session_id, idx, role, data, text, created_at) "
                                      "VALUES(?,?,?,?,?,?)",
                                      (sid, start + i, m.role, json.dumps(m.to_dict(), ensure_ascii=False), text, now))
                if m.role in ("user", "assistant") and text.strip():
                    self.db.execute("INSERT INTO messages_fts(msg_id, session_id, body) VALUES(?,?,?)",
                                    (cur.lastrowid, sid, " ".join(tokenize(text))))
            self.db.execute("UPDATE sessions SET updated_at=?, message_count=message_count+? WHERE id=?",
                            (now, len(messages), sid))
            self.db.commit()

    def load_messages(self, sid: str) -> list:
        from ..types import Message
        with self._lock:
            rows = self.db.execute("SELECT data FROM messages WHERE session_id=? ORDER BY idx", (sid,)).fetchall()
        return [Message.from_dict(json.loads(r["data"])) for r in rows]

    def sessions(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def session_search(self, query: str, k: int = 8) -> list[dict]:
        q = fts_query(query)
        if not q:
            return []
        with self._lock:
            rows = self.db.execute(
                "SELECT m.session_id, m.idx, m.role, m.text, m.created_at, s.title FROM messages_fts f "
                "JOIN messages m ON m.id=f.msg_id LEFT JOIN sessions s ON s.id=m.session_id "
                "WHERE messages_fts MATCH ? ORDER BY bm25(messages_fts) LIMIT ?", (q, k)).fetchall()
            summaries = self.db.execute(
                "SELECT id, title, summary FROM sessions WHERE summary IS NOT NULL").fetchall()
        hits = [{"session_id": r["session_id"], "title": r["title"], "idx": r["idx"], "role": r["role"],
                 "text": r["text"][:600], "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"]))}
                for r in rows]
        qt = set(tokenize(query))
        for s in summaries:
            if qt & set(tokenize(s["summary"])):
                hits.append({"session_id": s["id"], "title": s["title"], "summary": s["summary"][:800]})
        return hits[:k + 3]

    def session_read(self, sid: str, around: int = 0, window: int = 6) -> list[dict]:
        with self._lock:
            rows = self.db.execute("SELECT idx, role, text FROM messages WHERE session_id=? AND idx BETWEEN ? AND ? "
                                   "ORDER BY idx", (sid, around - window, around + window)).fetchall()
        return [{"idx": r["idx"], "role": r["role"], "text": r["text"][:1500]} for r in rows]

    def set_summary(self, sid: str, summary: str, reflected_upto: int | None = None) -> None:
        with self._lock:
            self.db.execute("UPDATE sessions SET summary=? WHERE id=?", (summary, sid))
            if reflected_upto is not None:
                self.db.execute("UPDATE sessions SET reflected_upto=? WHERE id=?", (reflected_upto, sid))
            self.db.commit()

    # ------------------------------------------------------------------ pending (approval-gated) writes
    def stage(self, kind: str, payload: dict) -> str:
        pid = "p" + secrets.token_hex(3)
        with self._lock:
            self.db.execute("INSERT INTO pending_writes VALUES(?,?,?,?)",
                            (pid, kind, json.dumps(payload, ensure_ascii=False), time.time()))
            self.db.commit()
        return pid

    def pending(self) -> list[dict]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM pending_writes ORDER BY created_at").fetchall()
        return [{"id": r["id"], "kind": r["kind"], "payload": json.loads(r["payload"])} for r in rows]

    def pop_pending(self, pid: str) -> dict | None:
        with self._lock:
            r = self.db.execute("SELECT * FROM pending_writes WHERE id=?", (pid,)).fetchone()
            if r:
                self.db.execute("DELETE FROM pending_writes WHERE id=?", (pid,))
                self.db.commit()
        return {"id": r["id"], "kind": r["kind"], "payload": json.loads(r["payload"])} if r else None

    # ------------------------------------------------------------------ usage & cost accounting
    def record_usage(self, *, session_id: str | None, purpose: str, provider: str, model: str, input_tokens: int,
                     output_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0,
                     cost_usd: float | None = None, price_source: str = "", ts: float | None = None) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO usage(ts, session_id, purpose, provider, model, input_tokens, output_tokens, "
                "cache_read_tokens, cache_write_tokens, cost_usd, price_source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (ts or time.time(), session_id, purpose, provider, model, input_tokens, output_tokens,
                 cache_read_tokens, cache_write_tokens, cost_usd, price_source))
            self.db.commit()

    _AGG = ("COUNT(*) AS calls, COALESCE(SUM(input_tokens),0) AS input_tokens, "
            "COALESCE(SUM(output_tokens),0) AS output_tokens, COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens, "
            "COALESCE(SUM(cache_write_tokens),0) AS cache_write_tokens, COALESCE(SUM(cost_usd),0) AS cost_usd, "
            "SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS unpriced_calls")

    def usage_totals(self, since: float = 0, until: float | None = None, session_id: str | None = None) -> dict:
        q = f"SELECT {self._AGG} FROM usage WHERE ts>=? AND ts<?"
        args: list = [since, until or time.time() + 1]
        if session_id:
            q += " AND session_id=?"
            args.append(session_id)
        with self._lock:
            r = self.db.execute(q, args).fetchone()
        d = dict(r)
        d["cost_usd"] = round(d["cost_usd"], 6)
        d["total_tokens"] = d["input_tokens"] + d["output_tokens"]
        return d

    def usage_grouped(self, by: str, since: float = 0, until: float | None = None, limit: int = 50) -> list[dict]:
        cols = {"model": "provider || '/' || model", "purpose": "purpose", "session": "session_id",
                "provider": "provider", "day": "date(ts, 'unixepoch', 'localtime')"}
        expr = cols[by]
        order = "key" if by == "day" else "cost_usd DESC, input_tokens DESC"
        with self._lock:
            rows = self.db.execute(
                f"SELECT {expr} AS key, {self._AGG} FROM usage WHERE ts>=? AND ts<? GROUP BY key "
                f"ORDER BY {order} LIMIT ?", (since, until or time.time() + 1, limit)).fetchall()
            titles = {}
            if by == "session":
                ids = [r["key"] for r in rows if r["key"]]
                if ids:
                    q = ",".join("?" * len(ids))
                    titles = {r["id"]: r["title"] for r in
                              self.db.execute(f"SELECT id, title FROM sessions WHERE id IN ({q})", ids).fetchall()}
        out = []
        for r in rows:
            d = dict(r)
            d["cost_usd"] = round(d["cost_usd"], 6)
            if by == "session":
                d["title"] = titles.get(d["key"])
            out.append(d)
        return out

    def unpriced_models(self) -> list[str]:
        with self._lock:
            rows = self.db.execute("SELECT DISTINCT provider || '/' || model AS m FROM usage WHERE cost_usd IS NULL "
                                   "ORDER BY m").fetchall()
        return [r["m"] for r in rows]

    # ------------------------------------------------------------------ turns & user feedback (self-learning data)
    def last_index(self, sid: str) -> int:
        with self._lock:
            r = self.db.execute("SELECT COALESCE(MAX(idx), -1) FROM messages WHERE session_id=?", (sid,)).fetchone()
        return int(r[0])

    def record_turn(self, sid: str, start_idx: int, end_idx: int, status: str, model: str, skills: list[str],
                    tool_calls: int, cost_usd: float, duration_s: float) -> int:
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO turns(session_id,start_idx,end_idx,status,model,skills,tool_calls,cost_usd,duration_s,"
                "created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (sid, start_idx, end_idx, status, model, json.dumps(skills), tool_calls, cost_usd, duration_s,
                 time.time()))
            self.db.commit()
            return int(cur.lastrowid)

    def turn(self, turn_id: int) -> dict | None:
        with self._lock:
            r = self.db.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["skills"] = json.loads(d["skills"] or "[]")
        return d

    def turns(self, sid: str) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT t.*, f.rating, f.tags AS fb_tags, f.comment AS fb_comment FROM turns t "
                "LEFT JOIN feedback f ON f.id = (SELECT MAX(id) FROM feedback WHERE turn_id=t.id) "
                "WHERE t.session_id=? ORDER BY t.id", (sid,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["skills"] = json.loads(d["skills"] or "[]")
            d["fb_tags"] = json.loads(d["fb_tags"] or "[]") if d.get("fb_tags") else []
            out.append(d)
        return out

    def turn_messages(self, t: dict) -> list:
        from ..types import Message
        with self._lock:
            rows = self.db.execute("SELECT data FROM messages WHERE session_id=? AND idx BETWEEN ? AND ? ORDER BY idx",
                                   (t["session_id"], t["start_idx"], t["end_idx"])).fetchall()
        return [Message.from_dict(json.loads(r["data"])) for r in rows]

    def add_feedback(self, turn_id: int, sid: str, rating: int, tags: list[str], comment: str) -> int:
        with self._lock:
            cur = self.db.execute("INSERT INTO feedback(turn_id,session_id,rating,tags,comment,created_at) "
                                  "VALUES(?,?,?,?,?,?)", (turn_id, sid, 1 if rating > 0 else -1,
                                                         json.dumps(tags, ensure_ascii=False), comment, time.time()))
            self.db.commit()
            return int(cur.lastrowid)

    def latest_feedback(self, turn_id: int) -> dict | None:
        with self._lock:
            r = self.db.execute("SELECT * FROM feedback WHERE turn_id=? ORDER BY id DESC LIMIT 1", (turn_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["tags"] = json.loads(d["tags"] or "[]")
        return d

    def update_feedback(self, fid: int, rating: int, tags: list[str], comment: str) -> None:
        with self._lock:
            self.db.execute("UPDATE feedback SET rating=?, tags=?, comment=?, learned=0 WHERE id=?",
                            (1 if rating > 0 else -1, json.dumps(tags, ensure_ascii=False), comment, fid))
            self.db.commit()

    def mark_feedback_learned(self, fid: int, report: dict) -> None:
        with self._lock:
            self.db.execute("UPDATE feedback SET learned=1, learn_report=? WHERE id=?",
                            (json.dumps(report, ensure_ascii=False, default=str)[:20000], fid))
            self.db.commit()

    def feedback_stats(self, since: float = 0) -> dict:
        with self._lock:
            tot = dict(self.db.execute(
                "SELECT COUNT(*) n, SUM(rating>0) up, SUM(rating<0) down FROM feedback WHERE created_at>=?",
                (since,)).fetchone())
            by_model = [dict(r) for r in self.db.execute(
                "SELECT t.model AS key, COUNT(*) n, SUM(f.rating>0) up, SUM(f.rating<0) down FROM feedback f "
                "JOIN turns t ON t.id=f.turn_id WHERE f.created_at>=? GROUP BY t.model ORDER BY n DESC", (since,))]
            rows = self.db.execute("SELECT f.rating, t.skills FROM feedback f JOIN turns t ON t.id=f.turn_id "
                                   "WHERE f.created_at>=?", (since,)).fetchall()
            tags = self.db.execute("SELECT tags FROM feedback WHERE rating<0 AND created_at>=?", (since,)).fetchall()
            recent = [dict(r) for r in self.db.execute(
                "SELECT f.id, f.rating, f.tags, f.comment, f.created_at, f.session_id, s.title FROM feedback f "
                "LEFT JOIN sessions s ON s.id=f.session_id WHERE f.created_at>=? AND (f.comment!='' OR f.rating<0) "
                "ORDER BY f.id DESC LIMIT 10", (since,))]
        skills: dict[str, dict] = {}
        for r in rows:
            for name in json.loads(r["skills"] or "[]"):
                s = skills.setdefault(name, {"key": name, "n": 0, "up": 0, "down": 0})
                s["n"] += 1
                s["up" if r["rating"] > 0 else "down"] += 1
        tag_count: dict[str, int] = {}
        for r in tags:
            for t in json.loads(r["tags"] or "[]"):
                tag_count[t] = tag_count.get(t, 0) + 1
        for r in recent:
            r["tags"] = json.loads(r["tags"] or "[]")
        tot = {k: int(v or 0) for k, v in tot.items()}
        tot["satisfaction"] = round(tot["up"] / tot["n"], 3) if tot["n"] else None
        return {"totals": tot, "by_model": by_model, "by_skill": sorted(skills.values(), key=lambda x: -x["n"]),
                "negative_tags": sorted(tag_count.items(), key=lambda x: -x[1]), "recent": recent}

    def feedback_dataset(self) -> list[dict]:
        """Rated turns as a JSONL-ready dataset (for evals / preference tuning)."""
        with self._lock:
            rows = self.db.execute("SELECT f.*, t.start_idx, t.end_idx, t.model, t.status, t.skills FROM feedback f "
                                   "JOIN turns t ON t.id=f.turn_id ORDER BY f.id").fetchall()
        out = []
        for r in rows:
            t = {"session_id": r["session_id"], "start_idx": r["start_idx"], "end_idx": r["end_idx"]}
            out.append({"feedback_id": r["id"], "turn_id": r["turn_id"], "rating": r["rating"],
                        "tags": json.loads(r["tags"] or "[]"), "comment": r["comment"], "model": r["model"],
                        "status": r["status"], "skills": json.loads(r["skills"] or "[]"),
                        "created_at": r["created_at"], "messages": [m.to_dict() for m in self.turn_messages(t)]})
        return out
