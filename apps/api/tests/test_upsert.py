"""The bulk upsert: the statements it builds for each database, and what it does on SQLite.

MySQL cannot be run on the development machine, so what the statement says is checked here by
compiling it; the CI jobs that run against MySQL 8.0 and 8.4 then check that it works.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import mysql, sqlite

from app.database.models import AddressTransaction, DataCoverage
from app.database.upsert import upsert_rows, upsert_statement

KEYS = ("chain_id", "address", "stream")
T0 = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)


def sql(dialect: str, compiled_for, **options) -> str:
    statement = upsert_statement(dialect, DataCoverage, keys=KEYS, **options)
    return str(statement.compile(dialect=compiled_for.dialect())).replace("\n", " ")


def test_mysql_overwrites_with_the_incoming_value_and_keeps_what_was_known_when_it_is_null():
    text = sql("mysql", mysql, overwrite=["status"], keep_known=["head_block"])

    assert "ON DUPLICATE KEY UPDATE" in text
    assert "status = VALUES(status)" in text
    assert "head_block = coalesce(VALUES(head_block), data_coverage.head_block)" in text
    assert "updated_at = " in text
    assert "INSERT IGNORE" not in text and "created_at = " not in text


def test_sqlite_overwrites_with_the_incoming_value_and_keeps_what_was_known_when_it_is_null():
    text = sql("sqlite", sqlite, overwrite=["status"], keep_known=["head_block"])

    assert "ON CONFLICT (chain_id, address, stream) DO UPDATE SET" in text
    assert "status = excluded.status" in text
    assert "head_block = coalesce(excluded.head_block, data_coverage.head_block)" in text
    assert "created_at = " not in text


def test_a_key_is_never_part_of_what_is_updated():
    for dialect, compiled_for in (("mysql", mysql), ("sqlite", sqlite)):
        text = sql(dialect, compiled_for, overwrite=["status"])
        assert "chain_id = " not in text and "address = " not in text and "stream = " not in text


def test_with_nothing_to_update_a_row_that_exists_is_left_alone_on_both():
    statement = upsert_statement("sqlite", AddressTransaction, keys=("chain_id", "address"))
    text = str(statement.compile(dialect=sqlite.dialect())).replace("\n", " ")
    assert "ON CONFLICT (chain_id, address) DO NOTHING" in text

    statement = upsert_statement("mysql", AddressTransaction, keys=("chain_id", "address"))
    text = str(statement.compile(dialect=mysql.dialect())).replace("\n", " ")
    # A no-op assignment; INSERT IGNORE would also hide a truncated value.
    assert "ON DUPLICATE KEY UPDATE chain_id = address_transactions.chain_id" in text
    assert "IGNORE" not in text


def test_a_database_without_an_upsert_is_refused_loudly():
    with pytest.raises(NotImplementedError, match="postgresql"):
        upsert_statement("postgresql", DataCoverage, keys=KEYS)


def coverage_row(**changes) -> dict:
    values = {
        "chain_id": "eip155:1",
        "address": "0x" + "a1" * 20,
        "stream": "account",
        "status": "complete",
        "synced_through_block": 10,
        "resume_cursor": None,
        "head_block": 12,
        "synced_at": T0,
        "verified": None,
        "notes": [],
        "details": None,
    }
    values.update(changes)
    return values


def stored(db) -> list[DataCoverage]:
    with db.read() as session:
        return list(session.scalars(select(DataCoverage).order_by(DataCoverage.stream)))


def test_a_conflict_overwrites_some_columns_keeps_known_values_and_leaves_the_rest(committing_db):
    with committing_db.write() as session:
        upsert_rows(session, DataCoverage, [coverage_row()], keys=KEYS)
        session.commit()
        created = session.scalars(select(DataCoverage)).one().created_at

    later = datetime(2026, 11, 6, tzinfo=UTC)
    with committing_db.write() as session:
        upsert_rows(
            session,
            DataCoverage,
            [coverage_row(status="partial", synced_at=later, head_block=None, verified="ok")],
            keys=KEYS,
            overwrite=["status", "synced_at"],
            keep_known=["head_block", "verified"],
        )
        session.commit()

    (row,) = stored(committing_db)
    assert (row.status, row.synced_at) == ("partial", later)  # overwritten
    assert (row.head_block, row.verified) == (12, "ok")  # NULL kept the known 12; "ok" is known
    assert row.synced_through_block == 10  # not named: untouched
    assert row.created_at == created and row.updated_at > created


def test_rows_go_in_in_key_order_so_overlapping_writers_lock_in_the_same_order(committing_db):
    seen: list[list] = []

    @event.listens_for(committing_db.engine, "before_cursor_execute")
    def capture(connection, cursor, statement, parameters, context, executemany):
        if executemany and "INSERT INTO data_coverage" in statement:
            seen.append([p[2] for p in parameters])  # the stream column

    rows = [coverage_row(stream=name) for name in ("internal", "erc20", "account")]
    with committing_db.write() as session:
        upsert_rows(session, DataCoverage, rows, keys=KEYS, overwrite=["status"])
        session.commit()

    assert seen and seen[0] == sorted(seen[0])


def test_nothing_to_insert_does_nothing(committing_db):
    with committing_db.write() as session:
        upsert_rows(session, DataCoverage, [], keys=KEYS, overwrite=["status"])
        session.commit()

    assert stored(committing_db) == []
