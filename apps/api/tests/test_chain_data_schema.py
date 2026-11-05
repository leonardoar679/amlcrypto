"""The shape of the chain-data tables, checked without a MySQL server.

SQLite accepts almost anything, so each of these protects a limit that only MySQL enforces and that
would otherwise be found by the MySQL CI job, or in production.
"""

from __future__ import annotations

import pytest
from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from app.chains.evm.normalize import erc20_key, internal_key
from app.chains.tron.normalize import trc20_key
from app.database.models import Base
from app.database.models.chain_data import (
    ADDRESS_LENGTH,
    AMOUNT_LENGTH,
    EVENT_KEY_LENGTH,
    HASH_LENGTH,
    StoredTransfer,
)

MYSQL_MAX_IDENTIFIER = 64
INNODB_MAX_INDEX_BYTES = 3072  # DYNAMIC row format
UINT256_MAX = str(2**256 - 1)  # 78 digits
CONTRACT = "0x" + "a" * 40
TRON_ADDRESS = "T" + "A" * 33


def test_every_constraint_and_index_name_fits_mysqls_identifier_limit():
    too_long = []
    for table in Base.metadata.sorted_tables:
        names = [constraint.name for constraint in table.constraints if constraint.name]
        names += [index.name for index in table.indexes]
        names.append(table.name)
        too_long += [name for name in names if len(str(name)) > MYSQL_MAX_IDENTIFIER]
    assert too_long == []


def _bytes_in_index(column) -> int:
    """What a column adds to an index key on MySQL with utf8mb4 (4 bytes a character)."""
    kind = column.type
    if isinstance(kind, String):
        return 4 * (kind.length or 0)
    if isinstance(kind, BigInteger):
        return 8
    if isinstance(kind, DateTime):
        return 8
    if isinstance(kind, Integer | Boolean):
        return 4
    # The primary keys use IntegerPK, a BIGINT with a SQLite variant.
    return 8


def test_no_index_exceeds_innodbs_key_limit():
    too_big = {}
    for table in Base.metadata.sorted_tables:
        keys = [(index.name, index.columns) for index in table.indexes]
        keys += [
            (constraint.name, constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ in ("UniqueConstraint", "PrimaryKeyConstraint")
        ]
        for name, columns in keys:
            size = sum(_bytes_in_index(column) for column in columns)
            if size > INNODB_MAX_INDEX_BYTES:
                too_big[name] = size
    assert too_big == {}


def test_the_longest_event_key_a_connector_can_build_fits_its_column():
    longest = max(
        erc20_key(CONTRACT, CONTRACT, CONTRACT, UINT256_MAX),
        trc20_key(TRON_ADDRESS, TRON_ADDRESS, TRON_ADDRESS, UINT256_MAX),
        internal_key(CONTRACT, CONTRACT, UINT256_MAX),
        key=len,
    )
    # Identical repeats in one transaction get a "#n" suffix; 999 of them is far beyond reality.
    assert len(longest + "#999") <= EVENT_KEY_LENGTH


def test_the_widest_amount_a_chain_allows_fits_its_column():
    assert len(UINT256_MAX) <= AMOUNT_LENGTH


def test_the_identifier_columns_leave_room_for_what_the_chains_produce():
    assert HASH_LENGTH >= 88  # a Solana signature in Base58
    assert ADDRESS_LENGTH >= 90  # the longest Bitcoin address


@pytest.mark.parametrize("name", ["transactions", "transfers"])
def test_identifiers_are_case_sensitive_on_mysql(name):
    sql = str(CreateTable(Base.metadata.tables[name]).compile(dialect=mysql.dialect()))
    for column in ("chain_id", "transaction_hash"):
        assert f"{column} VARCHAR" in sql
        line = next(row for row in sql.splitlines() if row.strip().startswith(f"{column} "))
        assert "COLLATE utf8mb4_bin" in line


def test_the_transfer_identity_is_unique_per_chain():
    constraint = next(
        c for c in StoredTransfer.__table__.constraints if c.name and c.name.startswith("uq_")
    )
    assert [column.name for column in constraint.columns] == [
        "chain_id",
        "transaction_hash",
        "event_key",
    ]


MYSQL_MAX_ROW_BYTES = 65_535  # what MySQL allows the declared columns of one row to add up to


def test_no_table_declares_more_column_bytes_than_a_mysql_row_may_hold():
    """MySQL refuses to create a table whose VARCHAR columns could together exceed 65,535 bytes
    (error 1118), counting every character as four bytes. TEXT, BLOB and JSON columns are stored
    apart from the row and do not count."""
    too_wide = {}
    for table in Base.metadata.sorted_tables:
        declared = sum(
            4 * (column.type.length or 0)
            for column in table.columns
            if isinstance(column.type, String) and not isinstance(column.type, Text)
        )
        if declared > MYSQL_MAX_ROW_BYTES:
            too_wide[table.name] = declared
    assert too_wide == {}
