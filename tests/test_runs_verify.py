"""Chat turns as runs, fixed workflows declared by skills, completion checks and model escalation."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from sinobee.agent import Agent, Runtime
from sinobee.types import ChatResponse, Message, ToolCall, Usage
from sinobee.work import actions
from sinobee.work.hub import get_hub

SKILL_DIR = Path(__file__).resolve().parent.parent / "sinobee" / "bundled_skills" / "ai-video-production"


class Fake:
    """Scripted model: a list of replies (str / Message) or a function(messages) -> reply."""
    provider_name = "openai"
    model = "fake"

    def __init__(self, replies, label="test/fake"):
        self.replies = replies
        self.label = label
        self.calls = 0
        self.recorder = None

    async def chat(self, messages, tools=None, **kw):
        self.calls += 1
        if callable(self.replies):
            out = self.replies(messages)
        else:
            out = self.replies.pop(0) if self.replies else "完成。"
        return ChatResponse(out if isinstance(out, Message) else Message("assistant", out), Usage(10, 5, 0))

    async def embed(self, texts):
        raise RuntimeError("no embeddings")


def call(tool, **args):
    return Message("assistant", "", [ToolCall(f"c{time.time_ns()}", tool, args)])


@pytest.fixture
def cfg(config):
    config.data["permissions"]["toolsets"] = ["files", "skills", "shell", "work"]
    config.data["permissions"]["toolsets_v"] = 2
    config.data.setdefault("learning", {})["enabled"] = False
    return config


async def run_turn(rt, text="做一份報告", sid="s1"):
    agent = Agent(rt, session_id=sid, platform="web")
    seen = []
    orig = agent._emit
    agent._emit = lambda ev, data: (seen.append((ev, data)), orig(ev, data))
    await agent.run(text)
    return agent, seen


# ------------------------------------------------------------------ 1. every chat turn is a run
async def test_chat_turn_is_recorded_as_a_run_with_steps_files_and_steers(cfg):
    holder = {}

    def replies(messages):
        n = sum(1 for m in messages if m.role == "tool")
        if n == 0:
            holder["agent"].steer("標題改成「季報」")
            return call("write_file", path="out/report.md", content="# 報告")
        if n == 1:
            return call("read_file", path="no/such.txt")
        if n == 2:
            return call("list_dir", path="out")
        return "報告已寫好：out/report.md"
    rt = Runtime(cfg, provider=Fake(replies))
    agent = Agent(rt, session_id="s1", platform="web")
    holder["agent"] = agent
    await agent.run("寫一份報告")
    hub = get_hub(rt)
    runs = [r for r in hub.store.runs(limit=10) if r["kind"] == "chat"]
    assert len(runs) == 1
    r = runs[0]
    assert r["status"] == "done" and r["session_id"] == "s1" and "out/report.md" in r["summary"]
    st = r["state"]
    assert [s["tool"] for s in st["steps"]] == ["write_file", "read_file", "list_dir"]
    assert [s["ok"] for s in st["steps"]] == [True, False, True] and st["steps"][1]["error"].startswith("ERROR")
    assert st["artifacts"][0]["path"] == "out/report.md"
    assert st["steers"][0]["text"] == "標題改成「季報」"
    detail = hub.run_detail(r["id"])
    assert any(b["kind"] == "artifact" for b in detail["board"])
    ov = hub.overview()
    assert any(h["id"] == r["id"] for h in ov["history"])
    assert not any(h["type"] == "chat" for h in ov["history"])          # no duplicate turn row


async def test_interrupted_chat_run_resumes_in_its_session(cfg):
    rt = Runtime(cfg, provider=Fake(["好"]))
    hub = get_hub(rt)
    run = hub.store.create_run(job_id=None, name="拍片", kind="chat", trigger="chat", state={"input": "拍一段武打片"})
    hub.store.update_run(run["id"], status="interrupted", session_id="s9")
    got = []
    hub.chat_resumer = lambda sid, text: got.append((sid, text))
    hub.resume(run["id"])
    assert got and got[0][0] == "s9" and "拍一段武打片" in got[0][1] and "中斷處" in got[0][1]


# ------------------------------------------------------------------ 2. skills declare fixed workflows
def test_skill_workflow_is_in_the_catalog_and_the_quick_start(cfg):
    rt = Runtime(cfg, provider=Fake([]))
    wfs = get_hub(rt).workflows()
    assert wfs["video-production"]["skill"] == "ai-video-production"
    skill = rt.skills.skills["ai-video-production"]
    assert skill.workflow[0] == "video-production"
    assert skill.quickstart.startswith('Preferred: run_workflow(name="video-production"')


@pytest.mark.parametrize("approve", [True, False])
async def test_video_workflow_plans_renders_and_asks_before_the_final(cfg, monkeypatch, approve):
    example = (SKILL_DIR / "assets" / "example-film-duel.json").read_text(encoding="utf-8")
    entry_calls = []

    async def fake_entry(ctx, skill, args, timeout=3600):
        entry_calls.append(args)
        folder = args[0]
        if args[1:3] == ["--budget", "3"]:
            return {"status": "working", "folder": folder}
        if "--until" in args:
            return {"status": "ready_for_final", "folder": folder, "preview": f"{folder}/out/draft.mp4",
                    "units": 3, "seconds": 22.5, "estimate_minutes": 30, "steps_done": ["render"]}
        return {"status": "done", "folder": folder, "film": f"{folder}/out/final.mp4", "steps_done": ["assemble"]}
    monkeypatch.setattr(actions, "_run_entry", fake_entry)
    rt = Runtime(cfg, provider=Fake(lambda messages: f"```json\n{example}\n```"))
    hub = get_hub(rt)
    run = hub.start_workflow("video-production", {"intent": "古裝武打片，雙方使用繡春刀"}, trigger="manual")
    for _ in range(300):
        await asyncio.sleep(0.02)
        if hub.store.approvals("pending"):
            break
    a = hub.store.approvals("pending")[0]
    assert "預覽" in a["reason"] and "30 分鐘" in a["reason"]
    hub.decide(a["id"], approve)
    await asyncio.wait_for(hub.tasks[run["id"]], 30)
    r = hub.store.run(run["id"])
    assert r["status"] == "done", r["error"]
    nodes = r["state"]["nodes"]
    folder = nodes["plan"]["output"]["folder"]
    plan = json.loads((Path(cfg.workspace) / folder / "plan.json").read_text(encoding="utf-8"))
    assert plan["title"] == "繡春刀・黃昏對決" and plan["intent"].startswith("古裝武打片")
    if approve:
        assert nodes["final"]["status"] == "done" and nodes["deliver_draft"]["status"] == "skipped"
        assert entry_calls[-1] == [folder, "--budget", "600"]
    else:
        assert nodes["final"]["status"] == "skipped" and nodes["deliver_draft"]["status"] == "done"
        assert entry_calls[-1][-1] == "--draft-only"


# ------------------------------------------------------------------ 3. completion check and escalation
async def test_completion_check_sends_the_agent_back_once_then_reports_incomplete(cfg):
    cfg.data["agent"]["verify"] = True
    main = Fake([call("list_dir", path="."), call("list_dir", path=".."), call("write_file", path="a.txt", content="x"),
                 "全部完成了！",                                      # verifier says no -> one more round
                 "已經都做完了。"])
    verifier = Fake(['{"complete": false, "missing": "沒有寄出郵件"}'] * 2)
    rt = Runtime(cfg, provider=main, aux_provider=verifier)
    agent, seen = await run_turn(rt, "整理檔案並寄出郵件")
    assert any(m.role == "user" and "[自動完成檢查] 任務還沒有完成：沒有寄出郵件" in m.text() for m in agent.history)
    assert agent.last_status["status"] == "incomplete"
    assert "沒有寄出郵件" in agent.last_status["error"]["summary"]
    run = [r for r in get_hub(rt).store.runs(limit=5) if r["kind"] == "chat"][0]
    assert any("完成檢查未通過" in e["text"] for e in run["state"]["events"])


async def test_completion_check_passes(cfg):
    cfg.data["agent"]["verify"] = True
    main = Fake([call("list_dir", path="."), call("list_dir", path=".."), call("list_dir", path="/"), "列好了。"])
    rt = Runtime(cfg, provider=main, aux_provider=Fake(['{"complete": true, "missing": ""}']))
    agent, seen = await run_turn(rt, "列出資料夾")
    assert agent.last_status["status"] == "done"


async def test_repeated_failures_consult_the_thinking_model_and_never_switch(cfg):
    weak = Fake([call("read_file", path=f"missing{i}.txt") for i in range(3)] + ["找不到這些檔案，請確認路徑。"],
                label="ollama/small")
    strong = Fake(["路徑錯了，先列出資料夾。\nDECISION: continue"], label="anthropic/strong")
    rt = Runtime(cfg, provider=weak, thinking_provider=strong)
    agent, seen = await run_turn(rt, "讀檔")
    assert strong.calls == 1 and weak.calls == 4                # every step stayed on the execution model
    assert any(e == "status" and "請教思維模型" in d["message"] for e, d in seen)
    assert any(e == "status" and "思維模型 anthropic/strong" in d["message"] for e, d in seen)
    assert not any(e == "status" and "改用" in d["message"] for e, d in seen)


async def test_complex_skill_is_planned_by_the_thinking_model(cfg):
    cfg.data["agent"]["plan"] = "auto"
    weak = Fake(["好的，開始規劃影片。"], label="ollama/small")
    plan = json.dumps({"understanding": "拍一段影片", "approach": "用 ai-video-production 技能",
                       "steps": ["skill_view ai-video-production", "寫 plan.json"],
                       "checklist": [{"text": "影片檔存在", "check": "ls"}], "risks": [], "ask_user": None})
    strong = Fake([plan], label="anthropic/strong")
    rt = Runtime(cfg, provider=weak, thinking_provider=strong)
    agent, seen = await run_turn(rt, "/ai-video-production 拍一段影片")
    assert strong.calls == 1                      # planned once; the open checklist item then went back to the executor
    assert any(m.role == "user" and m.text().startswith("[思維模型的計畫]") for m in agent.history)
    assert agent.ctx.state["checklist"][0]["text"] == "影片檔存在"
    assert any(e == "plan" for e, d in seen)


def test_no_escalation_model_configured(cfg):
    rt = Runtime(cfg, provider=Fake([]))
    assert rt.strong() is None
