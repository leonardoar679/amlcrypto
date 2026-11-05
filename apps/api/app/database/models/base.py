"""Declarative base and the column types every model shares.

SQLite and MySQL disagree on several things that matter here, so the rules live in one place:

* Integer primary keys use BIGINT, except on SQLite, where only a column declared exactly
  INTEGER PRIMARY KEY autoincrements.
* Identifier columns (addresses, transaction hashes, chain ids) are case-sensitive. SQLite
  already compares that way; MySQL's default collation does not, and would treat two Base58
  addresses that differ only by case as the same row.
* Timestamps are stored as naive UTC and handed back timezone-aware.
* Amounts are never floats; they are integer strings (see the `amount_raw` convention).
* Constraints get deterministic names so Alembic can drop and alter them.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Integer, MetaData, String
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, TypeEngine

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


IntegerPK = BigInteger().with_variant(Integer(), "sqlite")


def identifier(length: int) -> TypeEngine[str]:
    """A case-sensitive string column."""
    return String(length).with_variant(String(length, collation="utf8mb4_bin"), "mysql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware in Python, naive UTC in the database (microsecond precision on MySQL)."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "mysql":
            return dialect.type_descriptor(mysql.DATETIME(fsp=6))
        return dialect.type_descriptor(DateTime())

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; pass a timezone-aware value")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
