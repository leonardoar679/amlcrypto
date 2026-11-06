"""The records of the transaction graph (blueprint sections 3.1, 6.4 and 8.3).

A graph is computed from stored histories each time it is asked for, so none of this is persisted.
Everything in it is a fact the chain states or a count of such facts; what a pattern MEANS (whether
funds that reached an address belong to its owner, whether a fan-in is a crime or an exchange) is
left to whoever reads it, and the graph says which of its parts are inferences.

Amounts are integers in the asset's smallest unit and travel as strings, like everywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.chains.models import iso
from app.ingestion.render import format_amount

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.intel.model import AddressIntel, SourceStatus

EVIDENCE_PER_EDGE = 5  # the most recent movements an edge carries as proof


class Direction(StrEnum):
    BOTH = "both"
    IN = "in"  # follow money that arrived: where did it come from
    OUT = "out"  # follow money that left: where did it go


class Basis(StrEnum):
    """How firmly the chain says that value went from this address to that one."""

    EXACT = "exact"  # the transfer itself, or a transaction with one paying address
    ALLOCATED = "allocated"  # Bitcoin, several paying addresses: the output's value shared out


@dataclass(frozen=True, slots=True)
class AssetRef:
    key: str  # "native", a token contract or mint, or a TRC-10 id
    symbol: str | None
    decimals: int | None

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "symbol": self.symbol, "decimals": self.decimals}


@dataclass(frozen=True, slots=True)
class Flow:
    """One movement of value from one address to another, as the graph sees it."""

    chain_id: str
    source: str
    target: str
    asset: AssetRef
    amount: int  # raw units, never zero
    timestamp: datetime | None
    transaction: str
    event: str  # tells apart the movements of one transaction
    basis: Basis = Basis.EXACT

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.transaction, self.event, self.source, self.target)


@dataclass(frozen=True, slots=True)
class Evidence:
    transaction: str
    event: str
    amount_raw: str
    timestamp: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_hash": self.transaction,
            "event": self.event,
            "amount_raw": self.amount_raw,
            "timestamp": iso(self.timestamp),
        }


@dataclass(frozen=True, slots=True)
class GraphLimits:
    """Engineering safeguards, not AML thresholds (blueprint section 3.1)."""

    depth: int = 1
    max_nodes: int = 500
    max_edges: int = 2000
    per_node: int = 25  # the counterparties of one node that are followed
    window_days: int = 365
    direction: Direction = Direction.BOTH
    history_items: int = 500  # the most transfers read for one address when acquiring
    request_budget: int = 300  # provider requests one investigation may spend

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        depth: int | None = None,
        max_nodes: int | None = None,
        max_edges: int | None = None,
        per_node: int | None = None,
        window_days: int | None = None,
        direction: Direction | str | None = None,
        history_items: int | None = None,
    ) -> GraphLimits:
        """The configured limits, which a request may tighten but never raise (depth and window
        have a ceiling of their own)."""

        def under(asked: int | None, default: int, ceiling: int) -> int:
            return max(1, min(default if asked is None else asked, ceiling))

        return cls(
            depth=under(depth, settings.graph_default_depth, settings.graph_max_depth),
            max_nodes=under(max_nodes, settings.graph_max_nodes, settings.graph_max_nodes),
            max_edges=under(max_edges, settings.graph_max_edges, settings.graph_max_edges),
            per_node=under(
                per_node, settings.graph_max_counterparties, settings.graph_max_counterparties
            ),
            window_days=under(
                window_days, settings.graph_window_days, settings.graph_max_window_days
            ),
            direction=Direction(direction) if direction is not None else Direction.BOTH,
            history_items=under(
                history_items, settings.graph_history_items, settings.history_max_items
            ),
            request_budget=settings.graph_request_budget,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "depth": self.depth,
            "max_nodes": self.max_nodes,
            "max_edges": self.max_edges,
            "counterparties_per_node": self.per_node,
            "window_days": self.window_days,
            "direction": self.direction.value,
            "history_items": self.history_items,
            "request_budget": self.request_budget,
        }


@dataclass(frozen=True, slots=True)
class Gap:
    """Something the graph does not show that it would show if it were exhaustive.

    A graph with a gap is incomplete, and says so; one without is complete only within its scope
    (the depth and window it was asked for), which it also states.
    """

    code: str
    message: str
    node: str | None = None
    count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "node": self.node, "count": self.count}


@dataclass(frozen=True, slots=True)
class NodeCoverage:
    """How much of an expanded node's history is held."""

    state: str  # "complete", "partial" or "not_read"
    streams: dict[str, str] = field(default_factory=dict)
    synced_at: datetime | None = None
    verified: str | None = None  # "ok" or "mismatch" (Bitcoin and Solana)
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "streams": dict(self.streams),
            "synced_at": iso(self.synced_at),
            "verified": self.verified,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class NodeStats:
    """What an expanded node's own history says, within the window."""

    transfers_in: int
    transfers_out: int
    counterparties_in: int
    counterparties_out: int
    first_seen: datetime | None
    last_seen: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "transfers_in": self.transfers_in,
            "transfers_out": self.transfers_out,
            "counterparties_in": self.counterparties_in,
            "counterparties_out": self.counterparties_out,
            "first_seen": iso(self.first_seen),
            "last_seen": iso(self.last_seen),
        }


@dataclass(slots=True)
class GraphNode:
    id: str  # "<chain slug>:<address>"
    chain_id: str
    address: str  # the chain's storage form
    display: str  # how to show it (checksummed on EVM)
    depth: int
    expanded: bool = False  # its own history was used
    coverage: NodeCoverage | None = None
    stats: NodeStats | None = None
    counterparties: int | None = None  # distinct, in the window; None when not expanded
    counterparties_shown: int = 0
    intel: AddressIntel | None = None
    bridge: str | None = None  # the bridge this address is a contract of, from the registry

    def to_dict(self) -> dict[str, Any]:
        labels = self.intel.labels if self.intel else ()
        named = next((label.entity_name for label in labels if label.entity_name), None)
        return {
            "id": self.id,
            "label": named or _short(self.display),
            "type": "address",
            "address": self.display,
            "chain_id": self.chain_id,
            "depth": self.depth,
            "expanded": self.expanded,
            # Scoring is milestone M11; until then a node carries its evidence, not a verdict.
            "risk_level": None,
            "labels": [
                {
                    "category": label.category.value,
                    "entity": label.entity_name,
                    "confidence": label.confidence,
                    "source": label.source.slug,
                    "source_ref": label.source_ref,
                }
                for label in labels
            ],
            "bridge": self.bridge,
            "coverage": self.coverage.to_dict() if self.coverage else None,
            "stats": self.stats.to_dict() if self.stats else None,
            "counterparties": self.counterparties,
            "counterparties_shown": self.counterparties_shown,
        }


@dataclass(slots=True)
class GraphEdge:
    """Everything one address sent another in one asset, within the window."""

    id: str
    source: str  # node ids
    target: str
    asset: AssetRef
    count: int
    total: int
    first: datetime | None
    last: datetime | None
    basis: Basis
    evidence: tuple[Evidence, ...]
    # The timed movements, oldest first, for path analysis; and those without a time.
    timeline: list[tuple[datetime, Flow]] = field(default_factory=list, repr=False)
    untimed: list[Flow] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "asset": self.asset.symbol or self.asset.key,
            "asset_detail": self.asset.to_dict(),
            "amount": format_amount(str(self.total), self.asset.decimals),
            "amount_raw": str(self.total),
            "transfers": self.count,
            "first_timestamp": iso(self.first),
            "timestamp": iso(self.last),
            "basis": self.basis.value,
            "transaction_hash": self.evidence[0].transaction if self.evidence else None,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class Hop:
    """One step of a path: an edge, and the movement of it that the path rests on."""

    source: str
    target: str
    edge: str
    asset: AssetRef
    edge_total: int
    edge_transfers: int
    chosen: Evidence
    ordering_proven: bool
    basis: Basis

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "edge": self.edge,
            "asset": self.asset.to_dict(),
            "edge_total": format_amount(str(self.edge_total), self.asset.decimals),
            "edge_total_raw": str(self.edge_total),
            "edge_transfers": self.edge_transfers,
            "transfer": {
                **self.chosen.to_dict(),
                "amount": format_amount(self.chosen.amount_raw, self.asset.decimals),
            },
            "ordering_proven": self.ordering_proven,
            "basis": self.basis.value,
        }


@dataclass(frozen=True, slots=True)
class ExposurePath:
    """A route by which value could have gone between the root and an address with a risk label.

    Value moves forward in time: each hop's transfer is no earlier than the previous hop's. That
    makes it a path funds COULD have taken; it does not show they did, that the intermediaries
    were one person, or how much of the value (if any) was the same funds.
    """

    direction: str  # "incoming": from the risky address to the root; "outgoing": the other way
    target: str  # the risky node
    hops: tuple[Hop, ...]
    transfers: int  # the transfers of every edge on the path, together
    first_at: datetime | None
    last_at: datetime | None
    gaps_seconds: tuple[int | None, ...]  # time between each hop's transfer and the next
    same_asset: bool
    upper_bound_raw: str | None  # the smallest hop total, when every hop is one asset
    all_exact: bool
    ordering_proven: bool
    contains_bridge: bool
    intermediaries: tuple[str, ...]  # node ids between the two ends
    categories: tuple[str, ...]  # of the risk labels on the risky end
    confidence: int  # the strongest of those labels, in percent

    @property
    def length(self) -> int:
        return len(self.hops)

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "target": self.target,
            "hops": self.length,
            "path": [hop.to_dict() for hop in self.hops],
            "transfers": self.transfers,
            "first_timestamp": iso(self.first_at),
            "last_timestamp": iso(self.last_at),
            "seconds_between_hops": list(self.gaps_seconds),
            "same_asset": self.same_asset,
            "upper_bound_raw": self.upper_bound_raw,
            "all_links_exact": self.all_exact,
            "ordering_proven": self.ordering_proven,
            "contains_bridge": self.contains_bridge,
            "intermediaries": list(self.intermediaries),
            "risk_categories": list(self.categories),
            "label_confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class FanPattern:
    """Many counterparties sending to one address (fan-in) or one address sending to many
    (fan-out) within a short time. An indicator: exchanges, payment processors and airdrops do
    both all day."""

    kind: str  # "fan_in" or "fan_out"
    node: str
    window_seconds: int
    start: datetime
    end: datetime
    counterparties: int
    transfers: int
    assets: tuple[str, ...]
    sample: tuple[str, ...]  # some of the counterparties
    evidence: tuple[Evidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "node": self.node,
            "window_seconds": self.window_seconds,
            "start": iso(self.start),
            "end": iso(self.end),
            "counterparties": self.counterparties,
            "transfers": self.transfers,
            "assets": list(self.assets),
            "sample_counterparties": list(self.sample),
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(slots=True)
class Graph:
    chain_id: str
    root: str  # node id
    limits: GraphLimits
    since: datetime
    generated_at: datetime
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    gaps: list[Gap] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    exposures: list[ExposurePath] = field(default_factory=list)
    patterns: list[FanPattern] = field(default_factory=list)
    sources: tuple[SourceStatus, ...] = ()
    requests_used: int = 0
    ignored: dict[str, int] = field(default_factory=dict)  # movements left out, and why
    # Each expanded address's own movements in the window, for the rules that judge behaviour.
    histories: dict[str, list[Flow]] = field(default_factory=dict, repr=False)

    @property
    def complete(self) -> bool:
        return not self.gaps

    def node(self, node_id: str) -> GraphNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def summary(self) -> dict[str, Any]:
        """Counts only: small enough for a job result."""
        return {
            "chain_id": self.chain_id,
            "root": self.root,
            "complete": self.complete,
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "expanded": sum(1 for n in self.nodes if n.expanded),
            "gaps": [gap.to_dict() for gap in self.gaps],
            "exposures": len(self.exposures),
            "patterns": len(self.patterns),
            "requests_used": self.requests_used,
        }

    def to_dict(self) -> dict[str, Any]:
        """Shaped for Cytoscape (blueprint section 8.3), with what that shape cannot say."""
        return {
            "success": True,
            "chain_id": self.chain_id,
            "root": self.root,
            "complete": self.complete,
            "scope": {
                **self.limits.to_dict(),
                "since": iso(self.since),
                "until": iso(self.generated_at),
            },
            "gaps": [gap.to_dict() for gap in self.gaps],
            "warnings": list(self.warnings),
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "exposures": [path.to_dict() for path in self.exposures],
            "patterns": [pattern.to_dict() for pattern in self.patterns],
            "ignored": dict(self.ignored),
            "sources": [source.to_dict() for source in self.sources],
            "requests_used": self.requests_used,
            "generated_at": iso(self.generated_at),
            "notes": [
                "A link between addresses shows that value moved, not that they share an owner.",
                "Exposure paths are routes funds could have taken, not proof that they did.",
            ],
        }


def node_id(slug: str, address: str) -> str:
    return f"{slug}:{address}"


def _short(value: str, head: int = 8, tail: int = 6) -> str:
    return value if len(value) <= head + tail + 2 else f"{value[:head]}..{value[-tail:]}"
