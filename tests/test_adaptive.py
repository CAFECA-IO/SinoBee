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


def test_main_content_and_github_regex():
    from sinobee.tools.web import GITHUB_RE, main_content
    html = ("<html><head><title>Repo</title></head><body><nav>" + "menu " * 500 + "</nav>"
            "<main><h1>Project X</h1><p>" + "Real content. " * 60 + "</p></main><footer>foot</footer></body></html>")
    text = main_content(html)
    assert "Project X" in text and "menu" not in text and "foot" not in text
    assert GITHUB_RE.match("https://github.com/CAFECA-IO/CO2Exchange").groups()[:2] == ("CAFECA-IO", "CO2Exchange")
    assert GITHUB_RE.match("https://github.com/a/b/blob/main/src/x.ts").groups() == ("a", "b", "blob", "main", "src/x.ts")
