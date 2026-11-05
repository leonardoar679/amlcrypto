from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_principal, get_providers, get_session
from app.api.errors import APIError
from app.database.repositories.usage import UsageTotals, usage_since
from app.providers.service import ProbeResult, ProviderService

router = APIRouter(prefix="/providers", tags=["providers"])

USAGE_WINDOW = timedelta(hours=24)


class UsageOut(BaseModel):
    requests: int = 0
    successes: int = 0
    errors: int = 0
    rate_limited: int = 0
    retries: int = 0


class ProviderOut(BaseModel):
    name: str
    display_name: str
    configured: bool
    needs_key: bool
    rate_limit_rps: float
    chains: list[str]
    paid_chains: list[str]
    notes: str
    circuit: str
    consecutive_failures: int
    retry_after_seconds: float | None
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error: str | None
    usage_24h: UsageOut


class ProbeOut(BaseModel):
    provider: str
    status: str
    ok: bool
    latency_ms: float | None
    detail: str
    checked_at: datetime


@router.get("", response_model=list[ProviderOut])
def list_providers(
    providers: ProviderService = Depends(get_providers),
    _principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[ProviderOut]:
    """Every data provider: whether it is configured, its limits and what it has been used for.

    The circuit and last-error fields describe this API process only. `usage_24h` comes from the
    database, so it also counts what the worker did. Keys are never part of any response.
    """
    totals = usage_since(session, datetime.now(UTC) - USAGE_WINDOW)
    return [
        ProviderOut(
            **asdict(status),
            notes=providers.spec(status.name).notes,
            usage_24h=UsageOut(**asdict(totals.get(status.name, UsageTotals()))),
        )
        for status in providers.statuses()
    ]


@router.post("/{name}/probe", response_model=ProbeOut)
async def probe_provider(
    name: str,
    providers: ProviderService = Depends(get_providers),
    _principal: Principal = Depends(get_principal),
) -> ProbeResult:
    """Make the provider's single cheap health request. Costs one request of its quota."""
    if name not in providers.names():
        raise APIError(404, "provider_not_found", "There is no provider with that name.")
    return await providers.probe(name)
