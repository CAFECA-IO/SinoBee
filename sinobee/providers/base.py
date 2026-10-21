"""Provider interface, retry and fallback chain."""
from __future__ import annotations

import asyncio
import contextvars
import logging
import random
import re
import time
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


QUOTA_WINDOWS = {"6h", "168h", "subscription", "points"}


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


def _scope_add(model_seconds: float = 0.0, retry: dict | None = None) -> None:
    """Per-step accounting (agent/loop.py): time spent on models and calls sent again, on the current usage scope."""
    try:
        from ..accounting import current_scope
        sc = current_scope.get()
        if sc is None:
            return
        sc.model_seconds += model_seconds
        if retry:
            sc.retries += 1
            sc.retry_events.append(retry)
            del sc.retry_events[:-200]
    except Exception:  # noqa: BLE001 - accounting must never break a call
        pass


def _gateway_timeout(err: ProviderError) -> bool:
    """A proxy in front of the model gave up waiting (504 / Cloudflare 524), as opposed to the model failing."""
    if err.status in (504, 524):
        return True
    text = str(err).lower()
    return "gateway time-out" in text or "gateway timeout" in text or "a timeout occurred" in text


# How hard a reasoning model should think on this call ("low" ...), or None for its own default. Set by the agent
# loop around a step (proposal U3: after a reply was cut off at the output limit, thinking is turned down so the
# answer fits); providers that support it send it, the others ignore it.
REASONING_EFFORT: contextvars.ContextVar = contextvars.ContextVar("reasoning_effort", default=None)
# U4: the call right after a reply cut off at the output limit. A timeout there means "too long", not a network
# problem: the same request is not sent again (no retry, no fallback model) - the agent asks for the file in parts.
TOO_LONG: contextvars.ContextVar = contextvars.ContextVar("too_long", default=False)


def is_timeout(err) -> bool:
    """A call that ran out of time (the read timeout, or a proxy's gateway timeout)."""
    return bool(re.search(r"timed out|ReadTimeout|HTTP 5(04|24)\b|gateway time-?out", str(err), re.I))


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
        from . import compat
        tools = tools or []
        delay = 1.5
        timeouts = 0
        if temperature is not None and "temperature" in compat.dropped(self.label):
            temperature = None            # learned earlier: this model does not accept it
        adapted = False
        attempt = 0
        t0 = time.monotonic()
        try:
            return await self._chat_retrying(messages, tools, temperature, max_tokens, retries, delay, timeouts,
                                             adapted, attempt)
        finally:
            _scope_add(model_seconds=time.monotonic() - t0)

    async def _chat_retrying(self, messages, tools, temperature, max_tokens, retries, delay, timeouts, adapted,
                             attempt) -> ChatResponse:
        from . import compat
        while True:
            try:
                return await self._chat(messages, tools, temperature=temperature, max_tokens=max_tokens)
            except httpx.TimeoutException as e:
                # a request that already ran into the timeout usually does so again: retry it once, not four times
                err: ProviderError = ProviderError(f"{self.label} timed out: {e!r}",
                                                   retryable=timeouts == 0 and not TOO_LONG.get())
                timeouts += 1
            except httpx.TransportError as e:
                err = ProviderError(f"{self.label} network error: {e!r}", retryable=True)
            except ProviderError as e:
                sent = {"temperature": temperature, "reasoning_effort": REASONING_EFFORT.get()}
                param = None if adapted else compat.rejected_param(e, sent)
                if param in ("temperature", "reasoning_effort"):
                    # the model rejects a parameter that only tunes the answer: learn it and send the same call
                    # again without it, at once - this is not counted as a retry (a learned reasoning_effort is
                    # left out by the provider from now on)
                    compat.learn(self.label, param, str(e))
                    compat.note(self.label, param)
                    if param == "temperature":
                        temperature = None
                    adapted = True
                    continue
                err = e
            if err.retryable and _gateway_timeout(err):
                # a proxy cut the call because no answer came within its limit (Cloudflare ~100 s, nginx 60 s). The
                # same call takes as long again, and the relay may still be finishing (and charging) the first one:
                # retry it once, like a timeout, not four times
                err.retryable = timeouts == 0 and not TOO_LONG.get()
                timeouts += 1
            if not err.retryable or attempt >= retries:
                raise err
            sleep = delay * (2 ** attempt) + random.random()
            _scope_add(retry={"model": self.label, "status": err.status, "error": str(err)[:160]})
            log.warning("%s failed (%s); retrying in %.1fs", self.label, err, sleep)
            await asyncio.sleep(sleep)
            attempt += 1

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

    def call_timeout(self, max_tokens: int | None) -> float:
        """Seconds to wait for an answer of up to `max_tokens`. A model that is not streamed sends nothing until it has
        finished thinking and writing, so a long answer (a whole HTML page, 16-32k tokens with hidden reasoning) needs
        more than the default: about 50 tokens/s, never less than the configured timeout."""
        return max(float(self.timeout), 60.0 + int(max_tokens or 0) / 50.0)

    async def _post(self, url: str, payload: dict, headers: dict, timeout: float | None = None) -> dict:
        """POST a JSON request. With payload["stream"] the answer is read as server-sent events (_read_stream) and
        returned in the same shape as a plain answer: bytes keep arriving while the model writes, so a proxy that cuts
        requests which stay silent for ~100 s (Cloudflare in front of the SinoBee.ai relay) never cuts a long answer."""
        import json as _json
        stream = bool(payload.get("stream"))
        body = _json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if self.auth:
            keep = {k: v for k, v in {**headers, **self.extra_headers}.items()
                    if k.lower() not in ("x-api-key", "authorization", "content-type")}
            headers = {**keep, "Content-Type": "application/json", **await self._auth_headers("POST", url, body)}
        else:
            headers = {**{k: v for k, v in headers.items() if k.lower() != "content-type"}, **self.extra_headers,
                       "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
            if not stream:
                r = await client.post(url, content=body, headers=headers)
                if r.status_code >= 400:
                    raise self._http_error(r.status_code, r.text[:2000])
                self._relay_headers(r.headers)
                return r.json()
            async with client.stream("POST", url, content=body, headers=headers) as r:
                if r.status_code >= 400:
                    raise self._http_error(r.status_code, (await r.aread()).decode("utf-8", "replace")[:2000])
                self._relay_headers(r.headers)
                if "text/event-stream" not in r.headers.get("content-type", ""):
                    return _json.loads(await r.aread())          # a server that answered without streaming
                return await self._read_stream(r)

    def _http_error(self, status: int, text: str) -> ProviderError:
        if self.auth:
            return relay_error(self.label, status, text)
        return ProviderError(f"{self.label} HTTP {status}: {text}", status, status in RETRYABLE_STATUS)

    def _relay_headers(self, headers) -> None:
        if not self.auth:
            return
        _note_quota(headers)
        dropped = headers.get("x-sinobee-dropped-params")
        if dropped:                      # the relay had to leave a parameter out: stop sending it from here too
            from . import compat
            for prm in dropped.split(","):
                if prm.strip() in compat.OPTIONAL and prm.strip() not in compat.dropped(self.label):
                    compat.learn(self.label, prm.strip(), "the SinoBee.ai relay left it out (x-sinobee-dropped-params)")
                    compat.note(self.label, prm.strip())

    async def _read_stream(self, r) -> dict:
        raise ProviderError(f"{self.label}: streaming is not supported by this provider")

    async def _sse(self, r):
        """The JSON events of a server-sent event stream. The relay's quota for the answer arrives as an SSE comment
        (": x-sinobee-quota {...}") at the end, since headers are sent before the answer exists."""
        import json as _json
        async for line in r.aiter_lines():
            if line.startswith(": x-sinobee-quota "):
                try:
                    self._relay_headers({k.lower(): str(v) for k, v in _json.loads(line[18:]).items()})
                except (ValueError, AttributeError):
                    pass
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                ev = _json.loads(data)
            except ValueError:
                continue
            if isinstance(ev, dict):
                yield ev


class FallbackChain:
    """Try providers in order; move on when one fails with a non-recoverable error."""

    def __init__(self, providers: list[Provider], config=None):
        if not providers:
            raise ValueError("at least one provider required")
        self.providers = providers
        self.config = config  # for billing: who pays once the subscription allowance runs out (sinobee/billing.py)
        self.recorder = None  # callable(provider, ChatResponse | None, usage) set by Runtime for cost accounting

    @property
    def primary(self) -> Provider:
        return self.providers[0]

    @property
    def label(self) -> str:
        return self.primary.label

    def pool(self) -> list[Provider]:
        """The user's own keys and local models in this chain (the fallback pool after the subscription)."""
        return [p for p in self.providers if getattr(p, "provider_name", "") != "sinobee"]

    async def ordered_pool(self) -> list[Provider]:
        """The pool in the order to try: a pinned model's own-key twin first (the pin is kept through fallback);
        otherwise as the user's preference ranks it (routing.order_pool; unchanged when that is not available)."""
        pool = self.pool()
        if self.config is None:
            return pool
        from .. import routing
        pinned = routing.pin(self.config)
        if pinned:
            want = pinned.removeprefix("sinobee/").lower()
            if not any(p.label.lower() == want for p in pool):
                try:                       # the pinned model on your own key, when you have that vendor's key
                    from . import build_provider, usable_now
                    if usable_now(want.split("/", 1)[0], self.config):
                        pool = [build_provider(want, self.config)] + pool
                except Exception:  # noqa: BLE001
                    pass
            return sorted(pool, key=lambda p: 0 if p.label.lower() == want else 1)
        if len(pool) < 2:
            return pool
        import asyncio
        order = await asyncio.to_thread(routing.order_pool, self.config, [p.label for p in pool])
        return sorted(pool, key=lambda p: order.index(p.label) if p.label in order else len(order))

    async def _try(self, providers, messages, tools, kw, source: str | None):
        """Call providers in order. Returns (response, None) or (None, last error); a QuotaExceeded from the relay
        ends the relay part at once (every relay model shares the allowance)."""
        last: Exception | None = None
        for p in providers:
            try:
                resp = await p.chat(messages, tools, **kw)
                resp.model = resp.model or p.label
                if self.recorder:
                    try:
                        self.recorder(getattr(resp, "served_by", None) or p, resp.usage, None)
                    except Exception:
                        log.exception("usage recording failed")
                from ..accounting import current_scope
                sc = current_scope.get()
                sc.served.append(resp.model)
                del sc.served[:-20]
                if source:
                    sc.billing_note(source, p.label)
                return resp, None
            except ProviderError as e:
                log.error("provider %s failed: %s", p.label, e)
                last = e
                if TOO_LONG.get() and is_timeout(e):
                    raise                   # too long for any model: the agent splits the work instead (U4)
                if isinstance(e, QuotaExceeded):
                    return None, e
                # auth / bad request errors on the primary still allow fallback to a different vendor
        return None, last

    async def chat(self, messages, tools=None, **kw) -> ChatResponse:
        from .. import billing
        relay = [p for p in self.providers if getattr(p, "provider_name", "") == "sinobee"]
        pool = self.pool()
        if not relay:                                   # your own keys / local models only: nothing to decide
            resp, err = await self._try(self.providers, messages, tools, kw, None)
            if resp:
                return resp
            raise err or ProviderError("all providers failed")
        st = billing.active(self.config)
        src = st.get("source") if st else billing.SUBSCRIPTION
        quota: QuotaExceeded | None = None
        if src == billing.SUBSCRIPTION:
            resp, err = await self._try(relay, messages, tools, kw, None)
            if resp:
                return resp
            if not isinstance(err, QuotaExceeded):
                if pool:                                # a broken relay (not the allowance): your models, as before
                    resp, err2 = await self._try(await self.ordered_pool(), messages, tools, kw, None)
                    if resp:
                        return resp
                    err = err2 or err
                raise err or ProviderError("all providers failed")
            quota = err
            src = billing.decide(self.config, has_pool=bool(pool))
            if src == "stop":
                self._quota_event(quota, None, ask=True)
                raise quota
            billing.choose(self.config, src, window=quota.window, retry_at=quota.retry_at)
            self._quota_event(quota, src, ask=False)
        if src == billing.POINTS:
            tok = billing.PAYING.set(billing.POINTS)
            try:
                resp, err = await self._try(relay, messages, tools, kw, billing.POINTS)
            finally:
                billing.PAYING.reset(tok)
            if resp:
                return resp
            if not isinstance(err, QuotaExceeded):
                raise err or ProviderError("all providers failed")
            # the points ran out (or cannot be used): ask again before your own keys are charged
            out = QuotaExceeded(f"{relay[0].label} SinoBee.ai quota points: {err}", "points", err.retry_at,
                                err.status)
            nxt = billing.decide(self.config, has_pool=bool(pool), after_points=True)
            if nxt != billing.OWN:
                billing.clear(self.config, "points used up")
                self._quota_event(out, None, ask=True)
                raise out
            billing.choose(self.config, billing.OWN, window=(st or {}).get("window") or (quota.window if quota else None),
                           retry_at=(quota.retry_at if quota else None))
            self._quota_event(out, billing.OWN, ask=False)
            src = billing.OWN
        if src == billing.OWN:
            if not pool:
                billing.clear(self.config, "no own keys")
                out = quota or QuotaExceeded(f"{relay[0].label} SinoBee.ai quota 6h: no fallback models", "6h")
                self._quota_event(out, None, ask=True)
                raise out
            resp, err = await self._try(await self.ordered_pool(), messages, tools, kw, billing.OWN)
            if resp:
                return resp
            raise err or ProviderError("all providers failed")
        raise ProviderError("all providers failed")

    def _quota_event(self, e: QuotaExceeded, source: str | None, *, ask: bool) -> None:
        from ..accounting import current_scope
        pool = self.pool()
        nxt = (pool[0].label if pool else None) if source == "own" else (self.primary.label if source else None)
        current_scope.get().quota_events.append(
            {"window": e.window, "retry_at": e.retry_at, "model": self.primary.label, "fallback": nxt,
             "source": source, "ask": ask})

    async def embed(self, texts):
        return await self.primary.embed(texts)

    async def list_models(self):
        return await self.primary.list_models()
