"""The handlers every worker has."""

from __future__ import annotations

from typing import Any

from app.graph.jobs import FETCH_COUNTERPARTIES, fetch_counterparties
from app.ingestion.jobs import FETCH_ADDRESS_HISTORY, fetch_address_history
from app.intel.jobs import REFRESH_LABEL_SOURCE, refresh_label_source
from app.risk.jobs import (
    SCREEN_ADDRESS,
    SCREEN_TRANSACTION,
    screen_address_job,
    screen_transaction_job,
)
from app.worker.context import Handler, HandlerRegistry, JobContext, NonRetryable

PROVIDER_PROBE = "PROVIDER_PROBE"

__all__ = [
    "FETCH_ADDRESS_HISTORY",
    "FETCH_COUNTERPARTIES",
    "PROVIDER_PROBE",
    "REFRESH_LABEL_SOURCE",
    "SCREEN_ADDRESS",
    "SCREEN_TRANSACTION",
    "Handler",
    "HandlerRegistry",
    "JobContext",
    "NonRetryable",
    "build_default_handlers",
    "provider_probe",
]


async def provider_probe(context: JobContext) -> dict[str, Any]:
    """Payload {"provider": name}. Reports a health check as the job result."""
    name = context.payload.get("provider")
    if not isinstance(name, str) or name not in context.providers.names():
        raise NonRetryable('the payload must be {"provider": <a known provider name>}')
    probe = await context.providers.probe(name)
    return {
        "provider": probe.provider,
        "status": probe.status,
        "ok": probe.ok,
        "latency_ms": None if probe.latency_ms is None else round(probe.latency_ms, 1),
        "detail": probe.detail,
        "checked_at": probe.checked_at.isoformat(),
    }


def build_default_handlers() -> HandlerRegistry:
    """The handlers every worker has. Later milestones register theirs here."""
    registry = HandlerRegistry()
    registry.register(PROVIDER_PROBE)(provider_probe)
    registry.register(FETCH_ADDRESS_HISTORY)(fetch_address_history)
    registry.register(REFRESH_LABEL_SOURCE)(refresh_label_source)
    registry.register(FETCH_COUNTERPARTIES)(fetch_counterparties)
    registry.register(SCREEN_ADDRESS)(screen_address_job)
    registry.register(SCREEN_TRANSACTION)(screen_transaction_job)
    return registry
