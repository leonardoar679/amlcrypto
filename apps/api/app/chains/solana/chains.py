"""Solana: the one chain of this family, and the facts the connector depends on."""

from __future__ import annotations

from dataclasses import dataclass

from app.chains.models import Asset
from app.chains.registry import get_chain


@dataclass(frozen=True, slots=True)
class SolanaChain:
    chain_id: str  # CAIP-2
    slug: str
    native: Asset
    # Everything is read at the `finalized` commitment, which cannot be rolled back, so there is
    # nothing to re-read after a reorganisation.
    reorg_margin: int

    def incremental_start(self, synced_through_slot: int) -> int:
        """Where to start an update after a complete read through `synced_through_slot`."""
        return max(0, synced_through_slot - self.reorg_margin)


_SPEC = get_chain("solana")
assert _SPEC is not None  # the registry always has Solana

SOLANA_CHAIN = SolanaChain(
    chain_id=_SPEC.id,
    slug=_SPEC.slug,
    native=Asset(symbol="SOL", contract_address=None, decimals=9, name="Solana"),
    reorg_margin=0,
)


def get_solana_chain(key: str) -> SolanaChain | None:
    """The Solana chain by slug or CAIP-2 id; None for anything else."""
    spec = get_chain(key)
    return SOLANA_CHAIN if spec is not None and spec.id == SOLANA_CHAIN.chain_id else None
