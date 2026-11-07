"""The rules for users that hold whoever asks: passwords, roles, an organization's last admin."""

from __future__ import annotations

import pytest

from app.access.permissions import Role
from app.services import users as accounts
from tests.access_helpers import PASSWORD, make_user


def test_a_password_needs_twelve_characters_and_not_more_than_a_thousand_and_twenty_four():
    accounts.check_password("x" * 12)
    accounts.check_password("x" * 1024)
    for bad in ("", "x" * 11, "x" * 1025):
        with pytest.raises(accounts.UserError):
            accounts.check_password(bad)


def test_only_the_three_roles_exist():
    for role in Role:
        assert accounts.check_role(role) is role
        assert accounts.check_role(role.value) is role
    for text in ("", "root", "Admin", "owner", " viewer"):
        with pytest.raises(accounts.UserError):
            accounts.check_role(text)


def test_the_email_is_trimmed_and_lowered_and_has_to_look_like_one():
    assert accounts.normalize_email("  Ada@Example.COM ") == "ada@example.com"
    for bad in (
        "",
        "ada",
        "ada@",
        "@example.com",
        "ada@example",
        "a b@example.com",
        "x" * 320 + "@a.bc",
    ):
        with pytest.raises(accounts.UserError):
            accounts.normalize_email(bad)


def test_a_clash_is_told_from_bad_input(db_session):
    make_user(db_session, "taken@example.com", Role.ADMIN)
    with pytest.raises(accounts.UserConflict):
        accounts.create_user(
            db_session, email="TAKEN@example.com", password=PASSWORD, organization_name="Acme"
        )
    with pytest.raises(accounts.UserError) as bad:
        accounts.create_user(
            db_session, email="not-an-email", password=PASSWORD, organization_name="Acme"
        )
    assert not isinstance(bad.value, accounts.UserConflict)


def test_an_admin_of_another_organization_does_not_make_this_ones_last_admin_dispensable(
    db_session,
):
    mine = make_user(db_session, "mine@example.com", Role.ADMIN, "Acme")
    make_user(db_session, "theirs@example.com", Role.ADMIN, "Other Org")
    with pytest.raises(accounts.UserConflict):
        accounts.set_role(db_session, mine, Role.VIEWER)
    with pytest.raises(accounts.UserConflict):
        accounts.set_active(db_session, mine, False)
    assert mine.role == "admin" and mine.is_active


def test_only_an_active_admin_makes_another_admin_dispensable(db_session):
    boss = make_user(db_session, "boss@example.com", Role.ADMIN, "Acme")
    make_user(db_session, "analyst@example.com", Role.ANALYST, "Acme")
    make_user(db_session, "viewer@example.com", Role.VIEWER, "Acme")
    with pytest.raises(accounts.UserConflict):  # an analyst and a viewer are not admins
        accounts.set_role(db_session, boss, Role.ANALYST)
    with pytest.raises(accounts.UserConflict):
        accounts.set_active(db_session, boss, False)

    second = make_user(db_session, "second@example.com", Role.ADMIN, "Acme")
    accounts.set_active(db_session, second, False)
    with pytest.raises(accounts.UserConflict):  # a switched-off admin is not one either
        accounts.set_role(db_session, boss, Role.ANALYST)
    accounts.set_active(db_session, second, True)
    accounts.set_role(db_session, boss, Role.ANALYST)  # now there is another
    assert boss.role == "analyst"


def test_switching_off_or_demoting_someone_who_is_not_an_admin_never_needs_another_admin(
    db_session,
):
    make_user(db_session, "boss@example.com", Role.ADMIN, "Acme")
    analyst = make_user(db_session, "analyst@example.com", Role.ANALYST, "Acme")
    accounts.set_active(db_session, analyst, False)
    accounts.set_role(db_session, analyst, Role.VIEWER)
    assert (analyst.role, analyst.is_active) == ("viewer", False)


def test_an_admin_who_is_already_off_can_be_demoted_without_another_admin(db_session):
    only = make_user(db_session, "only@example.com", Role.ADMIN, "Acme")
    other = make_user(db_session, "other@example.com", Role.ADMIN, "Acme")
    accounts.set_active(db_session, other, False)
    # the one switched off is not what keeps the organization running: demoting it loses nothing
    accounts.set_role(db_session, other, Role.VIEWER)
    assert other.role == "viewer" and only.role == "admin"
