"""SinoBee.ai subscription models: `sinobee/<vendor>/<model>` (e.g. `sinobee/anthropic/claude-sonnet-5-5`).

Calls go to the sinobee.ai relay with this install's access token and an Ed25519 signature over each request
(see sinobee.account). The relay holds quota before calling the vendor and charges the 6 hour / 7 day windows of
the user's plan, so no vendor API key is needed here. Claude models use the Anthropic format
(`/api/v1/llm/anthropic/v1/messages`, prompt caching), everything else the OpenAI format
(`/api/v1/llm/chat/completions`). When a window is used up the relay answers 429 with `quota: 6h|168h` (402 for
no subscription); that raises QuotaExceeded, which is not retried, so the fallback chain moves on at once.
"""
from __future__ import annotations

import time

from .. import account
from .anthropic import AnthropicProvider
from .base import Provider, ProviderError
from .openai_compat import OpenAICompatProvider

# model id -> format ("anthropic" | "openai"), learned from GET /api/v1/llm/models
_FORMATS: dict[str, str] = {}
_MODELS: dict[str, tuple[float, list[dict]]] = {}
MODELS_TTL = 600


def guess_format(model: str) -> str:
    vendor = model.split("/", 1)[0].lower()
    return _FORMATS.get(model) or ("anthropic" if vendor in ("anthropic", "claude") else "openai")


class SinoBeeProvider(Provider):
    name = "sinobee"

    def __init__(self, model: str, base_url: str, api_key: str | None = None, *, config=None, vision: bool = True,
                 **kw):
        kw.pop("provider_name", None)
        super().__init__(model, base_url, None, provider_name="sinobee", **kw)
        self.config = config
        self.vision = vision
        self._inner: Provider | None = None
        self._inner_fmt: str | None = None

    def _sign(self, method: str, url: str, body: bytes) -> dict:
        return account.relay_headers(self.config, method, url, body)

    def inner(self) -> Provider:
        fmt = guess_format(self.model)
        if self._inner is None or self._inner_fmt != fmt:
            common = {"provider_name": "sinobee", "timeout": self.timeout, "extra_headers": self.extra_headers,
                      "auth": self._sign}
            if fmt == "anthropic":
                self._inner = AnthropicProvider(self.model, f"{self.base_url}/anthropic", None, **common)
            else:
                self._inner = OpenAICompatProvider(self.model, self.base_url, None, vision=self.vision, stream=True,
                                                   **common)
            self._inner_fmt = fmt
        return self._inner

    async def _chat(self, messages, tools, *, temperature, max_tokens):
        if self.model not in _FORMATS and self.model != "_":
            try:
                await self.list_models()
            except ProviderError:
                pass  # guess from the vendor prefix
        return await self.inner()._chat(messages, tools, temperature=temperature, max_tokens=max_tokens)

    async def embed(self, texts):
        p = OpenAICompatProvider(self.model, self.base_url, None, provider_name="sinobee", auth=self._sign)
        p.recorder = getattr(self, "recorder", None)
        return await p.embed(texts)

    async def model_rows(self) -> list[dict]:
        """The relay's catalog (cached): [{id, name, format, price_twd_per_1m, available}]."""
        hit = _MODELS.get(self.base_url)
        if hit and time.time() - hit[0] < MODELS_TTL:
            return hit[1]
        probe = OpenAICompatProvider("_", self.base_url, None, provider_name="sinobee", auth=self._sign)
        data = await probe._get(f"{self.base_url}/models", {})
        rows = [m for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
        for m in rows:
            if m.get("format") in ("anthropic", "openai"):
                _FORMATS[m["id"]] = m["format"]
        _MODELS[self.base_url] = (time.time(), rows)
        return rows

    async def list_models(self) -> list[str]:
        return [m["id"] for m in await self.model_rows() if m.get("available", True)]


def forget() -> None:
    _MODELS.clear()
