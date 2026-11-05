"""chains and chain_assets, seeded with the five supported chains

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept local on purpose: a migration must not change meaning when app code changes.
INTEGER_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
UTC_DATETIME = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def case_sensitive(length: int) -> sa.types.TypeEngine:
    return sa.String(length).with_variant(sa.String(length, collation="utf8mb4_bin"), "mysql")


# Frozen snapshot of app.chains.registry as of this revision (a test compares the two).
# (CAIP-2 id, slug, name, family, native symbol, native name, native decimals)
SEED_CHAINS = [
    ("eip155:1", "ethereum", "Ethereum", "evm", "ETH", "Ether", 18),
    ("eip155:56", "bsc", "BNB Smart Chain", "evm", "BNB", "BNB", 18),
    ("tron:728126428", "tron", "TRON", "tron", "TRX", "TRON", 6),
    (
        "bip122:000000000019d6689c085ae165831e93",
        "bitcoin",
        "Bitcoin",
        "utxo",
        "BTC",
        "Bitcoin",
        8,
    ),
    ("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp", "solana", "Solana", "solana", "SOL", "Solana", 9),
]


def upgrade() -> None:
    chains = op.create_table(
        "chains",
        sa.Column("id", case_sensitive(64), nullable=False),
        sa.Column("slug", sa.String(32), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("family", sa.String(16), nullable=False),
        sa.Column("native_symbol", sa.String(16), nullable=False),
        sa.Column("native_decimals", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTC_DATETIME, nullable=False),
        sa.Column("updated_at", UTC_DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chains")),
        sa.UniqueConstraint("slug", name=op.f("uq_chains_slug")),
    )
    chain_assets = op.create_table(
        "chain_assets",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("chain_id", case_sensitive(64), nullable=False),
        sa.Column("asset_key", case_sensitive(128), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("decimals", sa.Integer(), nullable=False),
        sa.Column("standard", sa.String(16), nullable=False),
        sa.Column("is_native", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTC_DATETIME, nullable=False),
        sa.Column("updated_at", UTC_DATETIME, nullable=False),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["chains.id"],
            name=op.f("fk_chain_assets_chain_id_chains"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chain_assets")),
        sa.UniqueConstraint("chain_id", "asset_key", name="uq_chain_assets_chain_id_asset_key"),
    )

    now = datetime.now(UTC).replace(tzinfo=None)  # naive UTC at rest
    op.bulk_insert(
        chains,
        [
            {
                "id": chain_id,
                "slug": slug,
                "name": name,
                "family": family,
                "native_symbol": symbol,
                "native_decimals": decimals,
                "enabled": True,
                "created_at": now,
                "updated_at": now,
            }
            for chain_id, slug, name, family, symbol, _native_name, decimals in SEED_CHAINS
        ],
    )
    op.bulk_insert(
        chain_assets,
        [
            {
                "chain_id": chain_id,
                "asset_key": "native",
                "symbol": symbol,
                "name": native_name,
                "decimals": decimals,
                "standard": "native",
                "is_native": True,
                "created_at": now,
                "updated_at": now,
            }
            for chain_id, _slug, _name, _family, symbol, native_name, decimals in SEED_CHAINS
        ],
    )


def downgrade() -> None:
    op.drop_table("chain_assets")
    op.drop_table("chains")
