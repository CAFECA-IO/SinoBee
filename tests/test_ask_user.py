"""ask_user: SinoBee asks for a decision with suggested options; the web UI answers over the websocket."""
import asyncio

from fastapi.testclient import TestClient

from sinobee.agent import Runtime
from sinobee.config import Config
from sinobee.server import create_app
from sinobee.tools import REGISTRY, ToolContext
from sinobee.types import ToolCall

OPTS = [{"label": "精簡版", "description": "只列重點", "recommended": True}, {"label": "完整版", "description": "附推導"},
        {"label": "x", "recommended": True}, {"label": "y"}, {"label": "z"}]


def run(coro):
    return asyncio.run(coro)


def test_the_tool_is_always_available_and_falls_back_when_nobody_can_answer(config):
    config.data["permissions"]["toolsets"] = ["files"]
    assert "ask_user" in {t.name for t in REGISTRY.available(config)}
    ctx = ToolContext(config=config)
    r = run(REGISTRY.dispatch(ToolCall("1", "ask_user", {"question": "要哪種？", "options": OPTS}), ctx))
    assert "unattended" in r.content and "精簡版" in r.content
    seen = {}

    async def ask(q, options, multi, allow_text):
        seen.update(q=q, options=options, multi=multi, allow_text=allow_text)
        return {"choices": ["完整版"], "text": "要附圖"}
    ctx.ask = ask
    r = run(REGISTRY.dispatch(ToolCall("2", "ask_user", {"question": "要哪種？", "options": OPTS}), ctx))
    assert r.content == "The user chose: 完整版. Their note: 要附圖"
    assert len(seen["options"]) == 4 and [o["recommended"] for o in seen["options"]] == [True, False, False, False]
    r = run(REGISTRY.dispatch(ToolCall("3", "ask_user", {"question": "?", "options": ["only one"]}), ctx))
    assert "ERROR" in r.content


def test_web_ui_answers_with_a_button_or_by_typing(home, provider):
    rt = Runtime(Config.load(), provider=provider)
    app = create_app(rt)
    q = {"question": "報告要哪種長度？", "options": OPTS[:2]}
    for answer, expect in ((lambda ws, rid: ws.send_json({"type": "answer", "request_id": rid, "choices": ["完整版", "不存在"], "text": ""}),
                            "The user chose: 完整版"),
                           (lambda ws, rid: ws.send_json({"type": "message", "session_id": sid, "text": "兩種都要"}),
                            "The user answered: 兩種都要")):
        provider.push("", [("ask_user", q)])
        provider.push("好的，照你的選擇做。")
        with TestClient(app).websocket_connect("/ws") as ws:
            ws.send_json({"type": "message", "text": "寫一份報告", "client_ref": "t"})
            rid = sid = None
            while True:
                ev = ws.receive_json()
                if ev["type"] == "session":
                    sid = ev["session_id"]
                if ev["type"] == "ask":
                    assert ev["question"] == q["question"] and ev["options"][0]["recommended"]
                    rid = ev["request_id"]
                    answer(ws, rid)
                if ev["type"] == "ask_resolved":
                    assert ev["request_id"] == rid
                if ev["type"] == "turn_end":
                    break
        tool_msgs = [m for m in provider.requests[-1][0] if m.role == "tool"]
        assert tool_msgs[-1].text() == expect


def test_plain_string_options_still_become_buttons():
    from sinobee.server.app import ask_options
    assert ask_options(["只這次", {"label": "記住", "recommended": True}, "", None, {"description": "x"}]) == \
        [{"label": "只這次"}, {"label": "記住", "recommended": True}]
