"""Provider errors and the static description of each data provider.

Error messages are written to be safe to log and to return from the API: they never contain a URL
or a credential, only the provider's name and a short reason.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ProviderError(Exception):
    # Whether asking again later could work. A rejected key, a plan that lacks the endpoint or a
    # malformed request will fail the same way every time, so the job queue gives up at once.
    retryable = True

    def __init__(self, provider: str, reason: str, *, retry_after: float | None = None) -> None:
        super().__init__(f"{provider}: {reason}")
        self.provider = provider
        self.reason = reason
        # Seconds to wait first, when the provider (or our circuit breaker) said how long.
        self.retry_after = retry_after


class ProviderNotConfigured(ProviderError):
    """No API key (or other required setting) is available for this provider."""

    retryable = False


class ProviderUnavailable(ProviderError):
    """A network failure, timeout or 5xx that survived the retries."""


class CircuitOpen(ProviderUnavailable):
    """The provider has been failing; no request was made."""


class ProviderRateLimited(ProviderError):
    """HTTP 429, or a block we are still serving from an earlier 429."""


class ProviderAuthError(ProviderError):
    """401 or 403: wrong credentials, or the plan does not include this."""

    retryable = False


class ProviderBadResponse(ProviderError):
    """Any other 4xx, or a body that is not what the endpoint promises."""

    retryable = False


class BudgetExceeded(ProviderError):
    """The request budget for this investigation is used up."""

    retryable = False


class AllProvidersFailed(ProviderError):
    def __init__(self, capability: str, failures: list[tuple[str, ProviderError]]) -> None:
        summary = "; ".join(f"{name}: {error.reason}" for name, error in failures) or "none tried"
        waits = [error.retry_after for _, error in failures if error.retry_after is not None]
        super().__init__(
            capability,
            f"no provider could serve this ({summary})",
            retry_after=min(waits) if waits else None,
        )
        self.failures = failures
        # Worth another go if any provider failed for a reason that can pass; with none tried
        # (nothing configured for this chain) waiting changes nothing.
        self.retryable = any(error.retryable for _, error in failures)


class AuthStyle(StrEnum):
    NONE = "none"  # public endpoint, no key
    QUERY = "query"  # key sent as a query parameter named by `auth_name`
    HEADER = "header"  # key sent as a header named by `auth_name`
    PATH = "path"  # key embedded in the URL path; base_url contains "{key}"


@dataclass(frozen=True, slots=True)
class Probe:
    """One cheap, read-only request that shows a provider is reachable and the key works."""

    method: str
    path: str = ""
    params: Mapping[str, str] | None = None
    json: Any = None
    chain: str | None = None
    # Receives the parsed response body; False means "answered, but not what a healthy node says".
    check: Callable[[Any], bool] = lambda _data: True
    describe: str = ""


@dataclass(frozen=True, slots=True, eq=False)
class ProviderSpec:
    name: str
    display_name: str
    base_url: str  # may contain "{key}" when auth is PATH
    auth: AuthStyle
    auth_name: str | None = None
    # Name of the Settings attribute that holds the key; None when no key is needed.
    key_setting: str | None = None
    # CAIP-2 ids served on the plan we assume (the free one where there is one).
    chains: tuple[str, ...] = ()
    # Chains that need a paid plan with this provider.
    paid_chains: tuple[str, ...] = ()
    # Per-chain hosts, for providers with one host per network (Alchemy).
    chain_base_urls: Mapping[str, str] = field(default_factory=dict)
    default_rps: float = 2.0
    max_concurrency: int = 4
    probe: Probe | None = None
    notes: str = ""

    @property
    def needs_key(self) -> bool:
        return self.auth is not AuthStyle.NONE

    def base_for(self, chain: str | None) -> str:
        if chain is not None and chain in self.chain_base_urls:
            return self.chain_base_urls[chain]
        return self.base_url
