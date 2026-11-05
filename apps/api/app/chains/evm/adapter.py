"""The EVM adapter: one object that answers for a chain, whichever sources are configured.

Sources are tried in order. A cursor remembers which source issued it and goes back to that one,
because a position in Etherscan's history means nothing to Alchemy. Starting a history afresh
(no cursor) tries the sources in order and uses the first that answers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import datetime
from functools import partial
from typing import Any, Protocol, TypeVar

from app.chains.evm.chains import EvmChain
from app.chains.evm.cursors import InvalidCursor, split_source, with_source
from app.chains.evm.normalize import (
    MalformedRow,
    hash_of,
    transaction_from_rpc,
    transfers_from_receipt,
)
from app.chains.models import (
    AccountPage,
    Page,
    Transaction,
    TransactionDetail,
    Transfer,
    TransferType,
)
from app.providers.alchemy import AlchemySource
from app.providers.base import AllProvidersFailed, ProviderError
from app.providers.budget import RequestBudget
from app.providers.etherscan import EtherscanSource
from app.providers.fallback import first_success
from app.providers.service import ProviderService

R = TypeVar("R", AccountPage, Page)


class EvmSource(Protocol):
    name: str

    def covers(self, chain: EvmChain, kind: TransferType) -> bool: ...

    async def block_height(self, chain: EvmChain) -> int: ...

    async def account_page(
        self,
        chain: EvmChain,
        address: str,
        *,
        cursor: str | None,
        limit: int,
        from_block: int | None,
        to_block: int | None,
    ) -> AccountPage: ...

    async def transfer_page(
        self,
        chain: EvmChain,
        address: str,
        kind: TransferType,
        *,
        cursor: str | None,
        limit: int,
        from_block: int | None,
        to_block: int | None,
    ) -> Page[Transfer]: ...

    async def transaction(
        self, chain: EvmChain, tx_hash: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, datetime | None]: ...


class EvmAdapter:
    def __init__(self, chain: EvmChain, sources: Sequence[EvmSource]) -> None:
        self.chain = chain
        self._sources = list(sources)

    @property
    def chain_id(self) -> str:
        return self.chain.chain_id

    @property
    def source_names(self) -> list[str]:
        return [source.name for source in self._sources]

    # --- the chain ---------------------------------------------------------------------------

    async def get_block_height(self) -> int:
        _, height = await first_success(
            "block height",
            [(s.name, partial(s.block_height, self.chain)) for s in self._sources],
        )
        return height

    # --- address history ---------------------------------------------------------------------

    async def get_account_page(
        self,
        address: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
        from_block: int | None = None,
        to_block: int | None = None,
    ) -> AccountPage:
        """Normal transactions of an address and the coin each one sent."""
        return await self._route(
            "address transactions",
            self._sources,
            cursor,
            lambda source, inner: source.account_page(
                self.chain,
                address,
                cursor=inner,
                limit=limit,
                from_block=from_block,
                to_block=to_block,
            ),
        )

    async def get_address_activity(
        self,
        address: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
        from_block: int | None = None,
        to_block: int | None = None,
    ) -> Page[Transaction]:
        page = await self.get_account_page(
            address, cursor=cursor, limit=limit, from_block=from_block, to_block=to_block
        )
        return Page(page.transactions, page.next_cursor, page.truncated, page.coverage)

    async def get_transfers(
        self,
        address: str,
        kind: TransferType,
        *,
        cursor: str | None = None,
        limit: int = 100,
        from_block: int | None = None,
        to_block: int | None = None,
    ) -> Page[Transfer]:
        """Movements of one kind: the coin (NATIVE), a contract's coin (INTERNAL) or tokens."""
        if kind is TransferType.NATIVE:
            page = await self.get_account_page(
                address, cursor=cursor, limit=limit, from_block=from_block, to_block=to_block
            )
            return Page(page.native_transfers, page.next_cursor, page.truncated, page.coverage)

        capable = [source for source in self._sources if source.covers(self.chain, kind)]
        if not capable:
            return Page(
                [],
                None,
                coverage=(
                    f"no configured source provides {kind.value} transfers on {self.chain.slug}",
                ),
                unavailable=True,
            )
        return await self._route(
            f"{kind.value} transfers",
            capable,
            cursor,
            lambda source, inner: source.transfer_page(
                self.chain,
                address,
                kind,
                cursor=inner,
                limit=limit,
                from_block=from_block,
                to_block=to_block,
            ),
        )

    async def get_token_transfers(self, address: str, **options: Any) -> Page[Transfer]:
        return await self.get_transfers(address, TransferType.ERC20, **options)

    # --- one transaction ---------------------------------------------------------------------

    async def get_transaction(self, tx_hash: str) -> TransactionDetail | None:
        """The transaction and the movements its receipt proves. None if no source has it mined.

        Internal coin movements are not in a receipt, so they are not here; the answer says so.
        """
        try:
            wanted = hash_of(tx_hash)
        except MalformedRow as error:
            raise ValueError("not a transaction hash") from error

        failures: list[tuple[str, ProviderError]] = []
        for source in self._sources:
            try:
                tx, receipt, when = await source.transaction(self.chain, wanted)
            except ProviderError as error:
                failures.append((source.name, error))
                continue
            if tx is None or receipt is None:
                continue  # unknown here, or not mined yet; another source may know
            return await self._detail(source.name, tx, receipt, when)

        if failures:
            # Even if another source said "not found", a source that could not answer might have
            # known it, so "not found" would be a guess.
            raise AllProvidersFailed("transaction", failures)
        return None

    async def _detail(
        self, source: str, tx: dict[str, Any], receipt: dict[str, Any], when: datetime | None
    ) -> TransactionDetail:
        try:
            head = await self.get_block_height()
        except ProviderError:
            head = None  # confirmations stay unknown rather than failing the lookup
        transaction = transaction_from_rpc(tx, receipt, self.chain, when, source, head)
        transfers, unreadable = transfers_from_receipt(tx, receipt, self.chain, when, source, head)
        coverage = ["internal coin movements are not part of a receipt; see the address history"]
        if unreadable:
            coverage.append(f"{unreadable} Transfer events in a non-ERC-20 shape were not read")
        if any(
            t.asset.decimals is None for t in transfers if t.transfer_type is TransferType.ERC20
        ):
            coverage.append("token symbols and decimals are not in a receipt")
        return TransactionDetail(transaction, tuple(transfers), tuple(coverage))

    # --- routing -----------------------------------------------------------------------------

    async def _route(
        self,
        capability: str,
        sources: Sequence[EvmSource],
        cursor: str | None,
        call: Callable[[EvmSource, str | None], Awaitable[R]],
    ) -> R:
        """Run `call` on the source that issued `cursor`, or else on the first that works."""
        by_name = {source.name: source for source in sources}
        if cursor:
            name, inner = split_source(cursor)
            if name not in by_name:
                raise InvalidCursor(f"{name} is not a source for {self.chain.slug} here")
            result = await call(by_name[name], inner)
        else:
            name, result = await first_success(
                capability, [(s.name, partial(call, s, None)) for s in sources]
            )
        return replace(result, next_cursor=with_source(name, result.next_cursor))


def build_evm_adapter(
    chain: EvmChain,
    providers: ProviderService,
    *,
    etherscan_paid_plan: bool = False,
    budget: RequestBudget | None = None,
) -> EvmAdapter:
    """The sources that are configured and can serve this chain, the richest first."""
    client = providers.client
    sources: list[EvmSource] = []

    etherscan = providers.spec("etherscan")
    serves = chain.chain_id in etherscan.chains or (
        etherscan_paid_plan and chain.chain_id in etherscan.paid_chains
    )
    if client.is_configured("etherscan") and serves:
        sources.append(EtherscanSource(client, budget=budget))

    if client.is_configured("alchemy") and chain.chain_id in providers.spec("alchemy").chains:
        sources.append(AlchemySource(client, budget=budget))

    return EvmAdapter(chain, sources)
