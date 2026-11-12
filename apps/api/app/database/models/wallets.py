"""Wallet profiles: the public record of who a known address belongs to, and what says so.

Everything here is global, like the incidents (app.database.models.incidents): it belongs to the
platform, only a platform editor writes it (app.wallets.service), and what is published can be read
by anybody (app.wallets.public). Nothing in these tables is ever read from, or written about, an
organization's own records, and nothing is derived from the chain data held for customers.

* `wallet_profiles` are the entities: an exchange, a bridge, a protocol, a fund, an institution, a
  government body, or a public figure who has disclosed their own wallet. Never a private
  individual.
* `wallet_profile_evidence` are the documents a profile rests on. Every address cites one.
* `wallet_profile_addresses` are the addresses the documents attribute to the entity, each with
  what it is ("hot wallet") and how firmly that is established (the same six statuses as the
  incidents).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
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


class WalletProfile(TimestampMixin, Base):
    __tablename__ = "wallet_profiles"
    __table_args__ = (Index("ix_wallet_profiles_status_name", "status", "name"),)

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # exchange, bridge, protocol ...
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)  # http or https only
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # draft .. archived
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # the first
    attribution_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # public
    internal_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # editors only
    created_by: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class WalletProfileEvidence(TimestampMixin, Base):
    __tablename__ = "wallet_profile_evidence"
    __table_args__ = (
        UniqueConstraint("profile_id", "ref", name="uq_wallet_profile_evidence_profile_id_ref"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("wallet_profiles.id", ondelete="CASCADE"), nullable=False
    )
    # The editor's own name for the document within the profile ("e1"), which the addresses point
    # at; it stays the same when the content is replaced.
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


class WalletProfileAddress(TimestampMixin, Base):
    __tablename__ = "wallet_profile_addresses"
    __table_args__ = (
        # Starts with the profile, so it is also the index the foreign key to it needs.
        UniqueConstraint(
            "profile_id",
            "chain_id",
            "address",
            name="uq_wallet_profile_addresses_profile_id_chain_id_address",
        ),
        Index("ix_wallet_profile_addresses_chain_id_address", "chain_id", "address"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("wallet_profiles.id", ondelete="CASCADE"), nullable=False
    )
    chain_id: Mapped[str] = mapped_column(
        identifier(CHAIN_ID_LENGTH), ForeignKey("chains.id", ondelete="RESTRICT"), nullable=False
    )
    address: Mapped[str] = mapped_column(identifier(ADDRESS_LENGTH), nullable=False)
    label: Mapped[str] = mapped_column(String(60), nullable=False)  # what it is: "hot wallet"
    attribution_status: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)  # percent, as shown
    evidence_id: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("wallet_profile_evidence.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
