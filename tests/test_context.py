"""Context window management: estimates, per-model budgets, compaction, overflow retry and checkpoints."""
from sinobee.agent import Agent, Runtime
from sinobee.agent.context import (SUMMARY_PREFIX, compact, context_budget, context_window, estimate_tokens,
                                   known_window, text_tokens)
from sinobee.config import Config
from sinobee.providers.base import ProviderError
from sinobee.types import ChatResponse, Message, ToolSpec, Usage


def answer_summaries(provider, text="summary of earlier work"):
    """Let the scripted provider answer summariser calls itself, without consuming the queued replies."""
    real = provider.chat

    async def chat(messages, tools=None, **kw):
        if kw.get("max_tokens") == 1500:
            provider.requests.append((messages, tools))
            return ChatResponse(Message("assistant", text), Usage(100, 50, 0))
        return await real(messages, tools, **kw)
    provider.chat = chat


def test_cjk_text_counts_about_one_token_per_character():
    zh = "分析我的資料庫，找出前五大客戶並計算營收成長率。" * 20
    assert text_tokens(zh) >= len(zh) * 0.95          # was len/3.2: a ~3x underestimate
    code = "SELECT id, SUM(amount) FROM orders GROUP BY 1;" * 20
    assert len(code) / 4.5 < text_tokens(code) < len(code) / 3


def test_tool_schemas_and_extra_text_are_counted():
    msgs = [Message("system", "s"), Message("user", "hi")]
    tool = ToolSpec("db_query", "Run read-only SQL " * 50, {"type": "object", "properties": {"sql": {"type": "string"}}})
    assert estimate_tokens(msgs, [tool], "記憶" * 500) > estimate_tokens(msgs) + 1000


def test_context_windows_per_model(tmp_path):
    assert known_window("anthropic/claude-sonnet-4-5") == 200_000
    assert known_window("openai/gpt-4.1-mini") == 1_047_576
    assert known_window("openai/gpt-4o") == 128_000
    assert known_window("gemini/gemini-2.5-flash") == 1_048_576
    assert known_window("someone/unknown-model") is None
    cfg = Config({"model": {"default": "someone/unknown-model"}}, tmp_path)
    assert context_window(cfg) == (128_000, "default")
    cfg = Config({"model": {"default": "openai/gpt-4o", "context_window": 32000}}, tmp_path)
    assert context_window(cfg) == (32000, "setting")
    assert context_budget(cfg, 8192) == 32000 - 8192 - 1024


async def test_compaction_keeps_recent_messages_by_size(provider):
    provider.push("目標：整理客戶資料；已完成匯出。")
    msgs = [Message("system", "sys"), Message("user", "最初的需求：整理客戶資料")]
    for i in range(30):
        msgs += [Message("assistant", f"第 {i} 步" + "內容" * 300), Message("user", f"繼續 {i}")]
    res = await compact(msgs, provider, budget=8000)
    assert res.changed and res.summary.startswith("目標")
    assert res.messages[1].text().startswith(SUMMARY_PREFIX)
    kept = res.messages[-res.tail_len:]
    assert kept[-1].text() == "繼續 29" and estimate_tokens(kept) <= 8000 * 0.4 + 700
    assert estimate_tokens(res.messages) < 8000 * 0.8
    digest = provider.requests[0][0][1].text()
    assert "最初的需求" in digest                         # the original request survives a long digest


async def test_a_single_huge_message_is_trimmed(provider):
    provider.push("summary")
    big = "很長的文件" * 20000                              # ~100k tokens pasted in one message
    msgs = [Message("system", "sys"), Message("user", "hi"), Message("assistant", "hello"), Message("user", big)]
    res = await compact(msgs, provider, budget=10000)
    assert res.changed and res.trimmed == 1
    assert estimate_tokens(res.messages) < 10000
    assert "removed to fit the context window" in res.messages[-1].text()


async def test_overflow_error_compacts_and_retries(config, provider):
    config.data["model"]["context_window"] = 400_000      # the estimate thinks everything fits
    rt = Runtime(config, provider=provider)
    agent = Agent(rt)
    for i in range(40):
        agent.history += [Message("user", f"問題 {i} " + "資料" * 400), Message("assistant", f"回答 {i}")]
    calls = {"n": 0}
    real_chat = provider.chat

    async def chat(messages, tools=None, **kw):
        if kw.get("max_tokens") != 1500:                  # not the summariser
            calls["n"] += 1
            if calls["n"] == 1:
                raise ProviderError("HTTP 400: prompt is too long: 210000 tokens > 200000 maximum", 400)
        return await real_chat(messages, tools, **kw)
    provider.chat = chat
    answer_summaries(provider)
    provider.push("好的，已完成")
    out = await agent.run("最後一個問題")
    assert out == "好的，已完成" and calls["n"] == 2
    assert agent.history[0].text().startswith(SUMMARY_PREFIX)
    assert agent.last_status["status"] == "done"


async def test_checkpoint_restores_the_compacted_context(config, provider):
    config.data["model"]["context_window"] = 12000
    config.data["model"]["max_tokens"] = 2000
    rt = Runtime(config, provider=provider)
    agent = Agent(rt)
    answer_summaries(provider)
    for i in range(12):
        provider.push(f"回答 {i} " + "說明" * 400)
        await agent.run(f"問題 {i} " + "細節" * 400)
    cp = rt.memory.checkpoint(agent.session_id)
    assert cp and cp["upto_idx"] > 0
    full = rt.memory.load_messages(agent.session_id)
    assert len(full) == 24                                 # the transcript itself is never shortened
    again = Agent(rt, agent.session_id)
    assert again.history[0].text().startswith(SUMMARY_PREFIX)
    assert [m.text() for m in again.history if not m.text().startswith(SUMMARY_PREFIX)][-1].startswith("回答 11")
    assert len(again.history) < len(full)
