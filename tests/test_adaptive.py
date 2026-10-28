import json

from sinobee.agent import Agent, Runtime
from sinobee.agent.context import compress
from sinobee.learning import learn_from_session
from sinobee.types import Message, TextPart

SKILL = ("---\nname: weekly-report\ndescription: Builds the weekly sales report from the CRM export. Use when the "
         "user asks for the weekly report.\nmetadata:\n  version: \"1.0.0\"\n---\n# Procedure\n1. export\n2. build\n")


async def test_explicit_activation_and_enum(config, provider):
    provider.push("done")
    rt = Runtime(config, provider=provider)
    agent = Agent(rt)
    await agent.run("/web-research 查一下今天的匯率")
    msgs, tools = provider.requests[0]
    first_user = msgs[1]
    assert isinstance(first_user.content, list) and first_user.content[0].text.startswith('<skill_content name="web-research"')
    assert "web-research" in agent.ctx.state["skills_used"]
    view = next(t for t in tools if t.name == "skill_view")
    assert "web-research" in view.parameters["properties"]["name"]["enum"]
    assert rt.memory.sessions()[0]["title"].startswith("查一下")


async def test_learn_command(config, provider):
    provider.push("", [("skill_create", {"name": "weekly-report", "content": SKILL})])
    provider.push("建立了 weekly-report")
    rt = Runtime(config, provider=provider)
    agent = Agent(rt)
    await agent.run("/learn 每週一從 CRM 匯出並產生週報的流程")
    assert "[/learn]" in provider.requests[0][0][1].text()
    s = rt.skills.skills["weekly-report"]
    assert s.trust == "agent" and rt.skills.stats("weekly-report")["status"] == "candidate"
    await rt.drain()
    assert len(provider.requests) == 2  # /learn turns do not trigger an extra reflection


async def test_active_generation_with_repetition_and_suggest_mode(config, provider):
    config.data["skills"]["auto_create"] = "suggest"
    rt = Runtime(config, provider=provider)
    rt.reload = lambda: None
    rt.skills.write_approval = True
    for i in range(2):
        rt.memory.save(f"Built the weekly sales report from the CRM export (week {i})", kind="episode",
                       source=f"old{i}", importance=0.3)
    for i in range(4):
        provider.push("", [("shell", {"command": f"echo step{i}"})])
    provider.push("report ready")
    seen = {}

    def reflect_reply(messages):
        seen["prompt"] = messages[1].text()
        return Message("assistant", json.dumps({"skill": {"action": "create", "name": "weekly-report",
                                                          "content": SKILL, "reason": "repetition"}}))
    provider.script.append(reflect_reply)
    agent = Agent(rt)
    await agent.run("build the weekly sales report from the CRM export")
    await rt.drain()
    assert "repetition=2" in seen["prompt"]
    assert "weekly-report" not in rt.skills.skills           # suggest mode: staged, not created
    pending = rt.memory.pending()
    assert pending and pending[0]["kind"] == "skill_create"
    rt.skills.apply_pending(rt.memory.pop_pending(pending[0]["id"]))
    assert rt.skills.skills["weekly-report"].trust == "agent"


async def test_failure_driven_fork_patch(config, provider):
    rt = Runtime(config, provider=provider)
    provider.push(json.dumps({"skill": {"action": "patch", "name": "web-research",
                                        "old_string": "# When to Use", "new_string": "# When To Use (patched)"}}))
    from sinobee.learning.reflector import reflect
    await reflect(rt, "s1", [Message("user", "research x")], {"correction": True}, [])
    assert "web-research-custom" in rt.skills.skills   # builtin is read-only -> forked then patched
    assert "patched" in rt.skills.skills["web-research-custom"].location.read_text()
    assert "patched" not in rt.skills.skills["web-research"].location.read_text()


async def test_learn_from_session(config, provider):
    rt = Runtime(config, provider=provider)
    rt.memory.ensure_session("s9")
    rt.memory.append_messages("s9", [Message("user", "build the weekly report"), Message("assistant", "done")])
    provider.push(json.dumps({"skill": {"action": "create", "name": "weekly-report", "content": SKILL}}))
    rep = await learn_from_session(rt, "s9")
    assert rep["skill"]["status"] == "created"
    assert "USER ASKED" in provider.requests[0][0][1].text()


async def test_compression_preserves_active_skills(config, provider):
    provider.push("summary of earlier work")
    skill_text = '<skill_content name="web-research">IMPORTANT STEPS</skill_content>'
    msgs = [Message("system", "sys"), Message("user", "start")]
    msgs.append(Message("assistant", "", []))
    msgs.append(Message("tool", skill_text, tool_call_id="x", name="skill_view"))
    msgs += [Message("user", "x" * 4000) for _ in range(20)]
    out, changed = await compress(msgs, provider, budget=1000, keep_recent=4)
    assert changed and "IMPORTANT STEPS" in out[1].text()


async def test_empty_reply_is_retried_then_reported(config, provider):
    from sinobee.types import ChatResponse, Usage
    rt = Runtime(config, provider=provider)
    provider.push("")                       # empty -> nudge
    provider.push("這是完整的解說")          # recovered
    agent = Agent(rt)
    out = await agent.run("explain the repo")
    assert out == "這是完整的解說"
    assert "previous reply was empty" in provider.requests[1][0][-1].text()
    assert all(m.text() or m.tool_calls for m in agent.history if m.role == "assistant")
    for _ in range(3):
        provider.push("")
    out = await agent.run("again")
    assert "空白回覆" in out


async def test_empty_replies_count_only_when_consecutive(config, provider):
    """The task fails after 3 empty replies in a row - not after 3 spread over a long task with real steps between."""
    rt = Runtime(config, provider=provider)
    for _ in range(3):
        provider.push("")
        provider.push("", [("skill_view", {"name": "web-research"})])
    provider.push("完成")
    out = await Agent(rt).run("explain the repo")
    assert "空白回覆" not in out and len(provider.requests) >= 7     # went on past the third empty reply


def test_main_content_and_github_regex():
    from sinobee.tools.web import GITHUB_RE, main_content
    html = ("<html><head><title>Repo</title></head><body><nav>" + "menu " * 500 + "</nav>"
            "<main><h1>Project X</h1><p>" + "Real content. " * 60 + "</p></main><footer>foot</footer></body></html>")
    text = main_content(html)
    assert "Project X" in text and "menu" not in text and "foot" not in text
    assert GITHUB_RE.match("https://github.com/CAFECA-IO/CO2Exchange").groups()[:2] == ("CAFECA-IO", "CO2Exchange")
    assert GITHUB_RE.match("https://github.com/a/b/blob/main/src/x.ts").groups() == ("a", "b", "blob", "main", "src/x.ts")


class _LocalThinker:
    """A local (Ollama) model whose hidden thinking fills the output limit: empty answers cut at the limit."""
    label = "ollama/gemma4:e4b"
    provider_name = "ollama"
    model = "gemma4:e4b"

    def __init__(self, empties: int):
        self.empties = empties
        self.calls = []
        self.recorder = None

    async def chat(self, messages, tools=None, max_tokens=0, **kw):
        from sinobee import local
        from sinobee.providers.base import REASONING_EFFORT
        from sinobee.types import ChatResponse, Usage
        self.calls.append({"max_tokens": max_tokens, "effort": REASONING_EFFORT.get(), "ctx": local.grown(self.label)})
        if len(self.calls) <= self.empties:
            return ChatResponse(Message("assistant", ""), Usage(5000, max_tokens, thinking_tokens=max_tokens), "length")
        return ChatResponse(Message("assistant", "完成"), Usage(5000, 10), "stop")

    async def embed(self, texts):
        raise RuntimeError("no embeddings")


def _local_setup(config, monkeypatch, grow_to):
    from sinobee import local
    config.data["local"]["num_ctx"] = 32768     # the prompt (system + tools) takes ~13k
    config.data["model"]["default"] = "ollama/gemma4:e4b"
    sizes = list(grow_to)

    async def larger(cfg, tag, current, need):
        return sizes.pop(0) if sizes else None
    monkeypatch.setattr(local, "larger_ctx", larger)


async def test_local_model_gets_room_before_its_thinking_is_turned_off(config, monkeypatch):
    """A local model costs nothing to wait for: the rest of the window first, then a larger window, and thinking
    is switched off only when no more room can be had - the task then goes on."""
    _local_setup(config, monkeypatch, [65536])
    prov = _LocalThinker(empties=3)
    out = await Agent(Runtime(config, provider=prov)).run("explain the repo")
    assert "空白回覆" not in out
    c = prov.calls
    assert c[0]["max_tokens"] == 8192 and c[0]["effort"] is None
    assert c[1]["max_tokens"] > 8192 * 1.25 and c[1]["effort"] is None and not c[1]["ctx"]    # rest of the window
    assert c[2]["ctx"] == 65536 and c[2]["max_tokens"] > c[1]["max_tokens"] and c[2]["effort"] is None  # larger one
    assert c[3]["effort"] == "none"                                                            # then thinking off


async def test_local_model_gives_up_only_when_nothing_is_left_to_try(config, monkeypatch):
    _local_setup(config, monkeypatch, [])
    prov = _LocalThinker(empties=99)
    out = await Agent(Runtime(config, provider=prov)).run("explain the repo")
    assert "空白回覆" in out and "已等待" in out
    assert [c["effort"] for c in prov.calls] == [None, None, "none", "none", "none"]


async def test_local_model_gives_up_after_the_time_limit(config, monkeypatch):
    _local_setup(config, monkeypatch, [65536])
    config.data["local"]["empty_minutes"] = 0
    prov = _LocalThinker(empties=99)
    out = await Agent(Runtime(config, provider=prov)).run("explain the repo")
    assert "已等待" in out and len(prov.calls) == 1


class _LocalRecorder(_LocalThinker):
    def __init__(self):
        super().__init__(empties=0)
        self.tool_names = []

    async def chat(self, messages, tools=None, max_tokens=0, **kw):
        self.tool_names.append({t.name for t in tools or []})
        return await super().chat(messages, tools, max_tokens=max_tokens, **kw)


async def test_a_local_window_too_small_for_the_prompt_is_enlarged_before_the_first_step(config, monkeypatch):
    """System prompt + tool schemas + a skill can pass a 16k window: grow it first instead of compacting every step
    (which summarised the skill's instructions away - the model then asked for the skill again and again)."""
    _local_setup(config, monkeypatch, [65536])
    config.data["local"]["num_ctx"] = 8192
    prov = _LocalRecorder()
    agent = Agent(Runtime(config, provider=prov))
    await agent.run("explain the repo")
    assert prov.calls[0]["ctx"] == 65536 and prov.calls[0]["max_tokens"] == 8192
    assert agent._grown_ctx == 65536


async def test_with_the_common_tools_only_a_16k_window_is_enough(config, monkeypatch):
    _local_setup(config, monkeypatch, [65536])
    config.data["local"]["num_ctx"] = 16384
    prov = _LocalRecorder()
    agent = Agent(Runtime(config, provider=prov))
    await agent.run("explain the repo")
    assert not prov.calls[0]["ctx"] and not getattr(agent, "_grown_ctx", None)


async def test_without_memory_for_a_larger_window_unused_tool_groups_are_left_out(config, monkeypatch):
    _local_setup(config, monkeypatch, [])
    config.data["local"]["num_ctx"] = 16384
    config.data["local"]["slim_tools"] = False      # every tool sent: the window cannot hold them
    config.data["permissions"]["toolsets"] += ["desktop", "database"]
    prov = _LocalRecorder()
    agent = Agent(Runtime(config, provider=prov))
    await agent.run("explain the repo")
    first = prov.tool_names[0]
    assert "read_file" in first and not {"mouse", "db_query"} & first
    assert "database" in agent._trimmed_toolsets


class _LocalScripted(_LocalRecorder):
    """A local model that follows a script of replies (text, or (name, args) tool calls)."""
    def __init__(self, script):
        super().__init__()
        self.script = list(script)

    async def chat(self, messages, tools=None, max_tokens=0, **kw):
        from sinobee.types import ChatResponse, ToolCall, Usage
        if tools is None:                         # side calls (plan, consult) are not part of the script
            return ChatResponse(Message("assistant", "{}"), Usage(10, 5), "stop")
        self.tool_names.append({t.name for t in tools})
        self.descriptions = {t.name: t.description for t in tools}
        step = self.script.pop(0) if self.script else "完成"
        if isinstance(step, tuple):
            return ChatResponse(Message("assistant", "", [ToolCall(f"c{len(self.script)}", step[0], step[1])]),
                                Usage(100, 10), "tool_calls")
        return ChatResponse(Message("assistant", step), Usage(100, 10), "stop")


async def test_a_local_model_gets_the_common_tools_and_asks_for_more(config):
    config.data["model"]["default"] = "ollama/gemma4:e4b"
    config.data["local"]["num_ctx"] = 65536
    config.data["permissions"]["toolsets"] += ["work"]
    prov = _LocalScripted([("more_tools", {"names": ["work"]}), "完成"])
    agent = Agent(Runtime(config, provider=prov))
    await agent.run("explain the repo")
    first, second = prov.tool_names[0], prov.tool_names[1]
    assert "read_file" in first and "more_tools" in first and "board_write" not in first
    assert {"board_write", "delegate"} <= second               # asked for: sent from the next step
    assert "board_write" not in agent.ctx.state["hidden_tools"]


async def test_a_hidden_tool_still_runs_when_called_by_name(config):
    config.data["model"]["default"] = "ollama/gemma4:e4b"
    config.data["local"]["num_ctx"] = 65536
    prov = _LocalScripted([("memory_stats", {}), "完成"])
    agent = Agent(Runtime(config, provider=prov))
    seen = []
    orig = agent._emit
    agent._emit = lambda ev, data: (seen.append((ev, data)), orig(ev, data))
    await agent.run("explain the repo")
    res = [d for e, d in seen if e == "tool_result" and d.get("name") == "memory_stats"]
    assert res and not res[0]["error"]
    assert "memory_stats" in prov.tool_names[1]                  # used: sent from then on


def test_cloud_models_get_every_tool(config, provider):
    agent = Agent(Runtime(config, provider=provider))
    names = {s.name for s in agent._tool_specs()}
    assert "more_tools" not in names and "memory_stats" in names


async def test_every_model_call_tells_the_ui_to_reload_todays_tokens(config, provider):
    """The token count at the bottom left follows a running task (usage_tick after each step), not only its end."""
    provider.push("", [("skill_view", {"name": "web-research"})])
    provider.push("完成")
    agent = Agent(Runtime(config, provider=provider))
    seen = []
    orig = agent._emit
    agent._emit = lambda ev, data: (seen.append(ev), orig(ev, data))
    await agent.run("explain the repo")
    first_tick, end = seen.index("usage_tick"), seen.index("turn_end") if "turn_end" in seen else len(seen)
    assert first_tick < seen.index("tool_call") < end


async def test_tasks_on_the_same_local_model_share_one_window(config, monkeypatch):
    """Two tasks at once on one model: the window one of them enlarged is the other's too (no Ollama reloads)."""
    from sinobee import local
    _local_setup(config, monkeypatch, [65536])
    config.data["local"]["num_ctx"] = 8192
    first = _LocalRecorder()
    await Agent(Runtime(config, provider=first)).run("explain the repo")
    assert first.calls[0]["ctx"] == 65536
    second = _LocalRecorder()                       # another conversation: starts with the shared window
    await Agent(Runtime(config, provider=second)).run("explain the repo")
    assert second.calls[0]["ctx"] == 65536 and local.num_ctx(config, model="ollama/gemma4:e4b") == 65536
    assert local.num_ctx(config) == 8192            # the setting itself is unchanged
    local.set_grown("ollama/gemma4:e4b", 32768)     # never shrinks
    assert local.grown("ollama/gemma4:e4b") == 65536
