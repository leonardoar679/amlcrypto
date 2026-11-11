"""incident_flows: the movements of funds an incident reports

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0012"
down_revision: str | None = "0011"
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
        "incident_flows",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("incident_id", INTEGER_PK, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("from_chain_id", identifier(64), nullable=False),
        sa.Column("from_address", identifier(128), nullable=False),
        sa.Column("to_chain_id", identifier(64), nullable=False),
        sa.Column("to_address", identifier(128), nullable=False),
        sa.Column("evidence_id", INTEGER_PK, nullable=False),
        sa.Column("tx_hash", identifier(100), nullable=True),
        sa.Column("amount_text", sa.String(80), nullable=True),
        sa.Column("occurred_on", sa.Date(), nullable=True),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("created_by", INTEGER_PK, nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_flows_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["from_chain_id"],
            ["chains.id"],
            name=op.f("fk_incident_flows_from_chain_id_chains"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_chain_id"],
            ["chains.id"],
            name=op.f("fk_incident_flows_to_chain_id_chains"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["incident_evidence.id"],
            name=op.f("fk_incident_flows_evidence_id_incident_evidence"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_incident_flows_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_flows")),
        sa.UniqueConstraint(
            "incident_id", "position", name="uq_incident_flows_incident_id_position"
        ),
    )
    op.create_index(op.f("ix_incident_flows_evidence_id"), "incident_flows", ["evidence_id"])


def downgrade() -> None:
    # The table only: dropping it drops its indexes, and MySQL refuses to drop an index that backs
    # a foreign key (see 0001).
    op.drop_table("incident_flows")
