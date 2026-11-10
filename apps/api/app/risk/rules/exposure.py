"""Rules about whom the subject has dealt with, directly or through others: they read the graph's
exposure paths (see app.graph.exposure for what a path does and does not show)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from app.graph.model import ExposurePath
from app.intel.model import Category, LabelMatch
from app.risk.model import Finding, RuleOutcome, Severity
from app.risk.rules.base import (
    Rule,
    RuleContext,
    flow_summary,
    has_category,
    label_dict,
    risk_labels,
    transactions_of,
)

ILLICIT = frozenset(
    {Category.SCAM, Category.PHISHING, Category.RANSOMWARE, Category.STOLEN_FUNDS, Category.DARKNET}
)
_DOWN = {
    Severity.CRITICAL: Severity.HIGH,
    Severity.HIGH: Severity.MEDIUM,
    Severity.MEDIUM: Severity.LOW,
}


def _labels_in(path: ExposurePath, ctx: RuleContext, *categories: Category) -> list[LabelMatch]:
    return [label for label in risk_labels(ctx.node(path.target)) if label.category in categories]


def _via(path: ExposurePath, ctx: RuleContext) -> list[dict[str, Any]]:
    """The addresses a route passes through that a source says something about, with what it says.
    An exchange in the middle of a route is the context an analyst needs to judge it: value that an
    exchange received may have left it for anyone."""
    found = []
    for node_id in path.intermediaries:
        node = ctx.node(node_id)
        if node is not None and node.intel is not None and node.intel.labels:
            found.append(
                {
                    "address": node_id.split(":", 1)[-1],
                    "labels": [label_dict(label) for label in node.intel.labels],
                }
            )
    return found


def _named(via: list[dict[str, Any]]) -> str:
    names = []
    for item in via:
        first = item["labels"][0]
        names.append(first["entity"] or first["category"].lower())
    return ", ".join(dict.fromkeys(names))


def _path_finding(
    rule: Rule,
    ctx: RuleContext,
    path: ExposurePath,
    labels: list[LabelMatch],
    cfg: Mapping[str, Any],
    *,
    factor: str,
    severity: Severity,
    override_at: int | None = None,
) -> Finding:
    """A finding about a route by which value could have moved between the subject and an address
    a source flags. A route that rests on an allocated Bitcoin amount or on movements with no
    known time is discounted: it is weaker evidence."""
    strongest = max(labels, key=lambda label: label.confidence)
    quality = 1.0 if path.all_exact and path.ordering_proven else float(cfg["inferred_discount"])
    toward = "from" if path.direction == "incoming" else "to"
    who = strongest.entity_name or path.target.split(":", 1)[-1]
    kinds = "/".join(sorted({label.category.value for label in labels}))
    hop_word = "hop" if path.length == 1 else "hops"
    via = _via(path, ctx)
    return rule.finding(
        description=(
            f"{'Received' if path.direction == 'incoming' else 'Sent'} value {toward} {who} "
            f"({kinds}, {strongest.source.slug}), {path.length} {hop_word} away"
            + (f", via {_named(via)}" if via else "")
        ),
        key=("exposure", path.target, path.direction),
        confidence=strongest.confidence / 100 * quality,
        severity=severity,
        factor=factor,
        hops=path.length,
        direction=path.direction,
        target=path.target,
        transactions=tuple(dict.fromkeys(hop.chosen.transaction for hop in path.hops)),
        transactions_total=len({hop.chosen.transaction for hop in path.hops}),
        evidence=(
            {
                "type": "exposure_path",
                "target": path.target,
                "direction": path.direction,
                "hops": path.length,
                "path": [hop.to_dict() for hop in path.hops],
                "ordering_proven": path.ordering_proven,
                "all_links_exact": path.all_exact,
                "contains_bridge": path.contains_bridge,
                "upper_bound_raw": path.upper_bound_raw,
                "labels": [label_dict(label) for label in labels],
                **({"via": via} if via else {}),
            },
        ),
        sources=tuple(sorted({label.source.slug for label in labels})),
        last_verified=max(label.last_verified for label in labels),
        latest_at=path.last_at,
        override=override_at is not None and strongest.confidence >= override_at,
    )


def _verdict(findings: list[Finding], ctx: RuleContext, needs_depth: int = 1) -> RuleOutcome:
    if findings:
        return RuleOutcome.triggered(findings)
    if not ctx.root_read:
        return RuleOutcome.not_evaluated(
            "the subject's own history is not held, so whom it dealt with is unknown"
        )
    if ctx.graph.limits.depth < needs_depth:
        return RuleOutcome.not_evaluated(
            f"the graph is {ctx.graph.limits.depth} hop(s) deep and this needs {needs_depth}"
        )
    return RuleOutcome.clear()


def _paths(
    ctx: RuleContext,
    categories: frozenset[Category] | set[Category],
    *,
    min_hops: int = 1,
    max_hops: int,
    direction: str | None = None,
) -> list[tuple[ExposurePath, list[LabelMatch]]]:
    found = []
    for path in ctx.graph.exposures:
        if not min_hops <= path.length <= max_hops:
            continue
        if direction is not None and path.direction != direction:
            continue
        labels = _labels_in(path, ctx, *categories)
        if labels:
            found.append((path, labels))
    return found


# --- AML-001 / 002 / 003: sanctions and mixers --------------------------------------------------


def direct_sanctions(rule: Rule, ctx: RuleContext, cfg: Mapping[str, Any]) -> RuleOutcome:
    findings = []
    labels = ctx.root.intel.labels if ctx.root.intel else ()
    own = [label for label in labels if label.category is Category.SANCTIONS]
    if own:
        strongest = max(own, key=lambda label: label.confidence)
        findings.append(
            rule.finding(
                description=(
                    f"The address itself is listed by {strongest.source.slug}"
                    + (f" as {strongest.entity_name}" if strongest.entity_name else "")
                ),
                key=("self", ctx.root.id),
                confidence=strongest.confidence / 100,
                severity=Severity.CRITICAL,
                hops=0,
                target=ctx.root.id,
                evidence=({"type": "label", "labels": [label_dict(label) for label in own]},),
                sources=tuple(sorted({label.source.slug for label in own})),
                last_verified=max(label.last_verified for label in own),
                override=strongest.confidence >= int(cfg["override_min_confidence"]),
            )
        )
    for path, labels in _paths(ctx, {Category.SANCTIONS}, max_hops=1):
        findings.append(
            _path_finding(
                rule,
                ctx,
                path,
                labels,
                cfg,
                factor=rule.factor,
                severity=Severity.CRITICAL,
                override_at=int(cfg["override_min_confidence"]),
            )
        )
    return _verdict(findings, ctx)


def indirect_sanctions(rule: Rule, ctx: RuleContext, cfg: Mapping[str, Any]) -> RuleOutcome:
    findings = [
        _path_finding(rule, ctx, path, labels, cfg, factor=rule.factor, severity=rule.severity)
        for path, labels in _paths(
            ctx, {Category.SANCTIONS}, min_hops=2, max_hops=int(cfg["max_hops"])
        )
    ]
    return _verdict(findings, ctx, needs_depth=2)


def mixer(rule: Rule, ctx: RuleContext, cfg: Mapping[str, Any]) -> RuleOutcome:
    findings = [
        _path_finding(rule, ctx, path, labels, cfg, factor=rule.factor, severity=rule.severity)
        for path, labels in _paths(ctx, {Category.MIXER}, max_hops=int(cfg["max_hops"]))
    ]
    return _verdict(findings, ctx)


# --- AML-007 / 008: any risky counterparty ------------------------------------------------------


def _risky(direction: str) -> Callable[[Rule, RuleContext, Mapping[str, Any]], RuleOutcome]:
    def evaluate(rule: Rule, ctx: RuleContext, cfg: Mapping[str, Any]) -> RuleOutcome:
        findings = []
        for path, labels in _paths(
            ctx,
            set(Category) - {Category.UNKNOWN},
            max_hops=int(cfg["max_hops"]),
            direction=direction,
        ):
            kinds = {label.category for label in labels}
            if Category.SANCTIONS in kinds:
                factor, severity = "indirect_sanctions", Severity.CRITICAL
            elif Category.MIXER in kinds:
                factor, severity = "mixer", Severity.HIGH
            else:
                factor, severity = "scam_ransomware", rule.severity
            if path.length > 1:
                severity = _DOWN.get(severity, severity)
            findings.append(
                _path_finding(rule, ctx, path, labels, cfg, factor=factor, severity=severity)
            )
        return _verdict(findings, ctx)

    return evaluate


incoming_risk = _risky("incoming")
outgoing_risk = _risky("outgoing")


# --- AML-013: bridges ---------------------------------------------------------------------------


def bridge_exposure(rule: Rule, ctx: RuleContext, cfg: Mapping[str, Any]) -> RuleOutcome:
    graph = ctx.graph
    bridges = [
        node
        for node in graph.nodes
        if node.id != graph.root and (node.bridge or has_category(node, Category.BRIDGE))
    ]
    findings = []
    for node in bridges:
        edges = [e for e in graph.edges if {e.source, e.target} == {graph.root, node.id}]
        if not edges:
            continue
        flows = [flow for edge in edges for _, flow in edge.timeline] + [
            flow for edge in edges for flow in edge.untimed
        ]
        txs, total = transactions_of(flows)
        sent = any(e.source == graph.root for e in edges)
        received = any(e.target == graph.root for e in edges)
        verb = (
            "Sent to and received from"
            if sent and received
            else ("Sent to" if sent else "Received from")
        )
        name = node.bridge
        if name is None and node.intel is not None:
            name = next(
                (label.entity_name for label in node.intel.labels if label.entity_name), None
            )
        findings.append(
            rule.finding(
                description=f"{verb} a cross-chain bridge ({name or node.address})",
                key=("bridge", node.id),
                confidence=float(cfg["confidence"]),
                hops=1,
                direction=None if sent and received else ("outgoing" if sent else "incoming"),
                target=node.id,
                transactions=txs,
                transactions_total=total,
                evidence=tuple({"type": "transfer", **flow_summary(flow)} for flow in flows[:5]),
                latest_at=max((f.timestamp for f in flows if f.timestamp), default=None),
            )
        )
    for path in graph.exposures:
        if path.contains_bridge and path.length > 1:
            txs = tuple(dict.fromkeys(hop.chosen.transaction for hop in path.hops))
            findings.append(
                rule.finding(
                    description=(
                        f"A route to {path.target} passes a cross-chain bridge "
                        f"({path.length} hops, {path.direction})"
                    ),
                    key=("bridge-path", path.target, path.direction),
                    confidence=float(cfg["confidence"]),
                    hops=path.length,
                    direction=path.direction,
                    target=path.target,
                    transactions=txs,
                    transactions_total=len(txs),
                    evidence=({"type": "exposure_path", "path": [h.to_dict() for h in path.hops]},),
                    latest_at=path.last_at,
                )
            )
    if findings:
        return RuleOutcome.triggered(findings)
    if not bridges and not ctx.bridges_known:
        return RuleOutcome.not_evaluated(
            "no bridge is known: no bridge registry is configured and no address is labelled "
            "as a bridge"
        )
    return _verdict([], ctx)
