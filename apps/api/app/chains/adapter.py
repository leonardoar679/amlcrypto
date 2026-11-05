"""What every chain adapter shares, and what every account-model chain (Ethereum, BNB Smart Chain,
TRON) shares in its adapter.

An adapter is the one object the rest of the platform talks to for a chain. It owns an ordered list
of sources (Etherscan, Alchemy, TronGrid, Esplora ...), sends each call to the right one, keeps a
cursor tied to the source that issued it, and falls back to the next source when starting afresh.
What differs per chain (how a hash looks, how a receipt is decoded) is left to the subclass. Bitcoin
has the same base but not the account-model half: it has no transfers between accounts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import datetime
from functools import partial
from typing import Any, Generic, Protocol, TypeVar

from app.chains.cursors import InvalidCursor, split_source, with_source
from app.chains.models import (
    AccountPage,
    Page,
    Transaction,
    TransactionDetail,
    Transfer,
    TransferType,
)
from app.providers.base import AllProvidersFailed, ProviderError
from app.providers.fallback import first_success

R = TypeVar("R")  # a page: a dataclass with a `next_cursor`


class ChainConfig(Protocol):
    chain_id: str
    slug: str


class Source(Protocol):
    """What any provider must offer for any chain: a name, and the chain's height."""

    name: str

    async def block_height(self, chain: Any) -> int: ...


S = TypeVar("S", bound=Source)


class AccountSource(Source, Protocol):
    """A provider's view of an account-model chain, in the terms the adapter needs."""

    def covers(self, chain: Any, kind: TransferType) -> bool: ...

    async def account_page(
        self,
        chain: Any,
        address: str,
        *,
        cursor: str | None,
        limit: int,
        from_block: int | None,
        to_block: int | None,
    ) -> AccountPage: ...

    async def transfer_page(
        self,
        chain: Any,
        address: str,
        kind: TransferType,
        *,
        cursor: str | None,
        limit: int,
        from_block: int | None,
        to_block: int | None,
    ) -> Page[Transfer]: ...

    async def transaction(
        self, chain: Any, tx_hash: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, datetime | None]: ...


class SourceAdapter(ABC, Generic[S]):
    """An ordered list of sources, the chain's height, and routing that respects cursors."""

    def __init__(self, chain: ChainConfig, sources: Sequence[S]) -> None:
        self.chain = chain
        self._sources: list[S] = list(sources)

    @property
    def chain_id(self) -> str:
        return self.chain.chain_id

    @property
    def source_names(self) -> list[str]:
        return [source.name for source in self._sources]

    async def get_block_height(self) -> int:
        _, height = await first_success(
            "block height",
            [(s.name, partial(s.block_height, self.chain)) for s in self._sources],
        )
        return height

    async def get_heights(self) -> tuple[int, int]:
        """(the chain's height, the height up to which a read started now is complete).

        They differ when a chain's feeds only show final data, which trails the head.
        """
        head = await self.get_block_height()
        return head, head

    async def _route(
        self,
        capability: str,
        sources: Sequence[S],
        cursor: str | None,
        call: Callable[[S, str | None], Awaitable[R]],
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


class AccountAdapter(SourceAdapter[AccountSource], ABC):
    # The kind of fungible-token event this chain has; the history's token stream is named after it.
    token_kind: TransferType

    @property
    def stream_kinds(self) -> frozenset[TransferType]:
        """The kinds of movement that can be read as a stream on this chain."""
        return frozenset({TransferType.NATIVE, TransferType.INTERNAL, self.token_kind})

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
        if kind not in self.stream_kinds:
            have = ", ".join(sorted(k.value for k in self.stream_kinds))
            raise ValueError(f"{self.chain.slug} has no {kind.value} transfers to read ({have})")
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
        return await self.get_transfers(address, self.token_kind, **options)

    # --- one transaction ---------------------------------------------------------------------

    async def get_transaction(self, tx_hash: str) -> TransactionDetail | None:
        """The transaction and the movements the chain itself proves. None if no source has it."""
        wanted = self._normalize_hash(tx_hash)

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

    @abstractmethod
    def _normalize_hash(self, tx_hash: str) -> str:
        """The stored form of a transaction hash; ValueError if it cannot be one."""

    @abstractmethod
    async def _detail(
        self, source: str, tx: dict[str, Any], receipt: dict[str, Any], when: datetime | None
    ) -> TransactionDetail:
        """Decode what a source returned for one transaction."""
