"""label_sources, entities, address_labels and label_imports: what is known about addresses

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-05
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0006"
down_revision: str | None = "0005"
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
    label_sources = op.create_table(
        "label_sources",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(32), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("license_note", sa.String(255), nullable=True),
        sa.Column("base_confidence", sa.Integer(), nullable=False),
        sa.Column("refresh_seconds", sa.Integer(), nullable=False),
        sa.Column("publish_date", sa.Date(), nullable=True),
        sa.Column("last_checked_at", UTC_DATETIME, nullable=True),
        sa.Column("last_fetched_at", UTC_DATETIME, nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_label_sources")),
        sa.UniqueConstraint("slug", name=op.f("uq_label_sources_slug")),
    )

    op.create_table(
        "entities",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("source_id", INTEGER_PK, nullable=False),
        sa.Column("source_ref", identifier(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("programs", sa.JSON(), nullable=False),
        sa.Column("remarks", sa.Text(), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["label_sources.id"],
            name=op.f("fk_entities_source_id_label_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entities")),
        sa.UniqueConstraint("source_id", "source_ref", name="uq_entities_source_id_source_ref"),
    )

    op.create_table(
        "address_labels",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("chain_id", identifier(64), nullable=False),
        sa.Column("address", identifier(128), nullable=False),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("entity_id", INTEGER_PK, nullable=True),
        sa.Column("source_id", INTEGER_PK, nullable=False),
        sa.Column("source_ref", identifier(100), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("listed_as", sa.String(16), nullable=True),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("first_observed", UTC_DATETIME, nullable=False),
        sa.Column("last_verified", UTC_DATETIME, nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("removed_at", UTC_DATETIME, nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["chains.id"],
            name=op.f("fk_address_labels_chain_id_chains"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=op.f("fk_address_labels_entity_id_entities"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["label_sources.id"],
            name=op.f("fk_address_labels_source_id_label_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_address_labels")),
        sa.UniqueConstraint(
            "source_id",
            "source_ref",
            "chain_id",
            "address",
            name="uq_address_labels_source_id_source_ref_chain_id_address",
        ),
    )
    op.create_index("ix_address_labels_chain_id_address", "address_labels", ["chain_id", "address"])
    op.create_index("ix_address_labels_source_id_active", "address_labels", ["source_id", "active"])
    op.create_index(op.f("ix_address_labels_entity_id"), "address_labels", ["entity_id"])

    op.create_table(
        "label_imports",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("source_id", INTEGER_PK, nullable=False),
        sa.Column("started_at", UTC_DATETIME, nullable=False),
        sa.Column("finished_at", UTC_DATETIME, nullable=False),
        sa.Column("outcome", sa.String(12), nullable=False),
        sa.Column("publish_date", sa.Date(), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("records_seen", sa.Integer(), nullable=False),
        sa.Column("added", sa.Integer(), nullable=False),
        sa.Column("updated", sa.Integer(), nullable=False),
        sa.Column("removed", sa.Integer(), nullable=False),
        sa.Column("unsupported", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["label_sources.id"],
            name=op.f("fk_label_imports_source_id_label_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_label_imports")),
    )
    op.create_index(
        "ix_label_imports_source_id_started_at", "label_imports", ["source_id", "started_at"]
    )

    # The one source there is so far. Its figures are policy, not data: an official list is
    # trusted fully, and is expected to be brought up to date daily (OFAC publishes when it acts).
    now = datetime.now(UTC).replace(tzinfo=None)
    op.bulk_insert(
        label_sources,
        [
            {
                "slug": "ofac_sdn",
                "name": "OFAC Specially Designated Nationals (SDN) list",
                "kind": "official",
                "url": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML",
                "license_note": "US government work; public domain",
                "base_confidence": 100,
                "refresh_seconds": 86400,
                "record_count": 0,
                "status": "never",
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade() -> None:
    # Tables only, children first: dropping a table drops its indexes, and MySQL refuses to drop an
    # index that backs a foreign key (see 0001).
    op.drop_table("label_imports")
    op.drop_table("address_labels")
    op.drop_table("entities")
    op.drop_table("label_sources")
