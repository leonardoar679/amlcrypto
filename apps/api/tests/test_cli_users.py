"""The operator's commands for users and the audit log."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app import cli
from app.core.config import Settings, get_settings
from app.database.connection import create_db_engine, create_session_factory
from app.database.models import AuditLog, User
from tests.conftest import TEST_SECRET

PASSWORD = "correct horse battery staple"


@pytest.fixture
def cli_env(monkeypatch, database_url, committing_db):
    # The command line commits for real. committing_db empties what the tests wrote when each one
    # ends, which matters on MySQL, where every test shares one database: without it the audit
    # entries and the people of one test are still there for the next.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    yield database_url
    get_settings.cache_clear()


def users_and_audit(url):
    engine = create_db_engine(url)
    try:
        with create_session_factory(engine)() as session:
            return (
                {u.email: (u.role, u.is_active) for u in session.scalars(select(User))},
                session.scalars(select(AuditLog).order_by(AuditLog.id)).all(),
            )
    finally:
        engine.dispose()


def create(email="boss@example.com", *extra, org="Acme"):
    return cli.main(
        ["create-user", "--email", email, "--org-name", org, "--password", PASSWORD, *extra]
    )


def test_the_first_user_is_an_admin_unless_told_otherwise(cli_env, capsys):
    assert create() == 0
    assert "Created admin boss@example.com" in capsys.readouterr().out
    assert create("viewer@example.com", "--role", "viewer") == 0
    assert create("analyst@example.com", "--role", "analyst") == 0

    users, _ = users_and_audit(cli_env)
    assert users == {
        "boss@example.com": ("admin", True),
        "viewer@example.com": ("viewer", True),
        "analyst@example.com": ("analyst", True),
    }


def test_a_role_that_does_not_exist_is_refused_by_the_parser(cli_env, capsys):
    with pytest.raises(SystemExit):
        create("x@example.com", "--role", "owner")
    assert "invalid choice" in capsys.readouterr().err


def test_what_the_command_line_does_to_a_person_is_audited_as_the_system(cli_env, capsys):
    create()
    create("viewer@example.com", "--role", "viewer")
    assert cli.main(["user", "set-role", "viewer@example.com", "analyst"]) == 0
    assert cli.main(["user", "deactivate", "viewer@example.com"]) == 0
    assert cli.main(["user", "activate", "viewer@example.com"]) == 0
    assert (
        cli.main(["user", "set-password", "viewer@example.com", "--password", "a different one!"])
        == 0
    )

    _, rows = users_and_audit(cli_env)
    assert [r.action for r in rows] == [
        "user.create",
        "user.create",
        "user.update",
        "user.update",
        "user.update",
        "user.password_reset",
    ]
    assert {(r.actor_type, r.actor_label, r.outcome) for r in rows} == {
        ("system", "cli", "success")
    }
    assert all(r.organization_id is not None and r.target_type == "user" for r in rows)
    assert rows[2].details == {
        "email": "viewer@example.com",
        "changes": {"role": {"from": "viewer", "to": "analyst"}},
    }
    assert "PASSWORD" not in str(rows) and "a different one" not in str([r.details for r in rows])
    assert PASSWORD not in str([r.details for r in rows])


def test_the_last_active_admin_cannot_be_demoted_or_switched_off_from_the_command_line(
    cli_env, capsys
):
    create()
    capsys.readouterr()
    assert cli.main(["user", "set-role", "boss@example.com", "viewer"]) == 1
    assert cli.main(["user", "deactivate", "boss@example.com"]) == 1
    assert "at least one active admin" in capsys.readouterr().err
    users, rows = users_and_audit(cli_env)
    assert users["boss@example.com"] == ("admin", True)
    assert [r.action for r in rows] == ["user.create"]  # a refused change leaves no entry


def test_a_user_who_is_not_there_is_an_error_not_a_crash(cli_env, capsys):
    assert cli.main(["user", "set-role", "ghost@example.com", "viewer"]) == 1
    assert "there is no user" in capsys.readouterr().err


def test_the_new_password_works_and_the_old_one_does_not(cli_env, client, capsys):
    create("someone@example.com", "--role", "viewer", org="Other")
    create()  # a second organization's admin, so the first stays an admin of its own
    assert (
        cli.main(["user", "set-password", "someone@example.com", "--password", "a different one!"])
        == 0
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "someone@example.com", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "someone@example.com", "password": "a different one!"},
        ).status_code
        == 200
    )


def test_users_and_the_audit_log_can_be_listed(cli_env, capsys):
    create()
    create("viewer@example.com", "--role", "viewer")
    capsys.readouterr()

    assert cli.main(["user", "list"]) == 0
    listing = capsys.readouterr().out
    assert "boss@example.com" in listing and "viewer@example.com" in listing
    assert "admin" in listing and "viewer" in listing and "acme" in listing

    assert cli.main(["user", "list", "--org", "nobody"]) == 0
    assert "boss@example.com" not in capsys.readouterr().out

    assert cli.main(["audit", "list", "--limit", "5"]) == 0
    trail = capsys.readouterr().out
    assert trail.count("user.create") == 2 and "cli" in trail and "user:" in trail
    assert cli.main(["audit", "list", "--org", "acme"]) == 0
    assert cli.main(["audit", "list", "--org", "nobody"]) == 1
