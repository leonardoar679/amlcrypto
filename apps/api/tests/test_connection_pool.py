"""A request holds one database connection at a time.

The pool is small and shared by every request in flight. A request that held a connection while it
waited for a second would, once enough of them were in flight, be waiting for connections that
other such requests hold, and none of them could go on until the pool gave up: the API would stall
for the length of its timeout and then answer 500. So the rule is one at a time: authenticating
gives its connection back before the route takes its own, and a route that reads and then writes
gives the read's back first.

The test runs the whole API on a pool of exactly one connection and calls every route. With any
route that needs two at once, it waits on itself, times out and is answered 503.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.catalog import CATALOG, V1
from tests.conftest import MYSQL_URL, TEST_SECRET
from tests.world import Fresh, World

pytestmark = pytest.mark.skipif(bool(MYSQL_URL), reason="the MySQL tests share one connection")


@pytest.fixture
def world(client, db_session, settings) -> World:
    return World(client, db_session, settings)


@pytest.fixture
def tight(database_url, tmp_path):
    """The application, on the same database, with a pool of one connection and no patience."""
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        secret_key=TEST_SECRET,
        data_dir=tmp_path,
        db_pool_size=1,
        db_max_overflow=0,
        db_pool_timeout_seconds=0.5,
    )
    application = create_app(settings)
    try:
        yield TestClient(application, raise_server_exceptions=False)
    finally:
        application.state.engine.dispose()


def test_every_route_works_on_a_pool_of_one_connection(world, tight):
    stuck = []
    for (method, path), build in CATALOG.items():
        call = build(Fresh(world, world.acme))
        response = tight.request(
            call.method,
            call.path,
            json=call.json,
            params=call.params,
            headers=world.acme.admin,
        )
        if response.status_code >= 500:
            stuck.append((method, path, response.status_code, response.text[:120]))
    assert not stuck, stuck


def test_a_program_with_a_key_works_on_a_pool_of_one_connection(world, tight):
    _, secret = world.key(world.acme, role="analyst")
    alert = world.alert(world.acme)
    headers = {"X-API-Key": secret}

    read = tight.get(f"{V1}/alerts/{alert}", headers=headers)
    written = tight.patch(f"{V1}/alerts/{alert}", json={"status": "acknowledged"}, headers=headers)
    refused = tight.get(f"{V1}/users", headers=headers)  # a refusal is audited, which writes

    assert (read.status_code, written.status_code, refused.status_code) == (200, 200, 403)


def test_a_refused_request_works_on_a_pool_of_one_connection(world, tight):
    refused = tight.get(f"{V1}/users", headers=world.acme.viewer)
    wrong_password = tight.post(
        f"{V1}/auth/login", json={"email": world.acme.admin_email, "password": "nope"}
    )
    assert (refused.status_code, wrong_password.status_code) == (403, 401)


def test_a_pool_that_cannot_serve_is_a_503_that_says_to_try_again(world, tight):
    held = tight.app.state.engine.connect()  # the only connection, kept
    try:
        response = tight.get(f"{V1}/alerts", headers=world.acme.admin)
    finally:
        held.close()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    assert response.headers["Retry-After"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    # and the service is back as soon as the connection is
    assert tight.get(f"{V1}/alerts", headers=world.acme.admin).status_code == 200


def test_a_burst_of_sign_ins_is_served_not_stalled(world, tight):
    from concurrent.futures import ThreadPoolExecutor

    body = {"email": world.acme.admin_email, "password": "not the password"}

    with ThreadPoolExecutor(max_workers=12) as pool:
        responses = list(pool.map(lambda _: tight.post(f"{V1}/auth/login", json=body), range(12)))

    assert all(response.status_code in (401, 429, 503) for response in responses)
    assert [r.status_code for r in responses].count(401) >= 1
