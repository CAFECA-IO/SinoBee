"""Findings from the Roadster Rocket run (2026-10-18): the acceptance review crashed on HTML screenshots, a quality
step that crashes must not fail a finished task, and the tree must stay free of undefined names."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ScriptedProvider
from sinobee.agent import Agent, Runtime
from sinobee.config import Config
from sinobee.types import Message, ToolCall

ROOT = Path(__file__).resolve().parent.parent


def msg(text="", calls=None):
    return Message("assistant", text, [ToolCall(f"x{i}_{n}", n, a) for i, (n, a) in enumerate(calls or [])])


def judge(verdict, problems=(), summary=""):
    return msg(json.dumps({"verdict": verdict, "items": [], "problems": list(problems), "summary": summary}))


# ---------- the `Path` crash ----------

async def test_review_with_an_html_screenshot_does_not_crash(home, monkeypatch):
    """The review attaches screenshots of HTML results; that branch used `Path` without importing it."""
    from sinobee.agent import qa

    async def fake_check(paths, shots=None):
        if shots is not None:
            for p in paths:
                shots[str(p)] = b"\xff\xd8fakejpeg"
        return []
    monkeypatch.setattr(qa, "check", fake_check)
    main = ScriptedProvider([msg("", [("write_file", {"path": "page.html", "content": "<h1>hi</h1>"})]), msg("做好了")])
    strong = ScriptedProvider([judge("pass", summary="ok")])
    cfg = Config.load()
    cfg.data["model"]["vision"] = True
    rt = Runtime(cfg, provider=main, strong_provider=strong)
    agent = Agent(rt)
    out = await agent.run("做一個網頁 page.html")
    assert out == "做好了" and agent.last_status["status"] == "done"
    (review_req, _), = strong.requests
    assert any("Screenshot of page.html" in getattr(p, "text", "") for p in review_req[0].content)


async def test_a_crashing_quality_step_never_fails_a_finished_task(home, monkeypatch):
    events = []
    main = ScriptedProvider([msg("", [("write_file", {"path": "r.md", "content": "# r\n"})]), msg("完成")])
    rt = Runtime(Config.load(), provider=main, strong_provider=ScriptedProvider([]))
    agent = Agent(rt, emit=lambda kind, data: events.append((kind, data)))

    async def boom(*a, **k):
        raise NameError("name 'Path' is not defined")
    monkeypatch.setattr(agent, "_acceptance", boom)
    out = await agent.run("寫 r.md")
    assert out == "完成" and agent.last_status["status"] == "done"
    end = next(d for k, d in events if k == "turn_end")
    assert end["quality_skipped"] and "NameError" in end["quality_skipped"][0]
    assert any(k == "status" and "驗收審查發生內部錯誤" in d["message"] for k, d in events)


# ---------- lint: undefined names never reach a release again ----------

@pytest.mark.skipif(shutil.which("ruff") is None and subprocess.run([sys.executable, "-m", "ruff", "--version"],
                    capture_output=True).returncode != 0, reason="ruff not installed")
def test_no_undefined_or_unused_names():
    cmd = [sys.executable, "-m", "ruff"] if subprocess.run([sys.executable, "-m", "ruff", "--version"],
                                                           capture_output=True).returncode == 0 else ["ruff"]
    res = subprocess.run(cmd + ["check", "--select", "F821,F811,F401,F823", "--output-format", "concise", "sinobee"],
                         cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr


# ---------- reviewer hardening ----------

def test_unbacked_check_claims_are_found():
    from sinobee.agent import review
    turn = [msg("", [("skill_run", {"name": "engineering-feasibility", "args": "check reports/feasibility/x"}),
                     ("shell", {"command": "node page.mjs"})])]
    said = "page.html 整合 Fig 1 至 Fig 5，audit 與 check 均無錯誤。VERDICT: pass"
    found = review.unbacked_claims(said, turn)
    assert len(found) == 1 and found[0].startswith("audit")              # check ran, audit never did
    turn.append(msg("", [("shell", {"command": "node fig1.mjs --audit > audits/fig1.txt"})]))
    assert review.unbacked_claims(said, turn) == []
    assert review.unbacked_claims("所有測試通過", turn)[0].startswith("test")
    assert review.unbacked_claims("報告已寫好", turn) == []


async def test_reviewer_runs_on_the_reviewer_model_and_its_unbacked_pass_is_flagged(home):
    main = ScriptedProvider([msg("", [("read_file", {"path": "src.md"})]),
                             msg("", [("write_file", {"path": "r.md", "content": "# 報告\n空氣 313 kg\n"})]),
                             msg("完成，所有 audit 都通過")])
    reviewer = ScriptedProvider([msg("數字都對，audit 均通過。\nVERDICT: pass"),              # the reviewer sub-agent
                                 judge("revise", [{"what": "313 kg 與來源 250 kg 不符", "fix": "用 250 kg"}])])
    cfg = Config.load()
    cfg.data["agent"]["quality"] = "strict"
    (Path(cfg.workspace) / "src.md").parent.mkdir(parents=True, exist_ok=True)
    (Path(cfg.workspace) / "src.md").write_text("there's about 250 kilos of air", encoding="utf-8")
    rt = Runtime(cfg, provider=main, strong_provider=ScriptedProvider([]), reviewer_provider=reviewer)
    agent = Agent(rt)
    await agent.run("依 src.md 寫報告 r.md")
    task = next(m.text() for m in reviewer.requests[0][0] if "Subtask" in m.text())
    assert "SOURCE CHECK" in task and "WORK LOG" in task and "src.md" in task
    judge_prompt = reviewer.requests[1][0][0].text()
    assert "[系統核對] 審查員宣稱以下檢查通過" in judge_prompt          # the reviewer's audit claim is voided
    assert "UNBACKED CLAIMS" in judge_prompt and "所有 audit 都通過" in judge_prompt   # and the agent's own
    assert "WORK LOG" in judge_prompt and "read_file src.md" in judge_prompt


# ---------- memory and evidence ----------

def test_episode_summaries_lose_self_praise_and_are_labelled_when_recalled():
    import time
    from sinobee.agent.prompt import build_context_block
    from sinobee.memory.neutral import neutral
    from sinobee.memory.store import Memory
    s = ("The agent successfully utilized the 'anatomy' skill and generated a visually enhanced HTML report, "
         "which fully meets the delivery standard.")
    assert neutral(s) == "The agent utilized the 'anatomy' skill and generated a HTML report."
    assert neutral("審查完成，完全符合交付標準。代理成功地建立了報告") == "審查完成。代理建立了報告"
    m = Memory("m1", "long", "episode", s, ["episode"], 0.3, 1.0, time.time(), time.time(), time.time(), 0)
    block = build_context_block(recalled=[m], skills_index="", working={}, core_changes=[])
    assert "successfully" not in block and "a lead, not a result - re-verify" in block


async def test_checklist_evidence_must_be_checkable(home, provider):
    provider.push("", [("task_checklist", {"action": "set", "items": [{"text": "模型使用影片的 250 kg"}]})])
    provider.push("", [("shell", {"command": "echo AIR_KG=250"})])
    provider.push("", [("task_checklist", {"action": "update", "id": 1, "status": "done",
                                           "evidence": "check script reported no problems"})])   # a claim, not evidence
    provider.push("", [("task_checklist", {"action": "update", "id": 1, "status": "done",
                                           "evidence": "shell 輸出 `AIR_KG=250`"})])               # copied output
    provider.push("好了")
    cfg = Config.load()
    cfg.data["permissions"]["approval"] = "auto"
    agent = Agent(Runtime(cfg, provider=provider))
    await agent.run("確認模型輸入")
    texts = [m.text() for m in agent.history]
    assert any("the evidence must be checkable" in t for t in texts)
    assert agent.ctx.state["checklist"] == [] or agent.ctx.state["checklist"][0]["status"] == "done"


# ---------- latency ----------

def test_inline_compaction_avoids_a_local_aux_model_when_the_main_model_is_remote(home):
    main, aux = ScriptedProvider([]), ScriptedProvider([])
    aux.label = "ollama/gemma4:e4b"
    rt = Runtime(Config.load(), provider=main, aux_provider=aux)
    assert rt.inline_aux() is main                     # the task would wait on a small local model
    rt.config.data["agent"]["inline_aux"] = "auxiliary"
    assert rt.inline_aux() is aux
    rt.config.data["agent"]["inline_aux"] = "auto"
    aux.label = "openai/gpt-5-mini"
    assert rt.inline_aux() is aux                      # a cloud auxiliary model is still used


def test_core_eval_has_the_source_numbers_regression():
    suite = json.loads((ROOT / "sinobee" / "evals" / "suites" / "core.json").read_text(encoding="utf-8"))
    task = next(t for t in suite["tasks"] if t["id"] == "source-numbers-win")
    assert "250 kilos of air" in task["setup"]["files"]["talk/transcript.md"]
