"""Registering addresses to watch, changing how, and queueing their checks."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.access.permissions import Role
from app.database.models import FetchJob, MonitoredAddress
from app.database.repositories import jobs as queue
from app.database.repositories import monitoring as store
from app.monitoring import service
from app.monitoring.service import CHECK_ADDRESS, MonitorConflict, MonitorError
from tests.access_helpers import make_user
from tests.monitoring_helpers import ADDRESS, NOW, OTHER_ADDRESS, watch


@pytest.fixture
def user(db_session):
    return make_user(db_session, "ana@example.com", Role.ANALYST)


def jobs(session):
    return list(session.scalars(select(FetchJob).where(FetchJob.job_type == CHECK_ADDRESS)))


# --- registering ---------------------------------------------------------------------------------


def test_an_address_is_watched_from_now_with_the_usual_choices(db_session, settings, user):
    row = watch(db_session, settings, user)

    assert row.id and row.organization_id == user.organization_id and row.created_by == user.id
    assert (row.chain_id, row.address) == ("eip155:1", ADDRESS)
    assert row.status == "active" and row.name is None
    assert row.check_interval_seconds == settings.monitoring_default_interval_seconds == 21_600
    assert row.depth == 1 and row.min_severity == "MEDIUM"
    assert row.next_check_at == NOW  # it is checked as soon as the scheduler next runs
    assert row.known_findings == [] and row.consecutive_failures == 0
    assert row.last_checked_at is None and row.last_assessment_id is None


def test_what_was_chosen_is_what_is_kept(db_session, settings, user):
    row = watch(
        db_session, settings, user, name="  Treasury  ", interval=900, depth=3, min_severity="HIGH"
    )
    assert (row.name, row.check_interval_seconds, row.depth, row.min_severity) == (
        "Treasury",
        900,
        3,
        "HIGH",
    )


def test_a_blank_name_is_no_name_and_a_long_one_is_refused(db_session, settings, user):
    assert watch(db_session, settings, user, name="   ").name is None
    assert (
        watch(db_session, settings, user, address=OTHER_ADDRESS, name="x" * 120).name == "x" * 120
    )
    with pytest.raises(MonitorError, match="at most 120"):
        watch(db_session, settings, user, address="0x" + "ee" * 20, name="x" * 121)


@pytest.mark.parametrize("seconds", [899, 0, -1, 30 * 24 * 3600 + 1])
def test_an_interval_outside_the_allowed_range_is_refused(db_session, settings, user, seconds):
    with pytest.raises(MonitorError, match="every 900 seconds to 2592000 seconds"):
        watch(db_session, settings, user, interval=seconds)


@pytest.mark.parametrize("seconds", [900, 30 * 24 * 3600])
def test_the_ends_of_the_allowed_range_are_allowed(db_session, settings, user, seconds):
    assert watch(db_session, settings, user, interval=seconds).check_interval_seconds == seconds


def test_the_smallest_interval_is_a_setting(db_session, settings, user):
    tight = settings.model_copy(update={"monitoring_min_interval_seconds": 60})
    assert watch(db_session, tight, user, interval=60).check_interval_seconds == 60
    with pytest.raises(MonitorError):
        watch(db_session, tight, user, address=OTHER_ADDRESS, interval=59)


@pytest.mark.parametrize("depth", [0, -1, 4])
def test_a_depth_outside_one_to_the_graph_maximum_is_refused(db_session, settings, user, depth):
    with pytest.raises(MonitorError, match="between 1 and 3 hops"):
        watch(db_session, settings, user, depth=depth)


@pytest.mark.parametrize("severity", ["", "low", "SEVERE", "MODERATE"])
def test_only_the_four_severities_can_be_the_threshold(db_session, settings, user, severity):
    with pytest.raises(MonitorError, match="LOW, MEDIUM, HIGH or CRITICAL"):
        watch(db_session, settings, user, min_severity=severity)


def test_an_address_is_watched_once_per_organization_and_chain(db_session, settings, user):
    watch(db_session, settings, user)

    with pytest.raises(MonitorConflict, match="already being monitored"):
        watch(db_session, settings, user)

    # the same address on another chain, and in another organization, are other things to watch
    assert watch(db_session, settings, user, chain="bsc").chain_id == "eip155:56"
    stranger = make_user(db_session, "sam@example.com", Role.ANALYST, "Other Org")
    assert watch(db_session, settings, stranger).organization_id == stranger.organization_id


def test_an_organization_may_watch_only_so_many_addresses(db_session, settings, user):
    small = settings.model_copy(update={"monitoring_max_addresses": 2})
    watch(db_session, small, user, address="0x" + "01" * 20)
    watch(db_session, small, user, address="0x" + "02" * 20)

    with pytest.raises(MonitorConflict, match="at most 2 addresses"):
        watch(db_session, small, user, address="0x" + "03" * 20)

    stranger = make_user(db_session, "sam@example.com", Role.ANALYST, "Other Org")
    assert watch(db_session, small, stranger, address="0x" + "01" * 20)  # their own allowance


def test_a_request_that_loses_the_race_to_register_is_a_conflict_not_a_crash(
    db_session, settings, user, monkeypatch
):
    watch(db_session, settings, user)
    monkeypatch.setattr(store, "find_monitor", lambda *args: None)  # blind to the winner

    with pytest.raises(MonitorConflict, match="already being monitored"):
        watch(db_session, settings, user)

    db_session.rollback()
    assert db_session.scalar(select(MonitoredAddress.id).where(MonitoredAddress.name.is_(None)))


# --- changing how an address is watched --------------------------------------------------------


def test_only_what_is_given_changes_and_the_changes_are_reported(db_session, settings, user):
    row = watch(db_session, settings, user, name="Old")

    changes = service.update(
        db_session,
        row,
        settings=settings,
        name="New",
        check_interval_seconds=1800,
        depth=2,
        min_severity="HIGH",
        now=NOW,
    )

    assert changes == {
        "name": {"from": "Old", "to": "New"},
        "check_interval_seconds": {"from": 21_600, "to": 1800},
        "depth": {"from": 1, "to": 2},
        "min_severity": {"from": "MEDIUM", "to": "HIGH"},
    }
    assert (row.name, row.check_interval_seconds, row.depth, row.min_severity) == (
        "New",
        1800,
        2,
        "HIGH",
    )


def test_giving_what_is_already_so_changes_nothing(db_session, settings, user):
    row = watch(db_session, settings, user, name="Same")
    assert service.update(db_session, row, settings=settings, now=NOW) == {}
    assert (
        service.update(
            db_session,
            row,
            settings=settings,
            name="Same",
            status="active",
            check_interval_seconds=21_600,
            depth=1,
            min_severity="MEDIUM",
            now=NOW,
        )
        == {}
    )


def test_a_name_can_be_taken_away_but_leaving_it_out_keeps_it(db_session, settings, user):
    row = watch(db_session, settings, user, name="Keep")
    service.update(db_session, row, settings=settings, now=NOW)
    assert row.name == "Keep"

    changes = service.update(db_session, row, settings=settings, name=None, now=NOW)

    assert row.name is None and changes == {"name": {"from": "Keep", "to": None}}
    assert service.update(db_session, row, settings=settings, name="  ", now=NOW) == {}


def test_pausing_stops_the_checks_and_resuming_looks_at_once(db_session, settings, user):
    row = watch(db_session, settings, user)
    row.next_check_at = NOW + timedelta(hours=5)

    assert service.update(db_session, row, settings=settings, status="paused", now=NOW) == {
        "status": {"from": "active", "to": "paused"}
    }
    assert row.status == "paused" and row.next_check_at == NOW + timedelta(hours=5)

    later = NOW + timedelta(days=1)
    assert service.update(db_session, row, settings=settings, status="active", now=later) == {
        "status": {"from": "paused", "to": "active"}
    }
    assert row.status == "active" and row.next_check_at == later


def test_resuming_an_address_that_is_not_paused_does_not_move_its_turn(db_session, settings, user):
    row = watch(db_session, settings, user)
    row.next_check_at = NOW + timedelta(hours=5)
    service.update(db_session, row, settings=settings, status="active", now=NOW)
    assert row.next_check_at == NOW + timedelta(hours=5)


@pytest.mark.parametrize(
    "bad",
    [
        {"status": "stopped"},
        {"check_interval_seconds": 10},
        {"depth": 9},
        {"min_severity": "huge"},
        {"name": "x" * 121},
    ],
)
def test_a_change_that_makes_no_sense_is_refused(db_session, settings, user, bad):
    row = watch(db_session, settings, user)
    with pytest.raises(MonitorError):
        service.update(db_session, row, settings=settings, now=NOW, **bad)


# --- queueing checks ----------------------------------------------------------------------------


def test_a_queued_check_names_the_monitor_and_why(db_session, settings, user):
    row = watch(db_session, settings, user)

    job, created = service.queue_check(db_session, row, reason=service.MANUAL)

    assert created and job.job_type == CHECK_ADDRESS
    assert job.payload == {"monitor_id": row.id, "reason": "manual"}
    assert job.organization_id == user.organization_id
    assert job.dedupe_key == f"monitor:{row.id}" and job.status == "QUEUED"


def test_a_person_or_an_event_goes_before_the_routine_rounds(db_session, settings, user):
    priorities = {}
    for reason in (service.SCHEDULED, service.REGISTERED, service.MANUAL, service.WEBHOOK):
        address = "0x" + f"{len(priorities) + 1:02x}" * 20
        row = watch(db_session, settings, user, address=address)
        job, _ = service.queue_check(db_session, row, reason=reason)
        priorities[reason] = job.priority
    assert priorities["scheduled"] < priorities["registered"] <= priorities["manual"]
    assert priorities["manual"] < priorities["webhook"]


def test_one_check_per_monitor_is_waiting_at_a_time(db_session, settings, user):
    row = watch(db_session, settings, user)
    first, created = service.queue_check(db_session, row, reason=service.SCHEDULED)
    again, created_again = service.queue_check(db_session, row, reason=service.WEBHOOK)

    assert created and not created_again and again.id == first.id
    assert len(jobs(db_session)) == 1

    other = watch(db_session, settings, user, address=OTHER_ADDRESS)
    assert service.queue_check(db_session, other, reason=service.SCHEDULED)[1]  # another monitor

    # once the check is over the next one can be queued
    claimed = queue.claim_next(db_session, "worker-1")
    queue.complete(db_session, claimed.id, {})
    assert service.queue_check(db_session, row, reason=service.SCHEDULED)[1]


def test_the_scheduler_queues_what_is_due_and_moves_each_turn_on(db_session, settings, user):
    due = watch(db_session, settings, user, interval=900)
    later = watch(db_session, settings, user, address=OTHER_ADDRESS)
    later.next_check_at = NOW + timedelta(minutes=1)
    paused = watch(db_session, settings, user, address="0x" + "ee" * 20)
    paused.status = "paused"
    db_session.commit()

    assert service.queue_due(db_session, settings, NOW) == 1

    assert [j.payload["monitor_id"] for j in jobs(db_session)] == [due.id]
    assert jobs(db_session)[0].payload["reason"] == "scheduled"
    db_session.refresh(due)
    assert due.next_check_at == NOW + timedelta(seconds=900)  # not due again until then
    assert later.next_check_at == NOW + timedelta(minutes=1)  # untouched
    assert service.queue_due(db_session, settings, NOW) == 0  # nothing more is due now


def test_the_moment_a_monitor_is_due_is_the_moment_it_is_queued(db_session, settings, user):
    row = watch(db_session, settings, user, interval=900)
    row.next_check_at = NOW
    db_session.commit()
    assert service.queue_due(db_session, settings, NOW - timedelta(seconds=1)) == 0
    assert service.queue_due(db_session, settings, NOW) == 1


def test_a_check_that_is_still_waiting_is_not_queued_again_but_the_turn_still_moves(
    db_session, settings, user
):
    row = watch(db_session, settings, user, interval=900)
    service.queue_check(db_session, row, reason=service.REGISTERED)

    assert service.queue_due(db_session, settings, NOW) == 0

    assert len(jobs(db_session)) == 1
    db_session.rollback()  # what was not committed is gone
    db_session.refresh(row)
    assert row.next_check_at == NOW + timedelta(seconds=900)


def test_the_scheduler_takes_a_batch_at_a_time_oldest_first(db_session, settings, user):
    small = settings.model_copy(update={"monitoring_batch": 2})
    rows = []
    for index in range(4):
        row = watch(db_session, settings, user, address="0x" + f"{index + 1:02x}" * 20)
        row.next_check_at = NOW - timedelta(minutes=10 - index)  # row 0 has waited longest
        rows.append(row)
    db_session.commit()

    assert service.queue_due(db_session, small, NOW) == 2
    assert [j.payload["monitor_id"] for j in jobs(db_session)] == [rows[0].id, rows[1].id]

    assert service.queue_due(db_session, small, NOW) == 2  # the next two on the next tick
    assert len(jobs(db_session)) == 4
