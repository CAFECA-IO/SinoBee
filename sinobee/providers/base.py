"""Provider interface, retry and fallback chain."""
from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod

import httpx

from ..types import ChatResponse, Message, ToolSpec

log = logging.getLogger("sinobee.providers")

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class Provider(ABC):
    """A single (provider, model) endpoint."""

    name: str = "base"

    def __init__(self, model: str, base_url: str, api_key: str | None, *, provider_name: str = "",
                 timeout: float = 180.0, extra_headers: dict | None = None):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.provider_name = provider_name or self.name
        self.timeout = timeout
        self.extra_headers = extra_headers or {}

    @property
    def label(self) -> str:
        return f"{self.provider_name}/{self.model}"

    @abstractmethod
    async def _chat(self, messages: list[Message], tools: list[ToolSpec], *, temperature: float | None,
                    max_tokens: int) -> ChatResponse: ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise ProviderError(f"{self.label} does not support embeddings")

    async def list_models(self) -> list[str]:
        return []

    async def chat(self, messages: list[Message], tools: list[ToolSpec] | None = None, *,
                   temperature: float | None = 0.3, max_tokens: int = 4096, retries: int = 4) -> ChatResponse:
        tools = tools or []
        delay = 1.5
        for attempt in range(retries + 1):
            try:
                return await self._chat(messages, tools, temperature=temperature, max_tokens=max_tokens)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                err: ProviderError = ProviderError(f"{self.label} network error: {e!r}", retryable=True)
            except ProviderError as e:
                err = e
            if not err.retryable or attempt == retries:
                raise err
            sleep = delay * (2 ** attempt) + random.random()
            log.warning("%s failed (%s); retrying in %.1fs", self.label, err, sleep)
            await asyncio.sleep(sleep)
        raise ProviderError("unreachable")

    async def _get(self, url: str, headers: dict) -> dict:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, headers={**headers, **self.extra_headers})
        if r.status_code >= 400:
            raise ProviderError(f"{self.label} HTTP {r.status_code}: {r.text[:500]}", r.status_code)
        return r.json()

    async def _post(self, url: str, payload: dict, headers: dict) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(url, json=payload, headers={**headers, **self.extra_headers})
        if r.status_code >= 400:
            body = r.text[:2000]
            raise ProviderError(f"{self.label} HTTP {r.status_code}: {body}", r.status_code,
                                r.status_code in RETRYABLE_STATUS)
        return r.json()


class FallbackChain:
    """Try providers in order; move on when one fails with a non-recoverable error."""

    def __init__(self, providers: list[Provider]):
        if not providers:
            raise ValueError("at least one provider required")
        self.providers = providers
        self.recorder = None  # callable(provider, ChatResponse | None, usage) set by Runtime for cost accounting

    @property
    def primary(self) -> Provider:
        return self.providers[0]

    @property
    def label(self) -> str:
        return self.primary.label

    async def chat(self, messages, tools=None, **kw) -> ChatResponse:
        last: Exception | None = None
        for p in self.providers:
            try:
                resp = await p.chat(messages, tools, **kw)
                resp.model = resp.model or p.label
                if self.recorder:
                    try:
                        self.recorder(p, resp.usage, None)
                    except Exception:
                        log.exception("usage recording failed")
                return resp
            except ProviderError as e:
                log.error("provider %s failed: %s", p.label, e)
                last = e
                # auth / bad request errors on the primary still allow fallback to a different vendor
        raise last or ProviderError("all providers failed")

    async def embed(self, texts):
        return await self.primary.embed(texts)

    async def list_models(self):
        return await self.primary.list_models()
