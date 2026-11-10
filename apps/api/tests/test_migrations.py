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


def _columns(url: str, table: str) -> set[str]:
    engine = create_db_engine(url)
    try:
        return {column["name"] for column in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def test_the_incident_tables_and_the_editor_flag_come_and_go_with_their_revision(migration_url):
    tables = {
        "incidents",
        "incident_evidence",
        "incident_amounts",
        "incident_addresses",
        "incident_updates",
    }
    try:
        migrations.upgrade(migration_url)
        migrations.downgrade(migration_url, "0010")
        assert not tables & _tables(migration_url)
        assert "is_platform_editor" not in _columns(migration_url, "users")
        migrations.upgrade(migration_url)
        assert tables <= _tables(migration_url)
        assert "is_platform_editor" in _columns(migration_url, "users")
    finally:
        migrations.upgrade(migration_url)  # leave the shared database at head


def test_the_incident_revision_comes_and_goes_on_a_database_that_holds_data(sqlite_url):
    """A table rebuild (what SQLite's batch alter does) fails once other tables hold rows that point
    at the one being rebuilt; the revision must not need one, in either direction."""
    from sqlalchemy import text

    from tests.fill import fill

    engine = create_db_engine(sqlite_url)
    try:
        fill(engine)
        with engine.connect() as connection:
            people = connection.execute(text("SELECT COUNT(*) FROM users")).scalar()
        assert people

        migrations.downgrade(sqlite_url, "0010")
        assert "is_platform_editor" not in _columns(sqlite_url, "users")
        migrations.upgrade(sqlite_url)

        with engine.connect() as connection:
            now = connection.execute(text("SELECT COUNT(*) FROM users")).scalar()
            editors = connection.execute(
                text("SELECT COUNT(*) FROM users WHERE is_platform_editor")
            ).scalar()
        assert (now, editors) == (people, 0)
    finally:
        engine.dispose()


def test_people_who_exist_before_editors_are_not_editors(migration_url):
    from sqlalchemy import text

    stamp = "2026-01-01"
    engine = for_writing(create_db_engine(migration_url))
    try:
        migrations.downgrade(migration_url, "base")
        migrations.upgrade(migration_url, "0010")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organizations (name, slug, is_active, created_at, updated_at) "
                    "VALUES ('Old Org', 'old-org', 1, :t, :t)"
                ),
                {"t": stamp},
            )
            org = connection.execute(
                text("SELECT id FROM organizations WHERE slug = 'old-org'")
            ).scalar()
            connection.execute(
                text(
                    "INSERT INTO users (organization_id, email, password_hash, role, "
                    "is_active, created_at, updated_at) "
                    "VALUES (:org, 'old@example.com', 'x', 'admin', 1, :t, :t)"
                ),
                {"org": org, "t": stamp},
            )

        migrations.upgrade(migration_url)

        with engine.begin() as connection:
            flag = connection.execute(
                text("SELECT is_platform_editor FROM users WHERE email = 'old@example.com'")
            ).scalar()
            assert not flag
    finally:
        migrations.upgrade(migration_url)  # leave the shared database at head, and clean up
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE email = 'old@example.com'"))
            connection.execute(text("DELETE FROM organizations WHERE slug = 'old-org'"))
        engine.dispose()


def test_models_and_migrations_have_not_drifted(migration_url):
    migrations.upgrade(migration_url)
    migrations.check(migration_url)  # raises if autogenerate would emit any change


def test_there_is_a_single_head():
    assert migrations.head_revision() is not None


def test_users_that_exist_before_roles_become_admins_and_roles_cannot_be_empty(migration_url):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

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
        # there is no default: whoever makes a user chooses a role. SQLite refuses the NULL with an
        # integrity error; MySQL says the field has no default value (error 1364, an operational
        # one). Either way the row is refused.
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(insert_user, {"org": org, "email": "new@example.com", "t": stamp})
    finally:
        migrations.upgrade(migration_url)  # leave the shared database at head, and clean up
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE email = 'old@example.com'"))
            connection.execute(text("DELETE FROM organizations WHERE slug = 'old-org'"))
        engine.dispose()
