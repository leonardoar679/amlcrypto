"""The cases endpoints: who may open and work them, and what each call leaves behind."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.access import apikeys
from app.access.permissions import Role
from app.database.models import Alert, AuditLog, Case, CaseEvent, CaseLink, RiskAssessment
from app.database.repositories import risk as risk_store
from tests.access_helpers import make_user, person
from tests.monitoring_helpers import ADDRESS, OTHER_ADDRESS, alert_for, watch

URL = "/api/v1/cases"


@pytest.fixture
def world(client, db_session, settings):
    ana, analyst = person(client, db_session, Role.ANALYST)
    _, viewer = person(client, db_session, Role.VIEWER)
    _, admin = person(client, db_session, Role.ADMIN)
    monitor = watch(db_session, settings, ana)
    db_session.rollback()
    return SimpleNamespace(
        ana=ana,
        analyst=analyst,
        viewer=viewer,
        admin=admin,
        monitor=monitor,
        org=ana.organization_id,
    )


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


def open_case(client, headers, title="Suspicious deposits", **more):
    return client.post(URL, json={"title": title, **more}, headers=headers)


def made(client, headers, **more):
    response = open_case(client, headers, **more)
    assert response.status_code == 201, response.text
    return response.json()["case"]


def listed(client, headers, **query):
    response = client.get(URL, params=query, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def ids(payload):
    return [item["id"] for item in payload["items"]]


def audit_rows(db_session, action):
    return [r for r in rows(db_session, AuditLog) if r.action == action]


# --- opening ------------------------------------------------------------------------------------


def test_an_analyst_opens_a_case_by_hand(client, db_session, world):
    response = open_case(
        client,
        world.analyst,
        title="  Suspicious deposits  ",
        description="Three deposits in a minute.",
        priority="high",
        chain="ethereum",
        address="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    )

    assert response.status_code == 201
    case = response.json()["case"]
    assert (
        case["title"] == "Suspicious deposits"
        and case["description"] == "Three deposits in a minute."
    )
    assert (case["status"], case["priority"], case["outcome"]) == ("open", "high", None)
    assert (case["chain"], case["chain_id"]) == ("ethereum", "eip155:1")
    assert case["address"] == "0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed"
    assert case["display"] == "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
    assert case["assignee"] is None and case["created_by"] == {
        "id": world.ana.id,
        "email": "analyst-acme@example.com",
    }
    assert case["source_alert_id"] is None and case["alert"] is None and case["links"] == []
    assert [e["kind"] for e in case["events"]] == ["created"]
    assert case["events"][0]["actor"]["label"] == "analyst-acme@example.com"

    (entry,) = audit_rows(db_session, "case.create")
    assert (entry.actor_id, entry.target_type, entry.target_id) == (
        world.ana.id,
        "case",
        str(case["id"]),
    )
    assert entry.organization_id == world.org
    assert entry.details["title"] == "Suspicious deposits" and entry.details["priority"] == "high"
    assert entry.details["chain"] == "ethereum"


def test_a_case_needs_only_a_title(client, world):
    case = made(client, world.analyst)
    assert (case["priority"], case["chain"], case["address"], case["description"]) == (
        "medium",
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    "body,status,code",
    [
        ({"title": "   "}, 422, "invalid_case"),
        ({"title": "t" * 201}, 422, "invalid_case"),
        ({"title": ""}, 422, None),
        ({"title": "x", "priority": "urgent"}, 422, None),
        ({"title": "x", "assignee_id": 10**6}, 422, "invalid_case"),
        ({"title": "x", "chain": "ethereum"}, 422, "invalid_case"),
        ({"title": "x", "address": ADDRESS}, 422, "invalid_case"),
        ({"title": "x", "chain": "dogecoin", "address": ADDRESS}, 404, "unknown_chain"),
        ({"title": "x", "chain": "ethereum", "address": "0x123"}, 422, "invalid_address"),
        ({"title": "x", "alert_id": 10**6}, 422, "invalid_case"),
        ({"title": "x", "colour": "red"}, 422, None),
        ({}, 422, None),
    ],
)
def test_a_case_that_makes_no_sense_is_refused_and_leaves_nothing(
    client, db_session, world, body, status, code
):
    response = client.post(URL, json=body, headers=world.analyst)

    assert response.status_code == status, response.text
    if code:
        assert response.json()["error"]["code"] == code
    assert rows(db_session, Case) == [] and rows(db_session, CaseEvent) == []
    assert audit_rows(db_session, "case.create") == []


def test_a_case_may_be_given_to_a_colleague_from_the_start(client, db_session, world):
    bob = make_user(db_session, "bob@example.com", Role.ANALYST)
    db_session.rollback()
    case = made(client, world.analyst, assignee_id=bob.id)
    assert case["assignee"] == {"id": bob.id, "email": "bob@example.com"}


# --- opening from an alert ----------------------------------------------------------------------


@pytest.fixture
def alert(db_session, world):
    assessment = risk_store.create_assessment(
        db_session,
        organization_id=world.org,
        subject_type="address",
        chain_id="eip155:1",
        subject=ADDRESS,
        options={},
    )
    db_session.commit()
    made_alert = alert_for(db_session, world.monitor, assessment_id=assessment.id)
    db_session.rollback()
    return made_alert


def test_a_case_from_an_alert_takes_its_address_links_what_raised_it_and_acknowledges_it(
    client, db_session, world, alert
):
    response = open_case(client, world.analyst, title="Look into it", alert_id=alert.id)

    assert response.status_code == 201
    case = response.json()["case"]
    assert (case["source_alert_id"], case["chain"], case["address"]) == (
        alert.id,
        "ethereum",
        ADDRESS,
    )
    assert case["alert"] == {
        "id": alert.id,
        "title": alert.title,
        "severity": alert.severity,
        "status": "acknowledged",
    }
    links = {(x["kind"], x["reference"]) for x in case["links"]}
    assert links == {
        ("alert", str(alert.id)),
        ("address", ADDRESS),
        ("screening", f"scr_{alert.assessment_id}"),
    }
    assert case["events"][0]["data"]["source_alert_id"] == alert.id

    (kept,) = rows(db_session, Alert)
    assert kept.status == "acknowledged"
    (entry,) = audit_rows(db_session, "alert.update")
    assert entry.target_id == str(alert.id) and entry.actor_id == world.ana.id
    assert entry.details["reason"] == "case_opened" and entry.details["case_id"] == case["id"]
    assert entry.details["changes"] == {"status": {"from": "open", "to": "acknowledged"}}


def test_an_alert_somebody_already_took_up_keeps_its_status(client, db_session, world, alert):
    client.patch(
        f"/api/v1/alerts/{alert.id}",
        json={"status": "closed", "resolution": "true_positive"},
        headers=world.analyst,
    )

    assert open_case(client, world.analyst, alert_id=alert.id).status_code == 201

    (kept,) = rows(db_session, Alert)
    assert kept.status == "closed"  # opening a case does not reopen or touch a finished alert
    assert len(audit_rows(db_session, "alert.update")) == 1  # only the close


def test_an_alert_lists_the_cases_opened_from_it(client, world, alert):
    assert (
        client.get(f"/api/v1/alerts/{alert.id}", headers=world.viewer).json()["alert"]["cases"]
        == []
    )
    first = made(client, world.analyst, title="First look", alert_id=alert.id)
    client.patch(
        f"{URL}/{first['id']}",
        json={"status": "closed", "outcome": "dismissed"},
        headers=world.analyst,
    )
    second = made(client, world.analyst, title="Second look", alert_id=alert.id)

    shown = client.get(f"/api/v1/alerts/{alert.id}", headers=world.viewer).json()["alert"]["cases"]

    assert shown == [
        {"id": first["id"], "title": "First look", "status": "closed"},
        {"id": second["id"], "title": "Second look", "status": "open"},
    ]


def test_an_alert_has_one_unfinished_case_at_a_time(client, world, alert):
    first = made(client, world.analyst, alert_id=alert.id)

    again = open_case(client, world.analyst, alert_id=alert.id)

    assert again.status_code == 409 and again.json()["error"]["code"] == "conflict"
    assert f"Case #{first['id']}" in again.json()["error"]["message"]
    client.patch(
        f"{URL}/{first['id']}",
        json={"status": "closed", "outcome": "dismissed"},
        headers=world.analyst,
    )
    assert open_case(client, world.analyst, alert_id=alert.id).status_code == 201


def test_an_alert_names_the_address_so_the_request_may_not(client, world, alert):
    response = open_case(
        client, world.analyst, alert_id=alert.id, chain="ethereum", address=ADDRESS
    )
    assert response.status_code == 422


def test_an_alert_of_another_organization_cannot_open_a_case(client, db_session, settings, world):
    stranger = make_user(db_session, "sam@example.com", Role.ANALYST, "Other Org")
    theirs = alert_for(db_session, watch(db_session, settings, stranger, address=OTHER_ADDRESS))
    db_session.rollback()

    response = open_case(client, world.analyst, alert_id=theirs.id)

    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_case"
    assert rows(db_session, Case) == []


# --- roles --------------------------------------------------------------------------------------


def test_a_viewer_reads_cases_but_does_not_open_or_change_them(client, db_session, world):
    case = made(client, world.analyst)
    base = f"{URL}/{case['id']}"

    assert listed(client, world.viewer)["total"] == 1
    assert client.get(f"{URL}/summary", headers=world.viewer).status_code == 200
    assert client.get(base, headers=world.viewer).status_code == 200
    assert client.get(f"{base}/export", headers=world.viewer).status_code == 200
    assert open_case(client, world.viewer).status_code == 403
    assert client.patch(base, json={"priority": "low"}, headers=world.viewer).status_code == 403
    assert (
        client.post(f"{base}/notes", json={"body": "hi"}, headers=world.viewer).status_code == 403
    )
    link = {"kind": "address", "chain": "ethereum", "reference": ADDRESS}
    assert client.post(f"{base}/links", json=link, headers=world.viewer).status_code == 403
    assert client.delete(f"{base}/links/1", headers=world.viewer).status_code == 403
    assert client.get(base, headers=world.analyst).json()["case"]["priority"] == "medium"


def test_a_program_with_a_key_works_cases_for_the_person_who_made_the_key(
    client, db_session, world
):
    made_key = apikeys.create_key(
        db_session, creator=world.ana, name="Triage bot", role=Role.ANALYST
    )
    db_session.commit()
    db_session.rollback()
    headers = {"X-API-Key": made_key.secret}

    case = made(client, headers, title="From the pipeline")

    assert case["created_by"]["id"] == world.ana.id
    assert case["events"][0]["actor"] == {
        "type": "api_key",
        "id": made_key.row.id,
        "label": "Triage bot",
    }
    note = client.post(f"{URL}/{case['id']}/notes", json={"body": "Seen."}, headers=headers).json()
    assert note["event"]["actor"]["label"] == "Triage bot"


# --- the list and the summary -------------------------------------------------------------------


def test_the_list_is_newest_first_and_pages(client, world):
    created = [made(client, world.analyst, title=f"Case {n}")["id"] for n in range(5)]

    body = listed(client, world.viewer)
    assert body["total"] == 5 and ids(body) == created[::-1]
    first = body["items"][0]
    assert first["title"] == "Case 4" and "events" not in first and "links" not in first
    assert set(first) == {
        "id", "title", "status", "priority", "outcome", "chain", "chain_id", "address", "display",
        "source_alert_id", "assignee", "created_by", "created_at", "updated_at", "closed_at",
    }  # fmt: skip

    page = listed(client, world.viewer, limit=2, offset=1)
    assert ids(page) == created[::-1][1:3] and (page["total"], page["limit"], page["offset"]) == (
        5,
        2,
        1,
    )
    for bad in (
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"status": "done"},
        {"priority": "urgent"},
    ):
        assert client.get(URL, params=bad, headers=world.viewer).status_code == 422


def test_the_list_can_be_narrowed(client, db_session, world):
    bob = make_user(db_session, "bob@example.com", Role.ANALYST)
    db_session.rollback()
    a = made(
        client,
        world.analyst,
        title="Alpha wallet",
        priority="high",
        chain="ethereum",
        address=ADDRESS,
    )
    b = made(client, world.analyst, title="Beta wallet", assignee_id=bob.id)
    c = made(client, world.analyst, title="Gamma 100% sure", priority="high")
    client.patch(
        f"{URL}/{c['id']}", json={"status": "closed", "outcome": "confirmed"}, headers=world.analyst
    )
    client.patch(f"{URL}/{b['id']}", json={"status": "in_progress"}, headers=world.analyst)

    viewer = world.viewer
    assert ids(listed(client, viewer, status="open")) == [a["id"]]
    assert ids(listed(client, viewer, status="in_progress")) == [b["id"]]
    assert ids(listed(client, viewer, status="closed")) == [c["id"]]
    assert ids(listed(client, viewer, status="unresolved")) == [b["id"], a["id"]]
    assert ids(listed(client, viewer, priority="high")) == [c["id"], a["id"]]
    assert ids(listed(client, viewer, status="unresolved", priority="high")) == [a["id"]]
    assert ids(listed(client, viewer, assignee_id=bob.id)) == [b["id"]]
    assert ids(
        listed(client, viewer, chain="ethereum", address=ADDRESS.upper().replace("0X", "0x"))
    ) == [a["id"]]
    assert listed(client, viewer, chain="bsc", address=ADDRESS)["items"] == []
    assert ids(listed(client, viewer, q="Beta")) == [b["id"]]
    assert listed(client, viewer, q="%")["items"] == []  # a wildcard is not a wildcard
    assert ids(listed(client, viewer, q="Gamma 100%")) == [c["id"]]
    assert client.get(URL, params={"chain": "ethereum"}, headers=viewer).status_code == 422
    assert client.get(URL, params={"address": ADDRESS}, headers=viewer).status_code == 422
    assert (
        client.get(URL, params={"chain": "ethereum", "address": "0x1"}, headers=viewer).status_code
        == 422
    )


def test_the_list_can_show_whose_cases_they_are(client, db_session, world):
    mine = made(client, world.analyst, assignee_id=world.ana.id)
    made(client, world.analyst)
    assert ids(listed(client, world.analyst, mine="true")) == [mine["id"]]
    assert listed(client, world.analyst, mine="true")["items"][0]["assignee"] == {
        "id": world.ana.id,
        "email": "analyst-acme@example.com",
    }
    assert listed(client, world.analyst, mine="false")["total"] == 2


def test_mine_for_a_key_is_the_person_who_made_the_key(client, db_session, world):
    boss = make_user(db_session, "boss@example.com", Role.ADMIN)
    made_key = apikeys.create_key(db_session, creator=boss, name="Bot", role=Role.ANALYST)
    db_session.commit()
    db_session.rollback()
    theirs = made(client, world.analyst, assignee_id=boss.id)
    made(client, world.analyst)

    assert ids(listed(client, {"X-API-Key": made_key.secret}, mine="true")) == [theirs["id"]]


def test_the_summary_is_this_organizations_alone(client, db_session, world):
    made(client, world.analyst, priority="critical")
    _, outsider = person(
        client, db_session, Role.ANALYST, organization="Other Org", email="a@o.com"
    )
    made(client, outsider, priority="high")
    made(client, outsider, priority="high")

    mine = client.get(f"{URL}/summary", headers=world.viewer).json()
    theirs = client.get(f"{URL}/summary", headers=outsider).json()

    assert mine["unresolved_by_priority"] == {"critical": 1} and mine["unresolved"] == 1
    assert theirs["unresolved_by_priority"] == {"high": 2} and theirs["unresolved"] == 2


def test_each_case_shows_its_own_timeline_and_links_only(client, world):
    one = made(client, world.analyst, title="One")
    two = made(client, world.analyst, title="Two")
    client.post(
        f"{URL}/{one['id']}/notes", json={"body": "About the first."}, headers=world.analyst
    )
    client.post(
        f"{URL}/{two['id']}/notes", json={"body": "About the second."}, headers=world.analyst
    )
    for case, address in ((one, ADDRESS), (two, OTHER_ADDRESS)):
        link = {"kind": "address", "chain": "ethereum", "reference": address}
        client.post(f"{URL}/{case['id']}/links", json=link, headers=world.analyst)

    first = client.get(f"{URL}/{one['id']}", headers=world.viewer).json()["case"]
    second = client.get(f"{URL}/{two['id']}", headers=world.viewer).json()["case"]

    assert [e["body"] for e in first["events"] if e["kind"] == "note"] == ["About the first."]
    assert [e["body"] for e in second["events"] if e["kind"] == "note"] == ["About the second."]
    assert [x["reference"] for x in first["links"]] == [ADDRESS]
    assert [x["reference"] for x in second["links"]] == [OTHER_ADDRESS]
    assert [e["kind"] for e in first["events"]] == ["created", "note", "link_added"]


def test_the_summary_counts_by_status_and_the_unresolved_by_priority(client, world):
    summary = client.get(f"{URL}/summary", headers=world.viewer).json()
    assert summary == {
        "success": True,
        "by_status": {"open": 0, "in_progress": 0, "closed": 0},
        "unresolved": 0,
        "unresolved_by_priority": {},
    }
    made(client, world.analyst, priority="high")
    working = made(client, world.analyst, priority="high")
    made(client, world.analyst, priority="low")
    done = made(client, world.analyst, priority="critical")
    client.patch(f"{URL}/{working['id']}", json={"status": "in_progress"}, headers=world.analyst)
    client.patch(
        f"{URL}/{done['id']}",
        json={"status": "closed", "outcome": "dismissed"},
        headers=world.analyst,
    )

    assert client.get(f"{URL}/summary", headers=world.viewer).json() == {
        "success": True,
        "by_status": {"open": 2, "in_progress": 1, "closed": 1},
        "unresolved": 3,
        "unresolved_by_priority": {"high": 2, "low": 1},
    }


# --- one case -----------------------------------------------------------------------------------


def test_a_case_is_read_by_its_number(client, world):
    case = made(client, world.analyst)

    got = client.get(f"{URL}/{case['id']}", headers=world.viewer)

    assert got.status_code == 200 and got.json() == {"success": True, "case": case}
    missing = client.get(f"{URL}/{case['id'] + 100}", headers=world.viewer)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "case_not_found"
    assert client.get(f"{URL}/abc", headers=world.viewer).status_code == 422
    assert client.get(f"{URL}/summary", headers=world.viewer).status_code == 200  # not a number


# --- changing -----------------------------------------------------------------------------------


def test_a_case_is_worked_through_to_its_end_and_audited(client, db_session, world):
    case = made(client, world.analyst, priority="low")
    base = f"{URL}/{case['id']}"

    started = client.patch(
        base, json={"status": "in_progress", "priority": "high"}, headers=world.analyst
    )
    assert started.status_code == 200
    body = started.json()["case"]
    assert (body["status"], body["priority"]) == ("in_progress", "high")

    closed = client.patch(
        base, json={"status": "closed", "outcome": "escalated"}, headers=world.analyst
    ).json()["case"]
    assert (closed["status"], closed["outcome"]) == ("closed", "escalated")
    assert closed["closed_at"] and closed["closed_by"] == {
        "id": world.ana.id,
        "email": "analyst-acme@example.com",
    }

    reopened = client.patch(base, json={"status": "open"}, headers=world.analyst).json()["case"]
    assert (reopened["status"], reopened["outcome"], reopened["closed_at"]) == ("open", None, None)

    kinds = [e["kind"] for e in reopened["events"]]
    assert kinds == ["created", "priority", "status", "status", "status"]
    entries = audit_rows(db_session, "case.update")
    assert [e.details["changes"].get("status", {}).get("to") for e in entries] == [
        "in_progress",
        "closed",
        "open",
    ]
    assert entries[0].details["changes"]["priority"] == {"from": "low", "to": "high"}
    assert all(e.target_id == str(case["id"]) and e.actor_id == world.ana.id for e in entries)


def test_the_title_the_description_and_the_owner_can_be_changed_and_cleared(
    client, db_session, world
):
    bob = make_user(db_session, "bob@example.com", Role.ANALYST)
    db_session.rollback()
    case = made(client, world.analyst, description="Old text")
    base = f"{URL}/{case['id']}"

    body = client.patch(
        base,
        json={"title": "New title", "description": "New text", "assignee_id": bob.id},
        headers=world.analyst,
    ).json()["case"]
    assert (body["title"], body["description"]) == ("New title", "New text")
    assert body["assignee"] == {"id": bob.id, "email": "bob@example.com"}

    cleared = client.patch(
        base, json={"description": None, "assignee_id": None}, headers=world.analyst
    ).json()["case"]
    assert cleared["description"] is None and cleared["assignee"] is None
    kept = client.patch(base, json={"priority": "low"}, headers=world.analyst).json()["case"]
    assert kept["title"] == "New title"  # leaving it out keeps it
    assert client.patch(base, json={"title": None}, headers=world.analyst).status_code == 422
    assert client.patch(base, json={"title": "  "}, headers=world.analyst).status_code == 422


def test_what_a_change_does_not_mention_it_leaves_alone(client, db_session, world):
    bob = make_user(db_session, "bob@example.com", Role.ANALYST)
    db_session.rollback()
    case = made(
        client, world.analyst, title="Keep me", description="Keep this text", assignee_id=bob.id
    )

    body = client.patch(
        f"{URL}/{case['id']}", json={"priority": "high"}, headers=world.analyst
    ).json()["case"]

    assert body["title"] == "Keep me" and body["description"] == "Keep this text"
    assert body["assignee"] == {"id": bob.id, "email": "bob@example.com"}
    again = client.patch(
        f"{URL}/{case['id']}", json={"status": "in_progress"}, headers=world.analyst
    ).json()["case"]
    assert (again["priority"], again["description"], again["assignee"]["id"]) == (
        "high",
        "Keep this text",
        bob.id,
    )


@pytest.mark.parametrize(
    "body,status,code",
    [
        ({}, 422, "nothing_to_change"),
        ({"status": "closed"}, 422, "invalid_case"),
        ({"outcome": "confirmed"}, 422, "invalid_case"),
        ({"assignee_id": 10**6}, 422, "invalid_case"),
        ({"status": "done"}, 422, None),
        ({"outcome": "won"}, 422, None),
        ({"assignee_id": 0}, 422, None),
        ({"colour": "red"}, 422, None),
    ],
)
def test_a_change_that_makes_no_sense_is_refused_and_changes_nothing(
    client, db_session, world, body, status, code
):
    case = made(client, world.analyst)

    response = client.patch(
        f"{URL}/{case['id']}",
        json={"priority": "high", **body} if body else body,
        headers=world.analyst,
    )

    assert response.status_code == status, response.text
    if code:
        assert response.json()["error"]["code"] == code
    assert client.get(f"{URL}/{case['id']}", headers=world.analyst).json()["case"] == case
    assert audit_rows(db_session, "case.update") == []


def test_a_closed_case_cannot_be_moved_without_reopening_it(client, world):
    case = made(client, world.analyst)
    base = f"{URL}/{case['id']}"
    client.patch(base, json={"status": "closed", "outcome": "dismissed"}, headers=world.analyst)

    response = client.patch(base, json={"status": "in_progress"}, headers=world.analyst)

    assert response.status_code == 409 and response.json()["error"]["code"] == "conflict"
    assert "reopen it first" in response.json()["error"]["message"]


def test_a_change_that_changes_nothing_is_not_audited(client, db_session, world):
    case = made(client, world.analyst)
    response = client.patch(
        f"{URL}/{case['id']}", json={"priority": "medium", "status": "open"}, headers=world.analyst
    )
    assert response.status_code == 200
    assert audit_rows(db_session, "case.update") == []
    assert [e["kind"] for e in response.json()["case"]["events"]] == ["created"]


# --- notes --------------------------------------------------------------------------------------


def test_a_note_is_added_to_the_timeline_and_audited(client, db_session, world):
    case = made(client, world.analyst)

    response = client.post(
        f"{URL}/{case['id']}/notes", json={"body": "  Asked the customer.  "}, headers=world.analyst
    )

    assert response.status_code == 201
    event = response.json()["event"]
    assert (event["kind"], event["body"]) == ("note", "Asked the customer.")
    assert event["actor"]["label"] == "analyst-acme@example.com" and event["at"]
    timeline = client.get(f"{URL}/{case['id']}", headers=world.viewer).json()["case"]["events"]
    assert [e["kind"] for e in timeline] == ["created", "note"] and timeline[1][
        "body"
    ] == "Asked the customer."
    (entry,) = audit_rows(db_session, "case.note")
    assert entry.details == {"event_id": event["id"], "note": "Asked the customer."}


@pytest.mark.parametrize("body", ["", "   ", "x" * 5001])
def test_an_empty_or_huge_note_is_refused(client, db_session, world, body):
    case = made(client, world.analyst)
    response = client.post(f"{URL}/{case['id']}/notes", json={"body": body}, headers=world.analyst)
    assert response.status_code == 422
    assert [e.kind for e in rows(db_session, CaseEvent)] == ["created"]


def test_a_note_can_be_added_to_a_finished_case(client, world):
    case = made(client, world.analyst)
    base = f"{URL}/{case['id']}"
    client.patch(base, json={"status": "closed", "outcome": "dismissed"}, headers=world.analyst)
    assert (
        client.post(
            f"{base}/notes", json={"body": "For the file."}, headers=world.analyst
        ).status_code
        == 201
    )


def test_the_timeline_cannot_be_edited(client, world):
    case = made(client, world.analyst)
    event = client.post(
        f"{URL}/{case['id']}/notes", json={"body": "x"}, headers=world.analyst
    ).json()["event"]
    for method in ("patch", "put", "delete"):
        for path in (
            f"{URL}/{case['id']}/notes/{event['id']}",
            f"{URL}/{case['id']}/events/{event['id']}",
        ):
            assert getattr(client, method)(path, headers=world.admin).status_code in (404, 405)


# --- links --------------------------------------------------------------------------------------


def test_links_are_added_listed_and_taken_away_with_a_trace(client, db_session, world):
    case = made(client, world.analyst)
    base = f"{URL}/{case['id']}"

    added = client.post(
        f"{base}/links",
        json={
            "kind": "address",
            "chain": "ethereum",
            "reference": ADDRESS.upper().replace("0X", "0x"),
            "note": "Sender",
        },
        headers=world.analyst,
    )

    assert added.status_code == 201
    link = added.json()["link"]
    assert (link["kind"], link["chain"], link["reference"], link["note"]) == (
        "address",
        "ethereum",
        ADDRESS,
        "Sender",
    )
    detail = client.get(base, headers=world.viewer).json()["case"]
    assert [x["id"] for x in detail["links"]] == [link["id"]]
    assert detail["events"][-1]["kind"] == "link_added"
    (entry,) = audit_rows(db_session, "case.link.add")
    assert entry.details == {"kind": "address", "chain_id": "eip155:1", "reference": ADDRESS}

    removed = client.delete(f"{base}/links/{link['id']}", headers=world.analyst)
    assert removed.status_code == 204 and removed.content == b""
    after = client.get(base, headers=world.viewer).json()["case"]
    assert after["links"] == [] and after["events"][-1]["kind"] == "link_removed"
    (gone,) = audit_rows(db_session, "case.link.remove")
    assert gone.details["reference"] == ADDRESS


@pytest.mark.parametrize(
    "link,status,code",
    [
        ({"kind": "address", "reference": ADDRESS}, 422, "invalid_case"),
        ({"kind": "address", "chain": "ethereum", "reference": "0x1"}, 422, "invalid_case"),
        ({"kind": "transaction", "reference": "0x" + "ab" * 32}, 422, "invalid_case"),
        ({"kind": "screening", "reference": "scr_999999"}, 422, "invalid_case"),
        ({"kind": "alert", "reference": "999999"}, 422, "invalid_case"),
        ({"kind": "wallet", "reference": ADDRESS}, 422, None),
        ({"kind": "address", "chain": "ethereum"}, 422, None),
        (
            {"kind": "address", "chain": "ethereum", "reference": ADDRESS, "note": "n" * 1001},
            422,
            None,
        ),
    ],
)
def test_a_link_that_points_nowhere_is_refused(client, db_session, world, link, status, code):
    case = made(client, world.analyst)

    response = client.post(f"{URL}/{case['id']}/links", json=link, headers=world.analyst)

    assert response.status_code == status, response.text
    if code:
        assert response.json()["error"]["code"] == code
    assert rows(db_session, CaseLink) == []


def test_the_same_link_twice_is_a_conflict(client, world):
    case = made(client, world.analyst)
    link = {"kind": "address", "chain": "ethereum", "reference": ADDRESS}
    assert (
        client.post(f"{URL}/{case['id']}/links", json=link, headers=world.analyst).status_code
        == 201
    )
    again = client.post(f"{URL}/{case['id']}/links", json=link, headers=world.analyst)
    assert again.status_code == 409 and again.json()["error"]["code"] == "conflict"


def test_a_link_to_a_screening_and_to_an_alert_of_the_organization_works(
    client, db_session, world, alert
):
    case = made(client, world.analyst)
    base = f"{URL}/{case['id']}"
    assert (
        client.post(
            f"{base}/links",
            json={"kind": "screening", "reference": f" scr_{alert.assessment_id}"},
            headers=world.analyst,
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"{base}/links",
            json={"kind": "alert", "reference": str(alert.id)},
            headers=world.analyst,
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"{base}/links",
            json={"kind": "transaction", "chain": "ethereum", "reference": "0x" + "AB" * 32},
            headers=world.analyst,
        ).status_code
        == 201
    )
    detail = client.get(base, headers=world.viewer).json()["case"]
    assert {(x["kind"], x["reference"]) for x in detail["links"]} == {
        ("screening", f"scr_{alert.assessment_id}"),
        ("alert", str(alert.id)),
        ("transaction", "0x" + "ab" * 32),
    }


def test_taking_away_a_link_that_is_not_there_or_not_this_cases_is_a_404(client, world):
    one = made(client, world.analyst, title="One")
    two = made(client, world.analyst, title="Two")
    link = client.post(
        f"{URL}/{one['id']}/links",
        json={"kind": "address", "chain": "ethereum", "reference": ADDRESS},
        headers=world.analyst,
    ).json()["link"]

    for case_id, link_id in ((two["id"], link["id"]), (one["id"], link["id"] + 100)):
        response = client.delete(f"{URL}/{case_id}/links/{link_id}", headers=world.analyst)
        assert response.status_code == 404 and response.json()["error"]["code"] == "link_not_found"
    assert len(client.get(f"{URL}/{one['id']}", headers=world.analyst).json()["case"]["links"]) == 1


# --- the case file ------------------------------------------------------------------------------


def test_the_case_file_holds_the_case_the_linked_alerts_and_the_reports_as_kept(
    client, db_session, world, alert
):
    case = made(client, world.analyst, title="Look into it", alert_id=alert.id)
    client.post(f"{URL}/{case['id']}/notes", json={"body": "Called them."}, headers=world.analyst)
    db_session.rollback()
    db_session.execute(
        update(RiskAssessment)
        .where(RiskAssessment.id == alert.assessment_id)
        .values(status="COMPLETED", report={"risk_score": 61, "findings": [{"rule_id": "AML-001"}]})
    )
    db_session.commit()

    response = client.get(f"{URL}/{case['id']}/export", headers=world.viewer)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert (
        response.headers["content-disposition"] == f'attachment; filename="case-{case["id"]}.json"'
    )
    file = json.loads(response.text)
    assert file["schema_version"] == 1 and file["generated_by"] == "viewer-acme@example.com"
    assert file["generated_at"] and "not a clearance" in file["note"]
    assert file["case"]["title"] == "Look into it"
    assert [e["kind"] for e in file["case"]["events"]] == ["created", "note"]
    (exported_alert,) = file["alerts"]
    assert exported_alert["id"] == alert.id and exported_alert["evidence"] == alert.evidence
    assert exported_alert["screening_id"] == f"scr_{alert.assessment_id}"
    (report,) = file["screenings"]
    assert (
        report["screening_id"] == f"scr_{alert.assessment_id}" and report["status"] == "COMPLETED"
    )
    assert report["report"] == {"risk_score": 61, "findings": [{"rule_id": "AML-001"}]}

    (entry,) = audit_rows(db_session, "case.export")
    assert entry.actor_label == "viewer-acme@example.com" and entry.details == {
        "links": 3,
        "reports": 1,
    }


def test_the_case_file_holds_every_linked_alert_and_a_bounded_number_of_reports(
    client, db_session, settings, world
):
    from app.api.v1.cases import MAX_EXPORTED_REPORTS

    case = made(client, world.analyst, title="Wide")
    base = f"{URL}/{case['id']}"
    other_monitor = watch(db_session, settings, world.ana, address=OTHER_ADDRESS)
    first = alert_for(db_session, world.monitor, key="x1")
    second = alert_for(db_session, other_monitor, key="x2")
    numbers = []
    for _ in range(MAX_EXPORTED_REPORTS + 3):
        row = risk_store.create_assessment(
            db_session,
            organization_id=world.org,
            subject_type="address",
            chain_id="eip155:1",
            subject=ADDRESS,
            options={},
        )
        numbers.append(row.id)
    db_session.commit()
    db_session.rollback()
    for alert_id in (first.id, second.id):
        link = {"kind": "alert", "reference": str(alert_id)}
        assert client.post(f"{base}/links", json=link, headers=world.analyst).status_code == 201
    for number in numbers:
        link = {"kind": "screening", "reference": f"scr_{number}"}
        assert client.post(f"{base}/links", json=link, headers=world.analyst).status_code == 201

    file = json.loads(client.get(f"{base}/export", headers=world.viewer).text)

    assert sorted(a["id"] for a in file["alerts"]) == sorted([first.id, second.id])
    assert len(file["screenings"]) == MAX_EXPORTED_REPORTS
    assert [r["screening_id"] for r in file["screenings"]] == [
        f"scr_{n}" for n in numbers[:MAX_EXPORTED_REPORTS]
    ]
    (entry,) = audit_rows(db_session, "case.export")
    assert entry.details == {"links": 2 + len(numbers), "reports": MAX_EXPORTED_REPORTS}


def test_a_case_with_nothing_linked_exports_with_empty_lists(client, world):
    case = made(client, world.analyst)
    file = json.loads(client.get(f"{URL}/{case['id']}/export", headers=world.viewer).text)
    assert file["alerts"] == [] and file["screenings"] == []


# --- other organizations ------------------------------------------------------------------------


def test_another_organization_sees_nothing_and_changes_nothing(client, db_session, world):
    case = made(client, world.analyst, title="Mine")
    _, outsider = person(
        client, db_session, Role.ADMIN, organization="Other Org", email="boss@o.com"
    )
    base = f"{URL}/{case['id']}"

    assert listed(client, outsider)["total"] == 0
    assert client.get(f"{URL}/summary", headers=outsider).json()["unresolved"] == 0
    assert client.get(base, headers=outsider).status_code == 404
    assert client.get(f"{base}/export", headers=outsider).status_code == 404
    assert client.patch(base, json={"priority": "low"}, headers=outsider).status_code == 404
    assert client.post(f"{base}/notes", json={"body": "x"}, headers=outsider).status_code == 404
    link = {"kind": "address", "chain": "ethereum", "reference": ADDRESS}
    assert client.post(f"{base}/links", json=link, headers=outsider).status_code == 404
    assert client.delete(f"{base}/links/1", headers=outsider).status_code == 404

    assert client.get(base, headers=world.analyst).json()["case"] == case
    assert [e.kind for e in rows(db_session, CaseEvent)] == ["created"]


def test_a_person_cannot_assign_a_case_to_someone_of_another_organization(
    client, db_session, world
):
    stranger = make_user(db_session, "sam@example.com", Role.ANALYST, "Other Org")
    db_session.rollback()
    case = made(client, world.analyst)
    response = client.patch(
        f"{URL}/{case['id']}", json={"assignee_id": stranger.id}, headers=world.analyst
    )
    assert response.status_code == 422
