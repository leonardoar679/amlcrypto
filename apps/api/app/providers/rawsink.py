"""Where a source offers the raw answers it decodes, and what kinds of answer there are.

A source only offers; whether an answer is worth keeping is decided on the other side
(app.ingestion.raw), so the providers know nothing about storage.
"""

from __future__ import annotations

from typing import Any, Protocol

SOLANA_TRANSACTION = "solana_transaction"
BITCOIN_TRANSACTION = "bitcoin_transaction"


class RawSink(Protocol):
    def offer(
        self, chain_id: str, kind: str, record_key: str, provider: str, body: Any
    ) -> None: ...
