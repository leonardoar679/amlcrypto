"""Keeping label sources up to date without anyone asking: the worker's second scheduled task.

Nothing refreshed a label source by itself before this, and it stays off until the operator names
the sources in LABEL_REFRESH_SOURCES. The choice is theirs because it is a commitment: every
screening is checked against every source that exists, and one that has gone stale (not confirmed
current for twice its refresh interval) lowers the confidence of all of them. A source that is
listed here is looked at every tick, queued for a refresh once its last check is older than its own
refresh interval, and queued again at the next tick if the refresh failed, so a server that is down
for a day does not make it stale.

The refresh itself is the REFRESH_LABEL_SOURCE job (retries, a result, an error on the job), queued
with a key that lets only one per source wait at a time.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.database.models import LabelSource
from app.database.repositories import jobs as queue
from app.intel.importer import READERS
from app.intel.jobs import REFRESH_LABEL_SOURCE
from app.worker.context import Periodic

logger = logging.getLogger(__name__)


def is_due(row: LabelSource | None, now: datetime) -> bool:
    """A source that was never imported, or last confirmed current a refresh interval ago."""
    if row is None or row.last_checked_at is None:
        return True
    return now - row.last_checked_at >= timedelta(seconds=row.refresh_seconds)


def queue_due(session: Session, slugs: tuple[str, ...], now: datetime) -> int:
    """Queue a refresh for each of the sources whose time has come. Returns how many were queued
    (one already waiting for a worker is not queued twice)."""
    queued = 0
    for slug in slugs:
        row = session.scalar(select(LabelSource).where(LabelSource.slug == slug))
        if not is_due(row, now):
            continue
        _, created = queue.enqueue(
            session,
            REFRESH_LABEL_SOURCE,
            {"source": slug},
            dedupe_key=f"label-refresh:{slug}",
        )
        queued += int(created)
    return queued


def label_refresh_scheduler(
    settings: Settings, write_sessions: sessionmaker[Session]
) -> Periodic | None:
    """The tick that does it, or None when no source is named. A name there is no reader for stops
    the worker at start, where it is seen, rather than being ignored every hour."""
    slugs = settings.label_refresh_slugs
    if not slugs:
        return None
    unknown = [slug for slug in slugs if slug not in READERS]
    if unknown:
        raise ValueError(
            f"LABEL_REFRESH_SOURCES names {', '.join(unknown)}, which has no reader "
            f"(there are: {', '.join(READERS)})"
        )

    def tick() -> int:
        with write_sessions() as session:
            queued = queue_due(session, slugs, datetime.now(UTC))
        if queued:
            logger.info("label source refreshes queued", extra={"count": queued})
        return queued

    return Periodic("label-refresh", settings.label_refresh_tick_seconds, tick)
