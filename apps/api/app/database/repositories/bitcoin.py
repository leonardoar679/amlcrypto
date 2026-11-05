"""Bitcoin transactions: the transaction, its inputs and outputs, and whose history it is part of.

Bitcoin is read as whole transactions, and which of them belong to an address is a fact about the
read (a provider matched the address in an input or an output), so it is remembered in
`address_transactions` instead of being worked out again from the data. Call the save and remove
functions with a write session.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.chains.bitcoin.models import BitcoinTransaction, SpendState, TxInput, TxOutput
from app.chains.models import Confirmation, DataQuality
from app.database.models import (
    AddressTransaction,
    StoredInput,
    StoredOutput,
    StoredTransaction,
)
from app.database.repositories.membership import link_address, linked_transactions, transaction_ids
from app.database.repositories.rows import SaveCounts, chunks, fit, save_rows
from app.database.repositories.transfers import TRANSACTIONS, blank_transaction_row
from app.database.upsert import upsert_rows

# What is stored of an output's spend state: only what stays true. "Unspent" is true as of a
# moment, so it is not kept; NULL means nothing is known.
_KEPT_STATES = {SpendState.SPENT, SpendState.UNSPENDABLE}


def bitcoin_row(tx: BitcoinTransaction) -> dict[str, Any]:
    row = blank_transaction_row(tx.chain_id, tx.txid)
    row.update(
        block_number=tx.block_height,
        block_hash=tx.block_hash,
        timestamp=tx.timestamp,
        fee_raw=tx.fee_raw,
        size=tx.size,
        weight=tx.weight,
        version=str(tx.version),
        locktime=tx.locktime,
        confirmation_status=tx.confirmation_status.value,
        confirmations=tx.confirmations,
        provider=fit(tx.provider, 32),
        data_quality=tx.data_quality.value,
    )
    return row


def _input_rows(tx_id: int, tx: BitcoinTransaction) -> list[dict[str, Any]]:
    return [
        {
            "transaction_id": tx_id,
            "chain_id": tx.chain_id,
            "input_index": item.index,
            "previous_txid": item.previous_txid,
            "previous_output_index": item.previous_output_index,
            "source_address": item.source_address,
            "value_raw": item.value_raw,
            "script_type": fit(item.script_type, 32),
            "is_coinbase": item.is_coinbase,
        }
        for item in tx.inputs
    ]


def _output_rows(tx_id: int, tx: BitcoinTransaction) -> list[dict[str, Any]]:
    return [
        {
            "transaction_id": tx_id,
            "chain_id": tx.chain_id,
            "output_index": item.index,
            "destination_address": item.destination_address,
            "value_raw": item.value_raw,
            "script_type": fit(item.script_type, 32) or "",
            "spend_state": item.spend_state.value if item.spend_state in _KEPT_STATES else None,
            "spent_by_txid": item.spent_by_txid,
            "spent_by_input_index": item.spent_by_input_index,
        }
        for item in tx.outputs
    ]


def save_bitcoin_transactions(
    session: Session,
    transactions: Iterable[BitcoinTransaction],
    *,
    address: str | None = None,
    replace: bool = False,
) -> SaveCounts:
    """Store transactions with their inputs and outputs, and, given `address`, as part of its
    history. What is already stored is merged, not replaced: a spend an earlier read proved stays,
    and a read that knows one more adds it. `replace` instead deletes the stored inputs and
    outputs first, for a re-read after a decoding fix."""
    items = {tx.txid: tx for tx in transactions}
    if not items:
        return SaveCounts()
    chain_id = next(iter(items.values())).chain_id
    counts = save_rows(session, TRANSACTIONS, [bitcoin_row(tx) for tx in items.values()])

    ids = transaction_ids(session, chain_id, list(items))
    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    for txid, tx in items.items():
        inputs += _input_rows(ids[txid], tx)
        outputs += _output_rows(ids[txid], tx)
    if replace:
        _forget_children(session, [ids[txid] for txid in items])
    upsert_rows(
        session,
        StoredInput,
        inputs,
        keys=("transaction_id", "input_index"),
        keep_known=[
            "previous_txid",
            "previous_output_index",
            "source_address",
            "value_raw",
            "script_type",
        ],
        overwrite=["chain_id", "is_coinbase"],
    )
    upsert_rows(
        session,
        StoredOutput,
        outputs,
        keys=("transaction_id", "output_index"),
        keep_known=["destination_address", "spend_state", "spent_by_txid", "spent_by_input_index"],
        overwrite=["chain_id", "value_raw", "script_type"],
    )
    if address is not None:
        link_address(session, chain_id, address, list(ids.values()))
    return counts


def _forget_children(session: Session, ids: Sequence[int]) -> None:
    for group in chunks(list(ids)):
        session.execute(delete(StoredInput).where(StoredInput.transaction_id.in_(group)))
        session.execute(delete(StoredOutput).where(StoredOutput.transaction_id.in_(group)))


def load_bitcoin_history(session: Session, chain_id: str, address: str) -> list[BitcoinTransaction]:
    """The transactions read as part of the address's history (unordered)."""
    return _assemble(session, linked_transactions(session, chain_id, address))


def load_bitcoin_transaction(
    session: Session, chain_id: str, txid: str
) -> BitcoinTransaction | None:
    row = session.scalar(
        select(StoredTransaction).where(
            StoredTransaction.chain_id == chain_id, StoredTransaction.transaction_hash == txid
        )
    )
    return None if row is None else _assemble(session, [row])[0]


def _assemble(session: Session, rows: Sequence[StoredTransaction]) -> list[BitcoinTransaction]:
    ids = [row.id for row in rows]
    inputs: dict[int, list[StoredInput]] = {tx_id: [] for tx_id in ids}
    outputs: dict[int, list[StoredOutput]] = {tx_id: [] for tx_id in ids}
    for group in chunks(ids):
        for item in session.scalars(
            select(StoredInput)
            .where(StoredInput.transaction_id.in_(group))
            .order_by(StoredInput.transaction_id, StoredInput.input_index)
        ):
            inputs[item.transaction_id].append(item)
        for output in session.scalars(
            select(StoredOutput)
            .where(StoredOutput.transaction_id.in_(group))
            .order_by(StoredOutput.transaction_id, StoredOutput.output_index)
        ):
            outputs[output.transaction_id].append(output)
    return [
        BitcoinTransaction(
            chain_id=row.chain_id,
            txid=row.transaction_hash,
            block_height=row.block_number,
            block_hash=row.block_hash,
            timestamp=row.timestamp,
            inputs=tuple(_to_input(item) for item in inputs[row.id]),
            outputs=tuple(_to_output(item) for item in outputs[row.id]),
            fee_raw=row.fee_raw,
            size=row.size or 0,
            weight=row.weight or 0,
            version=int(row.version or 0),
            locktime=row.locktime or 0,
            provider=row.provider,
            data_quality=DataQuality(row.data_quality),
            confirmation_status=Confirmation(row.confirmation_status),
            confirmations=row.confirmations,
        )
        for row in rows
    ]


def _to_input(row: StoredInput) -> TxInput:
    return TxInput(
        index=row.input_index,
        previous_txid=row.previous_txid,
        previous_output_index=row.previous_output_index,
        source_address=row.source_address,
        value_raw=row.value_raw,
        script_type=row.script_type,
        is_coinbase=row.is_coinbase,
    )


def _to_output(row: StoredOutput) -> TxOutput:
    return TxOutput(
        index=row.output_index,
        destination_address=row.destination_address,
        value_raw=row.value_raw,
        script_type=row.script_type,
        spend_state=SpendState(row.spend_state) if row.spend_state else SpendState.UNKNOWN,
        spent_by_txid=row.spent_by_txid,
        spent_by_input_index=row.spent_by_input_index,
    )


def remove_unseen_bitcoin(
    session: Session,
    chain_id: str,
    address: str,
    *,
    from_height: int,
    seen_txids: Collection[str],
) -> int:
    """Delete the address's transactions from `from_height` on that a complete re-read no longer
    shows: a transaction dropped by a reorganisation must not stay as if it were in the chain."""
    seen = set(seen_txids)
    rows = session.execute(
        select(StoredTransaction.id, StoredTransaction.transaction_hash)
        .join(AddressTransaction, AddressTransaction.transaction_id == StoredTransaction.id)
        .where(
            AddressTransaction.chain_id == chain_id,
            AddressTransaction.address == address,
            StoredTransaction.block_number >= from_height,
        )
    )
    gone = sorted({row.id for row in rows if row.transaction_hash not in seen})
    for group in chunks(gone):
        # The inputs, outputs and links go with it (ON DELETE CASCADE).
        session.execute(delete(StoredTransaction).where(StoredTransaction.id.in_(group)))
    return len(gone)


__all__ = [
    "bitcoin_row",
    "load_bitcoin_history",
    "load_bitcoin_transaction",
    "remove_unseen_bitcoin",
    "save_bitcoin_transactions",
]
