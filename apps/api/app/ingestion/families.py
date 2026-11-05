"""How each family of chains is stored, and what bringing its stored history up to date means.

* Account chains (Ethereum, BNB Smart Chain, TRON) are read as three streams, each oldest first. A
  stream that was stopped carries on from its cursor; a finished one is read again from a little
  before where it ended, which catches late-indexed records and reorganised blocks.
* Bitcoin and Solana are read as one stream of whole transactions, newest first. Bringing a stored
  history up to date reads only the newest transactions, down to a little before the last sync; a
  history whose older part was never read keeps a cursor to carry on towards the oldest.

Each family can plan what to read from the coverage it holds, save what a read returned, say what
the coverage is afterwards, and put a history back together from storage so that it is the same
history a fresh read would have returned.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.chains.adapter import AccountAdapter
from app.chains.bitcoin.adapter import BitcoinAdapter
from app.chains.bitcoin.models import AddressSummary
from app.chains.evm.chains import EvmChain
from app.chains.models import StreamState, StreamStatus, TransferType
from app.chains.solana.adapter import SolanaAdapter
from app.chains.solana.models import WalletFigures
from app.database.repositories import bitcoin as bitcoin_store
from app.database.repositories import solana as solana_store
from app.database.repositories import transfers as transfer_store
from app.database.repositories.acquisition import COMPLETE, PARTIAL, TRUNCATED, Coverage
from app.database.repositories.membership import link_address, transaction_ids
from app.database.repositories.rows import SaveCounts
from app.ingestion.fetcher import ACCOUNT, INTERNAL, History, assemble_history
from app.ingestion.solana import (
    SolanaHistory,
    assemble_solana,
    limit_notes,
    verify_solana,
)
from app.ingestion.utxo import STREAM, UtxoHistory, assemble_utxo, verify_utxo

STOPPED = "stopped"  # the start of every note that says a read was cut short
# A read that a provider error cut short has not brought anything up to date: its row must look
# stale, so that the next sync carries on at once instead of serving the half it got.
NEVER = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class PassPlan:
    """One read to make: `full` from the beginning, `resume` from saved cursors, or `incremental`
    from a block a little before the last sync."""

    kind: str
    from_block: int | None = None
    # The streams to read, with the cursor each carries on from ("" starts it). None: all of them.
    resume: dict[str, str] | None = None


@dataclass(slots=True)
class PassOutcome:
    plan: PassPlan
    history: Any  # History, UtxoHistory or SolanaHistory
    failed: bool = False  # a provider error ended the read; `history` holds what was read before


@dataclass(frozen=True, slots=True)
class Saved:
    counts: SaveCounts
    removed: int = 0


@dataclass(frozen=True, slots=True)
class Check:
    """A whole history compared with the chain's own figures."""

    verified: str | None  # "ok", "mismatch", or None when the figures could not be read
    figures: dict[str, Any] | None
    notes: tuple[str, ...] = field(default=())


def seconds_since_synced(coverage: dict[str, Coverage], now: datetime) -> float | None:
    """The age of the oldest stream: a history is only as fresh as its stalest part."""
    if not coverage:
        return None
    return (now - min(row.synced_at for row in coverage.values())).total_seconds()


def _merged(*groups: Any) -> tuple[str, ...]:
    notes: list[str] = []
    for group in groups:
        for note in group:
            if note not in notes:
                notes.append(note)
    return tuple(notes)


class Family(ABC):
    verifies = False  # whether a complete history is checked against the chain's own figures

    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter
        self.chain_id: str = adapter.chain_id

    @property
    @abstractmethod
    def streams(self) -> tuple[str, ...]: ...

    @abstractmethod
    def plan(
        self,
        coverage: dict[str, Coverage],
        now: datetime,
        *,
        max_age: float,
        refresh: bool,
        rebuild: bool,
    ) -> tuple[list[PassPlan], str]:
        """What to read, and why; no passes means the stored history is current."""

    @abstractmethod
    def save(
        self, session: Session, address: str, outcome: PassOutcome, *, rebuild: bool
    ) -> Saved: ...

    @abstractmethod
    def coverage_after(
        self, previous: dict[str, Coverage], outcomes: list[PassOutcome], finished: datetime
    ) -> dict[str, Coverage]: ...

    @abstractmethod
    def load(self, session: Session, address: str, coverage: dict[str, Coverage]) -> Any: ...

    async def check(self, address: str, history: Any) -> Check | None:
        return None

    def with_check(self, row: Coverage, check: Check) -> Coverage:
        return replace(
            row,
            verified=check.verified,
            details={"figures": check.figures, "check_notes": list(check.notes)},
        )


# --- account chains ----------------------------------------------------------------------------


class AccountFamily(Family):
    def __init__(self, adapter: AccountAdapter) -> None:
        super().__init__(adapter)
        self._streams = (ACCOUNT, INTERNAL, adapter.token_kind.value)
        # Only chains whose latest blocks can be reorganised away need what was stored there
        # checked again. TRON's feeds show only final data.
        self._reorgs = isinstance(adapter.chain, EvmChain)

    @property
    def streams(self) -> tuple[str, ...]:
        return self._streams

    def plan(
        self,
        coverage: dict[str, Coverage],
        now: datetime,
        *,
        max_age: float,
        refresh: bool,
        rebuild: bool,
    ) -> tuple[list[PassPlan], str]:
        if rebuild:
            return [PassPlan("full")], "rebuild requested"
        if not coverage:
            return [PassPlan("full")], "never read"
        age = seconds_since_synced(coverage, now) or 0.0
        missing = [name for name in self._streams if name not in coverage]
        if not (refresh or missing or age >= max_age):
            return [], f"fresh: read {age:.0f}s ago"

        resume = {name: "" for name in missing}
        for name, row in coverage.items():
            if name in self._streams and row.status == PARTIAL:
                resume[name] = row.resume_cursor or ""
        finished = [name for name in self._streams if name in coverage and name not in resume]

        plans: list[PassPlan] = []
        if resume:
            plans.append(PassPlan("resume", None, resume))
        if finished:
            reached = [
                coverage[name].synced_through_block
                for name in finished
                if coverage[name].synced_through_block is not None
            ]
            start = self.adapter.chain.incremental_start(min(reached)) if reached else None
            plans.append(PassPlan("incremental", start, {name: "" for name in finished}))
        if refresh:
            reason = "refresh requested"
        elif resume:
            reason = "an earlier read was stopped before it finished"
        else:
            reason = f"older than {max_age:.0f}s (read {age:.0f}s ago)"
        return plans, reason

    def _types(self, streams: set[str]) -> set[TransferType]:
        types: set[TransferType] = set()
        if ACCOUNT in streams:
            types |= {TransferType.NATIVE, TransferType.TRC10}
        if INTERNAL in streams:
            types.add(TransferType.INTERNAL)
        if self.adapter.token_kind.value in streams:
            types.add(self.adapter.token_kind)
        return types

    def save(self, session: Session, address: str, outcome: PassOutcome, *, rebuild: bool) -> Saved:
        history: History = outcome.history
        counts = transfer_store.save_transfers(session, history.transfers)
        counts += transfer_store.save_transactions(session, history.transactions)
        # The provider decides which transactions belong to the address, and not always for a reason
        # that is in the transaction (TRON lists one for an internal call), so remember them.
        ids = transaction_ids(
            session, self.chain_id, [t.transaction_hash for t in history.transactions]
        )
        link_address(session, self.chain_id, address, list(ids.values()))
        removed = 0
        plan = outcome.plan
        start = plan.from_block if plan.kind == "incremental" else (0 if rebuild else None)
        done = {n for n, s in history.streams.items() if s.status is StreamStatus.COMPLETE}
        if self._reorgs and start is not None and done and not outcome.failed:
            removed = transfer_store.remove_unseen(
                session,
                self.chain_id,
                address,
                from_block=start,
                transfer_types=self._types(done),
                seen_transfers={(t.transaction_hash, t.event_key) for t in history.transfers},
                transactions_read=ACCOUNT in done,
                seen_transactions={t.transaction_hash for t in history.transactions},
            )
        return Saved(counts, removed)

    def coverage_after(
        self, previous: dict[str, Coverage], outcomes: list[PassOutcome], finished: datetime
    ) -> dict[str, Coverage]:
        rows = dict(previous)
        for outcome in outcomes:
            history: History = outcome.history
            for stream, state in history.streams.items():
                # An update that did not finish moves nothing: the next one starts from the same
                # place. A read of a whole stream records however far it got.
                if outcome.plan.kind == "incremental" and state.status not in (
                    StreamStatus.COMPLETE,
                    StreamStatus.UNAVAILABLE,
                ):
                    continue
                rows[stream] = Coverage(
                    stream=stream,
                    status=state.status.value,
                    synced_at=NEVER if outcome.failed else finished,
                    synced_through_block=state.synced_through,
                    resume_cursor=state.cursor if state.status is StreamStatus.PARTIAL else None,
                    head_block=history.head_block,
                    notes=tuple(history.coverage),
                )
        return rows

    def load(self, session: Session, address: str, coverage: dict[str, Coverage]) -> History:
        transfers, transactions = assemble_history(
            transfer_store.load_transfers(session, self.chain_id, address),
            transfer_store.load_transactions(session, self.chain_id, address),
        )
        states: dict[str, StreamState] = {}
        for name in self._streams:
            row = coverage.get(name)
            if row is None:
                states[name] = StreamState(StreamStatus.PARTIAL, cursor="")
            else:
                status = StreamStatus(row.status)
                states[name] = StreamState(
                    status,
                    synced_through=None
                    if status is StreamStatus.PARTIAL
                    else row.synced_through_block,
                    cursor=(row.resume_cursor or "") if status is StreamStatus.PARTIAL else None,
                )
        complete = all(
            state.status in (StreamStatus.COMPLETE, StreamStatus.UNAVAILABLE)
            for state in states.values()
        )
        reached = [s.synced_through for s in states.values() if s.synced_through is not None]
        rows = [coverage[name] for name in self._streams if name in coverage]
        heads = [row.head_block for row in rows if row.head_block is not None]
        return History(
            chain_id=self.chain_id,
            address=address,
            transfers=transfers,
            transactions=transactions,
            coverage=list(_merged(*(row.notes for row in rows))),
            complete=complete,
            head_block=max(heads, default=None),
            synced_through_block=min(reached) if complete and reached else None,
            resume={
                n: s.cursor or "" for n, s in states.items() if s.status is StreamStatus.PARTIAL
            },
            unavailable=[n for n, s in states.items() if s.status is StreamStatus.UNAVAILABLE],
            streams=states,
        )


# --- chains read as whole transactions, newest first -------------------------------------------


def _state(history: Any) -> tuple[str, int | None, str | None]:
    """(status, the height read up to, where to carry on) of a one-stream, newest-first read."""
    if STREAM in history.resume:
        return PARTIAL, history.head_block, history.resume[STREAM]
    if history.complete:
        return COMPLETE, history.synced_through_block, None
    return TRUNCATED, history.head_block, None


class TransactionFamily(Family, ABC):
    verifies = True

    @property
    def streams(self) -> tuple[str, ...]:
        return (STREAM,)

    def plan(
        self,
        coverage: dict[str, Coverage],
        now: datetime,
        *,
        max_age: float,
        refresh: bool,
        rebuild: bool,
    ) -> tuple[list[PassPlan], str]:
        row = coverage.get(STREAM)
        if rebuild:
            return [PassPlan("full")], "rebuild requested"
        if row is None:
            return [PassPlan("full")], "never read"
        age = seconds_since_synced(coverage, now) or 0.0
        if not (refresh or age >= max_age):
            return [], f"fresh: read {age:.0f}s ago"

        plans: list[PassPlan] = []
        if row.synced_through_block is not None:
            # The newest part: only what is newer than a little before the last sync.
            start = self.adapter.chain.incremental_start(row.synced_through_block)
            plans.append(PassPlan("incremental", start))
        if row.status == PARTIAL and row.resume_cursor is not None:
            # The oldest part, which an earlier read stopped short of.
            plans.append(PassPlan("resume", None, {STREAM: row.resume_cursor}))
        if not plans:
            plans.append(PassPlan("full"))
        if refresh:
            reason = "refresh requested"
        elif row.status == PARTIAL:
            reason = "the older part of the history was never read"
        else:
            reason = f"older than {max_age:.0f}s (read {age:.0f}s ago)"
        return plans, reason

    def coverage_after(
        self, previous: dict[str, Coverage], outcomes: list[PassOutcome], finished: datetime
    ) -> dict[str, Coverage]:
        row = previous.get(STREAM)
        for outcome in outcomes:
            history = outcome.history
            status, through, cursor = _state(history)
            notes = tuple(history.coverage)
            kind = outcome.plan.kind
            if kind == "full" or row is None:
                row = Coverage(
                    stream=STREAM,
                    status=status,
                    synced_at=NEVER if outcome.failed else finished,
                    synced_through_block=through,
                    resume_cursor=cursor,
                    head_block=history.head_block,
                    notes=notes,
                )
            elif kind == "incremental":
                if status != COMPLETE:
                    continue  # it did not reach the stored part: the frontier does not move
                row = replace(
                    row,
                    synced_through_block=through,
                    head_block=history.head_block,
                    synced_at=finished,
                    verified=None,
                    notes=_merged(row.notes, notes),
                    details=None,
                )
            else:  # resume: carrying on towards the oldest
                old = [note for note in row.notes if not note.startswith(STOPPED)]
                row = replace(
                    row,
                    status=status,
                    resume_cursor=cursor,
                    verified=None,
                    notes=_merged(old, notes),
                    details=None,
                )
        return {} if row is None else {STREAM: row}


class BitcoinFamily(TransactionFamily):
    adapter: BitcoinAdapter

    def save(self, session: Session, address: str, outcome: PassOutcome, *, rebuild: bool) -> Saved:
        history: UtxoHistory = outcome.history
        counts = bitcoin_store.save_bitcoin_transactions(
            session, history.transactions, address=address, replace=rebuild
        )
        plan = outcome.plan
        start = plan.from_block if plan.kind == "incremental" else (0 if rebuild else None)
        removed = 0
        if start is not None and history.complete and not outcome.failed:
            # A block read again no longer shows these: a reorganisation dropped them.
            removed = bitcoin_store.remove_unseen_bitcoin(
                session,
                self.chain_id,
                address,
                from_height=start,
                seen_txids={tx.txid for tx in history.transactions},
            )
        return Saved(counts, removed)

    def load(self, session: Session, address: str, coverage: dict[str, Coverage]) -> UtxoHistory:
        row = coverage[STREAM]
        transactions, activity = assemble_utxo(
            bitcoin_store.load_bitcoin_history(session, self.chain_id, address),
            head=row.head_block,
            chain=self.adapter.chain,
            address=address,
            whole=row.status == COMPLETE,
        )
        checked = row.details or {}
        complete = row.status == COMPLETE and row.verified != "mismatch"
        return UtxoHistory(
            chain_id=self.chain_id,
            address=address,
            transactions=transactions,
            activity=activity,
            coverage=list(_merged(row.notes, checked.get("check_notes", ()))),
            complete=complete,
            head_block=row.head_block,
            synced_through_block=row.synced_through_block if complete else None,
            resume={STREAM: row.resume_cursor or ""} if row.status == PARTIAL else {},
            provider_summary=(
                AddressSummary.from_dict(checked["figures"]) if checked.get("figures") else None
            ),
        )

    async def check(self, address: str, history: UtxoHistory) -> Check:
        notes: list[str] = []
        summary, agrees = await verify_utxo(
            self.adapter, address, history.transactions, history.activity, notes
        )
        if summary is None:
            return Check(None, None, tuple(notes))
        return Check("ok" if agrees else "mismatch", summary.to_dict(), tuple(notes))


class SolanaFamily(TransactionFamily):
    adapter: SolanaAdapter

    def save(self, session: Session, address: str, outcome: PassOutcome, *, rebuild: bool) -> Saved:
        history: SolanaHistory = outcome.history
        counts = solana_store.save_solana_transactions(
            session, history.transactions, address=address, replace=rebuild
        )
        # Reads are finalized: nothing that was read can be reorganised away.
        return Saved(counts, 0)

    def load(self, session: Session, address: str, coverage: dict[str, Coverage]) -> SolanaHistory:
        row = coverage[STREAM]
        transactions, activity = assemble_solana(
            solana_store.load_solana_history(session, self.chain_id, address), address
        )
        checked = row.details or {}
        complete = row.status == COMPLETE and row.verified != "mismatch"
        return SolanaHistory(
            chain_id=self.chain_id,
            address=address,
            transactions=transactions,
            activity=activity,
            coverage=list(
                _merged(
                    row.notes, limit_notes(transactions, address), checked.get("check_notes", ())
                )
            ),
            complete=complete,
            head_block=row.head_block,
            synced_through_block=row.synced_through_block if complete else None,
            resume={STREAM: row.resume_cursor or ""} if row.status == PARTIAL else {},
            figures=WalletFigures.from_dict(checked["figures"]) if checked.get("figures") else None,
        )

    async def check(self, address: str, history: SolanaHistory) -> Check:
        notes: list[str] = []
        figures, agrees = await verify_solana(
            self.adapter, address, history.transactions, history.activity, notes
        )
        if figures is None:
            return Check(None, None, tuple(notes))
        return Check("ok" if agrees else "mismatch", figures.to_dict(), tuple(notes))


def family_for(adapter: Any) -> Family:
    if isinstance(adapter, SolanaAdapter):
        return SolanaFamily(adapter)
    if isinstance(adapter, BitcoinAdapter):
        return BitcoinFamily(adapter)
    return AccountFamily(adapter)


__all__ = [
    "Check",
    "Family",
    "PassOutcome",
    "PassPlan",
    "Saved",
    "family_for",
    "seconds_since_synced",
]
