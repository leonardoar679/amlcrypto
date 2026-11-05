"""The provider layer as the rest of the application sees it: one object, built from Settings."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.providers.base import (
    CircuitOpen,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimited,
    ProviderSpec,
    ProviderUnavailable,
)
from app.providers.client import ProviderClient, ProviderStatus
from app.providers.registry import build_provider_specs, provider_keys
from app.providers.usage import UsageSink

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class ProbeResult:
    provider: str
    # ok | not_configured | auth_failed | rate_limited | circuit_open | unreachable
    # | bad_response | unhealthy | no_probe
    status: str
    ok: bool
    latency_ms: float | None
    detail: str
    checked_at: datetime


class ProviderService:
    def __init__(self, client: ProviderClient) -> None:
        self.client = client

    @classmethod
    def from_settings(
        cls, settings: Settings, *, usage: UsageSink | None = None, **client_options: Any
    ) -> ProviderService:
        """`client_options` reach ProviderClient (transport, clock, sleep ...): for tests."""
        specs = build_provider_specs(settings)
        client = ProviderClient(
            specs,
            provider_keys(settings, specs),
            usage=usage,
            timeout=settings.provider_timeout_seconds,
            max_retries=settings.provider_max_retries,
            breaker_threshold=settings.provider_breaker_threshold,
            breaker_cooldown=settings.provider_breaker_cooldown_seconds,
            rate_limits=settings.provider_rate_limits,
            **client_options,
        )
        return cls(client)

    # --- what is available ---------------------------------------------------------------------

    def names(self) -> list[str]:
        return [status.name for status in self.client.statuses()]

    def spec(self, name: str) -> ProviderSpec:
        return self.client.spec(name)

    def statuses(self) -> list[ProviderStatus]:
        return self.client.statuses()

    def providers_for_chain(self, chain_id: str, *, include_paid: bool = False) -> list[str]:
        """Configured providers that can serve a chain, in registry order."""
        names = []
        for status in self.client.statuses():
            served = status.chains + (status.paid_chains if include_paid else ())
            if status.configured and chain_id in served:
                names.append(status.name)
        return names

    # --- health ----------------------------------------------------------------------------------

    async def probe(self, name: str) -> ProbeResult:
        """Make the provider's one cheap health request and report what happened."""
        spec = self.client.spec(name)  # ValueError for an unknown name

        def result(status: str, detail: str, latency_ms: float | None = None) -> ProbeResult:
            return ProbeResult(name, status, status == "ok", latency_ms, detail, datetime.now(UTC))

        if spec.probe is None:
            return result("no_probe", "this provider has no health request defined")
        if not self.client.is_configured(name):
            return result("not_configured", "no API key is configured")

        probe = spec.probe
        try:
            response = await self.client.request(
                name,
                probe.method,
                probe.path,
                chain=probe.chain,
                params=probe.params,
                json=probe.json,
                timeout=PROBE_TIMEOUT_SECONDS,
                max_retries=0,
            )
        except ProviderAuthError as error:
            return result("auth_failed", error.reason)
        except ProviderRateLimited as error:
            return result("rate_limited", _with_wait(error))
        except CircuitOpen as error:  # before ProviderUnavailable, which it extends
            return result("circuit_open", _with_wait(error))
        except ProviderUnavailable as error:
            return result("unreachable", error.reason)
        except ProviderError as error:  # a 4xx that is not an auth problem
            return result("bad_response", error.reason)

        try:
            healthy = bool(probe.check(response.data))
        except Exception:  # a probe check must never take the endpoint down
            logger.exception("probe check failed", extra={"provider": name})
            healthy = False
        if not healthy:
            return result(
                "unhealthy",
                f"answered, but not with {probe.describe}: check the API key and the plan",
                response.elapsed_ms,
            )
        return result("ok", f"returned {probe.describe}", response.elapsed_ms)

    async def aclose(self) -> None:
        await self.client.aclose()


def _with_wait(error: ProviderError) -> str:
    if error.retry_after is None:
        return error.reason
    return f"{error.reason}; retry in about {error.retry_after:.0f}s"
