"""Model price table (USD per 1M tokens) and cost calculation.

Built-in prices are reference values checked on PRICES_CHECKED against the vendors' public pricing pages.
Vendors change prices often: every entry can be overridden in the Web UI (Settings -> Pricing), which is stored
under `pricing` in settings.json. Overrides match by "provider/model", "model", or a model-name prefix.

Fields: input, output, cache_read (cached input), cache_write (Anthropic prompt-cache writes),
        long: {threshold, input, output, cache_read, cache_write} for long-context tiers.
"""
from __future__ import annotations

import re

PRICES_CHECKED = "2026-10-13"

PRICES: dict[str, dict] = {
    # --- OpenAI ---
    "gpt-5.6-sol": {"input": 5.00, "cache_read": 0.50, "output": 30.00},
    "gpt-5.6-terra": {"input": 2.00, "cache_read": 0.20, "output": 12.00},
    "gpt-5.6-luna": {"input": 0.20, "cache_read": 0.02, "output": 1.20},
    "gpt-5.5-pro": {"input": 30.00, "output": 180.00},
    "gpt-5.5": {"input": 5.00, "cache_read": 0.50, "output": 30.00},
    "gpt-5.4-pro": {"input": 30.00, "output": 180.00},
    "gpt-5.4-mini": {"input": 0.75, "cache_read": 0.075, "output": 4.50},
    "gpt-5.4-nano": {"input": 0.20, "cache_read": 0.02, "output": 1.25},
    "gpt-5.4": {"input": 2.50, "cache_read": 0.25, "output": 15.00},
    "gpt-5-mini": {"input": 0.25, "cache_read": 0.025, "output": 2.00},
    "gpt-5-nano": {"input": 0.05, "cache_read": 0.005, "output": 0.40},
    "gpt-5": {"input": 1.25, "cache_read": 0.125, "output": 10.00},
    "gpt-4.1-mini": {"input": 0.40, "cache_read": 0.10, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "cache_read": 0.025, "output": 0.40},
    "gpt-4.1": {"input": 2.00, "cache_read": 0.50, "output": 8.00},
    "gpt-4o-mini": {"input": 0.15, "cache_read": 0.075, "output": 0.60},
    "gpt-4o": {"input": 2.50, "cache_read": 1.25, "output": 10.00},
    "o4-mini": {"input": 1.10, "cache_read": 0.275, "output": 4.40},
    "o3-mini": {"input": 1.10, "cache_read": 0.55, "output": 4.40},
    "o3": {"input": 2.00, "cache_read": 0.50, "output": 8.00},
    "o1": {"input": 15.00, "cache_read": 7.50, "output": 60.00},
    "text-embedding-3-small": {"input": 0.02, "output": 0},
    "text-embedding-3-large": {"input": 0.13, "output": 0},
    # --- Anthropic ---
    "claude-fable-5-1": {"input": 10, "cache_write": 12.5, "cache_read": 0.25, "output": 50},
    "claude-mythos-5-1": {"input": 10, "cache_write": 12.5, "cache_read": 0.25, "output": 50},
    "claude-fable-5": {"input": 10, "cache_write": 12.5, "cache_read": 1.0, "output": 50},
    "claude-mythos-5": {"input": 10, "cache_write": 12.5, "cache_read": 1.0, "output": 50},
    "claude-opus-5-5": {"input": 4, "cache_write": 5, "cache_read": 0.20, "output": 20},
    "claude-sonnet-5-5": {"input": 2, "cache_write": 2.5, "cache_read": 0.10, "output": 10},
    "claude-haiku-5-5": {"input": 0.10, "cache_write": 0.125, "cache_read": 0.01, "output": 0.50,
                         "long": {"threshold": 100_000, "input": 0.50, "cache_write": 0.625, "cache_read": 0.05,
                                  "output": 2.50}},
    "claude-opus-5": {"input": 5, "cache_write": 6.25, "cache_read": 0.50, "output": 25},
    "claude-sonnet-5": {"input": 2, "cache_write": 2.5, "cache_read": 0.20, "output": 10},
    "claude-opus-4-8": {"input": 5, "cache_write": 6.25, "cache_read": 0.50, "output": 25},
    "claude-opus-4-7": {"input": 5, "cache_write": 6.25, "cache_read": 0.50, "output": 25},
    "claude-opus-4-6": {"input": 5, "cache_write": 6.25, "cache_read": 0.50, "output": 25},
    "claude-opus-4-5": {"input": 5, "cache_write": 6.25, "cache_read": 0.50, "output": 25},
    "claude-opus-4-1": {"input": 15, "cache_write": 18.75, "cache_read": 1.50, "output": 75},
    "claude-opus-4": {"input": 15, "cache_write": 18.75, "cache_read": 1.50, "output": 75},
    "claude-sonnet-4": {"input": 3, "cache_write": 3.75, "cache_read": 0.30, "output": 15},  # 4, 4.5, 4.6
    "claude-haiku-4-5": {"input": 1, "cache_write": 1.25, "cache_read": 0.10, "output": 5},
    "claude-3-5-haiku": {"input": 0.80, "cache_write": 1.0, "cache_read": 0.08, "output": 4},
    # --- Google Gemini (paid tier, <=200k prompt) ---
    # "-latest" aliases point at Google's current generation (verify after Google re-points them)
    "gemini-pro-latest": {"input": 2.00, "cache_read": 0.20, "output": 12.00},
    "gemini-flash-latest": {"input": 0.75, "cache_read": 0.075, "output": 3.75},
    "gemini-flash-lite-latest": {"input": 0.30, "cache_read": 0.03, "output": 2.50},
    "gemini-3.8-flash": {"input": 0.75, "cache_read": 0.075, "output": 3.75},
    "gemini-3.6-flash": {"input": 0.75, "cache_read": 0.075, "output": 3.75},
    "gemini-3.5-flash-lite": {"input": 0.30, "cache_read": 0.03, "output": 2.50},
    "gemini-3.1-pro": {"input": 2.00, "cache_read": 0.20, "output": 12.00},
    "gemini-3.1-flash-lite": {"input": 0.25, "cache_read": 0.025, "output": 1.50},
    "gemini-3-flash": {"input": 0.50, "cache_read": 0.05, "output": 3.00},
    "gemini-2.5-pro": {"input": 1.25, "cache_read": 0.125, "output": 10.00,
                       "long": {"threshold": 200_000, "input": 2.50, "cache_read": 0.25, "output": 15.00}},
    "gemini-2.5-flash-lite": {"input": 0.10, "cache_read": 0.01, "output": 0.40},
    "gemini-2.5-flash": {"input": 0.30, "cache_read": 0.03, "output": 2.50},
    "gemini-embedding-2": {"input": 0.20, "output": 0},
    # --- DeepSeek (off-peak list price; peak hours cost 2x) ---
    "deepseek-flash": {"input": 0.15, "cache_read": 0.003, "output": 0.60},
    "deepseek-v4-flash": {"input": 0.15, "cache_read": 0.003, "output": 0.60},
    "deepseek-v4-pro": {"input": 0.66, "cache_read": 0.022, "output": 1.98},
}

FREE_PROVIDERS = {"ollama", "lmstudio", "vllm"}  # self-hosted: no per-token charge


def _norm(model: str) -> str:
    m = model.lower().split("/")[-1]          # openrouter style "anthropic/claude-..." -> "claude-..."
    m = m.removeprefix("models/")
    return re.sub(r"(?<=\d)\.(?=\d)", "-", m) if m.startswith("claude") else m


def _norm_key(key: str) -> str:
    return _norm(key)


def price_for(provider: str, model: str, overrides: dict | None = None) -> tuple[dict | None, str]:
    """Return (price, source) where source is override | builtin | free | unknown."""
    overrides = overrides or {}
    full = f"{provider}/{model}"
    for k in (full, model):
        if k in overrides:
            return overrides[k], "override"
    nm = _norm(model)
    best = None
    for k, v in overrides.items():
        nk = _norm_key(k)
        if nm.startswith(nk) and (best is None or len(nk) > len(best[0])):
            best = (nk, v)
    if best:
        return best[1], "override"
    if provider in FREE_PROVIDERS:
        return {"input": 0, "output": 0}, "free"
    for k in sorted(PRICES, key=len, reverse=True):
        if nm.startswith(_norm_key(k)):
            return PRICES[k], "builtin"
    return None, "unknown"


def cost_usd(price: dict | None, input_tokens: int, output_tokens: int, cache_read: int = 0,
             cache_write: int = 0) -> float | None:
    """input_tokens is the TOTAL prompt size (including cached/cache-write tokens)."""
    if price is None:
        return None
    p = price
    long = price.get("long")
    if long and input_tokens > long.get("threshold", 10**12):
        p = {**price, **long}
    inp = float(p.get("input", 0))
    uncached = max(0, input_tokens - cache_read - cache_write)
    total = (uncached * inp
             + cache_read * float(p.get("cache_read", inp))
             + cache_write * float(p.get("cache_write", inp))
             + output_tokens * float(p.get("output", 0)))
    return total / 1_000_000


def table(overrides: dict | None = None) -> list[dict]:
    rows = [{"model": k, **{f: v.get(f) for f in ("input", "cache_read", "cache_write", "output")},
             "long": v.get("long"), "source": "builtin"} for k, v in PRICES.items()]
    for k, v in (overrides or {}).items():
        rows.insert(0, {"model": k, **{f: v.get(f) for f in ("input", "cache_read", "cache_write", "output")},
                        "long": v.get("long"), "source": "override"})
    return rows
