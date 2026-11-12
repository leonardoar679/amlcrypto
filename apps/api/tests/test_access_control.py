"""Access control, checked for every route and every role.

Two guards. The structural one reads the application's routes and fails for any route that does not
say what it needs (so a new endpoint cannot be forgotten). The behavioural one signs in as each role
and calls every route: a role that lacks the permission must get 403, and one that has it must get
past the check (whatever the route then says about the request, which here is deliberately empty).
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select

from app.access.permissions import EDITOR_ONLY, ROLE_PERMISSIONS, Role
from app.database.models import AuditLog
from tests.access_helpers import person

PUBLIC = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/version"),
    ("POST", "/api/v1/auth/login"),
    # called by the provider, not by a person: it proves itself with a signature on the body
    ("POST", "/api/v1/webhooks/alchemy"),
    # what has been published, for anybody: read only, nothing of any organization's
    ("GET", "/api/v1/public/incidents"),
    ("GET", "/api/v1/public/incidents/{slug}"),
    ("GET", "/api/v1/public/addresses/{chain}/{address}/incidents"),
    ("GET", "/api/v1/public/wallets"),
    ("GET", "/api/v1/public/wallets/{slug}"),
    ("GET", "/api/v1/public/addresses/{chain}/{address}/wallets"),
}
# Open to anyone who is signed in, whatever their role: nothing here is more than reading what is
# public to the whole platform (the chains) or about the caller themselves.
SIGNED_IN_ONLY = {
    ("GET", "/api/v1/chains"),
    ("POST", "/api/v1/address/validate"),
    ("GET", "/api/v1/auth/me"),
    ("POST", "/api/v1/auth/password"),
}

PATH_VALUES = {
    "name": "nope",
    "job_id": "1",
    "screening": "scr_1",
    "chain": "ethereum",
    "address": "0x00",
    "user_id": "1",
    "key_id": "1",
    "monitor_id": "1",
    "alert_id": "1",
    "case_id": "1",
    "link_id": "1",
    "incident_id": "1",
    "profile_id": "1",
}


def walk(router, prefix=""):
    """Every APIRoute under a router with its full path. FastAPI keeps an included router whole
    and wraps it, so the wrappers are followed."""
    for route in router.routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif hasattr(route, "original_router"):
            yield from walk(route.original_router, prefix + (route.include_context.prefix or ""))


def api_routes(app):
    for path, route in walk(app.router):
        if path.startswith("/api/v1/"):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                yield method, path, route


def declared(route):
    """The permissions a route requires (empty: any signed-in caller), or None if it names none."""
    stack = list(route.dependant.dependencies)
    while stack:
        dependency = stack.pop()
        found = getattr(dependency.call, "required_permissions", None)
        if found is not None:
            return found
        stack.extend(dependency.dependencies)
    return None


def concrete(path: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda match: PATH_VALUES[match.group(1)], path)


# --- structure ----------------------------------------------------------------------------------


def test_every_route_says_what_it_needs_or_is_listed_as_open(app):
    unguarded, unreviewed = [], []
    routes = list(api_routes(app))
    # a walker that finds nothing would pass every check below without checking anything
    assert len(routes) >= 30, f"only {len(routes)} routes found"
    for method, path, route in routes:
        permissions = declared(route)
        if (method, path) in PUBLIC:
            if permissions is not None:
                unreviewed.append((method, path, "listed as public but checks access"))
        elif permissions is None:
            unguarded.append((method, path))
        elif not permissions and (method, path) not in SIGNED_IN_ONLY:
            unreviewed.append((method, path, "open to every role and not listed as such"))
    assert not unguarded, f"routes with no access rule: {unguarded}"
    assert not unreviewed, unreviewed


def test_the_lists_of_open_routes_name_routes_that_exist(app):
    existing = {(method, path) for method, path, _ in api_routes(app)}
    assert existing >= PUBLIC
    assert existing >= SIGNED_IN_ONLY


def test_every_permission_is_required_by_some_route(app):
    required = set()
    for _, _, route in api_routes(app):
        required |= set(declared(route) or ())
    held = set().union(*ROLE_PERMISSIONS.values())
    # what no role holds (a platform editor is given it) is still required by the editor routes
    wanted = held | EDITOR_ONLY
    assert required == wanted, f"never required by a route: {sorted(wanted - required)}"


# --- behaviour ----------------------------------------------------------------------------------


@pytest.fixture
def team(client, db_session):
    people = {}
    for role in Role:
        _, headers = person(client, db_session, role)
        people[role] = headers
    return people


def call(client, method, path, headers):
    kwargs = {"json": {}} if method in ("POST", "PATCH", "PUT") else {}
    return client.request(method, concrete(path), headers=headers, **kwargs)


def test_every_role_is_refused_exactly_what_it_lacks(app, client, team):
    wrong = []
    for method, path, route in api_routes(app):
        if (method, path) in PUBLIC:
            continue
        needed = declared(route)
        for role, headers in team.items():
            status = call(client, method, path, headers).status_code
            lacks = bool(needed - ROLE_PERMISSIONS[role])
            if lacks and status != 403:
                wrong.append((role.value, method, path, f"allowed ({status}) without {needed}"))
            if not lacks and status in (401, 403):
                wrong.append(
                    (role.value, method, path, f"refused ({status}) though it holds {needed}")
                )
    assert not wrong, wrong


def test_nobody_gets_in_without_signing_in(app, client):
    leaked = []
    for method, path, _ in api_routes(app):
        if (method, path) in PUBLIC:
            continue
        status = call(client, method, path, {}).status_code
        if status != 401:
            leaked.append((method, path, status))
    assert not leaked, leaked


def test_a_refusal_is_a_403_with_the_usual_envelope_and_is_written_to_the_audit_log(
    client, db_session, team
):
    response = client.get("/api/v1/users", headers=team[Role.VIEWER])

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    rows = db_session.scalars(select(AuditLog).where(AuditLog.action == "access.denied")).all()
    assert len(rows) == 1
    (row,) = rows
    assert row.outcome == "denied" and row.actor_type == "user"
    assert row.actor_label == "viewer-acme@example.com"
    assert row.details["needs"] == ["users:manage"] and row.details["role"] == "viewer"
    assert row.details["method"] == "GET" and row.details["path"] == "/api/v1/users"
    assert row.organization_id is not None and row.request_id


def test_a_role_change_takes_effect_on_the_next_request_with_the_same_token(
    client, db_session, team
):
    admin = team[Role.ADMIN]
    viewer = team[Role.VIEWER]
    assert client.post("/api/v1/screen/address", json={}, headers=viewer).status_code == 403

    users = client.get("/api/v1/users", headers=admin).json()["users"]
    viewer_id = next(u["id"] for u in users if u["role"] == "viewer")
    assert (
        client.patch(
            f"/api/v1/users/{viewer_id}", json={"role": "analyst"}, headers=admin
        ).status_code
        == 200
    )

    # the viewer's old token now carries an analyst's permissions (and fails validation, not access)
    assert client.post("/api/v1/screen/address", json={}, headers=viewer).status_code == 422
    assert client.get("/api/v1/users", headers=viewer).status_code == 403


def test_a_token_stops_working_when_the_organization_is_switched_off(client, db_session, team):
    from sqlalchemy import update

    from app.database.models import Organization

    assert client.get("/api/v1/screenings", headers=team[Role.VIEWER]).status_code == 200
    db_session.execute(update(Organization).values(is_active=False))
    db_session.commit()
    for headers in team.values():
        assert client.get("/api/v1/screenings", headers=headers).status_code == 401
