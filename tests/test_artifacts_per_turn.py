"""A later turn that changes a file again still reports it (2026-10-22: a "繼續" that fixed roadster-rocket/index.html
ended "完成" with no file, because the file had been shown two turns before)."""
import json

from sinobee.agent import Agent, Runtime
from sinobee.types import Message, ToolCall

from conftest import ScriptedProvider


def call(name, **a):
    return Message("assistant", "", [ToolCall(f"c{name}{len(json.dumps(a))}", name, a)])


async def test_each_turn_reports_the_files_it_changed(home, config):
    p = ScriptedProvider([call("write_file", path="game/index.html", content="<p>1</p>"), Message("assistant", "好了")])
    ends = []
    agent = Agent(Runtime(config, provider=p), session_id="arts", platform="web",
                  emit=lambda k, d: k == "turn_end" and ends.append(d))
    await agent.run("做遊戲")
    p.script = [call("edit_file", path="game/index.html", old_string="1", new_string="2"), Message("assistant", "修好了")]
    await agent.run("繼續")
    assert [[a["path"] for a in e["artifacts"]] for e in ends] == [["game/index.html"], ["game/index.html"]]

    from fastapi.testclient import TestClient
    from sinobee.server import create_app
    with TestClient(create_app(agent.rt)) as c:
        got = c.get("/api/sessions/arts/artifacts").json()
    assert [g["tool"] for g in got] == ["write_file", "edit_file"], "reopened: shown in both turns too"
