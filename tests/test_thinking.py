"""Thinking model vs execution model: the thinking model plans, advises and decides; the execution model runs every
step; models are never switched; a task the execution model cannot do fails with a report for improving SinoBee."""
import json

from sinobee.agent import Agent, Runtime, failures
from sinobee.agent.thinking import parse_decision, parse_plan
from sinobee.config import Config
from sinobee.types import Message, ToolCall

from conftest import ScriptedProvider


def msg(text="", calls=None):
    return Message("assistant", text, [ToolCall(f"c{i}{n}", n, a) for i, (n, a) in enumerate(calls or [])])


def test_parsers():
    adv, d, why = parse_decision("先用 list_dir。\n\nDECISION: stop - 它一直產生無效的工具呼叫")
    assert adv == "先用 list_dir。" and d == "stop" and why == "它一直產生無效的工具呼叫"
    assert parse_decision("照做就好\n**DECISION: continue**")[1] == "continue"
    assert parse_decision("沒有決定行")[1] == "continue"
    p = parse_plan('```json\n{"understanding":"u","approach":"a","steps":["s1"],"checklist":["c1",{"text":"c2","check":"k"}],'
                   '"risks":[],"ask_user":"null"}\n```')
    assert p["steps"] == ["s1"] and [c["text"] for c in p["checklist"]] == ["c1", "c2"] and p["ask_user"] is None
    assert parse_plan("not json") is None


def test_old_escalate_setting_becomes_the_thinking_model(home):
    (home / "home").mkdir(parents=True, exist_ok=True)
    (home / "home" / "config.yaml").write_text("model:\n  escalate: openai/gpt-5.5\nagent:\n  escalate_after_errors: 5\n",
                                               encoding="utf-8")
    cfg = Config.load()
    assert cfg.get("model.thinking") == "openai/gpt-5.5" and cfg.get("agent.consult_after_errors") == 5


async def test_the_thinking_model_plans_and_the_execution_model_runs_every_step(home, config):
    config.data["agent"]["plan"] = "always"
    plan = {"understanding": "寫一份摘要", "approach": "讀檔後寫 summary.md", "steps": ["read_file notes.md", "write_file summary.md"],
            "checklist": [{"text": "summary.md 存在", "check": "list_dir"}], "risks": ["數字要照原文"], "ask_user": None}
    thinker = ScriptedProvider([msg(json.dumps(plan, ensure_ascii=False))])
    thinker.label = "anthropic/thinker"
    main = ScriptedProvider([msg("", [("write_file", {"path": "summary.md", "content": "摘要"})]),
                             msg("", [("task_checklist", {"action": "update", "id": 1, "status": "done",
                                                          "evidence": "summary.md"})]),
                             msg("寫好了")])
    events = []
    rt = Runtime(config, provider=main, thinking_provider=thinker)
    agent = Agent(rt, emit=lambda k, d: events.append((k, d)))
    out = await agent.run("摘要 notes.md")
    assert out == "寫好了" and agent.last_status["status"] == "done"
    req, tools = thinker.requests[0]
    assert tools is None and "THINKING model" in req[0].text()             # the planner gets no tools
    assert all(t for _, t in main.requests)                                 # every step on the execution model
    note = next(m.text() for m in agent.history if m.text().startswith("[思維模型的計畫]"))
    assert "anthropic/thinker" in note and "數字要照原文" in note
    assert any(k == "plan" and d["steps"] == plan["steps"] for k, d in events)
    end = next(d for k, d in events if k == "turn_end")
    assert end["plan"] is True and end["failure"] is None


async def test_thinking_model_says_stop_and_the_task_fails_with_a_report(home, config):
    main = ScriptedProvider([msg("", [("read_file", {"path": f"missing{i}.txt"})]) for i in range(3)])
    thinker = ScriptedProvider([
        msg("它一直讀不存在的檔案，也沒有先列出資料夾。\nDECISION: stop - 執行模型無法依照結果調整做法"),
        msg(json.dumps({"category": "execution_capability", "summary": "重複讀不存在的檔案",
                        "failed_at": "第 1-3 步", "evidence": ["missing0.txt"],
                        "improvements": [{"target": "model", "change": "換用能讀懂錯誤訊息的執行模型"}],
                        "retry_advice": "換執行模型後重試"}, ensure_ascii=False))])
    thinker.label = "anthropic/thinker"
    events = []
    rt = Runtime(config, provider=main, thinking_provider=thinker)
    agent = Agent(rt, emit=lambda k, d: events.append((k, d)))
    out = await agent.run("讀那個檔案，告訴我內容")
    assert agent.last_status["status"] == "failed" and agent.last_status["error"]["kind"] == "capability"
    assert "執行模型無法依照結果調整做法" in out and len(main.requests) == 3
    end = next(d for k, d in events if k == "turn_end")
    rid = end["failure"]
    assert rid and end["consults"] == 1
    await rt.drain(5)
    rep = failures.get(config.home, rid)
    assert rep["status"] == "failed" and rep["request"] == "讀那個檔案，告訴我內容"
    assert rep["models"] == {"execution": main.label, "thinking": "anthropic/thinker", "reviewer": None}
    assert [w["tool"] for w in rep["work_log"] if "tool" in w] == ["read_file"] * 3
    assert all(w.get("ok") is False for w in rep["work_log"] if "tool" in w)
    assert rep["consults"][0]["decision"] == "stop"
    assert rep["diagnosis"]["category"] == "execution_capability" and rep["diagnosis"]["model"] == "anthropic/thinker"
    assert any(k == "failure_diagnosed" for k, _ in events)
    row = failures.listing(config.home)[0]
    assert row["id"] == rid and row["category"] == "execution_capability"
    task = failures.to_regression(config.home, rid)
    assert task["prompt"] == "讀那個檔案，告訴我內容" and failures.get(config.home, rid)["regression"] == task["id"]


async def test_stuck_without_a_way_forward_fails_after_the_allowed_advice(home, config):
    config.data["agent"]["consult_max"] = 1
    main = ScriptedProvider([msg("", [("read_file", {"path": f"m{i}.txt"})]) for i in range(12)])
    thinker = ScriptedProvider([msg("換個檔名試試\nDECISION: continue")])
    rt = Runtime(config, provider=main, thinking_provider=thinker)
    config.data["agent"]["failure_diagnosis"] = False
    agent = Agent(rt)
    await agent.run("讀檔")
    st = agent.last_status
    assert st["status"] == "failed" and "執行模型無法完成" in st["error"]["summary"]
    assert len(thinker.requests) == 1                                   # one consult, no switch
    assert all(t for _, t in main.requests)


def test_secrets_are_scrubbed_from_the_work_log():
    msgs = [Message("assistant", "", [ToolCall("a", "shell", {"command": "curl -H 'key: sk-very-secret-123'"})]),
            Message("tool", "ERROR: 401 for sk-very-secret-123", tool_call_id="a")]
    log = failures.work_log(msgs, ["sk-very-secret-123"])
    assert "sk-very-secret" not in json.dumps(log) and log[0]["ok"] is False


def test_failures_api(home, config, provider):
    from fastapi.testclient import TestClient
    from sinobee.server import create_app
    failures.save(config.home, {"id": "20261019-000000-abc-001", "created": 1, "status": "failed",
                                "reason": {"summary": "x"}, "request": "做 X", "work_log": [], "diagnosis": None})
    with TestClient(create_app(Runtime(config, provider=provider))) as c:
        assert c.get("/api/failures").json()["failures"][0]["id"] == "20261019-000000-abc-001"
        assert c.get("/api/failures/20261019-000000-abc-001").json()["request"] == "做 X"
        assert c.get("/api/failures/..%2Fx").status_code == 404
        assert c.post("/api/failures/20261019-000000-abc-001/regression").json()["kind"] == "regression"
        assert c.delete("/api/failures/20261019-000000-abc-001").json()["ok"]
        assert c.get("/api/failures").json()["failures"] == []
