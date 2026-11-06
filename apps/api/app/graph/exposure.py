"""Exposure paths: how value could have travelled between the root and an address with a risk label.

A path respects time. Value cannot leave an address before it arrived, so each hop's transfer is at
or after the one before it (the same second counts: a chain of calls inside one block is one
moment). Among paths with the fewest hops the one whose last transfer is earliest is reported.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from math import inf

from app.graph.model import Evidence, ExposurePath, Flow, Graph, GraphEdge, GraphNode, Hop
from app.intel.model import RISK_CATEGORIES, Category

MAX_PATH_HOPS = 6
MAX_EXPOSURES = 200

INCOMING = "incoming"  # from the risky address to the root
OUTGOING = "outgoing"  # from the root to the risky address


@dataclass(frozen=True, slots=True)
class _Step:
    previous: str
    edge: GraphEdge
    flow: Flow
    proven: bool


class _Edges:
    """The edges that leave each node, with the times of their movements ready to search."""

    def __init__(self, edges: list[GraphEdge]) -> None:
        self.leaving: dict[str, list[GraphEdge]] = {}
        self._times: dict[str, list[float]] = {}
        for edge in sorted(edges, key=lambda e: (e.source, e.target, e.asset.key)):
            self.leaving.setdefault(edge.source, []).append(edge)
            self._times[edge.id] = [moment.timestamp() for moment, _ in edge.timeline]

    def first_at_or_after(self, edge: GraphEdge, after: float) -> tuple[Flow, float, bool] | None:
        """The earliest movement of the edge no earlier than `after`: it, when it happened, and
        whether that ordering is proven. A movement with no known time may have happened whenever,
        so it is taken at `after`, unproven, only when no timed movement fits."""
        times = self._times[edge.id]
        index = bisect_left(times, after)
        if index < len(times):
            return edge.timeline[index][1], times[index], True
        if edge.untimed:
            return edge.untimed[0], after, False
        return None


def _layers(
    edges: _Edges, start: str, hops: int
) -> tuple[list[dict[str, float]], list[dict[str, _Step]]]:
    """For each hop count k, the earliest moment value from `start` could be at each node using at
    most k hops, and how that was reached."""
    best: list[dict[str, float]] = [{start: -inf}]
    steps: list[dict[str, _Step]] = [{}]
    for _ in range(hops):
        previous = best[-1]
        current = dict(previous)
        improved: dict[str, _Step] = {}
        for node in sorted(previous):
            for edge in edges.leaving.get(node, ()):
                hit = edges.first_at_or_after(edge, previous[node])
                if hit is None or edge.target == start:
                    continue
                flow, moment, proven = hit
                if moment < current.get(edge.target, inf):
                    current[edge.target] = moment
                    improved[edge.target] = _Step(node, edge, flow, proven)
        if not improved:
            break
        best.append(current)
        steps.append(improved)
    return best, steps


def _trace(steps: list[dict[str, _Step]], start: str, target: str, layer: int) -> list[_Step]:
    path: list[_Step] = []
    node = target
    while node != start:
        found = max(k for k in range(1, layer + 1) if node in steps[k])
        step = steps[found][node]
        path.append(step)
        node, layer = step.previous, found - 1
    path.reverse()
    return path


def find_exposures(graph: Graph) -> list[ExposurePath]:
    """Every address in the graph with a risk label that the root can reach, or that can reach the
    root, with the shortest time-respecting path each way. Nothing is scored here."""
    risky = {
        node.id: node
        for node in graph.nodes
        if node.id != graph.root
        and node.intel is not None
        and any(label.category in RISK_CATEGORIES for label in node.intel.labels)
    }
    if not risky or not graph.edges:
        return []
    edges = _Edges(graph.edges)
    hops = min(MAX_PATH_HOPS, max(1, 2 * graph.limits.depth))

    found: list[ExposurePath] = []
    best, steps = _layers(edges, graph.root, hops)
    for target in sorted(risky):
        layer = next((k for k, reach in enumerate(best) if target in reach), None)
        if layer:
            found.append(
                _describe(
                    graph, OUTGOING, risky[target].id, _trace(steps, graph.root, target, layer)
                )
            )
    for source in sorted(risky):
        best, steps = _layers(edges, source, hops)
        layer = next((k for k, reach in enumerate(best) if graph.root in reach), None)
        if layer:
            found.append(
                _describe(graph, INCOMING, source, _trace(steps, source, graph.root, layer))
            )

    found.sort(key=lambda p: (p.length, p.direction, -p.confidence, p.target))
    return found[:MAX_EXPOSURES]


def _describe(graph: Graph, direction: str, risky: str, path: list[_Step]) -> ExposurePath:
    nodes = {node.id: node for node in graph.nodes}
    hops = []
    for step in path:
        flow = step.flow
        hops.append(
            Hop(
                source=step.edge.source,
                target=step.edge.target,
                edge=step.edge.id,
                asset=step.edge.asset,
                edge_total=step.edge.total,
                edge_transfers=step.edge.count,
                chosen=Evidence(flow.transaction, flow.event, str(flow.amount), flow.timestamp),
                ordering_proven=step.proven,
                basis=step.edge.basis,
            )
        )
    moments = [hop.chosen.timestamp for hop in hops]
    timed = [m for m in moments if m is not None]
    gaps = tuple(
        int((later - earlier).total_seconds()) if earlier and later else None
        for earlier, later in zip(moments, moments[1:], strict=False)
    )
    same_asset = len({hop.asset.key for hop in hops}) == 1
    between = [hop.target for hop in hops[:-1]]
    labels = [
        label
        for label in (nodes[risky].intel.labels if nodes[risky].intel else ())
        if label.category in RISK_CATEGORIES
    ]
    return ExposurePath(
        direction=direction,
        target=risky,
        hops=tuple(hops),
        transfers=sum(hop.edge_transfers for hop in hops),
        first_at=min(timed) if timed else None,
        last_at=max(timed) if timed else None,
        gaps_seconds=gaps,
        same_asset=same_asset,
        upper_bound_raw=str(min(hop.edge_total for hop in hops)) if same_asset else None,
        all_exact=all(hop.basis.value == "exact" for hop in hops),
        ordering_proven=all(hop.ordering_proven for hop in hops),
        contains_bridge=any(_is_bridge(nodes[between_id]) for between_id in between),
        intermediaries=tuple(between),
        categories=tuple(sorted({label.category.value for label in labels})),
        confidence=max((label.confidence for label in labels), default=0),
    )


def _is_bridge(node: GraphNode) -> bool:
    return node.bridge is not None or (
        node.intel is not None
        and any(label.category is Category.BRIDGE for label in node.intel.labels)
    )
