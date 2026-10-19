"""Interrupted tasks are found every minute and resumed by themselves; the auxiliary model decides and writes the
brief, the resumed turn runs in the same conversation."""
import json
import time

from sinobee.agent import Runtime
from sinobee.types import Message
from sinobee.work.hub import get_hub

from conftest import ScriptedProvider


def setup(config, aux_replies):
    aux = ScriptedProvider([Message("assistant", r if isinstance(r, str) else json.dumps(r, ensure_ascii=False))
                            for r in aux_replies])
    rt = Runtime(config, provider=ScriptedProvider(), aux_provider=aux)
    hub = get_hub(rt)
    calls = []
    hub.chat_resumer = lambda sid, text: calls.append((sid, text))
    hub.chat_alive = lambda sid: False
    return rt, hub, calls, aux


def chat_run(rt, hub, sid, status="interrupted", age=300, steps=None):
    rt.memory.ensure_session(sid, "web", title="分析釷基熔鹽反應爐")
    run = hub.store.create_run(job_id=None, name="分析釷基熔鹽反應爐", kind="chat", trigger="chat",
                               state={"input": "解析釷基熔鹽反應爐 SMR 可行性", "steps": steps or [
                                   {"tool": "skill_view", "brief": "engineering-feasibility", "ok": True},
                                   {"tool": "web_search", "brief": "thorium MSR", "ok": True}]})
    hub.store.update_run(run["id"], status=status, session_id=sid, created_at=time.time() - age,
                         started_at=time.time() - age, error="SinoBee 重新啟動時中斷" if status == "interrupted" else "")
    return run["id"]


BRIEF = {"resume": True, "reason": "研究做到一半就中斷了", "done": ["載入技能", "搜尋資料"],
         "remaining": ["建立 scaffold", "寫報告"], "next": "執行 scaffold"}


async def test_an_interrupted_turn_is_resumed_with_the_auxiliary_models_brief(home, config):
    rt, hub, calls, aux = setup(config, [BRIEF])
    rid = chat_run(rt, hub, "s1")
    done = await hub.recovery.check()
    assert done == [{"run_id": rid, "kind": "chat", "session_id": "s1"}]
    sid, text = calls[0]
    assert sid == "s1" and text.startswith("[自動續跑]") and "先做：執行 scaffold" in text and "還沒做：建立 scaffold" in text
    assert "web_search thorium MSR -> ok" in aux.requests[0][0][0].text()            # it read the work log
    st = hub.store.run(rid)["state"]["auto_resume"]
    assert st["resumed"] is True and st["reason"] == "研究做到一半就中斷了"
    assert await hub.recovery.check() == [] and len(calls) == 1                     # never twice


async def test_a_turn_left_running_by_a_dead_task_is_marked_and_resumed(home, config):
    rt, hub, calls, _ = setup(config, [BRIEF])
    rid = chat_run(rt, hub, "s2", status="running", age=180)
    hub.chat_alive = lambda sid: sid == "other"
    await hub.recovery.check()
    assert hub.store.run(rid)["status"] == "interrupted" and calls and calls[0][0] == "s2"


async def test_a_live_or_young_turn_is_left_alone(home, config):
    rt, hub, calls, _ = setup(config, [BRIEF])
    chat_run(rt, hub, "s3", status="running", age=180)
    young = chat_run(rt, hub, "s4", status="running", age=10)
    hub.chat_alive = lambda sid: sid == "s3"
    await hub.recovery.check()
    assert not calls and hub.store.run(young)["status"] == "running"


async def test_the_auxiliary_model_can_decide_not_to_resume(home, config):
    rt, hub, calls, _ = setup(config, [{"resume": False, "reason": "已經回答完了"}])
    rid = chat_run(rt, hub, "s5")
    await hub.recovery.check()
    assert not calls and hub.store.run(rid)["state"]["auto_resume"] == {**hub.store.run(rid)["state"]["auto_resume"],
                                                                      "resumed": False, "reason": "已經回答完了"}


async def test_limits_old_newer_and_daily_budget(home, config):
    config.data["work"]["auto_resume_max"] = 1
    rt, hub, calls, _ = setup(config, [BRIEF, BRIEF])
    old = chat_run(rt, hub, "s6", age=3 * 86400)                                     # outside the window
    superseded = chat_run(rt, hub, "s7", age=600)
    chat_run(rt, hub, "s7", status="done", age=60)                                   # a newer turn exists
    first = chat_run(rt, hub, "s8", age=600)
    await hub.recovery.check()
    assert [c[0] for c in calls] == ["s8"]
    assert "auto_resume" not in hub.store.run(old)["state"]
    assert hub.store.run(superseded)["state"]["auto_resume"]["resumed"] is False
    again = chat_run(rt, hub, "s8", age=30)                                          # interrupted again the same day
    await hub.recovery.check()
    assert len(calls) == 1 and "今天已自動續跑 1 次" in hub.store.run(again)["state"]["auto_resume"]["reason"]
    assert hub.store.run(first)["state"]["auto_resume"]["resumed"] is True


async def test_cli_sessions_and_the_off_switch(home, config):
    rt, hub, calls, _ = setup(config, [BRIEF])
    rid = chat_run(rt, hub, "s9")
    rt.memory.db.execute("UPDATE sessions SET platform='cli' WHERE id='s9'")
    rt.memory.db.commit()
    await hub.recovery.check()
    assert not calls
    config.data["work"]["auto_resume"] = False
    rt.memory.db.execute("UPDATE sessions SET platform='web' WHERE id='s9'")
    rt.memory.db.commit()
    await hub.recovery.check()
    assert not calls and "auto_resume" not in hub.store.run(rid)["state"]


async def test_an_interrupted_scheduled_task_starts_again_once(home, config, monkeypatch):
    rt, hub, calls, _ = setup(config, [])
    started = []
    monkeypatch.setattr(hub, "run_job", lambda jid, trigger="manual": started.append((jid, trigger)) or {"id": "r"})
    run = hub.store.create_run(job_id="j1", name="每日報告", kind="task", trigger="schedule", state={})
    hub.store.update_run(run["id"], status="interrupted", created_at=time.time() - 100)
    await hub.recovery.check()
    await hub.recovery.check()
    assert started == [("j1", "retry")]


def test_interrupted_api(home, config):
    from fastapi.testclient import TestClient
    from sinobee.server import create_app
    rt = Runtime(config, provider=ScriptedProvider())
    hub = get_hub(rt)
    with TestClient(create_app(rt)) as c:
        assert c.get("/api/sessions/s1/interrupted").json() == {"interrupted": False}
        rid = chat_run(rt, hub, "s1")
        r = c.get("/api/sessions/s1/interrupted").json()
        assert r["interrupted"] and r["run_id"] == rid and "重新啟動" in r["error"]
