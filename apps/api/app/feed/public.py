"""What anybody may read of the news feed and of the hot addresses.

Every answer here is written out field by field, never taken from a row, so that a column added to a
table later cannot reach the public by being there; a test holds the exact set of fields. Everything
is read from published incidents and published wallet profiles: a draft, one in review and an
archived one have no item and no rank. Nothing here reads a table an organization owns or the stored
chain data, and nothing counts who looked at what.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.chains.models import iso
from app.chains.registry import get_chain
from app.database.models import Incident, IncidentAddress, IncidentUpdate, WalletProfile
from app.feed.model import (
    HALVINGS,
    INCIDENT_LABEL,
    PROFILE_LABEL,
    ROLE_WEIGHTS,
    SCANNED_INCIDENTS,
    SHOWN_INCIDENTS,
    STATUS_WEIGHTS,
    UPDATE_LABELS,
    FeedKind,
)
from app.incidents.model import (
    ROLE_WORDS,
    STATUS_WORDS,
    AddressRole,
    AttributionStatus,
    IncidentStatus,
    UpdateKind,
)
from app.incidents.public import _excerpt
from app.wallets.model import ProfileStatus
from app.wallets.public import known_as

# At the same moment an incident comes before a development of one, which comes before a profile.
_ORDER = {FeedKind.INCIDENT: 3, FeedKind.UPDATE: 2, FeedKind.PROFILE: 1}


# --- the news feed ------------------------------------------------------------------------------


def _incident_item(row: Incident) -> dict[str, Any]:
    return {
        "kind": FeedKind.INCIDENT.value,
        "at": iso(row.published_at),
        "key": f"incident:{row.slug}",
        "label": INCIDENT_LABEL,
        "title": row.title,
        "summary": _excerpt(row.summary),
        "link": {"type": "incident", "slug": row.slug},
        "update_kind": None,
        "chain": None,
        "tx_hash": None,
    }


def _update_item(update: IncidentUpdate, incident: Incident) -> dict[str, Any]:
    # The key says which entry this is without depending on a row number (which changes when an
    # incident is taken back to review and its content is written again): when, what kind, and a
    # digest of what it says.
    digest = hashlib.sha256(update.body.encode("utf-8")).hexdigest()[:8]
    moment = int(update.occurred_at.timestamp())
    return {
        "kind": FeedKind.UPDATE.value,
        "at": iso(update.occurred_at),
        "key": f"update:{incident.slug}:{moment}:{update.kind}:{digest}",
        "label": UPDATE_LABELS[UpdateKind(update.kind)],
        "title": incident.title,
        "summary": _excerpt(update.body),
        "link": {"type": "incident", "slug": incident.slug},
        "update_kind": update.kind,
        "chain": None if update.chain_id is None else get_chain(update.chain_id).slug,
        "tx_hash": update.tx_hash,
    }


def _profile_item(row: WalletProfile) -> dict[str, Any]:
    return {
        "kind": FeedKind.PROFILE.value,
        "at": iso(row.published_at),
        "key": f"profile:{row.slug}",
        "label": PROFILE_LABEL,
        "title": row.name,
        "summary": _excerpt(row.summary),
        "link": {"type": "wallet", "slug": row.slug},
        "update_kind": None,
        "chain": None,
        "tx_hash": None,
    }


def events(
    session: Session, *, kind: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    """The news items, the newest first, and how many there are.

    An item is an incident's first publication, a timeline entry of a published incident that is
    dated at or after that publication (what happened next, a correction included), or a wallet
    profile's first publication. An entry dated before the incident was first published is part of
    its first story, and an incident that is taken back to review and published again is not
    announced a second time (its first publication is the one that is kept).
    """
    wanted = offset + limit
    kinds = list(FeedKind) if kind is None else [FeedKind(kind)]
    found: list[tuple[datetime, int, int, dict[str, Any]]] = []
    total = 0

    if FeedKind.INCIDENT in kinds:
        shown = and_(
            Incident.status == IncidentStatus.PUBLISHED.value, Incident.published_at.is_not(None)
        )
        total += session.scalar(select(func.count()).select_from(Incident).where(shown)) or 0
        for row in session.scalars(
            select(Incident)
            .where(shown)
            .order_by(Incident.published_at.desc(), Incident.id.desc())
            .limit(wanted)
        ):
            found.append((row.published_at, _ORDER[FeedKind.INCIDENT], row.id, _incident_item(row)))

    if FeedKind.UPDATE in kinds:
        later = and_(
            Incident.status == IncidentStatus.PUBLISHED.value,
            Incident.published_at.is_not(None),
            IncidentUpdate.occurred_at >= Incident.published_at,
        )
        total += (
            session.scalar(
                select(func.count())
                .select_from(IncidentUpdate)
                .join(Incident, Incident.id == IncidentUpdate.incident_id)
                .where(later)
            )
            or 0
        )
        for update, incident in session.execute(
            select(IncidentUpdate, Incident)
            .join(Incident, Incident.id == IncidentUpdate.incident_id)
            .where(later)
            .order_by(IncidentUpdate.occurred_at.desc(), IncidentUpdate.id.desc())
            .limit(wanted)
        ):
            found.append(
                (
                    update.occurred_at,
                    _ORDER[FeedKind.UPDATE],
                    update.id,
                    _update_item(update, incident),
                )
            )

    if FeedKind.PROFILE in kinds:
        shown = and_(
            WalletProfile.status == ProfileStatus.PUBLISHED.value,
            WalletProfile.published_at.is_not(None),
        )
        total += session.scalar(select(func.count()).select_from(WalletProfile).where(shown)) or 0
        for profile in session.scalars(
            select(WalletProfile)
            .where(shown)
            .order_by(WalletProfile.published_at.desc(), WalletProfile.id.desc())
            .limit(wanted)
        ):
            found.append(
                (profile.published_at, _ORDER[FeedKind.PROFILE], profile.id, _profile_item(profile))
            )

    found.sort(key=lambda entry: entry[:3], reverse=True)
    return [entry[3] for entry in found[offset : offset + limit]], total


# --- the hot addresses --------------------------------------------------------------------------


def hot(
    session: Session,
    *,
    window: int,
    chain_id: str | None,
    limit: int,
    offset: int,
    today: date | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """The addresses that sources report most firmly and most recently, and how many there are.

    Only an address of a published incident, in the role of receiving what was taken or passing it
    on, at a status somebody stands behind with its document, is ranked. An incident counts for as
    long as its last event (the later of the day it happened and its latest timeline entry) is
    inside the window, and fades as that event gets older, so that publishing an old incident today
    does not make its addresses hot. An address's standing is the sum, over the incidents that
    name it, of what its role and status are worth, faded by age. No score is shown: the order is
    the answer.
    """
    today = today or datetime.now(UTC).date()
    cutoff = today - timedelta(days=window)
    cutoff_at = datetime.combine(cutoff, time.min, tzinfo=UTC)

    latest_update = (
        select(
            IncidentUpdate.incident_id.label("incident_id"),
            func.max(IncidentUpdate.occurred_at).label("at"),
        )
        .group_by(IncidentUpdate.incident_id)
        .subquery()
    )
    rows = session.execute(
        select(Incident, latest_update.c.at)
        .outerjoin(latest_update, latest_update.c.incident_id == Incident.id)
        .where(
            Incident.status == IncidentStatus.PUBLISHED.value,
            or_(Incident.occurred_on >= cutoff, latest_update.c.at >= cutoff_at),
        )
        .order_by(Incident.published_at.desc(), Incident.id.desc())
        .limit(SCANNED_INCIDENTS)
    ).all()

    last_event: dict[int, date] = {}
    incidents: dict[int, Incident] = {}
    for incident, update_at in rows:
        days = [
            day
            for day in (incident.occurred_on, None if update_at is None else update_at.date())
            if day is not None
        ]
        if days and max(days) >= cutoff:
            last_event[incident.id] = max(days)
            incidents[incident.id] = incident
    if not incidents:
        return [], 0

    query = select(IncidentAddress).where(
        IncidentAddress.incident_id.in_(list(incidents)),
        IncidentAddress.role.in_([role.value for role in ROLE_WEIGHTS]),
        IncidentAddress.attribution_status.in_([status.value for status in STATUS_WEIGHTS]),
        IncidentAddress.evidence_id.is_not(None),
    )
    if chain_id is not None:
        query = query.where(IncidentAddress.chain_id == chain_id)

    # for each address, in each incident that names it, the row that is worth the most there
    best: dict[tuple[str, str], dict[int, tuple[float, IncidentAddress]]] = defaultdict(dict)
    for row in session.scalars(query):
        weight = (
            ROLE_WEIGHTS[AddressRole(row.role)]
            * STATUS_WEIGHTS[AttributionStatus(row.attribution_status)]
        )
        held = best[(row.chain_id, row.address)].get(row.incident_id)
        if held is None or weight > held[0]:
            best[(row.chain_id, row.address)][row.incident_id] = (weight, row)

    half_life = window / HALVINGS
    ranked = []
    for key, per_incident in best.items():
        score = sum(
            weight * 0.5 ** (max((today - last_event[number]).days, 0) / half_life)
            for number, (weight, _) in per_incident.items()
        )
        latest = max(last_event[number] for number in per_incident)
        # rounded so that two sums of the same terms in another order are not told apart
        ranked.append((round(score, 9), latest, key, per_incident))
    ranked.sort(key=lambda entry: (-entry[0], -entry[1].toordinal(), entry[2]))

    page = ranked[offset : offset + limit]
    profiles = known_as(session, [key for _, _, key, _ in page])
    items = []
    for position, (_, latest, key, per_incident) in enumerate(page, offset + 1):
        chain, address = key
        named = sorted(
            per_incident.items(), key=lambda pair: (last_event[pair[0]], pair[0]), reverse=True
        )
        strongest = max(
            (AttributionStatus(row.attribution_status) for _, (_, row) in named),
            key=lambda status: STATUS_WEIGHTS[status],
        )
        items.append(
            {
                "rank": position,
                "chain": get_chain(chain).slug,
                "address": address,
                "reported_in": len(per_incident),
                "latest_event_on": latest.isoformat(),
                "strongest": {"status": strongest.value, "status_words": STATUS_WORDS[strongest]},
                "incidents": [
                    {
                        "slug": incidents[number].slug,
                        "title": incidents[number].title,
                        "kind": incidents[number].kind,
                        "occurred_on": _date(incidents[number].occurred_on),
                        "role_words": ROLE_WORDS[AddressRole(row.role)],
                        "status_words": STATUS_WORDS[AttributionStatus(row.attribution_status)],
                    }
                    for number, (_, row) in named[:SHOWN_INCIDENTS]
                ],
                "known_as": profiles.get(key),
            }
        )
    return items, len(ranked)


def _date(value: date | None) -> str | None:
    return None if value is None else value.isoformat()
