"""A question waiting for you is docked where you type (sketch B): full question, numbered options, your own words;
Esc folds it to one line; a reload brings it back; answering puts the composer back. Needs Playwright's Chromium."""
from test_ui_bubble import _msg, _page, _send, _serve, pw

OPTS = [{"label": "精簡版", "description": "只列重點", "recommended": True}, {"label": "完整版", "description": "附推導"},
        {"label": "兩種都要"}]
SCRIPT = [_msg("先確認格式。", [("ask_user", {"question": "報告要哪種長度？", "options": OPTS})]), _msg("好的，照你的選擇做。")]


def test_the_question_is_docked_and_answered_with_a_number_key(config):
    srv, url = _serve(config, SCRIPT)
    try:
        with pw.sync_playwright() as p:
            b, pg, errors = _page(p, url)
            _send(pg, "寫一份報告")
            pg.wait_for_selector("#askDock:not([hidden]) .ad-o button", timeout=20000)
            got = pg.evaluate("""() => ({q: document.querySelector('#askDock .ad-q').textContent,
              opts: [...document.querySelectorAll('#askDock .ad-o button b')].map(b => b.firstChild.textContent),
              rec: document.querySelector('#askDock .ad-o button.rec b').firstChild.textContent,
              form: getComputedStyle(document.querySelector('#form')).display, text: !!document.querySelector('#askDock textarea')})""")
            assert got["q"] == "報告要哪種長度？" and got["opts"] == ["精簡版", "完整版", "兩種都要"] and got["rec"] == "精簡版"
            assert got["form"] == "none" and got["text"], got
            pg.keyboard.press("Escape")                                   # folded: one line, the composer is back
            assert pg.evaluate("document.querySelector('#askDock').classList.contains('folded')")
            assert pg.evaluate("getComputedStyle(document.querySelector('#form')).display") != "none"
            pg.reload()                                                   # still waiting after a reload
            pg.wait_for_timeout(800)
            pg.evaluate("() => document.querySelectorAll('.modal.open').forEach(m => m.classList.remove('open'))")
            pg.locator("#side .item[data-sid]").first.click()
            pg.wait_for_selector("#askDock:not([hidden]):not(.folded) .ad-o button", timeout=10000)
            pg.evaluate("() => document.activeElement.blur()")
            pg.keyboard.press("2")
            pg.wait_for_selector("#askDock[hidden]", state="attached", timeout=20000)
            pg.wait_for_selector(".askcard.done .ask-o button.on", timeout=10000)
            assert pg.evaluate("document.querySelector('.askcard.done .ask-o button.on').dataset.l") == "完整版"
            assert pg.evaluate("getComputedStyle(document.querySelector('#form')).display") != "none"
            assert not errors, errors
            b.close()
    finally:
        srv.should_exit = True


def test_a_new_conversation_can_be_given_a_folder_before_its_first_message(config, tmp_path, monkeypatch):
    from sinobee.sandbox import folders as fd
    from sinobee.sandbox.manager import SandboxManager
    monkeypatch.setattr(SandboxManager, "unavailable", lambda self, max_age=60: None)
    config.data["permissions"]["approval"] = "sandbox"
    folder = tmp_path / "報告"
    folder.mkdir()
    (folder / "a.md").write_text("x")
    srv, url = _serve(config, [_msg("好的。")])
    try:
        with pw.sync_playwright() as p:
            b, pg, errors = _page(p, url)
            pg.wait_for_selector("#folderBtn:not([hidden])", timeout=10000)
            pg.click("#folderBtn")
            pg.fill("#fpPath", str(folder))
            pg.press("#fpPath", "Enter")                       # added at once: no 加入 button
            pg.wait_for_selector("#folderPop .fp-r", timeout=10000)
            draft = pg.evaluate("DRAFT_SID")
            assert draft and [f["name"] for f in fd.folders_for(config, draft)] == ["報告"]
            pg.keyboard.press("Escape")
            _send(pg, "看一下報告資料夾")
            pg.wait_for_function("sessionId !== null", timeout=20000)
            assert pg.evaluate("sessionId") == draft and pg.evaluate("DRAFT_SID") is None
            assert not errors, errors
            b.close()
    finally:
        srv.should_exit = True
