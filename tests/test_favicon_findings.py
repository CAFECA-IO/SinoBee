"""Findings of the calc favicon task (2026-10-24): the sandbox browser failed under uvloop (the server's loop), a
file check was taken for a screen check, and a rejected checklist entry turned a finished task into
"最後的操作失敗"."""
import asyncio

import pytest

from sinobee.agent import Agent, Runtime
from sinobee.sandbox.manager import SandboxManager
from sinobee.tools.checklist_tools import is_visual


def test_the_bridge_works_under_uvloop(config):
    uvloop = pytest.importorskip("uvloop")
    m = SandboxManager(config)

    async def go():
        a = await m.bridge("c1", 9222)
        b = await m.bridge("c1", 9222)
        return a, b
    a, b = uvloop.run(go())
    assert a == b and a > 0                    # (uvloop's Server takes no attributes: it used to raise here)
    m._close_bridges("c1")
    assert not m._bridges and not m._bridge_loops


@pytest.mark.parametrize("text,visual", [
    ("calc/resources/favicon.svg 存在，內容為 SVG 且與 iSunFA/public/favicon.svg 相同；cmp 顯示相同", False),
    ("ls shows the file report.pdf", False),
    ("the compressed archive is smaller than 2 MB", False),
    ("the page shows the score in the top bar", True),
    ("遊戲畫面顯示分數", True),
    ("按下開始後角色會移動", True),
    ("網頁上顯示登入按鈕", True),
    ("favicon 在瀏覽器分頁顯示", True),
])
def test_file_checks_are_not_screen_checks(text, visual):
    assert is_visual(text) is visual


async def test_a_rejected_checklist_entry_is_not_a_failed_operation(config, provider):
    provider.push("", [("task_checklist", {"action": "set", "items": [{"text": "note.txt 存在", "check": "ls"}]})])
    provider.push("", [("write_file", {"path": "note.txt", "content": "hi"})])
    provider.push("", [("task_checklist", {"action": "update", "id": 1, "status": "done",
                                            "evidence": "I checked it and it is fine"})])     # refused: not checkable
    provider.push("寫好了 note.txt。")
    ends = []
    agent = Agent(Runtime(config, provider=provider), emit=lambda k, d: ends.append(d) if k == "turn_end" else None)
    await agent.run("建立 note.txt")
    summary = ((ends[-1].get("error") or {}).get("summary") or "")
    assert "最後的操作失敗" not in summary
