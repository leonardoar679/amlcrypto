"""Provider usage counters. Call these with a write session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import ApiUsage
from app.database.models.base import utcnow
from app.database.retry import RETRIES, commit_with_retry

__all__ = [
    "RETRIES",
    "UsageTotals",
    "hour_start",
    "record_usage",
    "upsert_statement",
    "usage_since",
]


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

    One atomic statement per dialect (INSERT ... ON DUPLICATE KEY UPDATE on MySQL, ON CONFLICT DO
    UPDATE on SQLite). The obvious portable version, "UPDATE, and INSERT when nothing matched",
    deadlocks under concurrent writers on MySQL: both transactions take a gap lock by updating a
    row that is not there yet, and both then want to insert.
    """
    period = hour_start(at)
    deltas = {
        "requests": requests,
        "successes": successes,
        "errors": errors,
        "rate_limited": rate_limited,
        "retries": retries,
    }
    commit_with_retry(session, lambda: _add(session, provider, period, deltas))


def upsert_statement(dialect: str, provider: str, period: datetime, deltas: dict[str, int]):
    """The atomic upsert for a database dialect, or None when it has no native one."""
    values = {"provider": provider, "period_start": period, **deltas}
    increments = {name: getattr(ApiUsage, name) + amount for name, amount in deltas.items()}
    if dialect == "mysql":
        return (
            mysql.insert(ApiUsage)
            .values(**values)
            .on_duplicate_key_update(**increments, updated_at=utcnow())
        )
    if dialect == "sqlite":
        return (
            sqlite.insert(ApiUsage)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[ApiUsage.provider, ApiUsage.period_start],
                set_={**increments, "updated_at": utcnow()},
            )
        )
    return None


def _add(session: Session, provider: str, period: datetime, deltas: dict[str, int]) -> None:
    statement = upsert_statement(session.get_bind().dialect.name, provider, period, deltas)
    if statement is not None:
        session.execute(statement)
        return

    # A database without a native upsert: increment, and insert when nothing matched.
    increments = {name: getattr(ApiUsage, name) + amount for name, amount in deltas.items()}
    bump = (
        update(ApiUsage)
        .where(ApiUsage.provider == provider, ApiUsage.period_start == period)
        .values(**increments, updated_at=utcnow())
    )
    if session.execute(bump).rowcount == 0:
        try:
            with session.begin_nested():
                session.add(ApiUsage(provider=provider, period_start=period, **deltas))
        except IntegrityError:
            session.execute(bump)


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
