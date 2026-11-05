"""data_coverage, fetch_runs and raw_responses: what the platform remembers about its own reading

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept local on purpose: a migration must not change meaning when app code changes.
INTEGER_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
UTC_DATETIME = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
BLOB = sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql")


def identifier(length: int) -> sa.types.TypeEngine:
    """A case-sensitive string: MySQL's default collation would merge Base58 addresses."""
    return sa.String(length).with_variant(sa.String(length, collation="utf8mb4_bin"), "mysql")


def upgrade() -> None:
    op.create_table(
        "data_coverage",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("chain_id", identifier(64), nullable=False),
        sa.Column("address", identifier(128), nullable=False),
        sa.Column("stream", sa.String(16), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("synced_through_block", sa.BigInteger(), nullable=True),
        sa.Column("resume_cursor", sa.Text(), nullable=True),
        sa.Column("head_block", sa.BigInteger(), nullable=True),
        sa.Column("synced_at", UTC_DATETIME, nullable=False),
        sa.Column("verified", sa.String(12), nullable=True),
        sa.Column("notes", sa.JSON(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", UTC_DATETIME, nullable=False),
        sa.Column("updated_at", UTC_DATETIME, nullable=False),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["chains.id"],
            name=op.f("fk_data_coverage_chain_id_chains"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_coverage")),
        sa.UniqueConstraint(
            "chain_id", "address", "stream", name="uq_data_coverage_chain_id_address_stream"
        ),
    )

    op.create_table(
        "fetch_runs",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("chain_id", identifier(64), nullable=False),
        sa.Column("address", identifier(128), nullable=False),
        sa.Column("mode", sa.String(12), nullable=False),
        sa.Column("outcome", sa.String(12), nullable=False),
        sa.Column("started_at", UTC_DATETIME, nullable=False),
        sa.Column("finished_at", UTC_DATETIME, nullable=False),
        sa.Column("requests_used", sa.Integer(), nullable=False),
        sa.Column("from_block", sa.BigInteger(), nullable=True),
        sa.Column("new_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("unchanged_count", sa.Integer(), nullable=False),
        sa.Column("removed_count", sa.Integer(), nullable=False),
        sa.Column("sources", sa.String(255), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("notes", sa.JSON(), nullable=True),
        sa.Column("job_id", INTEGER_PK, nullable=True),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["chains.id"],
            name=op.f("fk_fetch_runs_chain_id_chains"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["fetch_jobs.id"],
            name=op.f("fk_fetch_runs_job_id_fetch_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fetch_runs")),
    )
    op.create_index(
        "ix_fetch_runs_chain_id_address_started_at",
        "fetch_runs",
        ["chain_id", "address", "started_at"],
    )
    op.create_index(op.f("ix_fetch_runs_job_id"), "fetch_runs", ["job_id"])

    op.create_table(
        "raw_responses",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("chain_id", identifier(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("record_key", identifier(100), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("payload", BLOB, nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("fetched_at", UTC_DATETIME, nullable=False),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["chains.id"],
            name=op.f("fk_raw_responses_chain_id_chains"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raw_responses")),
        sa.UniqueConstraint(
            "chain_id",
            "kind",
            "record_key",
            "provider",
            name="uq_raw_responses_chain_id_kind_record_key_provider",
        ),
    )


def downgrade() -> None:
    # Tables only: dropping a table drops its indexes, and MySQL refuses to drop an index that
    # backs a foreign key (see 0001).
    op.drop_table("raw_responses")
    op.drop_table("fetch_runs")
    op.drop_table("data_coverage")
