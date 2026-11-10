"""Addresses a team keeps watch on (blueprint M13: monitored address registration).

Registering an address does not read anything by itself: the worker checks each monitored address on
its interval (and at once when asked, or when a webhook says something happened), keeps a report
when something changed and raises an alert when something is new. This module only manages the
list of what is watched and how; the alerts are in app.api.v1.alerts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import (
    Audit,
    Principal,
    get_audit,
    get_session,
    get_settings_dep,
    get_write_session,
    monitoring_read,
    monitoring_write,
)
from app.api.errors import APIError
from app.api.limits import MAX_HOPS, IdPath
from app.chains.registry import get_chain
from app.core.config import Settings
from app.database.models import MonitoredAddress
from app.database.repositories import monitoring as store
from app.graph.service import GraphRequestError, resolve
from app.monitoring import service
from app.risk.service import screening_id

router = APIRouter(prefix="/monitoring", tags=["monitoring"])

Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class RegisterAddress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain: str = Field(max_length=100, description="Chain slug or CAIP-2 id.")
    address: str = Field(max_length=512)
    name: str | None = Field(None, max_length=1000, description="What the team calls it.")
    check_interval_seconds: int | None = Field(
        None, ge=1, le=service.MAX_INTERVAL_SECONDS, description="Seconds between checks."
    )
    depth: int | None = Field(
        None, ge=1, le=MAX_HOPS, description="Hops each check follows (default 1)."
    )
    min_severity: Severity | None = Field(
        None, description="Raise an alert from this severity up (default MEDIUM)."
    )


class UpdateMonitor(BaseModel):
    """Only what is sent changes. `name` may be sent as null to remove it."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, max_length=1000)
    status: Literal["active", "paused"] | None = None
    check_interval_seconds: int | None = Field(None, ge=1, le=service.MAX_INTERVAL_SECONDS)
    depth: int | None = Field(None, ge=1, le=MAX_HOPS)
    min_severity: Severity | None = None


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _item(monitor: MonitoredAddress, unresolved: int = 0) -> dict[str, Any]:
    spec = get_chain(monitor.chain_id)
    return {
        "id": monitor.id,
        "chain": spec.slug,
        "chain_id": monitor.chain_id,
        "address": monitor.address,
        "display": spec.validator.check(monitor.address).display or monitor.address,
        "name": monitor.name,
        "status": monitor.status,
        "check_interval_seconds": monitor.check_interval_seconds,
        "depth": monitor.depth,
        "min_severity": monitor.min_severity,
        "created_by": monitor.created_by,
        "created_at": _iso(monitor.created_at),
        "next_check_at": _iso(monitor.next_check_at) if monitor.status == store.ACTIVE else None,
        "last_checked_at": _iso(monitor.last_checked_at),
        "last_changed_at": _iso(monitor.last_changed_at),
        "last_activity_at": _iso(monitor.last_activity_at),
        "last_screening_id": None
        if monitor.last_assessment_id is None
        else screening_id(monitor.last_assessment_id),
        "last_risk_score": monitor.last_risk_score,
        "last_risk_level": monitor.last_risk_level,
        "consecutive_failures": monitor.consecutive_failures,
        "last_error": monitor.last_error,
        "unresolved_alerts": unresolved,
    }


def _own(session: Session, principal: Principal, monitor_id: int) -> MonitoredAddress:
    monitor = store.get_monitor(session, monitor_id, principal.organization_id)
    if monitor is None:
        raise APIError(404, "monitor_not_found", "No such monitored address.")
    return monitor


def _failure(error: service.MonitorError) -> APIError:
    if isinstance(error, service.MonitorConflict):
        return APIError(409, "conflict", str(error))
    return APIError(422, "invalid_monitor", str(error))


@router.post("/addresses", status_code=201)
def register_address(
    body: RegisterAddress,
    principal: Principal = Depends(monitoring_write),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    """Start watching an address. The first check is queued at once; it screens the address, so
    the first report and, if the address is already risky, the first alert come from it."""
    try:
        chain, address = resolve(body.chain, body.address)
    except GraphRequestError as error:
        if error.chain_unknown:
            raise APIError(404, "unknown_chain", "That chain is not supported.") from error
        raise APIError(422, "invalid_address", str(error)) from error
    assert principal.owner_id is not None  # a person, or the person whose key this is
    try:
        monitor = service.register(
            session,
            settings=settings,
            organization_id=principal.organization_id,
            created_by=principal.owner_id,
            chain=chain,
            address=address,
            name=body.name,
            check_interval_seconds=body.check_interval_seconds,
            depth=body.depth,
            min_severity=body.min_severity,
            now=datetime.now(UTC),
        )
    except service.MonitorError as error:
        raise _failure(error) from None
    audit.add(
        session,
        "monitor.create",
        target_type="monitor",
        target_id=monitor.id,
        details={
            "chain": get_chain(monitor.chain_id).slug,
            "address": monitor.address,
            "name": monitor.name,
            "check_interval_seconds": monitor.check_interval_seconds,
            "depth": monitor.depth,
            "min_severity": monitor.min_severity,
        },
    )
    session.commit()
    service.queue_check(session, monitor, reason=service.REGISTERED)
    return {"success": True, "monitor": _item(monitor)}


@router.get("/addresses")
def list_addresses(
    status: Annotated[Literal["active", "paused"] | None, Query()] = None,
    prefix: Annotated[str | None, Query(max_length=128, description="Start of an address.")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    principal: Principal = Depends(monitoring_read),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """The organization's monitored addresses, newest first."""
    rows, total = store.list_monitors(
        session,
        principal.organization_id,
        status=status,
        prefix=prefix,
        limit=limit,
        offset=offset,
    )
    unresolved = store.unresolved_alert_counts(session, [row.id for row in rows])
    return {
        "success": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_item(row, unresolved.get(row.id, 0)) for row in rows],
    }


@router.get("/addresses/{monitor_id}")
def get_address(
    monitor_id: IdPath,
    principal: Principal = Depends(monitoring_read),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    monitor = _own(session, principal, monitor_id)
    unresolved = store.unresolved_alert_counts(session, [monitor.id])
    return {"success": True, "monitor": _item(monitor, unresolved.get(monitor.id, 0))}


@router.patch("/addresses/{monitor_id}")
def update_address(
    monitor_id: IdPath,
    body: UpdateMonitor,
    principal: Principal = Depends(monitoring_write),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    """Rename, pause or resume, or change how often and how deeply an address is checked and from
    what severity it raises an alert."""
    if not body.model_fields_set:
        raise APIError(422, "nothing_to_change", "Give at least one field to change.")
    monitor = _own(session, principal, monitor_id)
    try:
        changes = service.update(
            session,
            monitor,
            settings=settings,
            name=body.name if "name" in body.model_fields_set else service.UNSET,
            status=body.status,
            check_interval_seconds=body.check_interval_seconds,
            depth=body.depth,
            min_severity=body.min_severity,
            now=datetime.now(UTC),
        )
    except service.MonitorError as error:
        session.rollback()
        raise _failure(error) from None
    if changes:
        audit.add(
            session,
            "monitor.update",
            target_type="monitor",
            target_id=monitor.id,
            details={"address": monitor.address, "changes": changes},
        )
    session.commit()
    unresolved = store.unresolved_alert_counts(session, [monitor.id])
    return {"success": True, "monitor": _item(monitor, unresolved.get(monitor.id, 0))}


@router.delete("/addresses/{monitor_id}", status_code=204)
def remove_address(
    monitor_id: IdPath,
    principal: Principal = Depends(monitoring_write),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> Response:
    """Stop watching an address. Its alerts and the reports it produced are kept."""
    monitor = _own(session, principal, monitor_id)
    audit.add(
        session,
        "monitor.delete",
        target_type="monitor",
        target_id=monitor.id,
        details={"chain": get_chain(monitor.chain_id).slug, "address": monitor.address},
    )
    session.delete(monitor)
    session.commit()
    return Response(status_code=204)


@router.post("/addresses/{monitor_id}/check", status_code=202)
def check_now(
    monitor_id: IdPath,
    principal: Principal = Depends(monitoring_write),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Queue a check of the address now instead of at its next turn. Asking while a check is
    already queued or running returns that one."""
    monitor = _own(session, principal, monitor_id)
    if monitor.status != store.ACTIVE:
        raise APIError(409, "monitor_paused", "This address is paused: resume it to check it.")
    job, created = service.queue_check(session, monitor, reason=service.MANUAL)
    audit.add(
        session,
        "monitor.check",
        target_type="monitor",
        target_id=monitor.id,
        details={"address": monitor.address, "already_queued": not created},
    )
    session.commit()
    return {"success": True, "job_id": job.id, "already_queued": not created}
