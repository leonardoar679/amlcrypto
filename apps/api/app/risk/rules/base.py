"""What a rule is, and what it is given to judge.

A rule is deterministic: the same graph, the same movements and the same configuration give the
same outcome, and nothing in a rule reads a clock (the assessment's time is in the context).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.graph.model import Evidence, Flow, Graph, GraphNode
from app.ingestion.render import format_amount
from app.intel.model import RISK_CATEGORIES, Category, LabelMatch
from app.risk.model import (
    MAX_EVIDENCE_TRANSACTIONS,
    Finding,
    RuleOutcome,
    Severity,
)


@dataclass(frozen=True, slots=True)
class RuleContext:
    graph: Graph
    now: datetime
    chain: str  # slug
    bridges_known: bool  # a bridge registry is configured
    flows: tuple[Flow, ...] = field(default=())  # the subject's own movements in the window

    @property
    def root(self) -> GraphNode:
        node = self.graph.node(self.graph.root)
        assert node is not None
        return node

    @property
    def root_read(self) -> bool:
        """Whether the subject's own history is held at all."""
        coverage = self.root.coverage
        return coverage is not None and coverage.state != "not_read"

    def node(self, node_id: str) -> GraphNode | None:
        return self.graph.node(node_id)


Evaluate = Callable[["Rule", RuleContext, Mapping[str, Any]], RuleOutcome]


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    name: str
    description: str
    version: str
    severity: Severity
    factor: str  # which part of the score it feeds (see risk.model.FACTOR_CAPS)
    priority: int  # when two rules find the same event the lower number is the one counted
    supported_chains: tuple[str, ...]  # chain slugs, or ("*",) for all
    config: Mapping[str, Any]  # the defaults, thresholds included
    evaluate: Evaluate

    def applies_to(self, chain: str) -> bool:
        return "*" in self.supported_chains or chain in self.supported_chains

    def finding(
        self,
        *,
        description: str,
        key: tuple[str, ...],
        confidence: float,
        severity: Severity | None = None,
        factor: str | None = None,
        **extra: Any,
    ) -> Finding:
        return Finding(
            rule_id=self.id,
            rule_name=self.name,
            rule_version=self.version,
            severity=severity or self.severity,
            factor=factor or self.factor,
            description=description,
            key=key,
            confidence=max(0.0, min(1.0, confidence)),
            **extra,
        )


# --- helpers shared by the rules ----------------------------------------------------------------


def risk_labels(node: GraphNode | None) -> list[LabelMatch]:
    """The labels in force that are reasons for concern."""
    if node is None or node.intel is None:
        return []
    return [label for label in node.intel.labels if label.category in RISK_CATEGORIES]


def has_category(node: GraphNode | None, *categories: Category) -> bool:
    return (
        node is not None
        and node.intel is not None
        and any(label.category in categories for label in node.intel.labels)
    )


def evidence_of(flow: Flow) -> Evidence:
    return Evidence(flow.transaction, flow.event, str(flow.amount), flow.timestamp)


def transactions_of(flows: Sequence[Flow]) -> tuple[tuple[str, ...], int]:
    """The distinct transactions behind some movements, newest first, and how many there are."""
    latest: dict[str, float] = {}
    for flow in flows:
        moment = flow.timestamp.timestamp() if flow.timestamp else float("-inf")
        latest[flow.transaction] = max(latest.get(flow.transaction, moment), moment)
    ordered = sorted(latest, key=lambda tx: (latest[tx], tx), reverse=True)
    return tuple(ordered[:MAX_EVIDENCE_TRANSACTIONS]), len(ordered)


def newest(flows: Sequence[Flow]) -> datetime | None:
    times = [flow.timestamp for flow in flows if flow.timestamp is not None]
    return max(times) if times else None


def flow_summary(flow: Flow) -> dict[str, Any]:
    return {
        "transaction_hash": flow.transaction,
        "from": flow.source,
        "to": flow.target,
        "asset": flow.asset.symbol or flow.asset.key,
        "amount": format_amount(str(flow.amount), flow.asset.decimals),
        "amount_raw": str(flow.amount),
        "timestamp": flow.timestamp.isoformat() if flow.timestamp else None,
    }


def label_dict(label: LabelMatch) -> dict[str, Any]:
    return {
        "category": label.category.value,
        "entity": label.entity_name,
        "confidence": label.confidence,
        "source": label.source.slug,
        "source_ref": label.source_ref,
        "source_url": label.source_url,
        "listed_as": label.listed_as,
        "last_verified": label.last_verified.isoformat(),
    }
