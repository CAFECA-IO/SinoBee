"""Local model hosting: hardware fit, catalog recommendation, Ollama transport and status assembly."""
import asyncio

import pytest

from sinobee import local
from sinobee.config import Config
from sinobee.local import catalog, hardware
from sinobee.providers import build_provider
from sinobee.providers.ollama import OllamaProvider, to_ollama_messages
from sinobee.types import ImagePart, Message, TextPart, ToolCall


def rows_for(b, nctx=16384, disk=None):
    return [{**m, "fit": catalog.fit(m, b, nctx, disk)} for m in catalog.CATALOG]


def test_budget_cpu_only_recommends_small_model():
    hw = {"gpus": [], "ram_gb": 8, "cores": 4}
    b = hardware.budget(hw)
    assert b["accel"] == "cpu" and b["fast_gb"] == 0
    rec = catalog.recommend(rows_for(b))
    assert rec["chat"] in ("qwen3.5:4b", "granite4.1:3b")
    assert rec["embedding"]
    big = next(r for r in rows_for(b) if r["id"] == "gpt-oss:120b")
    assert big["fit"]["level"] == "no"


def test_budget_apple_silicon_and_nvidia():
    mac = hardware.parse_host_hardware("os=darwin;arch=arm64;cpu=Apple M3 Pro;cores=12;mem_gb=36;gpu=Apple M3 Pro;"
                                       "vram_gb=0;gpu_vendor=;unified=1;disk_gb=200")
    b = hardware.budget(mac)
    assert b["accel"] == "metal" and 20 < b["fast_gb"] < 26
    nctx = hardware.auto_num_ctx(b)
    assert nctx == 32768
    rec = catalog.recommend(rows_for(b, nctx))
    assert rec["chat"] in ("qwen3.8:27b", "qwen3.6:35b")
    nv = hardware.parse_host_hardware("os=linux;arch=x86_64;cpu=x;cores=16;mem_gb=64;gpu=RTX 4090;vram_gb=24;"
                                      "gpu_vendor=nvidia;unified=0;disk_gb=500")
    b2 = hardware.budget(nv)
    assert b2["accel"] == "cuda" and b2["total_gb"] > b2["fast_gb"] > 20
    fit70 = catalog.fit(next(m for m in catalog.CATALOG if m["id"] == "llama3.3:70b"), b2, 16384)
    assert fit70["level"] == "slow"   # spills to system RAM


def test_override_and_disk_check():
    b = hardware.budget({"gpus": [], "ram_gb": 8}, override_gb=48)
    assert b["override"] and b["total_gb"] == 48
    m = next(m for m in catalog.CATALOG if m["id"] == "qwen3.5:9b")
    assert catalog.fit(m, {"accel": "cpu", "fast_gb": 0, "total_gb": 64}, 16384, disk_free_gb=3)["level"] == "no"


def test_to_ollama_messages_maps_tool_names_and_images():
    img = ImagePart("aGVsbG8=", "image/png")
    msgs = [Message("system", "sys"), Message("user", [TextPart("look"), img]),
            Message("assistant", "", [ToolCall("c1", "screenshot", {"x": 1})]),
            Message("tool", [TextPart("done"), img], tool_call_id="c1"), Message("assistant", "ok")]
    out = to_ollama_messages(msgs)
    assert out[1]["images"] == ["aGVsbG8="]
    assert out[2]["tool_calls"][0]["function"] == {"name": "screenshot", "arguments": {"x": 1}}
    assert out[3] == {"role": "tool", "content": "done", "tool_name": "screenshot"}
    assert out[4]["role"] == "user" and out[4]["images"] == ["aGVsbG8="]   # tool images follow as a user turn
    assert to_ollama_messages(msgs, vision=False)[1].get("images") is None


def test_ollama_provider_payload_and_parse(monkeypatch, tmp_path):
    monkeypatch.setenv("SINOBEE_HOME", str(tmp_path))
    cfg = Config({"local": {"base_url": "http://gpu-box:11434/v1", "num_ctx": 20000}}, tmp_path)
    p = build_provider("ollama/qwen3:8b", cfg)
    assert isinstance(p, OllamaProvider) and p.base_url == "http://gpu-box:11434" and p.num_ctx == 20000
    seen = {}

    async def fake_post(url, payload, headers):
        seen.update(url=url, payload=payload)
        return {"model": "qwen3:8b", "message": {"role": "assistant", "content": "",
                "tool_calls": [{"function": {"name": "shell", "arguments": {"command": "ls"}}}]},
                "done_reason": "stop", "prompt_eval_count": 1234, "eval_count": 56}
    monkeypatch.setattr(p, "_post", fake_post)
    from sinobee.types import ToolSpec
    r = asyncio.run(p.chat([Message("user", "hi")], [ToolSpec("shell", "run", {"type": "object"})], max_tokens=8192))
    assert seen["url"] == "http://gpu-box:11434/api/chat"
    assert seen["payload"]["options"]["num_ctx"] == 20000 and seen["payload"]["options"]["num_predict"] == 5000
    assert seen["payload"]["stream"] is False and seen["payload"]["tools"][0]["function"]["name"] == "shell"
    assert r.message.tool_calls[0].name == "shell" and r.message.tool_calls[0].arguments == {"command": "ls"}
    assert r.usage.input_tokens == 1234 and r.usage.output_tokens == 56


def test_local_model_needs_no_key_and_is_free(tmp_path):
    from sinobee.pricing import cost_usd, price_for
    from sinobee.providers import needs_key
    cfg = Config({}, tmp_path)
    assert not needs_key("ollama", cfg)
    price, src = price_for("ollama", "qwen3:8b")
    assert src == "free" and cost_usd(price, 10000, 500) == 0


def test_status_assembly(monkeypatch, tmp_path):
    from sinobee.local import ollama as oll

    class FakeCli:
        def __init__(self, url): self.url = url
        async def version(self): return "0.33.1"
        async def tags(self): return [{"name": "qwen3:8b", "size": 5_200_000_000, "digest": "a"},
                                      {"name": "my-custom:7b", "size": 4_000_000_000, "digest": "b"}]
        async def ps(self): return [{"name": "qwen3:8b", "size": 6_000_000_000, "size_vram": 6_000_000_000,
                                     "context_length": 16384}]
        async def show(self, name): return {"capabilities": ["completion", "tools"], "details": {"parameter_size": "7B"}}

    monkeypatch.setattr(local, "Ollama", FakeCli)
    monkeypatch.setattr(local, "resolve_url", lambda cfg, refresh=False: ("http://127.0.0.1:11434", True))
    monkeypatch.setattr(local, "detect", lambda models_dir=None: {"source": "local", "os": "linux", "cores": 8,
                        "ram_gb": 32, "gpus": [], "in_docker": False, "disk_free_gb": 100, "cpu": "x"})

    class RT:
        config = Config({"model": {"default": "ollama/qwen3:8b"}}, tmp_path)
    lr = oll.LocalRuntime(RT.config)
    st = asyncio.run(local.status(RT(), lr))
    q = next(m for m in st["models"] if m["id"] == "qwen3:8b")
    assert q["state"] == "running" and q["gpu_pct"] == 100 and q["selected"] == ["default"]
    assert any(e["id"] == "my-custom:7b" and e["custom"] for e in st["extras"])
    assert st["runtime"]["reachable"] and st["num_ctx"] == 16384
    assert next(m for m in st["models"] if m["id"] == "gpt-oss:20b")["state"] == "absent"


def test_agent_context_budget_shrinks_for_local(monkeypatch, tmp_path):
    from sinobee.agent.loop import Agent
    monkeypatch.setattr(local, "num_ctx", lambda cfg, b=None: 16384)

    class Dummy:
        config = Config({"model": {"default": "ollama/qwen3:8b"}}, tmp_path)
    a = Agent.__new__(Agent)
    a.config = Dummy.config
    assert a._context_budget() == 16384 - 4096 - 1024          # num_ctx - answer room - margin
    assert a._max_tokens() == 4096
    a.config = Config({"model": {"default": "openai/gpt-4.1"}}, tmp_path)
    assert a._context_budget() == 1_047_576 - 8192 - 1_047_576 // 50
    a.config = Config({"model": {"default": "openai/gpt-4.1"}, "agent": {"context_budget": 120000}}, tmp_path)
    assert a._context_budget() == 120000                        # an explicit cap still applies
