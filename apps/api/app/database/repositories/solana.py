"""Solana transactions: the transaction, the movements it made, and what every balance did.

A transaction's movements are ordinary `transfers` rows (identified by the instruction position
they came from), so the graph and the address queries see them like any other chain's. The balance
changes and the changes no instruction explains belong to the transaction and live beside it. Which
transactions are part of a wallet's history is remembered in `address_transactions`, because a
provider also returns transactions that only changed one of the wallet's token accounts, which the
transaction alone cannot always say. Call the save and remove functions with a write session.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.chains.models import Confirmation, DataQuality, Transfer, TxStatus
from app.chains.solana.models import SolanaTransaction, SolChange, TokenChange, Unattributed
from app.database.models import (
    StoredBalanceChange,
    StoredTransaction,
    StoredTransfer,
    StoredUnattributed,
)
from app.database.repositories.membership import link_address, linked_transactions, transaction_ids
from app.database.repositories.rows import SaveCounts, chunks, fit, save_rows
from app.database.repositories.transfers import (
    TRANSACTIONS,
    blank_transaction_row,
    save_transfers,
    to_transfer,
)
from app.database.upsert import upsert_rows

_POSITION = re.compile(r"^(\d+)(?:\.(\d+))?(?:#(\d+))?$")


def _position(event_key: str) -> tuple[int, int, int, str]:
    """Where a movement sits in its transaction: instruction 3 comes before its inner 3.0, 3.1."""
    match = _POSITION.fullmatch(event_key)
    if match is None:
        return (10**9, 0, 0, event_key)
    instruction, inner, repeat = match.groups()
    return (int(instruction), -1 if inner is None else int(inner), int(repeat or 0), "")


def solana_row(tx: SolanaTransaction) -> dict[str, Any]:
    row = blank_transaction_row(tx.chain_id, tx.signature)
    row.update(
        block_number=tx.slot,
        transaction_index=tx.transaction_index,
        timestamp=tx.timestamp,
        status=tx.status.value,
        fee_raw=tx.fee_raw,
        fee_payer=tx.fee_payer,
        confirmation_status=tx.confirmation_status.value,
        provider=fit(tx.provider, 32),
        data_quality=tx.data_quality.value,
        version=fit(tx.version, 12),
        error=tx.error,
        signers=list(tx.signers),
        programs=list(tx.programs),
        unreadable=tx.unreadable,
        unresolved_owners=tx.unresolved_owners,
    )
    return row


def _change_rows(tx_id: int, tx: SolanaTransaction) -> list[dict[str, Any]]:
    rows = [
        {
            "transaction_id": tx_id,
            "chain_id": tx.chain_id,
            "kind": "sol",
            "position": position,
            "account": change.account,
            "owner": None,
            "mint": None,
            "decimals": None,
            "program_id": None,
            "before_raw": change.before_raw,
            "after_raw": change.after_raw,
        }
        for position, change in enumerate(tx.sol_changes)
    ]
    rows += [
        {
            "transaction_id": tx_id,
            "chain_id": tx.chain_id,
            "kind": "token",
            "position": position,
            "account": change.account,
            "owner": change.owner,
            "mint": change.mint,
            "decimals": change.decimals,
            "program_id": change.program_id,
            "before_raw": change.before_raw,
            "after_raw": change.after_raw,
        }
        for position, change in enumerate(tx.token_changes)
    ]
    return rows


def _unattributed_rows(tx_id: int, tx: SolanaTransaction) -> list[dict[str, Any]]:
    return [
        {
            "transaction_id": tx_id,
            "chain_id": tx.chain_id,
            "position": position,
            "kind": item.kind,
            "account": item.account,
            "owner": item.owner,
            "mint": item.mint,
            "amount_raw": item.amount_raw,
        }
        for position, item in enumerate(tx.unattributed)
    ]


def save_solana_transactions(
    session: Session,
    transactions: Iterable[SolanaTransaction],
    *,
    address: str | None = None,
    replace: bool = False,
) -> SaveCounts:
    """Store transactions, their movements and balance changes, and, given `address`, as part of
    its history. What is already stored is merged, not replaced; `replace` instead deletes the
    stored balance changes first, for a re-read after a decoding fix."""
    items = {tx.signature: tx for tx in transactions}
    if not items:
        return SaveCounts()
    chain_id = next(iter(items.values())).chain_id
    counts = save_rows(session, TRANSACTIONS, [solana_row(tx) for tx in items.values()])

    ids = transaction_ids(session, chain_id, list(items))
    todo = list(items.values())
    if replace:
        _forget_children(session, [ids[tx.signature] for tx in todo])
    save_transfers(session, [transfer for tx in todo for transfer in tx.transfers])
    upsert_rows(
        session,
        StoredBalanceChange,
        [row for tx in todo for row in _change_rows(ids[tx.signature], tx)],
        keys=("transaction_id", "kind", "account"),
        overwrite=["chain_id", "position", "before_raw", "after_raw"],
        keep_known=["owner", "mint", "decimals", "program_id"],
    )
    upsert_rows(
        session,
        StoredUnattributed,
        [row for tx in todo for row in _unattributed_rows(ids[tx.signature], tx)],
        keys=("transaction_id", "position"),
        overwrite=["chain_id", "kind", "account", "amount_raw"],
        keep_known=["owner", "mint"],
    )
    if address is not None:
        link_address(session, chain_id, address, list(ids.values()))
    return counts


def _forget_children(session: Session, ids: Sequence[int]) -> None:
    for group in chunks(list(ids)):
        session.execute(
            delete(StoredBalanceChange).where(StoredBalanceChange.transaction_id.in_(group))
        )
        session.execute(
            delete(StoredUnattributed).where(StoredUnattributed.transaction_id.in_(group))
        )


def load_solana_history(session: Session, chain_id: str, address: str) -> list[SolanaTransaction]:
    """The transactions read as part of the wallet's history (unordered)."""
    return _assemble(session, linked_transactions(session, chain_id, address))


def load_solana_transaction(
    session: Session, chain_id: str, signature: str
) -> SolanaTransaction | None:
    row = session.scalar(
        select(StoredTransaction).where(
            StoredTransaction.chain_id == chain_id,
            StoredTransaction.transaction_hash == signature,
        )
    )
    return None if row is None else _assemble(session, [row])[0]


def _assemble(session: Session, rows: Sequence[StoredTransaction]) -> list[SolanaTransaction]:
    ids = [row.id for row in rows]
    changes: dict[int, list[StoredBalanceChange]] = {tx_id: [] for tx_id in ids}
    unattributed: dict[int, list[StoredUnattributed]] = {tx_id: [] for tx_id in ids}
    for group in chunks(ids):
        for change in session.scalars(
            select(StoredBalanceChange)
            .where(StoredBalanceChange.transaction_id.in_(group))
            .order_by(StoredBalanceChange.transaction_id, StoredBalanceChange.position)
        ):
            changes[change.transaction_id].append(change)
        for item in session.scalars(
            select(StoredUnattributed)
            .where(StoredUnattributed.transaction_id.in_(group))
            .order_by(StoredUnattributed.transaction_id, StoredUnattributed.position)
        ):
            unattributed[item.transaction_id].append(item)

    moves: dict[str, list[Transfer]] = {row.transaction_hash: [] for row in rows}
    by_chain: dict[str, list[str]] = {}
    for row in rows:
        by_chain.setdefault(row.chain_id, []).append(row.transaction_hash)
    for chain_id, hashes in by_chain.items():
        for group in chunks(sorted(hashes)):
            for stored in session.scalars(
                select(StoredTransfer).where(
                    StoredTransfer.chain_id == chain_id,
                    StoredTransfer.transaction_hash.in_(group),
                )
            ):
                moves[stored.transaction_hash].append(to_transfer(stored))

    return [
        SolanaTransaction(
            chain_id=row.chain_id,
            signature=row.transaction_hash,
            slot=row.block_number or 0,
            timestamp=row.timestamp,
            version=row.version or "legacy",
            status=TxStatus(row.status),
            error=row.error,
            fee_raw=row.fee_raw or "0",
            fee_payer=row.fee_payer or "",
            signers=tuple(row.signers or ()),
            programs=tuple(row.programs or ()),
            transfers=tuple(
                sorted(moves[row.transaction_hash], key=lambda t: _position(t.event_key))
            ),
            sol_changes=tuple(
                SolChange(item.account, item.before_raw, item.after_raw)
                for item in changes[row.id]
                if item.kind == "sol"
            ),
            token_changes=tuple(
                TokenChange(
                    item.account,
                    item.owner,
                    item.mint or "",
                    item.decimals or 0,
                    item.program_id,
                    item.before_raw,
                    item.after_raw,
                )
                for item in changes[row.id]
                if item.kind == "token"
            ),
            unattributed=tuple(
                Unattributed(item.kind, item.account, item.owner, item.mint, item.amount_raw)
                for item in unattributed[row.id]
            ),
            unreadable=row.unreadable or 0,
            provider=row.provider,
            data_quality=DataQuality(row.data_quality),
            transaction_index=row.transaction_index,
            confirmation_status=Confirmation(row.confirmation_status),
            unresolved_owners=row.unresolved_owners or 0,
        )
        for row in rows
    ]
