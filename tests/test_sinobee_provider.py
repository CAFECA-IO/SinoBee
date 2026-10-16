"""P6: the `sinobee/...` provider talks to the SinoBee.ai relay with signed requests; quota stops fall back."""
import asyncio
import base64
import hashlib
import json
from urllib.parse import urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from sinobee import account
from sinobee.accounting import scope
from sinobee.providers import FallbackChain, build_chain, build_provider, usable_now
from sinobee.providers import sinobee as sb
from sinobee.providers.base import QuotaExceeded
from sinobee.types import Message

MODELS = {"object": "list", "data": [
    {"id": "anthropic/claude-sonnet-5-5", "format": "anthropic", "name": "Claude Sonnet 5.5", "available": True},
    {"id": "openai/gpt-5.6-luna", "format": "openai", "name": "GPT-5.6 Luna", "available": True},
    {"id": "gemini/gemini-3.1-pro", "format": "openai", "available": False},
]}
QUOTA = {"plan": {"code": "low", "name": "Pro", "nameEn": "Pro", "concurrency": 2}, "endsAt": "2026-11-15T00:00:00Z",
         "graceEndsAt": "2026-12-15T00:00:00Z", "windows": [
             {"window": "6h", "usedPct": 42.5, "remainingTokens": 123456, "releases": [{"at": "2026-10-16T20:00:00Z", "pct": 10}]},
             {"window": "168h", "usedPct": 8.1, "remainingTokens": 999999, "releases": []}], "inFlight": 0}


class Relay:
    """A fake sinobee.ai relay that checks the Ed25519 signature on every request."""

    def __init__(self, config):
        self.verify = Ed25519PublicKey.from_public_bytes(base64.b64decode(account.signing_public_key(config)))
        self.seen = []
        self.quota_left = True
        self.subscribed = True

    def handler(self, req: httpx.Request) -> httpx.Response:
        u = urlsplit(str(req.url))
        body = req.read()
        msg = "\n".join([req.method, u.path + (f"?{u.query}" if u.query else ""), req.headers["x-sb-timestamp"],
                         req.headers["x-sb-nonce"], hashlib.sha256(body).hexdigest()])
        self.verify.verify(base64.b64decode(req.headers["x-sb-signature"]), msg.encode())
        assert req.headers["authorization"] == "Bearer sbh_" + "a" * 40
        assert "x-api-key" not in req.headers, "no vendor key leaves this install"
        self.seen.append((req.method, u.path, json.loads(body) if body else None))
        if not self.subscribed:
            return httpx.Response(402, json={"error": {"message": "subscription required", "type": "subscription_required",
                                                       "code": "subscription", "quota": "subscription"}})
        if u.path == "/api/v1/llm/models":
            return httpx.Response(200, json=MODELS)
        if u.path == "/api/v1/llm/quota":
            return httpx.Response(200, json=QUOTA)
        if not self.quota_left:
            if u.path.endswith("/messages"):
                return httpx.Response(429, json={"type": "error", "error": {"type": "insufficient_quota", "message": "6-hour limit reached",
                                                                            "quota": "6h", "retry_at": "2026-10-16T20:00:00Z"}})
            return httpx.Response(429, json={"error": {"message": "6-hour limit reached", "type": "insufficient_quota",
                                                       "code": "6h", "quota": "6h", "retry_at": "2026-10-16T20:00:00Z"}})
        h = {"x-sinobee-cost-6h-pct": "1.25", "x-sinobee-quota-6h": "43.75", "x-sinobee-quota-168h": "8.30"}
        if u.path == "/api/v1/llm/anthropic/v1/messages":
            return httpx.Response(200, headers=h, json={"content": [{"type": "text", "text": "hi from claude"}],
                                                        "usage": {"input_tokens": 10, "output_tokens": 3}, "stop_reason": "end_turn"})
        if u.path == "/api/v1/llm/chat/completions":
            return httpx.Response(200, headers=h, json={"choices": [{"message": {"content": "hi from gpt"}, "finish_reason": "stop"}],
                                                        "usage": {"prompt_tokens": 10, "completion_tokens": 3}})
        return httpx.Response(404, json={"error": {"message": "nope"}})


@pytest.fixture
def relay(config, monkeypatch):
    r = Relay(config)
    transport = httpx.MockTransport(r.handler)
    real_async, real_sync = httpx.AsyncClient, httpx.Client

    class Async(real_async):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    def request(method, url, **kw):
        kw.pop("timeout", None)
        with real_sync(transport=transport) as c:
            return c.request(method, url, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", Async)
    monkeypatch.setattr(account.httpx, "request", request)
    sb.forget()
    sb._FORMATS.clear()
    account.save(config, {"access_token": "sbh_" + "a" * 40, "refresh_token": "sbr_" + "r" * 40, "expires_in": 900,
                          "scope": "llm vault:rw"})
    return r


def test_signed_relay_calls_pick_the_format_and_report_the_quota_share(config, relay):
    assert usable_now("sinobee", config)
    p = build_provider("sinobee/anthropic/claude-sonnet-5-5", config)
    assert p.label == "sinobee/anthropic/claude-sonnet-5-5"
    with scope("s1", "agent") as sc:
        r = asyncio.run(p.chat([Message("user", "hi")], max_tokens=64))
    assert r.message.text() == "hi from claude"
    assert [s[1] for s in relay.seen] == ["/api/v1/llm/models", "/api/v1/llm/anthropic/v1/messages"]
    assert relay.seen[-1][2]["model"] == "anthropic/claude-sonnet-5-5"
    assert sc.quota_6h_pct == 1.25 and sc.quota == {"6h": 43.75, "168h": 8.3}
    assert sc.as_dict()["quota_6h_pct"] == 1.25

    g = build_provider("sinobee/openai/gpt-5.6-luna", config)
    assert asyncio.run(g.chat([Message("user", "hi")])).message.text() == "hi from gpt"
    assert relay.seen[-1][1] == "/api/v1/llm/chat/completions"
    assert asyncio.run(g.list_models()) == ["anthropic/claude-sonnet-5-5", "openai/gpt-5.6-luna"], "unavailable hidden"


def test_quota_stop_is_not_retried_and_falls_back(config, relay):
    relay.quota_left = False

    class Local:
        label = "ollama/qwen"
        provider_name, model = "ollama", "qwen"

        async def chat(self, messages, tools=None, **kw):
            from sinobee.types import ChatResponse
            return ChatResponse(Message("assistant", "local answer"))

    chain = FallbackChain([build_provider("sinobee/anthropic/claude-sonnet-5-5", config), Local()])
    with scope("s1", "agent") as sc:
        r = asyncio.run(chain.chat([Message("user", "hi")]))
    assert r.message.text() == "local answer"
    calls = [s for s in relay.seen if s[1].endswith("/messages")]
    assert len(calls) == 1, "quota stops are not retried"
    [ev] = sc.drain_quota_events()
    assert ev == {"window": "6h", "retry_at": "2026-10-16T20:00:00Z", "model": "sinobee/anthropic/claude-sonnet-5-5",
                  "fallback": "ollama/qwen"}

    with pytest.raises(QuotaExceeded) as e:
        asyncio.run(build_provider("sinobee/openai/gpt-5.6-luna", config).chat([Message("user", "hi")]))
    from sinobee.agent.errors import explain_error
    info = explain_error(e.value)
    assert "6 小時額度" in info["summary"] and info["quota"]["window"] == "6h"
    assert info["model"] == "sinobee/openai/gpt-5.6-luna"


def test_no_subscription_and_signed_out(config, relay):
    relay.subscribed = False
    with pytest.raises(QuotaExceeded) as e:
        asyncio.run(build_provider("sinobee/openai/gpt-5.6-luna", config).chat([Message("user", "hi")], retries=0))
    assert e.value.window == "subscription"
    assert account.quota(config)["status"] == "no_subscription"
    account.logout(config)
    assert not usable_now("sinobee", config)
    assert account.quota(config) == {"status": "signed_out"}
    with pytest.raises(Exception, match="登入"):
        asyncio.run(build_provider("sinobee/openai/gpt-5.6-luna", config).chat([Message("user", "hi")], retries=0))


def test_quota_api_catalog_and_settings(config, relay):
    from fastapi.testclient import TestClient
    from sinobee.agent import Runtime
    from sinobee.server import create_app
    config.data["model"]["default"] = "sinobee/anthropic/claude-sonnet-5-5"
    with TestClient(create_app(Runtime(config))) as c:
        q = c.get("/api/llm/quota").json()
        assert q["status"] == "ok" and q["plan"]["name"] == "Pro" and q["in_use"] is True
        assert q["windows"][0]["usedPct"] == 42.5
        cat = c.get("/api/models/catalog?refresh=true").json()
        sbp = next(p for p in cat["providers"] if p["name"] == "sinobee")
        assert sbp["status"] == "ready" and sbp["subscription"] and sbp["needs_key"] is False
        assert [m["spec"] for m in sbp["models"]][:2] == ["sinobee/anthropic/claude-sonnet-5-5", "sinobee/openai/gpt-5.6-luna"]
        s = c.get("/api/settings").json()
        assert s["selected"]["default"]["ok"] and s["needs_setup"] is False
    from sinobee.pricing import price_for
    assert price_for("sinobee", "anthropic/claude-sonnet-5-5") == ({"input": 0, "output": 0}, "subscription")


def test_build_chain_mixes_subscription_and_keys(config, relay):
    config.data["model"]["default"] = "sinobee/openai/gpt-5.6-luna"
    config.data["model"]["fallbacks"] = ["openai/gpt-5.5"]
    chain = build_chain(config)
    assert [p.label for p in chain.providers] == ["sinobee/openai/gpt-5.6-luna", "openai/gpt-5.5"]


async def test_agent_reports_quota_share_and_fallback(config, relay):
    """Each answer carries its share of the 6 h limit; a quota stop that a fallback covered is announced."""
    from sinobee.agent import Agent, Runtime
    config.data["model"]["default"] = "sinobee/anthropic/claude-sonnet-5-5"
    config.data["model"]["fallbacks"] = ["sinobee/openai/gpt-5.6-luna"]
    rt = Runtime(config)
    events = []
    agent = Agent(rt, emit=lambda k, d: events.append((k, d)))
    assert await agent.run("hi") == "hi from claude"
    end = next(d for k, d in events if k == "turn_end")
    assert end["usage"]["quota_6h_pct"] == 1.25 and end["usage"]["quota"]["6h"] == 43.75

    relay.quota_left = False
    events.clear()
    await agent.run("again")
    q = [d for k, d in events if k == "quota"]
    assert q and q[0]["window"] == "6h" and q[-1]["stopped"] is True
    end = next(d for k, d in events if k == "turn_end")
    assert end["status"] == "error" and end["error"]["quota"]["window"] == "6h"
    await rt.drain()
