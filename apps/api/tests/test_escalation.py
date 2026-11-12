"""Attempts to become more than one is: through a key, through the shape of a request, through
someone else's token.

The role table and the access checks are tested route by route in test_access_control.py and the
rules of keys in test_api_keys.py. These are the attempts around them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select

from app.access.permissions import Role
from app.core.security import create_access_token
from app.database.models import AuditLog
from tests.access_helpers import PASSWORD
from tests.catalog import V1
from tests.test_access_control import api_routes
from tests.world import World


@pytest.fixture
def world(client, db_session, settings) -> World:
    return World(client, db_session, settings)


def second_admin(world: World, team) -> tuple[int, dict[str, str]]:
    """Another administrator of the team (so that the first may be demoted)."""
    body = {"email": "second-admin@example.com", "password": PASSWORD, "role": "admin"}
    made = world.client.post(f"{V1}/users", json=body, headers=team.admin)
    assert made.status_code == 201, made.text
    signed_in = world.client.post(
        f"{V1}/auth/login", json={"email": body["email"], "password": PASSWORD}
    )
    token = signed_in.json()["access_token"]
    return made.json()["user"]["id"], {"Authorization": f"Bearer {token}"}


def key_of(world: World, admin: dict[str, str], role: str) -> dict[str, str]:
    made = world.client.post(f"{V1}/api-keys", json={"name": "ci", "role": role}, headers=admin)
    assert made.status_code == 201, made.text
    return {"X-API-Key": made.json()["secret"]}


# --- keys ---------------------------------------------------------------------------------------


def test_a_creator_who_is_demoted_takes_their_keys_down_with_them(world):
    """A key may never be more than the person who made it. That holds when the key is made and
    for as long as it lives: demoting its maker must not leave a program with more than they
    have."""
    team = world.acme
    admin_id, admin = second_admin(world, team)
    key = key_of(world, admin, "admin")
    alert = world.alert(team)
    working = world.client.patch(
        f"{V1}/alerts/{alert}", json={"status": "acknowledged"}, headers=key
    )
    assert working.status_code == 200

    demoted = world.client.patch(
        f"{V1}/users/{admin_id}", json={"role": "viewer"}, headers=team.admin
    )
    assert demoted.status_code == 200

    # it can still read, as a viewer can ...
    assert world.client.get(f"{V1}/alerts/{alert}", headers=key).status_code == 200
    # ... and can no longer do what a viewer cannot
    close = {"status": "closed", "resolution": "false_positive"}
    assert world.client.patch(f"{V1}/alerts/{alert}", json=close, headers=key).status_code == 403
    watch = {"chain": "ethereum", "address": world.address()}
    assert (
        world.client.post(f"{V1}/monitoring/addresses", json=watch, headers=key).status_code == 403
    )
    assert world.client.post(f"{V1}/cases", json={"title": "x"}, headers=key).status_code == 403

    # and what the audit log says it was when it was refused is what it now is: a viewer's key
    world.db.rollback()
    refusals = world.db.scalars(
        select(AuditLog).where(AuditLog.action == "access.denied", AuditLog.actor_type == "api_key")
    ).all()
    assert refusals and {row.details["role"] for row in refusals} == {"viewer"}
    world.db.rollback()


def test_promoting_the_creator_again_does_not_give_the_key_more_than_it_was_made_with(world):
    team = world.acme
    admin_id, admin = second_admin(world, team)
    key = key_of(world, admin, "analyst")
    world.client.patch(f"{V1}/users/{admin_id}", json={"role": "viewer"}, headers=team.admin)
    world.client.patch(f"{V1}/users/{admin_id}", json={"role": "admin"}, headers=team.admin)

    # a key made as an analyst stays an analyst: it is never read as the creator's role
    assert world.client.get(f"{V1}/audit", headers=key).status_code == 403
    assert world.client.get(f"{V1}/providers", headers=key).status_code == 200


def test_a_key_of_any_role_is_refused_what_only_people_may_do(world):
    key = key_of(world, world.acme.admin, "admin")
    me = world.acme.analyst_id
    refused = []
    for method, path in (
        ("GET", "/users"),
        ("POST", "/users"),
        ("PATCH", f"/users/{me}"),
        ("POST", f"/users/{me}/password"),
        ("GET", "/api-keys"),
        ("POST", "/api-keys"),
        ("DELETE", "/api-keys/1"),
        # an editor is a person: a key made by one, of any role, never writes incidents
        ("GET", "/editor/incidents"),
        ("POST", "/editor/incidents"),
        ("GET", "/editor/incidents/1"),
        ("PUT", "/editor/incidents/1/content"),
        ("POST", "/editor/incidents/1/transition"),
        ("POST", "/editor/incidents/1/updates"),
        ("DELETE", "/editor/incidents/1"),
        # ... and never writes wallet profiles
        ("GET", "/editor/wallets"),
        ("POST", "/editor/wallets"),
        ("GET", "/editor/wallets/1"),
        ("PUT", "/editor/wallets/1/content"),
        ("POST", "/editor/wallets/1/transition"),
        ("DELETE", "/editor/wallets/1"),
    ):
        status = world.client.request(method, V1 + path, json={}, headers=key).status_code
        if status != 403:
            refused.append((method, path, status))
    assert not refused, refused


# --- the shape of a request ---------------------------------------------------------------------

# Bodies that are only asked about or checked, never kept: extra fields in them do nothing.
IGNORES_EXTRAS = {
    ("POST", f"{V1}/address/validate"),
    ("POST", f"{V1}/auth/login"),
    ("POST", f"{V1}/auth/password"),
    ("POST", f"{V1}/intel/lookup"),
}


def test_every_request_that_changes_something_refuses_fields_it_does_not_offer(app):
    """Sending `organization_id`, `role` or `created_by` to a route that does not take them must not
    be quietly ignored (or worse, applied): a body model that allows extra fields is a way of
    setting whatever a model happens to have."""
    lax = []
    checked = 0
    for method, path, route in api_routes(app):
        if not isinstance(route, APIRoute) or route.body_field is None:
            continue
        if (method, path) in IGNORES_EXTRAS:
            continue
        checked += 1
        model = route.body_field.field_info.annotation
        if model.model_config.get("extra") != "forbid":
            lax.append((method, path, model.__name__))
    assert checked >= 10, f"only {checked} bodies found"
    assert not lax, lax


def test_a_field_that_would_move_a_record_to_another_organization_is_refused(world):
    team, other = world.acme, world.rival
    case, alert, user = world.case(team), world.alert(team), world.user(team)
    moving = {"organization_id": other.organization_id}
    attempts = [
        ("PATCH", f"/cases/{case}", moving),
        ("PATCH", f"/alerts/{alert}", moving),
        ("PATCH", f"/users/{user}", moving),
        ("POST", "/cases", {"title": "t", **moving}),
        (
            "POST",
            "/users",
            {"email": "x@example.com", "password": "a" * 12, "role": "viewer", **moving},
        ),
        ("POST", "/api-keys", {"name": "k", "role": "viewer", **moving}),
        (
            "POST",
            "/monitoring/addresses",
            {"chain": "ethereum", "address": world.address(), **moving},
        ),
    ]
    before = world.snapshot()
    wrong = []
    for method, path, body in attempts:
        status = world.client.request(method, V1 + path, json=body, headers=team.admin).status_code
        if status != 422:
            wrong.append((method, path, status))
    assert not wrong, wrong
    assert world.snapshot() == before


# --- people -------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.VIEWER, Role.ANALYST])
def test_nobody_below_an_admin_can_raise_themselves(world, role):
    headers = {Role.VIEWER: world.acme.viewer, Role.ANALYST: world.acme.analyst}[role]
    me = {Role.VIEWER: world.acme.viewer_id, Role.ANALYST: world.acme.analyst_id}[role]
    password = {"new_password": "x" * 12}
    newcomer = {"email": "a@example.com", "password": "x" * 12, "role": "admin"}
    before = world.snapshot()
    refused = [
        world.client.patch(f"{V1}/users/{me}", json={"role": "admin"}, headers=headers),
        world.client.patch(f"{V1}/users/{me}", json={"is_active": True}, headers=headers),
        world.client.post(f"{V1}/users/{me}/password", json=password, headers=headers),
        world.client.post(f"{V1}/api-keys", json={"name": "k", "role": "admin"}, headers=headers),
        world.client.post(f"{V1}/users", json=newcomer, headers=headers),
    ]
    assert [response.status_code for response in refused] == [403] * 5
    # the refusals are written to the audit log, and that is all that changed
    after = world.snapshot()
    assert {name for name in after if after[name] != before[name]} == {"audit_logs"}


def test_a_token_names_an_organization_and_it_must_be_the_users_own(world, settings):
    """Whoever holds the signing key can make any token; but a token whose organization is not the
    one its user belongs to is refused, so a token cannot be pointed at another tenant."""
    token, _ = create_access_token(
        settings, user_id=world.acme.admin_id, organization_id=world.rival.organization_id
    )
    response = world.client.get(f"{V1}/alerts", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_a_token_cannot_say_what_its_holder_may_do(world, settings):
    """Role and permissions are read from the database on every request; whatever a token claims
    about them is not looked at."""
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(world.acme.viewer_id),
            "org": world.acme.organization_id,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "role": "admin",
            "permissions": ["users:manage", "apikeys:manage"],
            "is_admin": True,
        },
        settings.signing_key,
        algorithm=settings.jwt_algorithm,
    )
    response = world.client.get(f"{V1}/users", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
