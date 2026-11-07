"""monitored_addresses and alerts: addresses a team watches and what they raise

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0009"
down_revision: str | None = "0008"
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
        "monitored_addresses",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("organization_id", INTEGER_PK, nullable=False),
        sa.Column("chain_id", identifier(64), nullable=False),
        sa.Column("address", identifier(128), nullable=False),
        sa.Column("name", sa.String(120), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("check_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("depth", sa.SmallInteger(), nullable=False),
        sa.Column("min_severity", sa.String(10), nullable=False),
        sa.Column("created_by", INTEGER_PK, nullable=False),
        sa.Column("next_check_at", UTC_DATETIME, nullable=True),
        sa.Column("last_checked_at", UTC_DATETIME, nullable=True),
        sa.Column("last_changed_at", UTC_DATETIME, nullable=True),
        sa.Column("last_activity_at", UTC_DATETIME, nullable=True),
        sa.Column("last_assessment_id", INTEGER_PK, nullable=True),
        sa.Column("last_risk_score", sa.SmallInteger(), nullable=True),
        sa.Column("last_risk_level", sa.String(10), nullable=True),
        sa.Column("known_findings", sa.JSON(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_monitored_addresses_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["chains.id"],
            name=op.f("fk_monitored_addresses_chain_id_chains"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_monitored_addresses_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_assessment_id"],
            ["risk_assessments.id"],
            name=op.f("fk_monitored_addresses_last_assessment_id_risk_assessments"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monitored_addresses")),
        sa.UniqueConstraint(
            "organization_id",
            "chain_id",
            "address",
            name="uq_monitored_addresses_organization_id_chain_id_address",
        ),
    )
    op.create_index(
        op.f("ix_monitored_addresses_organization_id"), "monitored_addresses", ["organization_id"]
    )
    op.create_index(
        "ix_monitored_addresses_status_next_check_at",
        "monitored_addresses",
        ["status", "next_check_at"],
    )

    op.create_table(
        "alerts",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("organization_id", INTEGER_PK, nullable=False),
        sa.Column("monitored_address_id", INTEGER_PK, nullable=True),
        sa.Column("chain_id", identifier(64), nullable=False),
        sa.Column("address", identifier(128), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("status", sa.String(14), nullable=False),
        sa.Column("resolution", sa.String(20), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("assessment_id", INTEGER_PK, nullable=True),
        sa.Column("previous_assessment_id", INTEGER_PK, nullable=True),
        sa.Column("assignee_id", INTEGER_PK, nullable=True),
        sa.Column("dedupe_key", sa.String(120), nullable=False),
        sa.Column("closed_at", UTC_DATETIME, nullable=True),
        sa.Column("closed_by", INTEGER_PK, nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_alerts_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["monitored_address_id"],
            ["monitored_addresses.id"],
            name=op.f("fk_alerts_monitored_address_id_monitored_addresses"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["chains.id"],
            name=op.f("fk_alerts_chain_id_chains"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["risk_assessments.id"],
            name=op.f("fk_alerts_assessment_id_risk_assessments"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["previous_assessment_id"],
            ["risk_assessments.id"],
            name=op.f("fk_alerts_previous_assessment_id_risk_assessments"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["users.id"],
            name=op.f("fk_alerts_assignee_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["closed_by"],
            ["users.id"],
            name=op.f("fk_alerts_closed_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
        sa.UniqueConstraint(
            "organization_id", "dedupe_key", name="uq_alerts_organization_id_dedupe_key"
        ),
    )
    op.create_index(op.f("ix_alerts_monitored_address_id"), "alerts", ["monitored_address_id"])
    op.create_index(
        "ix_alerts_organization_id_status_id", "alerts", ["organization_id", "status", "id"]
    )


def downgrade() -> None:
    # Tables only, children first: dropping a table drops its indexes, and MySQL refuses to drop an
    # index that backs a foreign key (see 0001).
    op.drop_table("alerts")
    op.drop_table("monitored_addresses")
