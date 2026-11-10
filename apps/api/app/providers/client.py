"""The HTTP client every provider call goes through.

One place applies the rules that keep a data-provider integration cheap and safe:

* credentials are attached here (query, header or path, per provider) and never appear in logs,
  exceptions or responses: URLs are redacted, errors carry only a status and a reason;
* requests are throttled per provider (token bucket) and bounded in concurrency;
* a circuit breaker stops hammering a provider that keeps failing;
* network errors, timeouts, 429 and 5xx are retried with exponential backoff and jitter, honouring
  Retry-After; auth errors and other 4xx are not retried;
* an optional per-investigation request budget counts every attempt, retries included;
* repeatable GETs can be cached in memory for a short time;
* every network attempt is reported to a usage sink.

Provider-specific meaning (what a 200 body with an error message inside is) belongs to the chain
adapters built on top of this client, not here.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import random
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from json import dumps as _dumps
from typing import Any

import httpx2
from pydantic import SecretStr

from app import __version__
from app.core.redaction import secrets
from app.providers.base import (
    AuthStyle,
    CircuitOpen,
    ProviderAuthError,
    ProviderBadResponse,
    ProviderError,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderSpec,
    ProviderUnavailable,
)
from app.providers.breaker import CircuitBreaker
from app.providers.budget import RequestBudget
from app.providers.cache import MISSING, TtlCache
from app.providers.throttle import TokenBucket
from app.providers.usage import NullUsageSink, UsageEvent, UsageSink

logger = logging.getLogger(__name__)

# Looks at a successfully received body and returns the error it amounts to, or None.
BodyCheck = Callable[[str, Any], ProviderError | None]

# A Retry-After longer than this is not slept through inside a request. The provider is marked as
# blocked instead: every call fails fast with ProviderRateLimited(retry_after=...) until the time
# is up, and the job queue reschedules the work.
MAX_INLINE_RETRY_AFTER = 30.0


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    provider: str
    status_code: int
    data: Any  # parsed JSON, or the text when the body is not JSON
    elapsed_ms: float
    attempts: int
    from_cache: bool = False


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    name: str
    display_name: str
    configured: bool
    needs_key: bool
    rate_limit_rps: float
    chains: tuple[str, ...]
    paid_chains: tuple[str, ...]
    circuit: str
    consecutive_failures: int
    # Seconds until the provider's own rate-limit block ends; None when not blocked.
    retry_after_seconds: float | None
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error: str | None


@dataclass(slots=True)
class _ProviderState:
    spec: ProviderSpec
    key: str | None
    rps: float
    bucket: TokenBucket
    breaker: CircuitBreaker
    semaphore: asyncio.Semaphore
    blocked_until: float = 0.0  # client-clock time before which the provider asked us to wait


class ProviderClient:
    def __init__(
        self,
        specs: Mapping[str, ProviderSpec],
        keys: Mapping[str, SecretStr | str | None],
        *,
        usage: UsageSink | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_cap: float = 30.0,
        breaker_threshold: int = 5,
        breaker_cooldown: float = 30.0,
        rate_limits: Mapping[str, float] | None = None,
        cache_entries: int = 1024,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._usage = usage or NullUsageSink()
        self._transport = transport
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._cache = TtlCache(cache_entries, clock=clock)
        self._http: httpx2.AsyncClient | None = None

        self._states: dict[str, _ProviderState] = {}
        for name, spec in specs.items():
            raw = keys.get(name)
            key = raw.get_secret_value() if isinstance(raw, SecretStr) else (raw or None)
            if key:
                secrets.add(key)  # from now on this value is scrubbed from every log line
            rps = (rate_limits or {}).get(name, spec.default_rps)
            self._states[name] = _ProviderState(
                spec=spec,
                key=key,
                rps=rps,
                bucket=TokenBucket(rps, clock=clock, sleep=sleep),
                breaker=CircuitBreaker(
                    failure_threshold=breaker_threshold, cooldown=breaker_cooldown, clock=clock
                ),
                semaphore=asyncio.Semaphore(spec.max_concurrency),
            )

    # --- introspection -----------------------------------------------------------------------

    def spec(self, provider: str) -> ProviderSpec:
        return self._state(provider).spec

    def is_configured(self, provider: str) -> bool:
        state = self._state(provider)
        return not state.spec.needs_key or state.key is not None

    def statuses(self) -> list[ProviderStatus]:
        return [self._status(state) for state in self._states.values()]

    def _status(self, state: _ProviderState) -> ProviderStatus:
        spec, breaker = state.spec, state.breaker
        blocked_for = state.blocked_until - self._clock()
        return ProviderStatus(
            name=spec.name,
            display_name=spec.display_name,
            configured=not spec.needs_key or state.key is not None,
            needs_key=spec.needs_key,
            rate_limit_rps=state.rps,
            chains=spec.chains,
            paid_chains=spec.paid_chains,
            circuit=breaker.state.value,
            consecutive_failures=breaker.consecutive_failures,
            retry_after_seconds=round(blocked_for, 1) if blocked_for > 0 else None,
            last_success_at=breaker.last_success_at,
            last_error_at=breaker.last_error_at,
            last_error=breaker.last_error,
        )

    # --- requests ----------------------------------------------------------------------------

    async def request(
        self,
        provider: str,
        method: str,
        path: str = "",
        *,
        chain: str | None = None,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        cache_ttl: float | None = None,
        budget: RequestBudget | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        body_check: BodyCheck | None = None,
        accept: Collection[int] = (),
    ) -> ProviderResponse:
        """One request with all the protections. `body_check` finds errors inside a 200 response.

        It receives the provider name and the parsed body and returns None if the body is fine,
        or the ProviderError that body amounts to (rate limited, bad key, ...).

        `accept` lists non-2xx statuses that are an answer here rather than a failure, such as 404
        for "there is no such transaction". They come back as a response; check `status_code`.
        """
        state = self._state(provider)
        if state.spec.needs_key and state.key is None:
            raise ProviderNotConfigured(provider, "no API key is configured")

        method = method.upper()
        cache_key = None
        if cache_ttl and method == "GET":
            cache_key = (provider, chain, path, _dumps(params or {}, sort_keys=True, default=str))
            cached = self._cache.get(cache_key)
            if cached is not MISSING:
                # A copy, so a caller that edits the data cannot change what the next one gets.
                return replace(cached, data=copy.deepcopy(cached.data), from_cache=True)

        url, query, request_headers = self._build_request(state, chain, path, params, headers)
        retries = self._max_retries if max_retries is None else max_retries

        attempt = 0
        while True:
            attempt += 1
            blocked_for = state.blocked_until - self._clock()
            if blocked_for > 0:
                raise ProviderRateLimited(
                    provider, "rate limited by the provider", retry_after=blocked_for
                )

            response, failure, elapsed_ms = await self._send(
                state, budget, method, url, query, json, request_headers, timeout
            )

            status: int | None = None
            data: Any = None
            if response is None:
                problem: ProviderError | None = ProviderUnavailable(provider, failure)
            else:
                status = response.status_code
                if 200 <= status < 300:
                    data = _parse_body(response)
                    problem = _web_page(provider, response, data) or self._check_body(
                        provider, body_check, data
                    )
                elif status in accept:
                    data = _parse_body(response)
                    problem = None
                else:
                    problem = _problem_for_status(provider, response)
                    if body_check is not None and 400 <= status < 500 and status != 429:
                        # The provider often says exactly what is wrong in the body ("this network
                        # is not enabled for your key"). Prefer that to a bare status, but only
                        # for a JSON body: an HTML error page tells us nothing.
                        body = _parse_body(response)
                        if isinstance(body, dict | list):
                            problem = self._check_body(provider, body_check, body) or problem

            if problem is None:
                state.breaker.on_success()
                await self._record(provider, "success", attempt, status, elapsed_ms)
                result = ProviderResponse(provider, status, data, elapsed_ms, attempt)
                if cache_key is not None:
                    self._cache.set(cache_key, result, cache_ttl)
                return result

            # A failure is handled the same whether the provider said it with an HTTP status or
            # inside a 200 body (Etherscan and JSON-RPC both do the latter).
            if isinstance(problem, ProviderRateLimited):
                state.breaker.note_error(problem.reason)
                await self._record(provider, "rate_limited", attempt, status, elapsed_ms)
                retry_after = problem.retry_after
                if retry_after is not None and retry_after > MAX_INLINE_RETRY_AFTER:
                    state.blocked_until = self._clock() + retry_after
                    raise problem
                # The next acquire() waits this out, for every caller of this provider.
                state.bucket.penalize(
                    retry_after if retry_after is not None else self._backoff(attempt)
                )
                delay = 0.0
            elif isinstance(problem, ProviderUnavailable):
                state.breaker.on_failure(problem.reason)
                await self._record(provider, "error", attempt, status, elapsed_ms)
                delay = self._backoff(attempt)
            else:  # a rejected key or a request the provider will never accept
                state.breaker.note_error(problem.reason)
                await self._record(provider, "error", attempt, status, elapsed_ms)
                raise problem

            if attempt > retries:
                raise problem
            if delay:
                await self._sleep(delay)

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # --- internals ---------------------------------------------------------------------------

    def _state(self, provider: str) -> _ProviderState:
        try:
            return self._states[provider]
        except KeyError:
            raise ValueError(f"unknown provider {provider!r}") from None

    @staticmethod
    def _check_body(provider: str, body_check: BodyCheck | None, data: Any) -> ProviderError | None:
        if body_check is None:
            return None
        try:
            return body_check(provider, data)
        except Exception:  # a faulty checker must not turn into a crash in the caller
            logger.exception("a response checker failed", extra={"provider": provider})
            return ProviderBadResponse(provider, "the response could not be interpreted")

    async def _send(
        self,
        state: _ProviderState,
        budget: RequestBudget | None,
        method: str,
        url: str,
        query: dict[str, Any],
        json: Any,
        headers: dict[str, str],
        timeout: float | None,
    ) -> tuple[httpx2.Response | None, str, float]:
        """One network attempt: (response, failure, elapsed_ms); `failure` is set if no response."""
        provider = state.spec.name
        if not state.breaker.before_call():
            raise CircuitOpen(
                provider,
                "circuit open after repeated failures",
                retry_after=state.breaker.retry_in(),
            )
        try:
            if budget is not None:
                budget.consume(provider)
            async with state.semaphore:
                await state.bucket.acquire()
                started = self._clock()
                try:
                    response = await self._client().request(
                        method,
                        url,
                        params=query,
                        json=json,
                        headers=headers,
                        timeout=timeout if timeout is not None else httpx2.USE_CLIENT_DEFAULT,
                    )
                    failure = ""
                except httpx2.TimeoutException:
                    response, failure = None, "timeout"
                except httpx2.TransportError as exc:
                    # The class name only: the message of a transport error can carry the URL.
                    response, failure = None, type(exc).__name__
            return response, failure, (self._clock() - started) * 1000
        except BaseException:
            # Refused or cancelled before the outcome was known: give the half-open trial back.
            state.breaker.release_probe()
            raise

    def _client(self) -> httpx2.AsyncClient:
        if self._http is None:  # built lazily so it belongs to the event loop that uses it
            self._http = httpx2.AsyncClient(
                transport=self._transport,
                timeout=httpx2.Timeout(self._timeout, connect=min(self._timeout, 10.0)),
                limits=httpx2.Limits(max_connections=50, max_keepalive_connections=20),
                follow_redirects=False,  # never forward a key to a host the provider redirects to
                headers={"User-Agent": f"aml-platform/{__version__}", "Accept": "application/json"},
            )
        return self._http

    @staticmethod
    def _build_request(
        state: _ProviderState,
        chain: str | None,
        path: str,
        params: Mapping[str, Any] | None,
        headers: Mapping[str, str] | None,
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        spec = state.spec
        base = spec.base_for(chain)
        query = dict(params or {})
        request_headers = dict(headers or {})
        if spec.auth is AuthStyle.PATH:
            base = base.replace("{key}", state.key or "")
        elif spec.auth is AuthStyle.QUERY and state.key:
            query[spec.auth_name or "apikey"] = state.key
        elif spec.auth is AuthStyle.HEADER and state.key:
            request_headers[spec.auth_name or "Authorization"] = state.key
        url = base.rstrip("/") + (f"/{path.lstrip('/')}" if path else "")
        return url, query, request_headers

    def _backoff(self, attempt: int) -> float:
        """Exponential delay with equal jitter: half fixed, half random."""
        delay = min(self._backoff_cap, self._backoff_base * 2 ** (attempt - 1))
        return delay / 2 + self._rng.uniform(0, delay / 2)

    async def _record(
        self, provider: str, outcome: str, attempt: int, status: int | None, elapsed_ms: float
    ) -> None:
        try:
            await self._usage.record(
                UsageEvent(
                    provider=provider,
                    outcome=outcome,
                    retry=attempt > 1,
                    status_code=status,
                    latency_ms=elapsed_ms,
                    at=datetime.now(UTC),
                )
            )
        except Exception:  # accounting must never break a provider call
            logger.warning("could not record provider usage", extra={"provider": provider})


def _problem_for_status(provider: str, response: httpx2.Response) -> ProviderError:
    status = response.status_code
    if status in (401, 403):
        return ProviderAuthError(
            provider, f"HTTP {status}: the API key was rejected or the plan lacks this"
        )
    if status == 429:
        return ProviderRateLimited(provider, "HTTP 429", retry_after=_retry_after_seconds(response))
    if status >= 500 or status == 408:
        return ProviderUnavailable(provider, f"HTTP {status}")
    return ProviderBadResponse(provider, f"HTTP {status}")


def _web_page(provider: str, response: httpx2.Response, data: Any) -> ProviderError | None:
    """A success whose body is a web page is not an answer: it is a maintenance or challenge page
    from something in front of the API, which means the provider is away for now. (Taken for a
    malformed answer it would fail the request for good instead of retrying it.)"""
    if not isinstance(data, str):
        return None
    if "html" in response.headers.get("content-type", "").lower() or data.lstrip().startswith("<"):
        return ProviderUnavailable(provider, "the answer was a web page, not data")
    return None


def _parse_body(response: httpx2.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _retry_after_seconds(response: httpx2.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


__all__ = ["ProviderClient", "ProviderResponse", "ProviderStatus"]
