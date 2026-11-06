"""Screening a graph end to end, and the rule registry with its overrides."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.graph.reader import judge
from app.intel.model import Category, Freshness
from app.risk.engine import screen_graph
from app.risk.model import FACTOR_CAPS, Coverage, Outcome, RuleOutcome, RuleStatus
from app.risk.rules.registry import (
    BY_ID,
    RULES,
    RuleConfigError,
    effective_rules,
    merge_config,
)
from app.risk.scoring import ScoringSettings
from tests.risk_helpers import (
    NOW,
    ROOT,
    FakeLabels,
    addr,
    at,
    flow,
    graph_of,
    label,
    rule,
    screen,
    status,
)

BAD, A = addr(900), addr(10)


# --- the report ---------------------------------------------------------------------------------


def test_a_report_says_what_was_found_how_sure_and_what_was_not_checked():
    report = screen(
        [flow(BAD, ROOT, 7, at(1), tx="0xhash")], labels=FakeLabels(label(BAD, Category.SANCTIONS))
    )

    body = report.to_dict()
    assert body["subject"] == {
        "type": "address",
        "chain": "ethereum",
        "chain_id": "eip155:1",
        "id": ROOT,
    }
    assert body["risk_score"] == 100 and body["risk_level"] == "CRITICAL"
    assert body["outcome"] == "findings" and body["data_coverage"] == "COMPLETE"
    (finding,) = body["findings"]
    assert finding["rule_id"] == "AML-001" and finding["rule_version"] == "1.0.0"
    assert finding["transaction_hashes"] == ["0xhash"] and finding["sources"] == ["ofac_sdn"]
    assert [r["rule_id"] for r in body["rules"]] == [r.id for r in RULES]
    assert body["analysis"]["transactions_checked"] == 1 and body["analysis"]["maximum_hops"] == 1
    assert body["analysis"]["counterparties_checked"] == 1
    assert body["label_sources"][0]["slug"] == "ofac_sdn"
    assert "not proof of wrongdoing" in body["disclaimer"]
    json.dumps(body)  # nothing in it that cannot be stored


def test_the_same_data_gives_the_same_report():
    moves = [flow(BAD, ROOT, 7, at(1)), flow(ROOT, A, 3, at(2))]
    labels = FakeLabels(label(BAD, Category.SCAM))

    first = screen(moves, labels=labels).to_dict()
    second = screen(list(reversed(moves)), labels=labels).to_dict()

    assert first == second


def test_every_rule_is_accounted_for_in_every_report():
    report = screen([flow(A, ROOT, 1, at(1))])

    assert len(report.rules) == 15
    statuses = {r.status for r in report.rules}
    assert statuses <= {RuleStatus.CLEAR, RuleStatus.NOT_EVALUATED}
    assert report.to_dict()["analysis"]["rules_not_evaluated"] >= 1  # at least AML-013 / 015


def test_a_report_with_no_findings_says_so_and_is_not_a_clearance():
    report = screen([flow(A, ROOT, 1, at(1))])

    body = report.to_dict()
    assert body["outcome"] == "no_findings" and body["findings"] == []
    assert body["risk_score"] == 0 and body["risk_level"] == "LOW"
    assert "does not mean" in body["disclaimer"]


def test_with_too_little_data_the_report_has_no_score_because_zero_would_read_as_safe():
    report = screen([], states={ROOT: judge({}, "n")})

    body = report.to_dict()
    assert report.coverage is Coverage.INSUFFICIENT and report.outcome is Outcome.INSUFFICIENT_DATA
    assert body["risk_score"] is None and body["risk_level"] is None and body["confidence"] is None
    assert body["data_gaps"][0]["code"] == "history_not_read"


def test_data_coverage_is_partial_when_the_graph_leaves_something_out():
    moves = [flow(addr(100 + n), ROOT, 1, at(n)) for n in range(5)]

    complete = screen(moves[:1])
    partial = screen(moves, per_node=2)

    assert complete.coverage is Coverage.COMPLETE and partial.coverage is Coverage.PARTIAL
    assert partial.to_dict()["data_gaps"][0]["code"] == "counterparties_not_followed"


def test_a_stale_label_source_lowers_confidence_and_is_reported():
    moves = [flow(BAD, ROOT, 7, at(1))]
    fresh = screen(moves, labels=FakeLabels(label(BAD, Category.SCAM)))
    stale = screen(moves, labels=FakeLabels(label(BAD, Category.SCAM), freshness=Freshness.STALE))

    assert stale.score.value == fresh.score.value
    assert stale.score.confidence == pytest.approx(fresh.score.confidence * 0.8)
    assert any("ofac_sdn is stale" in w for w in stale.to_dict()["warnings"])


# --- the rules' own state -----------------------------------------------------------------------


def test_a_rule_that_is_switched_off_is_reported_as_not_evaluated():
    report = screen([flow(BAD, ROOT, 7, at(1))],
                    labels=FakeLabels(label(BAD, Category.SANCTIONS)),
                    overrides={"AML-001": (False, None)})  # fmt: skip

    assert status(report, "AML-001") is RuleStatus.NOT_EVALUATED
    assert "switched off" in rule(report, "AML-001").reason
    # the broader rule still sees the same event
    assert [i.finding.rule_id for i in report.findings] == ["AML-007"]


def test_a_rule_that_does_not_apply_to_the_chain_says_so():
    report = screen([flow(A, ROOT, 1, at(1))], chain="ethereum")

    assert status(report, "AML-006") is RuleStatus.NOT_EVALUATED
    assert "applies to bitcoin only, not ethereum" in rule(report, "AML-006").reason


def test_a_rule_that_breaks_does_not_lose_the_assessment():
    def broken(rule_, ctx, cfg):
        raise ZeroDivisionError("oops")

    moves = [flow(BAD, ROOT, 7, at(1))]
    labels = FakeLabels(label(BAD, Category.SCAM))
    normal = screen(moves, labels=labels).to_dict()["analysis"]["rules_not_evaluated"]
    rules = effective_rules()
    rules[3] = replace(rules[3], rule=replace(rules[3].rule, evaluate=broken))  # AML-004
    graph = graph_of(moves, labels=labels)

    report = screen_graph(graph, chain="ethereum", rules=rules, settings=ScoringSettings(), now=NOW)

    assert status(report, "AML-004") is RuleStatus.ERROR
    assert "ZeroDivisionError" in rule(report, "AML-004").reason
    assert report.score.value > 0  # the other rules still counted
    assert report.to_dict()["analysis"]["rules_not_evaluated"] == normal + 1


def test_one_stale_source_among_fresh_ones_lowers_confidence():
    moves = [flow(BAD, ROOT, 7, at(1))]
    graph = graph_of(moves, labels=FakeLabels(label(BAD, Category.SCAM)))
    fresh = graph.sources[0]
    stale = replace(fresh, slug="other", freshness=Freshness.STALE)

    def confidence(sources):
        graph.sources = sources
        report = screen_graph(
            graph, chain="ethereum", rules=effective_rules(), settings=ScoringSettings(), now=NOW
        )
        return report.score.confidence

    assert confidence((fresh,)) == pytest.approx(confidence((fresh, fresh)))
    assert confidence((fresh, stale)) == pytest.approx(confidence((fresh,)) * 0.8)


def test_the_configuration_a_rule_ran_with_is_in_the_report():
    report = screen([flow(A, ROOT, 1, at(1))], overrides={"AML-009": (None, {"threshold": 25})})

    config = rule(report, "AML-009").config
    assert config["threshold"] == 25 and config["window_seconds"] == 86_400


# --- the registry -------------------------------------------------------------------------------


def test_the_registry_holds_the_blueprints_fifteen_rules_with_distinct_priorities():
    assert [r.id for r in RULES] == [f"AML-{n:03d}" for n in range(1, 16)]
    assert len({r.priority for r in RULES}) == 15
    assert all(r.factor in FACTOR_CAPS and r.version and r.description for r in RULES)
    assert BY_ID["AML-006"].supported_chains == ("bitcoin",)


def test_a_registry_entry_has_every_field_the_blueprint_lists():
    entry = effective_rules()[0].to_dict()

    for field in ("rule_id", "rule_name", "description", "enabled", "supported_chains", "config",
                  "severity", "version", "defaults"):  # fmt: skip
        assert field in entry


def test_overrides_change_a_rule_and_leave_the_defaults_alone():
    rules = {e.rule.id: e for e in effective_rules({"AML-004": (False, {"window_seconds": 600})})}

    assert rules["AML-004"].enabled is False and rules["AML-004"].overridden
    assert rules["AML-004"].config["window_seconds"] == 600
    assert rules["AML-004"].config["min_ratio"] == 0.8  # untouched settings keep the default
    assert BY_ID["AML-004"].config["window_seconds"] == 3600
    assert rules["AML-005"].enabled and not rules["AML-005"].overridden


def test_a_setting_a_rule_does_not_have_cannot_be_set():
    with pytest.raises(RuleConfigError, match="no setting 'wndow'"):
        merge_config(BY_ID["AML-004"], {"wndow": 5})
    with pytest.raises(RuleConfigError, match="unknown rule"):
        effective_rules({"AML-999": (True, None)})


@pytest.mark.parametrize(
    "rule_id,change",
    [
        ("AML-004", {"window_seconds": "soon"}),
        ("AML-004", {"window_seconds": -5}),
        ("AML-004", {"min_ratio": True}),
        ("AML-015", {"thresholds": [1000]}),
        ("AML-001", {"override_min_confidence": None}),
    ],
)
def test_a_setting_must_be_the_same_kind_of_value_as_its_default(rule_id, change):
    with pytest.raises(RuleConfigError):
        merge_config(BY_ID[rule_id], change)


def test_numbers_may_be_whole_or_fractional_where_the_default_is_either():
    assert merge_config(BY_ID["AML-004"], {"min_ratio": 1})["min_ratio"] == 1
    assert merge_config(BY_ID["AML-011"], {"factor": 5})["factor"] == 5


def test_a_rule_outcome_helper_builds_what_its_name_says():
    assert RuleOutcome.clear().status is RuleStatus.CLEAR
    assert RuleOutcome.not_evaluated("why").reason == "why"


def test_a_setting_may_be_zero():
    assert merge_config(BY_ID["AML-004"], {"min_transfers": 0})["min_transfers"] == 0
