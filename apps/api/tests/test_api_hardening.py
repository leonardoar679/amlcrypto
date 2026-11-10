"""What every response carries, and how much any request may send.

Two guarantees that hold for the whole API and so are checked on the whole API: no answer, an error
included, goes out without the headers that keep it from being cached or read as something else, and
no request is allowed to make the server read more than the limit of its body.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.api.headers import SECURITY_HEADERS
from app.core.config import Settings
from app.main import create_app
from tests.access_helpers import person
from tests.conftest import TEST_SECRET

LIMIT = 2_048


@pytest.fixture
def small(database_url, tmp_path, committing_db):
    """The application with a small body limit and a webhook that may send more. (It commits for
    real, so committing_db empties what it wrote where the tests share one database.)"""
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        secret_key=TEST_SECRET,
        data_dir=tmp_path,
        max_request_body_bytes=LIMIT,
        webhook_max_body_bytes=4 * LIMIT,
        alchemy_webhook_signing_key="a-signing-key-the-tests-know",
    )
    application = create_app(settings)
    try:
        yield application
    finally:
        application.state.engine.dispose()


@pytest.fixture
def small_client(small):
    return TestClient(small, raise_server_exceptions=False)


def has_the_headers(response) -> bool:
    return all(response.headers.get(name) == value for name, value in SECURITY_HEADERS.items())


# --- headers ------------------------------------------------------------------------------------


def test_every_kind_of_answer_carries_the_security_headers(client, db_session):
    _, headers = person(client, db_session, "viewer")
    answers = {
        "success": client.get("/api/v1/auth/me", headers=headers),
        "public": client.get("/api/v1/health"),
        "unauthenticated": client.get("/api/v1/alerts"),
        "forbidden": client.get("/api/v1/users", headers=headers),
        "not found": client.get("/api/v1/alerts/12345", headers=headers),
        "no such route": client.get("/api/v1/nothing-here"),
        "wrong method": client.put("/api/v1/alerts", headers=headers),
        "invalid": client.get("/api/v1/alerts", params={"limit": "x"}, headers=headers),
        "no body": client.post("/api/v1/auth/login"),
    }
    assert {name: has_the_headers(response) for name, response in answers.items()} == {
        name: True for name in answers
    }
    assert {response.status_code for response in answers.values()} >= {200, 401, 403, 404, 405, 422}


def test_a_server_error_carries_them_too(app):
    @app.get("/api/v1/boom")
    def boom():
        raise RuntimeError("a bug")

    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/boom")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert has_the_headers(response)
    assert "a bug" not in response.text


def test_a_route_that_sets_one_of_them_itself_keeps_its_own(app):
    @app.get("/api/v1/cached")
    def cached():
        from fastapi.responses import JSONResponse

        return JSONResponse({}, headers={"Cache-Control": "max-age=60"})

    response = TestClient(app).get("/api/v1/cached")

    assert response.headers["Cache-Control"] == "max-age=60"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers.get_list("Cache-Control") == ["max-age=60"]


# --- size ---------------------------------------------------------------------------------------


def post(client, body: bytes, path="/api/v1/auth/login", **headers):
    return client.post(path, content=body, headers={"content-type": "application/json", **headers})


def test_a_body_over_the_limit_is_refused_before_it_is_read(small_client, db_session):
    from sqlalchemy import func, select

    from app.database.models import AuditLog

    response = post(
        small_client, b'{"email": "a@example.com", "password": "' + b"x" * LIMIT + b'"}'
    )

    assert response.status_code == 413
    body = response.json()
    assert body["success"] is False and body["error"]["code"] == "payload_too_large"
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    assert has_the_headers(response)
    # the route never ran, so there is no failed sign-in to count
    assert db_session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_a_body_exactly_at_the_limit_is_read(small_client):
    start = b'{"email": "a@example.com", "password": "x", "pad": "'
    body = start + b"y" * (LIMIT - len(start) - 2) + b'"}'
    assert len(body) == LIMIT

    assert post(small_client, body).status_code == 401  # read, and nobody has that email
    assert post(small_client, body + b" ").status_code == 413


def test_a_body_sent_in_chunks_is_cut_off_when_it_passes_the_limit(small_client):
    def chunks():
        for _ in range(10):
            yield b'{"email": "a@example.com", "password": "' + b"x" * 400 + b'"}'

    response = small_client.post(
        "/api/v1/auth/login", content=chunks(), headers={"content-type": "application/json"}
    )

    assert response.status_code == 413


def test_a_body_that_lies_about_its_length_is_still_cut_off(small_client):
    body = b'{"a": "' + b"x" * 5 * LIMIT + b'"}'
    response = post(small_client, body, **{"content-length": "20"})
    # whatever the server makes of a length that does not match, it does not hold the whole body
    assert response.status_code in (400, 413, 422)


def test_a_read_with_a_body_is_limited_too(small_client):
    response = small_client.request("GET", "/api/v1/health", content=b"x" * (LIMIT + 1))
    assert response.status_code == 413


def test_the_webhook_has_its_own_larger_limit(small_client):
    key = b"a-signing-key-the-tests-know"

    def send(size: int):
        body = json.dumps({"id": "e", "type": "ADDRESS_ACTIVITY", "pad": "x" * size}).encode()
        signature = hmac.new(key, body, hashlib.sha256).hexdigest()
        return small_client.post(
            "/api/v1/webhooks/alchemy",
            content=body,
            headers={"X-Alchemy-Signature": signature, "content-type": "application/json"},
        )

    # over the general limit, under the webhook's: read, and then judged on what it says
    assert send(3 * LIMIT).status_code == 422
    assert send(5 * LIMIT).status_code == 413


def test_an_ordinary_request_is_not_in_the_way(client, db_session):
    _, headers = person(client, db_session, "analyst")
    response = client.post(
        "/api/v1/cases", json={"title": "x" * 200, "description": "y" * 4_900}, headers=headers
    )
    assert response.status_code == 201


# --- what the database refuses ------------------------------------------------------------


def test_a_value_the_database_cannot_hold_is_a_422_and_a_database_that_is_busy_a_503(app):
    from sqlalchemy.exc import DataError, OperationalError

    @app.get("/api/v1/too-long")
    def too_long():
        raise DataError("INSERT ...", {}, Exception("Data too long for column 'name'"))

    @app.get("/api/v1/locked")
    def locked():
        raise OperationalError("INSERT ...", {}, Exception("database is locked"))

    http = TestClient(app, raise_server_exceptions=False)
    refused, busy = http.get("/api/v1/too-long"), http.get("/api/v1/locked")

    assert (refused.status_code, refused.json()["error"]["code"]) == (422, "unstorable_value")
    assert (busy.status_code, busy.json()["error"]["code"]) == (503, "service_unavailable")
    assert busy.headers["Retry-After"]
    assert "Data too long" not in refused.text and "locked" not in busy.text.replace("busy", "")
    assert has_the_headers(refused) and has_the_headers(busy)


# --- the middleware itself, message by message ---------------------------------------------------


async def call(
    middleware, chunks: list[bytes], headers: dict[str, str] | None = None
) -> list[dict]:
    """Send a request of these chunks through the middleware; every ASGI message it sent back."""
    pending = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]
    sent: list[dict] = []

    async def receive():
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "query_string": b"",
        "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
        "scheme": "http",
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
        "root_path": "",
    }
    await middleware(scope, receive, send)
    return sent


def statuses_sent(sent: list[dict]) -> list[int]:
    return [m["status"] for m in sent if m["type"] == "http.response.start"]


async def answering(status: int):
    async def app(scope, receive, send):
        while (await receive())["type"] == "http.request":
            pass
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"x"})

    return app


async def silent(scope, receive, send):
    while (await receive())["type"] == "http.request":
        pass


def test_a_body_cut_off_in_chunks_gets_one_413_whatever_the_application_then_says():
    from app.api.middleware import BodyLimitMiddleware
    from tests.helpers import run

    async def scenario():
        app = await answering(400)  # what FastAPI says of a body it could not read
        return await call(BodyLimitMiddleware(app, limit=10), [b"x" * 6, b"x" * 6, b"x" * 6])

    sent = run(scenario())

    assert statuses_sent(sent) == [413]
    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]


def test_a_body_cut_off_is_refused_even_when_the_application_says_nothing():
    from app.api.middleware import BodyLimitMiddleware
    from tests.helpers import run

    sent = run(call(BodyLimitMiddleware(silent, limit=10), [b"x" * 6, b"x" * 6]))

    assert statuses_sent(sent) == [413]
    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]


def test_a_body_within_the_limit_is_answered_by_the_application_untouched():
    from app.api.middleware import BodyLimitMiddleware
    from tests.helpers import run

    async def scenario():
        app = await answering(201)
        return await call(BodyLimitMiddleware(app, limit=12), [b"x" * 6, b"x" * 6])

    assert statuses_sent(run(scenario())) == [201]


def test_a_declared_length_over_the_limit_never_reaches_the_application():
    from app.api.middleware import BodyLimitMiddleware
    from tests.helpers import run

    reached = []

    async def app(scope, receive, send):
        reached.append(True)

    sent = run(call(BodyLimitMiddleware(app, limit=10), [], {"content-length": "11"}))

    assert statuses_sent(sent) == [413] and reached == []


def test_the_webhooks_own_cap_speaks_for_a_body_over_it_and_under_the_general_limit(
    small, small_client
):
    """The webhook's limit (below the general one here) is the route's to enforce, in its own words:
    the middleware must not cut the body short on its behalf."""
    key = b"a-signing-key-the-tests-know"
    app = small
    app.state.settings = app.state.settings.model_copy(update={"webhook_max_body_bytes": 1_024})
    body = json.dumps({"id": "e", "pad": "x" * 1_500}).encode()
    signature = hmac.new(key, body, hashlib.sha256).hexdigest()

    response = small_client.post(
        "/api/v1/webhooks/alchemy",
        content=body,
        headers={"X-Alchemy-Signature": signature, "content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["message"] == "The event is too large."


def test_a_path_with_a_smaller_limit_of_its_own_is_not_cut_below_the_general_one():
    """`larger` is for paths that may send more; one that is listed with less than the general
    limit is still allowed the general limit (the route behind it may be stricter, and says so)."""
    from app.api.middleware import BodyLimitMiddleware
    from tests.helpers import run

    async def scenario():
        app = await answering(202)
        middleware = BodyLimitMiddleware(app, limit=10, larger={"/x": 5})
        return await call(middleware, [b"x" * 8])

    assert statuses_sent(run(scenario())) == [202]


def test_an_error_that_sets_one_of_the_headers_itself_keeps_its_own():
    from starlette.requests import Request

    from app.api.errors import error_response

    response = error_response(
        Request({"type": "http", "headers": [], "method": "GET", "path": "/"}),
        409,
        "conflict",
        "Already there.",
        headers={"Cache-Control": "max-age=5"},
    )

    assert response.headers["cache-control"] == "max-age=5"
    assert response.headers["x-content-type-options"] == "nosniff"
