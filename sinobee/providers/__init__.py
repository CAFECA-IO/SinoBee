"""Provider presets and resolution of `provider/model` strings."""
from __future__ import annotations

import os

from ..config import Config
from .anthropic import AnthropicProvider
from .base import FallbackChain, Provider, ProviderError
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatProvider
from .sinobee import SinoBeeProvider

# name: (transport, base_url, api_key_env)
PRESETS: dict[str, tuple[str, str, str | None]] = {
    "sinobee": ("sinobee", "", None),  # SinoBee.ai subscription (relay; signed in, no vendor key)
    "openai": ("openai", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    "anthropic": ("anthropic", "https://api.anthropic.com", "ANTHROPIC_API_KEY"),
    "openrouter": ("openai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "gemini": ("openai", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "groq": ("openai", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "deepseek": ("openai", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "mistral": ("openai", "https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "xai": ("openai", "https://api.x.ai/v1", "XAI_API_KEY"),
    "together": ("openai", "https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    "fireworks": ("openai", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY"),
    "moonshot": ("openai", "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY"),
    "qwen": ("openai", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
    "zhipu": ("openai", "https://open.bigmodel.cn/api/paas/v4", "ZHIPU_API_KEY"),
    "ollama": ("ollama", "", None),  # base URL auto-discovered (see sinobee.local.ollama)
    "lmstudio": ("openai", "http://localhost:1234/v1", None),
    "vllm": ("openai", "http://localhost:8000/v1", "VLLM_API_KEY"),
}
ALIASES = {"google": "gemini", "claude": "anthropic", "dashscope": "qwen", "grok": "xai"}
TRANSPORTS = {"openai": OpenAICompatProvider, "anthropic": AnthropicProvider, "ollama": OllamaProvider,
              "sinobee": SinoBeeProvider}


def key_env_for(prov: str, config: Config) -> str | None:
    override = (config.get("providers") or {}).get(prov, {}) or {}
    if "api_key_env" in override:
        return override["api_key_env"]
    if prov in PRESETS:
        return PRESETS[prov][2]
    return f"{prov.upper()}_API_KEY"


def needs_key(prov: str, config: Config) -> bool:
    """Local servers (ollama, lmstudio) need no key; custom endpoints may work without one."""
    return prov in PRESETS and PRESETS[prov][2] is not None and prov != "vllm"


def usable_now(prov: str, config: Config) -> bool:
    """Can calls to this provider be made: a key when one is needed; for sinobee, signed in with the llm scope."""
    if prov == "sinobee":
        from .. import account
        return account.can_relay(config)
    return not needs_key(prov, config) or bool(resolve_key(prov, config)[0])


def resolve_key(prov: str, config: Config) -> tuple[str | None, str | None]:
    """Return (key, source) where source is env | gui | config | None.  Startup env wins (locked in GUI)."""
    env_name = key_env_for(prov, config)
    if env_name and os.environ.get(env_name):
        return os.environ[env_name], "env"
    stored = config.secrets.get(prov)
    if stored:
        return stored, "gui"
    override = (config.get("providers") or {}).get(prov, {}) or {}
    if override.get("api_key"):
        return override["api_key"], "config"
    return None, None


def provider_names(config: Config) -> list[str]:
    names = list(PRESETS)
    for n in list((config.get("providers") or {}).keys()) + list(config.secrets.api_keys().keys()):
        if n not in names:
            names.append(n)
    return names


def split_model(spec: str) -> tuple[str, str]:
    if "/" not in spec:
        raise ValueError(f"model must be 'provider/model', got {spec!r}")
    prov, model = spec.split("/", 1)
    prov = prov.lower()
    return ALIASES.get(prov, prov), model


def build_provider(spec: str, config: Config) -> Provider:
    prov, model = split_model(spec)
    override = (config.get("providers") or {}).get(prov, {}) or {}
    transport, base_url, key_env = PRESETS.get(prov, (None, None, None))
    transport = override.get("transport", transport or "openai")
    base_url = override.get("base_url") or os.environ.get(f"{prov.upper()}_BASE_URL") or base_url
    if transport == "sinobee":
        from .. import account
        return SinoBeeProvider(model, override.get("base_url") or account.relay_base(config), config=config,
                               extra_headers=override.get("headers"),
                               vision=bool(override.get("vision", config.get("model.vision", True))))
    if prov == "ollama" and transport == "ollama" and not base_url:
        from ..local.ollama import resolve_url
        base_url = resolve_url(config)[0]
    if not base_url:
        raise ProviderError(f"unknown provider '{prov}': set providers.{prov}.base_url in config.yaml")
    api_key, _ = resolve_key(prov, config)
    cls = TRANSPORTS[transport]
    kwargs = {"provider_name": prov, "extra_headers": override.get("headers")}
    if cls is OpenAICompatProvider:
        kwargs["vision"] = bool(override.get("vision", config.get("model.vision", True)))
        # streamed by default: bytes keep arriving while the model writes, so the read timeout only fires when the
        # service goes quiet - not while a long answer (a whole page) is still being written. providers.<name>.stream
        # or model.stream: false turns it off for a service whose stream does not work.
        kwargs["stream"] = bool(override.get("stream", config.get("model.stream", True)))
    if cls is OllamaProvider:
        from .. import local
        kwargs.update(vision=bool(override.get("vision", config.get("model.vision", True))),
                      num_ctx=local.num_ctx(config), keep_alive=str(config.get("local.keep_alive", "30m")),
                      think=config.get("local.think"))
    return cls(model, base_url, api_key, **kwargs)


def build_chain(config: Config, spec: str | None = None) -> FallbackChain:
    specs = [spec or config.get("model.default")] + list(config.get("model.fallbacks") or [])
    if not specs[0]:
        raise ProviderError("no model selected yet")
    return FallbackChain([build_provider(s, config) for s in specs if s])


def build_auxiliary(config: Config) -> FallbackChain:
    aux = config.get("model.auxiliary")
    return build_chain(config, aux) if aux else build_chain(config)


def build_embedder(config: Config) -> Provider | None:
    spec = config.get("model.embedding")
    return build_provider(spec, config) if spec else None


__all__ = ["Provider", "ProviderError", "FallbackChain", "PRESETS", "build_provider", "build_chain",
           "build_auxiliary", "build_embedder", "split_model", "resolve_key", "key_env_for", "needs_key",
           "provider_names", "usable_now", "SinoBeeProvider"]
