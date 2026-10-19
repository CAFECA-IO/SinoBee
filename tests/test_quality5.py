"""The thorium run of 2026-10-19: a Traditional-Chinese request got an English report and page, and a page whose
only figure covered the whole screen passed every check because nobody looked at it. Deliverables must be in the
user's language, and every page, drawing and image is screenshotted and judged by a vision model before the task
may end."""
import json
from pathlib import Path

import pytest

from conftest import ScriptedProvider
from sinobee.agent import Agent, Runtime, language, visual
from sinobee.config import Config
from sinobee.types import ImagePart, Message, ToolCall

ZH = "解析釷基熔鹽反應爐 SMR 可行性方案設計與原理解說，寫成報告"
EN_REPORT = ("# Thorium MSR feasibility\n\n" + "The reactor uses a molten fluoride salt that carries the fuel and "
             "transfers heat to a secondary loop, operating near atmospheric pressure. " * 4)
ZH_REPORT = ("# 釷基熔鹽反應爐可行性\n\n" + "反應爐以熔融氟化物鹽同時承載燃料與帶走熱量，在接近常壓下運轉，"
             "釷 232 吸收中子後經鏷 233 衰變成鈾 233。" * 4)
HANS_REPORT = ("# 钍基熔盐反应堆可行性\n\n" + "反应堆以熔融氟化物盐同时承载燃料与带走热量，在接近常压下运转，"
               "这种设计对于材料与关键问题的处理来说很重要。" * 4)


def msg(text="", calls=None):
    return Message("assistant", text, [ToolCall(f"x{i}_{n}", n, a) for i, (n, a) in enumerate(calls or [])])


# ---------- language ----------

def test_language_of_requests_and_prose():
    assert language.wanted(ZH) == "zh-Hant"
    assert language.wanted("解析钍基熔盐反应堆的可行性，这个设计") == "zh-Hans"
    assert language.wanted("Explain thorium molten salt reactors") == "en"
    assert language.wanted(ZH + "，報告用英文寫") == "en"            # an explicit request wins
    assert language.wanted("https://www.youtube.com/watch?v=qt5p-_tE8d0") == ""
    assert language.detect(language.strip(ZH_REPORT))[0] == "zh-Hant"
    assert language.detect(language.strip(HANS_REPORT))[0] == "zh-Hans"


def test_a_report_in_another_language_is_caught(tmp_path):
    en, zh, hans = tmp_path / "report.md", tmp_path / "zh.md", tmp_path / "hans.md"
    en.write_text(EN_REPORT, encoding="utf-8")
    zh.write_text(ZH_REPORT, encoding="utf-8")
    hans.write_text(HANS_REPORT, encoding="utf-8")
    page = tmp_path / "page.html"
    page.write_text(f"<html><body><h1>Feasibility</h1><p>{EN_REPORT}</p><script>const label='中文中文中文';</script>"
                    "</body></html>", encoding="utf-8")
    issues = language.check(ZH, [en, zh, hans, page])
    names = {i["name"]: i["problem"] for i in issues}
    assert names["report.md"] == "內容是英文，但使用者用繁體中文"
    assert names["page.html"].startswith("內容是英文")
    assert names["hans.md"] == "內容是簡體中文，但使用者用繁體中文"
    assert "zh.md" not in names
    assert not language.check(ZH + "，報告用英文寫", [en])               # asked for English: fine


def test_code_terms_and_quotes_do_not_count_as_another_language(tmp_path):
    md = tmp_path / "r.md"
    md.write_text("# 報告\n\n燃料鹽為 FLiBe（LiF-BeF₂），結構材料 Hastelloy-N，見 https://en.wikipedia.org/wiki/MSRE 。\n\n"
                  "```python\nimport numpy as np\nprint('thermal efficiency', 0.45)\n" + "x = 1  # long english comment\n" * 30
                  + "```\n\n" + "設計以低壓運轉，主要風險在材料腐蝕與氚管理。" * 5, encoding="utf-8")
    assert language.check(ZH, [md]) == []


def test_the_final_answer_must_be_in_the_users_language():
    issues = language.check(ZH, [], EN_REPORT)
    assert issues and issues[0]["name"] == "最後的回答"
    assert language.check(ZH, [], "完成。報告在 reports/x/report.md") == []      # too short to judge


async def test_the_agent_must_rewrite_an_english_report(home, monkeypatch):
    main = ScriptedProvider([msg("", [("write_file", {"path": "report.md", "content": EN_REPORT})]), msg("完成"),
                             msg("", [("write_file", {"path": "report.md", "content": ZH_REPORT})]), msg("已改成中文")])
    cfg = Config.load()
    cfg.data["agent"]["plan"] = "off"
    agent = Agent(Runtime(cfg, provider=main))

    async def no_acceptance(*a, **k):
        return None
    monkeypatch.setattr(agent, "_acceptance", no_acceptance)
    out = await agent.run(ZH)
    fb = [m.text() for m in agent.history if m.role == "user" and m.text().startswith("[自動成果檢查]")]
    assert len(fb) == 1 and "report.md：內容是英文，但使用者用繁體中文" in fb[0]
    assert out == "已改成中文" and agent.last_status["status"] == "done"


# ---------- visual judgment ----------

PAGE = ("<html><body><h1>釷基熔鹽反應爐</h1><p>說明</p>"
        "<h2>圖 1：一次迴路與排放槽</h2>"
        '<svg viewBox="0 0 800 600" width="800" height="600"><rect x="40" y="40" width="300" height="200" fill="#c55"/>'
        '<text x="60" y="140">爐心</text></svg>'
        "<figure><canvas id=c width=600 height=300></canvas><figcaption>圖 2：功率對溫度</figcaption></figure>"
        "<script>const g=document.getElementById('c').getContext('2d');g.fillStyle='#36c';g.fillRect(10,10,500,200);"
        "</script></body></html>")


def _page(home) -> Path:
    cfg = Config.load()
    ws = Path(cfg.workspace)
    ws.mkdir(parents=True, exist_ok=True)
    p = ws / "page.html"
    p.write_text(PAGE, encoding="utf-8")
    return p


async def _shots(tmp_path):
    p = tmp_path / "page.html"
    p.write_text(PAGE, encoding="utf-8")
    png = tmp_path / "chart.png"
    png.write_bytes(_png())
    shots = await visual.collect([p, png])
    if not any(s["label"].startswith("page.html") for s in shots):
        pytest.skip("no browser for screenshots")
    return shots


def _png() -> bytes:
    import struct
    import zlib
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(4))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


async def test_every_page_drawing_and_image_is_screenshotted_with_its_caption(tmp_path):
    pytest.importorskip("playwright")
    shots = await _shots(tmp_path)
    labels = [s["label"] for s in shots]
    assert labels[0] == "page.html 全頁"
    figs = [s for s in shots if "圖" in s["label"] and s["label"].startswith("page.html")]
    assert [f["caption"] for f in figs] == ["圖 1：一次迴路與排放槽", "圖 2：功率對溫度"]
    assert all(s["image"][:2] == b"\xff\xd8" for s in shots if s["label"].startswith("page.html"))
    assert "chart.png" in labels


async def test_a_blocking_visual_verdict_goes_back_to_the_agent(home, tmp_path):
    pytest.importorskip("playwright")
    if not await _shots(tmp_path):
        return
    page = _page(home)

    def verdict(messages):
        parts = messages[0].content
        n = sum(isinstance(p, ImagePart) for p in parts)
        items = [{"shot": i, "ok": True, "severity": "", "problem": "", "fix": ""} for i in range(1, n + 1)]
        items[1] = {"shot": 2, "ok": False, "severity": "blocking", "problem": "排放槽沒有畫出來，與圖說不符",
                    "fix": "在爐心下方畫出凍結閥與排放槽"}
        items[0] = {"shot": 1, "ok": False, "severity": "minor", "problem": "標題稍小", "fix": ""}
        return Message("assistant", json.dumps({"items": items}, ensure_ascii=False))
    vision = ScriptedProvider([verdict])
    cfg = Config.load()
    agent = Agent(Runtime(cfg, provider=ScriptedProvider([]), reviewer_provider=vision))
    stats = {"artifacts": [{"path": str(page)}]}
    kind, text = await agent._quality_gate(stats, ZH, "完成")
    assert kind == "qa" and "視覺檢查：排放槽沒有畫出來" in text and "圖 1" in text
    assert "標題稍小" not in text                                  # minor problems do not block
    prompt = vision.requests[0][0][0].content
    assert "圖說／標題：「圖 1：一次迴路與排放槽」" in "".join(getattr(p, "text", "") for p in prompt)
    assert "The user's language is 繁體中文" in prompt[0].text
    # pictures judged acceptable are not sent again; the blocking one is
    vision.script.append(lambda m: Message("assistant", json.dumps(
        {"items": [{"shot": i, "ok": True} for i in range(1, 10)]})))
    assert await agent._quality_gate(stats, ZH, "完成") is None
    resent = sum(isinstance(p, ImagePart) for p in vision.requests[1][0][0].content)
    assert resent == 1


async def test_a_visual_judgment_that_cannot_run_is_recorded_as_skipped(home, tmp_path):
    pytest.importorskip("playwright")
    if not await _shots(tmp_path):
        return
    page = _page(home)
    import asyncio as _a

    class Stalled(ScriptedProvider):
        label = "gemini/gemini-pro-latest"

        async def chat(self, messages, tools=None, **kw):
            await _a.sleep(5)
            return await super().chat(messages, tools, **kw)
    cfg = Config.load()
    cfg.data["agent"]["model_deadline"] = 0.3
    main = ScriptedProvider([msg("not json")])
    agent = Agent(Runtime(cfg, provider=main, reviewer_provider=Stalled([])))
    stats = {"artifacts": [{"path": str(page)}]}
    assert await agent._quality_gate(stats, ZH, "完成") is None
    assert len(main.requests) == 0                 # no hand-over to another model: the judge's role is not reassigned
    assert stats["quality_skipped"] and stats["quality_skipped"][0].startswith("視覺檢查")
