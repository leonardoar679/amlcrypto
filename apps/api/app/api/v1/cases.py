"""Cases: investigations a team opens about an address, from an alert or by hand.

A case has a status, a priority, an owner, notes, and links to what it is about and relied on. Its
timeline is only ever added to. A case belongs to one organization and is invisible to the others.
`GET /cases/{id}/export` produces the whole case file as JSON, with the linked alerts and the
reports as they were kept.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    Audit,
    Principal,
    cases_read,
    cases_write,
    get_audit,
    get_session,
    get_write_session,
)
from app.api.errors import APIError
from app.api.limits import MAX_ID, IdPath
from app.cases import service
from app.chains.registry import get_chain
from app.database.models import Alert, Case, CaseEvent, CaseLink, User
from app.database.repositories import cases as store
from app.database.repositories import monitoring as alert_store
from app.database.repositories import risk as risk_store
from app.graph.service import GraphRequestError, resolve
from app.monitoring import workflow as alert_workflow
from app.risk.service import parse_screening_id, screening_id

router = APIRouter(prefix="/cases", tags=["cases"])

Priority = Literal["low", "medium", "high", "critical"]
MAX_EXPORTED_REPORTS = 25


class OpenCase(BaseModel):
    """A case opened by hand names its address (chain and address together, or neither); one opened
    from an alert takes the alert's."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=1000)]
    description: str | None = Field(None, max_length=service.MAX_DESCRIPTION_LENGTH + 1000)
    priority: Priority | None = None
    assignee_id: int | None = Field(None, ge=1, le=MAX_ID)
    alert_id: int | None = Field(
        None, ge=1, le=MAX_ID, description="Open the case from this alert."
    )
    chain: str | None = Field(None, max_length=100)
    address: str | None = Field(None, max_length=512)


class UpdateCase(BaseModel):
    """Only what is sent changes. `description` and `assignee_id` may be sent as null to clear
    them."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, max_length=1000)
    description: str | None = Field(None, max_length=service.MAX_DESCRIPTION_LENGTH + 1000)
    priority: Priority | None = None
    status: Literal["open", "in_progress", "closed"] | None = None
    outcome: Literal["confirmed", "dismissed", "escalated"] | None = None
    assignee_id: int | None = Field(None, ge=1, le=MAX_ID)


class NewNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: Annotated[str, Field(min_length=1, max_length=service.MAX_NOTE_LENGTH + 1000)]


class NewLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["address", "transaction", "screening", "alert"]
    reference: Annotated[str, Field(min_length=1, max_length=512)]
    chain: str | None = Field(None, max_length=100)
    note: str | None = Field(None, max_length=1000)


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


def _address_fields(case: Case) -> dict[str, Any]:
    if case.chain_id is None or case.address is None:
        return {"chain": None, "chain_id": None, "address": None, "display": None}
    spec = get_chain(case.chain_id)
    return {
        "chain": spec.slug,
        "chain_id": case.chain_id,
        "address": case.address,
        "display": spec.validator.check(case.address).display or case.address,
    }


def _item(case: Case, emails: dict[int, str]) -> dict[str, Any]:
    return {
        "id": case.id,
        "title": case.title,
        "status": case.status,
        "priority": case.priority,
        "outcome": case.outcome,
        **_address_fields(case),
        "source_alert_id": case.source_alert_id,
        "assignee": _person(case.assignee_id, emails),
        "created_by": _person(case.created_by, emails),
        "created_at": _iso(case.created_at),
        "updated_at": _iso(case.updated_at),
        "closed_at": _iso(case.closed_at),
    }


def _link_item(link: CaseLink) -> dict[str, Any]:
    chain = get_chain(link.chain_id).slug if link.chain_id else None
    return {
        "id": link.id,
        "kind": link.kind,
        "chain": chain,
        "chain_id": link.chain_id,
        "reference": link.reference,
        "note": link.note,
        "created_at": _iso(link.created_at),
    }


def _event_item(event: CaseEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "kind": event.kind,
        "at": _iso(event.created_at),
        "actor": {"type": event.actor_type, "id": event.actor_id, "label": event.actor_label},
        "body": event.body,
        "data": event.data,
    }


def _own(session: Session, principal: Principal, case_id: int) -> Case:
    case = store.get_case(session, case_id, principal.organization_id)
    if case is None:
        raise APIError(404, "case_not_found", "No such case.")
    return case


def _failure(error: service.CaseError) -> APIError:
    if isinstance(error, service.CaseConflict):
        return APIError(409, "conflict", str(error))
    return APIError(422, "invalid_case", str(error))


def _detail(session: Session, case: Case) -> dict[str, Any]:
    events = store.events_of(session, case.id)
    links = store.links_of(session, case.id)
    emails = _emails(session, {case.assignee_id, case.created_by, case.closed_by})
    alert = (
        None
        if case.source_alert_id is None
        else alert_store.get_alert(session, case.source_alert_id, case.organization_id)
    )
    return {
        **_item(case, emails),
        "description": case.description,
        "closed_by": _person(case.closed_by, emails),
        "alert": None
        if alert is None
        else {
            "id": alert.id,
            "title": alert.title,
            "severity": alert.severity,
            "status": alert.status,
        },
        "links": [_link_item(link) for link in links],
        "events": [_event_item(event) for event in events],
    }


@router.post("", status_code=201)
def open_case(
    body: OpenCase,
    principal: Principal = Depends(cases_write),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Open a case. From an alert (`alert_id`) it is about the alert's address, links the alert and
    the report that raised it, and acknowledges the alert if it was still open."""
    if (body.chain is None) != (body.address is None):
        raise APIError(422, "invalid_case", "Give the chain and the address together, or neither.")
    now = datetime.now(UTC)
    assert principal.owner_id is not None  # a person, or the person whose key this is

    alert: Alert | None = None
    subject: tuple[str, str] | None = None
    if body.alert_id is not None:
        if body.chain is not None:
            raise APIError(422, "invalid_case", "An alert already names the address.")
        alert = alert_store.get_alert(session, body.alert_id, principal.organization_id)
        if alert is None:
            raise APIError(422, "invalid_case", "No such alert.")
    elif body.chain is not None and body.address is not None:
        try:
            chain, address = resolve(body.chain, body.address)
        except GraphRequestError as error:
            if error.chain_unknown:
                raise APIError(404, "unknown_chain", "That chain is not supported.") from error
            raise APIError(422, "invalid_address", str(error)) from error
        subject = (chain.chain_id, address)

    try:
        case = service.open_case(
            session,
            organization_id=principal.organization_id,
            created_by=principal.owner_id,
            actor=principal.actor,
            title=body.title,
            description=body.description,
            priority=body.priority,
            assignee_id=body.assignee_id,
            subject=subject,
            alert=alert,
            now=now,
        )
    except service.CaseError as error:
        session.rollback()
        raise _failure(error) from None
    audit.add(
        session,
        "case.create",
        target_type="case",
        target_id=case.id,
        details={
            "title": case.title,
            "priority": case.priority,
            "alert_id": body.alert_id,
            "chain": get_chain(case.chain_id).slug if case.chain_id else None,
            "address": case.address,
        },
    )
    if alert is not None and alert.status == alert_store.OPEN:
        changes = alert_workflow.change(
            session, alert, status=alert_store.ACKNOWLEDGED, by=principal.owner_id, now=now
        )
        audit.add(
            session,
            "alert.update",
            target_type="alert",
            target_id=alert.id,
            details={
                "address": alert.address,
                "changes": changes,
                "reason": "case_opened",
                "case_id": case.id,
            },
        )
    session.commit()
    return {"success": True, "case": _detail(session, case)}


@router.get("")
def list_cases(
    status: Annotated[
        Literal["open", "in_progress", "closed", "unresolved"] | None,
        Query(description="'unresolved' is open and in progress."),
    ] = None,
    priority: Annotated[Priority | None, Query()] = None,
    assignee_id: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None,
    mine: Annotated[bool, Query(description="Only the cases assigned to the caller.")] = False,
    chain: Annotated[str | None, Query(max_length=100)] = None,
    address: Annotated[str | None, Query(max_length=512)] = None,
    q: Annotated[str | None, Query(max_length=100, description="Start of a title.")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    principal: Principal = Depends(cases_read),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """The organization's cases, newest first. `chain` and `address` together find the cases about
    one address."""
    if (chain is None) != (address is None):
        raise APIError(422, "invalid_case", "Give the chain and the address together, or neither.")
    chain_id = None
    if chain is not None and address is not None:
        try:
            found, address = resolve(chain, address)
        except GraphRequestError as error:
            raise APIError(422, "invalid_address", str(error)) from error
        chain_id = found.chain_id
    if mine:
        assignee_id = principal.owner_id
    rows, total = store.list_cases(
        session,
        principal.organization_id,
        status=status,
        priority=priority,
        assignee_id=assignee_id,
        chain_id=chain_id,
        address=address,
        prefix=q,
        limit=limit,
        offset=offset,
    )
    emails = _emails(session, {i for row in rows for i in (row.assignee_id, row.created_by)})
    return {
        "success": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_item(row, emails) for row in rows],
    }


@router.get("/summary")
def case_summary(
    principal: Principal = Depends(cases_read), session: Session = Depends(get_session)
) -> dict[str, Any]:
    counts = store.case_counts(session, principal.organization_id)
    by_status = {status: counts["status"].get(status, 0) for status in service.STATUSES}
    return {
        "success": True,
        "by_status": by_status,
        "unresolved": by_status[service.OPEN] + by_status[service.IN_PROGRESS],
        "unresolved_by_priority": counts["unresolved_by_priority"],
    }


@router.get("/{case_id}")
def get_case(
    case_id: IdPath,
    principal: Principal = Depends(cases_read),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {"success": True, "case": _detail(session, _own(session, principal, case_id))}


@router.patch("/{case_id}")
def update_case(
    case_id: IdPath,
    body: UpdateCase,
    principal: Principal = Depends(cases_write),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Edit the title or description, change the priority, start work, assign, close (with how it
    ended) or reopen."""
    given = body.model_fields_set
    if not given:
        raise APIError(422, "nothing_to_change", "Give at least one field to change.")
    case = _own(session, principal, case_id)
    try:
        changes = service.change_case(
            session,
            case,
            actor=principal.actor,
            by=principal.owner_id,
            now=datetime.now(UTC),
            title=(body.title or "") if "title" in given else service.UNSET,
            description=body.description if "description" in given else service.UNSET,
            priority=body.priority,
            status=body.status,
            outcome=body.outcome,
            assignee_id=body.assignee_id if "assignee_id" in given else service.UNSET,
        )
    except service.CaseError as error:
        session.rollback()
        raise _failure(error) from None
    if changes:
        audit.add(
            session,
            "case.update",
            target_type="case",
            target_id=case.id,
            details={"changes": changes},
        )
    session.commit()
    return {"success": True, "case": _detail(session, case)}


@router.post("/{case_id}/notes", status_code=201)
def add_note(
    case_id: IdPath,
    body: NewNote,
    principal: Principal = Depends(cases_write),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Write on the case. Notes cannot be changed or removed afterwards."""
    case = _own(session, principal, case_id)
    try:
        event = service.add_note(
            session, case, body=body.body, actor=principal.actor, now=datetime.now(UTC)
        )
    except service.CaseError as error:
        raise _failure(error) from None
    session.flush()
    audit.add(
        session,
        "case.note",
        target_type="case",
        target_id=case.id,
        details={"event_id": event.id, "note": event.body},
    )
    session.commit()
    return {"success": True, "event": _event_item(event)}


@router.post("/{case_id}/links", status_code=201)
def add_link(
    case_id: IdPath,
    body: NewLink,
    principal: Principal = Depends(cases_write),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Link the case to an address (with its chain), a transaction (with its chain), a screening
    (`scr_12`) or an alert (its number) of the organization."""
    case = _own(session, principal, case_id)
    try:
        link = service.add_link(
            session,
            case,
            kind=body.kind,
            chain=body.chain,
            reference=body.reference,
            note=body.note,
            actor=principal.actor,
            created_by=principal.owner_id,
            now=datetime.now(UTC),
        )
    except service.CaseError as error:
        raise _failure(error) from None
    audit.add(
        session,
        "case.link.add",
        target_type="case",
        target_id=case.id,
        details={"kind": link.kind, "chain_id": link.chain_id, "reference": link.reference},
    )
    session.commit()
    return {"success": True, "link": _link_item(link)}


@router.delete("/{case_id}/links/{link_id}", status_code=204)
def remove_link(
    case_id: IdPath,
    link_id: IdPath,
    principal: Principal = Depends(cases_write),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> Response:
    case = _own(session, principal, case_id)
    link = store.get_link(session, case.id, link_id)
    if link is None:
        raise APIError(404, "link_not_found", "No such link.")
    audit.add(
        session,
        "case.link.remove",
        target_type="case",
        target_id=case.id,
        details={"kind": link.kind, "chain_id": link.chain_id, "reference": link.reference},
    )
    service.remove_link(session, case, link, actor=principal.actor, now=datetime.now(UTC))
    session.commit()
    return Response(status_code=204)


def _alert_record(alert: Alert) -> dict[str, Any]:
    spec = get_chain(alert.chain_id)
    return {
        "id": alert.id,
        "kind": alert.kind,
        "severity": alert.severity,
        "status": alert.status,
        "resolution": alert.resolution,
        "title": alert.title,
        "summary": alert.summary,
        "chain": spec.slug,
        "address": alert.address,
        "evidence": alert.evidence,
        "screening_id": None if alert.assessment_id is None else screening_id(alert.assessment_id),
        "previous_screening_id": None
        if alert.previous_assessment_id is None
        else screening_id(alert.previous_assessment_id),
        "created_at": _iso(alert.created_at),
        "closed_at": _iso(alert.closed_at),
    }


@router.get("/{case_id}/export")
def export_case(
    case_id: IdPath,
    principal: Principal = Depends(cases_read),
    session: Session = Depends(get_session),
    audit: Audit = Depends(get_audit),
) -> Response:
    """The whole case file as JSON: the case with its timeline and links, the alerts it is linked
    to and the reports it is linked to, each as it was kept. The file says when and by whom it was
    produced; it is a record of what the platform held then, not a verdict."""
    case = _own(session, principal, case_id)
    detail = _detail(session, case)
    links = store.links_of(session, case.id)

    alerts: list[dict[str, Any]] = []
    for link in links:
        if link.kind == "alert":
            alert = alert_store.get_alert(session, int(link.reference), case.organization_id)
            if alert is not None:
                alerts.append(_alert_record(alert))
    reports: list[dict[str, Any]] = []
    for link in [item for item in links if item.kind == "screening"][:MAX_EXPORTED_REPORTS]:
        number = parse_screening_id(link.reference)
        row = (
            None
            if number is None
            else risk_store.get_assessment(session, number, case.organization_id)
        )
        if row is not None:
            reports.append(
                {
                    "screening_id": screening_id(row.id),
                    "status": row.status,
                    "subject_type": row.subject_type,
                    "chain_id": row.chain_id,
                    "subject": row.subject,
                    "completed_at": _iso(row.completed_at),
                    "report": row.report,
                }
            )
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "generated_by": principal.actor.label,
        "case": detail,
        "alerts": alerts,
        "screenings": reports,
        "note": (
            "A record of what the platform held when this file was produced. A report that found "
            "nothing is not a clearance of the address: it states what was and was not examined."
        ),
    }
    number = case.id
    content = json.dumps(payload, indent=2, default=str)
    session.rollback()  # all that is needed is in hand; the entry below takes a connection itself
    audit.now(
        "case.export",
        target_type="case",
        target_id=number,
        details={"links": len(links), "reports": len(reports)},
    )
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="case-{number}.json"'},
    )
