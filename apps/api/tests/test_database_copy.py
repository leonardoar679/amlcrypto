"""Copying one database into another.

The copy is all or nothing and checked: every row of every table arrives with its id, as it was,
and the target is committed only after every table has been read back and compared. The tests run it
SQLite to SQLite everywhere; the MySQL leg (SQLite into MySQL and back) runs where
TEST_DATABASE_URL names a MySQL database, which is the api-mysql job in CI and nowhere else.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, func, select, text, update

from app.database import migrations
from app.database.connection import create_db_engine, for_writing
from app.database.models import Base, Chain, Organization
from app.operations import copy as ops
from app.operations.backup import OperationError
from tests.conftest import MYSQL_URL
from tests.fill import fill


@pytest.fixture
def engines():
    made: list[Engine] = []

    def make(url: str) -> Engine:
        engine = create_db_engine(url)
        made.append(engine)
        return engine

    yield make
    for engine in made:
        engine.dispose()


@pytest.fixture
def full(sqlite_url, engines) -> Engine:
    """A database with rows in every table."""
    engine = engines(sqlite_url)
    fill(engine)
    return engine


@pytest.fixture
def empty(sqlite_template, tmp_path, engines) -> Engine:
    """A database that has only been migrated."""
    target = tmp_path / "target" / "aml.db"
    target.parent.mkdir()
    shutil.copyfile(sqlite_template, target)
    return engines(f"sqlite:///{target.as_posix()}")


def rows_of(engine: Engine) -> dict[str, list[tuple]]:
    with engine.connect() as connection:
        return {
            table.name: [
                tuple(row)
                for row in connection.execute(
                    select(table).order_by(*table.primary_key.columns)
                ).all()
            ]
            for table in Base.metadata.sorted_tables
        }


def counts_of(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table.name: connection.execute(select(func.count()).select_from(table)).scalar_one()
            for table in Base.metadata.sorted_tables
        }


# --- the copy ----------------------------------------------------------------------------------


def test_every_row_of_every_table_arrives_as_it_was(full, empty):
    report = ops.copy_database(full, empty, batch=2)

    assert rows_of(empty) == rows_of(full)
    assert {table.name for table in report.tables} == {t.name for t in Base.metadata.sorted_tables}
    assert report.rows == sum(counts_of(full).values()) and report.revision
    assert all(table.rows >= 1 for table in report.tables)


def test_the_ids_are_kept_so_every_reference_still_points_where_it_did(full, empty):
    ops.copy_database(full, empty)

    with empty.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
        assert connection.execute(text("PRAGMA integrity_check")).fetchall() == [("ok",)]
    with full.connect() as a, empty.connect() as b:
        for table in Base.metadata.sorted_tables:
            ids = (
                f"SELECT {table.primary_key.columns.values()[0].name} FROM {table.name} ORDER BY 1"
            )
            assert a.execute(text(ids)).fetchall() == b.execute(text(ids)).fetchall(), table.name


def test_what_the_migrations_seed_is_replaced_by_what_the_source_has(full, empty):
    with for_writing(full).begin() as connection:
        connection.execute(update(Chain).where(Chain.id == "eip155:56").values(enabled=False))

    ops.copy_database(full, empty)

    with empty.connect() as connection:
        enabled = connection.execute(
            select(Chain.enabled).where(Chain.id == "eip155:56")
        ).scalar_one()
    assert enabled is False


def test_the_seeds_are_the_only_rows_a_migrated_database_holds(empty):
    """The copy clears what the migrations seed and insists that nothing else has rows; if a
    migration starts seeding another table, this says so before a copy fails on it."""
    held = {name for name, count in counts_of(empty).items() if count}
    assert held == set(ops.SEEDED)


def test_the_source_is_left_alone(full, empty):
    before = rows_of(full)
    ops.copy_database(full, empty)
    assert rows_of(full) == before


def test_progress_is_reported_table_by_table(full, empty):
    seen: list[tuple[str, int, int]] = []

    ops.copy_database(full, empty, batch=2, progress=lambda *step: seen.append(step))

    assert seen and all(done <= total for _, done, total in seen)
    assert {name for name, _, _ in seen} == {t.name for t in Base.metadata.sorted_tables}
    users = [step for step in seen if step[0] == "users"]
    assert users[-1][1:] == (5, 5) and len(users) == 3  # 5 rows in batches of 2


def test_a_batch_is_cut_by_what_it_weighs_as_well_as_by_its_rows(full, empty, monkeypatch):
    """MySQL refuses a statement larger than its max_allowed_packet; a table of stored answers
    would make one of a thousand rows."""
    monkeypatch.setattr(ops, "MAX_BATCH_BYTES", 300)
    seen: list[tuple[str, int, int]] = []

    ops.copy_database(full, empty, batch=1000, progress=lambda *step: seen.append(step))

    raw = [step for step in seen if step[0] == "raw_responses"]
    assert len(raw) > 1, "a table of 5 rows of about 260 bytes each is cut into several batches"
    assert rows_of(empty) == rows_of(full)


# --- what is refused -----------------------------------------------------------------------------


def test_a_target_that_holds_data_is_refused_and_left_alone(full, empty):
    with for_writing(empty).begin() as connection:
        connection.execute(
            Organization.__table__.insert().values(
                name="Already here",
                slug="already-here",
                is_active=True,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
    before = rows_of(empty)

    with pytest.raises(OperationError, match="not empty"):
        ops.copy_database(full, empty)

    assert rows_of(empty) == before


def test_a_database_at_another_revision_is_refused(full, empty):
    migrations.downgrade(str(empty.url), "-1")

    with pytest.raises(OperationError, match="migrate it first"):
        ops.copy_database(full, empty)
    with pytest.raises(OperationError, match="migrate it first"):
        ops.copy_database(empty, full)


def test_a_copy_that_fails_leaves_the_target_exactly_as_it_was(full, empty):
    """A row the source holds that the target cannot (here: one that points at nothing, which a
    SQLite file with its foreign keys off will accept) stops the copy, and nothing is kept."""
    raw = sqlite3.connect(full.url.database)
    raw.execute("PRAGMA foreign_keys=OFF")
    raw.execute("UPDATE users SET organization_id = 424242 WHERE id = 3")
    raw.commit()
    raw.close()
    before = rows_of(empty)

    with pytest.raises(OperationError, match="nothing was kept"):
        ops.copy_database(full, empty)

    assert rows_of(empty) == before


def test_a_table_that_did_not_arrive_as_it_was_stops_the_copy(full, empty, monkeypatch):
    real = ops.digest

    def lying(connection, table, **kwargs):
        found = real(connection, table, **kwargs)
        if table.name == "alerts" and connection.engine.url == empty.url:
            return ops.TableCopy(found.name, found.rows, "0" * 64)
        return found

    monkeypatch.setattr(ops, "digest", lying)
    before = rows_of(empty)

    with pytest.raises(OperationError, match="alerts did not arrive as it was"):
        ops.copy_database(full, empty)

    assert rows_of(empty) == before


def test_a_batch_has_at_least_one_row(full, empty):
    with pytest.raises(OperationError):
        ops.copy_database(full, empty, batch=0)


# --- the digest ----------------------------------------------------------------------------------


def test_the_digest_sees_every_kind_of_change(full, engines):
    table = Base.metadata.tables["users"]
    base = ops.digest(full.connect(), table)

    def changed(statement: str) -> ops.TableCopy:
        with for_writing(full).begin() as connection:
            connection.execute(text(statement))
        with full.connect() as connection:
            return ops.digest(connection, table)

    after_text = changed("UPDATE users SET email = email || ' ' WHERE id = 2")
    assert after_text.rows == base.rows and after_text.digest != base.digest
    after_null = changed("UPDATE users SET last_login_at = NULL")
    assert after_null.digest != after_text.digest
    after_micro = changed("UPDATE users SET created_at = '2026-11-07 12:00:00.123458' WHERE id = 1")
    assert after_micro.digest != after_null.digest


def test_the_digest_does_not_depend_on_how_json_was_written(full):
    """MySQL keeps JSON in its own order and spacing; what counts is what it says."""
    table = Base.metadata.tables["fetch_jobs"]
    with full.connect() as connection:
        before = ops.digest(connection, table)
    with for_writing(full).begin() as connection:
        connection.execute(
            text('UPDATE fetch_jobs SET payload = \'{ "list" : [1] ,"text":"é🙂" }\'')
        )
        raw = connection.execute(text("SELECT payload FROM fetch_jobs WHERE id = 1")).scalar_one()
    with full.connect() as connection:
        after = ops.digest(connection, table)
    assert '"list" : [1]' in raw
    assert before.digest != after.digest  # the value did change (it was a different object)


def test_the_digest_does_not_depend_on_the_order_of_keys_in_json(full):
    """MySQL keeps a JSON object's keys in an order of its own; only what the object says counts."""
    table = Base.metadata.tables["fetch_jobs"]
    with full.connect() as connection:
        before = ops.digest(connection, table)
        payloads = connection.execute(text("SELECT id, payload FROM fetch_jobs")).all()
    reversed_text = {
        number: json.dumps(dict(reversed(list(json.loads(payload).items()))))
        for number, payload in payloads
    }
    assert any(list(json.loads(p)) != list(json.loads(reversed_text[n])) for n, p in payloads)
    with for_writing(full).begin() as connection:
        for number, payload in reversed_text.items():
            connection.execute(
                text("UPDATE fetch_jobs SET payload = :p WHERE id = :n"),
                {"p": payload, "n": number},
            )
    with full.connect() as connection:
        after = ops.digest(connection, table)

    assert after == before


def test_plain_values_are_the_same_whatever_database_they_came_from():
    moment = datetime(2026, 11, 6, 12, 0, 0, 5, tzinfo=UTC)
    assert ops._plain(moment) == "2026-11-06T12:00:00.000005+00:00"
    assert ops._plain({"b": [1, 2.5, None], "a": Decimal("1.50")}) == {
        "b": [1, "2.5", None],
        "a": "1.50",
    }
    assert ops._plain(bytes([0, 255])) == "0x00ff"
    assert ops._plain(date(2026, 11, 6)) == "2026-11-06"
    assert ops._plain(True) is True and ops._plain(7) == 7 and ops._plain("é") == "é"
    with pytest.raises(OperationError):
        ops._plain(object())


def test_every_table_has_a_primary_key_to_order_by():
    assert [t.name for t in Base.metadata.sorted_tables if not t.primary_key.columns] == []


# --- into MySQL and back -------------------------------------------------------------------------


@pytest.mark.skipif(not MYSQL_URL, reason="needs TEST_DATABASE_URL to be a MySQL database")
def test_a_sqlite_database_is_copied_into_mysql_and_back_unchanged(full, tmp_path, engines):
    assert MYSQL_URL is not None
    # the shared test database is migrated and holds only the seeds; this loads it, so afterwards it
    # is rebuilt for whatever test runs next
    mysql = engines(MYSQL_URL)
    try:
        migrations.downgrade(MYSQL_URL, "base")
        migrations.upgrade(MYSQL_URL)
        forward = ops.copy_database(full, mysql, batch=3)
        assert forward.rows == sum(counts_of(full).values())
        assert counts_of(mysql) == counts_of(full)

        back_url = f"sqlite:///{(tmp_path / 'back.db').as_posix()}"
        migrations.upgrade(back_url)
        back = engines(back_url)
        ops.copy_database(mysql, back, batch=3)
        assert rows_of(back) == rows_of(full)
    finally:
        mysql.dispose()
        migrations.downgrade(MYSQL_URL, "base")
        migrations.upgrade(MYSQL_URL)
