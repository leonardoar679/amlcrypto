"""Reading a whole address history through an adapter.

An adapter answers one page of one stream at a time. This reads every stream to its end (or to a
limit), merges them, removes repeats and puts the result in chronological order. What could not be
read is said so in `coverage`: a history with a gap must never look like a complete one.

Resuming: when a limit stops the read, `resume` holds the cursor of each unfinished stream. Pass it
back to carry on. An empty string means "that stream has not started".
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.chains.models import (
    AccountPage,
    DataQuality,
    Page,
    Transaction,
    Transfer,
    TransferType,
)
from app.providers.base import BudgetExceeded, ProviderError

ACCOUNT = "account"  # normal transactions, and the coin they sent
INTERNAL = "internal"
# Within one transaction: the coin sent first, then what contracts moved, then token events.
_KIND_ORDER = {
    TransferType.NATIVE: 0,
    TransferType.TRC10: 0,
    TransferType.INTERNAL: 1,
    TransferType.ERC20: 2,
    TransferType.TRC20: 2,
}
ALL_KINDS = frozenset(TransferType)
_UNSET_POSITION = 10**9


class HistorySource(Protocol):
    chain_id: str
    # The kind of token event this chain has (ERC-20, TRC-20): its stream is named after it.
    token_kind: TransferType

    async def get_heights(self) -> tuple[int, int]: ...

    async def get_account_page(
        self,
        address: str,
        *,
        cursor: str | None,
        limit: int,
        from_block: int | None,
        to_block: int | None,
    ) -> AccountPage: ...

    async def get_transfers(
        self,
        address: str,
        kind: TransferType,
        *,
        cursor: str | None,
        limit: int,
        from_block: int | None,
        to_block: int | None,
    ) -> Page[Transfer]: ...


@dataclass(slots=True)
class History:
    chain_id: str
    address: str
    transfers: list[Transfer]  # oldest first, each movement once
    transactions: list[Transaction]
    coverage: list[str]  # what is missing or limited, in words
    # Every stream that CAN be read was read to its end: no limit was hit and nothing was cut.
    # Kinds that no source provides at all (`unavailable`) are gaps, but not an incomplete read.
    complete: bool
    head_block: int | None  # the chain's height when the read began
    # When complete: every block up to here has been read, so an update can start from here
    # (minus the chain's reorganisation margin). On a chain whose feeds only show final data this
    # is a little below the head.
    synced_through_block: int | None
    resume: dict[str, str] = field(default_factory=dict)
    unavailable: list[str] = field(default_factory=list)  # streams no source provides

    def summary(self, *, latest: int = 5) -> dict[str, Any]:
        """A compact, JSON-ready description (a job result must not hold the whole history)."""
        by_type = Counter(transfer.transfer_type.value for transfer in self.transfers)
        blocks = [t.block_number for t in self.transfers if t.block_number is not None]
        return {
            "chain_id": self.chain_id,
            "address": self.address,
            "complete": self.complete,
            "head_block": self.head_block,
            "synced_through_block": self.synced_through_block,
            "transfers": len(self.transfers),
            "transfers_by_type": dict(by_type),
            "transactions": len(self.transactions),
            "first_block": min(blocks, default=None),
            "last_block": max(blocks, default=None),
            "providers": sorted({t.provider for t in self.transfers + self.transactions}),
            "coverage": list(self.coverage),
            "unavailable": list(self.unavailable),
            "resume": dict(self.resume),
            "latest": [transfer.to_dict() for transfer in self.transfers[-latest:]],
        }


async def fetch_history(
    source: HistorySource,
    address: str,
    *,
    from_block: int | None = None,
    to_block: int | None = None,
    page_size: int = 100,
    max_items: int = 5000,
    kinds: frozenset[TransferType] = ALL_KINDS,
    resume: dict[str, str] | None = None,
) -> History:
    coverage: list[str] = []
    try:
        head, settled = await source.get_heights()
    except ProviderError as error:
        head = settled = None
        coverage.append(f"the chain's current height is unknown ({error.reason})")

    streams = [ACCOUNT]
    if TransferType.INTERNAL in kinds:
        streams.append(INTERNAL)
    if source.token_kind in kinds:
        streams.append(source.token_kind.value)
    if resume is not None:
        streams = [stream for stream in streams if stream in resume]

    transfers: dict[tuple[str, str, str], Transfer] = {}
    transactions: dict[tuple[str, str], Transaction] = {}
    unfinished: dict[str, str] = {}
    unavailable: list[str] = []
    complete = True

    def keep(items: list[Transfer]) -> None:
        for transfer in items:
            known = transfers.get(transfer.identity)
            if known is None or (
                transfer.data_quality is DataQuality.DECODED
                and known.data_quality is not DataQuality.DECODED
            ):
                transfers[transfer.identity] = transfer

    stopped = False
    for stream in streams:
        if stopped:
            unfinished[stream] = (resume or {}).get(stream, "")
            continue
        cursor = (resume or {}).get(stream) or None
        try:
            while True:
                if stream == ACCOUNT:
                    page = await source.get_account_page(
                        address,
                        cursor=cursor,
                        limit=page_size,
                        from_block=from_block,
                        to_block=to_block,
                    )
                    for transaction in page.transactions:
                        transactions[(transaction.chain_id, transaction.transaction_hash)] = (
                            transaction
                        )
                    keep(page.native_transfers)
                else:
                    kind = TransferType.INTERNAL if stream == INTERNAL else source.token_kind
                    page = await source.get_transfers(
                        address,
                        kind,
                        cursor=cursor,
                        limit=page_size,
                        from_block=from_block,
                        to_block=to_block,
                    )
                    keep(page.items)
                    if page.unavailable:
                        unavailable.append(stream)

                _remember(coverage, page.coverage)
                if page.truncated:
                    complete = False
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor
                if len(transfers) >= max_items:
                    unfinished[stream] = cursor
                    complete, stopped = False, True
                    _remember(
                        coverage,
                        (
                            f"stopped after {max_items} transfers; resume to go on. The streams "
                            "are read one after another, so this is not a time-ordered prefix",
                        ),
                    )
                    break
        except BudgetExceeded:
            # The page that was refused has not been read: resume from the cursor that asked for it.
            unfinished[stream] = cursor or ""
            complete, stopped = False, True
            _remember(coverage, ("stopped: the request budget for this read was used up",))

    # An internal transfer does not say where its transaction sits in the block, but another record
    # of the same transaction usually does.
    positions: dict[tuple[str, str], int] = {}
    for record in (*transactions.values(), *transfers.values()):
        if record.transaction_index is not None:
            positions.setdefault(
                (record.chain_id, record.transaction_hash), record.transaction_index
            )
    # Where blocks are known they order a history; where a feed carries only a time (TRON's token
    # and internal feeds), the time does.
    by_time = any(transfer.block_number is None for transfer in transfers.values())
    ordered = sorted(transfers.values(), key=lambda transfer: _order(transfer, positions, by_time))
    return History(
        chain_id=source.chain_id,
        address=address,
        transfers=ordered,
        transactions=sorted(transactions.values(), key=_transaction_order),
        coverage=coverage,
        complete=complete,
        head_block=head,
        synced_through_block=(to_block if to_block is not None else settled) if complete else None,
        resume=unfinished,
        unavailable=unavailable,
    )


def _remember(coverage: list[str], notes: tuple[str, ...]) -> None:
    for note in notes:
        if note not in coverage:
            coverage.append(note)


def _order(
    transfer: Transfer, positions: dict[tuple[str, str], int], by_time: bool
) -> tuple[float | int | str, ...]:
    position = positions.get((transfer.chain_id, transfer.transaction_hash))
    tail = (
        _UNSET_POSITION if position is None else position,
        # Where no position is known, at least keep one transaction's movements together.
        transfer.transaction_hash,
        _KIND_ORDER[transfer.transfer_type],
        transfer.log_index or 0,
        transfer.event_key,
    )
    if by_time:
        # Records of one block share its time. A block number would only split a transaction's own
        # records, since some of them (token and internal rows) have none and would come first.
        stamp = 0.0 if transfer.timestamp is None else transfer.timestamp.timestamp()
        return (stamp, *tail)
    return (transfer.block_number or 0, *tail)


def _transaction_order(transaction: Transaction) -> tuple[int, int, str]:
    position = transaction.transaction_index
    return (
        transaction.block_number,
        _UNSET_POSITION if position is None else position,
        transaction.transaction_hash,
    )
