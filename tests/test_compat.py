"""A model that no longer accepts a parameter (Claude Opus 5.5: "`temperature` is deprecated for this model") must
not break a task: the provider layer retries without it, remembers it across restarts, the error is explained, and a
failure the model itself caused is still diagnosed."""
import json

from sinobee.agent import Agent, Runtime, failures
from sinobee.agent.errors import explain_error
from sinobee.providers import compat
from sinobee.providers.base import Provider, ProviderError
from sinobee.types import ChatResponse, Message, ToolCall, Usage

LABEL = "sinobee/anthropic/claude-opus-5-5"
DEPRECATED = f"{LABEL} HTTP 400: `temperature` is deprecated for this model."


class NoTemperature(Provider):
    """Rejects any request that carries temperature, like Claude Opus 5.5 behind the SinoBee.ai relay."""
    name = "sinobee"

    def __init__(self, script=None, model="anthropic/claude-opus-5-5"):
        super().__init__(model, "https://example.invalid", "k", provider_name="sinobee")
        self.script = list(script or [])
        self.sent = []

    async def _chat(self, messages, tools, *, temperature, max_tokens):
        self.sent.append(temperature)
        if temperature is not None:
            raise ProviderError(DEPRECATED, 400)
        msg = self.script.pop(0) if self.script else Message("assistant", "好")
        return ChatResponse(msg, Usage(100, 50, 0))


def fresh():
    compat._cache = None


async def test_the_call_is_retried_without_temperature_and_remembered(home):
    fresh()
    p = NoTemperature()
    r = await p.chat([Message("user", "hi")], temperature=0.3, retries=0)    # adapting is not a retry
    assert r.message.text() == "好" and p.sent == [0.3, None]
    caps = json.loads(compat.path().read_text(encoding="utf-8"))
    assert caps[LABEL]["drop"] == ["temperature"] and "deprecated" in caps[LABEL]["learned"]["temperature"]["message"]
    await p.chat([Message("user", "again")], temperature=0.2)
    assert p.sent == [0.3, None, None]                                       # never sent again
    fresh()                                                                  # a restart reads the file
    p2 = NoTemperature()
    await p2.chat([Message("user", "x")], temperature=0)
    assert p2.sent == [None]


async def test_other_errors_are_not_adapted(home):
    fresh()

    class Bad(NoTemperature):
        async def _chat(self, messages, tools, *, temperature, max_tokens):
            self.sent.append(temperature)
            raise ProviderError(f"{LABEL} HTTP 400: messages.0.content: field required", 400)
    p = Bad()
    try:
        await p.chat([Message("user", "hi")], temperature=0.3, retries=2)
    except ProviderError as e:
        assert "field required" in str(e)
    assert p.sent == [0.3] and not compat.dropped(LABEL)


def test_rejection_messages_of_several_providers():
    def rej(text, status=400):
        return compat.rejected_param(ProviderError(text, status), {"temperature": 0.3})
    assert rej(DEPRECATED) == "temperature"
    assert rej("openai/o5 HTTP 400: Unsupported parameter: 'temperature' is not supported with this model.") == "temperature"
    assert rej("x HTTP 400: Unsupported value: 'temperature' does not support 0.3 with this model.") == "temperature"
    assert rej("x HTTP 400: temperature: Extra inputs are not permitted") == "temperature"
    assert rej("x HTTP 400: temperature must be between 0 and 1") is None          # a bad value, not a rejection
    assert rej("x HTTP 429: temperature is not supported", 429) is None
    assert compat.rejected_param(ProviderError(DEPRECATED, 400), {"temperature": None}) is None


def test_the_error_is_explained_with_the_parameter():
    e = explain_error(DEPRECATED)
    assert e["summary"] == "這個模型不接受參數 temperature" and e["param"] == "temperature" and "自動" in e["hint"]
    assert explain_error(f"{LABEL} HTTP 400: messages: field required")["summary"] == "模型服務拒絕了這次請求"


async def test_a_task_on_claude_opus_5_5_plans_runs_and_says_what_it_adapted(home, config):
    fresh()
    plan = {"understanding": "u", "approach": "a", "steps": ["s"], "checklist": [], "risks": [], "ask_user": None,
            "task_type": "media", "milestones": [], "facts": {}}
    thinker = NoTemperature([Message("assistant", json.dumps(plan, ensure_ascii=False))])
    main = NoTemperature([Message("assistant", "", [ToolCall("c1", "list_dir", {"path": "."})]),
                          Message("assistant", "完成")])
    config.data["agent"].update(plan="always", quality="fast")
    events = []
    agent = Agent(Runtime(config, provider=main, thinking_provider=thinker), emit=lambda k, d: events.append((k, d)))
    out = await agent.run("用動畫展示風力發電機建構過程")
    assert out == "完成" and agent.last_status["status"] == "done"
    assert any(k == "plan" for k, _ in events)                              # planning was not skipped
    assert not any(k == "status" and "無法完成規劃" in d.get("message", "") for k, d in events)
    assert any(k == "status" and "不接受參數 temperature" in d.get("message", "") for k, d in events)
    assert thinker.sent[:2] == [0.2, None] and None in main.sent and main.sent.count(0.3) <= 1


async def test_a_failure_the_model_caused_is_still_diagnosed(home, config):
    class Broken:
        label = LABEL

        async def chat(self, *a, **k):
            raise ProviderError(f"{LABEL} HTTP 400: something else entirely", 400)
    rt = Runtime(config, provider=Broken(), thinking_provider=Broken())
    failures.save(config.home, {"id": "r1", "created": 1, "status": "error", "request": "x", "work_log": [],
                                "reason": {"summary": "這個模型不接受參數 temperature", "detail": DEPRECATED},
                                "models": {"execution": LABEL}, "steps": 1, "diagnosis": None})
    dg = await failures.diagnose(rt, config.home, "r1")
    assert dg["model"] == "rules" and dg["category"] == "provider_compat"
    assert "temperature" in dg["summary"] and LABEL in dg["summary"] and "模型無法完成診斷" in dg["note"]
    assert dg["improvements"][0]["target"] == "sinobee"


async def test_diagnosis_falls_back_to_the_execution_model(home, config):
    class Ok:
        label = "openai/gpt-5.5"

        async def chat(self, *a, **k):
            return ChatResponse(Message("assistant", json.dumps({"category": "environment", "summary": "網路中斷"},
                                                                ensure_ascii=False)), Usage(1, 1, 0))

    class Broken:
        label = LABEL

        async def chat(self, *a, **k):
            raise ProviderError("down", 503)
    rt = Runtime(config, provider=Ok(), thinking_provider=Broken())
    failures.save(config.home, {"id": "r2", "created": 1, "status": "error", "request": "x", "work_log": [],
                                "reason": {"summary": "x"}, "diagnosis": None})
    dg = await failures.diagnose(rt, config.home, "r2")
    assert dg["model"] == "openai/gpt-5.5" and dg["category"] == "environment"


def test_model_caps_api(home, config):
    from fastapi.testclient import TestClient
    from sinobee.server import create_app
    from conftest import ScriptedProvider
    fresh()
    compat.learn(LABEL, "temperature", DEPRECATED)
    with TestClient(create_app(Runtime(config, provider=ScriptedProvider()))) as c:
        assert c.get("/api/model-caps").json()["models"][LABEL]["drop"] == ["temperature"]
        assert c.delete("/api/model-caps", params={"model": LABEL}).json()["ok"]
        assert LABEL not in c.get("/api/model-caps").json()["models"]


async def test_the_relay_header_teaches_the_client_too(home, monkeypatch):
    """The SinoBee.ai relay itself leaves out what a model rejects and says so (x-sinobee-dropped-params): the
    client then stops sending it, so later calls need no second attempt."""
    import httpx
    from sinobee.providers.anthropic import AnthropicProvider
    fresh()
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"x-sinobee-dropped-params": "temperature"},
                              json={"content": [{"type": "text", "text": "好"}], "usage": {"input_tokens": 1,
                                                                                            "output_tokens": 1}})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    p = AnthropicProvider("anthropic/claude-opus-5-5", "https://relay.invalid/anthropic", None,
                          provider_name="sinobee", auth=lambda m, u, b: {"authorization": "Bearer t"})
    await p.chat([Message("user", "hi")], temperature=0.3)
    await p.chat([Message("user", "again")], temperature=0.3)
    assert seen[0]["temperature"] == 0.3 and "temperature" not in seen[1]
    assert compat.dropped(LABEL) == {"temperature"}
