from __future__ import annotations

import pytest
from sqlalchemy import inspect

from app.database import migrations
from app.database.connection import create_db_engine
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
        assert {"organizations", "users"} <= _tables(migration_url)

        migrations.downgrade(migration_url, "base")
        assert _tables(migration_url) <= {"alembic_version"}
    finally:
        migrations.upgrade(migration_url)  # leave the shared database at head

    engine = create_db_engine(migration_url)
    try:
        assert migrations.current_revision(engine) == migrations.head_revision()
    finally:
        engine.dispose()


def test_models_and_migrations_have_not_drifted(migration_url):
    migrations.upgrade(migration_url)
    migrations.check(migration_url)  # raises if autogenerate would emit any change


def test_there_is_a_single_head():
    assert migrations.head_revision() is not None
