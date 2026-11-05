"""Reading and showing a history without caring which kind of chain it is.

Account-model chains give transfers between accounts; Bitcoin gives transactions of inputs and
outputs. The command line and the job queue treat both the same way through here.
"""

from __future__ import annotations

from typing import Any

from app.chains.adapter import AccountAdapter
from app.chains.bitcoin.adapter import BitcoinAdapter
from app.chains.bitcoin.models import BitcoinDetail
from app.chains.models import TransactionDetail
from app.ingestion.fetcher import History, fetch_history
from app.ingestion.render import (
    render_bitcoin_transaction,
    render_history,
    render_transaction,
    render_utxo_history,
)
from app.ingestion.utxo import UtxoHistory, fetch_utxo_history


async def read_history(
    adapter: AccountAdapter | BitcoinAdapter,
    address: str,
    *,
    from_block: int | None = None,
    to_block: int | None = None,
    max_items: int,
    resume: dict[str, str] | None = None,
) -> History | UtxoHistory:
    if isinstance(adapter, BitcoinAdapter):
        return await fetch_utxo_history(
            adapter,
            address,
            from_block=from_block,
            to_block=to_block,
            max_items=max_items,
            resume=resume,
        )
    return await fetch_history(
        adapter,
        address,
        from_block=from_block,
        to_block=to_block,
        max_items=max_items,
        resume=resume,
    )


def history_json(history: History | UtxoHistory) -> dict[str, Any]:
    """Everything in a history, JSON-ready."""
    if isinstance(history, UtxoHistory):
        return {
            **history.summary(latest=0),
            "transactions_list": [history.detailed(tx) for tx in history.transactions],
        }
    return {
        **history.summary(latest=len(history.transfers)),
        "transfers_list": [transfer.to_dict() for transfer in history.transfers],
    }


def render_any_history(history: History | UtxoHistory) -> str:
    if isinstance(history, UtxoHistory):
        return render_utxo_history(history)
    return render_history(history)


def render_any_transaction(detail: TransactionDetail | BitcoinDetail) -> str:
    if isinstance(detail, BitcoinDetail):
        return render_bitcoin_transaction(detail)
    return render_transaction(detail)
