"""Queueing the reading a graph needs, and following the job."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.database.models import FetchJob
from app.database.repositories import jobs as queue
from app.services.users import create_user

PASSWORD = "correct horse battery staple"
ME = "0x" + "a1" * 20


def login(client, db_session, email, organization):
    create_user(db_session, email=email, password=PASSWORD, organization_name=organization)
    token = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth(client, db_session):
    return login(client, db_session, "analyst@example.com", "Acme")


@pytest.fixture
def rival(client, db_session, auth):
    return login(client, db_session, "spy@example.com", "Rival")


def update(client, auth, **body):
    return client.post(
        "/api/v1/graph/update", json={"chain": "ethereum", "address": ME, **body}, headers=auth
    )


def test_the_endpoints_require_authentication(client):
    assert (
        client.post("/api/v1/graph/update", json={"chain": "ethereum", "address": ME}).status_code
        == 401
    )
    assert client.get("/api/v1/jobs/1").status_code == 401


def test_the_reading_is_queued_for_the_organization_with_the_limits_asked_for(
    client, auth, db_session
):
    response = update(
        client, auth, depth=2, days=30, counterparties=5, refresh=True, direction="in"
    )

    assert response.status_code == 202
    body = response.json()
    assert (
        body["success"] is True
        and body["status"] == "QUEUED"
        and body["already_requested"] is False
    )
    job = db_session.get(FetchJob, body["job_id"])
    assert job.job_type == "FETCH_COUNTERPARTIES" and job.organization_id is not None
    assert job.payload == {
        "chain": "ethereum",
        "address": ME,
        "depth": 2,
        "window_days": 30,
        "per_node": 5,
        "direction": "in",
        "refresh": True,
    }


def test_the_address_is_normalized_before_it_is_queued(client, auth, db_session):
    job = db_session.get(FetchJob, update(client, auth).json()["job_id"])
    payload = dict(job.payload)
    db_session.rollback()  # a session that has read holds the write lock until it ends

    assert payload["address"] == ME and payload["direction"] == "both"
    shouting = update(client, auth, address="0x" + "A1" * 20)
    assert shouting.json()["already_requested"] is True  # the same address, the same request


def test_asking_again_while_it_is_queued_returns_the_same_job(client, auth):
    first = update(client, auth, depth=2).json()
    again = update(client, auth, depth=2).json()
    other = update(client, auth, depth=3).json()

    assert again["job_id"] == first["job_id"] and again["already_requested"] is True
    assert other["job_id"] != first["job_id"]


def test_two_organizations_do_not_share_a_job(client, auth, rival):
    mine, theirs = update(client, auth).json(), update(client, rival).json()

    assert mine["job_id"] != theirs["job_id"] and theirs["already_requested"] is False


def test_nonsense_is_refused_before_anything_is_queued(client, auth, db_session):
    assert update(client, auth, address="0x123").status_code == 422
    assert update(client, auth, address="0x123").json()["error"]["code"] == "invalid_address"
    unknown = client.post(
        "/api/v1/graph/update", json={"chain": "dogecoin", "address": ME}, headers=auth
    )
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "unknown_chain"
    assert update(client, auth, depth=0).status_code == 422
    assert update(client, auth, direction="sideways").status_code == 422
    assert update(client, auth, include_everything=True).status_code == 422
    assert db_session.scalars(select(FetchJob)).all() == []


# --- following a job -----------------------------------------------------------------------------


def test_a_job_is_followed_from_queued_to_its_result(client, auth, db_session):
    job_id = update(client, auth).json()["job_id"]

    waiting = client.get(f"/api/v1/jobs/{job_id}", headers=auth).json()
    assert waiting["status"] == "QUEUED" and waiting["type"] == "FETCH_COUNTERPARTIES"
    assert (
        waiting["result"] is None and waiting["error"] is None and waiting["completed_at"] is None
    )
    assert waiting["attempts"] == 0 and waiting["max_attempts"] == 3

    queue.complete(db_session, job_id, {"nodes": 4, "complete": True})
    db_session.commit()

    done = client.get(f"/api/v1/jobs/{job_id}", headers=auth).json()
    assert done["status"] == "SUCCEEDED" and done["result"] == {"nodes": 4, "complete": True}
    assert done["completed_at"] is not None


def test_a_failed_job_says_why(client, auth, db_session):
    job_id = update(client, auth).json()["job_id"]
    job = db_session.get(FetchJob, job_id)
    job.status, job.error = "FAILED", "etherscan: HTTP 503"
    db_session.commit()

    body = client.get(f"/api/v1/jobs/{job_id}", headers=auth).json()

    assert body["status"] == "FAILED" and body["error"] == "etherscan: HTTP 503"


def test_only_the_organization_that_queued_a_job_can_see_it(client, auth, rival, db_session):
    job_id = update(client, auth).json()["job_id"]
    system = queue.enqueue(db_session, "PROVIDER_PROBE", {"provider": "esplora"})[0]

    assert client.get(f"/api/v1/jobs/{job_id}", headers=rival).status_code == 404
    assert client.get(f"/api/v1/jobs/{system.id}", headers=auth).status_code == 404  # a system job
    assert client.get("/api/v1/jobs/999999", headers=auth).status_code == 404
    assert client.get("/api/v1/jobs/abc", headers=auth).status_code == 422
    assert client.get(f"/api/v1/jobs/{job_id}", headers=auth).status_code == 200
