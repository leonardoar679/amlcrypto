"""The check of one monitored address: the job the scheduler (or a webhook, or a person) queues.

A check screens the address, which first brings the stored histories of the address and the
counterparties it follows up to date through the providers. It then compares what it found with
what the last check found, and keeps a report and raises an alert only when something is new: a
check that finds the same as before changes nothing but the time it last looked (and a report is
kept anyway once in a while, so an address that never changes still has a recent one).

A check that cannot be completed is retried by the worker like any job. Only when a whole attempt
has failed (the last retry, or an error retrying cannot cure) does it count against the monitor, and
a monitor that has failed several times in a row raises one alert of its own: a watch that cannot
look is not a quiet address.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.access.audit import Actor, record
from app.chains.support import find_chain
from app.core.config import Settings
from app.core.redaction import secrets
from app.database.connection import Sessions
from app.database.models import Alert, MonitoredAddress
from app.database.repositories import monitoring as store
from app.database.repositories import risk as risk_store
from app.graph.bridges import BridgeRegistryError
from app.graph.model import GraphLimits
from app.graph.service import GraphRequestError
from app.monitoring import detect
from app.monitoring.service import CHECK_ADDRESS, queue_due
from app.providers.base import ProviderError
from app.risk.model import Report
from app.risk.service import ADDRESS, screen_address
from app.worker.context import JobContext, NonRetryable, Periodic

logger = logging.getLogger(__name__)

MONITOR = Actor("system", None, "monitoring")
MAX_ERROR_LENGTH = 500


@dataclass(frozen=True, slots=True)
class Facts:
    """What a check needs from the monitor, copied so no session is open while the providers are."""

    id: int
    chain_id: str
    address: str
    depth: int


def label_of(monitor: MonitoredAddress) -> str:
    """How to name the address in an alert: the name it was given, else a short form of it."""
    if monitor.name:
        return monitor.name
    address = monitor.address
    return address if len(address) <= 16 else f"{address[:10]}..{address[-6:]}"


def scheduler(settings: Settings, write_sessions: sessionmaker[Session]) -> Periodic:
    """The worker's tick: queue a check for every monitored address whose time has come."""

    def tick() -> int:
        with write_sessions() as session:
            queued = queue_due(session, settings, datetime.now(UTC))
        if queued:
            logger.info("checks of monitored addresses queued", extra={"count": queued})
        return queued

    return Periodic("monitoring", settings.monitoring_tick_seconds, tick)


async def check_monitored_address(context: JobContext) -> dict[str, Any]:
    """Payload {"monitor_id", "reason"}: screen the address, compare with the last check, keep a
    report and raise an alert when something is new."""
    sessions = context.sessions
    if sessions is None:
        raise NonRetryable("this worker has no database to keep monitoring in")
    monitor_id = context.payload.get("monitor_id")
    if isinstance(monitor_id, bool) or not isinstance(monitor_id, int):
        raise NonRetryable('the payload must name a "monitor_id"')

    with sessions.read() as session:
        monitor = store.get_monitor(session, monitor_id)
        if monitor is None or monitor.status != store.ACTIVE:
            return {"skipped": "the address is not being monitored"}
        facts = Facts(monitor.id, monitor.chain_id, monitor.address, monitor.depth)

    chain = find_chain(facts.chain_id)
    if chain is None:
        message = f"{facts.chain_id} is not a chain this platform reads"
        await asyncio.to_thread(_failed, sessions, context.settings, facts.id, message)
        raise NonRetryable(message)

    try:
        report = await screen_address(
            sessions,
            context.settings,
            context.providers,
            chain,
            facts.address,
            GraphLimits.from_settings(context.settings, depth=facts.depth),
            update=True,
        )
    except (GraphRequestError, BridgeRegistryError) as error:
        await asyncio.to_thread(_failed, sessions, context.settings, facts.id, str(error))
        raise NonRetryable(str(error)) from error
    except ProviderError as error:
        # The worker retries what may pass; it counts against the monitor only when it will not.
        if not error.retryable or context.attempt >= context.max_attempts:
            await asyncio.to_thread(
                _failed, sessions, context.settings, facts.id, f"{type(error).__name__}: {error}"
            )
        raise

    return await asyncio.to_thread(
        _checked, sessions, context.settings, facts.id, report, datetime.now(UTC)
    )


# --- what a check that worked leaves behind -------------------------------------------------------


def _checked(
    sessions: Sessions, settings: Settings, monitor_id: int, report: Report, now: datetime
) -> dict[str, Any]:
    body = report.to_dict()
    current = detect.snapshot_of(body)
    with sessions.write() as session:
        monitor = store.get_monitor(session, monitor_id)
        if monitor is None:
            return {"skipped": "the address was removed while it was being checked"}

        first = monitor.last_checked_at is None
        known = set(monitor.known_findings or [])
        draft = detect.decide(
            previous=None
            if first
            else detect.Snapshot(monitor.last_risk_score, monitor.last_risk_level, frozenset()),
            report=body,
            known=known,
            min_severity=monitor.min_severity,
            score_delta=settings.monitoring_score_delta,
            label=label_of(monitor),
        )
        activity = store.latest_activity_at(session, monitor.chain_id, monitor.address)
        new_activity = activity is not None and (
            monitor.last_activity_at is None or activity > monitor.last_activity_at
        )
        heartbeat = monitor.last_changed_at is None or now - monitor.last_changed_at >= timedelta(
            days=settings.monitoring_heartbeat_days
        )
        changed = (
            first
            or draft is not None
            or new_activity
            or heartbeat
            or (current.score, current.level) != (monitor.last_risk_score, monitor.last_risk_level)
            or bool(current.keys - known)
        )

        previous_id = monitor.last_assessment_id
        assessment_id = None
        if changed:
            assessment = risk_store.create_assessment(
                session,
                organization_id=monitor.organization_id,
                subject_type=ADDRESS,
                chain_id=monitor.chain_id,
                subject=monitor.address,
                options={"monitor_id": monitor.id, "depth": monitor.depth, "update": True},
            )
            risk_store.complete_assessment(session, assessment.id, report, now)
            assessment_id = assessment.id
            monitor.last_assessment_id = assessment.id
            monitor.last_changed_at = now

        alert_id = None
        if draft is not None:
            assert assessment_id is not None  # an alert implies something changed
            alert_id = _raise(
                session, monitor, draft, f"{monitor.id}:{assessment_id}", assessment_id, previous_id
            )
        _recover(session, monitor, now)

        monitor.last_checked_at = now
        monitor.next_check_at = now + timedelta(seconds=monitor.check_interval_seconds)
        monitor.last_risk_score = current.score
        monitor.last_risk_level = current.level
        monitor.known_findings = detect.remember(known, body)
        if activity is not None and (
            monitor.last_activity_at is None or activity > monitor.last_activity_at
        ):
            monitor.last_activity_at = activity
        monitor.consecutive_failures = 0
        monitor.last_error = None
        session.commit()

    logger.info(
        "monitored address checked",
        extra={"monitor_id": monitor_id, "changed": changed, "alert_id": alert_id},
    )
    return {
        "monitor_id": monitor_id,
        "changed": changed,
        "new_activity": new_activity,
        "risk_score": current.score,
        "risk_level": current.level,
        "assessment_id": assessment_id,
        "alert_id": alert_id,
    }


def _raise(
    session: Session,
    monitor: MonitoredAddress,
    draft: detect.AlertDraft,
    dedupe_key: str,
    assessment_id: int | None,
    previous_id: int | None,
) -> int | None:
    """Add the alert (unless one with this key exists, which a repeated check can produce)."""
    alert = Alert(
        organization_id=monitor.organization_id,
        monitored_address_id=monitor.id,
        chain_id=monitor.chain_id,
        address=monitor.address,
        kind=draft.kind,
        severity=draft.severity,
        status=store.OPEN,
        title=draft.title[:200],
        summary=draft.summary,
        evidence=draft.evidence(),
        assessment_id=assessment_id,
        previous_assessment_id=previous_id,
        dedupe_key=f"{dedupe_key}:{draft.kind}"[:120],
    )
    try:
        with session.begin_nested():
            session.add(alert)
    except IntegrityError:
        return None
    session.flush()
    record(
        session,
        actor=MONITOR,
        action="alert.create",
        target_type="alert",
        target_id=alert.id,
        details={
            "kind": alert.kind,
            "severity": alert.severity,
            "monitor_id": monitor.id,
            "address": monitor.address,
        },
        organization_id=monitor.organization_id,
    )
    return alert.id


def _recover(session: Session, monitor: MonitoredAddress, now: datetime) -> None:
    """A watch that works again closes the alert that said it did not."""
    if monitor.consecutive_failures < 1:
        return
    alert = store.open_alert_of_kind(session, monitor.id, detect.KIND_DEGRADED)
    if alert is None:
        return
    before = alert.status
    alert.status = store.CLOSED
    alert.resolution = "recovered"
    alert.closed_at = now
    record(
        session,
        actor=MONITOR,
        action="alert.update",
        target_type="alert",
        target_id=alert.id,
        details={"changes": {"status": {"from": before, "to": "closed"}}, "reason": "recovered"},
        organization_id=monitor.organization_id,
    )


# --- what a check that did not work leaves behind ------------------------------------------------


def _failed(sessions: Sessions, settings: Settings, monitor_id: int, message: str) -> None:
    now = datetime.now(UTC)
    with sessions.write() as session:
        monitor = store.get_monitor(session, monitor_id)
        if monitor is None:
            return
        monitor.consecutive_failures += 1
        monitor.last_error = secrets.scrub(message)[:MAX_ERROR_LENGTH]
        monitor.next_check_at = now + timedelta(seconds=monitor.check_interval_seconds)
        if (
            monitor.consecutive_failures >= settings.monitoring_degraded_after
            and store.open_alert_of_kind(session, monitor.id, detect.KIND_DEGRADED) is None
        ):
            draft = detect.degraded(
                label_of(monitor), monitor.consecutive_failures, monitor.last_error
            )
            _raise(
                session,
                monitor,
                draft,
                f"{monitor.id}:degraded:{int(now.timestamp())}",
                None,
                monitor.last_assessment_id,
            )
        session.commit()


__all__ = ["CHECK_ADDRESS", "check_monitored_address", "label_of", "scheduler"]
