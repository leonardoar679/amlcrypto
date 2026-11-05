"""Reading a whole address history through an adapter.

An adapter answers one page of one stream at a time. This reads every stream to its end (or to a
limit), merges them, removes repeats and puts the result in chronological order. What could not be
read is said so in `coverage`: a history with a gap must never look like a complete one.

Resuming: when a limit stops the read, `resume` holds the cursor of each unfinished stream. Pass it
back to carry on. An empty string means "that stream has not started".
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.chains.models import (
    AccountPage,
    DataQuality,
    Page,
    StreamState,
    StreamStatus,
    Transaction,
    Transfer,
    TransferType,
)
from app.ingestion.partial import attach_partial
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
    # How far each stream that was read got, by stream name.
    streams: dict[str, StreamState] = field(default_factory=dict)

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
    states: dict[str, StreamState] = {}
    complete = True
    reached = to_block if to_block is not None else settled  # what a stream read to its end covers

    def keep(items: list[Transfer]) -> None:
        for transfer in items:
            known = transfers.get(transfer.identity)
            if known is None or (
                transfer.data_quality is DataQuality.DECODED
                and known.data_quality is not DataQuality.DECODED
            ):
                transfers[transfer.identity] = transfer

    def result(*, whole: bool) -> History:
        ordered, ordered_transactions = assemble_history(transfers.values(), transactions.values())
        return History(
            chain_id=source.chain_id,
            address=address,
            transfers=ordered,
            transactions=ordered_transactions,
            coverage=coverage,
            complete=whole,
            head_block=head,
            synced_through_block=reached if whole else None,
            resume=unfinished,
            unavailable=unavailable,
            streams=states,
        )

    stopped = False
    for position, stream in enumerate(streams):
        if stopped:
            unfinished[stream] = (resume or {}).get(stream, "")
            states[stream] = StreamState(StreamStatus.PARTIAL, cursor=unfinished[stream])
            continue
        cursor = (resume or {}).get(stream) or None
        cut = False  # this stream's window ran out before its history did
        absent = False  # no source provides this stream
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
                        absent = True

                _remember(coverage, page.coverage)
                if page.truncated:
                    complete = False
                    cut = True
                if page.next_cursor is None:
                    status = (
                        StreamStatus.UNAVAILABLE
                        if absent
                        else StreamStatus.TRUNCATED
                        if cut
                        else StreamStatus.COMPLETE
                    )
                    states[stream] = StreamState(status, synced_through=reached)
                    break
                cursor = page.next_cursor
                if len(transfers) >= max_items:
                    unfinished[stream] = cursor
                    states[stream] = StreamState(StreamStatus.PARTIAL, cursor=cursor)
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
            states[stream] = StreamState(StreamStatus.PARTIAL, cursor=unfinished[stream])
            complete, stopped = False, True
            _remember(coverage, ("stopped: the request budget for this read was used up",))
        except ProviderError as error:
            # The pages already read are good data that cost real quota: hand them to a caller that
            # keeps histories. The failed page is the one to read again, with the streams after it.
            for later in streams[position:]:
                unfinished[later] = (
                    (cursor or "") if later == stream else (resume or {}).get(later, "")
                )
                states[later] = StreamState(StreamStatus.PARTIAL, cursor=unfinished[later])
            _remember(coverage, (f"stopped: {error.reason}",))
            attach_partial(error, result(whole=False))
            raise

    return result(whole=complete)


def assemble_history(
    transfers: Iterable[Transfer], transactions: Iterable[Transaction]
) -> tuple[list[Transfer], list[Transaction]]:
    """Each movement once, in chronological order, and the transactions in order too.

    Whether the records were just read or have been stored and loaded, a history is put together
    the same way, so the same records always give the same history.
    """
    moves = list(transfers)
    sent = list(transactions)
    # An internal transfer does not say where its transaction sits in the block, but another record
    # of the same transaction usually does.
    positions: dict[tuple[str, str], int] = {}
    for record in (*sent, *moves):
        if record.transaction_index is not None:
            positions.setdefault(
                (record.chain_id, record.transaction_hash), record.transaction_index
            )
    # Where blocks are known they order a history; where a feed carries only a time (TRON's token
    # and internal feeds), the time does.
    by_time = any(transfer.block_number is None for transfer in moves)
    ordered = sorted(moves, key=lambda transfer: _order(transfer, positions, by_time))
    return ordered, sorted(sent, key=_transaction_order)


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
