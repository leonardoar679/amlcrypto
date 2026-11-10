"""Hostile input: nothing sent to any route is ever answered with a server error.

A 5xx is the API admitting that it did not expect what it was sent. For a client that is one more
thing to retry; for an operator it is a page in the night, and for an attacker it is a map of where
the code is soft. So every route is called, as an administrator (who passes every access check, so
the input reaches the code behind it), with the working request from tests/catalog.py changed in one
place at a time: each path segment, each query parameter, each field the body model declares, the
whole body, its content type, and the headers the API reads.

The answer must be below 500 whatever was sent. It is not checked that the answer is the right one
(an input that happens to be valid is allowed to work), only that the server did not fall over.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core import security
from app.core.config import Settings
from app.main import create_app
from tests.catalog import CATALOG, V1
from tests.hostile import (
    BODIES,
    CONTENT_TYPES,
    HEADER_VALUES,
    JSON_VALUES,
    fields_of,
    path_variants,
    query_variants,
    with_query,
    with_value,
    without,
)
from tests.test_access_control import api_routes
from tests.world import Fresh, World

# A server error is any status from here up.
SERVER_ERROR = 500


@pytest.fixture(autouse=True)
def _cheap_password_hash(monkeypatch):
    """Argon2 is meant to be slow, and this sends thousands of passwords. What is being tested is
    what the routes do with them, not how long the hash takes."""
    monkeypatch.setattr(
        security,
        "_password_hash",
        PasswordHash((Argon2Hasher(time_cost=1, memory_cost=64, parallelism=1),)),
    )


@pytest.fixture
def world(client, db_session, settings) -> World:
    return World(client, db_session, settings)


@pytest.fixture
def hostile(app) -> TestClient:
    """A client that reports a server error as an answer instead of raising it."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def routes(app):
    return {(method, path): route for method, path, route in api_routes(app)}


class Sweep:
    """Sends requests, and remembers how many and which ones fell over."""

    def __init__(self, client: TestClient, headers: dict[str, str]) -> None:
        self.client = client
        self.headers = headers
        self.sent = 0
        self.failed: list[tuple] = []

    def send(self, label: tuple, method: str, url: str, **kwargs) -> int:
        headers = {**self.headers, **kwargs.pop("headers", {})}
        response = self.client.request(method, url, headers=headers, **kwargs)
        self.sent += 1
        if response.status_code >= SERVER_ERROR:
            self.failed.append((*label, response.status_code))
        return response.status_code

    def json(self, label: tuple, method: str, url: str, body, **kwargs) -> int:
        return self.send(
            label,
            method,
            url,
            content=json.dumps(body).encode(),
            headers={"content-type": "application/json", **kwargs.pop("headers", {})},
            **kwargs,
        )


@pytest.fixture
def sweep(world, hostile) -> Sweep:
    return Sweep(hostile, world.acme.admin)


def base_calls(world: World):
    """(method, template, route-less call) for every route, each on records made for it."""
    for (method, path), build in CATALOG.items():
        yield method, path, build(Fresh(world, world.acme))


# --- the working requests -----------------------------------------------------------------------

# What a working request is answered with where it is not a success.
EXPECTED = {
    ("POST", f"{V1}/auth/password"): 403,  # the current password is wrong, on purpose
    ("POST", f"{V1}/providers/{{name}}/probe"): 404,  # there is no provider called nope
    ("POST", f"{V1}/webhooks/alchemy"): 404,  # there is no signing key
}


def test_every_working_request_works(world):
    wrong = []
    for method, path, call in base_calls(world):
        response = world.send(call, world.acme.admin)
        expected = EXPECTED.get((method, path))
        good = response.status_code == expected if expected else response.status_code < 300
        if not good:
            wrong.append((method, path, response.status_code, response.text[:200]))
    assert not wrong, wrong


# --- each place in a request --------------------------------------------------------------------


def test_hostile_path_values(world, sweep, routes):
    for method, path, call in base_calls(world):
        for name, value, url in path_variants(path, call.path):
            sweep.send(
                (method, path, name, value), method, with_query(url, call.params), json=call.json
            )
    assert sweep.sent >= 1000, f"only {sweep.sent} requests sent"
    assert not sweep.failed, sweep.failed


def test_hostile_query_values(world, sweep, routes):
    for method, path, call in base_calls(world):
        names = [parameter.name for parameter in routes[(method, path)].dependant.query_params]
        for name, value, params in query_variants(names, call.params):
            sweep.send(
                (method, path, name, value), method, with_query(call.path, params), json=call.json
            )
    assert sweep.sent >= 1000, f"only {sweep.sent} requests sent"
    assert not sweep.failed, sweep.failed


def test_hostile_body_fields(world, sweep, routes):
    for method, path, call in base_calls(world):
        field = routes[(method, path)].body_field
        if field is None:
            continue
        url = with_query(call.path, call.params)
        for place in fields_of(field.field_info.annotation):
            for value in JSON_VALUES:
                sweep.json(
                    (method, path, place, repr(value)[:40]),
                    method,
                    url,
                    with_value(call.json, place, value),
                )
            sweep.json((method, path, place, "missing"), method, url, without(call.json, place))
        sweep.json((method, path, "unknown field"), method, url, {**call.json, "zz_unknown": 1})
    assert sweep.sent >= 2000, f"only {sweep.sent} requests sent"
    assert not sweep.failed, sweep.failed


def test_hostile_whole_bodies_and_content_types(world, sweep, routes):
    for method, path, call in base_calls(world):
        if routes[(method, path)].body_field is None:
            continue
        url = with_query(call.path, call.params)
        for name, body in BODIES:
            sweep.send(
                (method, path, name),
                method,
                url,
                content=body,
                headers={"content-type": "application/json"},
            )
        for content_type in CONTENT_TYPES:
            sweep.send(
                (method, path, "content type", content_type),
                method,
                url,
                content=json.dumps(call.json).encode(),
                headers={"content-type": content_type},
            )
        # a body where none is expected, and a huge one
        sweep.send((method, path, "no content type"), method, url, content=b'{"a": 1}')
        sweep.send(
            (method, path, "huge"),
            method,
            url,
            content=b'{"a": "' + b"x" * 5_000_000 + b'"}',
            headers={"content-type": "application/json"},
        )
    for method, path, call in base_calls(world):
        if routes[(method, path)].body_field is None and method == "GET":
            sweep.send(
                (method, path, "body on a read"),
                method,
                with_query(call.path, call.params),
                content=b"{}",
            )
    assert sweep.sent >= 500, f"only {sweep.sent} requests sent"
    assert not sweep.failed, sweep.failed


def test_hostile_headers(world, hostile):
    """What the API reads from a request's headers: who is calling, and where from. Values are sent
    as bytes, which is how a client that does not care what a header may hold would send them."""
    problems, sent = [], 0
    reads = {
        ("GET", f"{V1}/auth/me"),
        ("GET", f"{V1}/alerts"),
        ("POST", f"{V1}/cases"),
        ("POST", f"{V1}/auth/login"),
    }
    calls = [(m, p, c) for m, p, c in base_calls(world) if (m, p) in reads]
    for name in ("Authorization", "X-API-Key"):
        for value in HEADER_VALUES:
            for method, path, call in calls:
                response = hostile.request(
                    method, call.path, json=call.json, headers={name: value.encode("utf-8")}
                )
                sent += 1
                if response.status_code >= SERVER_ERROR:
                    problems.append((name, value[:30], method, path, response.status_code))
    # the headers that end up in the audit log or in what is logged: a refusal is written to the
    # audit log with the caller's address and agent, so ask for what is refused
    forwarded = ["1.2.3.4, " * 500, "::1, 999.1.1.1", "unknown", "[::1]:80", "1.2.3.4,,,5.6.7.8"]
    for name in ("User-Agent", "X-Forwarded-For", "X-Request-ID", "Referer", "Origin", "Host"):
        for value in [*HEADER_VALUES, *forwarded]:
            for headers in (world.acme.admin, world.acme.viewer, {}):
                response = hostile.get(
                    f"{V1}/users", headers={**headers, name: value.encode("utf-8")}
                )
                sent += 1
                if response.status_code >= SERVER_ERROR:
                    problems.append((name, value[:30], response.status_code))
    assert sent >= 500, f"only {sent} requests sent"
    assert not problems, problems


def test_methods_and_paths_that_are_not_routes(world, hostile):
    problems, sent = [], 0
    paths = {call.path for _, _, call in base_calls(world)}
    paths |= {
        "/",
        "//",
        f"{V1}",
        f"{V1}/",
        f"{V1}//alerts",
        f"{V1}/alerts/",
        f"{V1}/%00",
        f"{V1}/..",
    }
    paths |= {f"{V1}/alerts/1/", f"{V1}/ALERTS", f"{V1}/alerts%2F1", "/api", "/api/v2/alerts"}
    for path in sorted(paths):
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            response = hostile.request(method, path, headers=world.acme.admin)
            sent += 1
            if response.status_code >= SERVER_ERROR:
                problems.append((method, path, response.status_code))
    assert sent >= 400, f"only {sent} requests sent"
    assert not problems, problems


# --- the provider's webhook, with its signing key set -------------------------------------------

SIGNING_KEY = "a-signing-key-the-tests-know"


def signed(body: bytes) -> dict[str, str]:
    digest = hmac.new(SIGNING_KEY.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Alchemy-Signature": digest, "content-type": "application/json"}


@pytest.fixture
def listening(database_url, tmp_path, committing_db):
    """The application with the webhook switched on."""
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        secret_key="test-secret-key-that-is-comfortably-over-32-characters",
        data_dir=tmp_path,
        alchemy_webhook_signing_key=SIGNING_KEY,
    )
    application = create_app(settings)
    try:
        yield TestClient(application, raise_server_exceptions=False)
    finally:
        application.state.engine.dispose()


def test_a_signed_webhook_survives_hostile_bodies(listening):
    event = {
        "webhookId": "wh_1",
        "id": "evt",
        "type": "ADDRESS_ACTIVITY",
        "event": {"network": "ETH_MAINNET", "activity": []},
    }
    bodies = [body for _, body in BODIES]
    bodies += [
        json.dumps(with_value(event, ("event", "network"), value)).encode() for value in JSON_VALUES
    ]
    bodies += [
        json.dumps({**event, "event": {"activity": value}}).encode() for value in JSON_VALUES
    ]
    bodies += [
        json.dumps({"event": {"activity": [{"fromAddress": value, "toAddress": value}]}}).encode()
        for value in JSON_VALUES
    ]
    problems = []
    for body in bodies:
        response = listening.post(f"{V1}/webhooks/alchemy", content=body, headers=signed(body))
        if response.status_code >= SERVER_ERROR:
            problems.append((body[:60], response.status_code))
    assert len(bodies) > 150
    assert not problems, problems
