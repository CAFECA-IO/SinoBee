import json

from sinobee.agent import Agent, Runtime
from sinobee.providers import build_provider, split_model
from sinobee.providers.anthropic import to_anthropic
from sinobee.providers.openai_compat import to_openai_messages
from sinobee.types import ImagePart, Message, TextPart, ToolCall


async def test_tool_loop_and_persistence(config, provider):
    provider.push("", [("shell", {"command": "echo hello-sinobee"})])
    provider.push("", [("write_file", {"path": "note.txt", "content": "hi"})])
    provider.push("done: hello-sinobee")
    rt = Runtime(config, provider=provider)
    events = []
    agent = Agent(rt, emit=lambda k, d: events.append(k))
    out = await agent.run("say hello")
    assert out == "done: hello-sinobee"
    tool_msgs = [m for m in agent.history if m.role == "tool"]
    assert "hello-sinobee" in tool_msgs[0].text()
    assert (config.workspace / "note.txt").read_text() == "hi"
    assert {"tool_call", "tool_result", "turn_end"} <= set(events)
    # context block attached to the user message, system prompt stable
    first_req = provider.requests[0][0]
    assert "<context>" in first_req[-1].text()
    assert "<context>" not in agent.history[0].text()
    # reload session from DB
    agent2 = Agent(rt, session_id=agent.session_id)
    assert len(agent2.history) == len(agent.history)
    await rt.drain()


async def test_approval_blocks_dangerous(config, provider):
    config.data["permissions"]["approval"] = "confirm"
    provider.push("", [("shell", {"command": "sudo rm -rf /"})])
    provider.push("ok, not doing that")
    rt = Runtime(config, provider=provider)
    asked = []

    async def approve(name, args, reason):
        asked.append(reason)
        return False

    agent = Agent(rt, approve=approve)
    await agent.run("wipe the disk")
    assert asked
    assert "did not approve" in [m for m in agent.history if m.role == "tool"][0].text()


async def test_readonly_mode(config, provider):
    config.data["permissions"]["approval"] = "readonly"
    provider.push("", [("write_file", {"path": "x.txt", "content": "x"})])
    provider.push("blocked")
    rt = Runtime(config, provider=provider)
    agent = Agent(rt)
    await agent.run("write")
    assert "readonly" in [m for m in agent.history if m.role == "tool"][0].text()


async def test_reflection_creates_memory_and_skill(config, provider):
    for i in range(4):
        provider.push("", [("shell", {"command": f"echo step{i}"})])
    provider.push("finished")
    skill_md = ("---\\nname: echo-steps\\ndescription: Run numbered echo steps\\nversion: 1.0.0\\n---\\n"
                "# When to Use\\nx\\n# Procedure\\n1. echo\\n# Pitfalls\\n- none\\n# Verification\\nok")
    provider.push(json.dumps({
        "memories": [{"content": "使用者偏好以繁體中文回覆", "kind": "preference", "tier": "core", "importance": 0.9}],
        "skill": {"action": "create", "name": "echo-steps", "content": skill_md.replace("\\n", "\n")},
        "episode_summary": "Ran four echo steps.",
    }))
    rt = Runtime(config, provider=provider)
    agent = Agent(rt)
    await agent.run("run the steps")
    await rt.drain()
    assert any("繁體中文" in m.content for m in rt.memory.core())
    assert "echo-steps" in rt.skills.skills
    assert rt.memory.session_search("echo steps")
    # next session sees the new core memory in its system prompt
    agent2 = Agent(rt)
    assert "繁體中文" in agent2.system.text()


async def test_recall_injected(config, provider):
    rt = Runtime(config, provider=provider)
    rt.memory.save("The staging server is at 10.0.0.5 and uses port 2222", kind="environment")
    provider.push("ok")
    agent = Agent(rt)
    await agent.run("ssh into the staging server")
    assert "10.0.0.5" in provider.requests[0][0][-1].text()


def test_openai_conversion_moves_tool_images():
    msgs = [Message("user", "look"),
            Message("assistant", "", [ToolCall("1", "screenshot", {})]),
            Message("tool", [TextPart("shot"), ImagePart("AAAA")], tool_call_id="1")]
    out = to_openai_messages(msgs)
    assert out[1]["tool_calls"][0]["function"]["name"] == "screenshot"
    assert out[2]["role"] == "tool" and out[2]["content"] == "shot"
    assert out[3]["role"] == "user" and out[3]["content"][1]["type"] == "image_url"


def test_anthropic_conversion():
    msgs = [Message("system", "sys"), Message("user", "hi"),
            Message("assistant", "ok", [ToolCall("t1", "shell", {"command": "ls"})]),
            Message("tool", [TextPart("files"), ImagePart("AAAA")], tool_call_id="t1"),
            Message("tool", "ERROR: x", tool_call_id="t2")]
    system, out = to_anthropic(msgs)
    assert system[0]["text"] == "sys"
    assert out[1]["content"][1]["type"] == "tool_use"
    assert out[2]["role"] == "user" and len(out[2]["content"]) == 2
    assert out[2]["content"][0]["content"][1]["type"] == "image"
    assert out[2]["content"][1]["is_error"] is True


def test_provider_resolution(config, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    p = build_provider("openrouter/anthropic/claude-sonnet-4.5", config)
    assert p.model == "anthropic/claude-sonnet-4.5" and "openrouter" in p.base_url
    assert split_model("google/gemini-2.5-pro") == ("gemini", "gemini-2.5-pro")
    config.data["providers"]["mylab"] = {"base_url": "http://gpu:9000/v1"}
    assert build_provider("mylab/llama", config).base_url == "http://gpu:9000/v1"
