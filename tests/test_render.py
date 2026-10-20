"""Evaluation report design R3 (2026-10-21): the judge looks at the screen. A trial's web page is photographed in a
browser that can reach only the trial's files; `page` questions are decided from what the browser saw, `screens`
rubrics are judged on the screenshots, and the same pictures go into the report."""
import asyncio
import json

import pytest

from sinobee.evals import graders as G
from sinobee.evals import render as R
from sinobee.types import ChatResponse, ImagePart, Message, Usage

pytest.importorskip("playwright")

ANIMATED = """<!doctype html><html><body><h1>風力發電機</h1><svg width="200" height="200"><rect id="b" x="10" y="10"
width="40" height="40" fill="teal"/></svg><button>播放</button><input type="range"><img src="pic.svg">
<img src="../secret.svg"><img src="https://example.com/x.png">
<script>let a=0;setInterval(()=>{a+=20;document.getElementById('b').setAttribute('x',10+(a%150))},100);
fetch('https://example.com/steal').catch(()=>{});</script></body></html>"""
STILL = """<!doctype html><html><body><div style="width:1200px">寬版頁面，沒有動畫的文字內容。這裡有足夠的字。</div>
<script>undefinedFunction()</script></body></html>"""


def _run(coro):
    return asyncio.run(coro)


def test_a_page_is_photographed_offline_and_its_facts_recorded(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "secret.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (ws / "pic.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'/>")
    (ws / "index.html").write_text(ANIMATED, encoding="utf-8")
    (ws / "still.html").write_text(STILL, encoding="utf-8")
    out = _run(R.capture(ws, [ws / "still.html", ws / "index.html"], tmp_path / "trial"))
    if out.get("skipped"):
        pytest.skip(out["skipped"])
    assert [s["id"] for s in out["shots"][:3]] == ["desktop-load", "desktop-later", "phone"]
    assert out["shots"][0]["page"] == "index.html"                       # index.html first
    assert all((tmp_path / "trial" / s["path"]).stat().st_size > 1000 for s in out["shots"])
    a, b = out["pages"]["index.html"], out["pages"]["still.html"]
    assert a["animates"] and not a["errors"] and a["controls"] >= 2 and not a["blank"]
    assert not b["animates"] and b["errors"] and b["phone_overflow"] > 100
    page = {"type": "page"}
    assert R.grade({**page, "check": "animates"}, out)[0]
    assert not R.grade({**page, "check": "animates", "path": "still.html"}, out)[0]
    assert not R.grade({**page, "check": "no_errors", "path": "still.html"}, out)[0]
    assert R.grade({**page, "check": "controls", "min": 2}, out)[0]
    assert not R.grade({**page, "check": "fits_phone", "path": "still.html"}, out)[0]
    assert not R.grade({**page, "check": "animates"}, {"skipped": "no browser"})[0]      # never passed unseen
    assert not R.grade({**page, "check": "animates"}, None)[0]


class Judge:
    """Records what it was shown; passes when it got pictures and names the second one."""
    def __init__(self):
        self.seen = []

    async def chat(self, messages, tools=None, **kw):
        m = messages[-1]
        parts = m.content if isinstance(m.content, list) else []
        self.seen.append(sum(isinstance(p, ImagePart) for p in parts))
        ok = self.seen[-1] > 0
        return ChatResponse(Message("assistant", json.dumps({"pass": ok, "reason": "葉片在兩張截圖位置不同",
                                                             "evidence": "screenshot 2: the square moved", "shot": 2})),
                            Usage(10, 10), "stop", "j")


def test_a_screens_rubric_is_judged_on_the_screenshots(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "index.html").write_text(ANIMATED, encoding="utf-8")
    render = _run(R.capture(ws, [ws / "index.html"], tmp_path / "trial"))
    if render.get("skipped"):
        pytest.skip(render["skipped"])
    j = Judge()
    t = G.Trial(prompt="用動畫展示", workspace=ws, files=[ws / "index.html"], judge=j, render=render,
                render_base=tmp_path / "trial")
    r = _run(G.grade_full({"type": "llm", "rubric": "截圖中的方塊會移動", "screens": True}, t))
    assert r["ok"] and r["screens"] == 3 and r["shot"] == 2 and j.seen == [3]
    r = _run(G.grade_full({"type": "llm", "rubric": "原始碼可讀"}, t))           # an ordinary rubric: code only
    assert not r["ok"] and j.seen[-1] == 0
    t.render, t.render_base = None, None
    r = _run(G.grade_full({"type": "llm", "rubric": "截圖中的方塊會移動", "screens": True}, t))
    assert not r["ok"] and "要看畫面" in r["detail"]


def test_page_questions_are_allowed_in_authored_suites():
    from sinobee.evals import sfs
    base = {"label": "x", "category": "格式"}
    assert not any("page" in p for p in sfs.lint({"graders": [{**base, "type": "page", "check": "animates"}]}))
    assert any("check" in p for p in sfs.lint({"graders": [{**base, "type": "page", "check": "dance"}]}))


def test_a_campaign_trial_keeps_its_screenshots_for_the_report(home, config, tmp_path, monkeypatch):
    from sinobee.agent import Runtime
    from sinobee.evals import campaign as C
    from sinobee.evals import publish as P
    from sinobee.evals import runner
    from conftest import ScriptedProvider
    monkeypatch.setattr(C, "remote_tested", lambda *a, **k: {})

    def child(rt, cfg):
        p = ScriptedProvider()
        p.push("", [("write_file", {"path": "index.html", "content": ANIMATED})])
        p.push("寫好了")
        return Runtime(cfg, provider=p)
    monkeypatch.setattr(runner, "child_runtime", child)
    f = tmp_path / "bench-page.json"
    f.write_text(json.dumps({"title": "動畫頁", "bench": {"kind": "skill", "skill": "anim", "threshold": 70,
                                                       "categories": {"畫面": 100}},
                             "tasks": [{"id": "t1", "prompt": "做一個會動的頁面", "toolsets": ["files"],
                                        "graders": [{"type": "page", "check": "animates", "label": "會動", "category": "畫面"},
                                                    {"type": "page", "check": "no_errors", "label": "沒有錯誤", "category": "畫面"}]}]}),
                 encoding="utf-8")
    rt = Runtime(config, provider=ScriptedProvider())
    m = "openai/gpt-4.1"
    c = C.new(rt, suite=str(f), combos=[{"thinking": m, "execution": m}], k=1, budget_usd=5)
    asyncio.run(C.execute(rt, c["id"]))
    v = C.trial_view(rt.config.home, c["id"], 0, 1)
    x = v["inputs"][0]
    if (x.get("render") or {}).get("skipped"):
        pytest.skip(x["render"]["skipped"])
    assert [g["ok"] for g in x["grades"]] == [True, True]
    assert len(x["shots"]) == 3 and all(len(s["sha256"]) == 64 for s in x["shots"])
    assert x["render"]["pages"]["index.html"]["animates"]
    body = P.payloads(C._docs(rt.config.home, C.get(rt.config.home, c["id"])), home=rt.config.home)[0]
    tr = body["tasks"][0]["trials"][0]
    assert len(tr["shots"]) == 3 and tr["render"]["pages"]["index.html"]["animates"] is True
