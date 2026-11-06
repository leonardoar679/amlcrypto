"""The rules about whom an address dealt with: sanctions, mixers, risky parties, bridges."""

from __future__ import annotations

from app.graph.bridges import BridgeRegistry
from app.graph.model import Basis
from app.graph.reader import judge
from app.intel.model import Category
from app.risk.model import Level, RuleStatus, Severity
from tests.risk_helpers import (
    ROOT,
    FakeLabels,
    addr,
    at,
    findings,
    flow,
    label,
    rule,
    screen,
    status,
)

BAD = addr(900)
OTHER = addr(901)
A, B = addr(10), addr(11)


def sanctioned(address=BAD, **kw):
    return FakeLabels(label(address, Category.SANCTIONS, **kw))


# --- AML-001: direct sanctions -----------------------------------------------------------------


def test_a_sanctioned_address_that_paid_the_subject_is_a_critical_direct_exposure():
    report = screen([flow(BAD, ROOT, 7, at(1))], labels=sanctioned())

    (item,) = findings(report, "AML-001")
    finding = item.finding
    assert finding.severity is Severity.CRITICAL and finding.hops == 1
    assert finding.direction == "incoming" and finding.target == f"ethereum:{BAD}"
    assert finding.override and finding.sources == ("ofac_sdn",)
    assert status(report, "AML-001") is RuleStatus.TRIGGERED
    assert report.score.value == 100 and report.score.level is Level.CRITICAL
    assert report.score.override == "AML-001"


def test_it_does_not_matter_which_way_the_money_went_or_how_little():
    report = screen([flow(ROOT, BAD, 1, at(1))], labels=sanctioned())

    (item,) = findings(report, "AML-001")
    assert item.finding.direction == "outgoing" and report.score.value == 100


def test_the_evidence_names_the_transaction_the_label_and_where_it_came_from():
    report = screen([flow(BAD, ROOT, 7, at(1), tx="0xproof")], labels=sanctioned(entity="LAZARUS"))

    finding = findings(report, "AML-001")[0].finding
    assert finding.transactions == ("0xproof",) and finding.transactions_total == 1
    evidence = finding.evidence[0]
    assert evidence["type"] == "exposure_path" and evidence["ordering_proven"] is True
    assert evidence["labels"][0]["entity"] == "LAZARUS"
    assert (
        evidence["labels"][0]["source"] == "ofac_sdn" and evidence["labels"][0]["confidence"] == 100
    )
    assert finding.last_verified is not None and "LAZARUS" in finding.description


def test_an_address_that_is_itself_listed_is_a_direct_finding_even_with_no_history():
    from tests.graph_helpers import NOW  # noqa: F401  (the label's own dates)

    report = screen(
        [],
        labels=sanctioned(ROOT),
        states={ROOT: judge({}, "ethereum:root")},
    )

    (item,) = findings(report, "AML-001")
    assert item.finding.hops == 0 and item.finding.key == ("self", f"ethereum:{ROOT}")
    assert report.score.value == 100
    assert report.coverage.value == "INSUFFICIENT"  # it says it knows little else


def test_a_weak_claim_of_sanctions_is_a_finding_but_not_a_policy_override():
    report = screen([flow(BAD, ROOT, 7, at(1))], labels=sanctioned(confidence=50))

    (item,) = findings(report, "AML-001")
    assert not item.finding.override and report.score.override is None
    assert item.finding.confidence == 0.5
    assert 0 < report.score.value < 100


def test_a_route_resting_on_an_unproven_order_is_weaker_evidence():
    proven = screen([flow(BAD, ROOT, 7, at(1))], labels=sanctioned())
    unproven = screen([flow(BAD, ROOT, 7, timed=False)], labels=sanctioned())

    assert findings(proven, "AML-001")[0].finding.confidence == 1.0
    assert findings(unproven, "AML-001")[0].finding.confidence == 0.85


def test_a_route_resting_on_an_allocated_amount_is_weaker_evidence_too():
    report = screen([flow(BAD, ROOT, 7, at(1), basis=Basis.ALLOCATED)], labels=sanctioned())

    assert findings(report, "AML-001")[0].finding.confidence == 0.85


def test_nobody_listed_means_no_finding_but_not_a_clearance():
    report = screen([flow(A, ROOT, 7, at(1))], labels=FakeLabels())

    assert status(report, "AML-001") is RuleStatus.CLEAR
    assert report.outcome.value == "no_findings" and report.score.value == 0
    assert "does not mean" in report.to_dict()["disclaimer"]


def test_without_the_subjects_history_the_counterparties_cannot_be_judged():
    report = screen([], states={ROOT: judge({}, "n")})

    assert status(report, "AML-001") is RuleStatus.NOT_EVALUATED
    assert "history is not held" in rule(report, "AML-001").reason


# --- AML-002: indirect sanctions ---------------------------------------------------------------


def two_hops():
    return [flow(BAD, A, 5, at(1)), flow(A, ROOT, 4, at(3)), flow(ROOT, B, 1, at(9))]


def test_a_sanctioned_address_two_hops_away_is_an_indirect_exposure_not_a_direct_one():
    report = screen(two_hops(), labels=sanctioned(), depth=2)

    assert status(report, "AML-001") is RuleStatus.CLEAR
    (item,) = findings(report, "AML-002")
    assert item.finding.hops == 2 and item.finding.severity is Severity.HIGH
    assert not item.finding.override
    # 40 (the factor's cap) x 0.7 (two hops) x 1.0 (the label's confidence)
    assert round(item.contribution, 1) == 28.0 and report.score.value == 28
    assert report.score.level is Level.MODERATE


def test_indirect_exposure_cannot_be_judged_from_a_one_hop_graph():
    report = screen(two_hops(), labels=sanctioned(), depth=1)

    assert status(report, "AML-002") is RuleStatus.NOT_EVALUATED
    assert "needs 2" in rule(report, "AML-002").reason


def test_a_route_beyond_the_rules_hop_limit_is_not_a_finding():
    report = screen(
        two_hops(), labels=sanctioned(), depth=2, overrides={"AML-002": (None, {"max_hops": 1})}
    )

    assert status(report, "AML-002") is RuleStatus.CLEAR


# --- AML-003 / 007 / 008 -------------------------------------------------------------------------


def test_a_mixer_is_its_own_finding_and_its_own_factor():
    report = screen([flow(ROOT, BAD, 5, at(1))], labels=FakeLabels(label(BAD, Category.MIXER)))

    (item,) = findings(report, "AML-003")
    assert item.finding.factor == "mixer" and round(item.contribution) == 35
    assert report.score.value == 35 and report.score.level is Level.MODERATE


def test_incoming_and_outgoing_risk_are_told_apart():
    labels = FakeLabels(label(BAD, Category.SCAM), label(OTHER, Category.RANSOMWARE))

    report = screen([flow(BAD, ROOT, 5, at(1)), flow(ROOT, OTHER, 5, at(2))], labels=labels)

    (incoming,) = findings(report, "AML-007")
    (outgoing,) = findings(report, "AML-008")
    assert incoming.finding.direction == "incoming" and incoming.finding.factor == "scam_ransomware"
    assert outgoing.finding.direction == "outgoing"
    assert incoming.finding.target.endswith(BAD) and outgoing.finding.target.endswith(OTHER)


def test_an_exchange_or_a_protocol_is_context_not_a_risk():
    labels = FakeLabels(label(BAD, Category.EXCHANGE), label(OTHER, Category.DEFI))

    report = screen([flow(BAD, ROOT, 5, at(1)), flow(ROOT, OTHER, 5, at(2))], labels=labels)

    assert findings(report) == [] and report.score.value == 0


def test_the_same_event_found_by_two_rules_is_counted_once_by_the_stronger():
    # AML-001 and AML-007 both describe "a sanctioned address paid the subject".
    report = screen([flow(BAD, ROOT, 5, at(1))], labels=sanctioned())

    assert [i.finding.rule_id for i in report.findings] == ["AML-001"]
    (duplicate,) = report.suppressed
    assert duplicate.finding.rule_id == "AML-007" and duplicate.suppressed_by == "AML-001"
    assert duplicate.contribution == 0
    assert report.to_dict()["duplicates_not_counted"][0]["suppressed_by"] == "AML-001"


def test_a_mixer_found_by_aml_003_and_007_is_counted_once():
    report = screen([flow(BAD, ROOT, 5, at(1))], labels=FakeLabels(label(BAD, Category.MIXER)))

    assert [i.finding.rule_id for i in report.findings] == ["AML-003"]
    assert [i.suppressed_by for i in report.suppressed] == ["AML-003"]


def test_a_farther_route_is_one_severity_lower():
    labels = FakeLabels(label(BAD, Category.SCAM))

    near = screen([flow(BAD, ROOT, 5, at(1))], labels=labels)
    far = screen([flow(BAD, A, 5, at(1)), flow(A, ROOT, 4, at(2)), flow(ROOT, B, 1, at(9))],
                 labels=labels, depth=2)  # fmt: skip

    assert findings(near, "AML-007")[0].finding.severity is Severity.HIGH
    assert findings(far, "AML-007")[0].finding.severity is Severity.MEDIUM


# --- AML-013: bridges ----------------------------------------------------------------------------


def registry():
    return BridgeRegistry.from_dict(
        {"bridges": [{"name": "Example Bridge", "contracts": [
            {"chain": "ethereum", "address": BAD, "role": "both"}]}]}
    )  # fmt: skip


def test_without_any_bridge_data_the_rule_says_it_cannot_judge():
    report = screen([flow(ROOT, A, 5, at(1))])

    assert status(report, "AML-013") is RuleStatus.NOT_EVALUATED
    assert "no bridge is known" in rule(report, "AML-013").reason


def test_with_a_registry_and_no_bridge_in_sight_it_is_clear():
    report = screen([flow(ROOT, A, 5, at(1))], bridges_known=True)

    assert status(report, "AML-013") is RuleStatus.CLEAR


def test_value_sent_to_a_registered_bridge_is_a_finding_that_names_it():
    report = screen(
        [flow(ROOT, BAD, 5, at(1), tx="0xdeposit"), flow(BAD, ROOT, 1, at(2), tx="0xback")],
        registry=registry(),
        bridges_known=True,
    )

    (item,) = findings(report, "AML-013")
    assert (
        "Example Bridge" in item.finding.description
        and "Sent to and received from" in item.finding.description
    )
    assert set(item.finding.transactions) == {"0xdeposit", "0xback"}
    assert item.finding.direction is None and item.finding.factor == "other"


def test_a_route_through_a_bridge_is_a_finding_too():
    labels = FakeLabels(label(OTHER, Category.SCAM))
    moves = [flow(OTHER, BAD, 5, at(1)), flow(BAD, ROOT, 4, at(2)), flow(ROOT, B, 1, at(9))]

    report = screen(moves, labels=labels, depth=2, registry=registry(), bridges_known=True)

    descriptions = [i.finding.description for i in findings(report, "AML-013")]
    assert any("passes a cross-chain bridge" in text for text in descriptions)


# --- pinned down by mutation checks -------------------------------------------------------------


def test_a_claim_of_exactly_the_override_confidence_is_a_policy_override_and_one_below_is_not():
    at_the_line = screen([flow(BAD, ROOT, 7, at(1))], labels=sanctioned(confidence=90))
    below = screen([flow(BAD, ROOT, 7, at(1))], labels=sanctioned(confidence=89))

    assert at_the_line.score.override == "AML-001" and at_the_line.score.value == 100
    assert below.score.override is None and below.score.value == 89


def test_the_same_line_applies_to_an_address_that_is_itself_listed():
    at_the_line = screen([], labels=sanctioned(ROOT, confidence=90))
    below = screen([], labels=sanctioned(ROOT, confidence=89))

    assert at_the_line.score.override == "AML-001" and below.score.override is None


def test_the_broad_rule_scores_a_sanctioned_party_as_sanctions_when_the_specific_rules_are_off():
    off = {"AML-001": (False, None), "AML-002": (False, None)}

    report = screen([flow(BAD, ROOT, 7, at(1))], labels=sanctioned(), overrides=off)

    (item,) = report.findings
    assert item.finding.rule_id == "AML-007" and item.finding.factor == "indirect_sanctions"
    assert item.finding.severity is Severity.CRITICAL


def test_the_broad_rule_scores_a_mixer_as_a_mixer_when_the_mixer_rule_is_off():
    labels = FakeLabels(label(BAD, Category.MIXER))

    report = screen(
        [flow(BAD, ROOT, 7, at(1))], labels=labels, overrides={"AML-003": (False, None)}
    )

    (item,) = report.findings
    assert item.finding.rule_id == "AML-007" and item.finding.factor == "mixer"
    assert item.finding.severity is Severity.HIGH


def test_a_subject_that_is_itself_a_bridge_has_no_other_bridge_to_judge_against():
    report = screen([flow(A, ROOT, 5, at(1))], labels=FakeLabels(label(ROOT, Category.BRIDGE)))

    assert status(report, "AML-013") is RuleStatus.NOT_EVALUATED
