"""Keeping a provider's raw answer for a transaction, selectively.

The platform stores what it decoded, and decodes Solana and Bitcoin transactions itself. If a
decoder is later found to be wrong (they have been, and fixed), the raw answer is what it can be
run on again without asking the provider a second time. So the answer is kept for exactly those
kinds, never for the indexer rows of the account chains (the normalized record holds every field of
those), and never when it is unreasonably large. The answer is the response body; no request, URL
or key is ever part of it.
"""

from __future__ import annotations

import hashlib
import json
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.database.repositories.acquisition import RawItem
from app.providers.rawsink import BITCOIN_TRANSACTION, SOLANA_TRANSACTION

if TYPE_CHECKING:
    from app.core.config import Settings

# What is worth keeping: the answers this platform decodes itself.
KEPT_KINDS = frozenset({SOLANA_TRANSACTION, BITCOIN_TRANSACTION})
COMPRESSION_LEVEL = 6


@dataclass(frozen=True, slots=True)
class RawPolicy:
    kinds: frozenset[str] = KEPT_KINDS
    # A larger answer (uncompressed) is not kept; the decoded record is.
    max_bytes: int = 262_144


class RawCollector:
    """Takes the answers a read offers and holds those the policy keeps until they are stored."""

    def __init__(
        self,
        policy: RawPolicy | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.policy = policy or RawPolicy()
        self._clock = clock
        self._items: dict[tuple[str, str, str, str], RawItem] = {}
        self.too_large = 0

    @classmethod
    def from_settings(cls, settings: Settings) -> RawCollector | None:
        if not settings.store_raw_responses:
            return None
        return cls(RawPolicy(max_bytes=settings.raw_max_bytes))

    def offer(self, chain_id: str, kind: str, record_key: str, provider: str, body: Any) -> None:
        if kind not in self.policy.kinds:
            return
        encoded = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(encoded) > self.policy.max_bytes:
            self.too_large += 1
            return
        self._items[(chain_id, kind, record_key, provider)] = RawItem(
            chain_id=chain_id,
            kind=kind,
            record_key=record_key,
            provider=provider,
            payload=zlib.compress(encoded, COMPRESSION_LEVEL),
            size_bytes=len(encoded),
            sha256=hashlib.sha256(encoded).hexdigest(),
            fetched_at=self._clock(),
        )

    def drain(self) -> list[RawItem]:
        """The answers held so far, which are then forgotten (a failed write puts them back)."""
        items = list(self._items.values())
        self._items.clear()
        return items

    def restore(self, items: list[RawItem]) -> None:
        for item in items:
            self._items.setdefault((item.chain_id, item.kind, item.record_key, item.provider), item)

    def __len__(self) -> int:
        return len(self._items)


def unpack(item: RawItem) -> Any:
    """The answer as the provider gave it, checked against the hash taken when it was kept."""
    encoded = zlib.decompress(item.payload)
    if hashlib.sha256(encoded).hexdigest() != item.sha256:
        raise ValueError("the stored answer does not match its hash")
    return json.loads(encoded)
