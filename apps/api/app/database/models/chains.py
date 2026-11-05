"""Chain registry tables. Seeded by migration 0002 and kept equal to app.chains.registry."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IntegerPK, TimestampMixin, identifier


class Chain(TimestampMixin, Base):
    __tablename__ = "chains"

    # CAIP-2 chain id, e.g. "eip155:1".
    id: Mapped[str] = mapped_column(identifier(64), primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    family: Mapped[str] = mapped_column(String(16), nullable=False)  # evm | utxo | tron | solana
    native_symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    native_decimals: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ChainAsset(TimestampMixin, Base):
    __tablename__ = "chain_assets"
    __table_args__ = (
        UniqueConstraint("chain_id", "asset_key", name="uq_chain_assets_chain_id_asset_key"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    chain_id: Mapped[str] = mapped_column(
        identifier(64), ForeignKey("chains.id", ondelete="CASCADE"), nullable=False
    )
    # "native", or the canonical contract / mint address of a token.
    asset_key: Mapped[str] = mapped_column(identifier(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    decimals: Mapped[int] = mapped_column(Integer, nullable=False)
    standard: Mapped[str] = mapped_column(String(16), nullable=False)  # native | erc20 | spl ...
    is_native: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
