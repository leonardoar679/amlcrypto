from __future__ import annotations

import pytest
from sqlalchemy import inspect

from app.database import migrations
from app.database.connection import create_db_engine, for_writing
from tests.conftest import MYSQL_URL


@pytest.fixture
def migration_url(tmp_path) -> str:
    """A database the test may freely migrate. Against MySQL it is the shared test database."""
    return MYSQL_URL or f"sqlite:///{(tmp_path / 'migrations.db').as_posix()}"


def _tables(url: str) -> set[str]:
    engine = create_db_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade_round_trip(migration_url):
    try:
        migrations.downgrade(migration_url, "base")
        assert _tables(migration_url) <= {"alembic_version"}

        migrations.upgrade(migration_url)
        assert {"organizations", "users", "chains", "chain_assets"} <= _tables(migration_url)

        migrations.downgrade(migration_url, "base")
        assert _tables(migration_url) <= {"alembic_version"}
    finally:
        migrations.upgrade(migration_url)  # leave the shared database at head

    engine = create_db_engine(migration_url)
    try:
        assert migrations.current_revision(engine) == migrations.head_revision()
    finally:
        engine.dispose()


def test_the_monitoring_tables_come_and_go_with_their_revision(migration_url):
    tables = {"monitored_addresses", "alerts"}
    try:
        migrations.upgrade(migration_url)
        migrations.downgrade(migration_url, "0008")
        assert not tables & _tables(migration_url)
        migrations.upgrade(migration_url)
        assert tables <= _tables(migration_url)
    finally:
        migrations.upgrade(migration_url)  # leave the shared database at head


def test_the_case_tables_come_and_go_with_their_revision(migration_url):
    tables = {"cases", "case_events", "case_links"}
    try:
        migrations.upgrade(migration_url)
        migrations.downgrade(migration_url, "0009")
        assert not tables & _tables(migration_url)
        migrations.upgrade(migration_url)
        assert tables <= _tables(migration_url)
    finally:
        migrations.upgrade(migration_url)  # leave the shared database at head


def test_models_and_migrations_have_not_drifted(migration_url):
    migrations.upgrade(migration_url)
    migrations.check(migration_url)  # raises if autogenerate would emit any change


def test_there_is_a_single_head():
    assert migrations.head_revision() is not None


def test_users_that_exist_before_roles_become_admins_and_roles_cannot_be_empty(migration_url):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    stamp = "2026-01-01"
    insert_org = text(
        "INSERT INTO organizations (name, slug, is_active, created_at, updated_at) "
        "VALUES ('Old Org', 'old-org', 1, :t, :t)"
    )
    insert_user = text(
        "INSERT INTO users "
        "(organization_id, email, password_hash, is_active, created_at, updated_at) "
        "VALUES (:org, :email, 'x', 1, :t, :t)"
    )
    engine = for_writing(create_db_engine(migration_url))
    try:
        migrations.downgrade(migration_url, "base")
        migrations.upgrade(migration_url, "0007")
        with engine.begin() as connection:
            connection.execute(insert_org, {"t": stamp})
            org = connection.execute(
                text("SELECT id FROM organizations WHERE slug = 'old-org'")
            ).scalar()
            connection.execute(insert_user, {"org": org, "email": "old@example.com", "t": stamp})

        migrations.upgrade(migration_url)

        with engine.begin() as connection:
            role = connection.execute(
                text("SELECT role FROM users WHERE email = 'old@example.com'")
            ).scalar()
            assert role == "admin"
        # there is no default: whoever makes a user chooses a role
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(insert_user, {"org": org, "email": "new@example.com", "t": stamp})
    finally:
        migrations.upgrade(migration_url)  # leave the shared database at head, and clean up
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE email = 'old@example.com'"))
            connection.execute(text("DELETE FROM organizations WHERE slug = 'old-org'"))
        engine.dispose()
