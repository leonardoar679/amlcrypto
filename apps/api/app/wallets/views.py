"""What an editor is shown of a wallet profile.

An editor sees all of it, drafts and internal notes included. (Everybody else is shown a published
profile through a separate, narrower set of functions, app.wallets.public, so that nothing an
editor alone should see can reach a public answer by being added to a row.)
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.chains.models import iso
from app.chains.registry import get_chain
from app.database.models import User, WalletProfile, WalletProfileAddress, WalletProfileEvidence
from app.incidents.views import evidence_view
from app.wallets import service
from app.wallets.model import EDITABLE, KIND_WORDS, STATUS_WORDS, TRANSITIONS, ProfileStatus


def _date(value) -> str | None:
    return None if value is None else value.isoformat()


def address_view(row: WalletProfileAddress, refs: dict[int, str]) -> dict[str, Any]:
    return {
        "chain": get_chain(row.chain_id).slug,
        "address": row.address,
        "label": row.label,
        "attribution_status": row.attribution_status,
        "confidence": row.confidence,
        "evidence_ref": None if row.evidence_id is None else refs.get(row.evidence_id),
        "note": row.note,
        "last_verified_at": iso(row.last_verified_at),
    }


def _emails(session: Session, ids: set[int | None]) -> dict[int, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = session.execute(select(User.id, User.email).where(User.id.in_(wanted))).all()
    return {user_id: email for user_id, email in rows}


def _person(user_id: int | None, emails: dict[int, str]) -> dict[str, Any] | None:
    return None if user_id is None else {"id": user_id, "email": emails.get(user_id)}


def editor_item(
    row: WalletProfile, counts: dict[str, int], emails: dict[int, str] | None = None
) -> dict[str, Any]:
    return {
        "id": row.id,
        "slug": row.slug,
        "name": row.name,
        "kind": row.kind,
        "status": row.status,
        "published_at": iso(row.published_at),
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
        "updated_by": _person(row.updated_by, emails or {}),
        "evidence": counts.get("evidence", 0),
        "addresses": counts.get("addresses", 0),
    }


def list_for_editor(
    session: Session, *, status: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    """Every profile, newest change first, with how much each holds."""
    query = select(WalletProfile)
    if status is not None:
        query = query.where(WalletProfile.status == status)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(WalletProfile.updated_at.desc(), WalletProfile.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    ids = [row.id for row in rows]
    counts: dict[int, dict[str, int]] = {i: {} for i in ids}
    for name, model in (("evidence", WalletProfileEvidence), ("addresses", WalletProfileAddress)):
        if not ids:
            break
        grouped = session.execute(
            select(model.profile_id, func.count())
            .where(model.profile_id.in_(ids))
            .group_by(model.profile_id)
        ).all()
        for profile_id, number in grouped:
            counts[profile_id][name] = number
    emails = _emails(session, {row.updated_by for row in rows})
    return [editor_item(row, counts[row.id], emails) for row in rows], total


def editor_detail(session: Session, profile: WalletProfile) -> dict[str, Any]:
    """The whole profile as an editor edits it, with what stands in the way of publishing it.

    `content` has the shape of the request that replaces it, so an editor reads it, changes it and
    sends it back."""
    evidence = session.scalars(
        select(WalletProfileEvidence)
        .where(WalletProfileEvidence.profile_id == profile.id)
        .order_by(WalletProfileEvidence.id)
    ).all()
    refs = {row.id: row.ref for row in evidence}
    addresses = session.scalars(
        select(WalletProfileAddress)
        .where(WalletProfileAddress.profile_id == profile.id)
        .order_by(WalletProfileAddress.id)
    ).all()
    emails = _emails(session, {profile.created_by, profile.updated_by})
    status = ProfileStatus(profile.status)
    return {
        **editor_item(profile, {"evidence": len(evidence), "addresses": len(addresses)}, emails),
        "created_by": _person(profile.created_by, emails),
        "content": {
            "name": profile.name,
            "kind": profile.kind,
            "summary": profile.summary,
            "website": profile.website,
            "attribution_note": profile.attribution_note,
            "internal_note": profile.internal_note,
            "evidence": [evidence_view(row) for row in evidence],
            "addresses": [address_view(row, refs) for row in addresses],
        },
        "publish_problems": service.publish_problems(session, profile),
        "allowed_transitions": sorted(state.value for state in TRANSITIONS[status]),
        "editable": status in EDITABLE,
        "words": {
            "statuses": {status.value: words for status, words in STATUS_WORDS.items()},
            "kinds": {kind.value: words for kind, words in KIND_WORDS.items()},
        },
    }
