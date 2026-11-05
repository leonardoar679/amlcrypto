"""The job queue. Call these with a write session; each function commits its own unit of work.

Claiming is a portable compare-and-set, not SELECT ... FOR UPDATE SKIP LOCKED (SQLite ignores that):
pick a candidate, then UPDATE it WHERE it is still QUEUED and check how many rows changed. Exactly
one claimer can win, on SQLite and on MySQL alike.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import FetchJob, JobStatus
from app.database.models.base import utcnow

CLAIM_ATTEMPTS = 5  # how many lost races to tolerate before reporting "nothing to do"


def backoff_delay(attempts: int, *, base: float = 5.0, cap: float = 900.0) -> float:
    """Seconds to wait before attempt number `attempts + 1`: 5, 10, 20, ... capped."""
    return min(cap, base * 2 ** max(0, attempts - 1))


def enqueue(
    session: Session,
    job_type: str,
    payload: dict | None = None,
    *,
    priority: int = 0,
    max_attempts: int = 3,
    available_at: datetime | None = None,
    dedupe_key: str | None = None,
    organization_id: int | None = None,
) -> tuple[FetchJob, bool]:
    """Queue a job. Returns (job, created).

    With a `dedupe_key`, an identical job that is still active is returned instead of a second
    one (`created` is False).
    """
    if dedupe_key is not None:
        existing = session.scalar(select(FetchJob).where(FetchJob.dedupe_key == dedupe_key))
        if existing is not None:
            return existing, False

    job = FetchJob(
        job_type=job_type,
        payload=payload or {},
        status=JobStatus.QUEUED.value,
        priority=priority,
        max_attempts=max_attempts,
        available_at=available_at or utcnow(),
        dedupe_key=dedupe_key,
        organization_id=organization_id,
    )
    try:
        with session.begin_nested():
            session.add(job)
    except IntegrityError:
        # Someone queued the same dedupe_key between our check and our insert.
        existing = session.scalar(select(FetchJob).where(FetchJob.dedupe_key == dedupe_key))
        if existing is None:
            raise
        session.commit()
        return existing, False
    session.commit()
    return job, True


def claim_next(
    session: Session,
    worker_id: str,
    *,
    job_types: Collection[str] | None = None,
    now: datetime | None = None,
) -> FetchJob | None:
    """Take the highest-priority job that is due, or None. The job is returned RUNNING."""
    now = now or utcnow()
    for _ in range(CLAIM_ATTEMPTS):
        candidate = select(FetchJob.id).where(
            FetchJob.status == JobStatus.QUEUED.value, FetchJob.available_at <= now
        )
        if job_types is not None:
            candidate = candidate.where(FetchJob.job_type.in_(list(job_types)))
        job_id = session.scalar(
            candidate.order_by(FetchJob.priority.desc(), FetchJob.available_at, FetchJob.id).limit(
                1
            )
        )
        if job_id is None:
            session.rollback()
            return None

        claimed = session.execute(
            update(FetchJob)
            .where(FetchJob.id == job_id, FetchJob.status == JobStatus.QUEUED.value)
            .values(
                status=JobStatus.RUNNING.value,
                locked_at=now,
                locked_by=worker_id,
                attempts=FetchJob.attempts + 1,
            )
        )
        if claimed.rowcount == 1:
            session.commit()
            return session.get(FetchJob, job_id, populate_existing=True)
        session.rollback()  # another worker took it first; look for the next candidate
    return None


def complete(session: Session, job_id: int, result: dict | None = None) -> None:
    """Mark a job succeeded and release its dedupe key."""
    session.execute(
        update(FetchJob)
        .where(FetchJob.id == job_id)
        .values(
            status=JobStatus.SUCCEEDED.value,
            result=result,
            error=None,
            completed_at=utcnow(),
            locked_at=None,
            locked_by=None,
            dedupe_key=None,
        )
    )
    session.commit()


def fail(
    session: Session,
    job_id: int,
    error: str,
    *,
    retry_after: float | None = None,
    retry: bool = True,
    now: datetime | None = None,
) -> JobStatus:
    """Record a failed attempt: requeue with backoff while attempts remain, otherwise give up.

    `retry_after` (seconds) overrides the computed backoff, for example when a provider said how
    long to wait. `retry=False` gives up at once, for failures that trying again cannot fix.
    """
    now = now or utcnow()
    job = session.get(FetchJob, job_id, populate_existing=True)
    if job is None:
        raise LookupError(f"job {job_id} does not exist")

    if retry and job.attempts < job.max_attempts:
        delay = retry_after if retry_after is not None else backoff_delay(job.attempts)
        job.status = JobStatus.QUEUED.value
        job.available_at = now + timedelta(seconds=delay)
        job.error = error
        job.locked_at = None
        job.locked_by = None
        outcome = JobStatus.QUEUED
    else:
        job.status = JobStatus.FAILED.value
        job.error = error
        job.completed_at = now
        job.locked_at = None
        job.locked_by = None
        job.dedupe_key = None
        outcome = JobStatus.FAILED
    session.commit()
    return outcome


def release(session: Session, job_id: int, *, now: datetime | None = None) -> None:
    """Hand a RUNNING job back because its worker is shutting down.

    The interrupted attempt does not count, and the job is due again immediately.
    """
    session.execute(
        update(FetchJob)
        .where(FetchJob.id == job_id, FetchJob.status == JobStatus.RUNNING.value)
        .values(
            status=JobStatus.QUEUED.value,
            available_at=now or utcnow(),
            locked_at=None,
            locked_by=None,
            attempts=FetchJob.attempts - 1,
        )
    )
    session.commit()


def requeue_stale(session: Session, *, older_than: timedelta, now: datetime | None = None) -> int:
    """Recover jobs whose worker died: RUNNING for longer than `older_than`.

    They go back to QUEUED, unless that was their last attempt, in which case they fail.
    """
    now = now or utcnow()
    stale = (FetchJob.status == JobStatus.RUNNING.value, FetchJob.locked_at < now - older_than)
    retried = session.execute(
        update(FetchJob)
        .where(*stale, FetchJob.attempts < FetchJob.max_attempts)
        .values(
            status=JobStatus.QUEUED.value,
            locked_at=None,
            locked_by=None,
            error="worker stopped responding; requeued",
        )
    )
    abandoned = session.execute(
        update(FetchJob)
        .where(*stale, FetchJob.attempts >= FetchJob.max_attempts)
        .values(
            status=JobStatus.FAILED.value,
            locked_at=None,
            locked_by=None,
            completed_at=now,
            dedupe_key=None,
            error="worker stopped responding on the last attempt",
        )
    )
    session.commit()
    return retried.rowcount + abandoned.rowcount
