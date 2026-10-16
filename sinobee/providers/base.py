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


class QuotaExceeded(ProviderError):
    """SinoBee.ai subscription limit reached (6 h / 7 day window, or no active subscription).
    Not retryable: the fallback chain moves on to the next model at once."""

    def __init__(self, message: str, window: str, retry_at: str | None = None, status: int | None = 429):
        super().__init__(message, status, retryable=False)
        self.window = window
        self.retry_at = retry_at


QUOTA_WINDOWS = {"6h", "168h", "subscription"}


def relay_error(label: str, status: int, text: str) -> ProviderError:
    """Turn a sinobee.ai relay error into ProviderError / QuotaExceeded (quota stops are not retried)."""
    import json as _json
    try:
        body = _json.loads(text)
    except ValueError:
        body = {}
    err = body.get("error") if isinstance(body, dict) else None
    err = err if isinstance(err, dict) else {}
    window = err.get("quota") or err.get("code")
    message = str(err.get("message") or text[:500])
    if window in QUOTA_WINDOWS:
        return QuotaExceeded(f"{label} SinoBee.ai quota {window}: {message}", window, err.get("retry_at"), status)
    if status == 401:
        return ProviderError(f"{label} HTTP 401 SinoBee.ai sign-in expired: {message}", status)
    return ProviderError(f"{label} HTTP {status}: {message}", status, status in RETRYABLE_STATUS)


def _note_quota(headers) -> None:
    """Record the relay's quota headers on the current usage scope (per answer: share of the 6-hour limit)."""
    pct = headers.get("x-sinobee-cost-6h-pct")
    if pct is None:
        return
    from ..accounting import current_scope
    sc = current_scope.get()
    try:
        sc.quota_6h_pct += float(pct)
        sc.quota = {"6h": float(headers.get("x-sinobee-quota-6h") or 0),
                    "168h": float(headers.get("x-sinobee-quota-168h") or 0)}
    except ValueError:
        pass


class Provider(ABC):
    """A single (provider, model) endpoint."""

    name: str = "base"

    def __init__(self, model: str, base_url: str, api_key: str | None, *, provider_name: str = "",
                 timeout: float = 180.0, extra_headers: dict | None = None, auth=None):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.provider_name = provider_name or self.name
        self.timeout = timeout
        self.extra_headers = extra_headers or {}
        # auth(method, url, body) -> headers, for endpoints that need per-request credentials and a signature over
        # the exact body (the sinobee.ai relay); called in a worker thread because it may refresh the token
        self.auth = auth

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

    async def _auth_headers(self, method: str, url: str, body: bytes) -> dict:
        if not self.auth:
            return {}
        try:
            return await asyncio.to_thread(self.auth, method, url, body)
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001 - AccountError (not signed in) and friends
            raise ProviderError(f"{self.label} {e}", 401) from None

    async def _get(self, url: str, headers: dict) -> dict:
        if self.auth:
            headers = {k: v for k, v in {**headers, **self.extra_headers}.items()
                       if k.lower() not in ("x-api-key", "authorization")}
            headers.update(await self._auth_headers("GET", url, b""))
        else:
            headers = {**headers, **self.extra_headers}
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, headers=headers)
        if r.status_code >= 400:
            if self.auth:
                raise relay_error(self.label, r.status_code, r.text)
            raise ProviderError(f"{self.label} HTTP {r.status_code}: {r.text[:500]}", r.status_code)
        return r.json()

    async def _post(self, url: str, payload: dict, headers: dict) -> dict:
        if self.auth:
            import json as _json
            body = _json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            keep = {k: v for k, v in {**headers, **self.extra_headers}.items()
                    if k.lower() not in ("x-api-key", "authorization", "content-type")}
            headers = {**keep, "Content-Type": "application/json", **await self._auth_headers("POST", url, body)}
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(url, content=body, headers=headers)
            if r.status_code >= 400:
                raise relay_error(self.label, r.status_code, r.text[:2000])
            _note_quota(r.headers)
            return r.json()
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
                if isinstance(e, QuotaExceeded):
                    from ..accounting import current_scope
                    i = self.providers.index(p)
                    nxt = self.providers[i + 1].label if i + 1 < len(self.providers) else None
                    current_scope.get().quota_events.append(
                        {"window": e.window, "retry_at": e.retry_at, "model": p.label, "fallback": nxt})
                # auth / bad request errors on the primary still allow fallback to a different vendor
        raise last or ProviderError("all providers failed")

    async def embed(self, texts):
        return await self.primary.embed(texts)

    async def list_models(self):
        return await self.primary.list_models()
