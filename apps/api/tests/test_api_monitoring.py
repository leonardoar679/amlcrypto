"""The monitored addresses endpoints: who may watch what, and what each call leaves behind."""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from app.access import apikeys
from app.access.permissions import Role
from app.database.models import Alert, AuditLog, FetchJob, MonitoredAddress
from tests.access_helpers import make_user, person
from tests.monitoring_helpers import ADDRESS, OTHER_ADDRESS, alert_for, watch

URL = "/api/v1/monitoring/addresses"
CHECKSUMMED = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"  # the EIP-55 specification's example


@pytest.fixture
def analyst(client, db_session):
    return person(client, db_session, Role.ANALYST)


@pytest.fixture
def viewer(client, db_session):
    return person(client, db_session, Role.VIEWER)


@pytest.fixture
def admin(client, db_session):
    return person(client, db_session, Role.ADMIN)


def register(client, headers, address=ADDRESS, chain="ethereum", **more):
    return client.post(URL, json={"chain": chain, "address": address, **more}, headers=headers)


def listed(client, headers, **query):
    response = client.get(URL, params=query, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def rows(db_session, model):
    """Everything in a table, detached, with this session's transaction ended again: a write
    session that has read holds SQLite's write lock, and the next API call would find it locked."""
    db_session.rollback()
    found = list(
        db_session.scalars(
            select(model).order_by(model.id).execution_options(populate_existing=True)
        )
    )
    db_session.expunge_all()
    db_session.rollback()
    return found


# --- watching an address ------------------------------------------------------------------------


def test_an_analyst_starts_watching_an_address_and_the_first_check_is_queued(
    client, db_session, analyst
):
    user, headers = analyst

    response = register(client, headers, name=" Treasury ", check_interval_seconds=3600, depth=2)

    assert response.status_code == 201
    monitor = response.json()["monitor"]
    assert monitor["chain"] == "ethereum" and monitor["chain_id"] == "eip155:1"
    assert monitor["address"] == ADDRESS and monitor["name"] == "Treasury"
    assert monitor["status"] == "active" and monitor["check_interval_seconds"] == 3600
    assert monitor["depth"] == 2 and monitor["min_severity"] == "MEDIUM"
    assert monitor["created_by"] == user.id and monitor["created_at"]
    assert monitor["last_checked_at"] is None and monitor["last_screening_id"] is None
    assert monitor["consecutive_failures"] == 0 and monitor["unresolved_alerts"] == 0
    assert monitor["next_check_at"]  # it is due at once

    (job,) = rows(db_session, FetchJob)
    assert job.job_type == "ADDRESS_MONITORING" and job.organization_id == user.organization_id
    assert job.payload == {"monitor_id": monitor["id"], "reason": "registered"}


def test_the_address_is_kept_in_storage_form_and_shown_in_the_chains_own(client, analyst):
    _, headers = analyst

    monitor = register(client, headers, address=CHECKSUMMED).json()["monitor"]

    assert monitor["address"] == CHECKSUMMED.lower() and monitor["display"] == CHECKSUMMED


def test_the_chain_can_be_given_by_slug_or_by_caip_2_id(client, analyst):
    _, headers = analyst
    assert register(client, headers, chain="eip155:56").json()["monitor"]["chain"] == "bsc"
    other = "0x" + "cd" * 20
    assert register(client, headers, address=other, chain="bsc").json()["monitor"]["chain_id"] == (
        "eip155:56"
    )


def test_a_registration_is_recorded_in_the_audit_log(client, db_session, analyst):
    user, headers = analyst
    monitor = register(client, headers, name="Hot wallet", min_severity="HIGH").json()["monitor"]

    (entry,) = [r for r in rows(db_session, AuditLog) if r.action == "monitor.create"]
    assert (entry.actor_type, entry.actor_id, entry.outcome) == ("user", user.id, "success")
    assert (entry.target_type, entry.target_id) == ("monitor", str(monitor["id"]))
    assert entry.organization_id == user.organization_id
    assert entry.details["address"] == ADDRESS and entry.details["name"] == "Hot wallet"
    assert entry.details["min_severity"] == "HIGH" and entry.details["chain"] == "ethereum"


@pytest.mark.parametrize(
    "body,status,code",
    [
        ({"chain": "dogecoin", "address": ADDRESS}, 404, "unknown_chain"),
        ({"chain": "ethereum", "address": "0x123"}, 422, "invalid_address"),
        ({"chain": "bitcoin", "address": ADDRESS}, 422, "invalid_address"),
        (
            {"chain": "ethereum", "address": ADDRESS, "check_interval_seconds": 60},
            422,
            "invalid_monitor",
        ),
        # more than the configured limit (3) is the service's to refuse; more than any graph
        # reaches (6) is refused before it gets there
        ({"chain": "ethereum", "address": ADDRESS, "depth": 4}, 422, "invalid_monitor"),
        ({"chain": "ethereum", "address": ADDRESS, "depth": 9}, 422, "validation_error"),
        (
            {"chain": "ethereum", "address": ADDRESS, "check_interval_seconds": 10**12},
            422,
            "validation_error",
        ),
        ({"chain": "ethereum", "address": ADDRESS, "name": "x" * 121}, 422, "invalid_monitor"),
        ({"chain": "ethereum", "address": ADDRESS, "min_severity": "SEVERE"}, 422, None),
        ({"chain": "ethereum", "address": ADDRESS, "depth": 0}, 422, None),
        ({"chain": "ethereum", "address": ADDRESS, "colour": "red"}, 422, None),
        ({"chain": "ethereum"}, 422, None),
        ({}, 422, None),
    ],
)
def test_a_registration_that_makes_no_sense_is_refused_and_leaves_nothing(
    client, db_session, analyst, body, status, code
):
    _, headers = analyst

    response = client.post(URL, json=body, headers=headers)

    assert response.status_code == status, response.text
    if code:
        assert response.json()["error"]["code"] == code
    assert rows(db_session, MonitoredAddress) == [] and rows(db_session, FetchJob) == []
    assert [r for r in rows(db_session, AuditLog) if r.action == "monitor.create"] == []


def test_watching_the_same_address_twice_is_a_conflict(client, db_session, analyst):
    _, headers = analyst
    register(client, headers)

    again = register(client, headers, address=ADDRESS.upper().replace("0X", "0x"))

    assert again.status_code == 409 and again.json()["error"]["code"] == "conflict"
    assert len(rows(db_session, MonitoredAddress)) == 1
    assert (
        register(client, headers, chain="bsc").status_code == 201
    )  # another chain is another watch


def test_an_organization_cannot_watch_more_than_its_allowance(client, app, analyst):
    _, headers = analyst
    app.state.settings = app.state.settings.model_copy(update={"monitoring_max_addresses": 1})
    assert register(client, headers).status_code == 201

    response = register(client, headers, address=OTHER_ADDRESS)

    assert (
        response.status_code == 409 and "at most 1 addresses" in response.json()["error"]["message"]
    )


# --- roles --------------------------------------------------------------------------------------


def test_a_viewer_can_look_but_not_touch(client, db_session, analyst, viewer):
    _, headers = analyst
    monitor = register(client, headers).json()["monitor"]
    _, looking = viewer
    base = f"{URL}/{monitor['id']}"

    assert listed(client, looking)["total"] == 1
    assert client.get(base, headers=looking).status_code == 200
    assert register(client, looking, address=OTHER_ADDRESS).status_code == 403
    assert client.patch(base, json={"name": "x"}, headers=looking).status_code == 403
    assert client.delete(base, headers=looking).status_code == 403
    assert client.post(f"{base}/check", headers=looking).status_code == 403
    assert client.get(base, headers=headers).json()["monitor"]["name"] is None


def test_an_admin_can_watch_too(client, admin):
    _, headers = admin
    assert register(client, headers).status_code == 201


def test_a_program_with_a_key_watches_on_behalf_of_the_person_who_made_the_key(
    client, db_session, admin
):
    boss, _ = admin
    made = apikeys.create_key(db_session, creator=boss, name="Pipeline", role=Role.ANALYST)
    db_session.commit()
    db_session.rollback()
    headers = {"X-API-Key": made.secret}

    response = register(client, headers)

    assert response.status_code == 201
    assert response.json()["monitor"]["created_by"] == boss.id
    (entry,) = [r for r in rows(db_session, AuditLog) if r.action == "monitor.create"]
    assert (entry.actor_type, entry.actor_label) == ("api_key", "Pipeline")  # the key is named

    viewer_key = apikeys.create_key(db_session, creator=boss, name="Reader", role=Role.VIEWER)
    db_session.commit()
    db_session.rollback()
    assert (
        register(client, {"X-API-Key": viewer_key.secret}, address=OTHER_ADDRESS).status_code == 403
    )


# --- looking ------------------------------------------------------------------------------------


def test_the_list_is_newest_first_and_pages(client, analyst):
    _, headers = analyst
    ids = [
        register(client, headers, address="0x" + f"{n:02x}" * 20).json()["monitor"]["id"]
        for n in range(1, 6)
    ]

    everything = listed(client, headers)
    assert everything["total"] == 5 and [m["id"] for m in everything["items"]] == ids[::-1]

    page = listed(client, headers, limit=2, offset=1)
    assert [m["id"] for m in page["items"]] == ids[::-1][1:3]
    assert (page["total"], page["limit"], page["offset"]) == (5, 2, 1)
    assert listed(client, headers, offset=10)["items"] == []


def test_the_list_can_be_narrowed_by_status_and_by_the_start_of_an_address(client, analyst):
    _, headers = analyst
    a = register(client, headers, address="0x" + "a1" * 20).json()["monitor"]
    register(client, headers, address="0x" + "b2" * 20)
    client.patch(f"{URL}/{a['id']}", json={"status": "paused"}, headers=headers)

    assert [m["id"] for m in listed(client, headers, status="paused")["items"]] == [a["id"]]
    assert listed(client, headers, status="active")["total"] == 1
    assert listed(client, headers, prefix="0xb2")["total"] == 1
    assert listed(client, headers, prefix="0x%")["total"] == 0  # a wildcard is not a wildcard
    assert client.get(URL, params={"status": "gone"}, headers=headers).status_code == 422
    assert client.get(URL, params={"limit": 201}, headers=headers).status_code == 422


def test_the_list_says_how_many_alerts_each_address_has_not_closed(
    client, db_session, settings, analyst
):
    user, headers = analyst
    monitor = register(client, headers).json()["monitor"]
    quiet = register(client, headers, address=OTHER_ADDRESS).json()["monitor"]
    db_session.rollback()
    row = db_session.get(MonitoredAddress, monitor["id"])
    alert_for(db_session, row, key="a", status="open")
    alert_for(db_session, row, key="b", status="acknowledged")
    alert_for(db_session, row, key="c", status="closed", resolution="false_positive")
    db_session.rollback()

    items = {m["id"]: m for m in listed(client, headers)["items"]}

    assert (
        items[monitor["id"]]["unresolved_alerts"] == 2
        and items[quiet["id"]]["unresolved_alerts"] == 0
    )
    assert (
        client.get(f"{URL}/{monitor['id']}", headers=headers).json()["monitor"]["unresolved_alerts"]
        == 2
    )


def test_one_address_is_read_by_its_id(client, analyst):
    _, headers = analyst
    monitor = register(client, headers, name="Cold").json()["monitor"]

    got = client.get(f"{URL}/{monitor['id']}", headers=headers)

    assert got.status_code == 200 and got.json() == {"success": True, "monitor": monitor}
    missing = client.get(f"{URL}/{monitor['id'] + 100}", headers=headers)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "monitor_not_found"
    assert client.get(f"{URL}/abc", headers=headers).status_code == 422


# --- changing -----------------------------------------------------------------------------------


def test_what_is_sent_is_what_changes_and_the_change_is_audited(client, db_session, analyst):
    user, headers = analyst
    monitor = register(client, headers, name="Old").json()["monitor"]

    response = client.patch(
        f"{URL}/{monitor['id']}",
        json={"name": "New", "check_interval_seconds": 1800, "min_severity": "CRITICAL"},
        headers=headers,
    )

    assert response.status_code == 200
    changed = response.json()["monitor"]
    assert (changed["name"], changed["check_interval_seconds"], changed["min_severity"]) == (
        "New",
        1800,
        "CRITICAL",
    )
    assert changed["depth"] == 1 and changed["status"] == "active"  # untouched
    (entry,) = [r for r in rows(db_session, AuditLog) if r.action == "monitor.update"]
    assert entry.actor_id == user.id and entry.target_id == str(monitor["id"])
    assert entry.details["changes"] == {
        "name": {"from": "Old", "to": "New"},
        "check_interval_seconds": {"from": 21600, "to": 1800},
        "min_severity": {"from": "MEDIUM", "to": "CRITICAL"},
    }
    assert entry.details["address"] == ADDRESS


def test_a_name_can_be_removed_by_sending_null_and_is_kept_by_leaving_it_out(client, analyst):
    _, headers = analyst
    monitor = register(client, headers, name="Keep").json()["monitor"]
    base = f"{URL}/{monitor['id']}"

    assert (
        client.patch(base, json={"depth": 2}, headers=headers).json()["monitor"]["name"] == "Keep"
    )
    assert (
        client.patch(base, json={"name": None}, headers=headers).json()["monitor"]["name"] is None
    )


def test_pausing_stops_the_clock_and_resuming_starts_it(client, analyst):
    _, headers = analyst
    monitor = register(client, headers).json()["monitor"]
    base = f"{URL}/{monitor['id']}"

    paused = client.patch(base, json={"status": "paused"}, headers=headers).json()["monitor"]
    assert paused["status"] == "paused" and paused["next_check_at"] is None

    resumed = client.patch(base, json={"status": "active"}, headers=headers).json()["monitor"]
    assert resumed["status"] == "active" and resumed["next_check_at"]


def test_a_change_that_changes_nothing_is_not_audited(client, db_session, analyst):
    _, headers = analyst
    monitor = register(client, headers, name="Same").json()["monitor"]

    response = client.patch(
        f"{URL}/{monitor['id']}", json={"name": "Same", "depth": 1}, headers=headers
    )

    assert response.status_code == 200
    assert [r for r in rows(db_session, AuditLog) if r.action == "monitor.update"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"status": "stopped"},
        {"check_interval_seconds": 10},
        {"depth": 0},
        {"depth": 9},
        {"min_severity": "huge"},
        {"name": "x" * 1001},
        {"colour": "red"},
        {"address": OTHER_ADDRESS},
        {"chain": "bsc"},
    ],
)
def test_a_bad_change_is_refused_and_nothing_of_it_is_kept(client, db_session, analyst, body):
    _, headers = analyst
    monitor = register(client, headers, name="Before").json()["monitor"]

    response = client.patch(
        f"{URL}/{monitor['id']}", json={"name": "After", **body}, headers=headers
    )

    assert response.status_code == 422
    assert client.get(f"{URL}/{monitor['id']}", headers=headers).json()["monitor"] == monitor


def test_an_empty_change_is_refused(client, analyst):
    _, headers = analyst
    monitor = register(client, headers).json()["monitor"]
    response = client.patch(f"{URL}/{monitor['id']}", json={}, headers=headers)
    assert response.status_code == 422 and response.json()["error"]["code"] == "nothing_to_change"


# --- stopping -----------------------------------------------------------------------------------


def test_stopping_keeps_the_alerts_and_frees_the_address_to_be_watched_again(
    client, db_session, analyst
):
    user, headers = analyst
    monitor = register(client, headers).json()["monitor"]
    db_session.rollback()
    alert_for(db_session, db_session.get(MonitoredAddress, monitor["id"]))
    db_session.rollback()

    response = client.delete(f"{URL}/{monitor['id']}", headers=headers)

    assert response.status_code == 204 and response.content == b""
    assert client.get(f"{URL}/{monitor['id']}", headers=headers).status_code == 404
    (alert,) = rows(db_session, Alert)
    assert alert.monitored_address_id is None and alert.address == ADDRESS
    (entry,) = [r for r in rows(db_session, AuditLog) if r.action == "monitor.delete"]
    assert entry.details == {"chain": "ethereum", "address": ADDRESS}
    assert entry.actor_id == user.id and entry.target_id == str(monitor["id"])
    assert register(client, headers).status_code == 201


def test_stopping_what_is_not_there_is_a_404(client, analyst):
    _, headers = analyst
    response = client.delete(f"{URL}/999", headers=headers)
    assert response.status_code == 404 and response.json()["error"]["code"] == "monitor_not_found"


# --- checking now -------------------------------------------------------------------------------


def test_a_check_can_be_asked_for_now_and_asking_twice_returns_the_same_job(
    client, db_session, analyst
):
    _, headers = analyst
    monitor = register(client, headers).json()["monitor"]
    # the first check, queued when it was registered, is over
    db_session.execute(update(FetchJob).values(status="SUCCEEDED", dedupe_key=None))
    db_session.commit()

    first = client.post(f"{URL}/{monitor['id']}/check", headers=headers)
    second = client.post(f"{URL}/{monitor['id']}/check", headers=headers)

    assert first.status_code == 202 and first.json()["already_queued"] is False
    assert second.status_code == 202 and second.json() == {
        "success": True,
        "job_id": first.json()["job_id"],
        "already_queued": True,
    }
    (job,) = [j for j in rows(db_session, FetchJob) if j.status == "QUEUED"]
    assert job.id == first.json()["job_id"] and job.payload["reason"] == "manual"
    entries = [r for r in rows(db_session, AuditLog) if r.action == "monitor.check"]
    assert [e.details["already_queued"] for e in entries] == [False, True]


def test_a_paused_address_cannot_be_checked_until_it_is_resumed(client, analyst):
    _, headers = analyst
    monitor = register(client, headers).json()["monitor"]
    base = f"{URL}/{monitor['id']}"
    client.patch(base, json={"status": "paused"}, headers=headers)

    response = client.post(f"{base}/check", headers=headers)

    assert response.status_code == 409 and response.json()["error"]["code"] == "monitor_paused"
    client.patch(base, json={"status": "active"}, headers=headers)
    assert client.post(f"{base}/check", headers=headers).status_code == 202


# --- other organizations ------------------------------------------------------------------------


def test_another_organization_sees_nothing_and_changes_nothing(client, db_session, analyst):
    _, mine = analyst
    monitor = register(client, mine, name="Mine").json()["monitor"]
    _, theirs = person(client, db_session, Role.ADMIN, organization="Other Org")
    base = f"{URL}/{monitor['id']}"

    assert listed(client, theirs)["total"] == 0
    assert client.get(base, headers=theirs).status_code == 404
    assert client.patch(base, json={"name": "Taken"}, headers=theirs).status_code == 404
    assert client.patch(base, json={"status": "paused"}, headers=theirs).status_code == 404
    assert client.delete(base, headers=theirs).status_code == 404
    assert client.post(f"{base}/check", headers=theirs).status_code == 404

    assert client.get(base, headers=mine).json()["monitor"] == monitor
    # and the same address is theirs to watch, separately
    assert register(client, theirs).status_code == 201


def test_a_person_of_one_organization_cannot_see_the_others_alert_counts(
    client, db_session, settings, analyst
):
    _, mine = analyst
    theirs_user = make_user(db_session, "sam@example.com", Role.ANALYST, "Other Org")
    theirs_monitor = watch(db_session, settings, theirs_user)
    alert_for(db_session, theirs_monitor)
    db_session.rollback()

    assert listed(client, mine)["items"] == []
    assert client.get(f"{URL}/{theirs_monitor.id}", headers=mine).status_code == 404
