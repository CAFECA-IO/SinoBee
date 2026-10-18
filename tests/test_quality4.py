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
