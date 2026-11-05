"""Provider usage counters. Call these with a write session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import ApiUsage
from app.database.models.base import utcnow


def hour_start(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def record_usage(
    session: Session,
    *,
    provider: str,
    at: datetime,
    requests: int = 1,
    successes: int = 0,
    errors: int = 0,
    rate_limited: int = 0,
    retries: int = 0,
) -> None:
    """Add to this provider's counters for the hour containing `at`. Commits.

    Portable upsert: increment in place, and only when no row exists yet insert one. If another
    writer creates the row in between, the unique constraint refuses ours and we increment theirs.
    """
    period = hour_start(at)
    bump = (
        update(ApiUsage)
        .where(ApiUsage.provider == provider, ApiUsage.period_start == period)
        .values(
            requests=ApiUsage.requests + requests,
            successes=ApiUsage.successes + successes,
            errors=ApiUsage.errors + errors,
            rate_limited=ApiUsage.rate_limited + rate_limited,
            retries=ApiUsage.retries + retries,
            updated_at=utcnow(),
        )
    )
    if session.execute(bump).rowcount == 0:
        try:
            with session.begin_nested():
                session.add(
                    ApiUsage(
                        provider=provider,
                        period_start=period,
                        requests=requests,
                        successes=successes,
                        errors=errors,
                        rate_limited=rate_limited,
                        retries=retries,
                    )
                )
        except IntegrityError:
            session.execute(bump)
    session.commit()


@dataclass(frozen=True, slots=True)
class UsageTotals:
    requests: int = 0
    successes: int = 0
    errors: int = 0
    rate_limited: int = 0
    retries: int = 0


def usage_since(session: Session, since: datetime) -> dict[str, UsageTotals]:
    """Totals per provider for every hour from `since` (rounded down to the hour) onwards."""
    rows = session.execute(
        select(
            ApiUsage.provider,
            func.sum(ApiUsage.requests),
            func.sum(ApiUsage.successes),
            func.sum(ApiUsage.errors),
            func.sum(ApiUsage.rate_limited),
            func.sum(ApiUsage.retries),
        )
        .where(ApiUsage.period_start >= hour_start(since))
        .group_by(ApiUsage.provider)
    )
    return {
        provider: UsageTotals(*(int(value or 0) for value in totals)) for provider, *totals in rows
    }
