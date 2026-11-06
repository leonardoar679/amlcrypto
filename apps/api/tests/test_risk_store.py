"""Keeping assessments: the report as a document, the findings as rows, and the rule overrides."""

from __future__ import annotations

import json

import pytest

from app.database.models import RiskFinding
from app.database.repositories import risk as store
from app.intel.model import Category
from app.risk.model import ScreeningStatus
from app.risk.rules.registry import effective_rules
from app.services.users import create_user
from tests.risk_helpers import NOW, ROOT, FakeLabels, addr, at, flow, label, screen

CHAIN = "eip155:1"
BAD = addr(900)


@pytest.fixture
def org(db_session) -> int:
    user = create_user(
        db_session, email="a@example.com", password="pw-pw-pw-pw-1", organization_name="Acme"
    )
    return user.organization_id


@pytest.fixture
def other_org(db_session, org) -> int:
    user = create_user(
        db_session, email="b@example.com", password="pw-pw-pw-pw-1", organization_name="Other"
    )
    return user.organization_id


def report():
    return screen(
        [flow(BAD, ROOT, 7, at(1), tx="0xproof")],
        labels=FakeLabels(label(BAD, Category.SANCTIONS)),
    )


def make(session, org, subject=ROOT):
    return store.create_assessment(
        session,
        organization_id=org,
        subject_type="address",
        chain_id=CHAIN,
        subject=subject,
        options={"depth": 1},
    )


def test_a_new_assessment_is_queued_and_belongs_to_its_organization(db_session, org, other_org):
    row = make(db_session, org)
    db_session.commit()

    assert row.status == "QUEUED" and row.report is None and row.options == {"depth": 1}
    assert store.get_assessment(db_session, row.id, org) is row
    assert store.get_assessment(db_session, row.id, other_org) is None  # not theirs: not found
    assert store.get_assessment(db_session, row.id) is row  # the worker acts on the row it has
    assert store.get_assessment(db_session, row.id + 1000, org) is None


def test_processing_marks_the_start_once(db_session, org):
    row = make(db_session, org)

    store.mark_processing(db_session, row.id, NOW)
    store.mark_processing(db_session, row.id, NOW.replace(year=2030))

    assert row.status == "PROCESSING" and row.started_at.year == NOW.year


def test_a_completed_assessment_keeps_the_report_as_it_was_produced(db_session, org):
    row = make(db_session, org)
    made = report()

    store.complete_assessment(db_session, row.id, made, NOW)
    db_session.commit()
    db_session.refresh(row)

    assert row.status == "COMPLETED" and row.completed_at == NOW
    assert (row.risk_score, row.risk_level) == (100, "CRITICAL")
    assert row.data_coverage == "COMPLETE" and row.outcome == "findings"
    assert row.confidence_percent == round(made.score.confidence * 100)
    assert row.report == json.loads(json.dumps(made.to_dict()))


def test_each_finding_is_a_row_with_its_rule_version_and_whether_it_was_counted(db_session, org):
    row = make(db_session, org)
    made = report()

    store.complete_assessment(db_session, row.id, made, NOW)
    db_session.commit()

    rows = store.findings_of(db_session, row.id)
    by_rule = {f.rule_id: f for f in rows}
    assert sorted(by_rule) == ["AML-001", "AML-007"]
    direct, duplicate = by_rule["AML-001"], by_rule["AML-007"]
    assert direct.counted and direct.suppressed_by is None and direct.rule_version == "1.0.0"
    assert direct.severity == "CRITICAL" and direct.hops == 1 and direct.direction == "incoming"
    assert direct.contribution_hundredths == 10_000 and direct.confidence_percent == 100
    assert direct.transaction_hashes == ["0xproof"] and direct.sources == ["ofac_sdn"]
    assert direct.evidence[0]["type"] == "exposure_path"
    assert not duplicate.counted and duplicate.suppressed_by == "AML-001"
    assert duplicate.contribution_hundredths == 0


def test_completing_again_replaces_the_findings_instead_of_adding_to_them(db_session, org):
    row = make(db_session, org)

    store.complete_assessment(db_session, row.id, report(), NOW)
    store.complete_assessment(db_session, row.id, report(), NOW)
    db_session.commit()

    assert len(store.findings_of(db_session, row.id)) == 2


def test_a_report_with_no_score_is_stored_without_one(db_session, org):
    from app.graph.reader import judge

    row = make(db_session, org)
    nothing = screen([], states={ROOT: judge({}, "n")})

    store.complete_assessment(db_session, row.id, nothing, NOW)

    assert row.status == "COMPLETED" and row.risk_score is None and row.risk_level is None
    assert row.confidence_percent is None and row.outcome == "insufficient_data"
    assert row.data_coverage == "INSUFFICIENT" and store.findings_of(db_session, row.id) == []


def test_a_failed_screening_keeps_why(db_session, org):
    row = make(db_session, org)

    store.fail_assessment(db_session, row.id, "x" * 5000, NOW)

    assert row.status == ScreeningStatus.FAILED.value and len(row.error) == 2000
    assert row.completed_at == NOW


def test_history_lists_the_organizations_finished_screenings_newest_first(
    db_session, org, other_org
):
    first, second, queued, failed = (make(db_session, org) for _ in range(4))
    theirs = make(db_session, other_org)
    elsewhere = make(db_session, org, subject=addr(5))
    store.complete_assessment(db_session, first.id, report(), NOW)
    store.complete_assessment(db_session, second.id, report(), NOW)
    store.complete_assessment(db_session, theirs.id, report(), NOW)
    store.complete_assessment(db_session, elsewhere.id, report(), NOW)
    store.fail_assessment(db_session, failed.id, "no", NOW)
    db_session.commit()

    found = store.completed_assessments(db_session, org, CHAIN, ROOT)

    assert [row.id for row in found] == [second.id, first.id]
    assert queued.id not in {row.id for row in found}
    assert store.completed_assessments(db_session, org, CHAIN, ROOT, limit=1)[0].id == second.id
    assert store.completed_assessments(db_session, other_org, CHAIN, ROOT)[0].id == theirs.id


def test_deleting_an_assessment_takes_its_findings_with_it(db_session, org):
    row = make(db_session, org)
    store.complete_assessment(db_session, row.id, report(), NOW)
    db_session.commit()

    db_session.delete(row)
    db_session.flush()

    assert db_session.query(RiskFinding).filter_by(assessment_id=row.id).count() == 0


# --- rule overrides ----------------------------------------------------------------------------


def test_an_override_is_kept_and_turns_into_the_effective_rule(db_session):
    store.set_rule_override(
        db_session, "AML-004", enabled=False, config={"window_seconds": 600}, note="noisy"
    )
    store.set_rule_override(db_session, "AML-009", enabled=None, config={"threshold": 25})
    db_session.commit()

    overrides = store.rule_overrides(db_session)
    rules = {e.rule.id: e for e in effective_rules(overrides)}

    assert overrides["AML-004"] == (False, {"window_seconds": 600})
    assert not rules["AML-004"].enabled and rules["AML-004"].config["window_seconds"] == 600
    assert rules["AML-009"].enabled and rules["AML-009"].config["threshold"] == 25
    assert rules["AML-010"].config["threshold"] == 10


def test_setting_an_override_again_replaces_it_and_clearing_returns_the_defaults(db_session):
    store.set_rule_override(db_session, "AML-004", enabled=False, config=None)
    store.set_rule_override(db_session, "AML-004", enabled=True, config={"min_ratio": 0.5})
    db_session.commit()

    assert store.rule_overrides(db_session) == {"AML-004": (True, {"min_ratio": 0.5})}
    assert store.clear_rule_override(db_session, "AML-004") is True
    assert store.clear_rule_override(db_session, "AML-004") is False
    assert store.rule_overrides(db_session) == {}


# --- pinned down by mutation checks -------------------------------------------------------------


def test_a_finished_screening_is_not_taken_back_to_processing(db_session, org):
    row = make(db_session, org)
    store.complete_assessment(db_session, row.id, report(), NOW)

    store.mark_processing(db_session, row.id, NOW)

    assert row.status == "COMPLETED"


def test_the_confidence_is_rounded_to_a_whole_percent_not_cut(db_session, org):
    row = make(db_session, org)
    made = screen(
        [flow(BAD, ROOT, 7, at(1))], labels=FakeLabels(label(BAD, Category.SCAM, confidence=58))
    )

    store.complete_assessment(db_session, row.id, made, NOW)

    assert made.score.confidence == pytest.approx(0.58)
    assert row.confidence_percent == 58  # 0.58 x 100 is 57.99999999999999 in floating point


def test_an_override_set_again_without_a_switch_goes_back_to_the_default_switch(db_session):
    store.set_rule_override(db_session, "AML-004", enabled=False, config=None)
    store.set_rule_override(db_session, "AML-004", enabled=None, config={"min_ratio": 0.5})
    db_session.commit()

    assert store.rule_overrides(db_session) == {"AML-004": (None, {"min_ratio": 0.5})}
