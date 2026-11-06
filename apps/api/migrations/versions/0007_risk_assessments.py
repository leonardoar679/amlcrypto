"""risk_assessments, risk_findings and risk_rules: the AML risk engine's records

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0007"
down_revision: str | None = "0006"
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
        "risk_assessments",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("organization_id", INTEGER_PK, nullable=False),
        sa.Column("job_id", INTEGER_PK, nullable=True),
        sa.Column("subject_type", sa.String(12), nullable=False),
        sa.Column("chain_id", identifier(64), nullable=False),
        sa.Column("subject", identifier(100), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("risk_score", sa.SmallInteger(), nullable=True),
        sa.Column("risk_level", sa.String(10), nullable=True),
        sa.Column("confidence_percent", sa.SmallInteger(), nullable=True),
        sa.Column("data_coverage", sa.String(14), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", UTC_DATETIME, nullable=True),
        sa.Column("completed_at", UTC_DATETIME, nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_risk_assessments_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["fetch_jobs.id"],
            name=op.f("fk_risk_assessments_job_id_fetch_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["chains.id"],
            name=op.f("fk_risk_assessments_chain_id_chains"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_risk_assessments")),
    )
    op.create_index(
        "ix_risk_assessments_subject",
        "risk_assessments",
        ["organization_id", "chain_id", "subject", "id"],
    )

    op.create_table(
        "risk_findings",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("assessment_id", INTEGER_PK, nullable=False),
        sa.Column("rule_id", sa.String(16), nullable=False),
        sa.Column("rule_version", sa.String(16), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("factor", sa.String(24), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("hops", sa.SmallInteger(), nullable=True),
        sa.Column("direction", sa.String(10), nullable=True),
        sa.Column("target", sa.String(200), nullable=True),
        sa.Column("confidence_percent", sa.SmallInteger(), nullable=False),
        sa.Column("contribution_hundredths", sa.Integer(), nullable=False),
        sa.Column("counted", sa.Boolean(), nullable=False),
        sa.Column("suppressed_by", sa.String(16), nullable=True),
        sa.Column("transaction_hashes", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("latest_movement_at", UTC_DATETIME, nullable=True),
        sa.Column("last_verified_at", UTC_DATETIME, nullable=True),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["risk_assessments.id"],
            name=op.f("fk_risk_findings_assessment_id_risk_assessments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_risk_findings")),
    )
    op.create_index(op.f("ix_risk_findings_assessment_id"), "risk_findings", ["assessment_id"])
    op.create_index("ix_risk_findings_rule_id_severity", "risk_findings", ["rule_id", "severity"])

    op.create_table(
        "risk_rules",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("rule_id", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("note", sa.String(255), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_risk_rules")),
        sa.UniqueConstraint("rule_id", name=op.f("uq_risk_rules_rule_id")),
    )


def downgrade() -> None:
    # Tables only, children first: dropping a table drops its indexes, and MySQL refuses to drop an
    # index that backs a foreign key (see 0001).
    op.drop_table("risk_rules")
    op.drop_table("risk_findings")
    op.drop_table("risk_assessments")
