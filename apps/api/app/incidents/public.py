"""What anybody may read of an incident: only a published one, and only what is meant to be public.

Every answer here is written out field by field, never taken from a row, so that a column added
to a table later (an editor's note, the person who wrote it) cannot reach the public by being
there; a test holds the exact set of fields. Nothing here reads a table an organization owns.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import exists, func, select
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
)
from app.incidents.model import (
    PUBLIC_NOTICE,
    ROLE_WORDS,
    STATUS_WORDS,
    AddressRole,
    AttributionStatus,
    IncidentStatus,
)

EXCERPT_LENGTH = 280


def _date(value) -> str | None:
    return None if value is None else value.isoformat()


def _slug(chain_id: str | None) -> str | None:
    return None if chain_id is None else get_chain(chain_id).slug


def _excerpt(text: str) -> str:
    if len(text) <= EXCERPT_LENGTH:
        return text
    cut = text[:EXCERPT_LENGTH].rsplit(" ", 1)[0].rstrip(".,;:")
    return cut + "..."


def _published():
    return Incident.status == IncidentStatus.PUBLISHED.value


def item(row: Incident, extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": row.slug,
        "title": row.title,
        "kind": row.kind,
        "occurred_on": _date(row.occurred_on),
        "published_at": iso(row.published_at),
        "updated_at": iso(row.updated_at),
        "excerpt": _excerpt(row.summary),
        "reported": extra.get("reported"),
        "chains": extra.get("chains", []),
        "sources": extra.get("sources", 0),
        "addresses": extra.get("addresses", 0),
    }


def list_published(
    session: Session, *, kind: str | None, chain_id: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    """Published incidents, the most recent first, with what a list needs to say about each."""
    query = select(Incident).where(_published())
    if kind is not None:
        query = query.where(Incident.kind == kind)
    if chain_id is not None:
        query = query.where(
            exists().where(
                IncidentAddress.incident_id == Incident.id, IncidentAddress.chain_id == chain_id
            )
        )
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(
            Incident.occurred_on.desc(), Incident.published_at.desc(), Incident.id.desc()
        )
        .limit(limit)
        .offset(offset)
    ).all()
    ids = [row.id for row in rows]
    extras: dict[int, dict[str, Any]] = {i: {"chains": []} for i in ids}
    if ids:
        for incident_id, chain in session.execute(
            select(IncidentAddress.incident_id, IncidentAddress.chain_id)
            .where(IncidentAddress.incident_id.in_(ids))
            .distinct()
        ):
            extras[incident_id]["chains"].append(get_chain(chain).slug)
        for incident_id, number in session.execute(
            select(IncidentAddress.incident_id, func.count())
            .where(IncidentAddress.incident_id.in_(ids))
            .group_by(IncidentAddress.incident_id)
        ):
            extras[incident_id]["addresses"] = number
        for incident_id, number in session.execute(
            select(
                IncidentEvidence.incident_id, func.count(func.distinct(IncidentEvidence.publisher))
            )
            .where(IncidentEvidence.incident_id.in_(ids))
            .group_by(IncidentEvidence.incident_id)
        ):
            extras[incident_id]["sources"] = number
        for incident_id, low, high in session.execute(
            select(
                IncidentAmount.incident_id,
                func.min(IncidentAmount.amount_usd_low),
                func.max(
                    func.coalesce(IncidentAmount.amount_usd_high, IncidentAmount.amount_usd_low)
                ),
            )
            .where(IncidentAmount.incident_id.in_(ids))
            .group_by(IncidentAmount.incident_id)
        ):
            extras[incident_id]["reported"] = {"low": low, "high": high}
    for extra in extras.values():
        extra["chains"].sort()
    return [item(row, extras[row.id]) for row in rows], total


def get_published(session: Session, slug: str) -> Incident | None:
    """The incident with this address, if it is published (a draft, one in review and an archived
    one are not found, in the same way as one that does not exist)."""
    return session.scalar(select(Incident).where(Incident.slug == slug, _published()))


def detail(session: Session, incident: Incident) -> dict[str, Any]:
    """A published incident as anybody may read it: the story, what it rests on, the amounts as each
    source reports them, the addresses with how firmly each is attributed, the movements of funds
    between them, and the timeline."""
    evidence = session.scalars(
        select(IncidentEvidence)
        .where(IncidentEvidence.incident_id == incident.id)
        .order_by(IncidentEvidence.id)
    ).all()
    by_id = {row.id: row for row in evidence}
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

    def source(evidence_id: int | None) -> dict[str, Any] | None:
        row = by_id.get(evidence_id) if evidence_id is not None else None
        if row is None:
            return None
        return {
            "ref": row.ref,
            "publisher": row.publisher,
            "title": row.title,
            "url": row.url,
            "kind": row.kind,
            "published_on": _date(row.published_on),
        }

    return {
        "slug": incident.slug,
        "title": incident.title,
        "kind": incident.kind,
        "summary": incident.summary,
        "attribution_note": incident.attribution_note,
        "occurred_on": _date(incident.occurred_on),
        "published_at": iso(incident.published_at),
        "updated_at": iso(incident.updated_at),
        "reported_amounts": [
            {
                "label": row.label,
                "amount_usd_low": row.amount_usd_low,
                "amount_usd_high": row.amount_usd_high,
                "as_of": _date(row.as_of),
                "source": source(row.evidence_id),
            }
            for row in amounts
        ],
        "sources": [
            {
                **(source(row.id) or {}),
                "retrieved_on": _date(row.retrieved_on),
                "excerpt": row.excerpt,
            }
            for row in evidence
        ],
        "addresses": [
            {
                "chain": _slug(row.chain_id),
                "address": row.address,
                "role": row.role,
                "role_words": ROLE_WORDS[AddressRole(row.role)],
                "attribution_status": row.attribution_status,
                "status_words": STATUS_WORDS[AttributionStatus(row.attribution_status)],
                "confidence": row.confidence,
                "source": source(row.evidence_id),
                "note": row.note,
                "last_verified_at": iso(row.last_verified_at),
            }
            for row in addresses
        ],
        # What the sources report moved, between addresses named above (never derived from stored
        # chain data): the page draws its funds trail from these.
        "flows": [
            {
                "from": {"chain": _slug(row.from_chain_id), "address": row.from_address},
                "to": {"chain": _slug(row.to_chain_id), "address": row.to_address},
                "amount_text": row.amount_text,
                "occurred_on": _date(row.occurred_on),
                "tx_hash": row.tx_hash,
                "note": row.note,
                "source": source(row.evidence_id),
            }
            for row in flows
        ],
        "timeline": [
            {
                "occurred_at": iso(row.occurred_at),
                "kind": row.kind,
                "body": row.body,
                "source": source(row.evidence_id),
                "chain": _slug(row.chain_id),
                "tx_hash": row.tx_hash,
            }
            for row in updates
        ],
        "notice": PUBLIC_NOTICE,
    }


def naming(session: Session, chain_id: str, address: str, *, limit: int) -> list[dict[str, Any]]:
    """The published incidents that name an address, with the part it plays in each."""
    rows = session.execute(
        select(Incident, IncidentAddress)
        .join(IncidentAddress, IncidentAddress.incident_id == Incident.id)
        .where(
            _published(), IncidentAddress.chain_id == chain_id, IncidentAddress.address == address
        )
        .order_by(Incident.occurred_on.desc(), Incident.id.desc(), IncidentAddress.id)
        .limit(limit)
    ).all()
    return [
        {
            "slug": incident.slug,
            "title": incident.title,
            "kind": incident.kind,
            "occurred_on": _date(incident.occurred_on),
            "role": named.role,
            "role_words": ROLE_WORDS[AddressRole(named.role)],
            "attribution_status": named.attribution_status,
            "status_words": STATUS_WORDS[AttributionStatus(named.attribution_status)],
        }
        for incident, named in rows
    ]
