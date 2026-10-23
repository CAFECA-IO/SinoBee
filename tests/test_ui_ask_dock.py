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
