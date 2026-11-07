"""Cases: an investigation a team opens about an address, with its history and what it relied on.

* `cases` are tenant-owned. A case is `open`, `in_progress` or `closed` (with an outcome), has a
  priority and may have an assignee. It may be opened from an alert.
* `case_events` are the case's timeline and are only ever added to: that it was opened, every change
  of status, owner or priority, and the notes people wrote. The audit log records the same actions
  for the whole platform; this is the case's own, readable by whoever can read the case.
* `case_links` point at what the case is about or relied on: addresses, transactions, screenings
  (the reports are kept as they were) and alerts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IntegerPK, TimestampMixin, UTCDateTime, identifier
from app.database.models.chain_data import ADDRESS_LENGTH, CHAIN_ID_LENGTH


class Case(TimestampMixin, Base):
    __tablename__ = "cases"
    __table_args__ = (
        Index("ix_cases_organization_id_status_id", "organization_id", "status", "id"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # open, in_progress, closed
    priority: Mapped[str] = mapped_column(String(10), nullable=False)  # low .. critical
    outcome: Mapped[str | None] = mapped_column(String(12), nullable=True)  # when closed
    chain_id: Mapped[str | None] = mapped_column(
        identifier(CHAIN_ID_LENGTH), ForeignKey("chains.id", ondelete="RESTRICT"), nullable=True
    )
    address: Mapped[str | None] = mapped_column(identifier(ADDRESS_LENGTH), nullable=True)
    source_alert_id: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assignee_id: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    closed_by: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class CaseEvent(Base):
    __tablename__ = "case_events"
    __table_args__ = (Index("ix_case_events_case_id_id", "case_id", "id"),)

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(12), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(IntegerPK, nullable=True)
    actor_label: Mapped[str | None] = mapped_column(String(320), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[Any] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class CaseLink(TimestampMixin, Base):
    __tablename__ = "case_links"
    __table_args__ = (
        UniqueConstraint("case_id", "link_key", name="uq_case_links_case_id_link_key"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # address, transaction, ...
    chain_id: Mapped[str | None] = mapped_column(identifier(CHAIN_ID_LENGTH), nullable=True)
    reference: Mapped[str] = mapped_column(identifier(ADDRESS_LENGTH), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    link_key: Mapped[str] = mapped_column(identifier(300), nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
