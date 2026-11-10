"""The editor's side of incidents: writing what the public pages show.

Only a platform editor (`users.is_platform_editor`, set from the command line) holds the
permission these routes need, and an API key never does. An incident is written as one document
(`PUT .../content`), moved through its states (`POST .../transition`) and, once published, only
added to (`POST .../updates`). Every change leaves an audit entry that belongs to the platform,
not to the editor's organization: what an editor does to public pages is not something that
organization's other people should read in their own log.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.access.audit import ACTOR_USER, Actor
from app.api.deps import (
    Audit,
    Principal,
    get_audit,
    get_session,
    get_write_session,
    incidents_curate,
)
from app.api.errors import APIError
from app.api.limits import IdPath
from app.database.models import Incident
from app.incidents import projection, service, views
from app.incidents.model import (
    MAX_ADDRESSES,
    MAX_AMOUNTS,
    MAX_EVIDENCE,
    MAX_EXCERPT,
    MAX_NOTE,
    MAX_SUMMARY,
    MAX_TITLE,
    MAX_UPDATE_BODY,
    MAX_UPDATES,
    MAX_USD,
    SLUG_PATTERN,
    AddressRole,
    AttributionStatus,
    EvidenceKind,
    IncidentKind,
    IncidentStatus,
    UpdateKind,
)

router = APIRouter(prefix="/editor/incidents", tags=["editor"])

DateText = Annotated[str, Field(min_length=10, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$")]
Ref = Annotated[str, Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9_-]+$")]


class EvidenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: Ref
    publisher: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1, max_length=500)
    kind: EvidenceKind
    retrieved_on: DateText
    published_on: DateText | None = None
    excerpt: str | None = Field(None, max_length=MAX_EXCERPT)


class AmountBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_ref: Ref
    amount_usd_low: int = Field(ge=0, le=MAX_USD)
    amount_usd_high: int | None = Field(None, ge=0, le=MAX_USD)
    as_of: DateText | None = None
    label: str | None = Field(None, max_length=100)


class AddressBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain: str = Field(min_length=1, max_length=100, description="A chain slug or a CAIP-2 id.")
    address: str = Field(min_length=1, max_length=512)
    role: AddressRole
    attribution_status: AttributionStatus
    confidence: int | None = Field(
        None, ge=0, le=100, description="The status gives it if left out."
    )
    evidence_ref: Ref | None = None
    note: str | None = Field(None, max_length=500)
    last_verified_at: datetime | None = None


class UpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurred_at: datetime
    kind: UpdateKind
    body: str = Field(min_length=1, max_length=MAX_UPDATE_BODY)
    evidence_ref: Ref | None = None
    chain: str | None = Field(None, max_length=100)
    tx_hash: str | None = Field(None, max_length=100)


class ContentBody(BaseModel):
    """The whole content of an incident. It replaces what a draft or an incident in review holds."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY)
    kind: IncidentKind
    occurred_on: DateText | None = None
    attribution_note: str | None = Field(None, max_length=MAX_NOTE, description="Shown publicly.")
    internal_note: str | None = Field(None, max_length=MAX_NOTE, description="Editors only.")
    evidence: list[EvidenceBody] = Field(default_factory=list, max_length=MAX_EVIDENCE)
    amounts: list[AmountBody] = Field(default_factory=list, max_length=MAX_AMOUNTS)
    addresses: list[AddressBody] = Field(default_factory=list, max_length=MAX_ADDRESSES)
    updates: list[UpdateBody] = Field(default_factory=list, max_length=MAX_UPDATES)


class CreateBody(ContentBody):
    slug: str = Field(
        min_length=3,
        max_length=80,
        pattern=SLUG_PATTERN,
        description="The permanent address of the page: lowercase letters and digits, hyphens.",
    )


class TransitionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: IncidentStatus


# --- from the request to the service ------------------------------------------------------------


def _day(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise service.IncidentError(f"{value!r} is not a date.") from None


def _update(body: UpdateBody) -> service.UpdateInput:
    return service.UpdateInput(
        occurred_at=body.occurred_at,
        kind=body.kind.value,
        body=body.body,
        evidence_ref=body.evidence_ref,
        chain=body.chain,
        tx_hash=body.tx_hash,
    )


def _content(body: ContentBody) -> service.Content:
    return service.Content(
        title=body.title,
        summary=body.summary,
        kind=body.kind.value,
        occurred_on=_day(body.occurred_on),
        attribution_note=body.attribution_note,
        internal_note=body.internal_note,
        evidence=tuple(
            service.EvidenceInput(
                ref=item.ref,
                publisher=item.publisher,
                title=item.title,
                url=item.url,
                kind=item.kind.value,
                retrieved_on=_day(item.retrieved_on),
                published_on=_day(item.published_on),
                excerpt=item.excerpt,
            )
            for item in body.evidence
        ),
        amounts=tuple(
            service.AmountInput(
                evidence_ref=item.evidence_ref,
                amount_usd_low=item.amount_usd_low,
                amount_usd_high=item.amount_usd_high,
                as_of=_day(item.as_of),
                label=item.label,
            )
            for item in body.amounts
        ),
        addresses=tuple(
            service.AddressInput(
                chain=item.chain,
                address=item.address,
                role=item.role.value,
                attribution_status=item.attribution_status.value,
                confidence=item.confidence,
                evidence_ref=item.evidence_ref,
                note=item.note,
                last_verified_at=item.last_verified_at,
            )
            for item in body.addresses
        ),
        updates=tuple(_update(item) for item in body.updates),
    )


def _failure(error: service.IncidentError) -> APIError:
    message = " ".join([str(error), *error.problems])
    if isinstance(error, service.IncidentConflict):
        return APIError(409, "incident_conflict", message)
    return APIError(422, "invalid_incident", message)


def _find(session: Session, incident_id: int) -> Incident:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise APIError(404, "incident_not_found", "No such incident.")
    return incident


def _editor(principal: Principal) -> tuple[int, Actor]:
    """Who is writing, and the actor the audit log names: the person, in no organization."""
    assert principal.user_id is not None  # only a person can be an editor
    return principal.user_id, Actor(ACTOR_USER, principal.user_id, principal.email, None)


def _detail(session: Session, incident: Incident) -> dict[str, Any]:
    return {"success": True, "incident": views.editor_detail(session, incident)}


# --- the routes ---------------------------------------------------------------------------------


@router.get("")
def list_incidents(
    status: Annotated[IncidentStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    principal: Principal = Depends(incidents_curate),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Every incident, drafts included, the most recently changed first."""
    items, total = views.list_for_editor(
        session, status=None if status is None else status.value, limit=limit, offset=offset
    )
    return {"success": True, "total": total, "limit": limit, "offset": offset, "items": items}


@router.post("", status_code=201)
def create_incident(
    body: CreateBody,
    principal: Principal = Depends(incidents_curate),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Start an incident as a draft. Its slug is the permanent address of its page."""
    user_id, actor = _editor(principal)
    try:
        incident = service.create_incident(
            session, user_id=user_id, slug=body.slug, content=_content(body)
        )
    except service.IncidentError as error:
        session.rollback()
        raise _failure(error) from None
    audit.add(
        session,
        "incident.create",
        target_type="incident",
        target_id=incident.id,
        details={"slug": incident.slug, "title": incident.title, "kind": incident.kind},
        actor=actor,
    )
    session.commit()
    return _detail(session, incident)


@router.get("/{incident_id}")
def get_incident(
    incident_id: IdPath,
    principal: Principal = Depends(incidents_curate),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _detail(session, _find(session, incident_id))


@router.put("/{incident_id}/content")
def replace_content(
    incident_id: IdPath,
    body: ContentBody,
    principal: Principal = Depends(incidents_curate),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Replace the whole content of a draft or an incident in review. A published incident only
    grows: add an update, or move it back to review first."""
    user_id, actor = _editor(principal)
    incident = _find(session, incident_id)
    try:
        service.replace_content(session, incident, _content(body), user_id=user_id)
    except service.IncidentError as error:
        session.rollback()
        raise _failure(error) from None
    audit.add(
        session,
        "incident.content",
        target_type="incident",
        target_id=incident.id,
        details={
            "slug": incident.slug,
            "status": incident.status,
            "evidence": len(body.evidence),
            "amounts": len(body.amounts),
            "addresses": len(body.addresses),
            "updates": len(body.updates),
        },
        actor=actor,
    )
    session.commit()
    return _detail(session, incident)


@router.post("/{incident_id}/transition")
def move_incident(
    incident_id: IdPath,
    body: TransitionBody,
    principal: Principal = Depends(incidents_curate),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Move an incident to review, publish it, take it back to review or archive it."""
    user_id, actor = _editor(principal)
    incident = _find(session, incident_id)
    before = incident.status
    try:
        service.transition(session, incident, body.to.value, user_id=user_id)
    except service.IncidentError as error:
        session.rollback()
        raise _failure(error) from None
    audit.add(
        session,
        "incident.status",
        target_type="incident",
        target_id=incident.id,
        details={
            "slug": incident.slug,
            "from": before,
            "to": incident.status,
            # What screening now acts on because of this incident (see app.incidents.projection).
            "labels_in_force": projection.labels_in_force(session, incident),
        },
        actor=actor,
    )
    session.commit()
    return _detail(session, incident)


@router.post("/{incident_id}/updates", status_code=201)
def add_update(
    incident_id: IdPath,
    body: UpdateBody,
    principal: Principal = Depends(incidents_curate),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Add a timeline entry in any state. After publication this is the way an incident changes: a
    correction is an update of kind `correction`."""
    user_id, actor = _editor(principal)
    incident = _find(session, incident_id)
    try:
        service.append_update(
            session, incident, _update(body), user_id=user_id, now=datetime.now(UTC)
        )
    except service.IncidentError as error:
        session.rollback()
        raise _failure(error) from None
    audit.add(
        session,
        "incident.update",
        target_type="incident",
        target_id=incident.id,
        details={"slug": incident.slug, "status": incident.status, "kind": body.kind.value},
        actor=actor,
    )
    session.commit()
    return _detail(session, incident)


@router.delete("/{incident_id}", status_code=204)
def delete_incident(
    incident_id: IdPath,
    principal: Principal = Depends(incidents_curate),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> Response:
    """Delete a draft that was never published. Anything else is archived, never deleted."""
    _, actor = _editor(principal)
    incident = _find(session, incident_id)
    details = {"slug": incident.slug, "title": incident.title}
    number = incident.id
    try:
        service.delete_incident(session, incident)
    except service.IncidentError as error:
        session.rollback()
        raise _failure(error) from None
    audit.add(
        session,
        "incident.delete",
        target_type="incident",
        target_id=number,
        details=details,
        actor=actor,
    )
    session.commit()
    return Response(status_code=204)
