"""fetch_jobs (the job queue) and api_usage (provider request counters)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept local on purpose: a migration must not change meaning when app code changes.
INTEGER_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
UTC_DATETIME = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade() -> None:
    op.create_table(
        "fetch_jobs",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("organization_id", INTEGER_PK, nullable=True),
        sa.Column("job_type", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", UTC_DATETIME, nullable=False),
        sa.Column("locked_at", UTC_DATETIME, nullable=True),
        sa.Column("locked_by", sa.String(80), nullable=True),
        sa.Column("dedupe_key", sa.String(200), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("completed_at", UTC_DATETIME, nullable=True),
        sa.Column("created_at", UTC_DATETIME, nullable=False),
        sa.Column("updated_at", UTC_DATETIME, nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_fetch_jobs_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fetch_jobs")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_fetch_jobs_dedupe_key")),
    )
    op.create_index(op.f("ix_fetch_jobs_organization_id"), "fetch_jobs", ["organization_id"])
    op.create_index("ix_fetch_jobs_status_available_at", "fetch_jobs", ["status", "available_at"])

    op.create_table(
        "api_usage",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("period_start", UTC_DATETIME, nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("successes", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.Column("rate_limited", sa.Integer(), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("created_at", UTC_DATETIME, nullable=False),
        sa.Column("updated_at", UTC_DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_usage")),
        sa.UniqueConstraint("provider", "period_start", name="uq_api_usage_provider_period_start"),
    )


def downgrade() -> None:
    # Tables only: dropping a table drops its indexes, and MySQL refuses to drop an index that
    # backs a foreign key (see 0001).
    op.drop_table("api_usage")
    op.drop_table("fetch_jobs")
