"""A database with rows in every table, made from the schema itself.

For the tests that move a whole database (backup, restore, copying to another engine): they must
cover every table and every kind of value a column holds (integers, text with accents and emoji,
JSON with nested keys and floats, binary data, moments with microseconds, booleans, nulls), and a
hand-made dataset covers whatever its author remembered. This one is read off the metadata, so a
table or a column type added later is covered the day it exists.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Engine,
    Float,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Table,
    Text,
    func,
    insert,
    select,
)
from sqlalchemy.types import JSON, TypeDecorator

from app.database.connection import for_writing
from app.database.models import Base

ROWS = 5
BASE_TIME = datetime(2026, 11, 6, 12, 0, 0, 123456, tzinfo=UTC)


def _kind(column) -> Any:
    kind = column.type
    while isinstance(kind, TypeDecorator):
        kind = kind.impl_instance if hasattr(kind, "impl_instance") else kind.impl
    return kind


def _value(table: Table, column, n: int) -> Any:
    """A value of the column's kind, different for each n."""
    kind = _kind(column)
    if isinstance(kind, JSON):
        return {"n": n, "text": "é🙂", "nested": {"z": [1, 2.5, None, True], "a": n}, "list": [n]}
    if isinstance(kind, Boolean):
        return n % 2 == 0
    if isinstance(kind, DateTime):
        return BASE_TIME + timedelta(days=n, microseconds=n)
    if isinstance(kind, Date):
        return date(2026, 11, 1 + n)
    if isinstance(kind, LargeBinary):
        return bytes(range(256)) + bytes([n])
    if isinstance(kind, Float):
        return n + 0.25
    if isinstance(kind, Numeric):
        return n
    if isinstance(kind, Integer | BigInteger):
        return 1_000 + n
    if isinstance(kind, String | Text):
        length = getattr(kind, "length", None)
        if length is not None and length < 8:
            return "abcdefghij"[n % 10] * min(length, 1)
        text = f"{n}|{table.name}.{column.name}|é🙂"  # n first: a cut keeps the rows apart
        return text if length is None else text[:length]
    raise AssertionError(f"no value for a {kind!r} ({table.name}.{column.name})")


def fill(engine: Engine, rows: int = ROWS) -> dict[str, int]:
    """Put `rows` rows in every table that has none (the seeded ones keep theirs), each row
    pointing at the same-numbered row of what it references. Returns how many each table holds."""
    kept: dict[str, list[dict[str, Any]]] = {}
    with for_writing(engine).begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name == "alembic_version":
                continue
            held = connection.execute(select(func.count()).select_from(table)).scalar_one()
            if held:
                kept[table.name] = [dict(r._mapping) for r in connection.execute(select(table))]
                continue
            made = []
            for n in range(rows):
                values: dict[str, Any] = {}
                for column in table.columns:
                    if column.foreign_keys:
                        (key,) = list(column.foreign_keys)[:1]
                        parents = kept[key.column.table.name]
                        parent = parents[n] if n < len(parents) else parents[0]
                        values[column.name] = parent[key.column.name]
                    elif (
                        column.primary_key
                        and column.autoincrement is not False
                        and (isinstance(_kind(column), Integer | BigInteger))
                    ):
                        continue  # the database numbers it
                    else:
                        values[column.name] = _value(table, column, n)
                result = connection.execute(insert(table).values(**values))
                row = {**values}
                for name, value in zip(
                    [c.name for c in table.primary_key.columns],
                    result.inserted_primary_key or [],
                    strict=False,
                ):
                    row[name] = value
                made.append(row)
            kept[table.name] = made
    with engine.connect() as connection:
        return {
            table.name: connection.execute(select(func.count()).select_from(table)).scalar_one()
            for table in Base.metadata.sorted_tables
        }
