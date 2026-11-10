"""incidents and their evidence, amounts, addresses and timeline; platform editors

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0011"
down_revision: str | None = "0010"
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
    # A plain ALTER, not a batch: on SQLite a batch rebuilds the table, which fails once other
    # tables hold rows that point at it (users is referenced by most of them now).
    op.add_column(
        "users",
        sa.Column("is_platform_editor", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "incidents",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=True),
        sa.Column("published_at", UTC_DATETIME, nullable=True),
        sa.Column("attribution_note", sa.Text(), nullable=True),
        sa.Column("internal_note", sa.Text(), nullable=True),
        sa.Column("created_by", INTEGER_PK, nullable=False),
        sa.Column("updated_by", INTEGER_PK, nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_incidents_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_incidents_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incidents")),
        sa.UniqueConstraint("slug", name=op.f("uq_incidents_slug")),
    )
    op.create_index("ix_incidents_status_published_at", "incidents", ["status", "published_at"])

    op.create_table(
        "incident_evidence",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("incident_id", INTEGER_PK, nullable=False),
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
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_evidence_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_incident_evidence_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_evidence")),
        sa.UniqueConstraint("incident_id", "ref", name="uq_incident_evidence_incident_id_ref"),
    )
    op.create_index(op.f("ix_incident_evidence_incident_id"), "incident_evidence", ["incident_id"])

    op.create_table(
        "incident_amounts",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("incident_id", INTEGER_PK, nullable=False),
        sa.Column("evidence_id", INTEGER_PK, nullable=False),
        sa.Column("amount_usd_low", sa.BigInteger(), nullable=False),
        sa.Column("amount_usd_high", sa.BigInteger(), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("label", sa.String(100), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_amounts_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["incident_evidence.id"],
            name=op.f("fk_incident_amounts_evidence_id_incident_evidence"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_amounts")),
    )
    op.create_index(op.f("ix_incident_amounts_incident_id"), "incident_amounts", ["incident_id"])
    op.create_index(op.f("ix_incident_amounts_evidence_id"), "incident_amounts", ["evidence_id"])

    op.create_table(
        "incident_addresses",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("incident_id", INTEGER_PK, nullable=False),
        sa.Column("chain_id", identifier(64), nullable=False),
        sa.Column("address", identifier(128), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("attribution_status", sa.String(24), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("evidence_id", INTEGER_PK, nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("last_verified_at", UTC_DATETIME, nullable=True),
        sa.Column("created_by", INTEGER_PK, nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_addresses_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["chains.id"],
            name=op.f("fk_incident_addresses_chain_id_chains"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["incident_evidence.id"],
            name=op.f("fk_incident_addresses_evidence_id_incident_evidence"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_incident_addresses_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_addresses")),
        sa.UniqueConstraint(
            "incident_id",
            "chain_id",
            "address",
            "role",
            name="uq_incident_addresses_incident_id_chain_id_address_role",
        ),
    )
    op.create_index(
        op.f("ix_incident_addresses_incident_id"), "incident_addresses", ["incident_id"]
    )
    op.create_index(
        "ix_incident_addresses_chain_id_address", "incident_addresses", ["chain_id", "address"]
    )

    op.create_table(
        "incident_updates",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("incident_id", INTEGER_PK, nullable=False),
        sa.Column("occurred_at", UTC_DATETIME, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("evidence_id", INTEGER_PK, nullable=True),
        sa.Column("chain_id", identifier(64), nullable=True),
        sa.Column("tx_hash", identifier(100), nullable=True),
        sa.Column("created_by", INTEGER_PK, nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_updates_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["incident_evidence.id"],
            name=op.f("fk_incident_updates_evidence_id_incident_evidence"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["chains.id"],
            name=op.f("fk_incident_updates_chain_id_chains"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_incident_updates_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_updates")),
    )
    op.create_index(
        "ix_incident_updates_incident_id_occurred_at_id",
        "incident_updates",
        ["incident_id", "occurred_at", "id"],
    )


def downgrade() -> None:
    # Tables only, children first: dropping a table drops its indexes, and MySQL refuses to drop an
    # index that backs a foreign key (see 0001).
    op.drop_table("incident_updates")
    op.drop_table("incident_addresses")
    op.drop_table("incident_amounts")
    op.drop_table("incident_evidence")
    op.drop_table("incidents")
    op.drop_column("users", "is_platform_editor")
