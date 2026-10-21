"""Plan "模型分配與備援機制" P2 (2026-10-21): when the SinoBee.ai subscription allowance runs out, nothing else is
charged without consent - subscription -> points (asked, or billing.auto_points) -> your own keys and local models
(asked again, or billing.on_exhausted = fallback); back on the subscription as soon as the allowance is."""
import asyncio
import time

import pytest

from sinobee import billing
from sinobee.accounting import scope
from sinobee.agent.errors import explain_error
from sinobee.providers.base import FallbackChain, QuotaExceeded
from sinobee.types import ChatResponse, Message


class Relay:
    label = "sinobee/anthropic/claude-sonnet-5-5"
    provider_name, model = "sinobee", "anthropic/claude-sonnet-5-5"

    def __init__(self, left=False, points_ok=True):
        self.left, self.points_ok, self.calls = left, points_ok, []

    async def chat(self, messages, tools=None, **kw):
        paying = billing.PAYING.get()
        self.calls.append(paying)
        if self.left or (paying == "points" and self.points_ok):
            return ChatResponse(Message("assistant", f"relay:{paying or 'subscription'}"))
        raise QuotaExceeded(f"{self.label} SinoBee.ai quota 6h: limit", "6h", time.time() + 3600)


class Own:
    label = "anthropic/claude-sonnet-5-5"
    provider_name, model = "anthropic", "claude-sonnet-5-5"

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None, **kw):
        self.calls += 1
        return ChatResponse(Message("assistant", "own"))


def ask(chain):
    with scope("s", "agent") as sc:
        try:
            r = asyncio.run(chain.chat([Message("user", "hi")]))
        except QuotaExceeded as e:
            return e, sc
        return r, sc


def test_used_up_stops_and_asks_before_points_or_your_keys(config):
    relay, own = Relay(), Own()
    config.data["billing"].update(points=None)
    billing.note_quota(config, {"status": "ok", "points": 1240, "windows": [{"window": "6h", "usedPct": 100}]})
    out, sc = ask(FallbackChain([relay, own], config))
    assert isinstance(out, QuotaExceeded) and own.calls == 0 and relay.calls == [None]
    [ev] = sc.drain_quota_events()
    assert ev["ask"] is True and ev["source"] is None
    info = explain_error(out)
    assert info["quota"]["window"] == "6h" and "fallback_offer" not in info


def test_points_chosen_then_used_up_asks_again(config):
    relay, own = Relay(points_ok=False), Own()
    billing.choose(config, billing.POINTS, window="6h", retry_at=time.time() + 3600)
    out, sc = ask(FallbackChain([relay, own], config))
    assert relay.calls == ["points"] and own.calls == 0              # paid with points: the relay is told so
    assert isinstance(out, QuotaExceeded) and out.window == "points"
    assert "平台點數用完" in explain_error(out)["summary"]
    assert billing.active(config) is None                              # back to "subscription" until asked
    assert sc.drain_quota_events()[0]["ask"] is True


def test_points_pay_while_chosen(config):
    relay, own = Relay(), Own()
    billing.choose(config, billing.POINTS, window="6h", retry_at=time.time() + 3600)
    r, sc = ask(FallbackChain([relay, own], config))
    assert r.message.text() == "relay:points" and sc.billing == {"points": [relay.label]}


def test_auto_points_and_remembered_fallback(config):
    relay, own = Relay(points_ok=True), Own()
    config.data["billing"]["auto_points"] = True
    billing.note_quota(config, {"status": "ok", "points": 50, "windows": []})
    r, sc = ask(FallbackChain([relay, own], config))
    assert r.message.text() == "relay:points" and relay.calls == [None, "points"]
    assert sc.drain_quota_events()[0]["source"] == "points"
    billing.clear(config)
    config.data["billing"].update(auto_points=False, on_exhausted="fallback")
    relay2 = Relay()
    r, sc = ask(FallbackChain([relay2, own], config))
    assert r.message.text() == "own" and sc.billing == {"own": [own.label]}
    assert billing.active(config)["source"] == "own"


def test_straight_back_to_the_subscription_when_the_allowance_is(config):
    relay, own = Relay(left=True), Own()
    billing.choose(config, billing.OWN, window="6h", retry_at=time.time() + 3600)
    r, _ = ask(FallbackChain([relay, own], config))
    assert r.message.text() == "own"
    billing.note_quota(config, {"status": "ok", "windows": [{"window": "6h", "usedPct": 40},
                                                             {"window": "168h", "usedPct": 70}]})
    r, _ = ask(FallbackChain([relay, own], config))
    assert r.message.text() == "relay:subscription"
    billing.choose(config, billing.OWN, window="6h", retry_at=time.time() - 5)    # a stale time: a short wait
    assert billing.active(config)["until"] > time.time()
    st = billing.state(config)
    billing._write(config, {**st, "until": time.time() - 1})                     # the time has come
    assert billing.active(config) is None


def test_no_relay_in_the_chain_is_not_affected(config):
    own = Own()
    billing.choose(config, billing.POINTS, window="6h")
    r, sc = ask(FallbackChain([own], config))
    assert r.message.text() == "own" and not sc.billing


@pytest.mark.parametrize("choice,key,value", [("points", "auto_points", True), ("own", "on_exhausted", "fallback"),
                                              ("pause", "on_exhausted", "pause")])
def test_do_this_next_time(config, choice, key, value):
    billing.remember(config, choice)
    assert config.get(f"billing.{key}") == value


def test_status_and_dialog_api(config):
    from fastapi.testclient import TestClient

    from sinobee.agent import Runtime
    from sinobee.server.app import create_app
    from conftest import ScriptedProvider
    rt = Runtime(config, provider=ScriptedProvider())
    c = TestClient(create_app(rt))
    tok = config.server_token()
    q = f"?token={tok}" if tok else ""
    s = c.get("/api/status" + q).json()
    assert s["version"] and s["mode"] == "own" and "today_own" in s and "billing" in s
    b = c.get("/api/billing" + q).json()
    assert b["source"] == "subscription" and b["on_exhausted"] == "ask" and b["auto_points"] is False
    r = c.post("/api/billing/choose" + q, json={"choice": "pause", "remember": True})
    assert r.status_code == 200 and config.get("billing.on_exhausted") == "pause"
    assert c.post("/api/billing/choose" + q, json={"choice": "nope"}).status_code == 400
    p = c.put("/api/settings" + q, json={"billing": {"auto_points": True, "on_exhausted": "ask"}})
    assert p.status_code == 200 and config.get("billing.auto_points") is True
    assert c.get("/api/settings" + q).json()["billing"] == {"on_exhausted": "ask", "auto_points": True}


async def test_a_scheduled_task_stopped_by_the_allowance_runs_again_when_it_is_back(config):
    """Unattended + ask (the default) = pause: the task is not left waiting for an answer; it runs again later."""
    from sinobee.agent import Runtime
    from sinobee.work.hub import get_hub
    from conftest import ScriptedProvider
    rt = Runtime(config, provider=ScriptedProvider())
    hub = get_hub(rt)
    job = hub.store.create_job(name="日報", kind="task", prompt="整理日報", schedule={"type": "daily", "time": "08:00"})
    run = hub.store.create_run(job_id=job["id"], name="日報", kind="task", trigger="schedule",
                               state={"quota_wait": {"at": time.time(), "window": "6h",
                                                     "retry_at": time.time() + 3600}})
    hub.store.update_run(run["id"], status="failed")
    started = []
    hub.run_job = lambda jid, trigger="manual": started.append((jid, trigger)) or {"id": "x"}
    await hub.recovery.check()
    assert not started                                                    # waits for the allowance
    st = hub.store.run(run["id"])["state"]
    st["quota_wait"]["retry_at"] = time.time() - 60
    hub.store.update_run(run["id"], state=st)
    await hub.recovery.check()
    assert started == [(job["id"], "retry")]
    assert hub.store.run(run["id"])["state"]["auto_resume"]["quota"] is True
    await hub.recovery.check()
    assert len(started) == 1                                              # once


async def test_the_task_run_records_that_it_waits_for_the_allowance(config):
    from sinobee.agent import Runtime
    from sinobee.work.hub import get_hub

    class Out:
        label, provider_name, model, recorder = "sinobee/x/y", "sinobee", "x/y", None

        async def chat(self, messages, tools=None, **kw):
            raise QuotaExceeded("sinobee/x/y SinoBee.ai quota 6h: limit", "6h", "2026-10-22T08:00:00Z")

        async def embed(self, texts):
            raise RuntimeError("no")
    rt = Runtime(config, provider=Out())
    hub = get_hub(rt)
    job = hub.store.create_job(name="日報", kind="task", prompt="整理日報", schedule={"type": "daily", "time": "08:00"})
    run = hub.store.create_run(job_id=job["id"], name="日報", kind="task", trigger="schedule")
    _, _, status = await hub._task(run, job)
    assert status == "failed"
    qw = hub.store.run(run["id"])["state"]["quota_wait"]
    assert qw["window"] == "6h" and qw["retry_at"] == "2026-10-22T08:00:00Z"


def test_points_balance_is_learned_with_or_without_a_subscription(config):
    billing.note_quota(config, {"status": "ok", "points": {"balance": 1240, "rate": 1, "unit": "TWD"}, "windows": []})
    assert billing.points(config) == 1240
    billing.choose(config, billing.POINTS, window="6h", retry_at=time.time() + 3600)
    billing.note_quota(config, {"status": "no_subscription", "points": {"balance": 1200}})
    assert billing.points(config) == 1200 and billing.active(config)["source"] == "points"   # still on points
