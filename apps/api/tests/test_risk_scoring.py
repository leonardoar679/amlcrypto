"""How findings become a score, a level and a confidence, worked by hand."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.risk.model import Coverage, Finding, Level, Severity
from app.risk.scoring import (
    ScoringSettings,
    contribution,
    decay,
    hop_weight,
    level_of,
    score_findings,
    suppress,
)
from tests.graph_helpers import NOW

SETTINGS = ScoringSettings()
PRIORITY = {"AML-001": 10, "AML-002": 20, "AML-007": 70, "AML-008": 80, "AML-004": 40}


def finding(
    rule_id="AML-007",
    factor="scam_ransomware",
    *,
    confidence=1.0,
    hops=1,
    key=None,
    direction="incoming",
    latest_at=None,
    override=False,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        rule_name="n",
        rule_version="1.0.0",
        severity=Severity.HIGH,
        factor=factor,
        description="d",
        key=key or (rule_id, factor, str(hops), direction or ""),
        confidence=confidence,
        hops=hops,
        direction=direction,
        latest_at=latest_at,
        override=override,
    )


def score(findings, *, coverage=Coverage.COMPLETE, fresh=True, settings=SETTINGS):
    return score_findings(
        findings,
        priorities=PRIORITY,
        coverage=coverage,
        sources_fresh=fresh,
        now=NOW,
        settings=settings,
    )


# --- levels -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,level",
    [
        (0, Level.LOW),
        (19, Level.LOW),
        (20, Level.MODERATE),
        (39, Level.MODERATE),
        (40, Level.HIGH),
        (69, Level.HIGH),
        (70, Level.CRITICAL),
        (100, Level.CRITICAL),
    ],
)
def test_the_levels_follow_the_blueprints_table_not_its_example(value, level):
    assert level_of(value, SETTINGS) is level  # the example showed 82 as HIGH; the table says 70+


# --- one finding --------------------------------------------------------------------------------


def test_a_finding_adds_its_factors_cap_weighted_by_distance_and_confidence():
    assert contribution(finding(hops=1), NOW, SETTINGS) == 40
    assert contribution(finding(hops=2), NOW, SETTINGS) == pytest.approx(28)
    assert contribution(finding(hops=3), NOW, SETTINGS) == pytest.approx(16)
    assert contribution(finding(hops=4), NOW, SETTINGS) == pytest.approx(8)
    assert contribution(finding(hops=5), NOW, SETTINGS) == pytest.approx(4)  # beyond the table
    assert contribution(finding(hops=1, confidence=0.5), NOW, SETTINGS) == 20


def test_a_finding_with_no_distance_counts_in_full():
    assert hop_weight(None, SETTINGS) == 1.0 and hop_weight(0, SETTINGS) == 1.0


def test_older_evidence_counts_for_less_down_to_a_floor():
    week, year, long_ago = (NOW - timedelta(days=d) for d in (7, 365, 2000))

    assert decay(finding(latest_at=week), NOW, SETTINGS) == 1.0
    assert decay(finding(latest_at=NOW - timedelta(days=90)), NOW, SETTINGS) == 1.0
    assert decay(finding(latest_at=NOW - timedelta(days=227.5)), NOW, SETTINGS) == pytest.approx(
        0.75
    )
    assert decay(finding(latest_at=year), NOW, SETTINGS) == 0.5
    assert decay(finding(latest_at=long_ago), NOW, SETTINGS) == 0.5
    assert decay(finding(latest_at=None), NOW, SETTINGS) == 1.0


def test_a_sanction_does_not_age():
    old = finding("AML-001", "direct_sanctions", latest_at=NOW - timedelta(days=900))

    assert decay(old, NOW, SETTINGS) == 1.0 and contribution(old, NOW, SETTINGS) == 100


def test_the_direction_can_be_weighted():
    heavier = ScoringSettings(direction_weights={"incoming": 1.0, "outgoing": 0.5})

    assert contribution(finding(direction="outgoing"), NOW, heavier) == 20
    assert contribution(finding(direction="incoming"), NOW, heavier) == 40
    assert contribution(finding(direction=None), NOW, heavier) == 40


# --- many findings ------------------------------------------------------------------------------


def test_findings_in_one_factor_add_up_but_never_pass_the_factors_cap():
    two = score([finding(confidence=0.5, key=("a",)), finding(confidence=0.5, key=("b",))])
    many = score([finding(key=(str(n),)) for n in range(10)])

    # 40 x (1 - 0.5 x 0.5) = 30: each is 20 of a possible 40
    assert two.factors["scam_ransomware"] == pytest.approx(30)
    assert many.factors["scam_ransomware"] == 40 and many.value == 40


def test_factors_add_up_to_at_most_one_hundred():
    findings = [
        finding("AML-007", "scam_ransomware", key=("a",)),
        finding("AML-002", "indirect_sanctions", hops=1, key=("b",)),
        finding("AML-004", "mixer", key=("c",)),
        finding("AML-004", "layering", key=("d",)),
        finding("AML-004", "rapid", key=("e",)),
    ]

    result = score(findings)

    assert sum(result.factors.values()) == pytest.approx(40 + 40 + 35 + 25 + 15)
    assert result.value == 100 and result.level is Level.CRITICAL


def test_the_score_is_rounded_to_a_whole_number():
    assert score([finding(hops=3)]).value == 16
    assert score([finding(hops=2, confidence=0.9)]).value == 25  # 25.2


def test_a_policy_override_makes_the_score_one_hundred_and_says_which_rule_did():
    small = finding("AML-007", "scam_ransomware", confidence=0.1, key=("a",))
    direct = finding("AML-001", "direct_sanctions", confidence=0.6, key=("b",), override=True)

    result = score([small, direct])

    assert result.value == 100 and result.override == "AML-001" and result.level is Level.CRITICAL


def test_without_an_override_a_weak_sanctions_claim_scores_by_its_confidence():
    weak = finding("AML-001", "direct_sanctions", confidence=0.5)

    result = score([weak])

    assert result.value == 50 and result.override is None and result.level is Level.HIGH


def test_nothing_found_scores_zero_without_saying_anything_is_safe():
    result = score([])

    assert result.value == 0 and result.level is Level.LOW and result.scored == ()


# --- duplicates ---------------------------------------------------------------------------------


def test_two_findings_about_one_event_are_counted_once_by_the_rule_with_priority():
    direct = finding("AML-001", "direct_sanctions", key=("exposure", "x", "incoming"))
    broad = finding("AML-007", "scam_ransomware", key=("exposure", "x", "incoming"))
    other = finding("AML-008", "scam_ransomware", key=("exposure", "y", "outgoing"))

    result = suppress([broad, direct, other], PRIORITY)

    assert [(f.rule_id, by) for f, by in result] == [
        ("AML-007", "AML-001"),
        ("AML-001", None),
        ("AML-008", None),
    ]


def test_a_suppressed_finding_adds_nothing_and_stays_on_the_record():
    direct = finding("AML-001", "direct_sanctions", key=("e",))
    broad = finding("AML-007", "scam_ransomware", key=("e",))

    result = score([broad, direct])

    assert result.factors == {"direct_sanctions": 100.0}
    suppressed = [s for s in result.scored if s.suppressed_by]
    assert len(suppressed) == 1 and suppressed[0].contribution == 0


# --- confidence ---------------------------------------------------------------------------------


def test_confidence_is_the_findings_own_weighted_by_what_they_contributed():
    firm = finding("AML-001", "direct_sanctions", confidence=1.0, key=("a",))
    weak = finding("AML-004", "rapid", confidence=0.5, key=("b",))  # contributes 7.5 against 100

    result = score([firm, weak])

    assert result.confidence == pytest.approx((1.0 * 100 + 0.5 * 7.5) / 107.5)


def test_partial_data_and_stale_sources_lower_confidence_but_not_the_score():
    base = score([finding()])
    partial = score([finding()], coverage=Coverage.PARTIAL)
    stale = score([finding()], fresh=False)
    poor = score([finding()], coverage=Coverage.INSUFFICIENT, fresh=False)

    assert base.confidence == 1.0 and base.value == partial.value == stale.value == poor.value
    assert partial.confidence == pytest.approx(0.7) and stale.confidence == pytest.approx(0.8)
    assert poor.confidence == pytest.approx(0.2 * 0.8)


def test_with_no_findings_confidence_is_how_far_a_negative_result_can_be_trusted():
    assert score([]).confidence == pytest.approx(0.9)
    assert score([], coverage=Coverage.PARTIAL).confidence == pytest.approx(0.63)
    assert score([], fresh=False).confidence == pytest.approx(0.72)


def test_the_constants_used_are_recorded_with_the_score_and_marked_uncalibrated():
    recorded = score([finding()]).settings

    assert recorded["calibrated"] is False and recorded["factor_caps"]["mixer"] == 35
    assert recorded["hop_weights"]["2"] == 0.7 and recorded["level_bounds"] == [20, 40, 70]


# --- pinned down by mutation checks -------------------------------------------------------------


def test_the_score_is_rounded_not_truncated():
    assert score([finding(hops=2, confidence=0.95)]).value == 27  # 26.6


def test_a_finding_with_no_confidence_adds_nothing_and_the_confidence_says_so():
    result = score([finding(confidence=0.0)])

    assert result.value == 0 and result.confidence == 0.0


def test_when_several_findings_set_the_override_the_stronger_rule_is_named():
    weaker = finding("AML-007", "scam_ransomware", key=("a",), override=True)
    stronger = finding("AML-001", "direct_sanctions", key=("b",), override=True)

    assert score([weaker, stronger]).override == "AML-001"
    assert score([stronger, weaker]).override == "AML-001"
