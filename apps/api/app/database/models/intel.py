"""What the platform knows about addresses from outside the chains: who they belong to and why they
matter, and where each claim came from.

* `label_sources` are the places claims come from (OFAC's sanctions list, later analytics providers
  or community lists), with how far to trust that kind of source and how often it must be refreshed.
  A source also remembers when it was last fetched and checked, which is what "source freshness"
  means: an answer is only as current as the list it came from.
* `entities` are the parties a source names (a sanctioned person, company or vessel).
* `address_labels` are the claims: this address, on this chain, is attributed to this entity under
  this category, by this source, with this confidence. A claim that a later import of the same
  source no longer contains is kept, inactive, with the time it disappeared (a delisting is
  evidence too).
* `label_imports` log each attempt to bring a source up to date.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IntegerPK, TimestampMixin, UTCDateTime, identifier
from app.database.models.chain_data import ADDRESS_LENGTH, CHAIN_ID_LENGTH


class LabelSource(TimestampMixin, Base):
    __tablename__ = "label_sources"

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # official, analytics, community, inferred or unknown: they are not equally reliable.
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    license_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # How far a claim from this source is trusted, in percent, unless the claim says otherwise.
    base_confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    # How old the source's data may be before this platform calls it stale (twice this).
    refresh_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    publish_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # the source's own date
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # active labels
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="never")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Entity(TimestampMixin, Base):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("source_id", "source_ref", name="uq_entities_source_id_source_ref"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("label_sources.id", ondelete="RESTRICT"), nullable=False
    )
    source_ref: Mapped[str] = mapped_column(identifier(100), nullable=False)  # the source's own id
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)  # individual, entity ...
    programs: Mapped[Any] = mapped_column(JSON, nullable=False)  # sanctions programs, in words
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)


class AddressLabel(TimestampMixin, Base):
    __tablename__ = "address_labels"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "source_ref",
            "chain_id",
            "address",
            name="uq_address_labels_source_id_source_ref_chain_id_address",
        ),
        Index("ix_address_labels_chain_id_address", "chain_id", "address"),
        Index("ix_address_labels_source_id_active", "source_id", "active"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    chain_id: Mapped[str] = mapped_column(
        identifier(CHAIN_ID_LENGTH), ForeignKey("chains.id", ondelete="RESTRICT"), nullable=False
    )
    address: Mapped[str] = mapped_column(identifier(ADDRESS_LENGTH), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)  # SANCTIONS, MIXER ...
    entity_id: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("entities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("label_sources.id", ondelete="RESTRICT"), nullable=False
    )
    # The source's id for the record this claim comes from; part of the claim's identity, so that
    # two parties listed with the same address are two claims.
    source_ref: Mapped[str] = mapped_column(identifier(100), nullable=False, default="")
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)  # percent
    listed_as: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # the source's currency
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # When this platform first saw the claim (not when the source made it), and when it last saw it
    # still being made.
    first_observed: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_verified: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    detail: Mapped[Any | None] = mapped_column(JSON, nullable=True)


class LabelImport(Base):
    __tablename__ = "label_imports"
    __table_args__ = (Index("ix_label_imports_source_id_started_at", "source_id", "started_at"),)

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("label_sources.id", ondelete="RESTRICT"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    outcome: Mapped[str] = mapped_column(String(12), nullable=False)  # changed, unchanged, failed
    publish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    records_seen: Mapped[int] = mapped_column(Integer, nullable=False)
    added: Mapped[int] = mapped_column(Integer, nullable=False)
    updated: Mapped[int] = mapped_column(Integer, nullable=False)
    removed: Mapped[int] = mapped_column(Integer, nullable=False)
    unsupported: Mapped[int] = mapped_column(Integer, nullable=False)  # on chains not read here
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
