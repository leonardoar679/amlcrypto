"""When a new report on a monitored address deserves an alert, and what the alert says."""

from __future__ import annotations

from app.monitoring import detect
from app.monitoring.detect import (
    KIND_BASELINE,
    KIND_DEGRADED,
    KIND_NEW_FINDING,
    KIND_RISK_INCREASE,
    Snapshot,
    decide,
    finding_key,
    remember,
    snapshot_of,
)


def finding(rule="AML-001", severity="HIGH", target="ethereum:0xbad", direction="incoming", **more):
    return {
        "rule_id": rule,
        "rule_name": f"Rule {rule}",
        "severity": severity,
        "description": f"{rule} found something",
        "hops": 1,
        "direction": direction,
        "target": target,
        "confidence": 0.9,
        "transaction_hashes": ["0xaa"],
        "sources": ["ofac_sdn"],
        **more,
    }


def report(score, level, *findings):
    return {"risk_score": score, "risk_level": level, "findings": list(findings)}


def snap(score, level, *findings):
    return snapshot_of(report(score, level, *findings))


def judge(previous, current, *, known=(), min_severity="MEDIUM", delta=15, label="0xabc"):
    return decide(
        previous=previous,
        report=current,
        known=known,
        min_severity=min_severity,
        score_delta=delta,
        label=label,
    )


# --- what a finding is, over time --------------------------------------------------------------


def test_a_finding_is_the_same_finding_whatever_its_evidence_or_confidence():
    base = finding()
    assert finding_key(base) == finding_key(
        finding(confidence=0.2, transaction_hashes=["0xbb", "0xcc"])
    )
    assert finding_key(base) == "AML-001|ethereum:0xbad|incoming"
    for other in (
        finding(rule="AML-002"),
        finding(target="ethereum:0xother"),
        finding(direction="outgoing"),
    ):
        assert finding_key(other) != finding_key(base)
    assert finding_key({"rule_id": "AML-009"}) == "AML-009||"
    assert finding_key(finding(target=None, direction=None)) == "AML-001||"


def test_a_snapshot_keeps_the_score_the_level_and_the_finding_keys():
    shot = snap(40, "HIGH", finding(), finding(rule="AML-003"))
    assert (shot.score, shot.level) == (40, "HIGH")
    assert shot.keys == {"AML-001|ethereum:0xbad|incoming", "AML-003|ethereum:0xbad|incoming"}
    empty = snapshot_of({})
    assert (empty.score, empty.level, empty.keys) == (None, None, frozenset())


def test_severities_and_levels_are_ranked_and_anything_else_is_below_all():
    assert [detect.severity_rank(s) for s in detect.SEVERITIES] == [0, 1, 2, 3]
    assert [detect.level_rank(s) for s in detect.LEVELS] == [0, 1, 2, 3]
    for unknown in (None, "", "SEVERE", "high"):
        assert detect.severity_rank(unknown) == -1 and detect.level_rank(unknown) == -1
    assert detect.LEVEL_SEVERITY == {
        "LOW": "LOW",
        "MODERATE": "MEDIUM",
        "HIGH": "HIGH",
        "CRITICAL": "CRITICAL",
    }


# --- the first report --------------------------------------------------------------------------


def test_the_first_report_is_quiet_unless_the_address_is_already_risky_enough():
    assert judge(None, report(5, "LOW")) is None
    assert (
        judge(None, report(25, "MODERATE", finding(severity="LOW"))) is not None
    )  # MEDIUM >= MEDIUM
    assert judge(None, report(25, "MODERATE"), min_severity="HIGH") is None
    assert judge(None, report(5, "LOW"), min_severity="LOW") is not None  # "tell me everything"


def test_an_address_that_is_risky_from_the_start_raises_a_baseline_alert_that_says_so():
    bad = finding(severity="CRITICAL", description="Listed by OFAC")
    alert = judge(None, report(100, "CRITICAL", bad), label="0xbad…")

    assert alert.kind == KIND_BASELINE and alert.severity == "CRITICAL"
    assert alert.title == "0xbad… is already CRITICAL risk"
    assert [(r.code, r.detail) for r in alert.reasons] == [
        ("baseline", "already CRITICAL risk when monitoring began")
    ]
    assert [f["rule_id"] for f in alert.findings] == ["AML-001"]
    assert alert.previous is None and alert.current.level == "CRITICAL"
    assert alert.summary.splitlines() == [
        "Already CRITICAL risk when monitoring began.",
        "AML-001: Listed by OFAC",
    ]


def test_a_baseline_alert_shows_a_bounded_number_of_findings():
    many = [finding(rule=f"AML-{n:03d}") for n in range(1, 16)]
    alert = judge(None, report(70, "CRITICAL", *many))
    assert len(alert.findings) == detect.MAX_FINDINGS_SHOWN
    assert [f["rule_id"] for f in alert.findings] == [f"AML-{n:03d}" for n in range(1, 11)]


def test_the_strongest_of_nothing_worth_ranking_is_the_weakest():
    assert detect._strongest([]) == "LOW"
    assert detect._strongest([None, "SEVERE", ""]) == "LOW"
    assert detect._strongest([None, "HIGH", "MEDIUM"]) == "HIGH"


def test_the_baseline_severity_is_the_strongest_of_the_level_and_the_findings():
    assert judge(None, report(45, "HIGH", finding(severity="CRITICAL"))).severity == "CRITICAL"
    assert judge(None, report(45, "HIGH", finding(severity="LOW"))).severity == "HIGH"


def test_a_report_with_no_score_cannot_raise_a_baseline_alert():
    assert judge(None, report(None, None)) is None


# --- later reports -----------------------------------------------------------------------------


def test_nothing_new_is_no_alert():
    before = snap(45, "HIGH", finding())
    assert judge(before, report(45, "HIGH", finding()), known=before.keys) is None
    assert judge(before, report(30, "MODERATE", finding()), known=before.keys) is None  # it fell


def test_a_new_finding_raises_an_alert_with_its_severity_and_evidence():
    before = snap(20, "MODERATE")
    now = report(
        60, "HIGH", finding(severity="CRITICAL", description="Sent to a sanctioned address")
    )

    alert = judge(before, now, label="0xabc")

    assert alert.kind == KIND_NEW_FINDING and alert.severity == "CRITICAL"
    assert alert.title == "1 new finding on 0xabc"
    assert [r.code for r in alert.reasons] == ["new_finding", "level_up", "score_up"]
    assert [r.detail for r in alert.reasons] == [
        "1 new finding",
        "MODERATE to HIGH",
        "score 20 to 60",
    ]
    assert [f["description"] for f in alert.findings] == ["Sent to a sanctioned address"]


def test_several_new_findings_are_one_alert():
    before = snap(0, "LOW")
    now = report(
        70,
        "CRITICAL",
        finding(),
        finding(rule="AML-003", severity="MEDIUM"),
        finding(rule="AML-005", severity="LOW"),
    )
    alert = judge(before, now)
    assert alert.title == "2 new findings on 0xabc"  # the LOW one is below the threshold
    assert [f["rule_id"] for f in alert.findings] == ["AML-001", "AML-003"]
    assert alert.reasons[0].detail == "2 new findings"


def test_a_finding_already_seen_is_not_new_again_even_if_it_went_away_and_came_back():
    seen = finding()
    known = {finding_key(seen)}
    # it is not in the previous report (it had gone away), and it is back: still not new
    alert = judge(snap(10, "LOW"), report(10, "LOW", seen), known=known)
    assert alert is None


def test_a_new_finding_below_the_threshold_is_kept_in_the_report_but_raises_nothing():
    assert judge(snap(5, "LOW"), report(8, "LOW", finding(severity="LOW"))) is None
    assert (
        judge(snap(5, "LOW"), report(8, "LOW", finding(severity="MEDIUM"))) is not None
    )  # at the line
    assert (
        judge(snap(5, "LOW"), report(8, "LOW", finding(severity="MEDIUM")), min_severity="HIGH")
        is None
    )


def test_a_higher_level_raises_an_alert_even_without_a_new_finding():
    kept = finding()
    before = snap(10, "LOW", kept)
    alert = judge(before, report(45, "HIGH", kept), known=before.keys)

    assert alert.kind == KIND_RISK_INCREASE and alert.severity == "HIGH"
    assert alert.title == "Risk on 0xabc rose from LOW to HIGH"
    assert [r.code for r in alert.reasons] == ["level_up", "score_up"]
    assert alert.findings == ()


def test_a_rise_to_a_level_below_the_threshold_is_not_worth_an_alert():
    assert judge(snap(5, "LOW"), report(25, "MODERATE"), min_severity="HIGH") is None
    assert judge(snap(5, "LOW"), report(25, "MODERATE"), min_severity="MEDIUM") is not None


def test_a_report_scored_for_the_first_time_after_one_that_could_not_be_is_a_rise():
    alert = judge(snap(None, None), report(45, "HIGH"))
    assert alert.title == "Risk on 0xabc rose from not scored to HIGH"
    assert [r.code for r in alert.reasons] == ["level_up"]  # no score to compare


def test_a_score_rise_by_the_set_amount_raises_an_alert_within_the_same_level():
    before = snap(40, "HIGH")
    exactly = judge(before, report(55, "HIGH"), delta=15)
    assert exactly.kind == KIND_RISK_INCREASE and exactly.severity == "HIGH"
    assert exactly.title == "Risk score on 0xabc rose from 40 to 55"
    assert [r.code for r in exactly.reasons] == ["score_up"]
    assert judge(before, report(54, "HIGH"), delta=15) is None  # one point short


def test_a_score_rise_inside_a_level_below_the_threshold_is_ignored():
    assert judge(snap(2, "LOW"), report(18, "LOW"), min_severity="MEDIUM", delta=15) is None


def test_the_alert_severity_is_the_strongest_thing_it_reports():
    # a MEDIUM finding while the level is HIGH: the alert is HIGH
    alert = judge(snap(10, "LOW"), report(45, "HIGH", finding(severity="MEDIUM")))
    assert alert.severity == "HIGH"
    # a CRITICAL finding while the level is below the threshold: still CRITICAL
    alert = judge(snap(0, "LOW"), report(5, "LOW", finding(severity="CRITICAL")))
    assert alert.severity == "CRITICAL" and [r.code for r in alert.reasons] == ["new_finding"]


def test_the_stored_evidence_says_what_changed_and_from_what():
    before = snap(10, "LOW")
    alert = judge(before, report(60, "HIGH", finding()))
    evidence = alert.evidence()
    assert evidence["reasons"] == [
        {"code": "new_finding", "detail": "1 new finding"},
        {"code": "level_up", "detail": "LOW to HIGH"},
        {"code": "score_up", "detail": "score 10 to 60"},
    ]
    assert evidence["previous"] == {"score": 10, "level": "LOW"}
    assert evidence["current"] == {"score": 60, "level": "HIGH"}
    assert evidence["findings"][0]["rule_id"] == "AML-001"
    assert judge(None, report(60, "HIGH")).evidence()["previous"] is None


def test_an_alert_keeps_a_few_findings_and_a_few_hashes_of_each():
    many = [
        finding(rule=f"AML-{n:03d}", transaction_hashes=[f"0x{i}" for i in range(20)])
        for n in range(1, 16)
    ]
    alert = judge(snap(0, "LOW"), report(70, "CRITICAL", *many))
    assert len(alert.findings) == detect.MAX_FINDINGS_SHOWN
    assert all(len(f["transaction_hashes"]) == detect.MAX_HASHES_SHOWN for f in alert.findings)
    assert alert.reasons[0].detail == "15 new findings"  # it still says how many there were
    named = [line for line in alert.summary.splitlines() if line.startswith("AML-")]
    assert len(named) == 3  # the summary names three of them
    assert set(alert.findings[0]) == {
        "rule_id", "rule_name", "severity", "description", "hops", "direction", "target",
        "confidence", "transaction_hashes", "sources",
    }  # fmt: skip


# --- not being able to look ----------------------------------------------------------------------


def test_monitoring_that_keeps_failing_is_an_alert_of_its_own():
    alert = detect.degraded("0xabc", 3, "provider down")
    assert alert.kind == KIND_DEGRADED and alert.severity == "MEDIUM"
    assert alert.title == "Monitoring of 0xabc is not working"
    assert "3 checks in a row" in alert.summary and "provider down" in alert.summary
    assert alert.findings == () and alert.reasons[0].code == "degraded"
    assert "last error" not in detect.degraded("0xabc", 3, None).summary
    assert alert.evidence()["current"] == {"score": None, "level": None}


def test_the_kinds_are_the_four_there_are():
    assert detect.KINDS == (KIND_BASELINE, KIND_NEW_FINDING, KIND_RISK_INCREASE, KIND_DEGRADED)


# --- what is remembered --------------------------------------------------------------------------


def test_what_is_remembered_keeps_the_old_and_adds_the_new_without_repeats():
    shown = report(10, "LOW", finding(rule="AML-002"), finding(rule="AML-001"))
    kept = remember(["old|a|", "AML-001|ethereum:0xbad|incoming"], shown)
    assert kept == ["old|a|", "AML-001|ethereum:0xbad|incoming", "AML-002|ethereum:0xbad|incoming"]
    assert len(kept) == len(set(kept))
    assert remember([], report(0, "LOW")) == []


def test_what_is_remembered_is_bounded_and_the_latest_survive():
    old = [f"old{n}||" for n in range(detect.MAX_KNOWN + 50)]
    kept = remember(old, report(10, "LOW", finding()))
    assert len(kept) == detect.MAX_KNOWN
    assert kept[-1] == "AML-001|ethereum:0xbad|incoming" and kept[0] == "old51||"


def test_a_snapshot_is_what_it_says():
    assert Snapshot(5, "LOW", frozenset({"a"})).keys == {"a"}
