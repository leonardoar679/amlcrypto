"""Registering addresses to watch, changing how they are watched, and queueing their checks.

What a check does is in app.monitoring.jobs; what makes it raise an alert is in
app.monitoring.detect. This module is the part the API, the scheduler and the webhook share.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.chains.support import ReadableChain
from app.core.config import Settings
from app.database.models import FetchJob, MonitoredAddress
from app.database.repositories import jobs as queue
from app.database.repositories import monitoring as store
from app.monitoring.detect import SEVERITIES

CHECK_ADDRESS = "ADDRESS_MONITORING"
# Why a check was queued. A person waiting for an answer, or an event from the chain, goes before
# the routine rounds.
SCHEDULED, REGISTERED, MANUAL, WEBHOOK = "scheduled", "registered", "manual", "webhook"
PRIORITY = {SCHEDULED: 0, REGISTERED: 5, MANUAL: 5, WEBHOOK: 10}
MAX_INTERVAL_SECONDS = 30 * 24 * 3600
MAX_NAME_LENGTH = 120
DEFAULT_SEVERITY = "MEDIUM"
UNSET: Any = object()  # "leave it as it is", for a name that may also be set to nothing


class MonitorError(ValueError):
    """The request is not acceptable (shown to the person who made it)."""


class MonitorConflict(MonitorError):
    """The request clashes with what exists: the address is already watched, or the limit is hit."""


def _name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if len(cleaned) > MAX_NAME_LENGTH:
        raise MonitorError(f"A name is at most {MAX_NAME_LENGTH} characters.")
    return cleaned or None


def _interval(settings: Settings, seconds: int | None) -> int:
    chosen = settings.monitoring_default_interval_seconds if seconds is None else seconds
    if not settings.monitoring_min_interval_seconds <= chosen <= MAX_INTERVAL_SECONDS:
        raise MonitorError(
            "An address is checked every "
            f"{settings.monitoring_min_interval_seconds} seconds to {MAX_INTERVAL_SECONDS} seconds."
        )
    return chosen


def _depth(settings: Settings, depth: int | None) -> int:
    chosen = 1 if depth is None else depth
    if not 1 <= chosen <= settings.graph_max_depth:
        raise MonitorError(f"A check follows between 1 and {settings.graph_max_depth} hops.")
    return chosen


def _severity(value: str | None) -> str:
    chosen = DEFAULT_SEVERITY if value is None else value
    if chosen not in SEVERITIES:
        raise MonitorError("The lowest severity to alert on is LOW, MEDIUM, HIGH or CRITICAL.")
    return chosen


def register(
    session: Session,
    *,
    settings: Settings,
    organization_id: int,
    created_by: int,
    chain: ReadableChain,
    address: str,
    name: str | None,
    check_interval_seconds: int | None,
    depth: int | None,
    min_severity: str | None,
    now: datetime,
) -> MonitoredAddress:
    """Start watching an address. It is checked as soon as the scheduler next runs. Adds to the
    session; the caller commits."""
    cleaned, interval = _name(name), _interval(settings, check_interval_seconds)
    hops, severity = _depth(settings, depth), _severity(min_severity)
    if store.find_monitor(session, organization_id, chain.chain_id, address) is not None:
        raise MonitorConflict("That address is already being monitored.")
    if store.count_monitors(session, organization_id) >= settings.monitoring_max_addresses:
        raise MonitorConflict(
            f"An organization may watch at most {settings.monitoring_max_addresses} addresses; "
            "remove one first."
        )
    row = MonitoredAddress(
        organization_id=organization_id,
        chain_id=chain.chain_id,
        address=address,
        name=cleaned,
        status=store.ACTIVE,
        check_interval_seconds=interval,
        depth=hops,
        min_severity=severity,
        created_by=created_by,
        next_check_at=now,
        known_findings=[],
        consecutive_failures=0,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        # Another request registered the same address between our look and our insert.
        session.rollback()
        raise MonitorConflict("That address is already being monitored.") from None
    return row


def update(
    session: Session,
    monitor: MonitoredAddress,
    *,
    settings: Settings,
    name: str | None = UNSET,
    status: str | None = None,
    check_interval_seconds: int | None = None,
    depth: int | None = None,
    min_severity: str | None = None,
    now: datetime,
) -> dict[str, Any]:
    """Change how an address is watched. Returns what changed, as {field: {"from", "to"}}."""
    changes: dict[str, Any] = {}

    def note(field: str, before: Any, after: Any) -> None:
        if before != after:
            changes[field] = {"from": before, "to": after}

    if name is not UNSET:
        cleaned = _name(name)
        note("name", monitor.name, cleaned)
        monitor.name = cleaned
    if status is not None:
        if status not in (store.ACTIVE, store.PAUSED):
            raise MonitorError("The status is active or paused.")
        note("status", monitor.status, status)
        if status == store.ACTIVE and monitor.status != store.ACTIVE:
            monitor.next_check_at = now  # resumed: look at once
        monitor.status = status
    if check_interval_seconds is not None:
        interval = _interval(settings, check_interval_seconds)
        note("check_interval_seconds", monitor.check_interval_seconds, interval)
        monitor.check_interval_seconds = interval
    if depth is not None:
        hops = _depth(settings, depth)
        note("depth", monitor.depth, hops)
        monitor.depth = hops
    if min_severity is not None:
        severity = _severity(min_severity)
        note("min_severity", monitor.min_severity, severity)
        monitor.min_severity = severity
    return changes


def queue_check(
    session: Session, monitor: MonitoredAddress, *, reason: str
) -> tuple[FetchJob, bool]:
    """Queue a check now. One check per monitor is queued or running at a time: asking again returns
    the one that is. Commits (as the queue does)."""
    return queue.enqueue(
        session,
        CHECK_ADDRESS,
        {"monitor_id": monitor.id, "reason": reason},
        priority=PRIORITY[reason],
        organization_id=monitor.organization_id,
        dedupe_key=f"monitor:{monitor.id}",
    )


def queue_due(session: Session, settings: Settings, now: datetime) -> int:
    """Queue a check for every monitor whose time has come, and move each one's next time on by its
    interval at once, so a check that fails does not come back every tick. Returns how many."""
    queued = 0
    for monitor in store.due_monitors(session, now, settings.monitoring_batch):
        monitor.next_check_at = now + timedelta(seconds=monitor.check_interval_seconds)
        session.commit()
        _, created = queue_check(session, monitor, reason=SCHEDULED)
        queued += int(created)
    return queued
