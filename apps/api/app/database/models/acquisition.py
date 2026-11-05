"""What the platform remembers about its own reading: how far each address has been read, each
network read it made, and the raw answers it chose to keep.

* `data_coverage` is one row per address and stream (the account chains read an address as three
  streams; Bitcoin and Solana as one). It says how much of the stream is held, whether the read
  that produced it ended cleanly, where an interrupted one can carry on, and when it was last
  brought up to date, which is what cache freshness is judged by.
* `fetch_runs` is a log of the reads that went to the network.
* `raw_responses` keeps a provider's answer for a transaction, compressed, where the platform
  decoded it itself and may need to decode it again.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IntegerPK, TimestampMixin, UTCDateTime, identifier
from app.database.models.chain_data import ADDRESS_LENGTH, CHAIN_ID_LENGTH, HASH_LENGTH

# 16 MB on MySQL, where a plain BLOB stops at 64 KB; SQLite has one size for all.
Blob = LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql")


class DataCoverage(TimestampMixin, Base):
    __tablename__ = "data_coverage"
    __table_args__ = (
        UniqueConstraint(
            "chain_id", "address", "stream", name="uq_data_coverage_chain_id_address_stream"
        ),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    chain_id: Mapped[str] = mapped_column(
        identifier(CHAIN_ID_LENGTH), ForeignKey("chains.id", ondelete="RESTRICT"), nullable=False
    )
    address: Mapped[str] = mapped_column(identifier(ADDRESS_LENGTH), nullable=False)
    # "account", "internal", "erc20" / "trc20" on the account chains; "transactions" elsewhere.
    stream: Mapped[str] = mapped_column(String(16), nullable=False)
    # complete: read to its end. partial: a limit stopped the read (see resume_cursor). truncated:
    # the provider's window ran out before the history did. unavailable: no source offers it.
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    # When the stream is complete: every block (slot) up to here has been read, so an update starts
    # from here minus the chain's reorganisation margin.
    synced_through_block: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Where an interrupted read carries on. Empty: that stream has not started. NULL: nothing to do.
    resume_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    head_block: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # when it was read
    # When the last read of this stream ended: the age of the data is measured from here.
    synced_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # Bitcoin and Solana: whether the whole history agreed with the chain's own figures ("ok" or
    # "mismatch"), or NULL when it was not checked.
    verified: Mapped[str | None] = mapped_column(String(12), nullable=True)
    notes: Mapped[Any] = mapped_column(JSON, nullable=False)  # what is missing, in words
    details: Mapped[Any | None] = mapped_column(
        JSON, nullable=True
    )  # the figures it was checked on


class FetchRun(Base):
    __tablename__ = "fetch_runs"
    __table_args__ = (
        Index("ix_fetch_runs_chain_id_address_started_at", "chain_id", "address", "started_at"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    chain_id: Mapped[str] = mapped_column(
        identifier(CHAIN_ID_LENGTH), ForeignKey("chains.id", ondelete="RESTRICT"), nullable=False
    )
    address: Mapped[str] = mapped_column(identifier(ADDRESS_LENGTH), nullable=False)
    mode: Mapped[str] = mapped_column(String(12), nullable=False)  # full, update, rebuild
    outcome: Mapped[str] = mapped_column(String(12), nullable=False)  # complete, partial, failed
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    requests_used: Mapped[int] = mapped_column(Integer, nullable=False)
    from_block: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    new_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unchanged_count: Mapped[int] = mapped_column(Integer, nullable=False)
    removed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sources: Mapped[str] = mapped_column(String(255), nullable=False)  # comma-separated names
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    job_id: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("fetch_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )


class RawResponse(Base):
    __tablename__ = "raw_responses"
    __table_args__ = (
        UniqueConstraint(
            "chain_id",
            "kind",
            "record_key",
            "provider",
            name="uq_raw_responses_chain_id_kind_record_key_provider",
        ),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    chain_id: Mapped[str] = mapped_column(
        identifier(CHAIN_ID_LENGTH), ForeignKey("chains.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # what the payload is
    record_key: Mapped[str] = mapped_column(identifier(HASH_LENGTH), nullable=False)  # its hash
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[bytes] = mapped_column(Blob, nullable=False)  # zlib-compressed JSON
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)  # before compression
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)  # of the uncompressed JSON
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
