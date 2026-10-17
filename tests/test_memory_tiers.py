"""Four memory layers (task / short / long / permanent), compression, and add/remove on instruction."""
from __future__ import annotations

import json
import sqlite3
import time

import pytest

from sinobee.agent import Runtime
from sinobee.memory.compress import compress, extractive, lost_details
from sinobee.memory.store import MemoryError_, MemoryStore
from sinobee.tools import REGISTRY, ToolContext
from sinobee.types import ChatResponse, Message, ToolCall, Usage


class Aux:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    async def chat(self, messages, tools=None, **kw):
        self.prompts.append(messages[-1].text())
        out = self.replies.pop(0) if self.replies else "{}"
        return ChatResponse(Message("assistant", out), Usage(1, 1, 0))


def _age(store: MemoryStore, mid: str, **cols) -> None:
    sets = ", ".join(f"{k}=?" for k in cols)
    store.db.execute(f"UPDATE memories SET {sets} WHERE id=?", (*cols.values(), mid))
    store.db.commit()


# ------------------------------------------------------------------ store: tiers and lifecycle
def test_migration_adds_columns_to_an_old_database(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE memories(id TEXT PRIMARY KEY, tier TEXT NOT NULL, kind TEXT NOT NULL, content TEXT "
                "NOT NULL, tags TEXT DEFAULT '[]', importance REAL DEFAULT 0.5, confidence REAL DEFAULT 0.8, "
                "created_at REAL, updated_at REAL, last_access REAL, access_count INTEGER DEFAULT 0, superseded_by "
                "TEXT, source TEXT, embedding BLOB)")
    con.execute("INSERT INTO memories(id,tier,kind,content,created_at,updated_at) VALUES('m1','long','fact','舊的記憶',1,1)")
    con.commit()
    con.close()
    s = MemoryStore(db)
    m = s.get("m1")
    assert m.pinned is False and m.expires_at is None and m.label == "long"


def test_short_memory_gets_an_expiry_and_permanent_is_pinned(tmp_path):
    s = MemoryStore(tmp_path / "m.db", short_ttl_days=10)
    a = s.save("影片專案目前做到第 3 個鏡頭", tier="short")
    b = s.save("公司統編是 12345678", tier="permanent")
    ma, mb = s.get(a["id"]), s.get(b["id"])
    assert ma.tier == "short" and 9.9 < (ma.expires_at - time.time()) / 86400 <= 10
    assert mb.tier == "long" and mb.pinned and mb.label == "permanent" and mb.expires_at is None
    assert {m.id for m in s.search("鏡頭 專案")} >= {a["id"]}          # short is recalled by default


def test_resaving_a_short_fact_promotes_it_and_drops_the_expiry(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    a = s.save("使用者的 NAS 位址是 nas.local", tier="short")
    b = s.save("使用者的 NAS 位址是 nas.local", tier="long")
    assert b["id"] == a["id"] and b["tier"] == "long"
    assert s.get(a["id"]).expires_at is None


def test_consolidate_keeps_useful_short_memories_and_archives_the_rest(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    used = s.save("報告模板放在 templates/q.md", tier="short")["id"]
    unused = s.save("今天測試了三次登入流程", tier="short")["id"]
    important = s.save("客戶要求所有輸出用 A4 直式", tier="short", importance=0.8)["id"]
    past = time.time() - 1
    for mid in (used, unused, important):
        _age(s, mid, expires_at=past)
    _age(s, used, access_count=3)
    rep = s.consolidate()
    assert set(rep["kept_short"]) == {used, important} and rep["expired_short"] == [unused]
    assert s.get(used).tier == "long" and s.get(unused).tier == "archive"


def test_permanent_memories_survive_decay_core_limits_and_dedupe(tmp_path):
    s = MemoryStore(tmp_path / "m.db", core_char_limit=40)
    p = s.save("永遠用繁體中文回答使用者", tier="core", pinned=True, importance=0.2)["id"]
    s.save("User likes very short answers please", tier="core", importance=0.95)
    assert s.get(p).tier == "core"                                     # not demoted although least important
    old = s.save("一個很少用的舊事實", importance=0.1, pinned=True)["id"]
    _age(s, old, last_access=time.time() - 400 * 86400, created_at=time.time() - 400 * 86400)
    s.consolidate()
    assert s.get(old).tier == "long"                                   # not archived
    st = s.stats()
    assert st["permanent"]["count"] == 2 and st["core_usage"]["limit"] == 40


def test_forgetting_a_permanent_memory_needs_force(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    p = s.save("家裡 Wi-Fi 名稱是 Bee", tier="permanent")["id"]
    with pytest.raises(MemoryError_):
        s.forget(p)
    assert s.forget(p, force=True)["status"] == "archived"
    m = s.get(p)
    assert m.tier == "archive" and not m.pinned


def test_update_moves_between_tiers(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    mid = s.save("專案代號是 Hive", tier="long")["id"]
    s.update(mid, tier="short", ttl_days=3)
    assert s.get(mid).expires_at and s.get(mid).tier == "short"
    s.update(mid, tier="permanent")
    m = s.get(mid)
    assert m.pinned and m.tier == "long" and m.expires_at is None
    new = s.update(mid, "專案代號是 Hive-2")["id"]                       # new version keeps the pin
    assert s.get(new).pinned and s.get(mid).superseded_by == new


# ------------------------------------------------------------------ compression
def test_extractive_merge_drops_covered_statements():
    s_mems = []
    st = MemoryStore(":memory:")
    for t in ["使用者偏好深色主題", "使用者偏好深色主題介面", "使用者的編輯器是 VS Code"]:
        s_mems.append(st.get(st.save(t, kind="preference", dedupe=False)["id"]))
    out = extractive(s_mems[:2])
    assert out.count("深色主題") == 1


def test_lost_details_detects_numbers_and_paths():
    st = MemoryStore(":memory:")
    m = st.get(st.save("輸出在 videos/duel/out/final.mp4，長度 22.5 秒")["id"])
    assert set(lost_details([m], "輸出影片在資料夾裡")) >= {"videos/duel/out/final.mp4", "22.5"}
    assert lost_details([m], "videos/duel/out/final.mp4 長 22.5 秒") == []


async def test_compress_merges_a_cluster_with_the_model_and_keeps_history(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    ids = [s.save(t, kind="environment", tags=["gpu"], dedupe=False)["id"] for t in [
        "GPU 伺服器 211.22.118.147 跑 ComfyUI",
        "GPU 伺服器 211.22.118.147 的 ComfyUI 在 8188 埠",
        "GPU 伺服器 211.22.118.147 有 MiniMax H3 模型"]]
    keep = s.save("使用者喜歡簡短回答", kind="preference")["id"]
    aux = Aux(['{"statements": ["GPU 伺服器 211.22.118.147 跑 ComfyUI（8188 埠），裝有 MiniMax H3 模型"]}'] * 2)
    preview = await compress(s, aux, dry_run=True)
    assert not preview["applied"] and len(preview["clusters"]) == 1
    c = preview["clusters"][0]
    assert set(c["sources"]) == set(ids) and c["method"] == "model" and c["saved_chars"] > 0
    assert all(s.get(i).superseded_by is None for i in ids)              # preview changes nothing
    rep = await compress(s, aux, dry_run=False)
    new = rep["clusters"][0]["new_id"]
    assert rep["applied"] and all(s.get(i).superseded_by == new for i in ids)
    assert s.get(new).tags == ["gpu"] and s.get(keep).superseded_by is None
    assert {m.id for m in s.history(new)} == set(ids)
    assert rep["chars_after"] < rep["chars_before"]


async def test_compress_rejects_a_lossy_model_merge_and_skips_pinned(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    a = s.save("備份腳本在 /opt/backup/run.sh 每天 03:00 執行", kind="procedure", dedupe=False)["id"]
    b = s.save("備份腳本在 /opt/backup/run.sh 執行", kind="procedure", dedupe=False)["id"]
    p = s.save("備份腳本在 /opt/backup/run.sh 由 root 執行", kind="procedure", pinned=True, dedupe=False)["id"]
    aux = Aux(['{"statements": ["有一個每天的備份腳本"]}'])               # drops the path and the time
    rep = await compress(s, aux, dry_run=True)
    assert rep["skipped_pinned"] == [p]
    assert rep["clusters"] and rep["clusters"][0]["method"] == "extractive"
    assert "/opt/backup/run.sh" in rep["clusters"][0]["merged"] and "03:00" in rep["clusters"][0]["merged"]
    assert set(rep["clusters"][0]["sources"]) == {a, b}


async def test_compress_target_suggests_forget_candidates_without_removing(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    low = s.save("使用者某天提到天氣很熱", importance=0.1)["id"]
    s.save("使用者是 CAFECA 的工程師", kind="profile", importance=0.9)
    rep = await compress(s, None, target_chars=10)
    assert rep["forget_candidates"][0]["id"] == low and s.get(low).tier == "long"


# ------------------------------------------------------------------ tools: add / remove on instruction
def _ctx(config, store, aux=None):
    rt = type("RT", (), {"aux": aux})()
    ctx = ToolContext(config=config, session_id="s1", memory=store)
    ctx.state["runtime"] = rt
    return ctx


async def _call(ctx, name, **args):
    res = await REGISTRY.dispatch(ToolCall("c1", name, args), ctx)
    try:
        return json.loads(res.content), res.is_error
    except (json.JSONDecodeError, TypeError):
        return res.content, res.is_error


async def test_forget_by_query_previews_then_removes_and_protects_permanent(config, tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    a = s.save("A 專案的截止日是 11/30", tags=["a-project"])["id"]
    b = s.save("A 專案的負責人是小王", tags=["a-project"])["id"]
    p = s.save("A 專案的合約編號 C-77 永久保存", tags=["a-project"], pinned=True)["id"]
    other = s.save("B 專案用 PostgreSQL")["id"]
    ctx = _ctx(config, s)
    out, err = await _call(ctx, "memory_forget", query="A 專案", tag="a-project")
    assert not err and {m["id"] for m in out["preview"]} == {a, b, p}
    assert s.get(a).tier == "long"                                      # preview did not remove anything
    out, err = await _call(ctx, "memory_forget", ids=[a, b, p], confirm=True)
    assert set(out["ids"]) == {a, b} and out["skipped_permanent"] == [p]
    assert s.get(a).tier == "archive" and s.get(p).pinned and s.get(other).tier == "long"
    out, err = await _call(ctx, "memory_forget", id=p)
    assert err and "permanent" in out
    out, err = await _call(ctx, "memory_forget", id=p, confirm_permanent=True)
    assert out["status"] == "archived"


async def test_save_update_stats_and_compress_tools(config, tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    ctx = _ctx(config, s, aux=None)
    out, _ = await _call(ctx, "memory_save", content="本週要交 Q3 報告", tier="short", ttl_days=7)
    sid = out["id"]
    out, _ = await _call(ctx, "memory_save", content="使用者的生日是 3/14", tier="permanent", kind="profile")
    pid = out["id"]
    assert s.get(sid).tier == "short" and s.get(pid).pinned
    out, err = await _call(ctx, "memory_update", id=pid, content="使用者的生日是 3/15")
    assert "permanent" in out and s.get(pid).superseded_by is None      # needs the user's confirmation
    out, _ = await _call(ctx, "memory_update", id=pid, content="使用者的生日是 3/15", confirm_permanent=True)
    assert s.get(out["id"]).pinned
    out, _ = await _call(ctx, "memory_stats")
    assert out["short"]["count"] == 1 and out["permanent"]["count"] == 1
    out, _ = await _call(ctx, "memory_search", query="生日", tier="permanent")
    assert out[0]["pinned"]
    rep, _ = await _call(ctx, "memory_compress")
    assert rep["applied"] is False


def test_skill_is_bundled_and_valid(config):
    rt = Runtime(config, provider=Aux([]))
    sk = rt.skills.skills["memory-management"]
    assert "memory_compress" in sk.metadata["sinobee-requires-tools"] and sk.trust == "builtin"
    assert not [d for d in sk.diagnostics if "error" in str(d).lower()]
    assert (sk.path / "references" / "tier-policy.md").exists() and (sk.path / "references" / "compression.md").exists()


async def test_reflection_never_writes_permanent_and_defaults_task_facts_to_short():
    from sinobee.learning.reflector import _reflect_tier
    assert _reflect_tier({"tier": "permanent", "kind": "fact"}) == "short"
    assert _reflect_tier({"kind": "fact"}) == "short"
    assert _reflect_tier({"kind": "preference", "importance": 0.9}) == "long"
    assert _reflect_tier({"tier": "core"}) == "core"


def test_anatomy_is_bundled_with_its_credits(config):
    """The Anatomy skill (MIT, by Ryan @wheresryan22) ships built in and keeps its source and author."""
    rt = Runtime(config, provider=Aux([]))
    sk = rt.skills.skills["anatomy"]
    assert sk.trust == "builtin" and not [d for d in sk.diagnostics if "error" in str(d).lower()]
    assert sk.metadata["author"].startswith("Ryan") and sk.metadata["sinobee-source"] == "https://github.com/wheresryan22/anatomy"
    for f in ("LICENSE", "NOTICE.md", "kit/iso-kit.mjs", "scripts/capture.mjs", "references/craft.md"):
        assert (sk.path / f).exists(), f
    assert "Copyright (c) 2026 Ryan" in (sk.path / "LICENSE").read_text()
