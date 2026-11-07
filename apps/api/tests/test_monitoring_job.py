"""The check of one monitored address: what it keeps, when it raises an alert, and how it fails."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.chains.support import find_chain
from app.core.redaction import secrets
from app.database.connection import Sessions
from app.database.models import Alert, AuditLog, MonitoredAddress
from app.database.repositories import risk as risk_store
from app.graph.model import GraphLimits
from app.graph.service import GraphRequestError
from app.monitoring import jobs, service
from app.monitoring.jobs import CHECK_ADDRESS, check_monitored_address, label_of, scheduler
from app.providers.base import ProviderError, ProviderNotConfigured, ProviderUnavailable
from app.services.users import create_user
from app.worker.context import JobContext, NonRetryable, Periodic
from app.worker.handlers import build_default_handlers
from tests.fake_etherscan import ME, OTHER, tx_row
from tests.graph_store import label_address
from tests.helpers import run
from tests.monitoring_helpers import draft
from tests.test_risk_jobs import down, rig_with_window


def sessions(db) -> Sessions:
    return Sessions(db.read, db.write)


def monitor_of(db, rig, *, address=ME, **options) -> int:
    with db.write() as session:
        user = create_user(
            session, email="analyst@example.com", password="pw-pw-pw-pw-1", organization_name="Acme"
        )
        row = service.register(
            session,
            settings=rig.settings,
            organization_id=user.organization_id,
            created_by=user.id,
            chain=find_chain("ethereum"),
            address=address,
            name=options.get("name"),
            check_interval_seconds=options.get("interval"),
            depth=options.get("depth"),
            min_severity=options.get("min_severity"),
            now=datetime.now(UTC),
        )
        session.commit()
        return row.id


def context(rig, db, monitor_id, *, attempt=1, max_attempts=3, payload=None):
    return JobContext(
        job_id=1,
        job_type=CHECK_ADDRESS,
        payload={"monitor_id": monitor_id, "reason": "scheduled"} if payload is None else payload,
        attempt=attempt,
        max_attempts=max_attempts,
        organization_id=None,
        providers=rig.providers,
        settings=rig.settings,
        sessions=sessions(db),
    )


def check(rig, db, monitor_id, **options):
    return run(check_monitored_address(context(rig, db, monitor_id, **options)))


def monitor(db, monitor_id) -> MonitoredAddress:
    with db.read() as session:
        row = session.get(MonitoredAddress, monitor_id)
        session.expunge(row)
        return row


def alerts(db) -> list[Alert]:
    with db.read() as session:
        rows = list(session.scalars(select(Alert).order_by(Alert.id)))
        session.expunge_all()
        return rows


def audit_rows(db, action) -> list[AuditLog]:
    with db.read() as session:
        rows = list(session.scalars(select(AuditLog).where(AuditLog.action == action)))
        session.expunge_all()
        return rows


def change_monitor(db, monitor_id, **fields) -> None:
    with db.write() as session:
        row = session.get(MonitoredAddress, monitor_id)
        for name, value in fields.items():
            setattr(row, name, value)
        session.commit()


def sanction(db, address=OTHER) -> None:
    with db.write() as session:
        label_address(session, address, "SANCTIONS")


@pytest.fixture
def ticking(monkeypatch):
    """The clock the jobs read moves on ten seconds every time it is read, so that two events never
    share a second (an alert's key has the second in it)."""
    start = datetime.now(UTC)
    reads = {"count": 0}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            reads["count"] += 1
            return start + timedelta(seconds=10 * reads["count"])

    monkeypatch.setattr(jobs, "datetime", Clock)
    return reads


def fail_checks(rig, db, number, times):
    for _ in range(times):
        with pytest.raises(ProviderError):
            check(rig, db, number, attempt=3)


def test_the_check_is_registered_with_the_worker():
    assert CHECK_ADDRESS in build_default_handlers().job_types()
    assert CHECK_ADDRESS == "ADDRESS_MONITORING"


# --- the first check ----------------------------------------------------------------------------


def test_the_first_check_of_a_quiet_address_keeps_a_report_and_raises_nothing(committing_db):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig, interval=3600)

    result = check(rig, committing_db, number)

    json.dumps(result)  # a job result is stored as JSON
    assert result["monitor_id"] == number and result["changed"] is True
    assert (result["risk_score"], result["risk_level"]) == (0, "LOW")
    assert result["alert_id"] is None and result["assessment_id"]
    row = monitor(committing_db, number)
    assert row.last_checked_at is not None and row.last_changed_at is not None
    assert row.next_check_at - row.last_checked_at == timedelta(seconds=3600)
    assert (row.last_risk_score, row.last_risk_level) == (0, "LOW")
    assert row.last_assessment_id == result["assessment_id"]
    assert row.known_findings == [] and row.consecutive_failures == 0 and row.last_error is None
    assert row.last_activity_at is not None  # the newest transaction it found
    assert alerts(committing_db) == []

    with committing_db.read() as session:
        kept = risk_store.get_assessment(session, result["assessment_id"])
        assert kept.status == "COMPLETED" and kept.subject == ME and kept.chain_id == "eip155:1"
        assert kept.organization_id == row.organization_id and kept.report["risk_score"] == 0
        assert kept.options == {"monitor_id": number, "depth": 1, "update": True}


def test_an_address_that_is_already_risky_raises_a_baseline_alert_with_its_evidence(committing_db):
    rig = rig_with_window()
    sanction(committing_db)
    number = monitor_of(committing_db, rig)

    result = check(rig, committing_db, number)

    (alert,) = alerts(committing_db)
    assert result["alert_id"] == alert.id and result["risk_level"] == "CRITICAL"
    assert (alert.kind, alert.severity, alert.status) == ("baseline_risk", "CRITICAL", "open")
    assert alert.title == f"{label_of(monitor(committing_db, number))} is already CRITICAL risk"
    assert alert.organization_id == monitor(committing_db, number).organization_id
    assert alert.monitored_address_id == number and (alert.chain_id, alert.address) == (
        "eip155:1",
        ME,
    )
    assert alert.assessment_id == result["assessment_id"] and alert.previous_assessment_id is None
    assert alert.dedupe_key == f"{number}:{result['assessment_id']}:baseline_risk"
    assert alert.evidence["reasons"][0]["code"] == "baseline"
    assert alert.evidence["current"]["level"] == "CRITICAL" and alert.evidence["previous"] is None
    assert alert.evidence["findings"][0]["rule_id"] == "AML-001"
    assert alert.assignee_id is None and alert.resolution is None and alert.closed_at is None
    row = monitor(committing_db, number)
    assert row.known_findings and all(key.startswith("AML-") for key in row.known_findings)


def test_the_alert_is_in_the_audit_log_as_the_systems_doing(committing_db):
    rig = rig_with_window()
    sanction(committing_db)
    number = monitor_of(committing_db, rig)
    check(rig, committing_db, number)

    (entry,) = audit_rows(committing_db, "alert.create")
    (alert,) = alerts(committing_db)
    assert (entry.actor_type, entry.actor_label, entry.outcome) == (
        "system",
        "monitoring",
        "success",
    )
    assert (entry.target_type, entry.target_id) == ("alert", str(alert.id))
    assert entry.organization_id == alert.organization_id
    assert entry.details == {
        "kind": "baseline_risk",
        "severity": "CRITICAL",
        "monitor_id": number,
        "address": ME,
    }


# --- later checks -------------------------------------------------------------------------------


def test_a_check_that_finds_what_it_found_before_changes_nothing_but_the_time(committing_db):
    rig = rig_with_window()
    sanction(committing_db)
    number = monitor_of(committing_db, rig)
    first = check(rig, committing_db, number)
    before = monitor(committing_db, number)
    asked = len(rig.etherscan.requests)

    second = check(rig, committing_db, number)

    assert second["changed"] is False and second["new_activity"] is False
    assert second["assessment_id"] is None and second["alert_id"] is None
    assert len(rig.etherscan.requests) == asked  # the stored history was fresh
    after = monitor(committing_db, number)
    assert after.last_assessment_id == first["assessment_id"]
    assert after.last_changed_at == before.last_changed_at
    assert after.last_checked_at > before.last_checked_at
    assert after.next_check_at > before.next_check_at
    assert len(alerts(committing_db)) == 1
    with committing_db.read() as session:
        assert (
            len(
                risk_store.completed_assessments(
                    session, after.organization_id, "eip155:1", ME, limit=10
                )
            )
            == 1
        )


def test_a_finding_that_appears_later_raises_an_alert_that_links_both_reports(committing_db):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig)
    first = check(rig, committing_db, number)
    assert alerts(committing_db) == []

    sanction(committing_db)
    second = check(rig, committing_db, number)

    (alert,) = alerts(committing_db)
    assert second["alert_id"] == alert.id and second["changed"] is True
    assert (alert.kind, alert.severity) == ("new_finding", "CRITICAL")
    assert alert.assessment_id == second["assessment_id"] != first["assessment_id"]
    assert alert.previous_assessment_id == first["assessment_id"]
    assert [r["code"] for r in alert.evidence["reasons"]] == ["new_finding", "level_up", "score_up"]
    assert alert.evidence["previous"] == {"score": 0, "level": "LOW"}
    assert alert.evidence["current"] == {"score": 100, "level": "CRITICAL"}
    assert alert.dedupe_key == f"{number}:{second['assessment_id']}:new_finding"
    assert monitor(committing_db, number).last_assessment_id == second["assessment_id"]


def test_the_same_finding_is_not_reported_again_at_the_next_check(committing_db):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig)
    check(rig, committing_db, number)
    sanction(committing_db)
    check(rig, committing_db, number)

    third = check(rig, committing_db, number)

    assert third["alert_id"] is None and third["changed"] is False
    assert len(alerts(committing_db)) == 1


def test_a_finding_below_the_threshold_is_kept_in_the_report_but_raises_no_alert(committing_db):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig, min_severity="CRITICAL")
    check(rig, committing_db, number)
    with committing_db.write() as session:
        label_address(session, OTHER, "MIXER")

    result = check(rig, committing_db, number)

    assert result["changed"] is True and result["assessment_id"]  # the report is still kept
    assert result["alert_id"] is None and alerts(committing_db) == []
    assert monitor(committing_db, number).known_findings  # and it is now known


def test_an_address_that_never_changes_still_gets_a_recent_report_once_in_a_while(committing_db):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig)
    first = check(rig, committing_db, number)
    change_monitor(committing_db, number, last_changed_at=datetime.now(UTC) - timedelta(days=6))
    assert check(rig, committing_db, number)["changed"] is False

    change_monitor(committing_db, number, last_changed_at=datetime.now(UTC) - timedelta(days=8))
    result = check(rig, committing_db, number)

    assert result["changed"] is True and result["alert_id"] is None
    assert result["assessment_id"] not in (None, first["assessment_id"])
    assert monitor(committing_db, number).last_changed_at > datetime.now(UTC) - timedelta(minutes=5)


def test_the_depth_of_the_monitor_is_the_depth_of_the_check(committing_db, monkeypatch):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig, depth=2)
    seen = []
    real = jobs.screen_address

    async def spy(*args, **kwargs):
        seen.append(args[5])  # the limits of the graph
        return await real(*args, **kwargs)

    monkeypatch.setattr(jobs, "screen_address", spy)

    check(rig, committing_db, number)

    assert [limits.depth for limits in seen] == [2]


def test_an_address_nothing_is_stored_about_is_not_news_at_every_check(committing_db, monkeypatch):
    rig = rig_with_window()
    monkeypatch.setattr(jobs.store, "latest_activity_at", lambda *args: None)
    number = monitor_of(committing_db, rig)
    first = check(rig, committing_db, number)
    assert first["changed"] is True  # the first check always keeps a report

    second = check(rig, committing_db, number)

    assert second["new_activity"] is False and second["changed"] is False
    assert monitor(committing_db, number).last_activity_at is None


def test_a_new_finding_is_a_change_even_when_the_score_and_the_level_cannot_move(committing_db):
    third = "0x" + "d5" * 20
    rig = rig_with_window()
    rig.etherscan.add("txlist", tx_row(14, third, ME, value="4"))  # a second counterparty
    sanction(committing_db)  # the score is 100 and the level CRITICAL from the start
    number = monitor_of(committing_db, rig, min_severity="CRITICAL")
    check(rig, committing_db, number)
    with committing_db.write() as session:
        label_address(session, third, "MIXER", ref="t2")

    result = check(rig, committing_db, number)

    assert (result["risk_score"], result["risk_level"]) == (100, "CRITICAL")
    assert result["alert_id"] is None  # below the threshold: no alert ...
    assert result["changed"] is True and result["assessment_id"]  # ... but the report is kept
    assert len(monitor(committing_db, number).known_findings) >= 2


def test_the_heartbeat_is_a_setting(committing_db):
    rig = rig_with_window()
    rig.settings = rig.settings.model_copy(update={"monitoring_heartbeat_days": 1})
    number = monitor_of(committing_db, rig)
    check(rig, committing_db, number)
    change_monitor(committing_db, number, last_changed_at=datetime.now(UTC) - timedelta(hours=25))

    assert check(rig, committing_db, number)["changed"] is True


def test_new_activity_on_the_address_is_a_change_even_when_the_risk_is_the_same(committing_db):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig)
    check(rig, committing_db, number)
    newest = monitor(committing_db, number).last_activity_at
    change_monitor(committing_db, number, last_activity_at=newest - timedelta(days=30))

    result = check(rig, committing_db, number)

    assert result["new_activity"] is True and result["changed"] is True
    assert result["alert_id"] is None
    assert monitor(committing_db, number).last_activity_at == newest


def test_a_score_that_moves_without_a_new_finding_still_gets_a_report(committing_db):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig)
    check(rig, committing_db, number)
    change_monitor(committing_db, number, last_risk_score=5)  # it was 5, it is now 0

    result = check(rig, committing_db, number)

    assert result["changed"] is True and result["alert_id"] is None
    assert monitor(committing_db, number).last_risk_score == 0


# --- monitors the check cannot or should not run for ---------------------------------------------


def test_an_address_that_is_no_longer_monitored_is_not_checked(committing_db):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig)
    change_monitor(committing_db, number, status="paused")

    assert "not being monitored" in check(rig, committing_db, number)["skipped"]
    assert check(rig, committing_db, number + 1000)["skipped"]
    assert rig.etherscan.requests == [] and rig.alchemy.requests == []
    assert monitor(committing_db, number).last_checked_at is None


def test_a_monitor_removed_while_it_was_being_checked_is_left_alone(committing_db):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig)
    with committing_db.write() as session:
        session.delete(session.get(MonitoredAddress, number))
        session.commit()
    report = run(
        jobs.screen_address(
            sessions(committing_db),
            rig.settings,
            rig.providers,
            find_chain("ethereum"),
            ME,
            GraphLimits.from_settings(rig.settings, depth=1),
            update=True,
        )
    )

    result = jobs._checked(sessions(committing_db), rig.settings, number, report, datetime.now(UTC))

    assert "removed while it was being checked" in result["skipped"]
    assert alerts(committing_db) == []


@pytest.mark.parametrize(
    "payload", [{}, {"monitor_id": "1"}, {"monitor_id": True}, {"monitor_id": 1.5}, {"id": 1}]
)
def test_a_payload_that_names_no_monitor_is_refused(committing_db, payload):
    rig = rig_with_window()
    monitor_of(committing_db, rig)

    with pytest.raises(NonRetryable, match="monitor_id"):
        check(rig, committing_db, 1, payload=payload)


def test_a_worker_without_a_database_refuses(committing_db):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig)
    bare = JobContext(
        1, CHECK_ADDRESS, {"monitor_id": number}, 1, 3, None, rig.providers, rig.settings, None
    )

    with pytest.raises(NonRetryable, match="no database"):
        run(check_monitored_address(bare))


# --- when a check goes wrong --------------------------------------------------------------------


def test_a_provider_failure_with_attempts_left_is_left_to_the_retry(committing_db):
    rig = rig_with_window()
    down(rig)
    number = monitor_of(committing_db, rig)

    with pytest.raises(ProviderError):
        check(rig, committing_db, number, attempt=1)

    row = monitor(committing_db, number)
    assert row.consecutive_failures == 0 and row.last_error is None and row.last_checked_at is None


def test_a_failure_on_the_last_attempt_counts_against_the_monitor_and_says_why(committing_db):
    rig = rig_with_window()
    down(rig)
    number = monitor_of(committing_db, rig, interval=1800)

    with pytest.raises(ProviderError):
        check(rig, committing_db, number, attempt=3)

    row = monitor(committing_db, number)
    assert row.consecutive_failures == 1 and "503" in row.last_error
    assert row.last_checked_at is None  # it did not get to look
    assert row.next_check_at > datetime.now(UTC) + timedelta(minutes=29)  # try again in its time
    assert alerts(committing_db) == []  # one failure is not yet a pattern


def test_a_failure_that_retrying_cannot_cure_counts_at_once(committing_db, monkeypatch):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig)

    async def refused(*args, **kwargs):
        raise ProviderNotConfigured("etherscan", "no key")

    monkeypatch.setattr(jobs, "screen_address", refused)

    with pytest.raises(ProviderNotConfigured):
        check(rig, committing_db, number, attempt=1)

    row = monitor(committing_db, number)
    assert row.consecutive_failures == 1 and row.last_error


def test_a_request_the_graph_refuses_fails_the_check_for_good(committing_db, monkeypatch):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig)

    async def refused(*args, **kwargs):
        raise GraphRequestError("the window is too wide")

    monkeypatch.setattr(jobs, "screen_address", refused)

    with pytest.raises(NonRetryable, match="the window is too wide"):
        check(rig, committing_db, number)

    row = monitor(committing_db, number)
    assert row.consecutive_failures == 1 and row.last_error == "the window is too wide"


def test_a_chain_this_platform_no_longer_reads_fails_the_check_for_good(committing_db, monkeypatch):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig)
    monkeypatch.setattr(jobs, "find_chain", lambda name: None)

    with pytest.raises(NonRetryable, match="not a chain this platform reads"):
        check(rig, committing_db, number)

    assert monitor(committing_db, number).consecutive_failures == 1
    assert rig.etherscan.requests == []


def test_what_is_kept_of_an_error_is_scrubbed_and_short(committing_db, monkeypatch):
    rig = rig_with_window()
    secrets.add("sekrit-key-0123456789")
    number = monitor_of(committing_db, rig)

    async def failing(*args, **kwargs):
        raise ProviderUnavailable("etherscan", "HTTP 503 sekrit-key-0123456789 " + "x" * 900)

    monkeypatch.setattr(jobs, "screen_address", failing)

    with pytest.raises(ProviderError):
        check(rig, committing_db, number, attempt=3)

    error = monitor(committing_db, number).last_error
    assert error and "sekrit-key-0123456789" not in error and len(error) <= jobs.MAX_ERROR_LENGTH


def test_a_watch_that_keeps_failing_raises_one_alert_and_closes_it_when_it_works_again(
    committing_db,
):
    rig = rig_with_window()
    down(rig)
    number = monitor_of(committing_db, rig)

    for _ in range(2):
        with pytest.raises(ProviderError):
            check(rig, committing_db, number, attempt=3)
    assert alerts(committing_db) == []

    with pytest.raises(ProviderError):
        check(rig, committing_db, number, attempt=3)
    (alert,) = alerts(committing_db)
    assert (alert.kind, alert.severity, alert.status) == ("monitoring_degraded", "MEDIUM", "open")
    assert "3 checks in a row" in alert.summary and "The last error:" in alert.summary
    assert alert.assessment_id is None and alert.monitored_address_id == number

    for _ in range(2):  # more failures do not pile up more alerts
        with pytest.raises(ProviderError):
            check(rig, committing_db, number, attempt=3)
    assert len(alerts(committing_db)) == 1
    assert monitor(committing_db, number).consecutive_failures == 5

    rig.etherscan.queued, rig.alchemy.queued = [], []  # the providers are back
    result = check(rig, committing_db, number)

    assert result["changed"] is True
    (alert,) = alerts(committing_db)
    assert (alert.status, alert.resolution) == ("closed", "recovered")
    assert alert.closed_at is not None and alert.closed_by is None
    row = monitor(committing_db, number)
    assert row.consecutive_failures == 0 and row.last_error is None
    (entry,) = audit_rows(committing_db, "alert.update")
    assert (entry.actor_type, entry.actor_label) == ("system", "monitoring")
    assert entry.details["changes"] == {"status": {"from": "open", "to": "closed"}}
    assert entry.details["reason"] == "recovered"


def test_more_failures_do_not_pile_up_alerts_and_a_new_run_after_a_recovery_is_a_new_alert(
    committing_db, ticking, monkeypatch
):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig)
    real = jobs.screen_address
    broken = {"now": True}

    async def sometimes_down(*args, **kwargs):
        if broken["now"]:
            raise ProviderUnavailable("etherscan", "HTTP 503")
        return await real(*args, **kwargs)

    monkeypatch.setattr(jobs, "screen_address", sometimes_down)

    fail_checks(rig, committing_db, number, 3)
    assert [a.status for a in alerts(committing_db)] == ["open"]
    fail_checks(rig, committing_db, number, 2)
    assert [a.status for a in alerts(committing_db)] == ["open"]  # still the one

    broken["now"] = False
    check(rig, committing_db, number)  # the providers are back: the system closes it
    assert [a.status for a in alerts(committing_db)] == ["closed"]

    broken["now"] = True
    fail_checks(rig, committing_db, number, 3)
    assert [a.status for a in alerts(committing_db)] == ["closed", "open"]


def test_a_monitoring_alert_somebody_acknowledged_still_counts_as_the_open_one(
    committing_db, ticking
):
    rig = rig_with_window()
    down(rig)
    number = monitor_of(committing_db, rig)
    fail_checks(rig, committing_db, number, 3)
    with committing_db.write() as session:
        session.scalar(select(Alert)).status = "acknowledged"
        session.commit()

    fail_checks(rig, committing_db, number, 2)
    assert [a.status for a in alerts(committing_db)] == ["acknowledged"]  # no second alert

    rig.etherscan.queued, rig.alchemy.queued = [], []
    check(rig, committing_db, number)
    (alert,) = alerts(committing_db)
    assert (alert.status, alert.resolution) == ("closed", "recovered")
    (entry,) = audit_rows(committing_db, "alert.update")
    assert entry.details["changes"] == {"status": {"from": "acknowledged", "to": "closed"}}


def test_a_degraded_alert_that_a_person_reopened_is_left_to_them(committing_db):
    rig = rig_with_window()
    down(rig)
    number = monitor_of(committing_db, rig)
    for _ in range(3):
        with pytest.raises(ProviderError):
            check(rig, committing_db, number, attempt=3)
    rig.etherscan.queued, rig.alchemy.queued = [], []
    check(rig, committing_db, number)  # recovered: the alert is closed by the system
    with committing_db.write() as session:
        reopened = session.scalar(select(Alert))
        reopened.status, reopened.resolution, reopened.closed_at = "open", None, None
        session.commit()

    check(rig, committing_db, number)  # healthy, and nothing has failed since

    (alert,) = alerts(committing_db)
    assert alert.status == "open"


def test_the_number_of_failures_that_raises_the_alert_is_a_setting(committing_db):
    rig = rig_with_window()
    rig.settings = rig.settings.model_copy(update={"monitoring_degraded_after": 1})
    down(rig)
    number = monitor_of(committing_db, rig)

    with pytest.raises(ProviderError):
        check(rig, committing_db, number, attempt=3)

    assert [a.kind for a in alerts(committing_db)] == ["monitoring_degraded"]


def test_a_recovery_with_nothing_to_close_closes_nothing(committing_db):
    rig = rig_with_window()
    down(rig)
    number = monitor_of(committing_db, rig)
    with pytest.raises(ProviderError):
        check(rig, committing_db, number, attempt=3)
    rig.etherscan.queued, rig.alchemy.queued = [], []

    check(rig, committing_db, number)

    assert alerts(committing_db) == [] and audit_rows(committing_db, "alert.update") == []
    assert monitor(committing_db, number).consecutive_failures == 0


# --- the alert row ------------------------------------------------------------------------------


def test_an_alert_with_a_key_that_exists_is_not_raised_twice(committing_db):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig)

    with committing_db.write() as session:
        row = session.get(MonitoredAddress, number)
        first = jobs._raise(session, row, draft(), "k", None, None)
        session.commit()
        second = jobs._raise(session, row, draft(), "k", None, None)
        session.commit()
        other_kind = jobs._raise(session, row, draft(kind="risk_increase"), "k", None, None)
        session.commit()

    assert first is not None and second is None and other_kind not in (None, first)
    assert len(alerts(committing_db)) == 2
    assert len(audit_rows(committing_db, "alert.create")) == 2


def test_an_alert_title_and_key_fit_their_columns(committing_db):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig)
    long_draft = replace(draft(), title="t" * 500)

    with committing_db.write() as session:
        row = session.get(MonitoredAddress, number)
        jobs._raise(session, row, long_draft, "k" * 300, None, None)
        session.commit()

    (alert,) = alerts(committing_db)
    assert len(alert.title) == 200 and len(alert.dedupe_key) == 120


def test_an_address_is_named_the_way_the_team_does_or_in_short(committing_db):
    rig = rig_with_window()
    named = monitor_of(committing_db, rig, name="Treasury")
    assert label_of(monitor(committing_db, named)) == "Treasury"
    row = MonitoredAddress(address="0x" + "ab" * 20, name=None)
    assert label_of(row) == "0xabababab..ababab"
    assert label_of(MonitoredAddress(address="short", name=None)) == "short"
    assert label_of(MonitoredAddress(address="x" * 16, name="")) == "x" * 16


# --- the worker's tick --------------------------------------------------------------------------


def test_the_tick_queues_the_checks_that_are_due(committing_db):
    rig = rig_with_window()
    number = monitor_of(committing_db, rig)
    change_monitor(committing_db, number, next_check_at=datetime.now(UTC) - timedelta(minutes=1))

    periodic = scheduler(rig.settings, committing_db.write)

    assert isinstance(periodic, Periodic) and periodic.name == "monitoring"
    assert periodic.every == rig.settings.monitoring_tick_seconds == 30
    assert periodic.run() == 1
    assert periodic.run() == 0  # its turn has moved on
    assert monitor(committing_db, number).next_check_at > datetime.now(UTC)
