"""users.role, api_keys and audit_logs: who may do what, and the record of what was done

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept local on purpose: a migration must not change meaning when app code changes.
INTEGER_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
UTC_DATETIME = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade() -> None:
    # Every user so far was made by the operator with the command line, so each becomes an admin
    # and nobody loses access. The column has no default afterwards: whoever makes a user chooses.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("role", sa.String(20), nullable=True))
    op.execute(sa.text("UPDATE users SET role = 'admin'"))
    with op.batch_alter_table("users") as batch:
        batch.alter_column("role", existing_type=sa.String(20), nullable=False)

    op.create_table(
        "api_keys",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("organization_id", INTEGER_PK, nullable=False),
        sa.Column("created_by", INTEGER_PK, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("key_prefix", sa.String(8), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("expires_at", UTC_DATETIME, nullable=True),
        sa.Column("last_used_at", UTC_DATETIME, nullable=True),
        sa.Column("revoked_at", UTC_DATETIME, nullable=True),
        sa.Column("revoked_by", INTEGER_PK, nullable=True),
        sa.Column("created_at", UTC_DATETIME, nullable=False),
        sa.Column("updated_at", UTC_DATETIME, nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_api_keys_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_api_keys_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["users.id"],
            name=op.f("fk_api_keys_revoked_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("key_prefix", name=op.f("uq_api_keys_key_prefix")),
    )
    op.create_index(op.f("ix_api_keys_organization_id"), "api_keys", ["organization_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("organization_id", INTEGER_PK, nullable=True),
        sa.Column("actor_type", sa.String(12), nullable=False),
        sa.Column("actor_id", INTEGER_PK, nullable=True),
        sa.Column("actor_label", sa.String(320), nullable=True),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("outcome", sa.String(10), nullable=False),
        sa.Column("target_type", sa.String(40), nullable=True),
        sa.Column("target_id", sa.String(100), nullable=True),
        sa.Column("subject_key", sa.String(64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("created_at", UTC_DATETIME, nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_audit_logs_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        "ix_audit_logs_organization_id_created_at", "audit_logs", ["organization_id", "created_at"]
    )
    op.create_index(
        "ix_audit_logs_action_subject_key_created_at",
        "audit_logs",
        ["action", "subject_key", "created_at"],
    )
    op.create_index(
        "ix_audit_logs_target_type_target_id", "audit_logs", ["target_type", "target_id"]
    )


def downgrade() -> None:
    # Tables only, children first: dropping a table drops its indexes, and MySQL refuses to drop an
    # index that backs a foreign key (see 0001).
    op.drop_table("audit_logs")
    op.drop_table("api_keys")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("role")
