"""A second opinion from Moov Watchman, never the only evidence.

Watchman (Apache-2.0, https://github.com/moov-io/watchman) screens names and identifiers against
sanctions lists over an HTTP API. Its service is unauthenticated by design, so it must only be
reachable from this backend (the compose file puts it on the internal network with no published
port), and this client is the only thing that talks to it.

Written from Watchman's published API (docs/api.yaml, v2) and NOT yet run against a live Watchman,
which needs Docker, absent from the development machine: `GET /v2/search` with a `cryptoAddress`
parameter in the form CURRENCY:ADDRESS, answering `{"entities": [...]}` where an entity carries
`name`, `entityType`, `sourceList`, `sourceID`, `match`, `cryptoAddresses` and
`sanctionsInfo.programs`. The docs do not say whether the crypto match is exact or fuzzy, so a hit
only counts if one of the entity's own listed addresses really is the address asked about.

The platform's own table of OFAC claims remains the authority for wallet addresses: Watchman is a
check that an independent implementation reading the same list agrees, and the way to bring in
name screening and further lists later.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx2

from app.chains.registry import ChainSpec
from app.intel.model import WatchmanCheck

# The currencies OFAC lists a key of each family under; a TRON or EVM key turns up under several.
CURRENCIES: dict[str, tuple[str, ...]] = {
    "bitcoin": ("XBT", "USDT"),
    "ethereum": ("ETH", "USDT", "USDC", "ETC", "ARB", "BSC", "BNB"),
    "bsc": ("ETH", "USDT", "USDC", "ETC", "ARB", "BSC", "BNB"),
    "tron": ("TRX", "USDT", "XBT"),
    "solana": ("SOL",),
}
MAX_HITS = 10


class WatchmanClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 5.0,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx2.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def list_info(self) -> dict[str, Any]:
        """Watchman's own view of its lists (`/v2/listinfo`): counts per list and when it loaded."""
        response = await self._http.get("/v2/listinfo")
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("unexpected /v2/listinfo shape")
        return body

    async def _search(self, currency: str, address: str) -> list[dict[str, Any]]:
        response = await self._http.get(
            "/v2/search", params={"cryptoAddress": f"{currency}:{address}", "limit": MAX_HITS}
        )
        response.raise_for_status()
        body = response.json()
        entities = body.get("entities") if isinstance(body, dict) else None
        if not isinstance(entities, list):
            raise ValueError("unexpected /v2/search shape")
        return [entity for entity in entities if isinstance(entity, dict)]

    async def check(self, chain: ChainSpec, address: str) -> WatchmanCheck:
        """Ask Watchman about an address under each currency it may be listed as.

        Never raises: Watchman being down must not stop a lookup, only be said.
        """
        currencies = CURRENCIES.get(chain.slug, ())
        try:
            answers = await asyncio.gather(*(self._search(c, address) for c in currencies))
        except (httpx2.HTTPError, ValueError) as error:
            return WatchmanCheck(available=False, error=f"{type(error).__name__}: {error}"[:200])

        wanted = address.lower() if chain.family.value == "evm" else address
        hits: dict[tuple[str, str], dict[str, Any]] = {}
        for entities in answers:
            for entity in entities:
                listed = [
                    item
                    for item in entity.get("cryptoAddresses") or []
                    if isinstance(item, dict) and _same(item.get("address"), wanted, chain)
                ]
                if not listed:
                    continue  # a fuzzy or unrelated result: not about this address
                key = (str(entity.get("sourceList")), str(entity.get("sourceID")))
                hits.setdefault(
                    key,
                    {
                        "name": entity.get("name"),
                        "entity_type": entity.get("entityType"),
                        "source_list": entity.get("sourceList"),
                        "source_id": entity.get("sourceID"),
                        "match": entity.get("match"),
                        "programs": (entity.get("sanctionsInfo") or {}).get("programs") or [],
                        "currencies": sorted({str(item.get("currency")) for item in listed}),
                    },
                )
        return WatchmanCheck(available=True, hits=tuple(hits.values()))


def _same(candidate: Any, wanted: str, chain: ChainSpec) -> bool:
    if not isinstance(candidate, str):
        return False
    return candidate.lower() == wanted if chain.family.value == "evm" else candidate == wanted
