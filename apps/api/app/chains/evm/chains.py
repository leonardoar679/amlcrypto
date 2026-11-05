"""The EVM chains the platform reads, and the per-chain facts the connectors depend on."""

from __future__ import annotations

from dataclasses import dataclass

from app.chains.models import Asset
from app.chains.registry import get_chain


@dataclass(frozen=True, slots=True)
class EvmChain:
    chain_id: str  # CAIP-2, e.g. "eip155:1"
    numeric_id: int  # what Etherscan calls chainid
    slug: str
    native: Asset
    # Policy numbers, not protocol finality: how deep a block must be before we call what is in
    # it confirmed, and how far back an incremental sync starts again to absorb reorganisations.
    confirmation_depth: int
    reorg_margin: int
    # Alchemy's Transfers API: whether the `internal` category exists here, and whether
    # `withMetadata` returns block timestamps (otherwise they are looked up per block).
    alchemy_internal: bool
    alchemy_timestamps: bool

    def incremental_start(self, synced_through_block: int) -> int:
        """Where to start an update after a complete read through `synced_through_block`.

        A little before it, so that blocks which were reorganised away since are read again. The
        overlap is harmless: a movement has the same identity every time it is read.
        """
        return max(0, synced_through_block - self.reorg_margin)


EVM_CHAINS: dict[str, EvmChain] = {
    chain.chain_id: chain
    for chain in (
        EvmChain(
            chain_id="eip155:1",
            numeric_id=1,
            slug="ethereum",
            native=Asset(symbol="ETH", contract_address=None, decimals=18, name="Ether"),
            confirmation_depth=12,
            reorg_margin=64,  # two epochs
            alchemy_internal=True,
            alchemy_timestamps=True,
        ),
        EvmChain(
            chain_id="eip155:56",
            numeric_id=56,
            slug="bsc",
            native=Asset(symbol="BNB", contract_address=None, decimals=18, name="BNB"),
            confirmation_depth=15,
            reorg_margin=30,
            alchemy_internal=False,
            alchemy_timestamps=False,
        ),
    )
}


def get_evm_chain(key: str) -> EvmChain | None:
    """Find an EVM chain by slug or CAIP-2 id; None if it is unknown or not an EVM chain."""
    spec = get_chain(key)
    return EVM_CHAINS.get(spec.id) if spec is not None else None
