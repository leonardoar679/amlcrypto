"""The analyst's workflow on an alert: which changes are allowed and what each one leaves behind."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.access.permissions import Role
from app.monitoring import workflow
from app.monitoring.workflow import AlertConflict, AlertError, change
from tests.access_helpers import make_user
from tests.monitoring_helpers import NOW, alert_for, watch


@pytest.fixture
def ana(db_session):
    return make_user(db_session, "ana@example.com", Role.ANALYST)


@pytest.fixture
def alert(db_session, settings, ana):
    return alert_for(db_session, watch(db_session, settings, ana))


def apply(db_session, alert, by=None, now=NOW, **fields):
    return change(db_session, alert, by=by, now=now, **fields)


def test_the_statuses_and_the_ways_between_them_are_these():
    assert workflow.STATUSES == ("open", "acknowledged", "closed")
    assert workflow.RESOLUTIONS == ("true_positive", "false_positive", "accepted_risk")
    assert {
        "open": {"acknowledged", "closed"},
        "acknowledged": {"open", "closed"},
        "closed": {"open"},
    } == workflow.TRANSITIONS


# --- acknowledging and closing ------------------------------------------------------------------


def test_acknowledging_notes_the_change_and_nothing_else(db_session, alert):
    changes = apply(db_session, alert, status="acknowledged", by=1)

    assert changes == {"status": {"from": "open", "to": "acknowledged"}}
    assert alert.status == "acknowledged" and alert.resolution is None
    assert alert.closed_at is None and alert.closed_by is None


def test_an_alert_is_closed_with_how_it_ended_and_who_and_when(db_session, alert, ana):
    changes = apply(db_session, alert, status="closed", resolution="false_positive", by=ana.id)

    assert changes == {
        "status": {"from": "open", "to": "closed"},
        "resolution": {"from": None, "to": "false_positive"},
    }
    assert (alert.status, alert.resolution) == ("closed", "false_positive")
    assert alert.closed_at == NOW and alert.closed_by == ana.id


def test_an_acknowledged_alert_can_be_closed_or_put_back(db_session, alert):
    apply(db_session, alert, status="acknowledged")
    assert apply(db_session, alert, status="open") == {
        "status": {"from": "acknowledged", "to": "open"}
    }
    apply(db_session, alert, status="acknowledged")
    apply(db_session, alert, status="closed", resolution="true_positive")
    assert alert.status == "closed"


@pytest.mark.parametrize("resolution", workflow.RESOLUTIONS)
def test_each_of_the_three_outcomes_closes_an_alert(db_session, alert, resolution):
    apply(db_session, alert, status="closed", resolution=resolution)
    assert alert.resolution == resolution


def test_closing_without_saying_how_it_ended_is_refused_and_changes_nothing(db_session, alert):
    with pytest.raises(AlertError, match="Say how it ended"):
        apply(db_session, alert, status="closed")
    assert alert.status == "open" and alert.closed_at is None


def test_the_system_may_close_an_alert_as_recovered_but_a_person_may_not(db_session, alert):
    with pytest.raises(AlertError, match="true_positive, false_positive or accepted_risk"):
        apply(db_session, alert, status="closed", resolution="recovered")
    alert.status, alert.resolution = "closed", "recovered"  # as monitoring does
    apply(db_session, alert, status="open")  # a person can still reopen it
    assert alert.resolution is None


@pytest.mark.parametrize("status", ["done", "OPEN", "", "resolved"])
def test_a_status_that_is_not_one_is_refused(db_session, alert, status):
    with pytest.raises(AlertError, match="open, acknowledged or closed"):
        apply(db_session, alert, status=status)


@pytest.mark.parametrize("status", [None, "open", "acknowledged"])
def test_a_resolution_goes_only_with_closing(db_session, alert, status):
    with pytest.raises(AlertError, match="goes with closing"):
        apply(db_session, alert, status=status, resolution="false_positive")
    assert alert.resolution is None


def test_asking_for_the_status_it_has_is_not_a_change(db_session, alert):
    assert apply(db_session, alert, status="open") == {}
    assert apply(db_session, alert) == {}


# --- closed alerts ------------------------------------------------------------------------------


def test_a_closed_alert_can_only_be_reopened(db_session, alert):
    apply(db_session, alert, status="closed", resolution="accepted_risk")

    with pytest.raises(AlertConflict, match="reopen it first"):
        apply(db_session, alert, status="acknowledged")
    assert alert.status == "closed"


def test_reopening_clears_how_it_ended_and_who_closed_it(db_session, alert, ana):
    apply(db_session, alert, status="closed", resolution="true_positive", by=ana.id)

    changes = apply(db_session, alert, status="open", by=ana.id, now=NOW + timedelta(hours=1))

    assert changes == {
        "status": {"from": "closed", "to": "open"},
        "resolution": {"from": "true_positive", "to": None},
    }
    assert alert.status == "open" and alert.resolution is None
    assert alert.closed_at is None and alert.closed_by is None


def test_closing_it_again_is_a_fresh_close(db_session, alert, ana):
    apply(db_session, alert, status="closed", resolution="false_positive", by=ana.id)
    apply(db_session, alert, status="open")
    later = NOW + timedelta(days=2)
    apply(db_session, alert, status="closed", resolution="true_positive", by=ana.id, now=later)
    assert (alert.resolution, alert.closed_at) == ("true_positive", later)


def test_how_a_closed_alert_ended_can_be_corrected_without_moving_when_it_closed(
    db_session, alert, ana
):
    apply(db_session, alert, status="closed", resolution="false_positive", by=ana.id)
    other = make_user(db_session, "bob@example.com", Role.ANALYST)

    changes = apply(
        db_session,
        alert,
        status="closed",
        resolution="true_positive",
        by=other.id,
        now=NOW + timedelta(days=1),
    )

    assert changes == {"resolution": {"from": "false_positive", "to": "true_positive"}}
    assert alert.closed_at == NOW and alert.closed_by == ana.id  # not by the correction
    assert apply(db_session, alert, status="closed") == {}
    assert apply(db_session, alert, status="closed", resolution="true_positive") == {}


# --- who has it ---------------------------------------------------------------------------------


def test_an_alert_is_handed_to_a_person_of_the_organization_and_taken_back(db_session, alert, ana):
    assert apply(db_session, alert, assignee_id=ana.id) == {
        "assignee_id": {"from": None, "to": ana.id}
    }
    assert alert.assignee_id == ana.id and alert.status == "open"  # handing over is not a decision

    assert apply(db_session, alert, assignee_id=ana.id) == {}
    assert apply(db_session, alert, assignee_id=None) == {
        "assignee_id": {"from": ana.id, "to": None}
    }
    assert alert.assignee_id is None


def test_leaving_the_assignee_out_keeps_it(db_session, alert, ana):
    apply(db_session, alert, assignee_id=ana.id)
    apply(db_session, alert, status="acknowledged")
    assert alert.assignee_id == ana.id


def test_the_assignee_must_be_an_active_person_of_the_same_organization(db_session, alert):
    stranger = make_user(db_session, "sam@example.com", Role.ANALYST, "Other Org")
    gone = make_user(db_session, "gone@example.com", Role.VIEWER)
    gone.is_active = False
    db_session.commit()

    for wrong in (stranger.id, gone.id, 10**6, 0):
        with pytest.raises(AlertError, match="active person of this organization"):
            apply(db_session, alert, assignee_id=wrong)
    assert alert.assignee_id is None


def test_a_request_with_one_bad_part_changes_none_of_it(db_session, alert):
    with pytest.raises(AlertError):
        apply(db_session, alert, status="acknowledged", assignee_id=10**6)
    assert alert.status == "open" and alert.assignee_id is None

    with pytest.raises(AlertError):
        apply(db_session, alert, status="closed", assignee_id=None)  # no resolution
    assert alert.status == "open"


def test_a_status_and_an_assignee_can_change_together(db_session, alert, ana):
    changes = apply(db_session, alert, status="acknowledged", assignee_id=ana.id)
    assert changes == {
        "status": {"from": "open", "to": "acknowledged"},
        "assignee_id": {"from": None, "to": ana.id},
    }
