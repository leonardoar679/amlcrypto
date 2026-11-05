"""Esplora as a source of Bitcoin address histories, transactions and the chain height.

Esplora is the open-source indexer behind blockstream.info and mempool.space (set
ESPLORA_BASE_URL to use either, or your own). What is assumed here, from its published REST API and
checked against the live service on 2026-11-05:

* /address/:address/txs/chain lists an address's CONFIRMED transactions, newest first, 25 to a page,
  and /address/:address/txs/chain/:last_seen_txid gives the next 25 after that transaction. The
  page size cannot be changed. A page shorter than 25 is the last one.
* every input comes with the output it spends (`prevout`), so a transaction is complete as listed;
* /tx/:txid/outspends says, for each output, whether it is spent and by what; an unknown transaction
  is a 404;
* /address/:address holds the node's own count of the address's transactions and the totals paid to
  and spent from it, which a history can be checked against.

The cursor is the id of the last transaction of the previous page. The mempool is not read: a
history is confirmed transactions only, so it does not change under the reader's feet.
"""

from __future__ import annotations

import re
from typing import Any

from app.chains.bitcoin.chains import BitcoinChain
from app.chains.bitcoin.models import AddressSummary, BitcoinTransaction, UtxoPage
from app.chains.bitcoin.normalize import esplora_transaction, txid_of, unresolved_inputs
from app.chains.cursors import InvalidCursor
from app.chains.models import Page
from app.chains.values import MalformedRow, raw_amount, to_int
from app.providers.base import BudgetExceeded, ProviderBadResponse, ProviderError
from app.providers.budget import RequestBudget
from app.providers.client import ProviderClient, ProviderResponse
from app.providers.rawsink import BITCOIN_TRANSACTION, RawSink

NAME = "esplora"
PAGE_SIZE = 25  # fixed by Esplora
_TXID = re.compile(r"^[0-9a-f]{64}$")


class EsploraSource:
    name = NAME

    def __init__(
        self,
        client: ProviderClient,
        *,
        budget: RequestBudget | None = None,
        raw: RawSink | None = None,
    ) -> None:
        self._client = client
        self._budget = budget
        self._raw = raw

    async def _get(self, path: str, *, accept: tuple[int, ...] = ()) -> ProviderResponse:
        return await self._client.request(NAME, "GET", path, budget=self._budget, accept=accept)

    # --- the chain ---------------------------------------------------------------------------

    async def block_height(self, chain: BitcoinChain) -> int:
        data = (await self._get("/blocks/tip/height")).data
        if isinstance(data, bool) or not isinstance(data, int) or data < 0:
            raise ProviderBadResponse(NAME, "unexpected response shape")
        return data

    # --- address history ---------------------------------------------------------------------

    async def address_summary(self, chain: BitcoinChain, address: str) -> AddressSummary:
        data = (await self._get(f"/address/{address}")).data
        try:
            stats, pending = data["chain_stats"], data["mempool_stats"]
            return AddressSummary(
                address=address,
                tx_count=to_int(stats["tx_count"]),
                funded_count=to_int(stats["funded_txo_count"]),
                funded_raw=raw_amount(stats["funded_txo_sum"]),
                spent_count=to_int(stats["spent_txo_count"]),
                spent_raw=raw_amount(stats["spent_txo_sum"]),
                mempool_tx_count=to_int(pending["tx_count"]),
                provider=NAME,
            )
        except (KeyError, TypeError, MalformedRow) as error:
            raise ProviderBadResponse(NAME, "unexpected response shape") from error

    async def address_page(
        self,
        chain: BitcoinChain,
        address: str,
        *,
        cursor: str | None = None,
        from_block: int | None = None,
        to_block: int | None = None,
    ) -> UtxoPage:
        """The next 25 confirmed transactions of an address, newest first.

        A block range is applied here: transactions above `to_block` are skipped (the read must go
        on through them to reach older ones), and the read ends at the first below `from_block`.
        """
        if cursor is not None and not _TXID.fullmatch(cursor):
            raise InvalidCursor("not an Esplora cursor")
        path = f"/address/{address}/txs/chain" + (f"/{cursor}" if cursor else "")
        response = await self._get(path, accept=(404,) if cursor else ())
        if response.status_code == 404:
            raise ProviderBadResponse(
                NAME, "the transaction this read stopped at is no longer in the chain; start again"
            )
        rows = response.data
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ProviderBadResponse(NAME, "unexpected response shape")

        transactions: list[BitcoinTransaction] = []
        skipped = 0
        for row in rows:
            try:
                decoded = esplora_transaction(row, chain, NAME)
            except MalformedRow:
                skipped += 1
                continue
            transactions.append(decoded)
            if self._raw is not None:
                self._raw.offer(chain.chain_id, BITCOIN_TRANSACTION, decoded.txid, NAME, row)

        next_cursor = None
        if len(rows) >= PAGE_SIZE:
            try:
                next_cursor = txid_of(rows[-1].get("txid"))
            except MalformedRow as error:
                raise ProviderBadResponse(NAME, "unexpected response shape") from error

        # Newest first: below the start of the range, nothing older can qualify.
        oldest = min(
            (tx.block_height for tx in transactions if tx.block_height is not None), default=None
        )
        if from_block is not None and oldest is not None and oldest < from_block:
            next_cursor = None
        wanted = [
            tx
            for tx in transactions
            if tx.block_height is not None
            and (from_block is None or tx.block_height >= from_block)
            and (to_block is None or tx.block_height <= to_block)
        ]
        return Page(wanted, next_cursor, False, _notes(skipped, transactions))

    # --- one transaction ---------------------------------------------------------------------

    async def transaction(
        self, chain: BitcoinChain, txid: str
    ) -> tuple[dict[str, Any] | None, Any]:
        """The transaction as Esplora gives it and, if it could be had, which outputs are spent.

        (None, None) if there is no such transaction. A failure to get the spend list does not fail
        the lookup: the second item is None and the caller says the spend state is unknown.
        """
        response = await self._get(f"/tx/{txid}", accept=(404,))
        if response.status_code == 404:
            return None, None
        if not isinstance(response.data, dict):
            raise ProviderBadResponse(NAME, "unexpected response shape")
        if self._raw is not None:
            self._raw.offer(chain.chain_id, BITCOIN_TRANSACTION, txid, NAME, response.data)
        try:
            outspends = (await self._get(f"/tx/{txid}/outspends")).data
        except ProviderError as error:
            if isinstance(error, BudgetExceeded):
                raise
            outspends = None
        return response.data, outspends


def _notes(skipped: int, transactions: list[BitcoinTransaction]) -> tuple[str, ...]:
    notes = []
    if skipped:
        notes.append(f"{skipped} transactions from esplora could not be read and were skipped")
    gaps = sum(1 for tx in transactions if unresolved_inputs(tx))
    if gaps:
        notes.append(
            f"{gaps} transactions have inputs whose spent output esplora did not give; "
            "amounts sent in them may be short"
        )
    return tuple(notes)


__all__ = ["EsploraSource"]
