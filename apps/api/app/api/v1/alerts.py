"""Alerts: what monitoring found that is new, and what the team did about it.

An alert is raised by a check of a monitored address (app.monitoring.jobs) and belongs to the
organization of that address. Analysts acknowledge, assign and close alerts; each change is in the
audit log, and an alert's activity is read back from there.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access.audit import MAX_DETAIL_TEXT
from app.api.deps import (
    Audit,
    Principal,
    alerts_manage,
    alerts_read,
    get_audit,
    get_session,
    get_write_session,
)
from app.api.errors import APIError
from app.api.limits import MAX_ID, IdPath
from app.chains.registry import get_chain
from app.database.models import Alert, AuditLog, User
from app.database.repositories import cases as case_store
from app.database.repositories import monitoring as store
from app.monitoring import workflow
from app.risk.service import screening_id

router = APIRouter(prefix="/alerts", tags=["alerts"])

MAX_ACTIVITY = 200


class UpdateAlert(BaseModel):
    """Only what is sent changes. `assignee_id` may be sent as null to take the alert from whoever
    has it. `note` is kept in the alert's activity, for the reason behind a decision."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["open", "acknowledged", "closed"] | None = None
    resolution: Literal["true_positive", "false_positive", "accepted_risk"] | None = None
    assignee_id: int | None = Field(None, ge=1, le=MAX_ID)
    note: str | None = Field(None, max_length=MAX_DETAIL_TEXT)


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _emails(session: Session, ids: set[int | None]) -> dict[int, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = session.execute(select(User.id, User.email).where(User.id.in_(wanted))).all()
    return {user_id: email for user_id, email in rows}


def _person(user_id: int | None, emails: dict[int, str]) -> dict[str, Any] | None:
    return None if user_id is None else {"id": user_id, "email": emails.get(user_id)}


def _item(alert: Alert, emails: dict[int, str]) -> dict[str, Any]:
    spec = get_chain(alert.chain_id)
    return {
        "id": alert.id,
        "kind": alert.kind,
        "severity": alert.severity,
        "status": alert.status,
        "resolution": alert.resolution,
        "title": alert.title,
        "chain": spec.slug,
        "chain_id": alert.chain_id,
        "address": alert.address,
        "display": spec.validator.check(alert.address).display or alert.address,
        "monitor_id": alert.monitored_address_id,
        "assignee": _person(alert.assignee_id, emails),
        "created_at": _iso(alert.created_at),
        "updated_at": _iso(alert.updated_at),
        "closed_at": _iso(alert.closed_at),
    }


def _own(session: Session, principal: Principal, alert_id: int) -> Alert:
    alert = store.get_alert(session, alert_id, principal.organization_id)
    if alert is None:
        raise APIError(404, "alert_not_found", "No such alert.")
    return alert


def _activity(session: Session, alert: Alert) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(AuditLog)
        .where(
            AuditLog.organization_id == alert.organization_id,
            AuditLog.target_type == "alert",
            AuditLog.target_id == str(alert.id),
        )
        .order_by(AuditLog.id)
        .limit(MAX_ACTIVITY)
    ).all()
    return [
        {
            "at": row.created_at.isoformat(),
            "actor": {"type": row.actor_type, "id": row.actor_id, "label": row.actor_label},
            "action": row.action,
            "details": row.details,
        }
        for row in rows
    ]


def _detail(session: Session, alert: Alert) -> dict[str, Any]:
    emails = _emails(session, {alert.assignee_id, alert.closed_by})
    monitor = (
        None
        if alert.monitored_address_id is None
        else store.get_monitor(session, alert.monitored_address_id, alert.organization_id)
    )
    return {
        **_item(alert, emails),
        "summary": alert.summary,
        "evidence": alert.evidence,
        "screening_id": None if alert.assessment_id is None else screening_id(alert.assessment_id),
        "previous_screening_id": None
        if alert.previous_assessment_id is None
        else screening_id(alert.previous_assessment_id),
        "closed_by": _person(alert.closed_by, emails),
        "cases": [
            {"id": case.id, "title": case.title, "status": case.status}
            for case in case_store.cases_of_alert(session, alert.organization_id, alert.id)
        ],
        "monitor": None
        if monitor is None
        else {"id": monitor.id, "name": monitor.name, "status": monitor.status},
        "activity": _activity(session, alert),
    }


@router.get("")
def list_alerts(
    status: Annotated[
        Literal["open", "acknowledged", "closed", "unresolved"] | None,
        Query(description="'unresolved' is open and acknowledged."),
    ] = None,
    severity: Annotated[Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None, Query()] = None,
    kind: Annotated[str | None, Query(max_length=24)] = None,
    monitor_id: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None,
    assignee_id: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None,
    mine: Annotated[bool, Query(description="Only the alerts assigned to the caller.")] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    principal: Principal = Depends(alerts_read),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """The organization's alerts, newest first (without their evidence: read one for that)."""
    if mine:
        assignee_id = principal.owner_id
    rows, total = store.list_alerts(
        session,
        principal.organization_id,
        status=status,
        severity=severity,
        monitor_id=monitor_id,
        assignee_id=assignee_id,
        kind=kind,
        limit=limit,
        offset=offset,
    )
    emails = _emails(session, {row.assignee_id for row in rows})
    return {
        "success": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_item(row, emails) for row in rows],
    }


@router.get("/summary")
def alert_summary(
    principal: Principal = Depends(alerts_read), session: Session = Depends(get_session)
) -> dict[str, Any]:
    """How many alerts there are by status and how severe the unresolved ones are: what a badge or
    a dashboard shows."""
    counts = store.alert_counts(session, principal.organization_id)
    by_status = {status: counts["status"].get(status, 0) for status in workflow.STATUSES}
    return {
        "success": True,
        "by_status": by_status,
        "unresolved": by_status[store.OPEN] + by_status[store.ACKNOWLEDGED],
        "unresolved_by_severity": counts["unresolved_by_severity"],
    }


@router.get("/{alert_id}")
def get_alert(
    alert_id: IdPath,
    principal: Principal = Depends(alerts_read),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """One alert with the evidence that raised it, the reports before and after, and what has been
    done about it."""
    return {"success": True, "alert": _detail(session, _own(session, principal, alert_id))}


@router.patch("/{alert_id}")
def update_alert(
    alert_id: IdPath,
    body: UpdateAlert,
    principal: Principal = Depends(alerts_manage),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Acknowledge, assign, close (with how it ended) or reopen an alert. A `note` alone is a
    comment on the alert: it goes into the alert's activity."""
    note = (body.note or "").strip()
    if not (body.model_fields_set - {"note"}) and not note:
        raise APIError(
            422, "nothing_to_change", "Give a status, a resolution, an assignee or a note."
        )
    alert = _own(session, principal, alert_id)
    try:
        changes = workflow.change(
            session,
            alert,
            status=body.status,
            resolution=body.resolution,
            assignee_id=body.assignee_id
            if "assignee_id" in body.model_fields_set
            else workflow.UNSET,
            by=principal.owner_id,
            now=datetime.now(UTC),
        )
    except workflow.AlertError as error:
        session.rollback()
        code, status_code = (
            ("conflict", 409)
            if isinstance(error, workflow.AlertConflict)
            else ("invalid_alert_change", 422)
        )
        raise APIError(status_code, code, str(error)) from None
    if changes or note:
        details: dict[str, Any] = {"address": alert.address, "changes": changes}
        if note:
            details["note"] = note
        audit.add(
            session,
            "alert.update",
            target_type="alert",
            target_id=alert.id,
            details=details,
        )
    session.commit()
    return {"success": True, "alert": _detail(session, alert)}
