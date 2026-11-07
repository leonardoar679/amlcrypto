"""The sign-in lockout: an account that keeps failing is refused for a while.

It counts failed sign-ins from the audit log, so there is no second place that has to agree with
it. An account (by its email, whether or not it exists: the answer must not tell) with
`login_max_failures` failures inside `login_failure_window_seconds` is locked until the failure that
tipped it over leaves the window. Attempts made while locked are logged as denied, not as failures,
so an attacker cannot extend the lock by trying again.

It protects an account from being guessed, at the price that someone who knows an email can lock
that account out for a few minutes. Limiting one client across many accounts is the reverse
proxy's job (rate limits in front of the API, milestone M14): behind a proxy the API cannot always
tell clients apart.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.database.models import AuditLog

LOGIN_ACTION = "auth.login"


def seconds_locked(
    session: Session, settings: Settings, subject: str, *, now: datetime | None = None
) -> int | None:
    """How long this account stays locked, in whole seconds (at least 1), or None if it is not."""
    when = now or datetime.now(UTC)
    window = timedelta(seconds=settings.login_failure_window_seconds)
    recent = session.scalars(
        select(AuditLog.created_at)
        .where(
            AuditLog.action == LOGIN_ACTION,
            AuditLog.outcome == "failure",
            AuditLog.subject_key == subject,
            AuditLog.created_at > when - window,
        )
        .order_by(AuditLog.created_at.desc())
        .limit(settings.login_max_failures)
    ).all()
    if len(recent) < settings.login_max_failures:
        return None
    lifts = recent[-1] + window
    return max(1, int((lifts - when).total_seconds()) + 1)
