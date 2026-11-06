"""Screening graphs built from in-memory movements, for the risk engine's tests."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.graph.builder import build_graph
from app.graph.model import Graph, GraphLimits
from app.risk.engine import screen_graph
from app.risk.model import Report, RuleStatus
from app.risk.rules.registry import effective_rules
from app.risk.scoring import ScoringSettings
from tests.graph_helpers import NOW, ROOT, FakeLabels, FakeReader, addr, at, flow, label
from tests.helpers import run
from tests.rigs import ETHEREUM

__all__ = ["NOW", "ROOT", "FakeLabels", "addr", "at", "flow", "label", "screen", "timedelta"]


def graph_of(
    flows, *, labels=None, depth=1, root=ROOT, states=None, registry=None, **limits
) -> Graph:
    return run(
        build_graph(
            FakeReader(flows, states=states),
            labels or FakeLabels(),
            ETHEREUM,
            root,
            GraphLimits(depth=depth, **limits),
            now=NOW,
            registry=registry,
        )
    )


def screen(
    flows,
    *,
    labels=None,
    depth=1,
    chain="ethereum",
    overrides: dict[str, Any] | None = None,
    settings: ScoringSettings | None = None,
    bridges_known=False,
    states=None,
    registry=None,
    **limits,
) -> Report:
    graph = graph_of(flows, labels=labels, depth=depth, states=states, registry=registry, **limits)
    return screen_graph(
        graph,
        chain=chain,
        rules=effective_rules(overrides),
        settings=settings or ScoringSettings(),
        now=NOW,
        bridges_known=bridges_known,
    )


def rule(report: Report, rule_id: str):
    return next(r for r in report.rules if r.rule_id == rule_id)


def status(report: Report, rule_id: str) -> RuleStatus:
    return rule(report, rule_id).status


def findings(report: Report, rule_id: str | None = None):
    items = report.findings + report.suppressed
    return [i for i in items if rule_id is None or i.finding.rule_id == rule_id]
