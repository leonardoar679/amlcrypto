"""Building the bounded graph (blueprint section 3.1 and milestone M10).

Breadth-first from the root. An address closer to the root than the depth limit is EXPANDED: its own
history is used to find its counterparties, of which the most relevant few are followed. An address
at the depth limit is shown but not expanded, and says so. The edges are the movements of the
expanded addresses between addresses the graph holds; a movement between two unexpanded addresses
is not known and not shown.

Every limit that cuts something off leaves a `Gap`, and the graph is complete only if it has none.
The depth and the window are the scope of the question, not gaps: the graph states them.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.chains.registry import get_chain
from app.chains.support import ReadableChain
from app.database.connection import Sessions
from app.graph.bridges import BridgeRegistry
from app.graph.exposure import find_exposures
from app.graph.model import (
    EVIDENCE_PER_EDGE,
    Basis,
    Direction,
    Evidence,
    Flow,
    Gap,
    Graph,
    GraphEdge,
    GraphLimits,
    GraphNode,
    NodeStats,
    node_id,
)
from app.graph.patterns import detect_fans
from app.graph.reader import NodeData, NodeReader, stale_note
from app.intel.lookup import lookup_many
from app.intel.model import RISK_CATEGORIES, AddressIntel, Freshness


class Labels(Protocol):
    async def of(self, addresses: Sequence[str]) -> dict[str, AddressIntel]: ...


class StoredLabels:
    """What is known about addresses, from the stored label sources (no request is made)."""

    def __init__(self, sessions: Sessions, chain_id: str, now: datetime) -> None:
        self._sessions, self._chain_id, self._now = sessions, chain_id, now

    async def of(self, addresses: Sequence[str]) -> dict[str, AddressIntel]:
        wanted = sorted(set(addresses))

        def go() -> dict[str, AddressIntel]:
            with self._sessions.read() as session:
                return lookup_many(session, self._chain_id, wanted, now=self._now)

        return await asyncio.to_thread(go)


@dataclass(slots=True)
class _Counterparty:
    address: str
    transfers: int = 0
    last: float = 0.0


def _other(address: str, flow: Flow, direction: Direction) -> str | None:
    """The far end of a movement as far as `direction` follows it, seen from `address`."""
    if flow.source == address and direction is not Direction.IN:
        return flow.target
    if flow.target == address and direction is not Direction.OUT:
        return flow.source
    return None


def _counterparties(
    address: str, flows: Sequence[Flow], direction: Direction
) -> list[_Counterparty]:
    found: dict[str, _Counterparty] = {}
    for flow in flows:
        other = _other(address, flow, direction)
        if other is None:
            continue
        entry = found.setdefault(other, _Counterparty(other))
        entry.transfers += 1
        if flow.timestamp is not None:
            entry.last = max(entry.last, flow.timestamp.timestamp())
    return list(found.values())


def _rank(candidates: list[_Counterparty], intel: dict[str, AddressIntel]) -> list[_Counterparty]:
    """The most relevant first: addresses a source calls risky, then any labelled address, then the
    busiest and most recent. Value is not used: amounts in different assets cannot be compared
    without prices, and a cap must never hide a known-risky counterparty."""

    def key(entry: _Counterparty) -> tuple[int, int, int, float, str]:
        labels = intel[entry.address].labels if entry.address in intel else ()
        risky = any(label.category in RISK_CATEGORIES for label in labels)
        return (0 if risky else 1, 0 if labels else 1, -entry.transfers, -entry.last, entry.address)

    return sorted(candidates, key=key)


def _stats(address: str, flows: Sequence[Flow]) -> NodeStats:
    received = [f for f in flows if f.target == address]
    sent = [f for f in flows if f.source == address]
    times = [f.timestamp for f in flows if f.timestamp is not None]
    return NodeStats(
        transfers_in=len(received),
        transfers_out=len(sent),
        counterparties_in=len({f.source for f in received}),
        counterparties_out=len({f.target for f in sent}),
        first_seen=min(times) if times else None,
        last_seen=max(times) if times else None,
    )


async def build_graph(
    reader: NodeReader,
    labels: Labels,
    chain: ReadableChain,
    root: str,
    limits: GraphLimits,
    *,
    now: datetime,
    fan_threshold: int = 10,
    fan_window_seconds: int = 86_400,
    registry: BridgeRegistry | None = None,
) -> Graph:
    """The graph around `root` (an address in the chain's storage form)."""
    spec = get_chain(chain.chain_id)
    slug = spec.slug
    since = now - timedelta(days=limits.window_days)

    depth_of = {root: 0}
    order = [root]
    data: dict[str, NodeData] = {}
    intel: dict[str, AddressIntel] = {}
    gaps: list[Gap] = []
    not_followed: dict[str, list[str]] = {}  # per node: counterparties past its limit
    held_back: set[str] = set()  # counterparties the node limit kept out

    frontier = [root]
    for depth in range(limits.depth):
        upcoming: list[str] = []
        for address in frontier:
            node = data[address] = await reader.load(address)
            gaps.extend(node.gaps)
            candidates = _counterparties(address, node.flows, limits.direction)
            intel.update(await labels.of([c.address for c in candidates if c.address not in intel]))
            ranked = _rank(candidates, intel)
            not_followed[address] = [c.address for c in ranked[limits.per_node :]]
            for entry in ranked[: limits.per_node]:
                if entry.address in depth_of:
                    continue
                if len(order) >= limits.max_nodes:
                    held_back.add(entry.address)
                    continue
                depth_of[entry.address] = depth + 1
                order.append(entry.address)
                upcoming.append(entry.address)
        frontier = upcoming

    # Which movements the graph shows: those of expanded addresses between addresses it holds, and
    # in the direction it follows. A movement between two expanded addresses is in both histories
    # and is one movement.
    expanded = [a for a in order if a in data]
    shown: dict[tuple[str, str, str, str], Flow] = {}
    for address in expanded:
        for flow in data[address].flows:
            if _other(address, flow, limits.direction) in depth_of:
                shown.setdefault(flow.identity, flow)

    edges, edges_cut = _edges(list(shown.values()), depth_of, slug, limits.max_edges)

    # Nodes.
    missing = [a for a in order if a not in intel]
    intel.update(await labels.of(missing))
    nodes: list[GraphNode] = []
    for address in order:
        check = spec.validator.check(address)
        item = GraphNode(
            id=node_id(slug, address),
            chain_id=chain.chain_id,
            address=address,
            display=check.display or address,
            depth=depth_of[address],
            intel=intel.get(address),
        )
        if registry is not None:
            bridge = registry.contract(slug, address)
            item.bridge = bridge[0].name if bridge else None
        if address in data:
            item.expanded = True
            item.coverage = data[address].coverage
            item.stats = _stats(address, data[address].flows)
            candidates = _counterparties(address, data[address].flows, limits.direction)
            item.counterparties = len(candidates)
            item.counterparties_shown = sum(1 for c in candidates if c.address in depth_of)
        nodes.append(item)

    # What was cut off, and why.
    for address, rest in not_followed.items():
        left_out = [a for a in rest if a not in depth_of]
        if left_out:
            gaps.append(
                Gap(
                    "counterparties_not_followed",
                    f"{slug}:{address}: {len(left_out)} further counterparties were not followed "
                    f"(the {limits.per_node} most relevant are)",
                    node_id(slug, address),
                    len(left_out),
                )
            )
    if held_back:
        gaps.append(
            Gap(
                "node_limit",
                f"{len(held_back)} counterparties were not added: the graph is limited to "
                f"{limits.max_nodes} nodes",
                None,
                len(held_back),
            )
        )
    if edges_cut:
        gaps.append(
            Gap(
                "edge_limit",
                f"{edges_cut} links were not shown: the graph is limited to "
                f"{limits.max_edges} edges",
                None,
                edges_cut,
            )
        )

    warnings = [
        note
        for address in expanded
        if (note := stale_note(data[address].coverage, f"{slug}:{address}", now))
    ]
    sources = intel[root].sources
    warnings += [
        f"label source {source.slug} is {source.freshness.value}: a listing newer than its copy "
        "would not appear"
        for source in sources
        if source.freshness is not Freshness.FRESH
    ]
    ignored: Counter[str] = Counter()
    for address in expanded:
        ignored.update(data[address].ignored)

    graph = Graph(
        chain_id=chain.chain_id,
        root=node_id(slug, root),
        limits=limits,
        since=since,
        generated_at=now,
        nodes=nodes,
        edges=edges,
        gaps=gaps,
        warnings=warnings,
        sources=sources,
        requests_used=reader.requests_used,
        ignored=dict(ignored),
    )
    graph.exposures = find_exposures(graph)
    for address in expanded:
        graph.patterns += detect_fans(
            node_id(slug, address),
            address,
            data[address].flows,
            threshold=fan_threshold,
            window_seconds=fan_window_seconds,
        )
    return graph


def _edges(
    flows: list[Flow], depth_of: dict[str, int], slug: str, limit: int
) -> tuple[list[GraphEdge], int]:
    """One edge per (sender, receiver, asset), nearest the root first, at most `limit`."""
    grouped: dict[tuple[str, str, str], list[Flow]] = {}
    for flow in flows:
        grouped.setdefault((flow.source, flow.target, flow.asset.key), []).append(flow)

    def order(key: tuple[str, str, str]) -> tuple[int, int, int, str, str, str]:
        source, target, asset = key
        near, far = sorted((depth_of[source], depth_of[target]))
        return (near, far, -len(grouped[key]), source, target, asset)

    keys = sorted(grouped, key=order)
    edges = []
    for number, key in enumerate(keys[:limit], 1):
        edges.append(_edge(f"e{number}", slug, grouped[key]))
    return edges, max(0, len(keys) - limit)


def _edge(edge_id: str, slug: str, flows: list[Flow]) -> GraphEdge:
    timed = sorted(
        ((f.timestamp, f) for f in flows if f.timestamp is not None),
        key=lambda item: (item[0], item[1].transaction, item[1].event),
    )
    untimed = sorted(
        (f for f in flows if f.timestamp is None), key=lambda f: (f.transaction, f.event)
    )
    recent = [flow for _, flow in reversed(timed)] + untimed
    first = flows[0]
    return GraphEdge(
        id=edge_id,
        source=node_id(slug, first.source),
        target=node_id(slug, first.target),
        asset=first.asset,
        count=len(flows),
        total=sum(f.amount for f in flows),
        first=timed[0][0] if timed else None,
        last=timed[-1][0] if timed else None,
        basis=Basis.EXACT if all(f.basis is Basis.EXACT for f in flows) else Basis.ALLOCATED,
        evidence=tuple(
            Evidence(f.transaction, f.event, str(f.amount), f.timestamp)
            for f in recent[:EVIDENCE_PER_EDGE]
        ),
        timeline=timed,
        untimed=untimed,
    )
