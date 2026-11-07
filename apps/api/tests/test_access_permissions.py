"""Roles and permissions as data: what each role holds, and what a key may never hold."""

from __future__ import annotations

import pytest

from app.access.permissions import (
    KEY_NEVER,
    ROLE_PERMISSIONS,
    Permission,
    Role,
    UnknownRole,
    can_grant,
    parse_role,
    permissions_for,
)


def test_every_role_has_an_entry_and_every_permission_is_held_by_someone():
    assert set(ROLE_PERMISSIONS) == set(Role)
    held = set().union(*ROLE_PERMISSIONS.values())
    assert held == set(Permission)


def test_each_role_holds_everything_the_one_below_it_does_and_more():
    viewer, analyst, admin = (ROLE_PERMISSIONS[r] for r in (Role.VIEWER, Role.ANALYST, Role.ADMIN))
    assert viewer < analyst < admin


def test_a_viewer_only_reads():
    assert ROLE_PERMISSIONS[Role.VIEWER] == {
        Permission.SCREENING_READ,
        Permission.ADDRESS_READ,
        Permission.MONITORING_READ,
        Permission.ALERTS_READ,
        Permission.CASES_READ,
    }
    assert all(p.value.endswith(":read") for p in ROLE_PERMISSIONS[Role.VIEWER])


def test_only_an_admin_manages_people_keys_and_the_audit_log_or_spends_a_provider_request():
    for permission in (
        Permission.USERS_MANAGE,
        Permission.API_KEYS_MANAGE,
        Permission.AUDIT_READ,
        Permission.PROVIDERS_PROBE,
    ):
        assert [r for r in Role if permission in ROLE_PERMISSIONS[r]] == [Role.ADMIN]


def test_an_analyst_can_work_but_not_administer():
    analyst = ROLE_PERMISSIONS[Role.ANALYST]
    working = {
        Permission.SCREENING_RUN,
        Permission.GRAPH_UPDATE,
        Permission.PROVIDERS_READ,
        Permission.MONITORING_WRITE,
        Permission.ALERTS_MANAGE,
        Permission.CASES_WRITE,
    }
    assert working <= analyst
    assert not analyst & KEY_NEVER


def test_a_key_never_manages_people_or_keys_whatever_its_role():
    assert {Permission.USERS_MANAGE, Permission.API_KEYS_MANAGE} == KEY_NEVER
    admin_key = permissions_for(Role.ADMIN, api_key=True)
    assert not admin_key & KEY_NEVER
    assert admin_key == ROLE_PERMISSIONS[Role.ADMIN] - KEY_NEVER
    # a person with the same role keeps them
    assert permissions_for(Role.ADMIN) >= KEY_NEVER
    # and the lower roles are untouched
    assert permissions_for(Role.VIEWER, api_key=True) == ROLE_PERMISSIONS[Role.VIEWER]


def test_roles_can_be_read_from_text_and_nothing_else_is_a_role():
    assert parse_role("analyst") is Role.ANALYST
    assert permissions_for("viewer") == ROLE_PERMISSIONS[Role.VIEWER]
    for text in ("", "Admin", "root", "superuser", " admin"):
        with pytest.raises(UnknownRole):
            parse_role(text)


def test_nobody_can_give_more_than_they_have():
    for creator in Role:
        for wanted in Role:
            expected = ROLE_PERMISSIONS[wanted] <= ROLE_PERMISSIONS[creator]
            assert can_grant(creator, wanted) is expected, (creator, wanted)
    assert can_grant(Role.ADMIN, Role.ADMIN)
    assert not can_grant(Role.ANALYST, Role.ADMIN)
    assert not can_grant(Role.VIEWER, Role.ANALYST)
    assert can_grant(Role.ANALYST, Role.VIEWER)
