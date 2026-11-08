"""Old tool output is not re-sent in full on every step: task 354fec5ba9b1 re-sent ~33k tokens per step, 97 % of its
input, including 19 copies of the checklist and the full text of every write_file."""
from types import SimpleNamespace

from sinobee.agent import Agent, Runtime
from sinobee.agent.context import SLIM_CHECKLIST, slim_stale
from sinobee.tools import checklist_tools
from sinobee.tools.registry import ToolContext
from sinobee.types import Message, ToolCall


def step(i, name="shell", args=None, out="ok"):
    return [Message("assistant", "", [ToolCall(f"c{i}", name, args or {"command": "ls"})]),
            Message("tool", out, tool_call_id=f"c{i}", name=name)]


def build(n_old=6):
    hist = [Message("user", "go")]
    big = "x" * 9000
    hist += step(0, "write_file", {"path": "/workspace/a.html", "content": big})
    hist += step(1, "task_checklist", {"action": "update"}, "<task_checklist>\n1. [x] a\n</task_checklist>")
    for i in range(2, 2 + n_old):
        hist += step(i, out=f"line-{i} " + "y" * 6000)
    hist += step(99, out="recent " + "z" * 6000)
    return hist


def test_old_outputs_and_big_arguments_are_trimmed_and_recent_ones_kept():
    hist = build()
    saved, gone = slim_stale(hist, keep_steps=4, min_gain=1000)
    assert saved > 5000 and gone
    assert "already applied" in hist[1].tool_calls[0].arguments["content"]
    assert hist[1].tool_calls[0].arguments["path"] == "/workspace/a.html"
    assert hist[4].content == SLIM_CHECKLIST
    assert "removed" in hist[6].content and hist[6].content.startswith("line-2")
    assert hist[-1].content.startswith("recent") and len(hist[-1].content) > 6000     # last steps untouched
    assert [m.tool_call_id for m in hist if m.role == "tool"] == [c.id for m in hist for c in m.tool_calls]
    again, _ = slim_stale(hist, keep_steps=4, min_gain=1000)
    assert again == 0                                       # nothing left to trim: the cached prefix stays as is


def test_small_gains_wait_so_the_prompt_cache_is_not_broken_every_step():
    hist = build(n_old=0)
    before = [m.content for m in hist]
    saved, _ = slim_stale(hist, keep_steps=4, min_gain=10 ** 6)
    assert saved == 0 and [m.content for m in hist] == before


def test_a_checklist_update_replies_in_one_line_and_old_output_still_counts_as_evidence(config):
    ctx = ToolContext(config=config, session_id="s1")
    agent = SimpleNamespace(history=[], slimmed=["== 外部資源 ==\nscript/link/img 帶 http : 0"])
    ctx.state["agent"] = agent
    checklist_tools.task_checklist(ctx, "set", items=[{"text": "a"}, {"text": "b"}])
    out = checklist_tools.task_checklist(ctx, "update", id=1, status="done", evidence="script/link/img 帶 http : 0")
    assert out.startswith("OK: item 1 is now done") and "next: 2" in out and "<task_checklist>" not in out


def test_steps_without_closing_an_item_bring_a_focus_note(config, provider):
    config.data.setdefault("agent", {})["item_step_limit"] = 3
    agent = Agent(Runtime(config, provider=provider))
    items = [{"id": 1, "status": "doing", "text": "a"}, {"id": 2, "status": "todo", "text": "b"}]
    assert agent._stall_note(items, 1) == "" and agent._stall_note(items, 3) == ""
    assert "steps without closing" in agent._stall_note(items, 4)
    items[0]["status"] = "done"
    assert agent._stall_note(items, 5) == ""                # closing an item resets the count
    assert agent._stall_note(items, 1) == ""                # a new turn starts over
