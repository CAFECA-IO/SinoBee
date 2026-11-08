"""Tool calls stay with their results: a milestone sealed after compaction cut between them and Anthropic refused
every later request ("tool_use ids were found without tool_result blocks immediately after")."""
import asyncio

from sinobee.agent import Agent, Runtime
from sinobee.agent import loop as loop_mod
from sinobee.agent.context import Compaction
from sinobee.agent.milestone_runner import safe_cut
from sinobee.providers.base import paired_tools
from sinobee.types import Message, ToolCall


def call(*ids):
    return Message("assistant", "", [ToolCall(i, "shell", {}) for i in ids])


def res(i):
    return Message("tool", f"result {i}", tool_call_id=i)


def test_a_call_without_its_result_gets_a_note_and_a_stray_result_is_dropped():
    hist = [Message("user", "go"), call("a", "b"), res("a"), Message("user", "[里程碑 m2 完成]"), res("x"), call("c"), res("c")]
    out = paired_tools(hist)
    assert [(m.role, m.tool_call_id) for m in out] == [("user", None), ("assistant", None), ("tool", "a"), ("tool", "b"),
                                                       ("user", None), ("assistant", None), ("tool", "c")]
    assert "removed from the context" in out[3].text()
    ok = [Message("user", "go"), call("a"), res("a")]
    assert paired_tools(ok) is ok                       # already paired: the same list


def test_a_milestone_is_never_sealed_between_a_call_and_its_results():
    hist = [Message("user", "go"), call("a", "b"), res("a"), res("b"), Message("assistant", "ok")]
    assert safe_cut(hist, 3) == 1 and safe_cut(hist, 2) == 1      # on a result: before the call
    assert safe_cut(hist, 4) == 4 and safe_cut(hist, 1) == 1      # whole pairs on both sides
    assert safe_cut(hist, 99) == 5


def test_compaction_moves_the_milestones_start_with_the_history(config, provider, monkeypatch):
    agent = Agent(Runtime(config, provider=provider))
    agent.history = [Message("user", f"old {i}") for i in range(10)] + [call("a"), res("a"), Message("assistant", "x"),
                                                                        call("b"), res("b")]

    class Msr:
        ms_start = 12                                    # the milestone began at "x"
    agent._msr = Msr()

    async def fake_compact(msgs, provider, **kw):        # summary + the last 3 messages kept
        tail = msgs[-3:]
        return Compaction([msgs[0], Message("user", "[summary]")] + tail, True, "s", tail_len=3)
    monkeypatch.setattr(loop_mod, "compact", fake_compact)
    asyncio.run(agent._compact(1000, [], "", 0))
    assert [m.text() or m.role for m in agent.history] == ["[summary]", "x", "assistant", "result b"]
    assert agent._msr.ms_start == 1                     # still at "x", not at the old index 12
