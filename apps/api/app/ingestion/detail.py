"""Single transactions: what a lookup stores, and what can be answered from storage.

A transaction looked up by hash is decoded from the chain's own data (a receipt, the transaction
itself), which is better evidence than an indexer's row, so it is stored and it outranks the
indexer's version of the same record. It is answered from storage only where the stored record is
exactly what a new lookup would give: a finalized Solana transaction never changes, and its notes
follow from the record. An Ethereum or TRON receipt carries notes about what was left out of it,
and a Bitcoin transaction's spend state moves on, so those are always asked of the provider.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.chains.bitcoin.models import BitcoinDetail
from app.chains.models import TransactionDetail
from app.chains.solana.adapter import detail_of
from app.chains.solana.models import SolanaDetail
from app.database.repositories import bitcoin as bitcoin_store
from app.database.repositories import solana as solana_store
from app.database.repositories import transfers as transfer_store
from app.database.repositories.rows import SaveCounts

Detail = TransactionDetail | BitcoinDetail | SolanaDetail


def save_detail(session: Session, detail: Detail) -> SaveCounts:
    """Store a transaction that was looked up. Call with a write session."""
    if isinstance(detail, SolanaDetail):
        return solana_store.save_solana_transactions(session, [detail.transaction])
    if isinstance(detail, BitcoinDetail):
        if detail.transaction.block_height is None:
            # Unconfirmed: it may never be mined, and nothing would ever remove it.
            return SaveCounts()
        return bitcoin_store.save_bitcoin_transactions(session, [detail.transaction])
    counts = transfer_store.save_transactions(session, [detail.transaction])
    return counts + transfer_store.save_transfers(session, detail.transfers)


def stored_solana_detail(session: Session, chain_id: str, signature: str) -> SolanaDetail | None:
    """A finalized Solana transaction already stored, exactly as a new lookup would return it."""
    transaction = solana_store.load_solana_transaction(session, chain_id, signature)
    return None if transaction is None else detail_of(transaction)
