"""What anybody may read of a wallet profile: only a published one, and only what is meant to be
public.

Every answer here is written out field by field, never taken from a row, so that a column added to a
table later (an editor's note, the person who wrote it) cannot reach the public by being there; a
test holds the exact set of fields. Nothing here reads a table an organization owns, and nothing is
derived from the chain data held for customers: the page says what the documents say.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import exists, func, select, tuple_
from sqlalchemy.orm import Session

from app.chains.models import iso
from app.chains.registry import get_chain
from app.database.models import (
    Incident,
    IncidentAddress,
    WalletProfile,
    WalletProfileAddress,
    WalletProfileEvidence,
)
from app.incidents.model import ROLE_WORDS, AddressRole, IncidentStatus
from app.wallets.model import (
    KIND_WORDS,
    PUBLIC_NOTICE,
    STATUS_WORDS,
    AttributionStatus,
    ProfileStatus,
    WalletKind,
)

EXCERPT_LENGTH = 280
# How many incidents a profile page lists, and how many of a profile's addresses one incident shows.
INCIDENTS_SHOWN = 20
ADDRESSES_PER_INCIDENT = 10


def _date(value) -> str | None:
    return None if value is None else value.isoformat()


def _slug(chain_id: str) -> str:
    return get_chain(chain_id).slug


def _excerpt(text: str) -> str:
    if len(text) <= EXCERPT_LENGTH:
        return text
    cut = text[:EXCERPT_LENGTH].rsplit(" ", 1)[0].rstrip(".,;:")
    return cut + "..."


def _published():
    return WalletProfile.status == ProfileStatus.PUBLISHED.value


def item(row: WalletProfile, extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": row.slug,
        "name": row.name,
        "kind": row.kind,
        "kind_words": KIND_WORDS[WalletKind(row.kind)],
        "website": row.website,
        "published_at": iso(row.published_at),
        "updated_at": iso(row.updated_at),
        "excerpt": _excerpt(row.summary),
        "chains": extra.get("chains", []),
        "sources": extra.get("sources", 0),
        "addresses": extra.get("addresses", 0),
    }


def list_published(
    session: Session,
    *,
    kind: str | None,
    chain_id: str | None,
    name: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """Published profiles, by name, with what a list needs to say about each."""
    query = select(WalletProfile).where(_published())
    if kind is not None:
        query = query.where(WalletProfile.kind == kind)
    if chain_id is not None:
        query = query.where(
            exists().where(
                WalletProfileAddress.profile_id == WalletProfile.id,
                WalletProfileAddress.chain_id == chain_id,
            )
        )
    if name:
        # `%` and `_` in what a visitor typed are letters, not wildcards.
        query = query.where(func.lower(WalletProfile.name).contains(name.lower(), autoescape=True))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(func.lower(WalletProfile.name), WalletProfile.id).limit(limit).offset(offset)
    ).all()
    ids = [row.id for row in rows]
    extras: dict[int, dict[str, Any]] = {i: {"chains": []} for i in ids}
    if ids:
        for profile_id, chain in session.execute(
            select(WalletProfileAddress.profile_id, WalletProfileAddress.chain_id)
            .where(WalletProfileAddress.profile_id.in_(ids))
            .distinct()
        ):
            extras[profile_id]["chains"].append(_slug(chain))
        for profile_id, number in session.execute(
            select(WalletProfileAddress.profile_id, func.count())
            .where(WalletProfileAddress.profile_id.in_(ids))
            .group_by(WalletProfileAddress.profile_id)
        ):
            extras[profile_id]["addresses"] = number
        for profile_id, number in session.execute(
            select(
                WalletProfileEvidence.profile_id,
                func.count(func.distinct(WalletProfileEvidence.publisher)),
            )
            .where(WalletProfileEvidence.profile_id.in_(ids))
            .group_by(WalletProfileEvidence.profile_id)
        ):
            extras[profile_id]["sources"] = number
    for extra in extras.values():
        extra["chains"].sort()
    return [item(row, extras[row.id]) for row in rows], total


def get_published(session: Session, slug: str) -> WalletProfile | None:
    """The profile with this address, if it is published (a draft, one in review and an archived
    one are not found, in the same way as one that does not exist)."""
    return session.scalar(select(WalletProfile).where(WalletProfile.slug == slug, _published()))


def incident_mentions(
    session: Session, pairs: Iterable[tuple[str, str]], *, limit: int = INCIDENTS_SHOWN
) -> list[dict[str, Any]]:
    """The published incidents that name any of these addresses, one entry for each incident, with
    which of the addresses it names and the part each plays there."""
    wanted = list(pairs)
    if not wanted:
        return []
    rows = session.execute(
        select(Incident, IncidentAddress)
        .join(IncidentAddress, IncidentAddress.incident_id == Incident.id)
        .where(
            Incident.status == IncidentStatus.PUBLISHED.value,
            tuple_(IncidentAddress.chain_id, IncidentAddress.address).in_(wanted),
        )
        .order_by(Incident.occurred_on.desc(), Incident.id.desc(), IncidentAddress.id)
    ).all()
    found: dict[int, dict[str, Any]] = {}
    for incident, named in rows:
        entry = found.get(incident.id)
        if entry is None:
            if len(found) >= limit:
                continue
            entry = found[incident.id] = {
                "slug": incident.slug,
                "title": incident.title,
                "kind": incident.kind,
                "occurred_on": _date(incident.occurred_on),
                "addresses": [],
            }
        if len(entry["addresses"]) < ADDRESSES_PER_INCIDENT:
            entry["addresses"].append(
                {
                    "chain": _slug(named.chain_id),
                    "address": named.address,
                    "role_words": ROLE_WORDS[AddressRole(named.role)],
                    "status_words": STATUS_WORDS[AttributionStatus(named.attribution_status)],
                }
            )
    return list(found.values())


def detail(session: Session, profile: WalletProfile) -> dict[str, Any]:
    """A published profile as anybody may read it: who it is, what it rests on, the addresses with
    how firmly each is attributed, and the published incidents that name any of them."""
    evidence = session.scalars(
        select(WalletProfileEvidence)
        .where(WalletProfileEvidence.profile_id == profile.id)
        .order_by(WalletProfileEvidence.id)
    ).all()
    by_id = {row.id: row for row in evidence}
    addresses = session.scalars(
        select(WalletProfileAddress)
        .where(WalletProfileAddress.profile_id == profile.id)
        .order_by(WalletProfileAddress.id)
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
        "slug": profile.slug,
        "name": profile.name,
        "kind": profile.kind,
        "kind_words": KIND_WORDS[WalletKind(profile.kind)],
        "summary": profile.summary,
        "website": profile.website,
        "attribution_note": profile.attribution_note,
        "published_at": iso(profile.published_at),
        "updated_at": iso(profile.updated_at),
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
                "label": row.label,
                "attribution_status": row.attribution_status,
                "status_words": STATUS_WORDS[AttributionStatus(row.attribution_status)],
                "confidence": row.confidence,
                "source": source(row.evidence_id),
                "note": row.note,
                "last_verified_at": iso(row.last_verified_at),
            }
            for row in addresses
        ],
        "incidents": incident_mentions(session, [(row.chain_id, row.address) for row in addresses]),
        "notice": PUBLIC_NOTICE,
    }


def naming(session: Session, chain_id: str, address: str, *, limit: int) -> list[dict[str, Any]]:
    """The published profiles that name an address, with what the address is in each."""
    rows = session.execute(
        select(WalletProfile, WalletProfileAddress)
        .join(WalletProfileAddress, WalletProfileAddress.profile_id == WalletProfile.id)
        .where(
            _published(),
            WalletProfileAddress.chain_id == chain_id,
            WalletProfileAddress.address == address,
        )
        .order_by(WalletProfile.id, WalletProfileAddress.id)
        .limit(limit)
    ).all()
    return [
        {
            "slug": profile.slug,
            "name": profile.name,
            "kind": profile.kind,
            "kind_words": KIND_WORDS[WalletKind(profile.kind)],
            "label": named.label,
            "attribution_status": named.attribution_status,
            "status_words": STATUS_WORDS[AttributionStatus(named.attribution_status)],
        }
        for profile, named in rows
    ]


def known_as(
    session: Session, pairs: Iterable[tuple[str, str]]
) -> dict[tuple[str, str], dict[str, Any]]:
    """For addresses (chain id, address), the first published profile that names each: what a page
    of something else (an incident) says to link them. An address no profile names is not a key."""
    wanted = list(pairs)
    if not wanted:
        return {}
    rows = session.execute(
        select(WalletProfile, WalletProfileAddress.chain_id, WalletProfileAddress.address)
        .join(WalletProfileAddress, WalletProfileAddress.profile_id == WalletProfile.id)
        .where(
            _published(),
            tuple_(WalletProfileAddress.chain_id, WalletProfileAddress.address).in_(wanted),
        )
        .order_by(WalletProfile.id)
    ).all()
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for profile, chain_id, address in rows:
        found.setdefault(
            (chain_id, address),
            {
                "slug": profile.slug,
                "name": profile.name,
                "kind": profile.kind,
                "kind_words": KIND_WORDS[WalletKind(profile.kind)],
            },
        )
    return found
