"""Bitcoin: the one chain of this family, and the facts the connector depends on."""

from __future__ import annotations

from dataclasses import dataclass

from app.chains.models import Asset
from app.chains.registry import get_chain


@dataclass(frozen=True, slots=True)
class BitcoinChain:
    chain_id: str  # CAIP-2
    slug: str
    native: Asset
    # Six blocks is the conventional point after which a payment is treated as final.
    confirmation_depth: int
    # Reorganisations deeper than a few blocks have not happened in practice; twice the depth is a
    # cheap margin, since re-reading a block's transactions costs one page.
    reorg_margin: int

    def incremental_start(self, synced_through_block: int) -> int:
        """Where to start an update after a complete read through `synced_through_block`."""
        return max(0, synced_through_block - self.reorg_margin)


_SPEC = get_chain("bitcoin")
assert _SPEC is not None  # the registry always has Bitcoin

BITCOIN_CHAIN = BitcoinChain(
    chain_id=_SPEC.id,
    slug=_SPEC.slug,
    native=Asset(symbol="BTC", contract_address=None, decimals=8, name="Bitcoin"),
    confirmation_depth=6,
    reorg_margin=12,
)


def get_bitcoin_chain(key: str) -> BitcoinChain | None:
    """The Bitcoin chain by slug or CAIP-2 id; None for anything else."""
    spec = get_chain(key)
    return BITCOIN_CHAIN if spec is not None and spec.id == BITCOIN_CHAIN.chain_id else None
