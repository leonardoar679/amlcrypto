"""The Bitcoin adapter: address histories and transactions as inputs and outputs.

It shares routing, cursors and heights with the account-model chains (`SourceAdapter`) but not their
vocabulary: Bitcoin has no transfers between accounts, so there is no `get_transfers` here, only
transactions, and the platform reads them as the chain wrote them.
"""

from __future__ import annotations

from collections.abc import Awaitable
from functools import partial
from typing import Any, Protocol

from app.chains.adapter import Source, SourceAdapter
from app.chains.bitcoin.chains import BitcoinChain
from app.chains.bitcoin.models import AddressSummary, BitcoinDetail, UtxoPage
from app.chains.bitcoin.normalize import (
    apply_outspends,
    esplora_transaction,
    txid_of,
    with_head,
)
from app.chains.values import MalformedRow
from app.providers.base import AllProvidersFailed, ProviderError
from app.providers.budget import RequestBudget
from app.providers.esplora import EsploraSource
from app.providers.fallback import first_success
from app.providers.rawsink import RawSink
from app.providers.service import ProviderService


class UtxoSource(Source, Protocol):
    """A provider's view of Bitcoin, in the terms the adapter needs."""

    def address_summary(self, chain: Any, address: str) -> Awaitable[AddressSummary]: ...

    def address_page(
        self,
        chain: Any,
        address: str,
        *,
        cursor: str | None,
        from_block: int | None,
        to_block: int | None,
    ) -> Awaitable[UtxoPage]: ...

    def transaction(
        self, chain: Any, txid: str
    ) -> Awaitable[tuple[dict[str, Any] | None, Any]]: ...


class BitcoinAdapter(SourceAdapter[UtxoSource]):
    chain: BitcoinChain

    async def get_address_page(
        self,
        address: str,
        *,
        cursor: str | None = None,
        from_block: int | None = None,
        to_block: int | None = None,
    ) -> UtxoPage:
        """The next page of an address's confirmed transactions, newest first."""
        return await self._route(
            "address transactions",
            self._sources,
            cursor,
            lambda source, inner: source.address_page(
                self.chain, address, cursor=inner, from_block=from_block, to_block=to_block
            ),
        )

    async def get_address_summary(self, address: str) -> AddressSummary:
        """The provider's own count of the address's transactions and totals."""
        _, summary = await first_success(
            "address summary",
            [(s.name, partial(s.address_summary, self.chain, address)) for s in self._sources],
        )
        return summary

    async def get_transaction(self, txid: str) -> BitcoinDetail | None:
        """The transaction, with which of its outputs are spent. None if no source has it."""
        try:
            wanted = txid_of(txid)
        except MalformedRow as error:
            raise ValueError("not a transaction id") from error

        failures: list[tuple[str, ProviderError]] = []
        for source in self._sources:
            try:
                tx, outspends = await source.transaction(self.chain, wanted)
            except ProviderError as error:
                failures.append((source.name, error))
                continue
            if tx is None:
                continue  # unknown here; another source may know
            return await self._detail(source.name, tx, outspends)

        if failures:
            # A source that could not answer might have known it, so "not found" would be a guess.
            raise AllProvidersFailed("transaction", failures)
        return None

    async def _detail(self, source: str, tx: dict[str, Any], outspends: Any) -> BitcoinDetail:
        transaction = esplora_transaction(tx, self.chain, source)
        coverage: list[str] = []

        try:
            head: int | None = await self.get_block_height()
        except ProviderError:
            head = None
            coverage.append("confirmations are unknown: the chain's height could not be read")
        transaction = with_head(transaction, head, self.chain)

        if outspends is None:
            coverage.append("which outputs are spent is unknown: the spend list could not be read")
        else:
            try:
                transaction = apply_outspends(transaction, outspends)
            except MalformedRow:
                coverage.append("which outputs are spent is unknown: the spend list was unreadable")
        return BitcoinDetail(transaction, tuple(coverage))


def build_bitcoin_adapter(
    chain: BitcoinChain,
    providers: ProviderService,
    *,
    budget: RequestBudget | None = None,
    raw: RawSink | None = None,
) -> BitcoinAdapter:
    """Esplora, which needs no key. (Alchemy's Bitcoin API is a paid add-on and is not used yet.)"""
    sources: list[UtxoSource] = []
    if providers.client.is_configured("esplora"):
        sources.append(EsploraSource(providers.client, budget=budget, raw=raw))
    return BitcoinAdapter(chain, sources)
