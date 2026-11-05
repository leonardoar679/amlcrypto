"""TronGrid as a source of TRON address history, transaction details and the block height.

What was checked against the live API (2026-11-05) and is therefore assumed here:

* the address feeds /v1/accounts/{address}/transactions, /transactions/trc20 and
  /internal-transactions take `limit` (at most 200), `order_by=block_timestamp,asc`,
  `only_confirmed`, `min_timestamp`, `max_timestamp` (milliseconds) and `fingerprint`;
* a page's `meta.fingerprint` is the cursor of the next page, and is absent when there is none;
* the feeds are filtered by time, not by block, so a block range is turned into times by looking the
  two blocks up (/wallet/getblockbynum);
* /wallet/gettransactionbyid (with visible=true, for Base58 addresses) and
  /wallet/gettransactioninfobyid give a transaction's details; an unknown id answers `{}`;
* an invalid address is HTTP 400 with {"success": false, "error": "..."}.

The cursor is the fingerprint itself. It belongs to this source and to one query: the range of times
must be the same on every page of a read, which the fetcher guarantees.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, TypeVar

from app.chains.cursors import InvalidCursor
from app.chains.models import AccountPage, Page, Transfer, TransferType
from app.chains.tron.chains import TronChain
from app.chains.tron.normalize import (
    moment_ms,
    tron_internal_transfer,
    tron_native_transfers,
    tron_transaction,
    tron_trc20_transfer,
    txid_of,
)
from app.chains.values import MalformedRow, assign_occurrences, to_int
from app.providers.base import (
    ProviderAuthError,
    ProviderBadResponse,
    ProviderError,
    ProviderRateLimited,
)
from app.providers.budget import RequestBudget
from app.providers.client import ProviderClient
from app.providers.jsonrpc import snippet

T = TypeVar("T")

NAME = "trongrid"
MAX_PAGE_SIZE = 200
_FINGERPRINT = re.compile(r"^[A-Za-z0-9]{1,1000}$")


# --- errors ------------------------------------------------------------------------------------


def trongrid_body_error(provider: str, data: Any) -> ProviderError | None:
    """The error a TronGrid body amounts to, or None if the body is fine.

    The v1 feeds say {"success": false, "error": "..."}, the wallet API {"Error": "..."}.
    """
    if not isinstance(data, dict):
        return None
    if data.get("success") is False:
        return error_from_text(provider, data.get("error") or data.get("message"))
    if "Error" in data:
        return error_from_text(provider, data["Error"])
    return None


def error_from_text(provider: str, text: Any) -> ProviderError:
    text = str(text or "")
    lowered = text.lower()
    if "rate limit" in lowered or "too many requests" in lowered:
        return ProviderRateLimited(provider, "rate limit reached")
    if "api key" in lowered or "apikey" in lowered:
        return ProviderAuthError(provider, "the API key was rejected")
    if "valid account address" in lowered:
        return ProviderBadResponse(provider, "not a valid TRON address")
    return ProviderBadResponse(provider, f"TronGrid reported an error: {snippet(text)}")


# --- the source --------------------------------------------------------------------------------


class TronGridSource:
    name = NAME

    def __init__(self, client: ProviderClient, *, budget: RequestBudget | None = None) -> None:
        self._client = client
        self._budget = budget
        self._block_times: dict[int, int] = {}  # block number -> milliseconds

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> dict[str, Any]:
        response = await self._client.request(
            NAME,
            method,
            path,
            chain="tron",
            params=params,
            json=json,
            budget=self._budget,
            body_check=trongrid_body_error,
        )
        if not isinstance(response.data, dict):
            raise ProviderBadResponse(NAME, "unexpected response shape")
        return response.data

    def covers(self, chain: TronChain, kind: TransferType) -> bool:
        return True

    # --- the chain ---------------------------------------------------------------------------

    async def block_height(self, chain: TronChain) -> int:
        data = await self._call("POST", "/wallet/getnowblock", json={})
        try:
            return to_int(data["block_header"]["raw_data"]["number"])
        except (KeyError, TypeError, MalformedRow) as error:
            raise ProviderBadResponse(NAME, "unexpected response shape") from error

    async def _block_ms(self, number: int) -> int:
        """The time of a block in milliseconds, remembered."""
        if number not in self._block_times:
            data = await self._call("POST", "/wallet/getblockbynum", json={"num": number})
            try:
                self._block_times[number] = to_int(data["block_header"]["raw_data"]["timestamp"])
            except (KeyError, TypeError, MalformedRow) as error:
                raise ProviderBadResponse(NAME, f"block {number} is unknown") from error
        return self._block_times[number]

    # --- address history ---------------------------------------------------------------------

    async def account_page(
        self,
        chain: TronChain,
        address: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
        from_block: int | None = None,
        to_block: int | None = None,
    ) -> AccountPage:
        """Transactions of an address, and the TRX or TRC-10 token each one sent."""
        rows, next_cursor = await self._feed(
            "transactions", address, cursor, limit, from_block, to_block
        )
        # A row is read whole or not at all, and counted when not: a transaction kept without the
        # transfer it sent (say, a TRC-10 token whose id is unreadable) would be a gap nobody sees.
        read, skipped = _normalize(
            rows, lambda row: [(tron_transaction(row, chain), tron_native_transfers(row, chain))]
        )
        return AccountPage(
            transactions=[transaction for transaction, _ in read],
            native_transfers=[transfer for _, moved in read for transfer in moved],
            next_cursor=next_cursor,
            coverage=_skipped_note(skipped),
        )

    async def transfer_page(
        self,
        chain: TronChain,
        address: str,
        kind: TransferType,
        *,
        cursor: str | None = None,
        limit: int = 100,
        from_block: int | None = None,
        to_block: int | None = None,
    ) -> Page[Transfer]:
        """Internal TRX movements (INTERNAL) or TRC-20 token events (TRC20)."""
        if kind is TransferType.INTERNAL:
            path, build = (
                "internal-transactions",
                lambda row: _listed(tron_internal_transfer(row, chain)),
            )
        elif kind is TransferType.TRC20:
            path, build = "transactions/trc20", lambda row: _listed(tron_trc20_transfer(row, chain))
        else:
            raise ValueError("native transfers come with account_page")
        rows, next_cursor = await self._feed(path, address, cursor, limit, from_block, to_block)
        items, skipped = _normalize(rows, build)
        return Page(assign_occurrences(items), next_cursor, False, _skipped_note(skipped))

    async def _feed(
        self,
        path: str,
        address: str,
        cursor: str | None,
        limit: int,
        from_block: int | None,
        to_block: int | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {
            "limit": max(1, min(limit, MAX_PAGE_SIZE)),
            "order_by": "block_timestamp,asc",
            "only_confirmed": "true",
        }
        if from_block:
            params["min_timestamp"] = await self._block_ms(from_block)
        if to_block is not None:
            params["max_timestamp"] = await self._block_ms(to_block)
        if cursor:
            if not _FINGERPRINT.match(cursor):
                raise InvalidCursor("not a TronGrid cursor")
            params["fingerprint"] = cursor

        data = await self._call("GET", f"/v1/accounts/{address}/{path}", params=params)
        rows = data.get("data")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ProviderBadResponse(NAME, "unexpected response shape")
        fingerprint = (data.get("meta") or {}).get("fingerprint")
        # No rows means no more, whatever the fingerprint says: it cannot loop forever.
        next_cursor = fingerprint if rows and isinstance(fingerprint, str) and fingerprint else None
        return rows, next_cursor

    # --- one transaction ---------------------------------------------------------------------

    async def transaction(
        self, chain: TronChain, tx_hash: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, datetime | None]:
        """The transaction, its details (fee, logs, internal movements) and its block time."""
        tx = await self._call(
            "POST", "/wallet/gettransactionbyid", json={"value": tx_hash, "visible": True}
        )
        if not tx:
            return None, None, None  # an unknown id answers {}
        info = await self._call("POST", "/wallet/gettransactioninfobyid", json={"value": tx_hash})
        if "blockNumber" not in info:
            return tx, None, None  # known, but not in a block yet
        try:
            when = moment_ms(info["blockTimeStamp"])
        except (KeyError, MalformedRow) as error:
            raise ProviderBadResponse(NAME, "unexpected response shape") from error
        return tx, info, when


# --- helpers -----------------------------------------------------------------------------------


def _listed(item: T | None) -> list[T]:
    return [] if item is None else [item]


def _normalize(rows: list[dict[str, Any]], build) -> tuple[list[Any], int]:
    """Build records from each row. A row that cannot be read is skipped and counted."""
    items: list[Any] = []
    skipped = 0
    for row in rows:
        try:
            items.extend(build(row))
        except MalformedRow:
            skipped += 1
    return items, skipped


def _skipped_note(skipped: int) -> tuple[str, ...]:
    if not skipped:
        return ()
    return (f"{skipped} rows from trongrid could not be read and were skipped",)


__all__ = ["TronGridSource", "trongrid_body_error", "txid_of"]
