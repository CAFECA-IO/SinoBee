"""Model settings: one catalog across providers, keys verified on connect, local + cloud models together."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sinobee.agent import Runtime
from sinobee.config import Config
from sinobee.providers import catalog
from sinobee.providers.base import ProviderError

GEMINI_IDS = ["gemini-flash-latest", "gemini-pro-latest", "gemini-2.5-flash", "gemini-embedding-2",
              "gemini-2.5-flash-preview-tts", "imagen-4.0-generate", "veo-3.0-generate"]


class Lister:
    def __init__(self, name, cfg):
        self.name, self.cfg = name, cfg

    async def list_models(self):
        from sinobee.providers import resolve_key
        if self.name == "ollama":
            return ["qwen3:8b", "nomic-embed-text"]
        key = resolve_key(self.name, self.cfg)[0]
        if self.name == "gemini":
            if key != "AIza-good-key-123":
                raise ProviderError("gemini HTTP 400: API key not valid", 400)
            return GEMINI_IDS
        raise ProviderError(f"{self.name} HTTP 401: unauthorized", 401)


@pytest.fixture
def client(home, monkeypatch, provider):
    monkeypatch.setattr(catalog, "build_provider", lambda spec, cfg: Lister(spec.split("/")[0], cfg))
    catalog.forget()
    cfg = Config.load()
    cfg.data["model"]["default"] = "ollama/qwen3:8b"
    cfg.save_gui_settings({"model": {"default": "ollama/qwen3:8b"}})
    rt = Runtime(cfg, provider=provider)
    from sinobee.server import create_app
    c = TestClient(create_app(rt))
    tok = rt.config.get("server.token") or ""
    c.q = f"?token={tok}"
    return c


def test_kinds():
    assert catalog.kind_of("gemini-flash-latest") == "chat"
    assert catalog.kind_of("text-embedding-3-small") == "embedding"
    assert catalog.kind_of("gemini-2.5-flash-preview-tts") == "other"
    assert catalog.kind_of("imagen-4.0-generate") == "other"


def test_adding_a_gemini_key_while_using_a_local_model_offers_gemini_models(client):
    cat = client.get("/api/models/catalog" + client.q).json()
    by = {p["name"]: p for p in cat["providers"]}
    assert by["ollama"]["status"] == "ready" and by["gemini"]["status"] == "no_key"
    assert cat["selected"]["default"] == {"spec": "ollama/qwen3:8b", "ok": True}

    bad = client.put("/api/providers/gemini/key" + client.q, json={"key": "AIza-wrong-key-999"}).json()
    assert bad["provider"]["status"] == "error" and "not valid" in bad["provider"]["error"]

    r = client.put("/api/providers/gemini/key" + client.q, json={"key": "AIza-good-key-123"}).json()
    g = r["provider"]
    assert g["status"] == "ready" and g["has_key"]
    chat = [m["spec"] for m in g["models"] if m["kind"] == "chat"]
    assert chat[:2] == ["gemini/gemini-flash-latest", "gemini/gemini-pro-latest"]       # recommended first
    assert "gemini/imagen-4.0-generate" not in chat
    assert [m["spec"] for m in g["models"] if m["kind"] == "embedding"] == ["gemini/gemini-embedding-2"]
    assert "suggest_default" not in r                      # the local main model still works: no nagging

    cat = client.get("/api/models/catalog" + client.q).json()
    ready = [p["name"] for p in cat["providers"] if p["status"] == "ready"]
    assert ready[:2] == ["gemini", "ollama"] or set(ready) >= {"gemini", "ollama"}

    s = client.put("/api/settings" + client.q, json={"model": {"default": "gemini/gemini-flash-latest",
                                                                 "fallbacks": "ollama/qwen3:8b"}}).json()
    assert s["model"]["default"] == "gemini/gemini-flash-latest" and s["warnings"] == []


def test_connecting_the_first_provider_suggests_a_main_model(client):
    client.put("/api/settings" + client.q, json={"model": {"default": "anthropic/claude-sonnet-5-5"}})
    r = client.put("/api/providers/gemini/key" + client.q, json={"key": "AIza-good-key-123"}).json()
    assert r["suggest_default"] == "gemini/gemini-flash-latest"
    s = client.get("/api/settings" + client.q).json()
    assert s["selected"]["default"]["ok"] is False
    w = client.put("/api/settings" + client.q, json={"model": {"escalate": "openai/gpt-5.5"}}).json()["warnings"]
    assert any("主要模型" in x for x in w) and any("升級模型" in x for x in w)


def test_key_validation_and_clear(client):
    assert client.put("/api/providers/gemini/key" + client.q, json={"key": "abc"}).status_code == 400
    assert client.put("/api/providers/nope/key" + client.q, json={"key": "AIza-good-key-123"}).status_code == 404
    client.put("/api/providers/gemini/key" + client.q, json={"key": "AIza-good-key-123"})
    r = client.put("/api/providers/gemini/key" + client.q, json={"key": ""}).json()
    assert r["provider"]["status"] == "no_key" and not r["provider"]["has_key"]


def test_env_key_is_read_only(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-good-key-123")
    assert client.put("/api/providers/gemini/key" + client.q, json={"key": "AIza-other-key-1"}).status_code == 409
