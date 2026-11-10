"""Where the graph gets an address's movements, and how much of the history it can vouch for.

`StoredReader` uses only what is already stored: it makes no provider request, so a graph from it
is cheap and says plainly which addresses it has no history for. `AcquiringReader` first brings
each address's stored history up to date through the sync engine (which reuses what is fresh and
reads only what is new), inside one request budget for the whole investigation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, TypeVar

from sqlalchemy.orm import Session

from app.chains.bitcoin.chains import BitcoinChain
from app.chains.models import StreamStatus
from app.chains.support import ReadableChain
from app.database.connection import Sessions
from app.database.repositories.acquisition import Coverage, load_coverage
from app.database.repositories.bitcoin import load_bitcoin_history
from app.database.repositories.transfers import load_transfers
from app.graph.flows import Flows, account_flows, bitcoin_flows
from app.graph.model import Flow, Gap, NodeCoverage
from app.ingestion.families import NEVER
from app.ingestion.raw import RawCollector
from app.ingestion.sync import SyncPolicy, sync_history
from app.providers.base import ProviderError
from app.providers.budget import RequestBudget

logger = logging.getLogger(__name__)
T = TypeVar("T")

STALE_AFTER = timedelta(days=1)  # older than this, the node says how old its history is
_CUT = {StreamStatus.PARTIAL, StreamStatus.TRUNCATED}
# The notes a read leaves about itself that say movements of value are missing from it: rows the
# provider sent that could not be read, events in a shape nobody decodes, amounts that may be short.
# The others are context (a height that could not be had, a token symbol a receipt does not carry).
_LOSS = ("were skipped", "were not read", "shape this reader does not know", "may be short")


@dataclass(slots=True)
class NodeData:
    """One address's movements within the window, and what is known about their completeness."""

    flows: list[Flow]
    coverage: NodeCoverage
    gaps: list[Gap] = field(default_factory=list)
    ignored: dict[str, int] = field(default_factory=dict)


class NodeReader(Protocol):
    async def load(self, address: str) -> NodeData: ...

    @property
    def requests_used(self) -> int: ...


def judge(coverage: dict[str, Coverage], node: str) -> tuple[NodeCoverage, list[Gap]]:
    """The state of a stored history, and what it leaves out.

    A stream whose only read failed is stored as partial with the epoch as its time (see
    `app.ingestion.families.NEVER`): that is a history never read, not one read long ago."""
    if not any(row.synced_at > NEVER for row in coverage.values()):
        return (
            NodeCoverage("not_read"),
            [Gap("history_not_read", f"{node}: no history is stored for this address", node)],
        )
    gaps: list[Gap] = []
    streams = {name: row.status for name, row in sorted(coverage.items())}
    cut = [name for name, status in streams.items() if status in _CUT]
    missing = [name for name, status in streams.items() if status == StreamStatus.UNAVAILABLE]
    if cut:
        gaps.append(
            Gap(
                "history_partial",
                f"{node}: only part of the history was read ({', '.join(cut)}); "
                "movements outside it are not in this graph",
                node,
            )
        )
    if missing:
        gaps.append(
            Gap(
                "stream_unavailable",
                f"{node}: no data source provides {', '.join(missing)}; those movements are "
                "not in this graph",
                node,
            )
        )
    verified = {row.verified for row in coverage.values() if row.verified}
    if "mismatch" in verified:
        gaps.append(
            Gap(
                "history_mismatch",
                f"{node}: the stored history disagrees with the chain's own totals",
                node,
            )
        )
    notes: list[str] = []
    for row in coverage.values():
        notes += [note for note in row.notes if note not in notes]
    lost = [note for note in notes if any(phrase in note for phrase in _LOSS)]
    if lost:
        # Movements that were sent and could not be read are not in the graph: it says so, and so
        # the report built on it is not called complete.
        gaps.append(Gap("rows_not_read", f"{node}: {'; '.join(lost)}", node))
    state = "partial" if cut else "complete"
    return (
        NodeCoverage(
            state=state,
            streams=streams,
            synced_at=min(row.synced_at for row in coverage.values() if row.synced_at > NEVER),
            verified="mismatch" if "mismatch" in verified else ("ok" if verified else None),
            notes=tuple(notes),
        ),
        gaps,
    )


class StoredReader:
    def __init__(
        self,
        sessions: Sessions,
        chain: ReadableChain,
        *,
        since: datetime,
        slug: str,
        native_symbol: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._chain = chain
        self._since = since
        self._slug = slug
        self._native = native_symbol

    @property
    def requests_used(self) -> int:
        return 0

    async def _read(self, work: Callable[[Session], T]) -> T:
        def go() -> T:
            with self._sessions.read() as session:
                return work(session)

        return await asyncio.to_thread(go)

    async def load(self, address: str) -> NodeData:
        chain_id = self._chain.chain_id
        node = f"{self._slug}:{address}"

        def work(session: Session) -> tuple[Flows, dict[str, Coverage]]:
            coverage = load_coverage(session, chain_id, address)
            if isinstance(self._chain, BitcoinChain):
                flows = bitcoin_flows(
                    load_bitcoin_history(session, chain_id, address), address, since=self._since
                )
            else:
                flows = account_flows(
                    load_transfers(session, chain_id, address),
                    address,
                    since=self._since,
                    native_symbol=self._native,
                )
            return flows, coverage

        flows, coverage = await self._read(work)
        state, gaps = judge(coverage, node)
        return NodeData(flows.flows, state, gaps, dict(flows.ignored))


class AcquiringReader(StoredReader):
    """Reads through the sync engine, so what is stored is brought up to date first."""

    def __init__(
        self,
        sessions: Sessions,
        chain: ReadableChain,
        adapter: Any,
        budget: RequestBudget,
        policy: SyncPolicy,
        raw: RawCollector | None,
        *,
        since: datetime,
        slug: str,
        native_symbol: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        super().__init__(sessions, chain, since=since, slug=slug, native_symbol=native_symbol)
        self._adapter = adapter
        self.errors: dict[str, ProviderError] = {}  # by address, for a caller that must act on one
        self._budget = budget
        self._policy = policy
        self._raw = raw
        self._clock = clock

    @property
    def requests_used(self) -> int:
        return self._budget.used

    async def load(self, address: str) -> NodeData:
        node = f"{self._slug}:{address}"
        failure: Gap | None = None
        if self._budget.exhausted:
            failure = Gap(
                "request_budget",
                f"{node}: the investigation's provider request budget "
                f"({self._budget.limit}) was spent before this address was updated",
                node,
            )
        else:
            try:
                await sync_history(
                    self._adapter,
                    address,
                    sessions=self._sessions,
                    policy=self._policy,
                    budget=self._budget,
                    raw=self._raw,
                    clock=self._clock,
                )
            except ProviderError as error:
                logger.warning("graph: reading %s failed: %s", node, type(error).__name__)
                self.errors[address] = error
                failure = Gap(
                    "acquisition_failed",
                    f"{node}: the providers could not be read ({type(error).__name__}); "
                    "what was stored is used",
                    node,
                )
        data = await super().load(address)
        if failure is not None:
            data.gaps.append(failure)
        return data


def stale_note(coverage: NodeCoverage, node: str, now: datetime) -> str | None:
    if coverage.synced_at is None or now - coverage.synced_at <= STALE_AFTER:
        return None
    days = (now - coverage.synced_at).total_seconds() / 86400
    return f"{node}: its history was last read {days:.0f} days ago"
