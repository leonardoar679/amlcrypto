"""Incidents: hacks, thefts and exploits that a platform editor documents for everyone, and what
each statement rests on.

Everything here is global: it belongs to the platform, not to an organization, and only a person
with `users.is_platform_editor` writes it (app.incidents.service). What is published can be read by
anybody (app.incidents.views); nothing in these tables is ever read from, or written about, an
organization's own records.

* `incidents` are the stories. An incident is `draft`, `in_review`, `published` or `archived`.
* `incident_evidence` are the documents a story rests on (who published what, where, when). Every
  number, every attribution and every timeline entry points at the document that says it.
* `incident_amounts` are the amounts the documents report. One row per source: publishers disagree,
  and the platform never states a single total in its own voice.
* `incident_addresses` are the addresses a story names, each with the role it plays in the story and
  how firmly that is established (the attribution status). An address on a chain this platform
  does not read cannot be stored here.
* `incident_updates` are the timeline; after publication they are only added to.
* `incident_flows` are the movements of funds the documents report, entered by hand: from one of
  the incident's addresses to another, with the document, the amount as that source reported it,
  and a transaction when there is one. The public page draws its funds trail from them.

Nothing here is ever derived from provider data held for customers.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
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
from app.database.models.chain_data import ADDRESS_LENGTH, CHAIN_ID_LENGTH, HASH_LENGTH


class Incident(TimestampMixin, Base):
    __tablename__ = "incidents"
    __table_args__ = (Index("ix_incidents_status_published_at", "status", "published_at"),)

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # hack, exploit, theft, ...
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # draft .. archived
    occurred_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # the first
    attribution_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # public
    internal_note: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # editors only, never public
    created_by: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class IncidentEvidence(TimestampMixin, Base):
    __tablename__ = "incident_evidence"
    __table_args__ = (
        UniqueConstraint("incident_id", "ref", name="uq_incident_evidence_incident_id_ref"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The editor's own name for the document within the incident ("e1"), which what rests on it
    # points at; it stays the same when the content is replaced.
    ref: Mapped[str] = mapped_column(String(24), nullable=False)
    publisher: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)  # http or https only
    published_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    retrieved_on: Mapped[date] = mapped_column(Date, nullable=False)  # when an editor read it
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # official, analytics, news ...
    excerpt: Mapped[str | None] = mapped_column(String(500), nullable=True)  # a short quotation
    created_by: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class IncidentAmount(TimestampMixin, Base):
    __tablename__ = "incident_amounts"

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_id: Mapped[int] = mapped_column(
        IntegerPK,
        ForeignKey("incident_evidence.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Whole US dollars, as the source states them (a range when it gives one).
    amount_usd_low: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_usd_high: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)  # "Elliptic estimate"


class IncidentAddress(TimestampMixin, Base):
    __tablename__ = "incident_addresses"
    __table_args__ = (
        UniqueConstraint(
            "incident_id",
            "chain_id",
            "address",
            "role",
            name="uq_incident_addresses_incident_id_chain_id_address_role",
        ),
        Index("ix_incident_addresses_chain_id_address", "chain_id", "address"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chain_id: Mapped[str] = mapped_column(
        identifier(CHAIN_ID_LENGTH), ForeignKey("chains.id", ondelete="RESTRICT"), nullable=False
    )
    address: Mapped[str] = mapped_column(identifier(ADDRESS_LENGTH), nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)  # ATTACKER_FUNDS_RECEIVER ...
    attribution_status: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)  # percent, as shown
    evidence_id: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("incident_evidence.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class IncidentFlow(TimestampMixin, Base):
    __tablename__ = "incident_flows"
    __table_args__ = (
        UniqueConstraint("incident_id", "position", name="uq_incident_flows_incident_id_position"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    # The unique key above starts with the incident, so it is also the index its foreign key needs.
    incident_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)  # the order the editor gave
    # Both ends are addresses the incident names (checked when the content is stored), so every
    # node on the trail has a role and an attribution status. Funds may cross chains (a bridge).
    from_chain_id: Mapped[str] = mapped_column(
        identifier(CHAIN_ID_LENGTH), ForeignKey("chains.id", ondelete="RESTRICT"), nullable=False
    )
    from_address: Mapped[str] = mapped_column(identifier(ADDRESS_LENGTH), nullable=False)
    to_chain_id: Mapped[str] = mapped_column(
        identifier(CHAIN_ID_LENGTH), ForeignKey("chains.id", ondelete="RESTRICT"), nullable=False
    )
    to_address: Mapped[str] = mapped_column(identifier(ADDRESS_LENGTH), nullable=False)
    # Every movement cites the document that reports it.
    evidence_id: Mapped[int] = mapped_column(
        IntegerPK,
        ForeignKey("incident_evidence.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tx_hash: Mapped[str | None] = mapped_column(identifier(HASH_LENGTH), nullable=True)  # sender's
    amount_text: Mapped[str | None] = mapped_column(String(80), nullable=True)  # as reported
    occurred_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class IncidentUpdate(TimestampMixin, Base):
    __tablename__ = "incident_updates"
    __table_args__ = (
        Index("ix_incident_updates_incident_id_occurred_at_id", "incident_id", "occurred_at", "id"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # event, funds_moved ...
    body: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_id: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("incident_evidence.id", ondelete="SET NULL"), nullable=True
    )
    chain_id: Mapped[str | None] = mapped_column(
        identifier(CHAIN_ID_LENGTH), ForeignKey("chains.id", ondelete="RESTRICT"), nullable=True
    )
    tx_hash: Mapped[str | None] = mapped_column(identifier(HASH_LENGTH), nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
