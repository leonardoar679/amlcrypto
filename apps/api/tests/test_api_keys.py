"""API keys: what one is, who may make one, and when it stops working."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.access import apikeys
from app.access.permissions import Role
from app.database.models import ApiKey, AuditLog, Organization, User
from tests.access_helpers import make_user, person

# --- the key itself ---------------------------------------------------------------------------


def test_a_key_is_scheme_prefix_and_secret_and_nothing_else_parses():
    secret = "A" * 43
    assert apikeys.parse(f"aml_0123abcd_{secret}") == ("0123abcd", secret)
    assert apikeys.parse(f"  aml_0123abcd_{secret}\n") == ("0123abcd", secret)  # a pasted key
    bad = [
        "",
        "aml_0123abcd_" + "A" * 42,  # a character short
        "aml_0123abcd_" + "A" * 44,  # a character long
        "aml_0123abc_" + secret,  # prefix too short
        "aml_0123ABCD_" + secret,  # the prefix is lower-case hex
        "aml_0123abcg_" + secret,
        "key_0123abcd_" + secret,
        "aml_0123abcd_" + "A" * 42 + "!",
        "Bearer aml_0123abcd_" + secret,
    ]
    for text in bad:
        assert apikeys.parse(text) is None, text
    assert apikeys.parse(None) is None


def test_a_made_key_is_random_and_only_a_hash_of_its_secret_is_stored(db_session):
    creator = make_user(db_session, "boss@example.com", Role.ADMIN)

    first = apikeys.create_key(db_session, creator=creator, name="Ingest", role=Role.ANALYST)
    second = apikeys.create_key(db_session, creator=creator, name="Ingest", role=Role.ANALYST)
    db_session.commit()

    assert first.secret != second.secret and first.row.key_prefix != second.row.key_prefix
    prefix, secret = apikeys.parse(first.secret)
    assert prefix == first.row.key_prefix
    assert len(first.row.key_hash) == 64 and secret not in first.row.key_hash
    assert first.secret not in first.row.key_hash and first.row.key_hash != secret
    assert first.row.role == "analyst" and first.row.organization_id == creator.organization_id


def test_naming_and_limits_of_a_key(db_session):
    creator = make_user(db_session, "boss@example.com", Role.ADMIN)
    for name in ("", "   ", "x" * 101):
        with pytest.raises(apikeys.ApiKeyError):
            apikeys.create_key(db_session, creator=creator, name=name, role=Role.VIEWER)
    for days in (0, -1, 3651):
        with pytest.raises(apikeys.ApiKeyError):
            apikeys.create_key(
                db_session, creator=creator, name="k", role=Role.VIEWER, expires_in_days=days
            )
    with pytest.raises(apikeys.ApiKeyError):
        apikeys.create_key(db_session, creator=creator, name="k", role="root")
    ok = apikeys.create_key(
        db_session, creator=creator, name="  trimmed  ", role=Role.VIEWER, expires_in_days=3650
    )
    assert ok.row.name == "trimmed"
    assert ok.row.expires_at is not None


def test_a_key_cannot_have_a_role_above_its_makers(db_session):
    analyst = make_user(db_session, "analyst@example.com", Role.ANALYST)
    with pytest.raises(apikeys.ApiKeyError):
        apikeys.create_key(db_session, creator=analyst, name="k", role=Role.ADMIN)
    assert apikeys.create_key(db_session, creator=analyst, name="k", role=Role.VIEWER)


def test_an_organization_has_a_limited_number_of_live_keys(db_session, monkeypatch):
    monkeypatch.setattr(apikeys, "MAX_KEYS_PER_ORGANIZATION", 3)
    creator = make_user(db_session, "boss@example.com", Role.ADMIN)
    made = [
        apikeys.create_key(db_session, creator=creator, name=f"k{i}", role=Role.VIEWER)
        for i in range(3)
    ]
    with pytest.raises(apikeys.ApiKeyError):
        apikeys.create_key(db_session, creator=creator, name="one more", role=Role.VIEWER)
    made[0].row.revoked_at = datetime.now(UTC)  # a revoked key does not count
    db_session.flush()
    assert apikeys.create_key(db_session, creator=creator, name="one more", role=Role.VIEWER)


def _key(db_session, **kwargs):
    creator = make_user(db_session, "boss@example.com", Role.ADMIN)
    made = apikeys.create_key(db_session, creator=creator, name="k", role=Role.ANALYST, **kwargs)
    db_session.commit()
    return creator, made


def test_a_valid_key_authenticates_and_anything_else_gets_the_same_none(db_session):
    creator, made = _key(db_session)

    found = apikeys.authenticate(db_session, made.secret)
    assert found is not None and found.key.id == made.row.id and found.creator.id == creator.id
    assert found.stale  # never used

    prefix, secret = apikeys.parse(made.secret)
    for wrong in (
        f"aml_{prefix}_" + ("B" * 43),  # the right prefix, the wrong secret
        "aml_ffffffff_" + secret,  # a prefix nobody has
        "garbage",
    ):
        assert apikeys.authenticate(db_session, wrong) is None


def test_a_key_stops_working_when_revoked_expired_or_its_maker_or_organization_is_switched_off(
    db_session,
):
    creator, made = _key(db_session, expires_in_days=30)
    assert apikeys.authenticate(db_session, made.secret) is not None

    # expired
    assert (
        apikeys.authenticate(db_session, made.secret, now=datetime.now(UTC) + timedelta(days=31))
        is None
    )
    assert (
        apikeys.authenticate(db_session, made.secret, now=datetime.now(UTC) + timedelta(days=29))
        is not None
    )

    # the maker is switched off
    creator.is_active = False
    db_session.commit()
    assert apikeys.authenticate(db_session, made.secret) is None
    creator.is_active = True

    # the organization is switched off
    organization = db_session.get(Organization, creator.organization_id)
    organization.is_active = False
    db_session.commit()
    assert apikeys.authenticate(db_session, made.secret) is None
    organization.is_active = True

    # revoked
    made.row.revoked_at = datetime.now(UTC)
    db_session.commit()
    assert apikeys.authenticate(db_session, made.secret) is None


def test_a_key_expires_at_the_very_instant_it_says(db_session):
    _, made = _key(db_session, expires_in_days=30)
    instant = made.row.expires_at
    assert (
        apikeys.authenticate(db_session, made.secret, now=instant - timedelta(microseconds=1))
        is not None
    )
    assert apikeys.authenticate(db_session, made.secret, now=instant) is None


def test_a_key_whose_maker_belongs_to_another_organization_is_refused(db_session):
    _, made = _key(db_session)
    stranger = make_user(db_session, "stranger@example.com", Role.ADMIN, "Other Org")
    made.row.created_by = stranger.id  # cannot happen through the API; the data must not be trusted
    db_session.commit()
    assert apikeys.authenticate(db_session, made.secret) is None


def test_last_use_is_only_refreshed_when_it_is_old(db_session):
    _, made = _key(db_session)
    now = datetime.now(UTC)
    made.row.last_used_at = now - timedelta(minutes=4)
    db_session.commit()
    assert not apikeys.authenticate(db_session, made.secret, now=now).stale
    assert apikeys.authenticate(db_session, made.secret, now=now + timedelta(minutes=2)).stale
    # five minutes is old enough, to the microsecond (it was last used four minutes before `now`)
    just_short = now + timedelta(minutes=1) - timedelta(microseconds=1)
    assert not apikeys.authenticate(db_session, made.secret, now=just_short).stale
    assert apikeys.authenticate(db_session, made.secret, now=now + timedelta(minutes=1)).stale


# --- over HTTP --------------------------------------------------------------------------------


def make_key(client, headers, *, name="Ingest", role="analyst", **extra):
    response = client.post(
        "/api/v1/api-keys", json={"name": name, "role": role, **extra}, headers=headers
    )
    return response


@pytest.fixture
def admin(client, db_session):
    return person(client, db_session, Role.ADMIN)[1]


def key_header(secret: str) -> dict[str, str]:
    return {"X-API-Key": secret}


def test_an_admin_makes_a_key_and_the_secret_is_shown_once(client, admin, db_session):
    response = make_key(client, admin, expires_in_days=30)

    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    secret = body["secret"]
    assert apikeys.parse(secret) is not None
    assert body["key"]["prefix"] == secret.split("_")[1] and body["key"]["role"] == "analyst"
    assert body["key"]["active"] is True and body["key"]["expires_at"] is not None

    listing = client.get("/api/v1/api-keys", headers=admin)
    assert listing.status_code == 200
    assert (
        secret not in listing.text and "key_hash" not in listing.text and "hash" not in listing.text
    )
    assert [k["id"] for k in listing.json()["keys"]] == [body["key"]["id"]]
    stored = db_session.scalar(select(ApiKey))
    assert stored.key_hash not in listing.text


def test_a_program_works_with_its_key_to_the_extent_of_its_role(client, admin):
    analyst_key = make_key(client, admin, role="analyst").json()["secret"]
    viewer_key = make_key(client, admin, role="viewer").json()["secret"]

    # analyst: may read providers and may run (an empty body is refused for being empty)
    assert client.get("/api/v1/providers", headers=key_header(analyst_key)).status_code == 200
    assert (
        client.post("/api/v1/screen/address", json={}, headers=key_header(analyst_key)).status_code
        == 422
    )
    # viewer: reads, nothing more
    assert client.get("/api/v1/screenings", headers=key_header(viewer_key)).status_code == 200
    assert client.get("/api/v1/providers", headers=key_header(viewer_key)).status_code == 403
    assert (
        client.post("/api/v1/screen/address", json={}, headers=key_header(viewer_key)).status_code
        == 403
    )


def test_a_key_never_manages_people_or_keys_even_with_an_admins_role(client, admin):
    secret = make_key(client, admin, role="admin").json()["secret"]
    headers = key_header(secret)

    # it can do an admin's other work ...
    assert client.get("/api/v1/audit", headers=headers).status_code == 200
    assert client.post("/api/v1/providers/nope/probe", headers=headers).status_code == 404
    # ... but not these, and cannot ask who it is
    assert client.get("/api/v1/users", headers=headers).status_code == 403
    assert client.get("/api/v1/api-keys", headers=headers).status_code == 403
    assert (
        client.post(
            "/api/v1/api-keys", json={"name": "more", "role": "viewer"}, headers=headers
        ).status_code
        == 403
    )
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 403
    assert (
        client.post(
            "/api/v1/auth/password",
            json={"current_password": "x", "new_password": "y" * 20},
            headers=headers,
        ).status_code
        == 403
    )


def test_a_wrong_or_malformed_key_is_one_401(client, admin):
    secret = make_key(client, admin).json()["secret"]
    prefix = secret.split("_")[1]
    for bad in (f"aml_{prefix}_" + "B" * 43, "aml_ffffffff_" + "C" * 43, "nonsense", secret[:-1]):
        response = client.get("/api/v1/screenings", headers=key_header(bad))
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_api_key"
    # no credentials at all is a different, honest answer
    assert client.get("/api/v1/screenings").json()["error"]["code"] == "not_authenticated"


def test_revoking_a_key_ends_it_at_once_and_is_not_an_error_twice(client, admin):
    made = make_key(client, admin).json()
    headers = key_header(made["secret"])
    assert client.get("/api/v1/screenings", headers=headers).status_code == 200

    first = client.delete(f"/api/v1/api-keys/{made['key']['id']}", headers=admin)
    assert first.status_code == 200 and first.json()["key"]["active"] is False
    assert first.json()["key"]["revoked_at"] is not None
    assert client.get("/api/v1/screenings", headers=headers).status_code == 401
    again = client.delete(f"/api/v1/api-keys/{made['key']['id']}", headers=admin)
    assert (
        again.status_code == 200
        and again.json()["key"]["revoked_at"] == first.json()["key"]["revoked_at"]
    )


def test_revoking_twice_is_audited_once(client, admin, db_session):
    made = make_key(client, admin).json()
    client.delete(f"/api/v1/api-keys/{made['key']['id']}", headers=admin)
    client.delete(f"/api/v1/api-keys/{made['key']['id']}", headers=admin)
    db_session.rollback()
    assert (
        len(db_session.scalars(select(AuditLog).where(AuditLog.action == "apikey.revoke")).all())
        == 1
    )


def test_a_key_expires(client, admin, db_session):
    made = make_key(client, admin, expires_in_days=1).json()
    headers = key_header(made["secret"])
    assert client.get("/api/v1/screenings", headers=headers).status_code == 200
    db_session.execute(update(ApiKey).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    db_session.commit()
    assert client.get("/api/v1/screenings", headers=headers).status_code == 401
    listing = client.get("/api/v1/api-keys", headers=admin).json()["keys"]
    assert listing[0]["active"] is False


def test_switching_off_the_maker_switches_off_their_keys(client, admin, db_session):
    made = make_key(client, admin).json()
    headers = key_header(made["secret"])
    assert client.get("/api/v1/screenings", headers=headers).status_code == 200

    db_session.execute(update(User).values(is_active=False))
    db_session.commit()
    assert client.get("/api/v1/screenings", headers=headers).status_code == 401


def test_using_a_key_records_when_but_not_on_every_request(client, admin, db_session):
    made = make_key(client, admin).json()
    headers = key_header(made["secret"])
    assert client.get("/api/v1/api-keys", headers=admin).json()["keys"][0]["last_used_at"] is None

    assert client.get("/api/v1/screenings", headers=headers).status_code == 200
    first = client.get("/api/v1/api-keys", headers=admin).json()["keys"][0]["last_used_at"]
    assert first is not None
    assert client.get("/api/v1/screenings", headers=headers).status_code == 200
    assert client.get("/api/v1/api-keys", headers=admin).json()["keys"][0]["last_used_at"] == first


def test_the_makers_role_caps_the_keys_role_and_only_admins_make_keys(client, db_session, admin):
    _, analyst = person(client, db_session, Role.ANALYST)
    assert (
        make_key(client, analyst, role="viewer").status_code == 403
    )  # analysts do not manage keys at all
    refused = make_key(client, admin, role="owner")
    assert refused.status_code == 422  # not a role
    assert make_key(client, admin, name="").status_code == 422
    assert make_key(client, admin, expires_in_days=0).status_code == 422
    assert make_key(client, admin, extra="field").status_code == 422  # nothing unknown is accepted


def test_keys_belong_to_one_organization(client, db_session, admin):
    made = make_key(client, admin).json()
    _, other_admin = person(client, db_session, Role.ADMIN, organization="Other Org")

    assert client.get("/api/v1/api-keys", headers=other_admin).json()["keys"] == []
    gone = client.delete(f"/api/v1/api-keys/{made['key']['id']}", headers=other_admin)
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "key_not_found"
    # and it is still alive
    assert client.get("/api/v1/screenings", headers=key_header(made["secret"])).status_code == 200


def test_making_and_revoking_a_key_are_audited_without_the_secret(client, admin, db_session):
    made = make_key(client, admin, name="Nightly").json()
    client.delete(f"/api/v1/api-keys/{made['key']['id']}", headers=admin)

    rows = db_session.scalars(select(AuditLog).where(AuditLog.action.startswith("apikey."))).all()
    assert [r.action for r in rows] == ["apikey.create", "apikey.revoke"]
    assert all(r.outcome == "success" and r.target_type == "api_key" for r in rows)
    assert (
        rows[0].details["name"] == "Nightly" and rows[0].details["prefix"] == made["key"]["prefix"]
    )
    secret = made["secret"]
    part = apikeys.parse(secret)[1]  # the secret half; it can itself contain "_"
    for row in db_session.scalars(select(AuditLog)).all():
        assert secret not in str(row.details) and part not in str(row.details)
