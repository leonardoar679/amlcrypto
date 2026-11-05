"""Transfers and account-model transactions (Ethereum, BNB Smart Chain, TRON, Solana's movements).

On these chains an address's history is every movement it is a party to and every transaction it
sent, received or created, so it is read straight from `from_address` and `to_address`; nothing
remembers which read found a movement. Call the save and remove functions with a write session.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.chains.models import (
    Asset,
    Confirmation,
    DataQuality,
    Transaction,
    Transfer,
    TransferType,
    TxStatus,
)
from app.database.models import StoredTransaction, StoredTransfer
from app.database.repositories.membership import linked_transactions
from app.database.repositories.rows import SaveCounts, Table, chunks, fit, save_rows

TRANSFERS = Table(StoredTransfer, ("chain_id", "transaction_hash", "event_key"))
TRANSACTIONS = Table(StoredTransaction, ("chain_id", "transaction_hash"))


# --- records <-> rows --------------------------------------------------------------------------


def transfer_row(transfer: Transfer) -> dict[str, Any]:
    asset = transfer.asset
    return {
        "chain_id": transfer.chain_id,
        "transaction_hash": transfer.transaction_hash,
        "event_key": transfer.event_key,
        "transfer_type": transfer.transfer_type.value,
        "block_number": transfer.block_number,
        "timestamp": transfer.timestamp,
        "from_address": transfer.from_address,
        "to_address": transfer.to_address,
        "asset_symbol": fit(asset.symbol, 64),
        "asset_name": fit(asset.name, 128),
        "asset_contract_address": asset.contract_address,
        "asset_id": asset.asset_id,
        "asset_decimals": asset.decimals,
        "amount_raw": transfer.amount_raw,
        "provider": fit(transfer.provider, 32),
        "data_quality": transfer.data_quality.value,
        "confirmation_status": transfer.confirmation_status.value,
        "confirmations": transfer.confirmations,
        "transaction_index": transfer.transaction_index,
        "log_index": transfer.log_index,
        "trace_id": fit(transfer.trace_id, 128),
        "source_account": transfer.source_account,
        "destination_account": transfer.destination_account,
        "program_id": transfer.program_id,
        "operation": fit(transfer.operation, 32),
    }


def to_transfer(row: StoredTransfer) -> Transfer:
    return Transfer(
        chain_id=row.chain_id,
        transaction_hash=row.transaction_hash,
        event_key=row.event_key,
        transfer_type=TransferType(row.transfer_type),
        block_number=row.block_number,
        timestamp=row.timestamp,
        from_address=row.from_address,
        to_address=row.to_address,
        asset=Asset(
            symbol=row.asset_symbol,
            contract_address=row.asset_contract_address,
            decimals=row.asset_decimals,
            name=row.asset_name,
            asset_id=row.asset_id,
        ),
        amount_raw=row.amount_raw,
        provider=row.provider,
        data_quality=DataQuality(row.data_quality),
        confirmation_status=Confirmation(row.confirmation_status),
        confirmations=row.confirmations,
        transaction_index=row.transaction_index,
        log_index=row.log_index,
        trace_id=row.trace_id,
        source_account=row.source_account,
        destination_account=row.destination_account,
        program_id=row.program_id,
        operation=row.operation,
    )


def blank_transaction_row(chain_id: str, transaction_hash: str) -> dict[str, Any]:
    """Every column of `transactions`, empty: each family fills in the ones that are its own."""
    return {
        **dict.fromkeys(
            (
                "block_number",
                "block_hash",
                "transaction_index",
                "timestamp",
                "status",
                "from_address",
                "to_address",
                "created_contract",
                "value_raw",
                "fee_raw",
                "fee_payer",
                "method_id",
                "function_name",
                "confirmations",
                "size",
                "weight",
                "version",
                "locktime",
                "error",
                "signers",
                "programs",
                "unreadable",
                "unresolved_owners",
            )
        ),
        "chain_id": chain_id,
        "transaction_hash": transaction_hash,
        "is_contract_call": False,
        "confirmation_status": Confirmation.UNKNOWN.value,
        "provider": "",
        "data_quality": DataQuality.INDEXED.value,
    }


def transaction_row(transaction: Transaction) -> dict[str, Any]:
    row = blank_transaction_row(transaction.chain_id, transaction.transaction_hash)
    row.update(
        block_number=transaction.block_number,
        transaction_index=transaction.transaction_index,
        timestamp=transaction.timestamp,
        status=transaction.status.value,
        from_address=transaction.from_address,
        to_address=transaction.to_address,
        created_contract=transaction.created_contract,
        value_raw=transaction.value_raw,
        fee_raw=transaction.fee_raw,
        method_id=fit(transaction.method_id, 16),
        function_name=fit(transaction.function_name, 255),
        is_contract_call=transaction.is_contract_call,
        confirmation_status=transaction.confirmation_status.value,
        confirmations=transaction.confirmations,
        provider=fit(transaction.provider, 32),
        data_quality=transaction.data_quality.value,
    )
    return row


def to_transaction(row: StoredTransaction) -> Transaction:
    return Transaction(
        chain_id=row.chain_id,
        transaction_hash=row.transaction_hash,
        block_number=row.block_number if row.block_number is not None else 0,
        timestamp=row.timestamp,
        from_address=row.from_address or "",
        to_address=row.to_address,
        created_contract=row.created_contract,
        value_raw=row.value_raw or "0",
        status=TxStatus(row.status),
        provider=row.provider,
        data_quality=DataQuality(row.data_quality),
        fee_raw=row.fee_raw,
        method_id=row.method_id,
        function_name=row.function_name,
        is_contract_call=row.is_contract_call,
        confirmation_status=Confirmation(row.confirmation_status),
        confirmations=row.confirmations,
        transaction_index=row.transaction_index,
    )


# --- saving ------------------------------------------------------------------------------------


def save_transfers(session: Session, transfers: Iterable[Transfer]) -> SaveCounts:
    return save_rows(session, TRANSFERS, [transfer_row(transfer) for transfer in transfers])


def save_transactions(session: Session, transactions: Iterable[Transaction]) -> SaveCounts:
    return save_rows(session, TRANSACTIONS, [transaction_row(item) for item in transactions])


# --- loading -----------------------------------------------------------------------------------


def load_transfers(session: Session, chain_id: str, address: str) -> list[Transfer]:
    """Every stored movement the address is a party to (unordered)."""
    found: dict[int, StoredTransfer] = {}
    for column in (StoredTransfer.from_address, StoredTransfer.to_address):
        for row in session.scalars(
            select(StoredTransfer).where(StoredTransfer.chain_id == chain_id, column == address)
        ):
            found[row.id] = row
    return [to_transfer(row) for row in found.values()]


def load_transactions(session: Session, chain_id: str, address: str) -> list[Transaction]:
    """Every stored transaction the address sent, received or created, and every one that a read
    of the address found (unordered). The second kind matters: TRON, for one, lists a transaction
    for an address that only took part through an internal call."""
    found: dict[int, StoredTransaction] = {
        row.id: row for row in linked_transactions(session, chain_id, address)
    }
    for column in (
        StoredTransaction.from_address,
        StoredTransaction.to_address,
        StoredTransaction.created_contract,
    ):
        for row in session.scalars(
            select(StoredTransaction).where(
                StoredTransaction.chain_id == chain_id, column == address
            )
        ):
            found[row.id] = row
    return [to_transaction(row) for row in found.values()]


def load_transaction_detail(
    session: Session, chain_id: str, transaction_hash: str
) -> tuple[Transaction, list[Transfer]] | None:
    """One stored transaction and every movement stored for it, or None if it is not stored."""
    row = session.scalar(
        select(StoredTransaction).where(
            StoredTransaction.chain_id == chain_id,
            StoredTransaction.transaction_hash == transaction_hash,
        )
    )
    if row is None:
        return None
    moves = session.scalars(
        select(StoredTransfer).where(
            StoredTransfer.chain_id == chain_id, StoredTransfer.transaction_hash == transaction_hash
        )
    )
    return to_transaction(row), [to_transfer(move) for move in moves]


# --- reorganisations ---------------------------------------------------------------------------


def remove_unseen(
    session: Session,
    chain_id: str,
    address: str,
    *,
    from_block: int,
    transfer_types: Collection[TransferType],
    seen_transfers: Collection[tuple[str, str]],
    transactions_read: bool,
    seen_transactions: Collection[str],
) -> int:
    """Delete what a complete re-read of blocks `from_block` onwards did not return.

    The re-read starts a little before the last sync, to absorb reorganisations. Anything stored for
    that stretch that the chain no longer shows (a transaction dropped by a reorganisation) must go,
    or it stays as a phantom. Only the kinds that were read count: a stream the sources do not
    provide returns nothing, which proves nothing about what is stored for it. Returns how many
    records were removed.
    """
    removed = 0
    seen = set(seen_transfers)
    stale: list[int] = []
    for column in (StoredTransfer.from_address, StoredTransfer.to_address):
        rows = session.execute(
            select(
                StoredTransfer.id,
                StoredTransfer.transaction_hash,
                StoredTransfer.event_key,
            ).where(
                StoredTransfer.chain_id == chain_id,
                column == address,
                StoredTransfer.block_number >= from_block,
                StoredTransfer.transfer_type.in_([kind.value for kind in transfer_types]),
            )
        )
        stale += [row.id for row in rows if (row.transaction_hash, row.event_key) not in seen]
    removed += _delete_ids(session, StoredTransfer, sorted(set(stale)))

    if transactions_read:
        gone: list[int] = []
        known = set(seen_transactions)
        for column in (
            StoredTransaction.from_address,
            StoredTransaction.to_address,
            StoredTransaction.created_contract,
        ):
            rows = session.execute(
                select(StoredTransaction.id, StoredTransaction.transaction_hash).where(
                    StoredTransaction.chain_id == chain_id,
                    column == address,
                    StoredTransaction.block_number >= from_block,
                )
            )
            gone += [row.id for row in rows if row.transaction_hash not in known]
        # Nor does a transaction a read of the address found for another reason stay.
        gone += [
            row.id
            for row in linked_transactions(session, chain_id, address)
            if row.block_number is not None
            and row.block_number >= from_block
            and row.transaction_hash not in known
        ]
        removed += _delete_ids(session, StoredTransaction, sorted(set(gone)))
    return removed


def _delete_ids(session: Session, model: Any, ids: Sequence[int]) -> int:
    for group in chunks(ids):
        session.execute(delete(model).where(model.id.in_(group)))
    return len(ids)
