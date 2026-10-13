import os
import stat

from fastapi.testclient import TestClient

from sinobee.agent import Agent, Runtime
from sinobee.config import Config
from sinobee.pricing import cost_usd, price_for
from sinobee.providers import build_provider, resolve_key
from sinobee.server import create_app


def test_layering_and_env_lock(home, monkeypatch):
    cfg = Config.load()
    assert cfg.get("model.default") is None
    cfg.save_gui_settings({"model": {"default": "openai/gpt-5.4"}, "permissions": {"approval": "auto"}})
    assert Config.load().get("model.default") == "openai/gpt-5.4"
    monkeypatch.setenv("SINOBEE_MODEL", "anthropic/claude-sonnet-4-5")
    cfg = Config.load()
    assert cfg.get("model.default") == "anthropic/claude-sonnet-4-5" and "model.default" in cfg.locked
    ignored = cfg.save_gui_settings({"model": {"default": "gemini/gemini-2.5-pro"}})
    assert ignored == ["model.default"] and cfg.get("model.default") == "anthropic/claude-sonnet-4-5"
    assert cfg.get("permissions.approval") == "auto"


def test_secrets_and_key_precedence(home, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = Config.load()
    cfg.secrets.set("openai", "sk-gui-key-123456")
    path = cfg.home / "secrets.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert resolve_key("openai", cfg) == ("sk-gui-key-123456", "gui")
    assert build_provider("openai/gpt-5.4", cfg).api_key == "sk-gui-key-123456"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key-999999")
    assert resolve_key("openai", cfg) == ("sk-env-key-999999", "env")


def test_pricing():
    p, src = price_for("anthropic", "claude-sonnet-4-5-20250929")
    assert src == "builtin" and p["input"] == 3
    p, _ = price_for("openrouter", "anthropic/claude-sonnet-4.5")
    assert p["output"] == 15
    assert price_for("openai", "gpt-5-mini")[0]["input"] == 0.25      # longest prefix, not gpt-5
    assert price_for("ollama", "qwen3")[1] == "free"
    assert price_for("mylab", "mystery-model")[0] is None
    p, src = price_for("openai", "gpt-5.4", {"gpt-5.4": {"input": 1, "output": 2}})
    assert src == "override" and p["input"] == 1
    # 1M input of which 400k cached, 100k output on gpt-4.1: 0.6*2 + 0.4*0.5 + 0.1*8 = 2.2
    assert abs(cost_usd(price_for("openai", "gpt-4.1")[0], 1_000_000, 100_000, 400_000) - 2.2) < 1e-9
    long = price_for("gemini", "gemini-2.5-pro")[0]
    assert abs(cost_usd(long, 300_000, 0) - 0.75) < 1e-9


async def test_usage_recorded_per_turn_and_budget(config, provider):
    provider.push("", [("usage_stats", {"period": "session"})])
    provider.push("done")
    config.data["permissions"]["toolsets"].append("usage")
    rt = Runtime(config, provider=provider)
    ends = []
    agent = Agent(rt, emit=lambda k, d: ends.append(d) if k == "turn_end" else None)
    await agent.run("how much have you spent?")
    u = ends[0]["usage"]
    assert u["calls"] == 2 and u["input_tokens"] == 2000
    # gpt-4.1: per call 800 uncached*2 + 200 cached*0.5 + 500 out*8 = 5700 / 1e6
    assert abs(u["cost_usd"] - 2 * 0.0057) < 1e-9
    tool_out = [m for m in agent.history if m.role == "tool"][0].text()
    assert '"calls": 1' in tool_out
    by = rt.memory.usage_grouped("model")
    assert by[0]["key"] == "openai/gpt-4.1"
    config.data["usage"]["daily_budget_usd"] = 0.001
    config.data["usage"]["enforce_budget"] = True
    out = await agent.run("again")
    assert "花費上限" in out


def test_settings_api(home, monkeypatch, provider):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai-000000")
    rt = Runtime(Config.load())
    c = TestClient(create_app(rt))
    s = c.get("/api/settings").json()
    assert s["needs_setup"] and s["model"]["default"] is None
    oa = next(p for p in s["providers"] if p["name"] == "openai")
    assert oa["key_source"] == "env" and "sk-env" not in oa["key_masked"]
    r = c.put("/api/settings", json={"model": {"default": "anthropic/claude-sonnet-4-5", "fallbacks": "openai/gpt-5.4"},
                                     "api_keys": {"anthropic": "sk-ant-gui-abcdef", "openai": "sk-should-be-ignored"},
                                     "usage": {"monthly_budget_usd": "20"},
                                     "pricing": {"my-model": {"input": 1, "output": 2}}}).json()
    assert not r["needs_setup"]
    assert "api_key:openai" in r["ignored_locked"]
    assert rt.provider.label == "anthropic/claude-sonnet-4-5" and rt.provider.providers[0].api_key == "sk-ant-gui-abcdef"
    assert rt.provider.providers[1].api_key == "sk-env-openai-000000"
    assert "sk-ant-gui" not in str(c.get("/api/settings").json())
    assert c.put("/api/settings", json={"model": {"default": "nonsense"}}).status_code == 400
    u = c.get("/api/usage?days=7").json()
    assert len(u["by_day"]) == 7 and u["budget"]["monthly"]["limit"] == 20
    assert any(row["source"] == "override" for row in c.get("/api/pricing").json()["rows"])


def test_skills_api(home, monkeypatch, provider, tmp_path):
    proj = tmp_path / "proj"
    sk = proj / ".sinobee" / "skills" / "repo-build"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text("---\nname: repo-build\ndescription: Builds the repo. Use when building.\n---\n1. make")
    monkeypatch.setenv("SINOBEE_PROJECT", str(proj))
    rt = Runtime(Config.load(), provider=provider)
    c = TestClient(create_app(rt))
    data = c.get("/api/skills").json()
    row = next(s for s in data["skills"] if s["name"] == "repo-build")
    assert row["trust"] == "untrusted" and not data["project"]["trusted"]
    c.post("/api/skills/project/trust", json={"trusted": True})
    assert next(s for s in c.get("/api/skills").json()["skills"] if s["name"] == "repo-build")["trust"] == "project"
    c.post("/api/skills/web-research/disable", json={"disabled": True})
    assert "web-research" not in {s.name for s in rt.skills.visible()}
    r = c.get("/api/skills/web-research/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"
    assert c.post("/api/skills/web-research/approve", json={}).status_code in (200, 400)
