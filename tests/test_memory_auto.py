"""Automatic memory upkeep: after turns, on a schedule, and when the context nears the model limit."""
from __future__ import annotations

import asyncio
import json
import time

from sinobee.agent import Agent, Runtime
from sinobee.memory.maintain import due, maintain, maybe_maintain
from sinobee.memory.working import compress_working
from sinobee.types import ChatResponse, Message, ToolCall, Usage
from sinobee.work.hub import get_hub


class Model:
    """Fake model answering by prompt content (main, auxiliary and summary calls alike)."""
    provider_name = "openai"
    model = "fake"
    label = "test/fake"

    def __init__(self, main=None, aux=None):
        self.main = main or (lambda msgs: "好的。")
        self.aux = aux or (lambda text: "{}")
        self.requests = []
        self.recorder = None

    async def chat(self, messages, tools=None, **kw):
        self.requests.append(messages)
        last = messages[-1].text() if messages else ""
        if tools:
            out = self.main(messages)
        else:
            compaction = messages and messages[0].role == "system"         # compact() sends its own system prompt
            out = self.aux("SUMMARISE" if compaction else last)
        return ChatResponse(out if isinstance(out, Message) else Message("assistant", out), Usage(10, 5, 0))

    async def embed(self, texts):
        raise RuntimeError("no embeddings")


def _old(store, mid, days=3):
    t = time.time() - days * 86400
    store.db.execute("UPDATE memories SET updated_at=?, created_at=?, last_access=? WHERE id=?", (t, t, t, mid))
    store.db.commit()


# ------------------------------------------------------------------ background upkeep after turns
async def test_maintenance_is_rate_limited_and_runs_in_the_background(config):
    config.data["memory"]["auto_maintain"] = True
    rt = Runtime(config, provider=Model())
    m = rt.memory
    expired = m.save("上週的部署狀態：等待審核", tier="short")["id"]
    m.db.execute("UPDATE memories SET expires_at=? WHERE id=?", (time.time() - 1, expired))
    m.db.commit()
    assert due(rt)
    task = maybe_maintain(rt)
    await task
    assert m.get(expired).tier == "archive"
    assert not due(rt) and maybe_maintain(rt) is None                  # next run only after the interval
    assert m.meta_get("maintain.last_report")["consolidation"]["expired_short"] == [expired]


async def test_a_chat_turn_triggers_upkeep(config):
    config.data["memory"]["auto_maintain"] = True
    rt = Runtime(config, provider=Model())
    await Agent(rt, session_id="s1", platform="web").run("你好")
    await rt.drain(10)
    assert rt.memory.meta_get("maintain.last_at")


async def test_automatic_compression_skips_recent_core_and_permanent_and_is_reversible(config):
    config.data["memory"].update(auto_compress_chars=50)
    merged_text = "NAS 位址 nas.local，備份到 /volume1/backup，每晚 02:00"
    rt = Runtime(config, provider=Model(aux=lambda t: json.dumps({"statements": [merged_text]}, ensure_ascii=False)))
    m = rt.memory
    olds = [m.save(t, kind="environment", dedupe=False)["id"] for t in [
        "NAS 位址是 nas.local，備份到 /volume1/backup",
        "NAS 位址 nas.local 每晚 02:00 備份到 /volume1/backup"]]
    for i in olds:
        _old(m, i)
    fresh = m.save("NAS 位址 nas.local 剛改成用 /volume1/backup", kind="environment", dedupe=False)["id"]
    pinned = m.save("NAS 位址 nas.local 的管理員是 Luphia", kind="environment", pinned=True, dedupe=False)["id"]
    core = m.save("NAS 位址 nas.local 只能在內網使用", kind="environment", tier="core", dedupe=False)["id"]
    for i in (pinned, core):
        _old(m, i)
    rep = await maintain(rt, force=True)
    merged = rep["compression"]["merged"]
    assert len(merged) == 1 and set(merged[0]["sources"]) == set(olds)
    new = merged[0]["new_id"]
    assert m.get(new).content == merged_text
    for i in (fresh, pinned, core):
        assert m.get(i).superseded_by is None
    m.restore(new)                                                     # undo
    assert all(m.get(i).superseded_by is None for i in olds) and m.get(new).tier == "archive"


# ------------------------------------------------------------------ the daily scheduled job
async def test_daily_job_is_seeded_once_and_runs_the_upkeep(config):
    config.data["memory"]["schedule"] = True
    rt = Runtime(config, provider=Model())
    hub = get_hub(rt)
    hub.seed_system_jobs()
    jobs = [j for j in hub.store.jobs() if j["workflow"] == "memory-maintenance"]
    assert len(jobs) == 1 and jobs[0]["schedule"] == {"type": "daily", "time": "03:17"} and jobs[0]["next_run_at"]
    hub.delete_job(jobs[0]["id"])
    hub.seed_system_jobs()                                             # the user's deletion is respected
    assert not [j for j in hub.store.jobs() if j["workflow"] == "memory-maintenance"]
    run = hub.start_workflow("memory-maintenance", {}, trigger="schedule")
    await asyncio.wait_for(hub.tasks[run["id"]], 10)
    r = hub.store.run(run["id"])
    assert r["status"] == "done", r["error"]
    assert "短期記憶轉長期" in r["state"]["nodes"]["maintain"]["output"]["summary"]


# ------------------------------------------------------------------ context near the model limit
async def test_working_memory_compression_keeps_keys():
    wm = {"goal": "整理報告", "done": "\n".join(f"第 {i} 步完成：處理檔案 file{i}.csv" for i in range(200)),
          "next": "寄出報告"}
    out = await compress_working(wm, None, 1000)
    assert set(out) == set(wm) and sum(len(v) for v in out.values()) < sum(len(v) for v in wm.values())
    assert out["goal"] == "整理報告" and "file199.csv" in out["done"]           # newest progress kept


async def test_context_pressure_shrinks_memory_then_compacts_and_flushes_to_memory(config):
    config.data["memory"]["flush_on_compact"] = True
    config.data["agent"]["context_budget"] = 9000

    def aux(text):
        if "SUMMARISE" in text:
            return "使用者在整理 Q3 報告，資料在 data/q3.csv。"
        if "scratchpad" in text:
            return json.dumps({"goal": "整理 Q3 報告", "done": "前 300 步已完成", "next": "畫圖"}, ensure_ascii=False)
        if "about to be summarised" in text:
            return json.dumps({"memories": [{"content": "Q3 報告的資料在 data/q3.csv", "kind": "environment",
                                             "tier": "short", "importance": 0.6}]}, ensure_ascii=False)
        return "{}"
    model = Model(main=lambda msgs: "繼續處理中。", aux=aux)
    rt = Runtime(config, provider=model)
    agent = Agent(rt, session_id="s2", platform="web")
    for i in range(6):
        agent.history += [Message("user", f"第 {i} 段資料：" + "數據" * 900), Message("assistant", "收到" * 400)]
    agent.ctx.working.update(goal="整理 Q3 報告", done="\n".join(f"第 {i} 步完成" for i in range(300)), next="畫圖")
    seen = []
    agent.emit_fn = lambda ev, d: seen.append((ev, d))
    await agent.run("繼續")
    await rt.drain(10)
    assert agent.ctx.working["done"] == "前 300 步已完成"                 # memory shrunk first
    assert any(e == "status" and "工作記憶過長" in d.get("message", "") for e, d in seen)
    assert any(e == "status" and "重點存入短期記憶" in d.get("message", "") for e, d in seen)
    hits = rt.memory.search("Q3 報告 資料")
    assert hits and hits[0].tier == "short" and "context-flush" in hits[0].tags


# ------------------------------------------------------------------ choosing the skill and seeing fresh memory
async def test_memory_requests_name_the_memory_skill_but_ram_questions_do_not(config):
    config.data["permissions"]["toolsets"] = ["memory", "skills"]
    rt = Runtime(config, provider=Model())
    agent = Agent(rt, session_id="s3", platform="web")
    block = await agent._build_context("請永久記住我的統編是 12345678")
    assert '<suggested_skill name="memory-management">' in block and "Quick start" in block
    block = await agent._build_context("電腦記憶體不足怎麼辦")
    assert "suggested_skill" not in block


async def test_working_memory_written_mid_task_is_visible_on_the_next_step(config):
    calls = []

    def main(msgs):
        calls.append(msgs[-1].text() if msgs[-1].role != "tool" else
                     next(m.text() for m in reversed(msgs) if m.role == "user"))
        if len(calls) == 1:
            return Message("assistant", "", [ToolCall("c1", "working_memory",
                                                      {"action": "set", "key": "plan", "value": "先讀檔再畫圖"})])
        return "好了。"
    rt = Runtime(config, provider=Model(main=main))
    await Agent(rt, session_id="s4", platform="web").run("畫一張圖")
    assert "先讀檔再畫圖" not in calls[0] and "先讀檔再畫圖" in calls[1]
