"""wallet profiles: who a known address belongs to, the documents that say so, and the addresses

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept local on purpose: a migration must not change meaning when app code changes.
INTEGER_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
UTC_DATETIME = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def identifier(length: int) -> sa.types.TypeEngine:
    """A case-sensitive string: MySQL's default collation would merge Base58 addresses."""
    return sa.String(length).with_variant(sa.String(length, collation="utf8mb4_bin"), "mysql")


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", UTC_DATETIME, nullable=False),
        sa.Column("updated_at", UTC_DATETIME, nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "wallet_profiles",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("website", sa.String(500), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("published_at", UTC_DATETIME, nullable=True),
        sa.Column("attribution_note", sa.Text(), nullable=True),
        sa.Column("internal_note", sa.Text(), nullable=True),
        sa.Column("created_by", INTEGER_PK, nullable=False),
        sa.Column("updated_by", INTEGER_PK, nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_wallet_profiles_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_wallet_profiles_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wallet_profiles")),
        sa.UniqueConstraint("slug", name=op.f("uq_wallet_profiles_slug")),
    )
    op.create_index("ix_wallet_profiles_status_name", "wallet_profiles", ["status", "name"])

    op.create_table(
        "wallet_profile_evidence",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("profile_id", INTEGER_PK, nullable=False),
        sa.Column("ref", sa.String(24), nullable=False),
        sa.Column("publisher", sa.String(100), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("published_on", sa.Date(), nullable=True),
        sa.Column("retrieved_on", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("excerpt", sa.String(500), nullable=True),
        sa.Column("created_by", INTEGER_PK, nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["wallet_profiles.id"],
            name=op.f("fk_wallet_profile_evidence_profile_id_wallet_profiles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_wallet_profile_evidence_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wallet_profile_evidence")),
        sa.UniqueConstraint("profile_id", "ref", name="uq_wallet_profile_evidence_profile_id_ref"),
    )

    op.create_table(
        "wallet_profile_addresses",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("profile_id", INTEGER_PK, nullable=False),
        sa.Column("chain_id", identifier(64), nullable=False),
        sa.Column("address", identifier(128), nullable=False),
        sa.Column("label", sa.String(60), nullable=False),
        sa.Column("attribution_status", sa.String(24), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("evidence_id", INTEGER_PK, nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("last_verified_at", UTC_DATETIME, nullable=True),
        sa.Column("created_by", INTEGER_PK, nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["wallet_profiles.id"],
            name=op.f("fk_wallet_profile_addresses_profile_id_wallet_profiles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["chains.id"],
            name=op.f("fk_wallet_profile_addresses_chain_id_chains"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["wallet_profile_evidence.id"],
            name=op.f("fk_wallet_profile_addresses_evidence_id_wallet_profile_evidence"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_wallet_profile_addresses_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wallet_profile_addresses")),
        sa.UniqueConstraint(
            "profile_id",
            "chain_id",
            "address",
            name="uq_wallet_profile_addresses_profile_id_chain_id_address",
        ),
    )
    op.create_index(
        "ix_wallet_profile_addresses_chain_id_address",
        "wallet_profile_addresses",
        ["chain_id", "address"],
    )


def downgrade() -> None:
    # Tables only, children first: dropping a table drops its indexes, and MySQL refuses to drop an
    # index that backs a foreign key (see 0001).
    op.drop_table("wallet_profile_addresses")
    op.drop_table("wallet_profile_evidence")
    op.drop_table("wallet_profiles")
