"""Screening an address: run the rules over its graph and score what they find.

`screen_graph` is a pure function of the graph, the rules' configuration, the scoring settings and
the time it is given, so the same stored data gives the same report.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime

from app.graph.model import Graph
from app.risk.model import (
    Coverage,
    Outcome,
    Report,
    RuleOutcome,
    RuleReport,
    RuleStatus,
)
from app.risk.rules.base import RuleContext
from app.risk.rules.registry import EffectiveRule
from app.risk.scoring import ScoringSettings, score_findings

logger = logging.getLogger(__name__)


def coverage_of(graph: Graph) -> Coverage:
    root = graph.node(graph.root)
    if root is None or root.coverage is None or root.coverage.state == "not_read":
        return Coverage.INSUFFICIENT
    return Coverage.COMPLETE if graph.complete else Coverage.PARTIAL


def run_rule(effective: EffectiveRule, ctx: RuleContext) -> RuleOutcome:
    rule = effective.rule
    if not effective.enabled:
        return RuleOutcome.not_evaluated("switched off in the rule configuration")
    if not rule.applies_to(ctx.chain):
        return RuleOutcome.not_evaluated(
            f"applies to {', '.join(rule.supported_chains)} only, not {ctx.chain}"
        )
    try:
        return rule.evaluate(rule, ctx, effective.config)
    except Exception as error:  # noqa: BLE001 - one broken rule must not lose the assessment
        logger.exception("rule %s failed", rule.id)
        return RuleOutcome(RuleStatus.ERROR, reason=f"{type(error).__name__}: {error}"[:200])


def screen_graph(
    graph: Graph,
    *,
    chain: str,
    rules: Sequence[EffectiveRule],
    settings: ScoringSettings,
    now: datetime,
    bridges_known: bool = False,
) -> Report:
    """The report on the subject at the root of `graph`."""
    root = graph.node(graph.root)
    assert root is not None
    flows = tuple(graph.histories.get(root.address, ()))
    ctx = RuleContext(graph, now, chain, bridges_known, flows)

    findings = []
    reports = []
    for effective in rules:
        outcome = run_rule(effective, ctx)
        findings.extend(outcome.findings)
        reports.append(
            RuleReport(
                effective.rule.id,
                effective.rule.name,
                effective.rule.version,
                outcome.status,
                outcome.reason,
                effective.config,
            )
        )

    coverage = coverage_of(graph)
    score = score_findings(
        findings,
        priorities={e.rule.id: e.rule.priority for e in rules},
        coverage=coverage,
        sources_fresh=all(s.freshness.value == "fresh" for s in graph.sources),
        now=now,
        settings=settings,
    )
    counted = [item for item in score.scored if item.suppressed_by is None]
    if counted:
        outcome = Outcome.FINDINGS
    elif coverage is Coverage.INSUFFICIENT:
        outcome = Outcome.INSUFFICIENT_DATA
    else:
        outcome = Outcome.NO_FINDINGS

    transactions = {flow.transaction for flow in flows}
    return Report(
        subject_type="address",
        chain_id=graph.chain_id,
        chain=chain,
        subject=root.address,
        generated_at=now,
        coverage=coverage,
        outcome=outcome,
        # With nothing found and too little data, a score of 0 would read as "low risk".
        score=None if outcome is Outcome.INSUFFICIENT_DATA else score,
        rules=reports,
        analysis={
            "transactions_checked": len(transactions),
            "counterparties_checked": len(graph.nodes) - 1,
            "expanded_addresses": sum(1 for node in graph.nodes if node.expanded),
            "maximum_hops": graph.limits.depth,
            "window_days": graph.limits.window_days,
            "since": graph.since.isoformat(),
            "graph": graph.summary(),
            "rules_triggered": sum(1 for r in reports if r.status is RuleStatus.TRIGGERED),
            "rules_not_evaluated": sum(
                1 for r in reports if r.status in (RuleStatus.NOT_EVALUATED, RuleStatus.ERROR)
            ),
        },
        gaps=[gap.to_dict() for gap in graph.gaps],
        warnings=list(graph.warnings),
        sources=[source.to_dict() for source in graph.sources],
    )
