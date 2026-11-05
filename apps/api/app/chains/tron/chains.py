"""TRON: the one chain this family has, and the facts the connector depends on."""

from __future__ import annotations

from dataclasses import dataclass

from app.chains.models import Asset
from app.chains.registry import get_chain


@dataclass(frozen=True, slots=True)
class TronChain:
    chain_id: str  # CAIP-2
    slug: str
    native: Asset
    # TRON calls a block solidified (final) after 19 more blocks: about a minute. History is read
    # with only_confirmed, so what comes back is final, and the newest ~20 blocks are left out.
    confirmation_depth: int
    # So an update starts back far enough to pick up that unsolidified tail (and then some).
    reorg_margin: int

    def incremental_start(self, synced_through_block: int) -> int:
        """Where to start an update after a complete read through `synced_through_block`."""
        return max(0, synced_through_block - self.reorg_margin)


_SPEC = get_chain("tron")
assert _SPEC is not None  # the registry always has TRON

TRON_CHAIN = TronChain(
    chain_id=_SPEC.id,
    slug=_SPEC.slug,
    native=Asset(symbol="TRX", contract_address=None, decimals=6, name="TRON"),
    confirmation_depth=20,
    reorg_margin=60,
)


def get_tron_chain(key: str) -> TronChain | None:
    """The TRON chain by slug or CAIP-2 id; None for anything else."""
    spec = get_chain(key)
    return TRON_CHAIN if spec is not None and spec.id == TRON_CHAIN.chain_id else None
