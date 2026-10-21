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
        h = account.relay_headers(self.config, method, url, body)
        from ..billing import PAYING
        if PAYING.get():                    # paid with platform points (the user agreed; sinobee/billing.py)
            h["x-sinobee-billing"] = PAYING.get()
        return h

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


class SinoBeeAutoProvider:
    """`sinobee/auto` (`sinobee/auto@thinking`, ...): SinoBee.ai picks the model per turn and role by the user's
    preference (routing.py; plan P4). One model for the whole turn. Never built while a model is pinned."""

    name = "sinobee"
    provider_name = "sinobee"
    model = "auto"

    def __init__(self, config, role: str = "main", *, vision: bool = True):
        from ..routing import role_of
        self.config = config
        self.role = role_of(f"x@{role}") if "@" not in role else role_of(role)
        self.vision = vision
        self.recorder = None
        self.base_url = account.relay_base(config)
        self.timeout = 180.0
        self._inners: dict[str, SinoBeeProvider] = {}

    @property
    def label(self) -> str:
        return "sinobee/auto" if self.role == "main" else f"sinobee/auto@{self.role}"

    def _inner(self, model: str) -> SinoBeeProvider:
        if model not in self._inners:
            self._inners[model] = SinoBeeProvider(model, self.base_url, config=self.config, vision=self.vision)
        return self._inners[model]

    async def pick(self, messages=None, tools=None, exclude: list[str] | None = None) -> dict:
        import asyncio

        from .. import routing
        needs = {"tools": bool(tools), "vision": any(getattr(m, "has_images", lambda: False)() for m in messages or [])}
        return await asyncio.to_thread(routing.ask, self.config, self.role, needs, exclude)

    @staticmethod
    def vendor_failure(e: Exception) -> bool:
        """The picked model's vendor cannot answer (taken out by SinoBee.ai, or down): another model can.
        Not: the allowance (QuotaExceeded), our own sign-in, a request the model rejects, the relay unreachable."""
        from .base import QuotaExceeded
        msg = str(e)
        if isinstance(e, QuotaExceeded) or "sign-in expired" in msg or "network error" in msg:
            return False
        return "vendor_down" in msg or getattr(e, "status", None) in (502, 503, 504)

    async def chat(self, messages, tools=None, **kw):
        """One model for the turn. When its vendor fails, forget the pick and ask again without it - in the same
        step, so the task goes on; the result says which model took over and why (at most two replacements)."""
        from .. import routing
        from ..accounting import current_scope
        from .base import TOO_LONG, ProviderError, is_timeout
        failed: list[dict] = []
        for attempt in range(3):
            try:
                pick = await self.pick(messages, tools, [f["model"] for f in failed] or None)
            except RuntimeError as e:              # nobody else to ask: the first failure stands
                raise ProviderError(f"{self.label}: {failed[-1]['why'] if failed else e}") from e
            inner = self._inner(pick["model"])
            try:
                resp = await inner.chat(messages, tools, **kw)
            except ProviderError as e:
                if attempt == 2 or not self.vendor_failure(e) or (TOO_LONG.get() and is_timeout(e)):
                    raise
                routing.forget(self.role)
                failed.append({"model": inner.label, "why": str(e).split(": ", 1)[-1][:160]})
                continue
            resp.model = inner.label
            resp.served_by = inner                 # usage is recorded for the model that answered
            reason = pick.get("reason", "")
            if failed:
                reason = f"已改用 {inner.label.removeprefix('sinobee/')}（{'、'.join(f['model'].removeprefix('sinobee/') for f in failed)} 暫時無法使用）"
            current_scope.get().route_note(self.role, inner.label, reason, pick.get("preference"), failed)
            return resp
        raise ProviderError(f"{self.label}: no model answered")

    async def embed(self, texts):
        import asyncio

        from .. import routing
        pick = await asyncio.to_thread(routing.ask, self.config, "embed", {})
        return await self._inner(pick["model"]).embed(texts)

    async def list_models(self):
        return await SinoBeeProvider("_", self.base_url, config=self.config).list_models()
