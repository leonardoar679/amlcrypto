"""The same request many times at once.

A uniqueness rule that is checked and then acted on is a race: two requests both see that there is
nothing yet and both go ahead. The answer to the loser must be the ordinary "already there" (409, or
the same thing returned), never a server error, and the rule must hold afterwards. Each scenario
sends the request from several threads that are released together.

SQLite only: on MySQL the tests share one connection, which threads cannot.
"""

from __future__ import annotations

import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.database.models import AuditLog, Case
from tests.catalog import V1
from tests.conftest import MYSQL_URL
from tests.world import World

pytestmark = pytest.mark.skipif(bool(MYSQL_URL), reason="threads need connections of their own")

THREADS = 8


@pytest.fixture
def world(client, db_session, settings) -> World:
    return World(client, db_session, settings)


def together(world: World, method: str, path: str, headers, *, json=None, times: int = THREADS):
    """Send one request `times` times, all released at the same moment; the responses."""
    barrier = threading.Barrier(times)

    def send(_):
        barrier.wait(timeout=10)
        return world.client.request(method, V1 + path, json=json, headers=headers)

    with ThreadPoolExecutor(max_workers=times) as pool:
        return list(pool.map(send, range(times)))


def statuses(responses) -> Counter:
    return Counter(response.status_code for response in responses)


def no_server_error(responses) -> None:
    assert all(response.status_code < 500 for response in responses), [
        (response.status_code, response.text[:200]) for response in responses
    ]


def count(db_session, model, *where) -> int:
    db_session.rollback()
    found = db_session.scalar(select(func.count()).select_from(model).where(*where))
    db_session.rollback()
    return found


# --- one winner ---------------------------------------------------------------------------------


def test_the_same_person_added_at_once_is_added_once(world):
    body = {"email": "twice@example.com", "password": "a password of 12", "role": "viewer"}
    responses = together(world, "POST", "/users", world.acme.admin, json=body)

    no_server_error(responses)
    assert statuses(responses) == {201: 1, 409: THREADS - 1}
    users = world.client.get(f"{V1}/users", headers=world.acme.admin).json()["users"]
    assert [user["email"] for user in users].count("twice@example.com") == 1


def test_the_same_address_watched_at_once_is_watched_once(world):
    body = {"chain": "ethereum", "address": world.address(world.acme)}
    responses = together(world, "POST", "/monitoring/addresses", world.acme.analyst, json=body)

    no_server_error(responses)
    assert statuses(responses) == {201: 1, 409: THREADS - 1}


def test_the_same_alert_opened_as_a_case_at_once_is_opened_once(world, db_session):
    alert = world.alert(world.acme)
    responses = together(
        world,
        "POST",
        "/cases",
        world.acme.analyst,
        json={"title": "From the alert", "alert_id": alert},
    )

    no_server_error(responses)
    assert statuses(responses)[201] == 1
    assert count(db_session, Case, Case.source_alert_id == alert) == 1


def test_the_same_link_added_at_once_is_added_once(world):
    case = world.case(world.acme)
    body = {"kind": "address", "chain": "ethereum", "reference": world.address(world.acme)}
    responses = together(world, "POST", f"/cases/{case}/links", world.acme.analyst, json=body)

    no_server_error(responses)
    assert statuses(responses) == {201: 1, 409: THREADS - 1}
    links = world.client.get(f"{V1}/cases/{case}", headers=world.acme.analyst).json()["case"][
        "links"
    ]
    assert len(links) == 1


# --- one thing, asked for many times ------------------------------------------------------------


def test_the_same_screening_asked_for_at_once_is_one_screening(world):
    body = {
        "chain": "ethereum",
        "address": world.address(world.acme),
        "options": {"update": False},
    }
    responses = together(world, "POST", "/screen/address", world.acme.analyst, json=body)

    no_server_error(responses)
    assert statuses(responses) == {202: THREADS}
    assert len({response.json()["screening_id"] for response in responses}) == 1
    assert [response.json()["already_requested"] for response in responses].count(False) == 1


def test_the_same_graph_update_asked_for_at_once_is_one_job(world):
    body = {"chain": "ethereum", "address": world.address(world.acme)}
    responses = together(world, "POST", "/graph/update", world.acme.admin, json=body)

    no_server_error(responses)
    assert statuses(responses) == {202: THREADS}
    assert len({response.json()["job_id"] for response in responses}) == 1


# --- one change ---------------------------------------------------------------------------------


def test_an_alert_acknowledged_at_once_is_acknowledged_and_recorded_once_each_time_it_changed(
    world, db_session
):
    alert = world.alert(world.acme)
    responses = together(
        world, "PATCH", f"/alerts/{alert}", world.acme.analyst, json={"status": "acknowledged"}
    )

    no_server_error(responses)
    assert set(statuses(responses)) <= {200, 409}
    assert statuses(responses)[200] >= 1
    shown = world.client.get(f"{V1}/alerts/{alert}", headers=world.acme.analyst).json()["alert"]
    assert shown["status"] == "acknowledged"
    # the activity says "acknowledged" once: a change is recorded only when it changed something
    changes = [
        event
        for event in shown["activity"]
        if event["action"] == "alert.update" and event["details"].get("changes")
    ]
    assert len(changes) == 1


def test_an_alert_closed_two_ways_at_once_ends_up_closed_one_way(world):
    alert = world.alert(world.acme)
    barrier = threading.Barrier(2)

    def close(resolution):
        barrier.wait(timeout=10)
        return world.client.patch(
            f"{V1}/alerts/{alert}",
            json={"status": "closed", "resolution": resolution},
            headers=world.acme.analyst,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(close, ["true_positive", "false_positive"]))

    no_server_error(responses)
    shown = world.client.get(f"{V1}/alerts/{alert}", headers=world.acme.analyst).json()["alert"]
    assert shown["status"] == "closed"
    assert shown["resolution"] in {"true_positive", "false_positive"}


def test_a_key_revoked_at_once_is_revoked_and_recorded_once(world, db_session):
    key_id, secret = world.key(world.acme)
    responses = together(world, "DELETE", f"/api-keys/{key_id}", world.acme.admin)

    no_server_error(responses)
    assert statuses(responses) == {200: THREADS}
    assert count(db_session, AuditLog, AuditLog.action == "apikey.revoke") == 1
    assert world.client.get(f"{V1}/alerts", headers={"X-API-Key": secret}).status_code == 401


def test_a_watch_ended_at_once_ends_once(world):
    monitor = world.monitor(world.acme)
    responses = together(world, "DELETE", f"/monitoring/addresses/{monitor}", world.acme.analyst)

    no_server_error(responses)
    assert statuses(responses) == {204: 1, 404: THREADS - 1}


def test_a_link_removed_at_once_is_removed_once(world):
    case = world.case(world.acme)
    link = world.link(world.acme, case)
    responses = together(world, "DELETE", f"/cases/{case}/links/{link}", world.acme.analyst)

    no_server_error(responses)
    assert statuses(responses) == {204: 1, 404: THREADS - 1}


# --- sign-in ------------------------------------------------------------------------------------


def test_wrong_passwords_sent_at_once_lock_the_account_without_an_error(world):
    body = {"email": world.acme.admin_email, "password": "not the password"}
    responses = together(world, "POST", "/auth/login", {}, json=body, times=16)

    no_server_error(responses)
    assert set(statuses(responses)) <= {401, 429}
    assert world.client.post(f"{V1}/auth/login", json=body).status_code == 429
