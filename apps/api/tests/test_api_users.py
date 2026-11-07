"""The people of an organization, managed by its admins."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.access.permissions import Role
from app.database.models import AuditLog, User
from tests.access_helpers import PASSWORD, make_user, person, sign_in


@pytest.fixture
def admin(client, db_session):
    return person(client, db_session, Role.ADMIN)


def create(client, headers, email="new@example.com", password=PASSWORD, role="analyst", **extra):
    return client.post(
        "/api/v1/users",
        json={"email": email, "password": password, "role": role, **extra},
        headers=headers,
    )


def users(client, headers):
    response = client.get("/api/v1/users", headers=headers)
    assert response.status_code == 200
    return response.json()["users"]


def by_email(client, headers, email):
    return next(u for u in users(client, headers) if u["email"] == email)


# --- listing and creating ----------------------------------------------------------------------


def test_the_list_has_only_this_organizations_people_in_order(client, db_session, admin):
    _, headers = admin
    make_user(db_session, "zed@example.com", Role.VIEWER)
    make_user(db_session, "bea@example.com", Role.ANALYST)
    make_user(db_session, "stranger@example.com", Role.ADMIN, "Other Org")
    db_session.rollback()

    listed = users(client, headers)

    assert [u["email"] for u in listed] == [
        "admin-acme@example.com",
        "bea@example.com",
        "zed@example.com",
    ]
    assert listed[0]["role"] == "admin" and listed[0]["is_active"] is True
    assert set(listed[0]) == {"id", "email", "role", "is_active", "last_login_at", "created_at"}
    assert "password" not in str(listed)


def test_an_admin_adds_a_person_who_can_then_sign_in_with_the_role_given(client, admin):
    _, headers = admin
    made = create(client, headers, email="  New@Example.com ", role="viewer")

    assert made.status_code == 201
    user = made.json()["user"]
    assert (
        user["email"] == "new@example.com"
        and user["role"] == "viewer"
        and user["is_active"] is True
    )
    assert PASSWORD not in made.text

    newcomer = sign_in(client, "new@example.com")
    me = client.get("/api/v1/auth/me", headers=newcomer).json()
    assert me["role"] == "viewer" and me["user"]["role"] == "viewer"
    assert me["permissions"] == [
        "address:read",
        "alerts:read",
        "cases:read",
        "monitoring:read",
        "screening:read",
    ]
    assert client.get("/api/v1/users", headers=newcomer).status_code == 403


def test_a_new_person_joins_the_admins_organization_not_a_new_one(client, db_session, admin):
    user, headers = admin
    create(client, headers)
    db_session.rollback()
    organizations = {u.organization_id for u in db_session.scalars(select(User)).all()}
    assert organizations == {user.organization_id}


def test_bad_requests_to_add_someone_are_refused_without_creating_anyone(client, admin):
    _, headers = admin
    assert create(client, headers, role="owner").status_code == 422
    assert create(client, headers, role="ADMIN").status_code == 422
    assert create(client, headers, email="not-an-email").status_code == 422
    assert create(client, headers, password="short").status_code == 422
    assert create(client, headers, extra="field").status_code == 422
    assert (
        client.post(
            "/api/v1/users", json={"email": "a@b.co", "password": PASSWORD}, headers=headers
        ).status_code
        == 422
    )
    assert [u["email"] for u in users(client, headers)] == ["admin-acme@example.com"]


def test_an_email_in_use_anywhere_cannot_be_added(client, db_session, admin):
    _, headers = admin
    make_user(db_session, "taken@example.com", Role.VIEWER, "Other Org")
    db_session.rollback()

    for email in ("taken@example.com", "TAKEN@example.com", "admin-acme@example.com"):
        refused = create(client, headers, email=email)
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "conflict", email


def test_adding_a_person_is_audited_without_their_password(client, db_session, admin):
    user, headers = admin
    made = create(client, headers, email="aud@example.com", role="analyst").json()["user"]
    db_session.rollback()

    (row,) = db_session.scalars(select(AuditLog).where(AuditLog.action == "user.create")).all()
    assert (row.outcome, row.actor_id, row.target_type, row.target_id) == (
        "success",
        user.id,
        "user",
        str(made["id"]),
    )
    assert row.details == {"email": "aud@example.com", "role": "analyst"}
    assert PASSWORD not in str(row.details)


# --- changing someone ---------------------------------------------------------------------------


def test_an_admin_changes_a_role_and_the_change_applies_at_once(client, db_session, admin):
    _, headers = admin
    create(client, headers, email="w@example.com", role="viewer")
    theirs = sign_in(client, "w@example.com")
    target = by_email(client, headers, "w@example.com")["id"]
    assert client.post("/api/v1/screen/address", json={}, headers=theirs).status_code == 403

    changed = client.patch(f"/api/v1/users/{target}", json={"role": "analyst"}, headers=headers)

    assert changed.status_code == 200 and changed.json()["user"]["role"] == "analyst"
    assert (
        client.post("/api/v1/screen/address", json={}, headers=theirs).status_code == 422
    )  # allowed now
    db_session.rollback()
    (row,) = db_session.scalars(select(AuditLog).where(AuditLog.action == "user.update")).all()
    assert row.details == {
        "email": "w@example.com",
        "changes": {"role": {"from": "viewer", "to": "analyst"}},
    }


def test_switching_a_person_off_ends_their_session_and_back_on_restores_it(client, admin):
    _, headers = admin
    create(client, headers, email="w@example.com", role="analyst")
    theirs = sign_in(client, "w@example.com")
    target = by_email(client, headers, "w@example.com")["id"]
    assert client.get("/api/v1/screenings", headers=theirs).status_code == 200

    off = client.patch(f"/api/v1/users/{target}", json={"is_active": False}, headers=headers)
    assert off.status_code == 200 and off.json()["user"]["is_active"] is False
    assert (
        client.get("/api/v1/screenings", headers=theirs).status_code == 401
    )  # the token no longer works
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "w@example.com", "password": PASSWORD}
        ).status_code
        == 401
    )

    on = client.patch(f"/api/v1/users/{target}", json={"is_active": True}, headers=headers)
    assert on.status_code == 200
    assert client.get("/api/v1/screenings", headers=theirs).status_code == 200


def test_a_change_that_changes_nothing_is_fine_and_leaves_no_entry_but_an_empty_one_is_refused(
    client, db_session, admin
):
    _, headers = admin
    create(client, headers, email="w@example.com", role="viewer")
    target = by_email(client, headers, "w@example.com")["id"]

    assert (
        client.patch(
            f"/api/v1/users/{target}", json={"role": "viewer"}, headers=headers
        ).status_code
        == 200
    )
    assert client.patch(f"/api/v1/users/{target}", json={}, headers=headers).status_code == 422
    assert (
        client.patch(f"/api/v1/users/{target}", json={"role": "boss"}, headers=headers).status_code
        == 422
    )
    assert (
        client.patch(f"/api/v1/users/{target}", json={"surprise": 1}, headers=headers).status_code
        == 422
    )
    db_session.rollback()
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "user.update")).all() == []


def test_an_organization_always_keeps_an_active_admin(client, admin):
    me, headers = admin
    # the only admin: cannot demote or switch themselves off
    demote = client.patch(f"/api/v1/users/{me.id}", json={"role": "viewer"}, headers=headers)
    assert demote.status_code == 409 and demote.json()["error"]["code"] == "conflict"
    off = client.patch(f"/api/v1/users/{me.id}", json={"is_active": False}, headers=headers)
    assert off.status_code == 409
    both = client.patch(
        f"/api/v1/users/{me.id}", json={"role": "analyst", "is_active": False}, headers=headers
    )
    assert both.status_code == 409
    assert (
        by_email(client, headers, "admin-acme@example.com")["role"] == "admin"
    )  # nothing half-applied

    # with a second admin either may step down
    create(client, headers, email="second@example.com", role="admin")
    assert (
        client.patch(
            f"/api/v1/users/{me.id}", json={"role": "analyst"}, headers=headers
        ).status_code
        == 200
    )


def test_an_admin_who_is_already_off_does_not_count_as_the_one_that_remains(client, admin):
    me, headers = admin
    create(client, headers, email="second@example.com", role="admin")
    second = by_email(client, headers, "second@example.com")["id"]
    assert (
        client.patch(
            f"/api/v1/users/{second}", json={"is_active": False}, headers=headers
        ).status_code
        == 200
    )

    refused = client.patch(f"/api/v1/users/{me.id}", json={"is_active": False}, headers=headers)
    assert refused.status_code == 409
    # and switching the other one on makes room again
    assert (
        client.patch(
            f"/api/v1/users/{second}", json={"is_active": True}, headers=headers
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/v1/users/{me.id}", json={"is_active": False}, headers=headers
        ).status_code
        == 200
    )


def test_another_organizations_people_are_not_found(client, db_session, admin):
    _, headers = admin
    stranger = make_user(db_session, "stranger@example.com", Role.ADMIN, "Other Org")
    db_session.rollback()

    for response in (
        client.patch(f"/api/v1/users/{stranger.id}", json={"role": "viewer"}, headers=headers),
        client.post(
            f"/api/v1/users/{stranger.id}/password",
            json={"new_password": "x" * 20},
            headers=headers,
        ),
        client.patch("/api/v1/users/99999", json={"role": "viewer"}, headers=headers),
    ):
        assert response.status_code == 404 and response.json()["error"]["code"] == "user_not_found"
    assert sign_in(client, "stranger@example.com")  # untouched


# --- resetting a password ----------------------------------------------------------------------


def test_an_admin_sets_a_new_password_for_someone_who_lost_theirs(client, db_session, admin):
    _, headers = admin
    create(client, headers, email="w@example.com", role="viewer")
    target = by_email(client, headers, "w@example.com")["id"]

    reset = client.post(
        f"/api/v1/users/{target}/password",
        json={"new_password": "reset to this one"},
        headers=headers,
    )

    assert reset.status_code == 204 and reset.content == b""
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "w@example.com", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "w@example.com", "password": "reset to this one"}
        ).status_code
        == 200
    )
    db_session.rollback()
    (row,) = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "user.password_reset")
    ).all()
    assert row.details == {"email": "w@example.com"} and row.target_id == str(target)


def test_a_reset_needs_a_real_password(client, admin):
    _, headers = admin
    create(client, headers, email="w@example.com", role="viewer")
    target = by_email(client, headers, "w@example.com")["id"]
    weak = client.post(
        f"/api/v1/users/{target}/password", json={"new_password": "short"}, headers=headers
    )
    assert weak.status_code == 422
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "w@example.com", "password": PASSWORD}
        ).status_code
        == 200
    )
