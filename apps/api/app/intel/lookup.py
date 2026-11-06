"""Looking an address up: what is claimed about it, by whom, how sure, and how current the sources
are.

A lookup reads only what is stored (the sources are brought up to date by their own refresh, see
importer.py), so it makes no network request and is cheap enough to run for every counterparty of
a history. The answer always carries the state of every source it was checked against, because
"nothing found" means "nothing found in these lists as of these dates", never "clean".
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chains.registry import get_chain
from app.database.models import AddressLabel, Entity, LabelSource
from app.database.repositories.rows import chunks
from app.intel.model import (
    STALE_AFTER_REFRESHES,
    AddressIntel,
    Category,
    Freshness,
    LabelMatch,
    SourceKind,
    SourceStatus,
)


class UnknownAddress(ValueError):
    """The address cannot be an address on the chain it was asked about (or the chain is unknown,
    which `chain_unknown` says)."""

    def __init__(self, message: str, *, chain_unknown: bool = False) -> None:
        super().__init__(message)
        self.chain_unknown = chain_unknown


def source_status(row: LabelSource, now: datetime) -> SourceStatus:
    age = None if row.last_checked_at is None else (now - row.last_checked_at).total_seconds()
    if age is None:
        freshness = Freshness.NEVER
    elif age > STALE_AFTER_REFRESHES * row.refresh_seconds:
        freshness = Freshness.STALE
    else:
        freshness = Freshness.FRESH
    return SourceStatus(
        slug=row.slug,
        name=row.name,
        kind=SourceKind(row.kind),
        url=row.url,
        publish_date=row.publish_date,
        last_checked_at=row.last_checked_at,
        refresh_seconds=row.refresh_seconds,
        status=row.status,
        last_error=row.last_error,
        age_seconds=age,
        freshness=freshness,
    )


def _statuses(session: Session, now: datetime) -> dict[int, SourceStatus]:
    rows = session.scalars(select(LabelSource).order_by(LabelSource.id))
    return {row.id: source_status(row, now) for row in rows}


def source_statuses(session: Session, now: datetime | None = None) -> tuple[SourceStatus, ...]:
    return tuple(_statuses(session, now or datetime.now(UTC)).values())


def _normalized(chain: str, address: str) -> tuple[str, str]:
    spec = get_chain(chain)
    if spec is None:
        raise UnknownAddress(f"{chain!r} is not a chain this platform knows", chain_unknown=True)
    check = spec.validator.check(address)
    if not check.valid or check.normalized is None:
        raise UnknownAddress(f"not a valid {spec.slug} address: {check.message}")
    return spec.id, check.normalized


def lookup_many(
    session: Session,
    chain_id: str,
    addresses: Sequence[str],
    *,
    now: datetime | None = None,
) -> dict[str, AddressIntel]:
    """Each address's labels. `addresses` must already be in the chain's storage form (as the
    stored histories hold them); every address gets an answer, with no labels if it has none."""
    now = now or datetime.now(UTC)
    by_id = _statuses(session, now)
    sources = tuple(by_id.values())

    found: dict[str, list[LabelMatch]] = {address: [] for address in addresses}
    for group in chunks(sorted(set(addresses))):
        rows = session.execute(
            select(AddressLabel, Entity)
            .outerjoin(Entity, Entity.id == AddressLabel.entity_id)
            .where(AddressLabel.chain_id == chain_id, AddressLabel.address.in_(group))
        )
        for label, entity in rows:
            found[label.address].append(_match(label, entity, by_id[label.source_id]))

    result = {}
    for address, matches in found.items():
        matches.sort(key=lambda m: (-m.confidence, m.source.slug, m.source_ref))
        result[address] = AddressIntel(
            chain_id=chain_id,
            address=address,
            labels=tuple(m for m in matches if m.active),
            former=tuple(m for m in matches if not m.active),
            sources=sources,
        )
    return result


def lookup(
    session: Session, chain: str, address: str, *, now: datetime | None = None
) -> AddressIntel:
    """What is known about one address (any spelling the chain accepts), on a chain given by slug
    or CAIP-2 id. Raises UnknownAddress for something that cannot be an address there."""
    chain_id, normalized = _normalized(chain, address)
    return lookup_many(session, chain_id, [normalized], now=now)[normalized]


def _match(label: AddressLabel, entity: Entity | None, source: SourceStatus) -> LabelMatch:
    try:
        category = Category(label.category)
    except ValueError:
        category = Category.UNKNOWN
    return LabelMatch(
        chain_id=label.chain_id,
        address=label.address,
        category=category,
        confidence=label.confidence,
        source=source,
        source_ref=label.source_ref,
        entity_name=None if entity is None else entity.name,
        entity_type=None if entity is None else entity.entity_type,
        programs=() if entity is None else tuple(entity.programs or ()),
        listed_as=label.listed_as,
        source_url=label.source_url,
        first_observed=label.first_observed,
        last_verified=label.last_verified,
        active=label.active,
        removed_at=label.removed_at,
    )
