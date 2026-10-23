"""Sandbox plan V: evidence the system captures, playtest, the checklist's evidence rule, the evidence API.
Tests marked docker need Docker and the sinobee-sandbox image; the rest run anywhere."""
import asyncio
import base64
import io
import json
import os
import time
import types
import uuid
import zipfile

import pytest

from sinobee.agent import evidence, playtest
from sinobee.sandbox.manager import SandboxManager, manager_for
from sinobee.tools.registry import REGISTRY, ToolContext
from sinobee.types import ToolCall

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def dispatch(ctx, name, **args):
    return asyncio.run(REGISTRY.dispatch(ToolCall("1", name, args), ctx))


@pytest.fixture(autouse=True)
def sandbox_wanted(monkeypatch, request):
    if "docker" not in request.keywords:
        monkeypatch.setattr(SandboxManager, "unavailable", lambda self, max_age=60: None)


# ------------------------------------------------------------------ the store
def test_evidence_ids_turns_and_files(config):
    st = evidence.Store(config, "conv/../x")
    a = st.add("screenshot", "首頁", [(PNG, "png")], turn="t1", source="screenshot")
    b = st.add("frames", "移動", [(PNG, "jpg"), (PNG, "jpg")], turn="t2", source="playtest", expect="往右", ok=None)
    assert (a["id"], b["id"]) == ("E1", "E2") and b["files"] == ["E2-1.jpg", "E2-2.jpg"]
    assert "ok" not in b and b["expect"] == "往右"
    assert st.ids() == {"E1", "E2"} and [e["id"] for e in st.for_turn("t2")] == ["E2"]
    assert st.file("E1.png") and st.file("../manifest.json") is None and st.file("nope.png") is None
    st.update("E2", ok=False, problem="往左")
    assert st.get("E2")["ok"] is False
    assert "/" not in st.conversation and st.dir.parent == st.root


def test_old_and_oversized_evidence_is_pruned(config):
    old = evidence.Store(config, "old")
    old.add("screenshot", "x", [(PNG, "png")])
    past = time.time() - 40 * 86400
    for f in old.dir.iterdir():
        os.utime(f, (past, past))
    evidence.Store(config, "new").add("screenshot", "y", [(PNG, "png")])
    assert not old.dir.exists() and evidence.Store(config, "new").ids() == {"E1"}
    config.data["evidence"] = {"keep_days": 30, "max_mb": 0.000001}
    evidence.Store(config, "a").add("screenshot", "z", [(PNG, "png")])
    evidence.Store(config, "b").add("screenshot", "z", [(PNG, "png")])
    left = {d.name for d in (config.home / "evidence").iterdir()}
    assert "b" in left and "a" not in left          # the conversation being written to is always kept


def _sandbox_ctx(config, conv="c1", taken=False):
    config.data["permissions"]["approval"] = "sandbox"
    m = types.SimpleNamespace(takeover={conv: True} if taken else {})
    ex = types.SimpleNamespace(kind="sandbox", conversation=conv, manager=m)
    events = []
    ctx = ToolContext(config=config, session_id=conv, emit=lambda k, d: events.append((k, d)))
    ctx.state["executor"] = ex
    ctx.state["turn_id"] = "t9"
    return ctx, events


def test_nothing_is_captured_while_the_user_has_the_desktop(config):
    ctx, events = _sandbox_ctx(config, taken=True)
    assert evidence.capture(ctx, PNG, "x", "screenshot") is None
    ctx, events = _sandbox_ctx(config, conv="c2")
    e = evidence.capture(ctx, PNG, "x", "screenshot")
    assert e["id"] == "E1" and e["turn"] == "t9" and events[0][0] == "evidence"


# ------------------------------------------------------------------ playtest pieces
def test_playtest_steps_are_checked():
    assert playtest.validate([]) and playtest.validate([{"jump": 1}])
    assert "shot" in playtest.validate([{"click": [1, 2]}])
    assert playtest.validate([{"hold": "w", "seconds": 2}, {"frames": 4, "every": 0.5}]) is None
    assert playtest.duration([{"hold": "w", "seconds": 2}, {"frames": 4, "every": 0.5}, {"wait": 1}]) == 5
    assert "longer" in playtest.validate([{"wait": 200}, {"shot": "x"}])


class StubModel:
    def __init__(self, reply):
        self.reply, self.seen = reply, None

    async def chat(self, messages, tools, **kw):
        self.seen = messages
        text = self.reply(messages) if callable(self.reply) else self.reply
        return types.SimpleNamespace(message=types.SimpleNamespace(text=lambda: text))


def test_judge_reads_verdicts_in_order():
    shots = [{"kind": "shot", "label": "a", "expect": "紅色方塊", "ext": "png", "images": [base64.b64encode(PNG).decode()]},
             {"kind": "frames", "label": "b", "expect": "往右", "ext": "jpg",
              "images": [base64.b64encode(PNG).decode()] * 9}]
    m = StubModel('好的 {"items": [{"shot": 2, "ok": false, "problem": "往左"}, {"shot": 1, "ok": true}]}')
    v = asyncio.run(playtest.judge(m, "測試", "繁體中文", shots))
    assert v[0]["ok"] is True and v[1]["problem"] == "往左"
    images = [p for p in m.seen[0].content if type(p).__name__ == "ImagePart"]
    assert len(images) == 7                       # one shot + nine frames thinned to six
    assert playtest.parse("no json") == []


# ------------------------------------------------------------------ the checklist rule
def test_screen_items_need_a_captured_passing_picture(config):
    ctx, _ = _sandbox_ctx(config)
    dispatch(ctx, "task_checklist", action="set", items=[{"text": "畫面上看得到分數"}, {"text": "README 有安裝步驟"}])
    out = dispatch(ctx, "task_checklist", action="update", id=1, status="done", evidence="我看過了，分數正常顯示在右上角")
    assert "evidence id" in out.content
    out = dispatch(ctx, "task_checklist", action="update", id=1, status="done", evidence="E7 分數")
    assert "not a captured picture" in out.content
    store = evidence.store_of(ctx)
    bad = store.add("shot", "分數", [(PNG, "png")], ok=False, problem="看不到分數")
    out = dispatch(ctx, "task_checklist", action="update", id=1, status="done", evidence=f"{bad['id']}")
    assert "failed its check" in out.content
    good = store.add("shot", "分數", [(PNG, "png")], ok=True)
    out = dispatch(ctx, "task_checklist", action="update", id=1, status="done", evidence=f"{good['id']}：右上角 120 分")
    assert "ERROR" not in out.content
    readme = config.workspace / "README.md"
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text("install")
    out = dispatch(ctx, "task_checklist", action="update", id=2, status="done", evidence=str(readme))
    assert "ERROR" not in out.content


def test_outside_the_sandbox_the_rule_does_not_apply(config):
    config.data["permissions"]["approval"] = "ask"
    ctx = ToolContext(config=config, session_id="h1")
    dispatch(ctx, "task_checklist", action="set", items=[{"text": "畫面上看得到分數"}])
    out = dispatch(ctx, "task_checklist", action="update", id=1, status="done", evidence="screenshot.png 顯示分數")
    assert "evidence id" not in out.content


# ------------------------------------------------------------------ the quality gate
def _gate_self(config, ctx):
    from sinobee.agent.loop import Agent
    me = types.SimpleNamespace(depth=0, config=config, ctx=ctx, INTERACTIVE=Agent.INTERACTIVE, VIEWPORT=Agent.VIEWPORT, notes=[])
    me._event = me.notes.append
    return me, Agent._playtest_gate


def test_interactive_pages_must_be_played(config, tmp_path):
    ctx, _ = _sandbox_ctx(config)
    game, doc = tmp_path / "game.html", tmp_path / "doc.html"
    game.write_text("<canvas></canvas><script>addEventListener('keydown', e => 0)</script>")
    doc.write_text("<h1>hello</h1>")
    me, gate = _gate_self(config, ctx)
    stats = {"gate_rounds": {}}
    assert gate(me, [doc], stats) is None
    kind, msg = gate(me, [game, doc], stats)
    assert kind == "playtest" and "game.html" in msg
    stats["gate_rounds"]["playtest"] = 1
    assert gate(me, [game], stats) is None and stats["quality_skipped"]
    ctx.state["playtests"] = [{"shots": [{"id": "E3", "label": "往右", "ok": False, "problem": "方塊往左"}]}]
    kind, msg = gate(me, [game], {"gate_rounds": {"playtest": 1}})
    assert "E3" in msg and "方塊往左" in msg
    ctx.state["playtests"].append({"shots": [{"id": "E4", "label": "往右", "ok": True}]})
    assert gate(me, [game], {"gate_rounds": {"playtest": 2}}) is None
    config.data["agent"]["playtest"] = "off"
    ctx.state["playtests"] = []
    assert gate(me, [game], {"gate_rounds": {}}) is None


def test_phone_ready_pages_are_also_played_at_phone_width(config, tmp_path):
    ctx, _ = _sandbox_ctx(config)
    page = tmp_path / "app.html"
    page.write_text('<meta name="viewport" content="width=device-width, initial-scale=1"><button onclick="go()">go</button>')
    me, gate = _gate_self(config, ctx)
    ctx.state["playtests"] = [{"width": "desktop", "shots": [{"id": "E1", "label": "a", "ok": True}]}]
    kind, msg = gate(me, [page], {"gate_rounds": {}})
    assert kind == "playtest_mobile" and 'width="mobile"' in msg
    stats = {"gate_rounds": {"playtest_mobile": 1}}
    assert gate(me, [page], stats) is None and "手機" in stats["quality_skipped"][0]
    ctx.state["playtests"].append({"width": "mobile", "shots": [{"id": "E2", "label": "b", "ok": False, "problem": "按鈕被裁掉"}]})
    kind, msg = gate(me, [page], {"gate_rounds": {}})
    assert kind == "playtest" and "按鈕被裁掉" in msg           # the phone run failed: fix it
    ctx.state["playtests"].append({"width": "mobile", "shots": [{"id": "E3", "label": "b", "ok": True}]})
    assert gate(me, [page], {"gate_rounds": {}}) is None
    config.data["agent"]["playtest_mobile"] = "off"
    ctx.state["playtests"] = ctx.state["playtests"][:1]
    assert gate(me, [page], {"gate_rounds": {}}) is None


def test_unjudged_playtests_are_reported_as_not_checked(config, tmp_path):
    ctx, _ = _sandbox_ctx(config)
    page = tmp_path / "g.html"
    page.write_text("<canvas></canvas>")
    me, gate = _gate_self(config, ctx)
    ctx.state["playtests"] = [{"width": "desktop", "judge_skipped": "TimeoutError: ",
                               "shots": [{"id": "E1", "label": "a", "ok": None}]}]
    stats = {"gate_rounds": {}}
    assert gate(me, [page], stats) is None and gate(me, [page], stats) is None
    assert len(stats["quality_skipped"]) == 1 and "沒有經過視覺模型判定" in stats["quality_skipped"][0]


# ------------------------------------------------------------------ the evidence API
def test_evidence_can_be_listed_opened_and_downloaded(config):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sinobee.server import sandbox_api
    st = evidence.Store(config, "s1")
    st.add("screenshot", "a", [(PNG, "png")], turn="t1")
    st.add("frames", "b", [(PNG, "jpg"), (PNG, "jpg")], turn="t2")
    rtobj = types.SimpleNamespace(config=config)
    app = FastAPI()
    sandbox_api.register(app, lambda: rtobj)
    with TestClient(app) as c:
        assert [e["id"] for e in c.get("/api/evidence/s1").json()] == ["E1", "E2"]
        assert [e["id"] for e in c.get("/api/evidence/s1?turn=t2").json()] == ["E2"]
        r = c.get("/api/evidence/s1/E1.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert c.get("/api/evidence/s1/..%2Fx").status_code == 404
        z = zipfile.ZipFile(io.BytesIO(c.get("/api/evidence/s1.zip?turn=t2").content))
        assert sorted(z.namelist()) == ["E2-1.jpg", "E2-2.jpg", "manifest.json"]
        assert c.get("/api/evidence/zz.zip").status_code == 404


# ------------------------------------------------------------------ in Docker
GAME = """<!doctype html><body style="margin:0;background:#fff;overflow:hidden">
<div id=b style="position:absolute;left:600px;top:300px;width:80px;height:80px;background:#f00"></div>
<script>let x = 600, v = 0; const DIR = %d;
addEventListener('keydown', e => { if (e.key === 'ArrowRight') { v = DIR; e.preventDefault(); } });
addEventListener('keyup', e => { if (e.key === 'ArrowRight') v = 0; });
(function f(){ x = Math.max(80, Math.min(1100, x + v * 3)); b.style.left = x + 'px'; requestAnimationFrame(f); })();</script></body>"""


def red_x(b64):
    from PIL import Image
    im = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    im = im.resize((im.width // 4, im.height // 4))
    xs = [x for x in range(im.width) for y in range(im.height)
          if (lambda p: p[0] > 200 and p[1] < 80 and p[2] < 80)(im.getpixel((x, y)))]
    return sum(xs) / len(xs) if xs else None


def pixel_judge(messages):
    """A stand-in for the vision model: reads where the red square is in the first and last frame."""
    imgs = [p for p in messages[0].content if type(p).__name__ == "ImagePart"]
    first, last = red_x(imgs[0].data), red_x(imgs[-1].data)
    ok = first is not None and last is not None and last > first + 10
    return json.dumps({"items": [{"shot": 1, "ok": ok, "observed": f"x {first} -> {last}",
                                  "problem": "" if ok else "方塊沒有往右移動"}]})


@pytest.fixture
def box(config):
    config.data["permissions"]["approval"] = "sandbox"
    config.data["sandbox"]["memory"] = "1500m"
    config.workspace.mkdir(parents=True, exist_ok=True)
    config.workspace.chmod(0o777)
    conv = "test-" + uuid.uuid4().hex[:8]
    from sinobee.sandbox.workspaces import own_dir
    own_dir(config, conv).chmod(0o777)            # its own /workspace, writable by uid 1000 on Linux
    ctx = ToolContext(config=config, session_id=conv)
    ctx.state["runtime"] = types.SimpleNamespace(judge=lambda: StubModel(pixel_judge))
    ctx.state["turn_id"] = "t1"
    yield config, conv, ctx
    from sinobee.tools import browser
    asyncio.run(browser.shutdown())
    asyncio.run(manager_for(config).remove(conv))


STEPS = [{"click": [640, 600]}, {"keydown": "right"},
         {"frames": 5, "every": 0.2, "label": "按住右鍵", "expect": "紅色方塊往右移動"}, {"keyup": "right"}]


@pytest.mark.docker
@pytest.mark.parametrize("direction,passes", [(1, True), (-1, False)])
def test_playtest_catches_the_wrong_direction(box, direction, passes):
    config, conv, ctx = box
    from sinobee.sandbox.workspaces import dir_of
    (dir_of(config, conv) / "game.html").write_text(GAME % direction)
    out = dispatch(ctx, "playtest", steps=STEPS, goal="按右鍵方塊往右", url="file:///workspace/game.html").content
    text = out if isinstance(out, str) else out[0].text
    assert "ERROR" not in text[:10], text
    shots = ctx.state["playtests"][-1]["shots"]
    assert len(shots) == 1 and shots[0]["kind"] == "frames" and len(shots[0]["files"]) == 5
    assert shots[0]["ok"] is passes, text
    assert ("✓" if passes else "✕") in text and shots[0]["id"] in text
    if not passes:
        assert "方塊沒有往右移動" in text and any(type(p).__name__ == "ImagePart" for p in out)
    st = evidence.Store(config, conv)                 # kept on disk: there when the conversation is reopened
    assert st.get(shots[0]["id"])["ok"] is passes and all(st.file(f) for f in shots[0]["files"])


@pytest.mark.docker
def test_screenshots_are_evidence_but_not_while_taken_over(box):
    config, conv, ctx = box
    out = dispatch(ctx, "screenshot").content
    assert "Evidence E1" in out[0].text
    manager_for(config).set_takeover(conv, True)
    dispatch(ctx, "screenshot")
    manager_for(config).set_takeover(conv, False)
    assert evidence.Store(config, conv).ids() == {"E1"}


def test_the_sandbox_browser_shows_no_warning_bars():
    from sinobee.tools.browser import SANDBOX_CHROME
    assert "--test-type" in SANDBOX_CHROME and "--disable-infobars" in SANDBOX_CHROME   # ~115 px of page back
