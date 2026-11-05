from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import Depends
from sqlalchemy import select

from app.api.deps import Principal, get_principal
from app.core.config import Settings
from app.core.security import create_access_token
from app.database.models import Organization, User
from app.services.users import UserError, create_user
from tests.conftest import TEST_SECRET

PASSWORD = "correct horse battery staple"


@pytest.fixture
def user(db_session) -> User:
    return create_user(
        db_session,
        email="Analyst@Example.com",
        password=PASSWORD,
        organization_name="Acme Compliance",
    )


@pytest.fixture
def whoami(app):
    @app.get("/api/v1/_whoami")
    def _whoami(principal: Principal = Depends(get_principal)):
        return {
            "user_id": principal.user_id,
            "organization_id": principal.organization_id,
            "email": principal.email,
        }


def login(client, email="analyst@example.com", password=PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _claims(user: User) -> dict:
    now = datetime.now(UTC)
    return {
        "sub": str(user.id),
        "org": user.organization_id,
        "iat": now,
        "exp": now + timedelta(hours=1),
    }


# --- login -----------------------------------------------------------------------------------


def test_login_returns_a_working_token(client, whoami, user, settings):
    response = login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.access_token_ttl_minutes * 60

    me = client.get("/api/v1/_whoami", headers=bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json() == {
        "user_id": user.id,
        "organization_id": user.organization_id,
        "email": "analyst@example.com",
    }


def test_login_records_the_time_and_ignores_email_case_and_padding(client, db_session, user):
    assert user.last_login_at is None
    assert login(client, email="  ANALYST@example.COM ").status_code == 200
    db_session.refresh(user)
    assert user.last_login_at is not None
    assert user.last_login_at.tzinfo is not None


def test_wrong_password_and_unknown_email_are_indistinguishable(client, user):
    wrong_password = login(client, password="not the password")
    unknown_email = login(client, email="nobody@example.com")
    assert wrong_password.status_code == unknown_email.status_code == 401

    def without_request_id(response):
        error = dict(response.json()["error"])
        error.pop("request_id")
        return error

    assert without_request_id(wrong_password) == without_request_id(unknown_email)
    assert wrong_password.json()["error"]["code"] == "invalid_credentials"


def test_disabled_user_cannot_log_in(client, db_session, user):
    user.is_active = False
    db_session.commit()
    assert login(client).status_code == 401


def test_disabled_organization_blocks_login(client, db_session, user):
    db_session.get(Organization, user.organization_id).is_active = False
    db_session.commit()
    assert login(client).status_code == 401


# --- bearer authentication -------------------------------------------------------------------


def test_missing_token_is_rejected_with_a_challenge(client, whoami):
    response = client.get("/api/v1/_whoami")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("token", ["garbage", "a.b.c", ""])
def test_malformed_tokens_are_rejected(client, whoami, token):
    response = client.get("/api/v1/_whoami", headers=bearer(token))
    assert response.status_code == 401


def test_expired_token_is_rejected(client, whoami, settings, user):
    token, _ = create_access_token(
        settings,
        user_id=user.id,
        organization_id=user.organization_id,
        now=datetime.now(UTC) - timedelta(hours=3),
    )
    response = client.get("/api/v1/_whoami", headers=bearer(token))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_token_signed_with_another_key_is_rejected(client, whoami, settings, user):
    other = Settings(
        _env_file=None,
        environment="test",
        database_url=settings.database_url,
        secret_key="a-different-secret-that-is-also-over-32-characters",
    )
    token, _ = create_access_token(other, user_id=user.id, organization_id=user.organization_id)
    assert client.get("/api/v1/_whoami", headers=bearer(token)).status_code == 401


def test_unsigned_alg_none_token_is_rejected(client, whoami, user):
    claims = _claims(user)
    token = jwt.encode(claims, key=None, algorithm="none")
    assert client.get("/api/v1/_whoami", headers=bearer(token)).status_code == 401


def _signed(claims: dict) -> str:
    return jwt.encode(claims, TEST_SECRET, algorithm="HS256")


def test_tokens_with_bad_claims_are_rejected(client, whoami, user):
    valid = _claims(user)

    assert client.get("/api/v1/_whoami", headers=bearer(_signed(valid))).status_code == 200

    for broken in (
        {**valid, "sub": "not-a-number"},
        {**valid, "org": user.organization_id + 1},
        {key: value for key, value in valid.items() if key != "exp"},
    ):
        response = client.get("/api/v1/_whoami", headers=bearer(_signed(broken)))
        assert response.status_code == 401, broken


def test_token_stops_working_when_the_user_is_disabled(client, whoami, db_session, user):
    token = login(client).json()["access_token"]
    assert client.get("/api/v1/_whoami", headers=bearer(token)).status_code == 200

    user.is_active = False
    db_session.commit()
    assert client.get("/api/v1/_whoami", headers=bearer(token)).status_code == 401


# --- account creation ------------------------------------------------------------------------


def test_passwords_are_stored_as_argon2_hashes(user):
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash


def test_users_with_the_same_organization_slug_share_it(db_session, user):
    second = create_user(
        db_session,
        email="second@example.com",
        password=PASSWORD,
        organization_name="Different display name",
        organization_slug="acme-compliance",
    )
    assert second.organization_id == user.organization_id
    assert len(db_session.scalars(select(Organization)).all()) == 1


@pytest.mark.parametrize(
    ("email", "password", "message"),
    [
        ("analyst@example.com", PASSWORD, "already registered"),
        ("ANALYST@EXAMPLE.COM", PASSWORD, "already registered"),
        ("not-an-email", PASSWORD, "valid email"),
        ("new@example.com", "short", "at least 12"),
    ],
)
def test_account_creation_rejects_bad_input(db_session, user, email, password, message):
    with pytest.raises(UserError, match=message):
        create_user(db_session, email=email, password=password, organization_name="Acme Compliance")
