"""The per-task cap in the units a task is paid in (2026-10-22): a subscription task is measured by its share of the
6-hour window (not $0.0000), an own-key task by billed tokens (cached reads at their price share); the recommendation
at the cap follows the room left and recent progress; a health check with no advice left becomes a self-check."""

from sinobee.agent import progress as P
from sinobee.pricing import billed_tokens
from sinobee.providers import base
from sinobee.types import Usage

from conftest import ScriptedProvider
from test_budget import ctl, make, msg


def test_billed_tokens_weigh_cached_reads_at_their_price():
    u = Usage(100_000, 2_000, 90_000, 0)
    price = {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75}
    assert billed_tokens(price, u) == 10_000 + 90_000 * 0.1 + 2_000
    assert billed_tokens(None, u) == 10_000 + 9_000 + 2_000, "no price: the usual 0.1 for a cached read"
    assert billed_tokens(price, Usage(10_000, 0, 0, 8_000)) == 2_000 + 8_000 * 1.25


class Subscription(ScriptedProvider):
    """SinoBee.ai relay: every answer reports its share of the 6-hour window and the window's use."""
    label, provider_name, model = "sinobee/claude-x", "sinobee", "claude-x"

    def __init__(self, script, share=15.0, window=30.0):
        super().__init__(script)
        self.share, self.window = share, window

    async def chat(self, messages, tools=None, **kw):
        base._note_quota({"x-sinobee-cost-6h-pct": str(self.share), "x-sinobee-quota-6h": str(self.window),
                          "x-sinobee-quota-168h": "5"})
        return await super().chat(messages, tools, **kw)


def _files(n):
    return [msg("", [("write_file", {"path": f"p{i}.md", "content": f"# {i}"})]) for i in range(n)] + [msg("完成")]


async def test_a_subscription_task_is_capped_by_its_share_of_the_window(home, config):
    config.data["usage"]["task_max_quota_pct"] = 40
    rt, agent, events = make(config, Subscription(_files(6)), ask=None)
    await agent.run("寫六段")
    [cap] = [c for c in ctl(events, "budget") if c["trigger"] == "budget_cap"]
    assert "訂閱 6 小時額度的 45%" in cap["detail"] and "6 小時額度 40%（設定）" in cap["detail"]
    assert "$" not in cap["detail"] and "tokens" not in cap["detail"], "no $0.0000 for a subscription"
    assert agent.last_status["error"]["kind"] == "budget"


async def _at_cap(home, config, window, script):
    config.data["usage"]["task_max_quota_pct"] = 20
    asked = []

    async def ask(q, options, multi, allow_text):
        asked.append((q, {o["label"]: o.get("recommended") for o in options}))
        return {"choices": ["停止並交付"], "text": ""}
    make_ = make(config, Subscription(script, share=10.0, window=window), ask=ask)
    await make_[1].run("寫")
    return asked[0]


async def test_going_on_is_recommended_with_room_and_progress(home, config):
    q, rec = await _at_cap(home, config, 30.0, _files(6))
    assert rec == {"停止並交付": False, "追加 1.5 倍並繼續": True}
    assert "6 小時額度還剩 70%" in q and "仍有產出" in q


async def test_stopping_is_recommended_when_the_window_is_nearly_used(home, config):
    q, rec = await _at_cap(home, config, 90.0, _files(6))
    assert rec["停止並交付"] is True and rec["追加 1.5 倍並繼續"] is False and "還剩 10%" in q


async def test_stopping_is_recommended_when_nothing_was_produced(home, config):
    q, rec = await _at_cap(home, config, 30.0, [msg("", [("list_dir", {"path": f"d{i}"})]) for i in range(6)])
    assert rec["停止並交付"] is True and "沒有產出" in q


async def test_the_health_check_without_advice_left_is_a_self_check(home, config):
    config.data["agent"]["progress"] = {**P.DEFAULTS, "health_steps": 3}
    config.data["agent"].update(consult_max=0, consult_per_steps=0, failure_diagnosis=False)
    steps = [msg("", [("task_checklist", {"action": "add", "items": ["寫好報告"]})])]
    steps += [msg("", [("list_dir", {"path": f"d{i}"})]) for i in range(3)] + [msg("好了")]
    rt, agent, events = make(config, ScriptedProvider(steps))
    config.data["agent"].update(consult_max=0, consult_per_steps=0)
    await agent.run("做報告")
    checks = ctl(events, "self_check")
    assert checks and checks[0]["trigger"] == "health_steps" and checks[0]["decision"] == "self_check"
    assert not [c for c in ctl(events, "consult_skipped") if c["trigger"] == "health_steps"], "no idle record"
    note = next(m.text() for m in agent.history if m.text().startswith("[進度自我檢查]"))
    assert "寫好報告" in note


def test_the_consult_limit_grows_with_the_steps(home, config):
    rt, agent, _ = make(config, ScriptedProvider())
    config.data["agent"]["consult_max"] = 2
    assert agent._consult_max({"step": 10}) == 2
    assert agent._consult_max({"step": 85}) == 4, "one more per 40 steps"
