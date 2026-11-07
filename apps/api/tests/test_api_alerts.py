"""The alerts endpoints: reading them, working them, and what each change leaves behind."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.access import apikeys
from app.access.audit import Actor, record
from app.access.permissions import Role
from app.api.v1.alerts import MAX_ACTIVITY
from app.database.models import Alert, AuditLog, MonitoredAddress, User
from app.database.repositories import risk as risk_store
from tests.access_helpers import make_user, person
from tests.monitoring_helpers import OTHER_ADDRESS, alert_for, watch

URL = "/api/v1/alerts"


@pytest.fixture
def world(client, db_session, settings):
    ana, analyst = person(client, db_session, Role.ANALYST)
    _, viewer = person(client, db_session, Role.VIEWER)
    monitor = watch(db_session, settings, ana)
    db_session.rollback()
    return SimpleNamespace(
        ana=ana, analyst=analyst, viewer=viewer, monitor=monitor, org=ana.organization_id
    )


def new_alert(db_session, world, **fields):
    alert = alert_for(db_session, world.monitor, **fields)
    db_session.rollback()
    return alert


def rows(db_session, model):
    """Everything in a table, detached, with the session's transaction ended again (a write
    session that has read holds SQLite's write lock)."""
    db_session.rollback()
    found = list(
        db_session.scalars(
            select(model).order_by(model.id).execution_options(populate_existing=True)
        )
    )
    db_session.expunge_all()
    db_session.rollback()
    return found


def listed(client, headers, **query):
    response = client.get(URL, params=query, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def ids(payload):
    return [item["id"] for item in payload["items"]]


# --- the list -----------------------------------------------------------------------------------


def test_the_list_is_newest_first_and_each_item_says_what_it_is_about(client, db_session, world):
    first = new_alert(db_session, world, key="1", title="First")
    second = new_alert(db_session, world, key="2", title="Second", severity="CRITICAL")

    body = listed(client, world.viewer)

    assert body["total"] == 2 and ids(body) == [second.id, first.id]
    item = body["items"][0]
    assert item == {
        "id": second.id,
        "kind": "new_finding",
        "severity": "CRITICAL",
        "status": "open",
        "resolution": None,
        "title": "Second",
        "chain": "ethereum",
        "chain_id": "eip155:1",
        "address": world.monitor.address,
        "display": item["display"],
        "monitor_id": world.monitor.id,
        "assignee": None,
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
        "closed_at": None,
    }
    assert item["display"].lower() == item["address"] and item["created_at"]
    assert "evidence" not in item and "summary" not in item  # read one alert for those


def test_the_list_pages(client, db_session, world):
    made = [new_alert(db_session, world, key=str(n)) for n in range(5)]
    newest_first = [a.id for a in reversed(made)]

    page = listed(client, world.viewer, limit=2, offset=1)

    assert ids(page) == newest_first[1:3]
    assert (page["total"], page["limit"], page["offset"]) == (5, 2, 1)
    assert client.get(URL, params={"limit": 0}, headers=world.viewer).status_code == 422
    assert client.get(URL, params={"limit": 201}, headers=world.viewer).status_code == 422
    assert client.get(URL, params={"offset": -1}, headers=world.viewer).status_code == 422


def test_the_list_can_be_narrowed(client, db_session, settings, world):
    other_monitor = watch(db_session, settings, world.ana, address=OTHER_ADDRESS)
    open_high = new_alert(db_session, world, key="a", severity="HIGH")
    ack_low = new_alert(db_session, world, key="b", severity="LOW", status="acknowledged")
    closed = new_alert(
        db_session, world, key="c", severity="HIGH", status="closed", resolution="false_positive"
    )
    degraded = alert_for(
        db_session, other_monitor, key="d", kind="monitoring_degraded", severity="MEDIUM"
    )
    db_session.rollback()

    assert ids(listed(client, world.viewer, status="open")) == [degraded.id, open_high.id]
    assert ids(listed(client, world.viewer, status="acknowledged")) == [ack_low.id]
    assert ids(listed(client, world.viewer, status="closed")) == [closed.id]
    assert ids(listed(client, world.viewer, status="unresolved")) == [
        degraded.id,
        ack_low.id,
        open_high.id,
    ]
    assert ids(listed(client, world.viewer, severity="LOW")) == [ack_low.id]
    assert ids(listed(client, world.viewer, kind="monitoring_degraded")) == [degraded.id]
    assert ids(listed(client, world.viewer, monitor_id=other_monitor.id)) == [degraded.id]
    assert ids(listed(client, world.viewer, status="unresolved", severity="HIGH")) == [open_high.id]
    for bad in (
        {"status": "done"},
        {"severity": "SEVERE"},
        {"monitor_id": 0},
        {"assignee_id": "x"},
    ):
        assert client.get(URL, params=bad, headers=world.viewer).status_code == 422


def test_the_list_can_show_whose_alerts_they_are(client, db_session, world):
    mine = new_alert(db_session, world, key="a", assignee_id=world.ana.id)
    new_alert(db_session, world, key="b")
    colleague = make_user(db_session, "bob@example.com", Role.ANALYST)
    theirs = new_alert(db_session, world, key="c", assignee_id=colleague.id)

    assert ids(listed(client, world.analyst, mine="true")) == [mine.id]
    assert ids(listed(client, world.analyst, assignee_id=colleague.id)) == [theirs.id]
    assert listed(client, world.analyst, mine="true")["items"][0]["assignee"] == {
        "id": world.ana.id,
        "email": "analyst-acme@example.com",
    }
    assert listed(client, world.analyst, mine="false")["total"] == 3


def test_mine_for_a_key_is_the_person_who_made_the_key(client, db_session, world):
    boss = make_user(db_session, "boss@example.com", Role.ADMIN)
    made = apikeys.create_key(db_session, creator=boss, name="Bot", role=Role.ANALYST)
    db_session.commit()
    db_session.rollback()
    theirs = new_alert(db_session, world, key="a", assignee_id=boss.id)
    new_alert(db_session, world, key="b")

    assert ids(listed(client, {"X-API-Key": made.secret}, mine="true")) == [theirs.id]


def test_another_organizations_alerts_are_not_in_the_list(client, db_session, settings, world):
    theirs_user = make_user(db_session, "sam@example.com", Role.ANALYST, "Other Org")
    theirs = alert_for(db_session, watch(db_session, settings, theirs_user, address=OTHER_ADDRESS))
    db_session.rollback()
    mine = new_alert(db_session, world)
    _, outsider = person(client, db_session, Role.VIEWER, organization="Other Org", email="v@o.com")

    assert ids(listed(client, world.viewer)) == [mine.id]
    assert ids(listed(client, outsider)) == [theirs.id]


# --- the summary --------------------------------------------------------------------------------


def test_the_summary_counts_by_status_and_the_unresolved_by_severity(client, db_session, world):
    new_alert(db_session, world, key="1", severity="HIGH")
    new_alert(db_session, world, key="2", severity="HIGH")
    new_alert(db_session, world, key="3", severity="CRITICAL", status="acknowledged")
    new_alert(
        db_session, world, key="4", severity="LOW", status="closed", resolution="accepted_risk"
    )

    body = client.get(f"{URL}/summary", headers=world.viewer).json()

    assert body == {
        "success": True,
        "by_status": {"open": 2, "acknowledged": 1, "closed": 1},
        "unresolved": 3,
        "unresolved_by_severity": {"HIGH": 2, "CRITICAL": 1},
    }


def test_with_no_alerts_the_summary_is_zeros_and_summary_is_not_an_alert_id(client, world):
    body = client.get(f"{URL}/summary", headers=world.viewer).json()
    assert body["by_status"] == {"open": 0, "acknowledged": 0, "closed": 0}
    assert body["unresolved"] == 0 and body["unresolved_by_severity"] == {}


def test_the_summary_is_this_organizations_alone(client, db_session, settings, world):
    theirs_user = make_user(db_session, "sam@example.com", Role.ANALYST, "Other Org")
    alert_for(db_session, watch(db_session, settings, theirs_user))
    db_session.rollback()
    _, outsider = person(client, db_session, Role.VIEWER, organization="Other Org", email="v@o.com")

    assert client.get(f"{URL}/summary", headers=world.viewer).json()["unresolved"] == 0
    assert client.get(f"{URL}/summary", headers=outsider).json()["unresolved"] == 1


# --- one alert ----------------------------------------------------------------------------------


def test_an_alert_comes_with_its_evidence_its_reports_and_the_monitor(client, db_session, world):
    first = risk_store.create_assessment(
        db_session,
        organization_id=world.org,
        subject_type="address",
        chain_id="eip155:1",
        subject=world.monitor.address,
        options={},
    )
    second = risk_store.create_assessment(
        db_session,
        organization_id=world.org,
        subject_type="address",
        chain_id="eip155:1",
        subject=world.monitor.address,
        options={},
    )
    db_session.commit()
    alert = new_alert(
        db_session,
        world,
        assessment_id=second.id,
        previous_assessment_id=first.id,
        evidence={"reasons": [{"code": "new_finding", "detail": "1 new finding"}]},
    )

    body = client.get(f"{URL}/{alert.id}", headers=world.viewer).json()["alert"]

    assert body["id"] == alert.id and body["summary"].startswith("1 new finding.")
    assert body["evidence"] == {"reasons": [{"code": "new_finding", "detail": "1 new finding"}]}
    assert body["screening_id"] == f"scr_{second.id}"
    assert body["previous_screening_id"] == f"scr_{first.id}"
    assert body["monitor"] == {"id": world.monitor.id, "name": None, "status": "active"}
    assert body["closed_by"] is None and body["activity"] == []


def test_an_alert_whose_monitor_was_removed_still_reads(client, db_session, world):
    alert = new_alert(db_session, world)
    db_session.delete(db_session.get(MonitoredAddress, world.monitor.id))
    db_session.commit()
    db_session.rollback()

    body = client.get(f"{URL}/{alert.id}", headers=world.viewer).json()["alert"]

    assert body["monitor"] is None and body["monitor_id"] is None
    assert body["address"] == world.monitor.address  # the alert kept what it was about


def test_the_activity_is_what_the_audit_log_says_about_this_alert_and_nothing_else(
    client, db_session, world
):
    alert = new_alert(db_session, world)
    system = Actor("system", None, "monitoring")
    record(
        db_session,
        actor=system,
        action="alert.create",
        target_type="alert",
        target_id=alert.id,
        details={"kind": "new_finding"},
        organization_id=world.org,
    )
    record(  # something else with the same number
        db_session,
        actor=system,
        action="monitor.check",
        target_type="monitor",
        target_id=alert.id,
        organization_id=world.org,
    )
    db_session.commit()
    db_session.rollback()
    client.patch(
        f"{URL}/{alert.id}",
        json={"status": "acknowledged", "note": "Looking"},
        headers=world.analyst,
    )

    activity = client.get(f"{URL}/{alert.id}", headers=world.viewer).json()["alert"]["activity"]

    assert [a["action"] for a in activity] == ["alert.create", "alert.update"]
    assert activity[0]["actor"] == {"type": "system", "id": None, "label": "monitoring"}
    assert activity[1]["actor"]["label"] == "analyst-acme@example.com"
    assert activity[1]["details"]["note"] == "Looking"
    assert set(activity[0]) == {"at", "actor", "action", "details"}  # no address, no user agent


def test_the_activity_shown_is_bounded_and_starts_from_the_beginning(client, db_session, world):
    alert = new_alert(db_session, world)
    system = Actor("system", None, "monitoring")
    for number in range(MAX_ACTIVITY + 5):
        record(
            db_session,
            actor=system,
            action="alert.update",
            target_type="alert",
            target_id=alert.id,
            details={"n": number},
            organization_id=world.org,
        )
    db_session.commit()
    db_session.rollback()

    activity = client.get(f"{URL}/{alert.id}", headers=world.viewer).json()["alert"]["activity"]

    assert len(activity) == MAX_ACTIVITY
    assert activity[0]["details"] == {"n": 0} and activity[-1]["details"] == {"n": MAX_ACTIVITY - 1}


def test_an_alert_of_another_organization_or_that_is_not_there_is_a_404(
    client, db_session, settings, world
):
    theirs_user = make_user(db_session, "sam@example.com", Role.ANALYST, "Other Org")
    theirs = alert_for(db_session, watch(db_session, settings, theirs_user))
    db_session.rollback()

    for number in (theirs.id, 10**6):
        response = client.get(f"{URL}/{number}", headers=world.viewer)
        assert response.status_code == 404 and response.json()["error"]["code"] == "alert_not_found"
    assert client.get(f"{URL}/abc", headers=world.viewer).status_code == 422


# --- working an alert ---------------------------------------------------------------------------


def patch(client, headers, alert, **body):
    return client.patch(f"{URL}/{alert.id}", json=body, headers=headers)


def entries(db_session, action="alert.update"):
    return [r for r in rows(db_session, AuditLog) if r.action == action]


def test_an_analyst_acknowledges_an_alert_and_it_is_audited(client, db_session, world):
    alert = new_alert(db_session, world)

    response = patch(client, world.analyst, alert, status="acknowledged")

    assert response.status_code == 200
    body = response.json()["alert"]
    assert body["status"] == "acknowledged" and body["resolution"] is None
    (entry,) = entries(db_session)
    assert (entry.actor_id, entry.outcome) == (world.ana.id, "success")
    assert (entry.target_type, entry.target_id) == ("alert", str(alert.id))
    assert entry.details == {
        "address": alert.address,
        "changes": {"status": {"from": "open", "to": "acknowledged"}},
    }
    assert body["activity"][-1]["action"] == "alert.update"


def test_closing_records_how_it_ended_who_closed_it_and_the_reason(client, db_session, world):
    alert = new_alert(db_session, world)

    response = patch(
        client,
        world.analyst,
        alert,
        status="closed",
        resolution="false_positive",
        note="  The exchange's own deposit address.  ",
    )

    body = response.json()["alert"]
    assert (body["status"], body["resolution"]) == ("closed", "false_positive")
    assert body["closed_at"] and body["closed_by"] == {
        "id": world.ana.id,
        "email": "analyst-acme@example.com",
    }
    (entry,) = entries(db_session)
    assert entry.details["note"] == "The exchange's own deposit address."
    assert entry.details["changes"] == {
        "status": {"from": "open", "to": "closed"},
        "resolution": {"from": None, "to": "false_positive"},
    }


def test_a_closed_alert_is_reopened_and_forgets_how_it_ended(client, db_session, world):
    alert = new_alert(db_session, world)
    patch(client, world.analyst, alert, status="closed", resolution="true_positive")

    body = patch(client, world.analyst, alert, status="open").json()["alert"]

    assert body["status"] == "open" and body["resolution"] is None
    assert body["closed_at"] is None and body["closed_by"] is None
    assert [e.details["changes"].get("status", {}).get("to") for e in entries(db_session)] == [
        "closed",
        "open",
    ]


def test_an_alert_is_handed_to_a_colleague_and_taken_back(client, db_session, world):
    colleague = make_user(db_session, "bob@example.com", Role.ANALYST)
    alert = new_alert(db_session, world)

    body = patch(client, world.analyst, alert, assignee_id=colleague.id).json()["alert"]
    assert body["assignee"] == {"id": colleague.id, "email": "bob@example.com"}
    assert body["status"] == "open"

    body = patch(client, world.analyst, alert, assignee_id=None).json()["alert"]
    assert body["assignee"] is None
    changes = [e.details["changes"] for e in entries(db_session)]
    assert changes == [
        {"assignee_id": {"from": None, "to": colleague.id}},
        {"assignee_id": {"from": colleague.id, "to": None}},
    ]


def test_leaving_the_assignee_out_of_a_change_keeps_it(client, db_session, world):
    alert = new_alert(db_session, world, assignee_id=world.ana.id)

    body = patch(client, world.analyst, alert, status="acknowledged").json()["alert"]

    assert body["assignee"] == {"id": world.ana.id, "email": "analyst-acme@example.com"}


def test_a_change_that_changes_nothing_is_not_audited(client, db_session, world):
    alert = new_alert(db_session, world)

    response = patch(client, world.analyst, alert, status="open")

    assert response.status_code == 200 and response.json()["alert"]["status"] == "open"
    assert entries(db_session) == []


def test_a_note_alone_is_a_comment_in_the_activity(client, db_session, world):
    alert = new_alert(db_session, world)

    response = patch(client, world.analyst, alert, note="Asked the customer.")

    assert response.status_code == 200 and response.json()["alert"]["status"] == "open"
    (entry,) = entries(db_session)
    assert entry.details == {"address": alert.address, "changes": {}, "note": "Asked the customer."}


@pytest.mark.parametrize(
    "body,status,code",
    [
        ({}, 422, "nothing_to_change"),
        ({"note": ""}, 422, "nothing_to_change"),
        ({"note": "   "}, 422, "nothing_to_change"),
        ({"status": "closed"}, 422, "invalid_alert_change"),  # how did it end?
        ({"resolution": "false_positive"}, 422, "invalid_alert_change"),
        ({"status": "acknowledged", "resolution": "false_positive"}, 422, "invalid_alert_change"),
        ({"assignee_id": 10**6}, 422, "invalid_alert_change"),
        ({"status": "done"}, 422, None),
        ({"status": "closed", "resolution": "recovered"}, 422, None),
        ({"assignee_id": 0}, 422, None),
        ({"note": "x" * 501}, 422, None),
        ({"title": "Edited"}, 422, None),
    ],
)
def test_a_change_that_makes_no_sense_is_refused_and_changes_nothing(
    client, db_session, world, body, status, code
):
    alert = new_alert(db_session, world)

    response = patch(client, world.analyst, alert, **body)

    assert response.status_code == status, response.text
    if code:
        assert response.json()["error"]["code"] == code
    assert entries(db_session) == []
    (kept,) = rows(db_session, Alert)
    assert (kept.status, kept.resolution, kept.assignee_id) == ("open", None, None)


def test_a_closed_alert_cannot_be_acknowledged_without_reopening_it_first(
    client, db_session, world
):
    alert = new_alert(db_session, world)
    patch(client, world.analyst, alert, status="closed", resolution="accepted_risk")

    response = patch(client, world.analyst, alert, status="acknowledged")

    assert response.status_code == 409 and response.json()["error"]["code"] == "conflict"
    assert "reopen it first" in response.json()["error"]["message"]
    assert len(entries(db_session)) == 1  # only the close


def test_a_bad_assignee_does_not_undo_nor_half_apply_the_rest(client, db_session, world):
    alert = new_alert(db_session, world)
    stranger = make_user(db_session, "sam@example.com", Role.ANALYST, "Other Org")
    db_session.rollback()

    response = patch(client, world.analyst, alert, status="acknowledged", assignee_id=stranger.id)

    assert response.status_code == 422
    assert rows(db_session, Alert)[0].status == "open"


def test_an_inactive_person_cannot_be_given_an_alert(client, db_session, world):
    gone = make_user(db_session, "gone@example.com", Role.ANALYST)
    gone.is_active = False
    db_session.commit()
    db_session.rollback()
    alert = new_alert(db_session, world)

    assert patch(client, world.analyst, alert, assignee_id=gone.id).status_code == 422


# --- who may --------------------------------------------------------------------------------------


def test_a_viewer_reads_alerts_but_does_not_work_them(client, db_session, world):
    alert = new_alert(db_session, world)

    assert client.get(f"{URL}/{alert.id}", headers=world.viewer).status_code == 200
    response = patch(client, world.viewer, alert, status="acknowledged")

    assert response.status_code == 403
    assert rows(db_session, Alert)[0].status == "open"


def test_a_key_works_an_alert_for_the_person_who_made_it_and_is_named_in_the_log(
    client, db_session, world
):
    boss = make_user(db_session, "boss@example.com", Role.ADMIN)
    made = apikeys.create_key(db_session, creator=boss, name="Triage bot", role=Role.ANALYST)
    db_session.commit()
    db_session.rollback()
    alert = new_alert(db_session, world)

    response = patch(
        client, {"X-API-Key": made.secret}, alert, status="closed", resolution="false_positive"
    )

    assert response.status_code == 200
    assert response.json()["alert"]["closed_by"] == {"id": boss.id, "email": "boss@example.com"}
    (entry,) = entries(db_session)
    assert (entry.actor_type, entry.actor_label) == ("api_key", "Triage bot")
    assert rows(db_session, User)  # the person is still there to answer for it


def test_another_organization_cannot_work_an_alert_it_cannot_see(client, db_session, world):
    alert = new_alert(db_session, world)
    _, outsider = person(
        client, db_session, Role.ANALYST, organization="Other Org", email="a@o.com"
    )

    response = patch(client, outsider, alert, status="closed", resolution="false_positive")

    assert response.status_code == 404
    (kept,) = rows(db_session, Alert)
    assert kept.status == "open" and entries(db_session) == []
