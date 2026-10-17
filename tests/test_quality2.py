"""Quality step 2: quality levels, the fresh-context acceptance review, the independent reviewer, consulting the
stronger model, and better delegation."""
import json

from conftest import ScriptedProvider
from sinobee.agent import Agent, Runtime
from sinobee.agent import review
from sinobee.config import Config
from sinobee.types import Message, ToolCall


def msg(text="", calls=None):
    return Message("assistant", text, [ToolCall(f"x{i}_{n}", n, a) for i, (n, a) in enumerate(calls or [])])


def judge(verdict, problems=(), summary=""):
    return msg(json.dumps({"verdict": verdict, "items": [], "problems": list(problems), "summary": summary}))


def test_quality_levels(config):
    config.data["agent"]["verify"] = True

    def lvl(calls, stats=None, complex_task=False):
        hist = [Message("assistant", "", [ToolCall(str(i), n, a) for i, (n, a) in enumerate(calls)])]
        return review.quality_level(config, hist, {"tool_calls": len(calls), **(stats or {})}, complex_task)[0]
    assert lvl([]) == "fast"
    assert lvl([("list_dir", {}), ("read_file", {}), ("read_file", {})]) == "standard"
    assert lvl([("write_file", {"path": "a.md"})], {"artifacts": [{"path": "a.md"}]}) == "standard"
    assert lvl([("edit_file", {"path": "src/app.py"})]) == "strict"
    assert lvl([("web_search", {}), ("web_fetch", {}), ("write_file", {"path": "r.md"})], {"artifacts": [{"path": "r.md"}]}) == "strict"
    assert lvl([("db_query", {})], {"final_text": "共 42 筆"}) == "strict"
    assert lvl([], complex_task=True) == "strict"
    config.data["agent"]["quality"] = "fast"
    assert lvl([("edit_file", {"path": "x.py"})]) == "fast"
    config.data["agent"]["quality"] = "auto"
    config.data["agent"]["verify"] = False
    assert lvl([("edit_file", {"path": "x.py"})]) == "fast"


def test_review_parsing_and_fix_list():
    assert review.parse("not json") is None
    assert review.parse('{"verdict": "maybe"}') is None
    assert review.parse('{"verdict": "revise", "problems": []}')["verdict"] == "pass"      # nothing to fix
    d = review.parse('```json\n{"verdict": "revise", "items": [{"id": 2, "ok": false, "note": "沒有來源"}],'
                     ' "problems": [{"what": "數字錯誤", "evidence": "第 3 段", "fix": "改成 42"}]}\n```')
    fl = review.fix_list(d, [{"id": 2, "text": "附上來源"}])
    assert "驗收清單 #2 附上來源：沒有來源" in fl and "數字錯誤（第 3 段） → 改成 42" in fl


async def test_acceptance_review_sends_back_a_fix_list_until_the_result_passes(home):
    main = ScriptedProvider([msg("", [("write_file", {"path": "r.md", "content": "# 報告\n\n市佔 30%\n"})]),
                             msg("寫好了"),
                             msg("", [("write_file", {"path": "r.md", "content": "# 報告\n\n市佔 30%（來源：IDC 2026）\n"})]),
                             msg("已補上來源")])
    strong = ScriptedProvider([judge("revise", [{"what": "市佔數字沒有來源", "fix": "補上出處"}], "缺來源"),
                               judge("pass", summary="可以交付")])
    rt = Runtime(Config.load(), provider=main, strong_provider=strong)
    agent = Agent(rt)
    out = await agent.run("寫一份市佔報告存成 r.md")
    assert out == "已補上來源" and agent.last_status["status"] == "done"
    fb = next(m.text() for m in agent.history if m.text().startswith("[自動驗收審查]"))
    assert "市佔數字沒有來源 → 補上出處" in fb
    (first, tools), (second, _) = strong.requests
    assert tools is None and len(first) == 1                       # a fresh context, no tools
    assert "USER REQUEST:\n寫一份市佔報告" in first[0].text() and "市佔 30%" in first[0].text()
    assert "IDC 2026" in second[0].text()                          # the second review sees the fixed file


async def test_review_that_never_passes_marks_the_turn_incomplete(home):
    main = ScriptedProvider([msg("", [("write_file", {"path": "r.md", "content": "# x\n"})]), msg("好了"), msg("改了")])
    strong = ScriptedProvider([judge("revise", [{"what": "內容太少"}]), judge("revise", [{"what": "還是太少"}])])
    rt = Runtime(Config.load(), provider=main, strong_provider=strong)
    agent = Agent(rt)
    await agent.run("寫一份報告存成 r.md")
    assert agent.last_status["status"] == "incomplete"
    assert "還是太少" in agent.last_status["error"]["detail"]


async def test_strict_level_runs_an_independent_reviewer_first(home):
    main = ScriptedProvider([msg("", [("write_file", {"path": "calc.py", "content": "def add(a, b):\n    return a - b\n"})]),
                             msg("完成"),
                             msg("問題：add 用了減號\nVERDICT: revise - add 回傳 a - b"),       # the reviewer sub-agent
                             msg("", [("write_file", {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"})]),
                             msg("修好了")])
    strong = ScriptedProvider([judge("revise", [{"what": "add 回傳 a - b", "fix": "改成 a + b"}]), judge("pass")])
    rt = Runtime(Config.load(), provider=main, strong_provider=strong)
    agent = Agent(rt)
    out = await agent.run("寫一個 add 函式存成 calc.py")
    assert out == "修好了"
    first = strong.requests[0][0][0].text()
    assert "QUALITY LEVEL: strict" in first and "INDEPENDENT REVIEWER'S FINDINGS" in first and "VERDICT: revise" in first
    reviewer_req = main.requests[2][0]
    assert "REVIEWER sub-agent" in reviewer_req[0].text()
    assert {t.name for t in main.requests[2][1]} & {"write_file", "edit_file", "delegate"} == set()   # read-only


async def test_stuck_agent_consults_the_stronger_model_and_carries_on(home):
    main = ScriptedProvider([msg("", [("read_file", {"path": f"missing{i}.txt"})]) for i in range(3)]
                            + [msg("找到了，答案是 42")])
    strong = ScriptedProvider([msg("檔案不存在：先用 list_dir 看資料夾裡有哪些檔案。"), judge("pass")])
    rt = Runtime(Config.load(), provider=main, strong_provider=strong)
    agent = Agent(rt)
    out = await agent.run("讀那個檔案")
    assert out == "找到了，答案是 42"
    advice = [m.text() for m in agent.history if m.text().startswith("[較強模型的建議]")]
    assert advice and "list_dir" in advice[0]
    consult_req, consult_tools = strong.requests[0]
    assert consult_tools is None and any(m.role == "tool" for m in consult_req)      # it saw the whole context
    assert agent._prov is main                                                       # the main model carried on


async def test_delegate_briefs_and_condensed_reports(home):
    from sinobee.tools.work_tools import delegate
    long_report = "細節 " * 2000
    main = ScriptedProvider([msg(long_report)])
    cfg = Config.load()
    cfg.data["permissions"]["toolsets"] = list(cfg.data["permissions"]["toolsets"]) + ["work"]
    rt = Runtime(cfg, provider=main)
    agent = Agent(rt)
    agent.ctx.state["runtime"] = rt
    out = await delegate(agent.ctx, [{"role": "researcher", "task": "研究甲", "output": "三點摘要",
                                      "boundaries": "不要研究乙"}])
    brief = main.requests[0][0][-2].text() if main.requests[0][0][-1].role == "user" and \
        any(getattr(p, "volatile", False) for p in main.requests[0][0][-1].content) else main.requests[0][0][-1].text()
    assert "Expected output:\n三點摘要" in brief and "Out of scope:\n不要研究乙" in brief
    assert len(out[0]["report"]) < 3200 and "完整報告" in out[0]["report"]


def test_quality_settings_round_trip(home, provider):
    from fastapi.testclient import TestClient
    from sinobee.server import create_app
    rt = Runtime(Config.load(), provider=provider)
    c = TestClient(create_app(rt))
    assert c.get("/api/settings").json()["agent"]["quality"] == "auto"
    r = c.put("/api/settings", json={"agent": {"quality": "strict"}, "model": {"escalate_mode": "consult"}})
    assert r.status_code == 200, r.text
    s = c.get("/api/settings").json()
    assert s["agent"]["quality"] == "strict" and s["model"]["escalate_mode"] == "consult"
    assert c.put("/api/settings", json={"agent": {"quality": "extreme"}}).status_code == 200
    assert c.get("/api/settings").json()["agent"]["quality"] == "strict"          # invalid values are ignored
