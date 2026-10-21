"""Plan "模型分配與備援機制" P4/P5 (2026-10-21): a pinned model outranks the preference and SinoBee.ai's allocation in
every role and through fallback; sinobee/auto asks once per turn and role; anonymous outcomes leave the computer
only when the user turned the stats switch on."""
import asyncio
import time

from sinobee import billing, routing
from sinobee.accounting import scope
from sinobee.providers import build_auxiliary, build_chain
from sinobee.providers.base import FallbackChain, QuotaExceeded
from sinobee.types import ChatResponse, Message


def test_a_pinned_model_does_every_role_and_no_allocation_is_asked(config, monkeypatch):
    from sinobee.agent import Runtime
    asked = []
    monkeypatch.setattr(routing, "_post", lambda *a, **k: asked.append(a) or {"model": "x/y"})
    config.data["model"].update(default="sinobee/auto", pin="sinobee/anthropic/claude-opus-5-5",
                                thinking="sinobee/auto@thinking", reviewer="openai/gpt-5.4")
    assert build_chain(config).label == "sinobee/anthropic/claude-opus-5-5"
    assert build_chain(config, "sinobee/auto@thinking").label == "sinobee/anthropic/claude-opus-5-5"
    assert build_auxiliary(config).label == "sinobee/anthropic/claude-opus-5-5"
    rt = Runtime(config)
    assert rt.thinking() is None and rt.reviewer() is None
    assert rt.thinker() is rt.provider and rt.judge() is rt.provider
    assert not asked
    config.data["model"]["pin"] = None                 # unpinned: SinoBee.ai allocates every role
    config.data["model"].update(auxiliary="gemini/gemini-flash-lite-latest")   # per-role picks are not used in auto
    assert build_chain(config).label == "sinobee/auto"
    assert build_auxiliary(config).label == "sinobee/auto@summary"
    rt = Runtime(config)
    assert rt.thinking().label == "sinobee/auto@thinking"
    assert rt.reviewer().label == "sinobee/auto@review"


class Answer:
    def __init__(self, label):
        self.label, self.provider_name, self.model = label, label.split("/")[0], label.split("/", 1)[1]
        self.calls = 0

    async def chat(self, messages, tools=None, **kw):
        self.calls += 1
        return ChatResponse(Message("assistant", self.label))


def test_auto_asks_once_per_turn_and_role_and_records_the_pick(config, monkeypatch):
    from sinobee.providers import sinobee as S
    asked = []

    def post(cfg, path, body, timeout=8):
        asked.append(body)
        return {"model": "openai/gpt-5.6-luna", "reason": "品質與成本最平衡", "ttl": 600}
    monkeypatch.setattr(routing, "_post", post)
    served = []

    async def fake_chat(self, messages, tools=None, **kw):
        served.append(self.model)
        return ChatResponse(Message("assistant", "ok"))
    monkeypatch.setattr(S.SinoBeeProvider, "chat", fake_chat)
    config.data["model"].update(default="sinobee/auto", preference="cost")
    chain = build_chain(config)
    tok = routing.HINT.set({"task_kind": "page", "skill": "frontend-design", "turn": "t1"})
    try:
        with scope("s", "agent") as sc:
            for _ in range(3):
                r = asyncio.run(chain.chat([Message("user", "做一頁")], tools=None))
        assert r.model == "sinobee/openai/gpt-5.6-luna" and served == ["openai/gpt-5.6-luna"] * 3
        assert len(asked) == 1, "one allocation per turn"
        assert asked[0] == {"role": "main", "preference": "cost", "skill": "frontend-design", "task_kind": "page",
                            "needs": {"tools": False, "vision": False}}
        assert sc.route["main"]["model"] == "sinobee/openai/gpt-5.6-luna" and sc.as_dict()["route"]
    finally:
        routing.HINT.reset(tok)
    tok = routing.HINT.set({"task_kind": "page", "skill": "frontend-design", "turn": "t2"})
    try:
        asyncio.run(chain.chat([Message("user", "再一頁")]))
    finally:
        routing.HINT.reset(tok)
    assert len(asked) == 2, "the next turn asks again"


class Quota:
    label, provider_name, model = "sinobee/anthropic/claude-sonnet-5-5", "sinobee", "anthropic/claude-sonnet-5-5"

    async def chat(self, messages, tools=None, **kw):
        raise QuotaExceeded("quota 6h", "6h", time.time() + 3600)


def test_the_pin_holds_through_fallback_and_the_pool_follows_the_preference(config, monkeypatch):
    a, b = Answer("gemini/gemini-flash-latest"), Answer("anthropic/claude-sonnet-5-5")
    billing.choose(config, billing.OWN, window="6h", retry_at=time.time() + 3600)
    config.data["model"]["pin"] = "sinobee/anthropic/claude-sonnet-5-5"
    monkeypatch.setattr(routing, "order_pool", lambda *a, **k: (_ for _ in ()).throw(AssertionError("not with a pin")))
    with scope("s", "agent"):
        r = asyncio.run(FallbackChain([Quota(), a, b], config).chat([Message("user", "hi")]))
    assert r.message.text() == "anthropic/claude-sonnet-5-5" and a.calls == 0      # the pinned model, on your key
    config.data["model"]["pin"] = None
    monkeypatch.setattr(routing, "order_pool", lambda cfg, labels, role="main": list(reversed(labels)))
    with scope("s", "agent"):
        r = asyncio.run(FallbackChain([Quota(), a, b], config).chat([Message("user", "hi")]))
    assert r.message.text() == "anthropic/claude-sonnet-5-5"                        # ranked first by preference
    monkeypatch.setattr(routing, "order_pool", lambda cfg, labels, role="main": labels)
    with scope("s", "agent"):
        r = asyncio.run(FallbackChain([Quota(), a, b], config).chat([Message("user", "hi")]))
    assert r.message.text() == "gemini/gemini-flash-latest"


def test_outcomes_are_sent_only_with_the_switch_on(config, monkeypatch):
    from sinobee import account
    from sinobee.agent import Agent, Runtime
    from conftest import ScriptedProvider
    sent = []
    monkeypatch.setattr(routing, "_post", lambda cfg, path, body, timeout=8: sent.append((path, body)) or {})
    monkeypatch.setattr(account, "can_relay", lambda cfg: True)
    p = ScriptedProvider()
    p.push("好了")
    asyncio.run(Agent(Runtime(config, provider=p), session_id="private-session").run("整理資料"))
    time.sleep(0.3)
    assert sent == [], "off by default: nothing leaves the computer"
    config.data["billing"]["share_stats"] = True
    p.push("好了")
    asyncio.run(Agent(Runtime(config, provider=p), session_id="private-session").run("整理資料成報表"))
    time.sleep(0.3)
    [(path, body)] = sent
    item = body["items"][0]
    assert path == "stats" and item["outcome"] == "done" and item["task_kind"] == "data"
    assert set(item) <= {"model", "task_kind", "skill", "outcome", "seconds", "tokens_in", "tokens_out", "retries",
                         "cut_off"}
    assert "private-session" not in str(body) and "整理資料" not in str(body)
    assert routing.report(config, {"model": "x/y", "outcome": "weird"}) is False


def test_task_kind_and_settings(config):
    assert routing.task_kind("用動畫展示風力發電機的網頁") == "page"
    assert routing.task_kind("修這個 bug") == "code"
    assert routing.task_kind("今天好嗎") == "chat"
    from fastapi.testclient import TestClient

    from sinobee.agent import Runtime
    from sinobee.server.app import create_app
    from conftest import ScriptedProvider
    c = TestClient(create_app(Runtime(config, provider=ScriptedProvider())))
    tok = config.server_token()
    q = f"?token={tok}" if tok else ""
    assert c.put("/api/settings" + q, json={"model": {"pin": "sinobee/auto"}}).status_code == 400
    r = c.put("/api/settings" + q, json={"model": {"preference": "speed", "pin": "anthropic/claude-opus-5-5"},
                                         "billing": {"share_stats": True}})
    assert r.status_code == 200
    info = c.get("/api/routing" + q).json()
    assert info["preference"] == "speed" and info["pin"] == "anthropic/claude-opus-5-5" and info["share_stats"] is True
    assert c.get("/api/status" + q).json()["pin"] == "anthropic/claude-opus-5-5"


def test_a_failing_vendor_is_left_out_and_another_model_answers_in_the_same_step(config, monkeypatch):
    from sinobee.providers import sinobee as S
    from sinobee.providers.base import ProviderError, relay_error
    asked = []

    def post(cfg, path, body, timeout=8):
        asked.append(body)
        ex = body.get("exclude") or []
        return {"model": "anthropic/claude-sonnet-5-5" if "openai/gpt-5.6-sol" in ex else "openai/gpt-5.6-sol",
                "reason": "偏好品質：評鑑分數最高", "ttl": 600}
    monkeypatch.setattr(routing, "_post", post)
    sent = []

    async def fake_chat(self, messages, tools=None, **kw):
        sent.append(self.model)
        if self.model == "openai/gpt-5.6-sol":
            raise relay_error(self.label, 503, '{"error":{"message":"openai models are unavailable on SinoBee.ai for '
                              'now (vendor_down): You have no credits remaining","type":"upstream_unavailable",'
                              '"code":"vendor_down"}}')
        return ChatResponse(Message("assistant", "plan"))
    monkeypatch.setattr(S.SinoBeeProvider, "chat", fake_chat)
    e = relay_error("sinobee/openai/gpt-5.6-sol", 503, '{"error":{"message":"x","code":"vendor_down"}}')
    assert isinstance(e, ProviderError) and not e.retryable and "vendor_down" in str(e)
    config.data["model"].update(default="sinobee/auto")
    chain = build_chain(config, "sinobee/auto@thinking")
    tok = routing.HINT.set({"task_kind": "page", "turn": "heal-1"})
    try:
        with scope("s", "agent") as sc:
            r = asyncio.run(chain.chat([Message("user", "規劃")]))
            r2 = asyncio.run(chain.chat([Message("user", "再來")]))
    finally:
        routing.HINT.reset(tok)
    assert r.message.text() == "plan" and r.model == "sinobee/anthropic/claude-sonnet-5-5"
    assert sent == ["openai/gpt-5.6-sol", "anthropic/claude-sonnet-5-5", "anthropic/claude-sonnet-5-5"]
    assert asked[1]["exclude"] == ["openai/gpt-5.6-sol"] and len(asked) == 2      # the new pick holds for the turn
    note = sc.route["thinking"]
    assert note["model"] == "sinobee/anthropic/claude-sonnet-5-5" and "已改用" in note["reason"]
    assert note["replaced"][0]["model"] == "sinobee/openai/gpt-5.6-sol" and "no credits" in note["replaced"][0]["why"]
    assert r2.model == "sinobee/anthropic/claude-sonnet-5-5"


def test_the_allowance_or_a_bad_request_is_not_rerouted(config, monkeypatch):
    from sinobee.providers import sinobee as S
    from sinobee.providers.base import ProviderError
    monkeypatch.setattr(routing, "_post", lambda *a, **k: {"model": "openai/gpt-5.6-sol", "ttl": 600})
    calls = []

    async def bad(self, messages, tools=None, **kw):
        calls.append(1)
        raise ProviderError(f"{self.label} HTTP 400: bad request", 400)
    monkeypatch.setattr(S.SinoBeeProvider, "chat", bad)
    config.data["model"].update(default="sinobee/auto")
    tok = routing.HINT.set({"turn": "heal-2"})
    try:
        with scope("s", "agent"):
            try:
                asyncio.run(build_chain(config).chat([Message("user", "x")]))
            except ProviderError:
                pass
    finally:
        routing.HINT.reset(tok)
    assert len(calls) == 1
