from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app import __version__
from app.api.deps import get_session
from app.database import migrations


def test_health_is_public_and_checks_the_database(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_reports_a_database_failure_with_503(app, client):
    class BrokenSession:
        def execute(self, *_args, **_kwargs):
            raise OperationalError("SELECT 1", {}, Exception("database is down"))

    def broken_session():
        yield BrokenSession()

    app.dependency_overrides[get_session] = broken_session
    response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "error"}


def test_version_reports_the_schema_revision(client):
    body = client.get("/api/v1/version").json()
    assert body["version"] == __version__
    assert body["schema_head"] == migrations.head_revision()
    assert body["schema_revision"] == body["schema_head"]
    assert body["schema_up_to_date"] is True


def test_request_id_is_generated_and_echoed(client):
    generated = client.get("/api/v1/health").headers["X-Request-ID"]
    assert len(generated) == 32

    echoed = client.get("/api/v1/health", headers={"X-Request-ID": "trace-12345678"})
    assert echoed.headers["X-Request-ID"] == "trace-12345678"


@pytest.mark.parametrize("bad", ["short", "has spaces in it", "x" * 65, "new\nline-injection"])
def test_malformed_request_ids_are_replaced(client, bad):
    try:
        response = client.get("/api/v1/health", headers={"X-Request-ID": bad})
    except Exception:  # noqa: BLE001 - the HTTP client itself may refuse an illegal header value
        pytest.skip("client refused to send the header")
    assert response.headers["X-Request-ID"] != bad


def test_unknown_route_uses_the_error_envelope(client):
    response = client.get("/api/v1/nope")
    assert response.status_code == 404
    error = response.json()["error"]
    assert response.json()["success"] is False
    assert error["code"] == "not_found"
    assert error["request_id"] == response.headers["X-Request-ID"]


def test_wrong_method_uses_the_error_envelope(client):
    response = client.post("/api/v1/health")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_validation_errors_never_echo_the_submitted_value(client):
    secret = "hunter2-" + "x" * 2000  # longer than the 1024 limit, so it fails validation
    response = client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": secret}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"][0]["field"] == "body.password"
    assert "hunter2" not in response.text


def test_unexpected_errors_become_a_generic_500(app):
    @app.get("/api/v1/_boom")
    def boom():
        raise RuntimeError("internal detail that must not leak")

    with TestClient(app, raise_server_exceptions=False) as quiet_client:
        response = quiet_client.get("/api/v1/_boom")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "internal detail" not in response.text
