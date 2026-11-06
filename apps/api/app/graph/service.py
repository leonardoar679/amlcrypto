"""Building a graph for a caller: from what is stored, or after bringing it up to date.

The command line, the API and the job share this. Reading from storage never contacts a provider;
acquiring does, inside one request budget for the whole investigation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.chains.registry import get_chain
from app.chains.support import SUPPORTED, ReadableChain, build_adapter, find_chain, keys_needed
from app.core.config import Settings
from app.database.connection import Sessions
from app.graph.bridges import BridgeRegistry
from app.graph.builder import StoredLabels, build_graph
from app.graph.model import Graph, GraphLimits
from app.graph.reader import AcquiringReader, StoredReader
from app.ingestion.raw import RawCollector
from app.ingestion.sync import SyncPolicy
from app.providers.budget import RequestBudget
from app.providers.service import ProviderService


class GraphRequestError(ValueError):
    """The request cannot be a graph: a chain or an address that does not exist."""

    def __init__(self, message: str, *, chain_unknown: bool = False) -> None:
        super().__init__(message)
        self.chain_unknown = chain_unknown


def load_registry(settings: Settings) -> BridgeRegistry:
    """The configured bridge registry; empty when none is configured."""
    if settings.bridge_registry_file is None:
        return BridgeRegistry()
    return BridgeRegistry.from_file(settings.bridge_registry_file)


def resolve(chain_name: str, address: str) -> tuple[ReadableChain, str]:
    """The chain (by slug or CAIP-2 id) and the address in its storage form."""
    chain = find_chain(chain_name)
    if chain is None:
        raise GraphRequestError(
            f"{chain_name!r} is not a chain this platform reads ({', '.join(SUPPORTED)})",
            chain_unknown=True,
        )
    check = get_chain(chain.chain_id).validator.check(address)
    if not check.valid or check.normalized is None:
        raise GraphRequestError(f"not a valid {chain.slug} address: {check.message}")
    return chain, check.normalized


async def graph_from_storage(
    sessions: Sessions,
    settings: Settings,
    chain: ReadableChain,
    address: str,
    limits: GraphLimits,
    *,
    registry: BridgeRegistry | None = None,
    now: datetime | None = None,
) -> Graph:
    """The graph the stored histories allow. An address whose history is not stored is shown as
    such, and the graph says it is incomplete."""
    now = now or datetime.now(UTC)
    spec = get_chain(chain.chain_id)
    reader = StoredReader(
        sessions,
        chain,
        since=_since(now, limits),
        slug=spec.slug,
        native_symbol=spec.native.symbol,
    )
    return await _build(sessions, settings, chain, address, limits, reader, registry, now)


async def graph_after_update(
    sessions: Sessions,
    settings: Settings,
    providers: ProviderService,
    chain: ReadableChain,
    address: str,
    limits: GraphLimits,
    *,
    registry: BridgeRegistry | None = None,
    refresh: bool = False,
    now: datetime | None = None,
) -> Graph:
    """The graph after each expanded address's history was brought up to date (recent data is
    reused, so a repeat costs nothing) within `limits.request_budget` provider requests.
    `refresh` updates every history now, which also carries on any that a limit stopped. A
    provider error on the root, when nothing of it is stored, is raised (an error on any other
    address is a gap in the graph)."""
    now = now or datetime.now(UTC)
    budget = RequestBudget(limits.request_budget)
    raw = RawCollector.from_settings(settings)
    adapter = build_adapter(
        chain,
        providers,
        etherscan_paid_plan=settings.etherscan_paid_plan,
        budget=budget,
        raw=raw,
    )
    if not adapter.source_names:
        raise GraphRequestError(
            f"no data provider is configured for {chain.slug}: set {keys_needed(chain)}"
        )
    spec = get_chain(chain.chain_id)
    reader = AcquiringReader(
        sessions,
        chain,
        adapter,
        budget,
        SyncPolicy.from_settings(settings, max_items=limits.history_items, refresh=refresh),
        raw,
        since=_since(now, limits),
        slug=spec.slug,
        native_symbol=spec.native.symbol,
    )
    graph = await _build(sessions, settings, chain, address, limits, reader, registry, now)
    root = graph.nodes[0]
    if address in reader.errors and root.coverage and root.coverage.state == "not_read":
        # Nothing of the address could be read at all, so there is no graph worth returning, and
        # the error says whether trying again may help.
        raise reader.errors[address]
    return graph


def _since(now: datetime, limits: GraphLimits) -> datetime:
    return now - timedelta(days=limits.window_days)


async def _build(
    sessions: Sessions,
    settings: Settings,
    chain: ReadableChain,
    address: str,
    limits: GraphLimits,
    reader: StoredReader,
    registry: BridgeRegistry | None,
    now: datetime,
) -> Graph:
    return await build_graph(
        reader,
        StoredLabels(sessions, chain.chain_id, now),
        chain,
        address,
        limits,
        now=now,
        fan_threshold=settings.graph_fan_threshold,
        fan_window_seconds=settings.graph_fan_window_seconds,
        registry=registry,
    )
