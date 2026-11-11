"""What each kind of reader is shown of an incident.

An editor sees all of it, drafts and internal notes included. (Everybody else is shown a
published incident through a separate, narrower set of functions, so that nothing an editor alone
should see can reach a public answer by being added to a row.)
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.chains.models import iso
from app.chains.registry import get_chain
from app.database.models import (
    Incident,
    IncidentAddress,
    IncidentAmount,
    IncidentEvidence,
    IncidentFlow,
    IncidentUpdate,
    User,
)
from app.incidents import service
from app.incidents.model import (
    EDITABLE,
    ROLE_WORDS,
    STATUS_WORDS,
    TRANSITIONS,
    IncidentStatus,
)


def _date(value) -> str | None:
    return None if value is None else value.isoformat()


def _slug(chain_id: str | None) -> str | None:
    return None if chain_id is None else get_chain(chain_id).slug


def evidence_view(row: IncidentEvidence) -> dict[str, Any]:
    return {
        "ref": row.ref,
        "publisher": row.publisher,
        "title": row.title,
        "url": row.url,
        "kind": row.kind,
        "published_on": _date(row.published_on),
        "retrieved_on": _date(row.retrieved_on),
        "excerpt": row.excerpt,
    }


def amount_view(row: IncidentAmount, refs: dict[int, str]) -> dict[str, Any]:
    return {
        "evidence_ref": refs.get(row.evidence_id),
        "amount_usd_low": row.amount_usd_low,
        "amount_usd_high": row.amount_usd_high,
        "as_of": _date(row.as_of),
        "label": row.label,
    }


def address_view(row: IncidentAddress, refs: dict[int, str]) -> dict[str, Any]:
    return {
        "chain": _slug(row.chain_id),
        "address": row.address,
        "role": row.role,
        "attribution_status": row.attribution_status,
        "confidence": row.confidence,
        "evidence_ref": None if row.evidence_id is None else refs.get(row.evidence_id),
        "note": row.note,
        "last_verified_at": iso(row.last_verified_at),
    }


def flow_view(row: IncidentFlow, refs: dict[int, str]) -> dict[str, Any]:
    return {
        "from_chain": _slug(row.from_chain_id),
        "from_address": row.from_address,
        "to_chain": _slug(row.to_chain_id),
        "to_address": row.to_address,
        "evidence_ref": refs.get(row.evidence_id),
        "tx_hash": row.tx_hash,
        "amount_text": row.amount_text,
        "occurred_on": _date(row.occurred_on),
        "note": row.note,
    }


def update_view(row: IncidentUpdate, refs: dict[int, str]) -> dict[str, Any]:
    return {
        "occurred_at": iso(row.occurred_at),
        "kind": row.kind,
        "body": row.body,
        "evidence_ref": None if row.evidence_id is None else refs.get(row.evidence_id),
        "chain": _slug(row.chain_id),
        "tx_hash": row.tx_hash,
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
    row: Incident, counts: dict[str, int], emails: dict[int, str] | None = None
) -> dict[str, Any]:
    return {
        "id": row.id,
        "slug": row.slug,
        "title": row.title,
        "kind": row.kind,
        "status": row.status,
        "occurred_on": _date(row.occurred_on),
        "published_at": iso(row.published_at),
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
        "updated_by": _person(row.updated_by, emails or {}),
        "evidence": counts.get("evidence", 0),
        "addresses": counts.get("addresses", 0),
        "flows": counts.get("flows", 0),
        "updates": counts.get("updates", 0),
    }


def list_for_editor(
    session: Session, *, status: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    """Every incident, newest change first, with how much each holds."""
    query = select(Incident)
    if status is not None:
        query = query.where(Incident.status == status)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(Incident.updated_at.desc(), Incident.id.desc()).limit(limit).offset(offset)
    ).all()
    ids = [row.id for row in rows]
    counts: dict[int, dict[str, int]] = {i: {} for i in ids}
    for name, model in (
        ("evidence", IncidentEvidence),
        ("addresses", IncidentAddress),
        ("flows", IncidentFlow),
        ("updates", IncidentUpdate),
    ):
        if not ids:
            break
        grouped = session.execute(
            select(model.incident_id, func.count())
            .where(model.incident_id.in_(ids))
            .group_by(model.incident_id)
        ).all()
        for incident_id, number in grouped:
            counts[incident_id][name] = number
    emails = _emails(session, {row.updated_by for row in rows})
    return [editor_item(row, counts[row.id], emails) for row in rows], total


def editor_detail(session: Session, incident: Incident) -> dict[str, Any]:
    """The whole incident as an editor edits it, with what stands in the way of publishing it.

    `content` has the shape of the request that replaces it, so an editor reads it, changes it and
    sends it back."""
    evidence = session.scalars(
        select(IncidentEvidence)
        .where(IncidentEvidence.incident_id == incident.id)
        .order_by(IncidentEvidence.id)
    ).all()
    refs = {row.id: row.ref for row in evidence}
    amounts = session.scalars(
        select(IncidentAmount)
        .where(IncidentAmount.incident_id == incident.id)
        .order_by(IncidentAmount.id)
    ).all()
    addresses = session.scalars(
        select(IncidentAddress)
        .where(IncidentAddress.incident_id == incident.id)
        .order_by(IncidentAddress.id)
    ).all()
    flows = session.scalars(
        select(IncidentFlow)
        .where(IncidentFlow.incident_id == incident.id)
        .order_by(IncidentFlow.position)
    ).all()
    updates = session.scalars(
        select(IncidentUpdate)
        .where(IncidentUpdate.incident_id == incident.id)
        .order_by(IncidentUpdate.occurred_at, IncidentUpdate.id)
    ).all()
    emails = _emails(session, {incident.created_by, incident.updated_by})
    status = IncidentStatus(incident.status)
    return {
        **editor_item(
            incident,
            {
                "evidence": len(evidence),
                "addresses": len(addresses),
                "flows": len(flows),
                "updates": len(updates),
            },
            emails,
        ),
        "created_by": _person(incident.created_by, emails),
        "content": {
            "title": incident.title,
            "summary": incident.summary,
            "kind": incident.kind,
            "occurred_on": _date(incident.occurred_on),
            "attribution_note": incident.attribution_note,
            "internal_note": incident.internal_note,
            "evidence": [evidence_view(row) for row in evidence],
            "amounts": [amount_view(row, refs) for row in amounts],
            "addresses": [address_view(row, refs) for row in addresses],
            "flows": [flow_view(row, refs) for row in flows],
            "updates": [update_view(row, refs) for row in updates],
        },
        "publish_problems": service.publish_problems(session, incident),
        "allowed_transitions": sorted(state.value for state in TRANSITIONS[status]),
        "editable": status in EDITABLE,
        "words": {
            "statuses": {status.value: words for status, words in STATUS_WORDS.items()},
            "roles": {role.value: words for role, words in ROLE_WORDS.items()},
        },
    }
