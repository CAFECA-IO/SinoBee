"""Step 1 of the quality pipeline: a cache-friendly request layout, the acceptance checklist and the deterministic
check of what a task produced."""
import asyncio
import json

import pytest

from sinobee.agent import Agent, Runtime
from sinobee.agent import qa
from sinobee.config import Config
from sinobee.providers.anthropic import AnthropicProvider, to_anthropic
from sinobee.types import Message, TextPart, ToolCall


def volatile(m):
    return isinstance(m.content, list) and any(getattr(p, "volatile", False) for p in m.content)


async def test_history_is_append_only_and_the_volatile_context_rides_at_the_end(home, provider):
    provider.push("", [("list_dir", {"path": "."})])
    provider.push("", [("list_dir", {"path": "."})])
    provider.push("好了")
    rt = Runtime(Config.load(), provider=provider)
    await Agent(rt).run("看一下資料夾")
    reqs = [m for m, _ in provider.requests if any(x.role == "system" for x in m)]
    assert len(reqs) >= 3
    for r in reqs:
        assert volatile(r[-1]) and "<context>" in r[-1].text()          # context only in the tail
        assert not any(volatile(m) for m in r[:-1])
        assert all("<context>\n" not in m.text() for m in r[1:-1])
    for a, b in zip(reqs, reqs[1:]):                                    # each request extends the previous one
        assert [m.to_dict() for m in b[:len(a) - 1]] == [m.to_dict() for m in a[:-1]]


def test_anthropic_cache_breakpoint_skips_the_volatile_tail(monkeypatch):
    sent = {}

    async def post(self, url, payload, headers):
        sent.update(payload)
        return {"content": [{"type": "text", "text": "ok"}], "usage": {}, "stop_reason": "end_turn"}
    monkeypatch.setattr(AnthropicProvider, "_post", post)
    p = AnthropicProvider(model="claude-x", api_key="k", base_url="https://api.anthropic.com")
    msgs = [Message("system", "sys"), Message("user", "做事"),
            Message("assistant", "", [ToolCall("t1", "list_dir", {})]), Message("tool", "a.txt", tool_call_id="t1"),
            Message("user", [TextPart("<context>time 10:01</context>", volatile=True)])]
    asyncio.run(p.chat(msgs, None))
    last = sent["messages"][-1]["content"]
    assert last[-1]["text"].startswith("<context>") and "cache_control" not in last[-1]
    assert last[0]["type"] == "tool_result" and last[0]["cache_control"] == {"type": "ephemeral"}
    assert all("_volatile" not in b for m in sent["messages"] for b in m["content"])
    assert to_anthropic(msgs)[1][-1]["content"][-1]["_volatile"] is True


async def test_checklist_must_be_closed_with_evidence_before_the_task_ends(home, provider):
    provider.push("", [("task_checklist", {"action": "set", "items": [{"text": "報告存成 report.md", "check": "檔案存在"},
                                                                      {"text": "附上資料來源"}]})])
    provider.push("做完了")                                                     # stops with both items open
    provider.push("", [("task_checklist", {"action": "update", "id": 1, "status": "done"})])     # no evidence
    provider.push("", [("task_checklist", {"action": "update", "id": 1, "status": "done", "evidence": "write_file wrote report.md"}),
                       ("task_checklist", {"action": "update", "id": 2, "status": "dropped", "note": "使用者說不用"})])
    provider.push("報告完成")
    rt = Runtime(Config.load(), provider=provider)
    agent = Agent(rt)
    out = await agent.run("寫報告")
    assert out == "報告完成"
    texts = [m.text() for m in agent.history]
    assert any(t.startswith("[自動驗收] 驗收清單還有 2 項未完成") for t in texts)
    assert any("needs evidence" in t for t in texts)
    tails = [m[-1].text() for m, _ in provider.requests if any(x.role == "system" for x in m)]
    assert "<task_checklist>" in tails[1] and "1. [ ] 報告存成 report.md — check: 檔案存在" in tails[1]
    assert [i["status"] for i in agent.ctx.state["checklist"]] == ["done", "dropped"]
    # a closed list belongs to the finished task: the next turn starts clean
    provider.push("好")
    await agent.run("謝謝")
    assert agent.ctx.state["checklist"] == []


async def test_files_with_problems_are_sent_back_before_the_task_ends(home, provider):
    rt = Runtime(Config.load(), provider=provider)
    ws = rt.config.workspace
    bad = "# 報告\n\n![封面](cover.png)\n\n速度 $\\frac{a}{$ 與 \x0crac\n"
    good = "# 報告\n\n速度 $v_e$\n"
    provider.push("", [("write_file", {"path": "report.md", "content": bad})])
    provider.push("報告寫好了")
    provider.push("", [("write_file", {"path": "report.md", "content": good})])
    provider.push("修好了")
    agent = Agent(rt)
    out = await agent.run("寫報告")
    assert out == "修好了"
    fb = next(m.text() for m in agent.history if m.text().startswith("[自動成果檢查]"))
    assert "report.md" in fb and "找不到檔案" in fb
    assert (ws / "report.md").read_text() == good


def test_qa_checks(tmp_path):
    (tmp_path / "data.json").write_text("{bad")
    (tmp_path / "t.csv").write_text("a,b\n1,2\n3\n")
    (tmp_path / "empty.md").write_text("")
    (tmp_path / "ok.md").write_text("# ok\n\n$v_e$ 與 `$x$`\n")
    (tmp_path / "bad.md").write_text("![x](nope.png) [y](missing.md) [w](https://a.b)\n\n$\\frac{a}{$\n\n```mermaid\nflowchart LR\n  A --> \n  ((\n```\n")
    (tmp_path / "page.html").write_text("<html><body><p>$\\frac{1}{$</p><img src='gone.png'><script src='app.js'></script>"
                                        "<script>undefined_fn()</script></body></html>")
    (tmp_path / "fine.html").write_text("<html><body><h1>ok</h1><p>速度 $v_e$</p></body></html>")
    issues = asyncio.run(qa.check([tmp_path / n for n in ("data.json", "t.csv", "empty.md", "ok.md", "bad.md",
                                                          "page.html", "fine.html")]))
    by = {}
    for i in issues:
        by.setdefault(i["name"], []).append(i["problem"])
    assert "JSON 無法解析" in by["data.json"] and any("欄位數" in p for p in by["t.csv"]) and by["empty.md"] == ["檔案是空的"]
    assert "ok.md" not in by and "fine.html" not in by
    assert any("2 個相對路徑找不到檔案" in p for p in by["bad.md"])
    if any("公式" in p for p in by.get("bad.md", [])):                 # browser checks ran (Chromium present)
        assert any("Mermaid" in p for p in by["bad.md"])
        assert any("JavaScript 錯誤" in p for p in by["page.html"])
        assert any("本機檔案載入失敗" in p for p in by["page.html"])
        assert any("公式無法排版" in p for p in by["page.html"])
    assert qa.report(issues).startswith("- ")
