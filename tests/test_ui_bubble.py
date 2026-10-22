"""Proposal R in the real page: a large task is one bubble (result and key files on top, the process folded under
them) and reopens the same; a small task keeps the simple presentation. Needs Playwright's Chromium."""
import asyncio
import socket
import threading
import time

import pytest

from sinobee.types import ChatResponse, Message, ToolCall, Usage

pw = pytest.importorskip("playwright.sync_api")


def _msg(text="", calls=None, k=[0]):
    k[0] += 1
    return Message("assistant", text, [ToolCall(f"u{k[0]}_{i}", n, a) for i, (n, a) in enumerate(calls or [])])


LARGE = [
    _msg("問題：遊戲頁要放哪些元件？\n先建立頁面骨架。", [("write_file", {"path": "game/index.html", "content": "<html><body><h1>Roadster</h1></body></html>"})]),
    _msg("", [("task_checklist", {"action": "add", "items": ["頁面能開啟"]})]),
    _msg("答：標題、畫布與拉柄。\n問題：語法正確嗎？\n檢查語法。", [("shell", {"command": "echo SYNTAX_OK"})]),
    _msg("", [("shell", {"command": "ls game"})]),
    _msg("答：語法正確。\n寫測試。", [("write_file", {"path": "game/test/run.md", "content": "# t"})]),
    _msg("", [("shell", {"command": "echo ok"})]),
    _msg("", [("task_checklist", {"action": "update", "id": 1, "status": "done", "evidence": "game/index.html 開啟正常"})]),
    _msg("做好遊戲頁 game/index.html。\n\n實測：語法檢查通過。"),
]
SMALL = [_msg("", [("list_dir", {"path": "."})]), _msg("資料夾是空的。")]


class Slow:
    label, provider_name, model, recorder = "test/slow", "openai", "gpt-4.1", None

    def __init__(self, script):
        self.script = list(script)

    async def chat(self, messages, tools=None, **kw):
        await asyncio.sleep(0.3)
        r = ChatResponse(self.script.pop(0) if self.script else Message("assistant", "{}"), Usage(100, 10))
        if self.recorder:
            self.recorder(self, r.usage, None)
        return r

    async def embed(self, texts):
        raise RuntimeError("no embeddings")


def _serve(config, script):
    import uvicorn
    from sinobee.agent import Runtime
    from sinobee.server import create_app
    config.data["model"]["default"] = "openai/gpt-4.1"
    config.secrets.set("openai", "sk-test-000000000000000000000000")
    config.data["agent"].update(quality="fast", failure_diagnosis=False)
    rt = Runtime(config, provider=Slow(script), thinking_provider=Slow([]), aux_provider=Slow([]))
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(create_app(rt), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(50):
        if srv.started:
            break
        time.sleep(0.1)
    return srv, f"http://127.0.0.1:{port}/"


def _page(p, url):
    try:
        b = p.chromium.launch()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no Chromium: {e}")
    pg = b.new_page(viewport={"width": 1100, "height": 900})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(url)
    pg.wait_for_timeout(800)
    pg.evaluate("() => { document.querySelectorAll('.modal.open').forEach(m => m.classList.remove('open')); }")
    return b, pg, errors


def _send(pg, text):
    pg.fill("#input", text)
    pg.keyboard.press("Enter")


def test_a_large_task_is_one_bubble_and_reopens_the_same(config):
    srv, url = _serve(config, LARGE)
    try:
        with pw.sync_playwright() as p:
            b, pg, errors = _page(p, url)
            _send(pg, "做一個遊戲頁")
            pg.wait_for_selector(".tbub.full", timeout=20000)
            pg.wait_for_selector(".tbub.full .tb-h .result", timeout=30000)
            got = pg.evaluate("""() => { const t = document.querySelector('.tbub.full'); return {
              bubbles: document.querySelectorAll('#chat > .tbub').length, outside: document.querySelectorAll('#chat > :not(.tbub):not(.msg.user)').length,
              folded: t.classList.contains('folded'), items: t.querySelectorAll('.tbi').length,
              key: [...t.querySelectorAll('.tb-files > .arts .art')].map(a => a.textContent),
              answer: t.querySelector('.tb-ans').textContent, q: t.querySelector('.tbi.think .tb-q').textContent }; }""")
            assert got["bubbles"] == 1 and got["outside"] == 0 and got["folded"], got
            assert len(got["key"]) == 1 and "index.html" in got["key"][0], got
            assert "做好遊戲頁" in got["answer"] and got["q"] == "遊戲頁要放哪些元件？"
            pg.reload()
            pg.wait_for_timeout(800)
            pg.evaluate("() => document.querySelectorAll('.modal.open').forEach(m => m.classList.remove('open'))")
            pg.locator("#side .item[data-sid]").first.click()
            pg.wait_for_selector(".tbub.full", timeout=10000)
            again = pg.evaluate("""() => { const t = document.querySelector('.tbub.full'); return {
              items: t.querySelectorAll('.tbi').length, folded: t.classList.contains('folded'),
              answer: t.querySelector('.tb-ans').textContent, result: !!t.querySelector('.tb-h .result')}; }""")
            assert again["items"] == got["items"] and again["folded"] and again["result"], again
            assert "做好遊戲頁" in again["answer"]
            assert not errors, errors
            b.close()
    finally:
        srv.should_exit = True


def test_a_small_task_keeps_the_simple_presentation(config):
    srv, url = _serve(config, SMALL)
    try:
        with pw.sync_playwright() as p:
            b, pg, errors = _page(p, url)
            _send(pg, "看看資料夾")
            pg.wait_for_selector("#chat .result", timeout=20000)
            got = pg.evaluate("""() => ({compact: !!document.querySelector('.tbub.compact'), full: !!document.querySelector('.tbub.full'),
              proc: !!document.querySelector('.tbub .proc'), answer: document.querySelector('.msg.assistant')?.textContent})""")
            assert got["compact"] and not got["full"] and got["proc"] and "資料夾是空的" in got["answer"], got
            assert not errors, errors
            b.close()
    finally:
        srv.should_exit = True
