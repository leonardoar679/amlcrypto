"""The Solana adapter: wallet histories and transactions, decoded.

It shares routing, cursors and heights with the other chains (`SourceAdapter`). A Solana wallet's
history is ONE feed of transactions, each carrying every kind of movement, so there are no
separate streams to read: a page of transactions is a page of everything.
"""

from __future__ import annotations

from collections.abc import Awaitable
from functools import partial
from typing import Any, Protocol

from app.chains.adapter import Source, SourceAdapter
from app.chains.models import Page
from app.chains.solana.chains import SolanaChain
from app.chains.solana.models import SolanaDetail, SolanaTransaction, WalletFigures
from app.chains.solana.normalize import signature_of, solana_transaction
from app.chains.values import MalformedRow
from app.providers.base import AllProvidersFailed, ProviderError
from app.providers.budget import RequestBudget
from app.providers.fallback import first_success
from app.providers.helius import HeliusSource
from app.providers.rawsink import RawSink
from app.providers.service import ProviderService


class SolanaSource(Source, Protocol):
    """A provider's view of Solana, in the terms the adapter needs."""

    def address_page(
        self,
        chain: Any,
        address: str,
        *,
        cursor: str | None,
        from_block: int | None,
        to_block: int | None,
    ) -> Awaitable[Page[SolanaTransaction]]: ...

    def transaction(self, chain: Any, signature: str) -> Awaitable[dict[str, Any] | None]: ...

    def wallet_figures(self, chain: Any, address: str) -> Awaitable[WalletFigures]: ...


class SolanaAdapter(SourceAdapter[SolanaSource]):
    chain: SolanaChain

    async def get_address_page(
        self,
        address: str,
        *,
        cursor: str | None = None,
        from_block: int | None = None,
        to_block: int | None = None,
    ) -> Page[SolanaTransaction]:
        """The next page of a wallet's finalized transactions, newest first. Blocks are slots."""
        return await self._route(
            "address transactions",
            self._sources,
            cursor,
            lambda source, inner: source.address_page(
                self.chain, address, cursor=inner, from_block=from_block, to_block=to_block
            ),
        )

    async def get_wallet_figures(self, address: str) -> WalletFigures:
        """What the chain says the wallet holds now."""
        _, figures = await first_success(
            "wallet balances",
            [(s.name, partial(s.wallet_figures, self.chain, address)) for s in self._sources],
        )
        return figures

    async def get_transaction(self, signature: str) -> SolanaDetail | None:
        """The transaction, decoded. None if no source has it finalized."""
        try:
            wanted = signature_of(signature)
        except MalformedRow as error:
            raise ValueError("not a transaction signature") from error

        failures: list[tuple[str, ProviderError]] = []
        for source in self._sources:
            try:
                item = await source.transaction(self.chain, wanted)
            except ProviderError as error:
                failures.append((source.name, error))
                continue
            if item is None:
                continue  # unknown here, or not finalized yet; another source may know
            return detail_of(solana_transaction(item, self.chain, source.name))

        if failures:
            # A source that could not answer might have known it, so "not found" would be a guess.
            raise AllProvidersFailed("transaction", failures)
        return None


def detail_of(transaction: SolanaTransaction) -> SolanaDetail:
    """The transaction with the notes its own contents call for."""
    coverage: list[str] = []
    if transaction.unreadable:
        coverage.append(
            f"{transaction.unreadable} instructions move value in a shape this reader does not know"
        )
    if transaction.unresolved_owners:
        coverage.append(
            f"{transaction.unresolved_owners} token accounts have no owner in this transaction's "
            "balances; the account itself stands in for its owner"
        )
    if transaction.unattributed:
        coverage.append(
            f"{len(transaction.unattributed)} balance changes are explained by no instruction "
            "(a program moved them directly); they are listed as unattributed"
        )
    return SolanaDetail(transaction, tuple(coverage))


def build_solana_adapter(
    chain: SolanaChain,
    providers: ProviderService,
    *,
    budget: RequestBudget | None = None,
    raw: RawSink | None = None,
) -> SolanaAdapter:
    """Helius, if it has a key."""
    sources: list[SolanaSource] = []
    if providers.client.is_configured("helius"):
        sources.append(HeliusSource(providers.client, budget=budget, raw=raw))
    return SolanaAdapter(chain, sources)
