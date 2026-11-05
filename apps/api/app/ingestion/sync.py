"""Screening an address without downloading its history again.

`sync_history` is what the command line and the job queue call to read an address. It looks at what
is already stored for the address and decides how little needs to be asked of a provider:

* never read: read it all (within the limits), store it, and remember how far it got;
* read recently (the freshness window): ask nothing, serve what is stored;
* read before, but not recently: read only what is new, starting a little before where the last
  read ended so that late-indexed and reorganised records are caught, and finish any read that an
  earlier limit stopped;
* either way, hand back the history as a fresh read would have returned it, rebuilt from storage.

Bitcoin and Solana histories are also checked against the chain's own figures whenever they are
complete, after the new part has been added to the old, so that a stored history is never trusted
on the strength of a check made when it was shorter.

Nothing here holds a database transaction across a network request: a read finishes first, and
what it returned is then stored in one short transaction.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from sqlalchemy.orm import Session

from app.chains.cursors import InvalidCursor
from app.core.config import Settings
from app.database.connection import Sessions
from app.database.repositories.acquisition import (
    COMPLETE,
    Coverage,
    RunRecord,
    add_requests_to_run,
    load_coverage,
    record_run,
    save_coverage,
    save_raw,
)
from app.database.repositories.rows import SaveCounts
from app.database.retry import commit_with_retry
from app.ingestion.families import (
    Family,
    PassOutcome,
    PassPlan,
    Saved,
    family_for,
    seconds_since_synced,
)
from app.ingestion.partial import partial_of
from app.ingestion.raw import RawCollector
from app.ingestion.read import read_history
from app.providers.base import ProviderBadResponse, ProviderError
from app.providers.budget import RequestBudget

logger = logging.getLogger(__name__)
T = TypeVar("T")

MAX_RUN_NOTES = 20


@dataclass(frozen=True, slots=True)
class SyncPolicy:
    # A stored history read this recently is served as it is. Zero: always ask.
    max_age: timedelta = timedelta(seconds=300)
    max_items: int = 5000  # the most transfers (transactions) one read collects
    refresh: bool = False  # ignore the freshness window: update now
    rebuild: bool = False  # read everything again, and drop what is no longer there

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        max_items: int | None = None,
        refresh: bool = False,
        rebuild: bool = False,
    ) -> SyncPolicy:
        return cls(
            max_age=timedelta(seconds=settings.history_fresh_seconds),
            max_items=min(max_items or settings.history_max_items, settings.history_max_items),
            refresh=refresh,
            rebuild=rebuild,
        )


@dataclass(frozen=True, slots=True)
class PassReport:
    kind: str  # full, resume or incremental
    from_block: int | None
    streams: tuple[str, ...]
    complete: bool
    failed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "from_block": self.from_block,
            "streams": list(self.streams),
            "complete": self.complete,
            "failed": self.failed,
        }


@dataclass(slots=True)
class SyncReport:
    """What the sync did, to show next to the history."""

    mode: str  # "cached" (no request), "full", "update" or "rebuild"
    reason: str
    age_seconds: float | None  # how old the stored data was when the sync began
    requests_used: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    verified: str | None = None  # "ok" or "mismatch" once checked against the chain
    records: int = 0  # in the history returned
    passes: list[PassReport] = field(default_factory=list)

    def describe(self) -> str:
        """One line for a person: what was reused, what was read, what it cost."""
        if self.mode == "cached":
            line = (
                f"served from storage: read {_ago(self.age_seconds)} ago, no provider request made"
            )
        else:
            done = ", ".join(p.kind for p in self.passes) or "nothing"
            line = (
                f"{self.reason}; read: {done}; {self.requests_used} provider requests; "
                f"{self.new} new, {self.updated} updated, {self.unchanged} already stored"
            )
            if self.removed:
                line += f", {self.removed} dropped because the chain no longer shows them"
        if self.verified == "ok":
            line += "; checked against the chain: agrees"
        elif self.verified == "mismatch":
            line += "; checked against the chain: DISAGREES"
        return f"stored data: {line}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reason": self.reason,
            "age_seconds": self.age_seconds,
            "requests_used": self.requests_used,
            "new": self.new,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "removed": self.removed,
            "verified": self.verified,
            "records": self.records,
            "passes": [item.to_dict() for item in self.passes],
        }


@dataclass(slots=True)
class SyncResult:
    history: Any  # History, UtxoHistory or SolanaHistory
    report: SyncReport


async def _read(sessions: Sessions, work: Callable[[Session], T]) -> T:
    def go() -> T:
        with sessions.read() as session:
            return work(session)

    return await asyncio.to_thread(go)


async def _write(sessions: Sessions, work: Callable[[Session], T]) -> T:
    def go() -> T:
        with sessions.write() as session:
            return commit_with_retry(session, lambda: work(session))

    return await asyncio.to_thread(go)


def _ago(seconds: float | None) -> str:
    if seconds is None:
        return "some time"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds / size:.0f}{unit}"
    return f"{seconds:.0f}s"


def _records(history: Any) -> int:
    if hasattr(history, "transfers"):
        return len(history.transfers) + len(history.transactions)
    return len(history.transactions)


async def sync_history(
    adapter: Any,
    address: str,
    *,
    sessions: Sessions,
    policy: SyncPolicy | None = None,
    budget: RequestBudget | None = None,
    raw: RawCollector | None = None,
    job_id: int | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> SyncResult:
    """The address's history, from storage where it is current and from the providers where not.

    `budget` is the one the adapter was built with, to say how many requests this cost. `raw`, if
    given, is the collector the adapter's sources were built with; what it collected is stored with
    the records. A provider error ends the sync after what was read has been stored, and is
    raised unchanged.
    """
    policy = policy or SyncPolicy()
    family = family_for(adapter)
    chain_id = adapter.chain_id
    started = clock()

    coverage = await _read(sessions, lambda s: load_coverage(s, chain_id, address))
    plans, reason = family.plan(
        coverage,
        started,
        max_age=policy.max_age.total_seconds(),
        refresh=policy.refresh,
        rebuild=policy.rebuild,
    )
    age = seconds_since_synced(coverage, started)

    if not plans:
        history = await _read(sessions, lambda s: family.load(s, address, coverage))
        stored = coverage.get(family.streams[0]) if family.verifies else None
        report = SyncReport(
            "cached",
            reason,
            age,
            verified=None if stored is None else stored.verified,
            records=_records(history),
        )
        return SyncResult(history, report)

    used_before = budget.used if budget is not None else 0
    outcomes: list[PassOutcome] = []
    failure: ProviderError | None = None
    for plan in plans:
        if outcomes and budget is not None and budget.exhausted:
            break  # nothing is left to ask with; the next sync carries on
        try:
            outcomes.append(await _read_pass(adapter, address, plan, policy))
        except ProviderError as error:
            partial = partial_of(error)
            if partial is not None:
                outcomes.append(PassOutcome(plan, partial, failed=True))
            failure = error
            break

    finished = clock()
    requests = (budget.used - used_before) if budget is not None else 0
    mode = "rebuild" if policy.rebuild else ("full" if plans[0].kind == "full" else "update")

    # Drained once, before the write, which may be run again if the database picks it as a
    # deadlock victim; if it fails for good the answers are put back for the next attempt.
    kept = raw.drain() if raw is not None else []

    def persist(session: Session) -> tuple[Saved, dict[str, Coverage], int]:
        saved = Saved(SaveCounts(), 0)
        for outcome in outcomes:
            one = family.save(session, address, outcome, rebuild=policy.rebuild)
            saved = Saved(saved.counts + one.counts, saved.removed + one.removed)
        rows = family.coverage_after(coverage, outcomes, finished)
        save_coverage(
            session,
            chain_id,
            address,
            [row for name, row in rows.items() if coverage.get(name) != row],
        )
        save_raw(session, kept)
        run_id = record_run(
            session,
            RunRecord(
                chain_id=chain_id,
                address=address,
                mode=mode,
                outcome=_outcome(outcomes, failure),
                started_at=started,
                finished_at=finished,
                requests_used=requests,
                from_block=plans[0].from_block,
                new=saved.counts.new,
                updated=saved.counts.updated,
                unchanged=saved.counts.unchanged,
                removed=saved.removed,
                sources=tuple(adapter.source_names),
                error=None if failure is None else str(failure),
                notes=_notes(outcomes),
                job_id=job_id,
            ),
        )
        return saved, rows, run_id

    try:
        saved, rows, run_id = await _write(sessions, persist)
    except BaseException:
        if raw is not None:
            raw.restore(kept)
        raise
    if failure is not None:
        raise failure

    verified: str | None = None
    before_check = budget.used if budget is not None else 0
    if family.verifies:
        row = rows.get(family.streams[0])
        if row is not None and row.status == COMPLETE and row.verified is None:
            merged = await _read(sessions, lambda s: family.load(s, address, rows))
            check = await family.check(address, merged)
            if check is not None:
                row = family.with_check(row, check)
                rows = {**rows, row.stream: row}
                await _write(sessions, lambda s: save_coverage(s, chain_id, address, [row]))
        verified = None if row is None else row.verified
        checking = (budget.used - before_check) if budget is not None else 0
        if checking:
            await _write(sessions, lambda s: add_requests_to_run(s, run_id, checking))

    history = await _read(sessions, lambda s: family.load(s, address, rows))
    _say_what_is_unfinished(history, outcomes)
    report = SyncReport(
        mode=mode,
        reason=reason,
        age_seconds=age,
        requests_used=(budget.used - used_before) if budget is not None else 0,
        new=saved.counts.new,
        updated=saved.counts.updated,
        unchanged=saved.counts.unchanged,
        removed=saved.removed,
        verified=verified,
        records=_records(history),
        passes=[_pass_report(outcome, family) for outcome in outcomes],
    )
    logger.info(
        "synced %s %s: %s (%s), %d requests, %d new records",
        chain_id,
        address,
        report.mode,
        reason,
        report.requests_used,
        saved.counts.new,
    )
    return SyncResult(history, report)


async def _read_pass(adapter: Any, address: str, plan: PassPlan, policy: SyncPolicy) -> PassOutcome:
    async def go(resume: dict[str, str] | None) -> Any:
        return await read_history(
            adapter,
            address,
            from_block=plan.from_block,
            max_items=policy.max_items,
            resume=resume,
            verify=False,  # the merged history is checked once, after the new part is added
        )

    try:
        return PassOutcome(plan, await go(plan.resume))
    except (InvalidCursor, ProviderBadResponse) as error:
        if not plan.resume or not any(plan.resume.values()):
            raise
        # The saved resume point cannot be used (its source is no longer configured, or the chain
        # moved under it): read those streams again from their start. What is stored is not lost.
        logger.warning("a saved resume point was unusable (%s); reading from the start", error)
        cleared = replace(plan, resume={name: "" for name in plan.resume})
        history = await go(cleared.resume)
        history.coverage.append(
            "a saved resume point could not be used; those streams were read again from their start"
        )
        return PassOutcome(cleared, history)


def _say_what_is_unfinished(history: Any, outcomes: list[PassOutcome]) -> None:
    """A read that a limit stopped before it was done has not brought the stored history up to
    date, whatever the stored coverage says: the result must not look complete. (An update that is
    cut short leaves the coverage where it was, so that the next one starts from the same place.)"""
    unfinished = [outcome for outcome in outcomes if not outcome.history.complete]
    if not unfinished:
        return
    history.complete = False
    history.synced_through_block = None
    for outcome in unfinished:
        for note in outcome.history.coverage:
            if note not in history.coverage:
                history.coverage.append(note)


def _outcome(outcomes: list[PassOutcome], failure: ProviderError | None) -> str:
    if failure is not None:
        return "failed"
    return "complete" if outcomes and all(o.history.complete for o in outcomes) else "partial"


def _notes(outcomes: list[PassOutcome]) -> tuple[str, ...]:
    notes: list[str] = []
    for outcome in outcomes:
        for note in outcome.history.coverage:
            if note not in notes:
                notes.append(note)
    return tuple(notes[:MAX_RUN_NOTES])


def _pass_report(outcome: PassOutcome, family: Family) -> PassReport:
    plan = outcome.plan
    streams = tuple(plan.resume) if plan.resume is not None else family.streams
    return PassReport(plan.kind, plan.from_block, streams, outcome.history.complete, outcome.failed)
