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
    assert neutral(s) == "The agent utilized the 'anatomy' skill and generated an HTML report."
    assert neutral("審查完成，完全符合交付標準。代理成功地建立了報告") == "審查完成。代理建立了報告"
    assert neutral("was generated and reviewed, showcasing advanced technical analysis and modern web design.") == \
        "was generated and reviewed, showcasing technical analysis."
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


async def test_there_is_no_fixed_step_limit_but_an_explicit_one_still_holds(home):
    # no step cap for a turn: 70 steps of new information run to the end (progress decides, agent/progress.py)
    main = ScriptedProvider([msg("", [("list_dir", {"path": f"d{i}"})]) for i in range(70)] + [msg("好了")])
    cfg = Config.load()
    cfg.data["agent"].update(verify=False)
    agent = Agent(Runtime(cfg, provider=main))
    assert await agent.run("看一下") == "好了" and agent.last_status["status"] == "done"
    # a caller's explicit budget (a sub-agent, the reviewer) is still respected
    main2 = ScriptedProvider([msg("", [("list_dir", {"path": f"d{i}"})]) for i in range(8)] + [msg("好了")])
    agent2 = Agent(Runtime(cfg, provider=main2), max_iterations=3)
    await agent2.run("看一下")
    assert agent2.last_status["status"] == "incomplete" and "3 步" in agent2.last_status["error"]["summary"]


async def test_a_stalled_thinking_model_is_skipped_not_replaced(home):
    import asyncio as _a

    class Stalled(ScriptedProvider):
        label = "gemini/gemini-pro-latest"

        async def chat(self, messages, tools=None, **kw):
            await _a.sleep(5)
            return await super().chat(messages, tools, **kw)
    main = ScriptedProvider([msg("執行模型的回覆")])
    cfg = Config.load()
    cfg.data["agent"]["model_deadline"] = 0.2
    events = []
    agent = Agent(Runtime(cfg, provider=main, thinking_provider=Stalled([msg("太慢")])),
                  emit=lambda k, d: events.append((k, d)))
    agent._prov = main
    stats = {}
    assert await agent._thinking_call("規劃", [Message("user", "hi")], 100, stats) is None
    assert stats["thinking_skipped"][0].startswith("規劃") and not main.requests      # nobody else took the call
    assert any(k == "status" and "思維模型" in d["message"] for k, d in events)
    resp = await agent._step_chat([Message("user", "hi")], [], 100, {})                # steps stay on execution
    assert resp.message.text() == "執行模型的回覆" and agent._prov is main


async def test_timeouts_are_retried_once(monkeypatch):
    import httpx
    from sinobee.providers.base import Provider, ProviderError
    calls = []

    class P(Provider):
        def __init__(self):
            super().__init__("y", "http://x", None, provider_name="x")

        async def _chat(self, *a, **k):
            calls.append(1)
            raise httpx.ReadTimeout("slow")
    monkeypatch.setattr("asyncio.sleep", lambda *_: _noop())
    with pytest.raises(ProviderError):
        await P().chat([Message("user", "hi")])
    assert len(calls) == 2


async def _noop():
    return None


def test_recall_does_not_bring_a_roadster_note_to_a_reactor_question(home):
    from sinobee.memory.store import MemoryStore
    cfg = Config.load()
    mem = MemoryStore(cfg.db_path)
    mem.save("正在分析的系統是 Tesla Roadster 的「SpaceX 套件」冷氣推進器（Cold Gas Thruster）系統，其核心原理是利用高壓"
             "氣體通過拉伐爾噴嘴進行等熵膨脹釋放推力。", kind="fact", tier="short", importance=0.9,
             tags=["spacex", "cold gas thruster", "tesla roadster", "流體力學"])     # as stored on the user's machine
    mem.save("用戶要求總結目前的進展和卡點，並繼續深入分析。代理首先總結了已建立的科學原則和已識別的技術缺口。",
             kind="episode", tier="long", importance=0.3)
    mem.save("釷基熔鹽反應爐的燃料鹽是 LiF-BeF2-ThF4-UF4，需要 99.95% 以上的 Li-7。", kind="fact", tier="long")
    hits = mem.search("解析釷基熔鹽反應爐 SMR 可行性方案設計與原理解說", k=6, tiers=("short", "long"), min_score=0.15,
                      min_rel=0.3)
    assert [h.kind for h in hits] == ["fact"], [h.content[:20] for h in hits]
    assert mem.search("Roadster 冷氣推進器的推力", k=6, tiers=("short",), min_score=0.15, min_rel=0.3)[0] \
        .content.startswith("正在分析")


async def test_a_stalled_acceptance_review_is_recorded_as_skipped(home):
    import asyncio as _a

    class Stalled(ScriptedProvider):
        label = "gemini/gemini-pro-latest"

        async def chat(self, messages, tools=None, **kw):
            await _a.sleep(5)
            return await super().chat(messages, tools, **kw)
    main = ScriptedProvider([msg("", [("write_file", {"path": "r.md", "content": "# r\n"})]), msg("完成")])
    cfg = Config.load()
    cfg.data["agent"]["model_deadline"] = 0.2
    cfg.data["agent"]["quality"] = "standard"
    cfg.data["agent"]["plan"] = "off"
    events = []
    agent = Agent(Runtime(cfg, provider=main, thinking_provider=Stalled([])), emit=lambda k, d: events.append((k, d)))
    out = await agent.run("寫 r.md")
    assert out == "完成" and len(main.requests) == 2            # the execution model was not made the judge
    end = next(d for k, d in events if k == "turn_end")
    assert any(x.startswith("驗收審查") for x in end["quality_skipped"])


async def test_a_reviewers_revise_stands_when_the_judge_cannot_run(home, monkeypatch):
    main = ScriptedProvider([msg("", [("write_file", {"path": "r.md", "content": "# r\n"})]), msg("完成"),
                             msg("原理解說缺漏\nVERDICT: revise - report.md 沒有原理解說"),          # reviewer
                             msg("", [("write_file", {"path": "r.md", "content": "# r\n\n## 原理\n補上\n"})]),
                             msg("補好了")])
    cfg = Config.load()
    cfg.data["agent"]["quality"] = "strict"
    agent = Agent(Runtime(cfg, provider=main))

    async def judge_down(*a, **k):
        return None
    monkeypatch.setattr(agent, "_review", _wrap_review(agent, judge_down))
    out = await agent.run("寫 r.md")
    fb = [m.text() for m in agent.history if m.text().startswith("[自動驗收審查]")]
    assert fb and "沒有原理解說" in fb[0] and out == "補好了"


def _wrap_review(agent, then):

    async def review(query, final_text, stats, level):
        if "reviewed" not in stats:                      # run the real independent reviewer once
            stats["reviewed"] = True
            stats["reviewer"] = await agent._independent_review(query, final_text, [], [])
        return await then()
    return review


async def test_qa_flags_a_drawing_that_spills_out_of_its_frame(tmp_path):
    pytest.importorskip("playwright")
    from sinobee.agent import qa
    page = tmp_path / "fig.html"
    page.write_text('<svg viewBox="0 0 800 600" style="overflow:visible;width:400px">'
                    '<rect x="-1300" y="-400" width="3400" height="2500"/></svg><p>x</p>', encoding="utf-8")
    ok = tmp_path / "ok.html"
    ok.write_text('<svg viewBox="0 0 800 600"><rect x="10" y="10" width="700" height="500"/></svg><p>x</p>',
                  encoding="utf-8")
    issues = await qa.check([page, ok])
    if not any("JavaScript" in str(i) or "畫框" in str(i) for i in issues) and not issues:
        pytest.skip("no browser for qa")
    texts = [str(i) for i in issues]
    assert any("超出自己的畫框" in t and "fig.html" in t for t in texts)
    assert not any("ok.html" in t for t in texts)
