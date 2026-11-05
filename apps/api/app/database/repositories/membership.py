"""Which stored transactions were read as part of an address's history.

A provider decides which transactions belong to an address, and its reasons are not always in the
transaction: TRON lists a transaction for an address that only took part through an internal call,
Solana for a wallet whose token account was changed. So the platform remembers what each read
found for each address, instead of working it out again from the transaction. Call `link_address`
with a write session.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import AddressTransaction, StoredTransaction
from app.database.repositories.rows import chunks
from app.database.upsert import upsert_rows


def transaction_ids(session: Session, chain_id: str, hashes: Sequence[str]) -> dict[str, int]:
    """The stored id of each of these transactions (those that are stored)."""
    found: dict[str, int] = {}
    for group in chunks(sorted(hashes)):
        rows = session.execute(
            select(StoredTransaction.transaction_hash, StoredTransaction.id).where(
                StoredTransaction.chain_id == chain_id,
                StoredTransaction.transaction_hash.in_(group),
            )
        )
        found.update({row.transaction_hash: row.id for row in rows})
    return found


def link_address(
    session: Session, chain_id: str, address: str, transaction_ids_: Sequence[int]
) -> None:
    """Record that these transactions were found when reading the address."""
    upsert_rows(
        session,
        AddressTransaction,
        [
            {"chain_id": chain_id, "address": address, "transaction_id": tx_id}
            for tx_id in transaction_ids_
        ],
        keys=("chain_id", "address", "transaction_id"),
    )


def linked_transactions(session: Session, chain_id: str, address: str) -> list[StoredTransaction]:
    """The transactions read as part of the address's history."""
    return list(
        session.scalars(
            select(StoredTransaction)
            .join(AddressTransaction, AddressTransaction.transaction_id == StoredTransaction.id)
            .where(AddressTransaction.chain_id == chain_id, AddressTransaction.address == address)
        )
    )
