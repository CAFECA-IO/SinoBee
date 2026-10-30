"""Provider interface, retry and fallback chain."""
from __future__ import annotations

import asyncio
import contextvars
import logging
import random
import re
import time
import uuid
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
    if err.get("code") == "vendor_down" or window == "provider" or "vendor_down" in message:
        # SinoBee.ai took this vendor out (its account there cannot be used for now): not worth a retry - the
        # allocation picks another model (providers/sinobee.py SinoBeeAutoProvider)
        return ProviderError(f"{label} HTTP {status} vendor_down: {message}", status, False)
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
# fn(output_tokens_so_far, done) set by a running task: a streamed answer reports how far it is (about once a
# second) so the UI's token count moves while a local model thinks for minutes, and once more when it ends
STREAM_PROGRESS: contextvars.ContextVar = contextvars.ContextVar("stream_progress", default=None)


class StreamMeter:
    """Counts streamed chunks (about one token each) and reports them through STREAM_PROGRESS, at most once a second."""

    def __init__(self):
        import time as _t
        self._t, self.n, self.fn, self.last = _t, 0, STREAM_PROGRESS.get(), 0.0

    def add(self, k: int = 1) -> None:
        self.n += k
        if self.fn and self._t.monotonic() - self.last >= 1.0:
            self.last = self._t.monotonic()
            try:
                self.fn(self.n, False)
            except Exception:  # noqa: BLE001 - progress must never break a call
                pass

    def done(self) -> None:
        if self.fn:
            try:
                self.fn(self.n, True)
            except Exception:  # noqa: BLE001
                pass
# how much a step may think when the agent turns it down: "medium" → "low" → "none" (see agent/loop.py)
EFFORT_LEVELS = ("medium", "low", "none")
# what the SinoBee.ai relay passes on beyond the basics (x-sinobee-relay-features), seen on its answers
RELAY_FEATURES: set[str] = set()
# U4: the call right after a reply cut off at the output limit. A timeout there means "too long", not a network
# problem: the same request is not sent again (no retry, no fallback model) - the agent asks for the file in parts.
TOO_LONG: contextvars.ContextVar = contextvars.ContextVar("too_long", default=False)


STREAM_CUT = re.compile(r"incomplete chunked read|peer closed connection|RemoteProtocolError|Server disconnected|"
                        r"stream cut", re.I)


def is_stream_cut(err) -> bool:
    """A streamed answer that stopped in the middle (a proxy or the network dropped the connection)."""
    return bool(STREAM_CUT.search(str(err)))


# After a relay stream was cut, new relay calls start as jobs (polled in short requests, which no proxy cuts) for a
# while; see RelayFeed. Module-wide: the edge between this computer and sinobee.ai is the same for every model.
POLL_STICKY_S = 1800.0
_poll_until = 0.0


def relay_polling() -> bool:
    return time.time() < _poll_until


def _no_polling() -> None:
    global _poll_until
    _poll_until = 0.0


def _mark_cut() -> None:
    global _poll_until
    _poll_until = time.time() + POLL_STICKY_S


def is_timeout(err) -> bool:
    """A call that ran out of time (the read timeout, or a proxy's gateway timeout)."""
    return bool(re.search(r"timed out|ReadTimeout|HTTP 5(04|24)\b|gateway time-?out", str(err), re.I))


# a call whose connection died (computer asleep, network gone) waits this long in total for the network to come
# back before it fails (SINOBEE_NETWORK_WAIT_S); the wall clock running ahead of the monotonic clock by more than
# SLEEP_GAP_S during a call means the computer slept (macOS: time.monotonic stops while asleep)
SLEEP_GAP_S = 30.0


def network_wait_s() -> float:
    import os
    try:
        return max(0.0, float(os.environ.get("SINOBEE_NETWORK_WAIT_S", 900)))
    except ValueError:
        return 900.0


def dropped_connection(err, elapsed: float, wall: float, timeout: float) -> str:
    """Why a timeout error is really a dropped connection, or "" when it is a real timeout (the model was slow).
    ConnectTimeout: no connection at all. A read timeout long before the configured timeout: the socket failed
    (ETIMEDOUT after sleep or a network change is reported by Python as TimeoutError -> httpx ReadTimeout(''))."""
    if wall - elapsed > SLEEP_GAP_S:
        return f"the computer was asleep for {wall - elapsed:.0f}s during the call"
    if isinstance(err, (httpx.ConnectTimeout, httpx.PoolTimeout)):
        return "could not connect"
    if TOO_LONG.get():
        return ""            # right after a reply cut off at the output limit a timeout means "too long" (U4)
    # (a read timeout within a second is not a socket giving up either - and not something a model does)
    if isinstance(err, (httpx.ReadTimeout, httpx.WriteTimeout)) and 1.0 <= elapsed < 0.5 * float(timeout or 0):
        return f"connection dropped after {elapsed:.0f}s (timeout {float(timeout):.0f}s)"
    return ""


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

    def thinking_params(self) -> dict:
        """The thinking settings this call adds to the request (Anthropic: see AnthropicProvider); {} when none"""
        return {}

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
        adapted: set[str] = set()          # parameters left out during this call, one 400 each
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
        offline_wait = 0.0                 # seconds spent waiting for the network in this call
        wait_budget = network_wait_s()
        while True:
            m0, w0 = time.monotonic(), time.time()
            network = False
            try:
                return await self._chat(messages, tools, temperature=temperature, max_tokens=max_tokens)
            except httpx.TimeoutException as e:
                cut = dropped_connection(e, time.monotonic() - m0, time.time() - w0, self.timeout)
                if cut:
                    # not the model being slow: the connection died under it (the computer slept, Wi-Fi changed,
                    # the OS gave up on the socket with ETIMEDOUT - which Python reports as a timeout). Waiting for
                    # the network and sending the same call again is right; "write it in parts" is not.
                    err: ProviderError = ProviderError(f"{self.label} network error: {cut} ({e!r})", retryable=True)
                    network = True
                else:
                    # a request that already ran into the timeout usually does so again: retry it once, not 4 times
                    err = ProviderError(f"{self.label} timed out: {e!r}",
                                        retryable=timeouts == 0 and not TOO_LONG.get())
                    timeouts += 1
            except httpx.TransportError as e:
                slept = time.time() - w0 - (time.monotonic() - m0) > SLEEP_GAP_S
                err = ProviderError(f"{self.label} network error: {e!r}"
                                    + (" - the computer was asleep" if slept else ""), retryable=True)
                network = True
            except ProviderError as e:
                sent = {"temperature": temperature, "reasoning_effort": REASONING_EFFORT.get(), **self.thinking_params()}
                param = compat.rejected_param(e, {k: v for k, v in sent.items() if k not in adapted})
                if param in ("temperature", "reasoning_effort", "thinking", "output_config"):
                    # the model rejects a parameter that only tunes the answer: learn it and send the same call
                    # again without it, at once - this is not counted as a retry (a learned reasoning_effort is
                    # left out by the provider from now on)
                    compat.learn(self.label, param, str(e))
                    compat.note(self.label, param)
                    if param == "temperature":
                        temperature = None
                    adapted.add(param)
                    continue
                err = e
            if err.retryable and is_stream_cut(err):
                # the answer stopped in the middle and could not be resumed (RelayFeed already tried): send the same
                # request once more, not four times - if it is cut again the agent asks for the work in parts (U4)
                err.retryable = timeouts == 0 and not TOO_LONG.get()
                timeouts += 1
            if err.retryable and _gateway_timeout(err):
                # a proxy cut the call because no answer came within its limit (Cloudflare ~100 s, nginx 60 s). The
                # same call takes as long again, and the relay may still be finishing (and charging) the first one:
                # retry it once, like a timeout, not four times
                err.retryable = timeouts == 0 and not TOO_LONG.get()
                timeouts += 1
            if network and err.retryable and offline_wait < wait_budget:
                # the network is gone: wait until the service can be reached again (a sleeping laptop, a Wi-Fi
                # switch) instead of spending the retries in a few seconds and failing the whole task
                waited = await self._wait_online(wait_budget - offline_wait)
                offline_wait += waited
                if waited >= 1.0:
                    _scope_add(retry={"model": self.label, "status": err.status,
                                      "error": f"network down {waited:.0f}s: {str(err)[:120]}"})
                    if attempt >= retries:
                        attempt = retries - 1        # the network came back: this attempt is not the last one
            if not err.retryable or attempt >= retries:
                raise err
            sleep = delay * (2 ** attempt) + random.random()
            _scope_add(retry={"model": self.label, "status": err.status, "error": str(err)[:160]})
            log.warning("%s failed (%s); retrying in %.1fs", self.label, err, sleep)
            await asyncio.sleep(sleep)
            attempt += 1

    def _probe_target(self) -> tuple[str, int] | None:
        """Where a TCP connect shows the service is reachable: the proxy when one is set, else the service's host."""
        import os
        from urllib.parse import urlparse
        u = urlparse(self.base_url)
        if not u.hostname:
            return None
        proxy = ""
        if u.hostname not in ("localhost", "127.0.0.1", "::1"):
            proxy = os.environ.get("HTTPS_PROXY" if u.scheme == "https" else "HTTP_PROXY") or \
                os.environ.get("https_proxy" if u.scheme == "https" else "http_proxy") or os.environ.get("ALL_PROXY", "")
        if proxy:
            p = urlparse(proxy if "://" in proxy else "http://" + proxy)
            if p.hostname:
                return p.hostname, p.port or (443 if p.scheme == "https" else 80)
        return u.hostname, u.port or (443 if u.scheme == "https" else 80)

    async def _wait_online(self, budget: float) -> float:
        """Wait (at most `budget` seconds) until the service accepts a TCP connection. Returns the seconds waited;
        0 when it was reachable at once."""
        target = self._probe_target()
        if not target or budget <= 0:
            return 0.0
        t0 = time.monotonic()
        pause = 5.0
        while True:
            try:
                _, w = await asyncio.wait_for(asyncio.open_connection(*target), 8)
                w.close()
                return time.monotonic() - t0
            except (OSError, asyncio.TimeoutError):
                pass
            spent = time.monotonic() - t0
            if spent >= budget:
                return spent
            if spent < 2:
                log.warning("%s: network unreachable (%s:%s); waiting up to %.0fs for it", self.label, *target, budget)
            await asyncio.sleep(min(pause, max(0.5, budget - spent)))
            pause = min(pause * 1.5, 30.0)

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
            if stream and self.auth:
                return await self._relay_stream(client, url, body, headers)
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

    async def _relay_stream(self, client, url: str, body: bytes, headers: dict) -> dict:
        """A streamed call through the SinoBee.ai relay that survives a cut connection: it carries a request id, so
        the relay keeps the call running and numbers its events; when the connection drops, RelayFeed picks up after
        the last whole event (resume, then polling) - the model is not called again and nothing is charged twice.
        After a cut, new calls start as jobs for a while (polled in short requests)."""
        import json as _json
        rid = "sb_" + uuid.uuid4().hex
        root = url.split("/api/v1/llm")[0] + "/api/v1/llm"
        if relay_polling():
            fmt = "anthropic" if "/anthropic/" in url else "openai"
            job = f"{root}/jobs?format={fmt}"
            keep = {k: v for k, v in headers.items() if not k.lower().startswith("x-sb-") and k.lower() != "authorization"}
            h = {**keep, "x-sinobee-request-id": rid, **await self._auth_headers("POST", job, body)}
            r = await client.post(job, content=body, headers=h)
            if r.status_code == 404 and "jobs" not in r.text:
                _no_polling()                # a relay without jobs (older SinoBee.ai): stream as before
            else:
                if r.status_code >= 400:
                    raise self._http_error(r.status_code, r.text[:2000])
                self._relay_headers(r.headers)
                return await self._read_stream(RelayFeed(self, client, root, rid, None))
        async with client.stream("POST", url, content=body, headers={**headers, "x-sinobee-request-id": rid}) as r:
            if r.status_code >= 400:
                raise self._http_error(r.status_code, (await r.aread()).decode("utf-8", "replace")[:2000])
            self._relay_headers(r.headers)
            if "text/event-stream" not in r.headers.get("content-type", ""):
                return _json.loads(await r.aread())
            return await self._read_stream(RelayFeed(self, client, root, rid, r))

    def _http_error(self, status: int, text: str) -> ProviderError:
        if self.auth:
            return relay_error(self.label, status, text)
        return ProviderError(f"{self.label} HTTP {status}: {text}", status, status in RETRYABLE_STATUS)

    def _relay_headers(self, headers) -> None:
        if not self.auth:
            return
        _note_quota(headers)
        feats = headers.get("x-sinobee-relay-features")
        if feats:
            RELAY_FEATURES.update(f.strip() for f in feats.split(",") if f.strip())
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


class RelayFeed:
    """The lines of a relay answer (what `_sse` reads), across cuts. Whole events only: an event is handed on when its
    blank line arrives, so a cut in the middle of one never delivers half of it twice. When the connection drops:
    resume the stream twice (GET /streams/{id}, Last-Event-ID), then poll it (GET /jobs/{id}?after=n) - short
    requests that no proxy cuts - until it ends. A relay that does not know the id (an older server, or the call
    ended more than 15 minutes ago) gives up with the original error."""

    RESUMES = 2
    ATTEMPTS = 6

    def __init__(self, prov: Provider, client, root: str, rid: str, first):
        self.prov, self.client, self.root, self.rid, self.first = prov, client, root, rid, first
        self.after = 0
        self.cuts = 0
        self.mode = "stream" if first is not None else "poll"

    async def _signed(self, url: str, extra: dict | None = None) -> dict:
        h = {**(extra or {}), **await self.prov._auth_headers("GET", url, b"")}
        return h

    async def _events_from_sse(self, r):
        block: list[str] = []
        num = None
        async for line in r.aiter_lines():
            if line == "":
                if block:
                    for b in block:
                        yield b
                    yield ""
                    if num is not None:
                        self.after = num
                block, num = [], None
                continue
            if line.startswith("id:"):
                try:
                    num = int(line[3:].strip())
                except ValueError:
                    pass
                continue
            block.append(line)
        for b in block:              # a last event without its blank line (the stream ended cleanly)
            yield b

    async def _events_from_polls(self):
        import json as _json
        while True:
            url = f"{self.root}/jobs/{self.rid}?after={self.after}&wait=20"
            r = await self.client.get(url, headers=await self._signed(url), timeout=60)
            if r.status_code >= 400:
                raise self.prov._http_error(r.status_code, r.text[:2000])
            data = _json.loads(r.text)
            for ev in data.get("events") or []:
                for line in str(ev).split("\n"):
                    yield line
                yield ""
            self.after = int(data.get("next") or self.after)
            if data.get("status") == "done":
                return
            if data.get("status") == "error":
                raise ProviderError(f"{self.prov.label} stream error: {data.get('error') or 'unknown'}", 502, True)

    async def aiter_lines(self):
        cut: Exception | None = None
        if self.first is not None:
            try:
                async for line in self._events_from_sse(self.first):
                    yield line
                return
            except httpx.TransportError as e:
                cut = e
        for attempt in range(self.ATTEMPTS):
            if cut is not None:
                self.cuts += 1
                _mark_cut()
                _scope_add(retry={"model": self.prov.label, "status": None,
                                  "error": f"stream cut after event {self.after}; resuming ({cut!r})"[:160]})
                log.warning("%s stream cut after event %s (%r); resuming", self.prov.label, self.after, cut)
            try:
                if self.mode == "stream" and attempt < self.RESUMES:
                    url = f"{self.root}/streams/{self.rid}"
                    async with self.client.stream("GET", url, headers=await self._signed(
                            url, {"Last-Event-ID": str(self.after)})) as r:
                        if r.status_code == 404:
                            raise ProviderError(f"{self.prov.label} network error: {cut!r} (the relay cannot resume it)",
                                                None, True)
                        if r.status_code >= 400:
                            raise self.prov._http_error(r.status_code, (await r.aread()).decode("utf-8", "replace"))
                        async for line in self._events_from_sse(r):
                            yield line
                    return
                self.mode = "poll"
                async for line in self._events_from_polls():
                    yield line
                return
            except httpx.TransportError as e:
                cut = e
        raise ProviderError(f"{self.prov.label} network error: stream cut, resuming failed {self.ATTEMPTS} times: "
                            f"{cut!r}", None, True)


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
