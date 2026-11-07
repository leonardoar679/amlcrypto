"""cases, case_events and case_links: investigations, their timeline and what they rely on

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0010"
down_revision: str | None = "0009"
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
        "cases",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("organization_id", INTEGER_PK, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False),
        sa.Column("outcome", sa.String(12), nullable=True),
        sa.Column("chain_id", identifier(64), nullable=True),
        sa.Column("address", identifier(128), nullable=True),
        sa.Column("source_alert_id", INTEGER_PK, nullable=True),
        sa.Column("assignee_id", INTEGER_PK, nullable=True),
        sa.Column("created_by", INTEGER_PK, nullable=False),
        sa.Column("closed_at", UTC_DATETIME, nullable=True),
        sa.Column("closed_by", INTEGER_PK, nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_cases_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["chain_id"], ["chains.id"], name=op.f("fk_cases_chain_id_chains"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_alert_id"],
            ["alerts.id"],
            name=op.f("fk_cases_source_alert_id_alerts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["users.id"],
            name=op.f("fk_cases_assignee_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_cases_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["closed_by"],
            ["users.id"],
            name=op.f("fk_cases_closed_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cases")),
    )
    op.create_index(op.f("ix_cases_source_alert_id"), "cases", ["source_alert_id"])
    op.create_index(
        "ix_cases_organization_id_status_id", "cases", ["organization_id", "status", "id"]
    )

    op.create_table(
        "case_events",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("case_id", INTEGER_PK, nullable=False),
        sa.Column("organization_id", INTEGER_PK, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("actor_type", sa.String(12), nullable=False),
        sa.Column("actor_id", INTEGER_PK, nullable=True),
        sa.Column("actor_label", sa.String(320), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", UTC_DATETIME, nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_case_events_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_case_events_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_events")),
    )
    op.create_index("ix_case_events_case_id_id", "case_events", ["case_id", "id"])

    op.create_table(
        "case_links",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("case_id", INTEGER_PK, nullable=False),
        sa.Column("organization_id", INTEGER_PK, nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("chain_id", identifier(64), nullable=True),
        sa.Column("reference", identifier(128), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("link_key", identifier(300), nullable=False),
        sa.Column("created_by", INTEGER_PK, nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_case_links_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_case_links_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_case_links_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_links")),
        sa.UniqueConstraint("case_id", "link_key", name="uq_case_links_case_id_link_key"),
    )
    op.create_index(op.f("ix_case_links_case_id"), "case_links", ["case_id"])


def downgrade() -> None:
    # Tables only, children first: dropping a table drops its indexes, and MySQL refuses to drop an
    # index that backs a foreign key (see 0001).
    op.drop_table("case_links")
    op.drop_table("case_events")
    op.drop_table("cases")
