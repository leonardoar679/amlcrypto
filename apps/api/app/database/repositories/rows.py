"""What every chain-data repository shares: saving records by identity, and counting what happened.

A record is saved once however often it is read. Saving again changes nothing unless the new read
knows more (a block time the first one lacked, a deeper confirmation count), and a record that this
platform decoded itself from a receipt or transaction (`data_quality` "decoded") is never replaced
by an indexer's summary of the same record.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.base import Base
from app.database.upsert import upsert_rows

CHUNK = 400  # values in one IN (...); well under every database's limit on bound parameters
DECODED = "decoded"
_BOOKKEEPING = ("id", "created_at", "updated_at")


@dataclass(frozen=True, slots=True)
class SaveCounts:
    new: int = 0
    updated: int = 0  # already stored, and this read knew something more
    unchanged: int = 0

    def __add__(self, other: SaveCounts) -> SaveCounts:
        return SaveCounts(
            self.new + other.new, self.updated + other.updated, self.unchanged + other.unchanged
        )

    @property
    def total(self) -> int:
        return self.new + self.updated + self.unchanged


def chunks(items: Sequence[Any], size: int = CHUNK) -> Iterator[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def fit(value: str | None, length: int) -> str | None:
    """Text from a provider, clamped to its column so that it can never make a write fail."""
    return value if value is None or len(value) <= length else value[:length]


@dataclass(frozen=True, slots=True)
class Table:
    """A table whose rows are keyed by (chain_id, transaction_hash[, more])."""

    model: type[Base]
    keys: tuple[str, ...]

    @property
    def columns(self) -> list[str]:
        return [c.name for c in self.model.__table__.columns if c.name not in _BOOKKEEPING]

    @property
    def values(self) -> list[str]:
        return [name for name in self.columns if name not in self.keys]

    def key_of(self, row: Any) -> tuple[Any, ...]:
        return tuple(row[name] for name in self.keys)


def save_rows(session: Session, table: Table, rows: Sequence[dict[str, Any]]) -> SaveCounts:
    """Save `rows`, which share the same columns, by their natural key."""
    if not rows:
        return SaveCounts()
    # One record per identity in a batch: the last one wins, as it would reading them in order.
    unique = {table.key_of(row): row for row in rows}
    existing = _existing(session, table, list(unique.values()))
    nullable = {c.name for c in table.model.__table__.columns if c.nullable}

    to_write: list[dict[str, Any]] = []
    new = updated = unchanged = 0
    for key, row in unique.items():
        stored = existing.get(key)
        if stored is None:
            new += 1
            to_write.append(row)
            continue
        if stored.get("data_quality") == DECODED and row.get("data_quality") != DECODED:
            unchanged += 1
            continue
        merged = {
            name: (stored[name] if value is None and name in nullable else value)
            for name, value in row.items()
        }
        if merged == {name: stored[name] for name in merged}:
            unchanged += 1
        else:
            updated += 1
            to_write.append(row)

    nonkey = table.values
    upsert_rows(
        session,
        table.model,
        to_write,
        keys=table.keys,
        overwrite=[name for name in nonkey if name not in nullable],
        keep_known=[name for name in nonkey if name in nullable],
    )
    return SaveCounts(new, updated, unchanged)


def _existing(
    session: Session, table: Table, rows: Sequence[dict[str, Any]]
) -> dict[tuple[Any, ...], dict[str, Any]]:
    model = table.model
    columns = [model.__table__.c[name] for name in table.columns]
    hashes_by_chain: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        hashes_by_chain[row["chain_id"]].add(row["transaction_hash"])

    found: dict[tuple[Any, ...], dict[str, Any]] = {}
    for chain_id, hashes in hashes_by_chain.items():
        for group in chunks(sorted(hashes)):
            statement = select(*columns).where(
                model.__table__.c.chain_id == chain_id,
                model.__table__.c.transaction_hash.in_(group),
            )
            for stored in session.execute(statement).mappings():
                found[table.key_of(stored)] = dict(stored)
    return found
