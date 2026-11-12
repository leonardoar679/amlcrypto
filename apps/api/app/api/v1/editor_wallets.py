"""The editor's side of wallet profiles: writing what the public pages show.

Only a platform editor (`users.is_platform_editor`, set from the command line) holds the permission
these routes need, and an API key never does (it is the one that writes incidents: editorial
curation). A profile is written as one document (`PUT .../content`) and moved through its states
(`POST .../transition`). Every change leaves an audit entry that belongs to the platform, not to the
editor's organization.
"""

from __future__ import annotations

from datetime import date, datetime
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
from app.api.v1.editor_incidents import EvidenceBody, Ref, TransitionBody
from app.database.models import WalletProfile
from app.editorial import SLUG_PATTERN, EditorialConflict, EditorialError, EvidenceInput
from app.wallets import projection, service, views
from app.wallets.model import (
    MAX_ADDRESSES,
    MAX_LABEL,
    MAX_NAME,
    MAX_NOTE,
    MAX_SUMMARY,
    AttributionStatus,
    ProfileStatus,
    WalletKind,
)

router = APIRouter(prefix="/editor/wallets", tags=["editor"])


class AddressBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain: str = Field(min_length=1, max_length=100, description="A chain slug or a CAIP-2 id.")
    address: str = Field(min_length=1, max_length=512)
    label: str = Field(min_length=1, max_length=MAX_LABEL, description="What it is: 'hot wallet'.")
    attribution_status: AttributionStatus
    confidence: int | None = Field(
        None, ge=0, le=100, description="The status gives it if left out."
    )
    evidence_ref: Ref | None = None
    note: str | None = Field(None, max_length=500)
    last_verified_at: datetime | None = None


class ContentBody(BaseModel):
    """The whole content of a profile. It replaces what a draft or a profile in review holds."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME)
    kind: WalletKind
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY)
    website: str | None = Field(None, max_length=500)
    attribution_note: str | None = Field(None, max_length=MAX_NOTE, description="Shown publicly.")
    internal_note: str | None = Field(None, max_length=MAX_NOTE, description="Editors only.")
    evidence: list[EvidenceBody] = Field(default_factory=list, max_length=50)
    addresses: list[AddressBody] = Field(default_factory=list, max_length=MAX_ADDRESSES)


class CreateBody(ContentBody):
    slug: str = Field(
        min_length=3,
        max_length=80,
        pattern=SLUG_PATTERN,
        description="The permanent address of the page: lowercase letters and digits, hyphens.",
    )


# --- from the request to the service ------------------------------------------------------------


def _day(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise EditorialError(f"{value!r} is not a date.") from None


def _content(body: ContentBody) -> service.Content:
    return service.Content(
        name=body.name,
        kind=body.kind.value,
        summary=body.summary,
        website=body.website,
        attribution_note=body.attribution_note,
        internal_note=body.internal_note,
        evidence=tuple(
            EvidenceInput(
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
        addresses=tuple(
            service.AddressInput(
                chain=item.chain,
                address=item.address,
                label=item.label,
                attribution_status=item.attribution_status.value,
                confidence=item.confidence,
                evidence_ref=item.evidence_ref,
                note=item.note,
                last_verified_at=item.last_verified_at,
            )
            for item in body.addresses
        ),
    )


def _failure(error: EditorialError) -> APIError:
    message = " ".join([str(error), *error.problems])
    if isinstance(error, EditorialConflict):
        return APIError(409, "wallet_conflict", message)
    return APIError(422, "invalid_wallet", message)


def _find(session: Session, profile_id: int) -> WalletProfile:
    profile = session.get(WalletProfile, profile_id)
    if profile is None:
        raise APIError(404, "wallet_not_found", "No such wallet profile.")
    return profile


def _editor(principal: Principal) -> tuple[int, Actor]:
    """Who is writing, and the actor the audit log names: the person, in no organization."""
    assert principal.user_id is not None  # only a person can be an editor
    return principal.user_id, Actor(ACTOR_USER, principal.user_id, principal.email, None)


def _detail(session: Session, profile: WalletProfile) -> dict[str, Any]:
    return {"success": True, "wallet": views.editor_detail(session, profile)}


# --- the routes ---------------------------------------------------------------------------------


@router.get("")
def list_wallets(
    status: Annotated[ProfileStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    principal: Principal = Depends(incidents_curate),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Every profile, drafts included, the most recently changed first."""
    items, total = views.list_for_editor(
        session, status=None if status is None else status.value, limit=limit, offset=offset
    )
    return {"success": True, "total": total, "limit": limit, "offset": offset, "items": items}


@router.post("", status_code=201)
def create_wallet(
    body: CreateBody,
    principal: Principal = Depends(incidents_curate),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Start a profile as a draft. Its slug is the permanent address of its page."""
    user_id, actor = _editor(principal)
    try:
        profile = service.create_profile(
            session, user_id=user_id, slug=body.slug, content=_content(body)
        )
    except EditorialError as error:
        session.rollback()
        raise _failure(error) from None
    audit.add(
        session,
        "wallet.create",
        target_type="wallet_profile",
        target_id=profile.id,
        details={"slug": profile.slug, "name": profile.name, "kind": profile.kind},
        actor=actor,
    )
    session.commit()
    return _detail(session, profile)


@router.get("/{profile_id}")
def get_wallet(
    profile_id: IdPath,
    principal: Principal = Depends(incidents_curate),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _detail(session, _find(session, profile_id))


@router.put("/{profile_id}/content")
def replace_content(
    profile_id: IdPath,
    body: ContentBody,
    principal: Principal = Depends(incidents_curate),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Replace the whole content of a draft or a profile in review. A published profile is changed
    by taking it back to review first."""
    user_id, actor = _editor(principal)
    profile = _find(session, profile_id)
    try:
        service.replace_content(session, profile, _content(body), user_id=user_id)
    except EditorialError as error:
        session.rollback()
        raise _failure(error) from None
    audit.add(
        session,
        "wallet.content",
        target_type="wallet_profile",
        target_id=profile.id,
        details={
            "slug": profile.slug,
            "status": profile.status,
            "evidence": len(body.evidence),
            "addresses": len(body.addresses),
        },
        actor=actor,
    )
    session.commit()
    return _detail(session, profile)


@router.post("/{profile_id}/transition")
def move_wallet(
    profile_id: IdPath,
    body: TransitionBody,
    principal: Principal = Depends(incidents_curate),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Move a profile to review, publish it, take it back to review or archive it."""
    user_id, actor = _editor(principal)
    profile = _find(session, profile_id)
    before = profile.status
    try:
        service.transition(session, profile, body.to.value, user_id=user_id)
    except EditorialError as error:
        session.rollback()
        raise _failure(error) from None
    audit.add(
        session,
        "wallet.status",
        target_type="wallet_profile",
        target_id=profile.id,
        details={
            "slug": profile.slug,
            "from": before,
            "to": profile.status,
            # What screening now takes from this profile (see app.wallets.projection).
            "labels_in_force": projection.labels_in_force(session, profile),
        },
        actor=actor,
    )
    session.commit()
    return _detail(session, profile)


@router.delete("/{profile_id}", status_code=204)
def delete_wallet(
    profile_id: IdPath,
    principal: Principal = Depends(incidents_curate),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> Response:
    """Delete a draft that was never published. Anything that was ever shown is archived."""
    _, actor = _editor(principal)
    profile = _find(session, profile_id)
    details = {"slug": profile.slug, "name": profile.name}
    try:
        service.delete_profile(session, profile)
    except EditorialError as error:
        session.rollback()
        raise _failure(error) from None
    audit.add(
        session,
        "wallet.delete",
        target_type="wallet_profile",
        target_id=profile_id,
        details=details,
        actor=actor,
    )
    session.commit()
    return Response(status_code=204)
