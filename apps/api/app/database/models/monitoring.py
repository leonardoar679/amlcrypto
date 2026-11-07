"""Monitored addresses and the alerts they raise.

* `monitored_addresses` are tenant-owned: an address an organization wants watched, how often, how
  deep, and from what severity an alert is worth raising. Each row also remembers where the last
  check left off (the score and level it found, the findings it has already seen), so the next one
  can say what is new.
* `alerts` are tenant-owned and are kept when the monitor that raised them is removed (the address
  and chain are copied onto them). An alert is `open`, then `acknowledged`, then `closed` with a
  resolution; every change is in the audit log.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IntegerPK, TimestampMixin, UTCDateTime, identifier
from app.database.models.chain_data import ADDRESS_LENGTH, CHAIN_ID_LENGTH


class MonitoredAddress(TimestampMixin, Base):
    __tablename__ = "monitored_addresses"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "chain_id",
            "address",
            name="uq_monitored_addresses_organization_id_chain_id_address",
        ),
        Index("ix_monitored_addresses_status_next_check_at", "status", "next_check_at"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    chain_id: Mapped[str] = mapped_column(
        identifier(CHAIN_ID_LENGTH), ForeignKey("chains.id", ondelete="RESTRICT"), nullable=False
    )
    address: Mapped[str] = mapped_column(identifier(ADDRESS_LENGTH), nullable=False)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # active, paused
    check_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    depth: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # hops each check follows
    min_severity: Mapped[str] = mapped_column(String(10), nullable=False)  # alert from this up
    created_by: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    # Where checking stands.
    next_check_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_assessment_id: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("risk_assessments.id", ondelete="SET NULL"), nullable=True
    )
    last_risk_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    last_risk_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    known_findings: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Alert(TimestampMixin, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "dedupe_key", name="uq_alerts_organization_id_dedupe_key"
        ),
        Index("ix_alerts_organization_id_status_id", "organization_id", "status", "id"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    monitored_address_id: Mapped[int | None] = mapped_column(
        IntegerPK,
        ForeignKey("monitored_addresses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    chain_id: Mapped[str] = mapped_column(
        identifier(CHAIN_ID_LENGTH), ForeignKey("chains.id", ondelete="RESTRICT"), nullable=False
    )
    address: Mapped[str] = mapped_column(identifier(ADDRESS_LENGTH), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(14), nullable=False)  # open, acknowledged, closed
    resolution: Mapped[str | None] = mapped_column(String(20), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[Any] = mapped_column(JSON, nullable=False, default=dict)
    assessment_id: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("risk_assessments.id", ondelete="SET NULL"), nullable=True
    )
    previous_assessment_id: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("risk_assessments.id", ondelete="SET NULL"), nullable=True
    )
    assignee_id: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    dedupe_key: Mapped[str] = mapped_column(String(120), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    closed_by: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
