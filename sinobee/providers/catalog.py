"""Model catalog across every provider the user can use right now (keys set, local server reachable).

The settings UI picks models from this list instead of a free-text "provider/model" field, so adding a key for a
second provider (e.g. Gemini while a local Ollama model is the main model) immediately offers that provider's
models. Listing is live (each provider's /models endpoint), cached briefly, and falls back to a short curated list
when a provider does not answer.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time

from . import PRESETS, build_provider, needs_key, provider_names, resolve_key

LABELS = {
    "sinobee": "SinoBee.ai 訂閱",
    "openai": "OpenAI", "anthropic": "Anthropic", "gemini": "Google Gemini", "openrouter": "OpenRouter",
    "deepseek": "DeepSeek", "groq": "Groq", "mistral": "Mistral", "xai": "xAI", "together": "Together",
    "fireworks": "Fireworks", "moonshot": "Moonshot (Kimi)", "qwen": "Qwen (DashScope)", "zhipu": "Zhipu (GLM)",
    "ollama": "Ollama（本機）", "lmstudio": "LM Studio（本機）", "vllm": "vLLM（自架）",
}
# the providers most people use, shown first; the rest sit under "more providers"
POPULAR = ["sinobee", "anthropic", "openai", "gemini", "ollama", "openrouter", "deepseek"]
LOCAL = {"ollama", "lmstudio", "vllm"}
KEY_URLS = {
    "openai": "https://platform.openai.com/api-keys", "anthropic": "https://console.anthropic.com/settings/keys",
    "gemini": "https://aistudio.google.com/apikey", "openrouter": "https://openrouter.ai/keys",
    "deepseek": "https://platform.deepseek.com/api_keys", "groq": "https://console.groq.com/keys",
    "mistral": "https://console.mistral.ai/api-keys", "xai": "https://console.x.ai",
}

# curated picks: (model id, short note). Shown first and used when live listing fails.
RECOMMENDED: dict[str, list[tuple[str, str]]] = {
    "anthropic": [("claude-sonnet-5-5", "平衡，推薦"), ("claude-opus-5-5", "最強"), ("claude-haiku-5-5", "便宜快速")],
    "sinobee": [("anthropic/claude-sonnet-5-5", "平衡，推薦"), ("openai/gpt-5.6-terra", "OpenAI"),
                ("anthropic/claude-haiku-5-5", "便宜快速")],
    "openai": [("gpt-5.5", "平衡，推薦"), ("gpt-5.4-mini", "便宜快速"), ("gpt-5.5-pro", "最強，昂貴"),
               ("text-embedding-3-small", "記憶搜尋")],
    "gemini": [("gemini-flash-latest", "平衡，推薦"), ("gemini-pro-latest", "最強"),
               ("gemini-flash-lite-latest", "便宜快速"), ("gemini-embedding-2", "記憶搜尋")],
    "deepseek": [("deepseek-v4-flash", "便宜快速"), ("deepseek-v4-pro", "較強")],
    "openrouter": [("anthropic/claude-sonnet-5-5", "經 OpenRouter"), ("google/gemini-flash-latest", "經 OpenRouter")],
}

_NOT_CHAT = re.compile(r"(tts|speech|audio|realtime|transcri|whisper|dall-e|image|imagen|veo|lyria|aqa|moderation|"
                       r"live|robotics|computer-use|search-preview|babbage|davinci|rerank|guard)", re.I)
_EMBED = re.compile(r"embed", re.I)
CACHE_SECONDS = 600
LIST_TIMEOUT = 8
_CACHE: dict[str, tuple[float, dict]] = {}


def kind_of(model_id: str) -> str:
    """chat | embedding | other (image, audio, video, moderation ... models that cannot run the agent)."""
    if _EMBED.search(model_id):
        return "embedding"
    if _NOT_CHAT.search(model_id):
        return "other"
    return "chat"


def _fingerprint(name: str, cfg) -> str:
    key, _ = resolve_key(name, cfg)
    base = ((cfg.get("providers") or {}).get(name) or {}).get("base_url") or ""
    if name == "sinobee":
        from .. import account
        key = (account.account(cfg) or {}).get("account") or ""
        base = account.relay_base(cfg)
    if name == "ollama":
        base = str(cfg.get("local.base_url") or "")
    return hashlib.sha256(f"{name}|{key or ''}|{base}".encode()).hexdigest()[:16]


def _entry(name: str, mid: str, note: str = "", recommended: bool = False) -> dict:
    return {"id": mid, "spec": f"{name}/{mid}", "kind": kind_of(mid), "note": note, "recommended": recommended}


def _order(name: str, ids: list[str]) -> list[dict]:
    rec = RECOMMENDED.get(name, [])
    rec_ids = [r[0] for r in rec]
    out = [_entry(name, mid, note, True) for mid, note in rec if mid in ids]
    rest = sorted((i for i in ids if i not in rec_ids), reverse=True)     # newer versions first, roughly
    return out + [_entry(name, i) for i in rest]


def usable(name: str, cfg) -> bool:
    """Worth asking for models: a key is set, or the provider needs none (local / custom endpoints)."""
    if name == "sinobee":
        from .. import account
        return account.can_relay(cfg)
    if needs_key(name, cfg):
        return bool(resolve_key(name, cfg)[0])
    if name in ("lmstudio", "vllm"):
        return bool(((cfg.get("providers") or {}).get(name) or {}).get("base_url")) or bool(resolve_key(name, cfg)[0])
    return True


async def provider_models(name: str, cfg, *, refresh: bool = False) -> dict:
    """{name, label, status, models, error, has_key, needs_key, key_source, local, popular, key_url}.
    status: ready | no_key | error | offline"""
    key, source = resolve_key(name, cfg)
    info = {"name": name, "label": LABELS.get(name, name), "has_key": bool(key), "key_source": source,
            "needs_key": needs_key(name, cfg), "local": name in LOCAL, "popular": name in POPULAR,
            "custom": name not in PRESETS, "key_url": KEY_URLS.get(name, ""), "models": [], "error": ""}
    if name == "sinobee":
        from .. import account
        info.update(needs_key=False, subscription=True, signed_in=bool(account.account(cfg)),
                    has_scope=account.can_relay(cfg), hub_url=account.base_url(cfg))
    if not usable(name, cfg):
        return {**info, "status": "no_key",
                "models": [_entry(name, m, n, True) for m, n in RECOMMENDED.get(name, [])]}
    fp = _fingerprint(name, cfg)
    hit = _CACHE.get(fp)
    if hit and not refresh and time.time() - hit[0] < CACHE_SECONDS:
        return {**info, **hit[1]}
    try:
        ids = await asyncio.wait_for(build_provider(f"{name}/_", cfg).list_models(), LIST_TIMEOUT)
        ids = [i for i in dict.fromkeys(ids) if i and i != "_"]
        res = {"status": "ready", "models": _order(name, ids), "error": ""}
        if not ids:
            res["error"] = "沒有可用的模型" if name not in LOCAL else "尚未下載任何模型"
    except Exception as e:  # noqa: BLE001 - shown on the provider card
        msg = str(e) or type(e).__name__
        offline = name in LOCAL or isinstance(e, (asyncio.TimeoutError, OSError)) or "network error" in msg
        bad_key = re.search(r"HTTP (401|403)|API key not valid|invalid.{0,10}api.?key|incorrect api key", msg, re.I)
        res = {"status": "offline" if offline and not bad_key else "error",
               "error": ("金鑰無效或沒有權限：" if bad_key else "") + msg[:300],
               "models": [_entry(name, m, n, True) for m, n in RECOMMENDED.get(name, [])] if not bad_key else []}
    _CACHE[fp] = (time.time(), res)
    return {**info, **res}


async def catalog(cfg, *, refresh: bool = False, only: list[str] | None = None) -> dict:
    names = only or provider_names(cfg)
    rows = await asyncio.gather(*(provider_models(n, cfg, refresh=refresh) for n in names))
    rank = {"ready": 0, "error": 1, "offline": 2, "no_key": 3}
    rows = sorted(rows, key=lambda r: (rank[r["status"]], POPULAR.index(r["name"]) if r["name"] in POPULAR else 99))
    return {"providers": rows, "at": time.time()}


def forget(name: str | None = None) -> None:
    """Drop cached listings (after a key change)."""
    if name is None:
        _CACHE.clear()
        return
    for k in list(_CACHE):
        _CACHE.pop(k, None)


def suggest_default(entry: dict) -> str | None:
    """The model to offer as main model right after a provider is connected."""
    chat = [m for m in entry.get("models", []) if m["kind"] == "chat"]
    rec = [m for m in chat if m["recommended"]]
    return (rec or chat or [{}])[0].get("spec")
