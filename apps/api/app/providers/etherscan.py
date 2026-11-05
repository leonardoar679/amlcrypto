"""Etherscan API V2 as a source of address history, receipts and the block height.

What the documentation says, and what this code therefore assumes (checked 2026-11-05):

* one key serves every chain; the chain is the `chainid` parameter, one per request;
* `txlist`, `txlistinternal` and `tokentx` take `startblock`, `endblock`, `page`, `offset` and
  `sort`. No maximum for `offset` or `page * offset` is documented, and the default order is not
  stated, so every request says `sort=asc` and the classic 10,000-row window is respected anyway;
* `tokentx` rows carry no log index (see app.chains.evm.normalize for how that is handled);
* errors come back as HTTP 200 with {"status": "0", "message": "NOTOK", "result": "<cause>"}, and
  an empty history as status "0" with an empty list.

Reading a long history
----------------------
The cursor is "<block>:<skip>": read from that block on, and ignore the first <skip> rows, which an
earlier page delivered. Inside the window the position simply advances. When the next page would
cross the window, the start moves to the last block read, which is what the skip count is for.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from app.chains.evm.chains import EvmChain
from app.chains.evm.cursors import decode_block_cursor, encode_block_cursor
from app.chains.evm.normalize import (
    MalformedRow,
    assign_occurrences,
    etherscan_internal_transfer,
    etherscan_native_transfer,
    etherscan_token_transfer,
    etherscan_transaction,
    to_int,
)
from app.chains.models import AccountPage, Page, Transfer, TransferType
from app.providers.base import (
    ProviderAuthError,
    ProviderBadResponse,
    ProviderError,
    ProviderRateLimited,
    ProviderUnavailable,
)
from app.providers.budget import RequestBudget
from app.providers.client import ProviderClient
from app.providers.jsonrpc import jsonrpc_error, snippet

T = TypeVar("T")

NAME = "etherscan"
LATEST_BLOCK = 999_999_999
WINDOW_ROWS = 10_000
MAX_PAGE_SIZE = 1000

_RESET = re.compile(r"resets\s+(\d{4}-\d{2}-\d{2})[ t](\d{2}:\d{2}:\d{2})", re.IGNORECASE)


# --- errors that arrive inside a 200 -----------------------------------------------------------


def etherscan_body_error(provider: str, data: Any) -> ProviderError | None:
    """The error an Etherscan response body amounts to, or None if the body is fine."""
    if not isinstance(data, dict):
        return ProviderBadResponse(provider, "the response is not a JSON object")
    if "jsonrpc" in data:  # the proxy module answers in JSON-RPC
        return jsonrpc_error(provider, data["error"]) if data.get("error") is not None else None
    if "status" not in data or str(data.get("status")) == "1":
        return None
    result = data.get("result")
    if isinstance(result, list):
        return None  # status "0" with an empty list: no transactions found
    return error_from_text(provider, result if isinstance(result, str) else data.get("message"))


def error_from_text(provider: str, text: Any, now: datetime | None = None) -> ProviderError:
    text = str(text or "")
    lowered = text.lower()
    if "community free api limit reached" in lowered:
        return ProviderRateLimited(
            provider,
            "the shared free API allowance is used up",
            retry_after=seconds_until_reset(text, now),
        )
    if "rate limit" in lowered:  # "Max rate limit reached, please use API Key for higher ..."
        return ProviderRateLimited(provider, "rate limit reached")
    if "too many invalid api key attempts" in lowered:
        return ProviderRateLimited(provider, "too many invalid key attempts", retry_after=30.0)
    if "invalid api key" in lowered:
        return ProviderAuthError(provider, "the API key was rejected")
    if "free api access is not supported for this chain" in lowered:
        return ProviderAuthError(provider, "the plan does not include this chain")
    if "unsupported chainid" in lowered:
        return ProviderBadResponse(provider, "the chain is not supported")
    if "timeout" in lowered or "too busy" in lowered:
        return ProviderUnavailable(provider, "the query timed out")
    if "missing or invalid action" in lowered:
        return ProviderBadResponse(provider, "the request was refused")
    return ProviderBadResponse(provider, f"the provider reported an error: {snippet(text)}")


def seconds_until_reset(text: str, now: datetime | None = None) -> float:
    """From "... Resets 2026-06-24 12:00:00 UTC ...": seconds to wait, within sane bounds."""
    match = _RESET.search(text)
    if match is None:
        return 3600.0
    reset = datetime.strptime(f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M:%S")
    wait = (reset.replace(tzinfo=UTC) - (now or datetime.now(UTC))).total_seconds()
    return min(max(wait, 60.0), 7 * 24 * 3600.0)


# --- the source --------------------------------------------------------------------------------


class EtherscanSource:
    name = NAME

    def __init__(
        self,
        client: ProviderClient,
        *,
        budget: RequestBudget | None = None,
        window: int = WINDOW_ROWS,
    ) -> None:
        self._client = client
        self._budget = budget
        self._window = window  # rows one query is assumed to be able to reach

    async def _call(self, chain: EvmChain, **params: Any) -> dict[str, Any]:
        response = await self._client.request(
            NAME,
            "GET",
            chain=chain.chain_id,
            params={"chainid": str(chain.numeric_id), **params},
            budget=self._budget,
            body_check=etherscan_body_error,
        )
        if not isinstance(response.data, dict):
            raise ProviderBadResponse(NAME, "unexpected response shape")
        return response.data

    # --- the chain ---------------------------------------------------------------------------

    async def block_height(self, chain: EvmChain) -> int:
        data = await self._call(chain, module="proxy", action="eth_blockNumber")
        return _number(data.get("result"))

    def covers(self, chain: EvmChain, kind: TransferType) -> bool:
        """Whether this source has the data at all. (Whether the plan allows it is found out
        when asking: an unsupported chain answers with an authorisation error.)"""
        return True

    # --- address history ---------------------------------------------------------------------

    async def transfer_page(
        self,
        chain: EvmChain,
        address: str,
        kind: TransferType,
        *,
        cursor: str | None = None,
        limit: int = 100,
        from_block: int | None = None,
        to_block: int | None = None,
    ) -> Page[Transfer]:
        """Internal or token transfers. (The coin sent by transactions comes with account_page.)"""
        options = {"cursor": cursor, "limit": limit, "from_block": from_block, "to_block": to_block}
        if kind is TransferType.INTERNAL:
            return await self.internal_page(chain, address, **options)
        if kind is TransferType.ERC20:
            return await self.token_page(chain, address, **options)
        raise ValueError("native transfers come with account_page")

    async def account_page(
        self,
        chain: EvmChain,
        address: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
        from_block: int | None = None,
        to_block: int | None = None,
    ) -> AccountPage:
        """Normal transactions of an address, and the coin each one sent."""
        rows, next_cursor, truncated = await self._rows(
            chain, "txlist", address, cursor, limit, from_block, to_block
        )
        transactions, skipped = _normalize(rows, lambda row: etherscan_transaction(row, chain))
        transfers, _ = _normalize(rows, lambda row: etherscan_native_transfer(row, chain))
        return AccountPage(
            transactions=transactions,
            native_transfers=transfers,
            next_cursor=next_cursor,
            truncated=truncated,
            coverage=_skipped_note(skipped) + _truncated_note(truncated),
        )

    async def internal_page(
        self,
        chain: EvmChain,
        address: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
        from_block: int | None = None,
        to_block: int | None = None,
    ) -> Page[Transfer]:
        rows, next_cursor, truncated = await self._rows(
            chain, "txlistinternal", address, cursor, limit, from_block, to_block
        )
        items, skipped = _normalize(rows, lambda row: etherscan_internal_transfer(row, chain))
        return Page(
            assign_occurrences(items),
            next_cursor,
            truncated,
            _skipped_note(skipped) + _truncated_note(truncated),
        )

    async def token_page(
        self,
        chain: EvmChain,
        address: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
        from_block: int | None = None,
        to_block: int | None = None,
    ) -> Page[Transfer]:
        rows, next_cursor, truncated = await self._rows(
            chain, "tokentx", address, cursor, limit, from_block, to_block
        )
        items, skipped = _normalize(rows, lambda row: etherscan_token_transfer(row, chain))
        return Page(
            assign_occurrences(items),
            next_cursor,
            truncated,
            _skipped_note(skipped) + _truncated_note(truncated),
        )

    async def _rows(
        self,
        chain: EvmChain,
        action: str,
        address: str,
        cursor: str | None,
        limit: int,
        from_block: int | None,
        to_block: int | None,
    ) -> tuple[list[dict[str, Any]], str | None, bool]:
        """One page of raw rows not yet delivered, the cursor after it, and whether it was cut."""
        limit = max(1, min(limit, MAX_PAGE_SIZE, self._window))
        start, skip = decode_block_cursor(cursor) if cursor else (from_block or 0, 0)
        page = skip // limit + 1
        data = await self._call(
            chain,
            module="account",
            action=action,
            address=address,
            startblock=start,
            endblock=LATEST_BLOCK if to_block is None else to_block,
            page=page,
            offset=limit,
            sort="asc",
        )
        rows = data.get("result")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ProviderBadResponse(NAME, "unexpected response shape")

        first_index = (page - 1) * limit  # the position, within this query, of rows[0]
        fresh = rows[max(0, skip - first_index) :]
        if len(rows) < limit:
            return fresh, None, False  # a short page is the last one

        read = first_index + len(rows)
        if (read // limit + 1) * limit <= self._window:
            return fresh, encode_block_cursor(start, read), False

        # The next page would cross the window: move the start to the last block read.
        try:
            first_block, last_block = (
                to_int(rows[0]["blockNumber"]),
                to_int(rows[-1]["blockNumber"]),
            )
        except (KeyError, MalformedRow) as error:
            raise ProviderBadResponse(NAME, "rows without a block number") from error
        if first_block == last_block:
            return fresh, None, True  # one block alone holds more than the window shows
        in_last_block = sum(1 for row in rows if to_int(row["blockNumber"]) == last_block)
        return fresh, encode_block_cursor(last_block, in_last_block), False

    # --- one transaction ---------------------------------------------------------------------

    async def transaction(
        self, chain: EvmChain, tx_hash: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, datetime | None]:
        """The node's transaction, its receipt and its block time; None parts when not mined."""
        tx = (
            await self._call(
                chain, module="proxy", action="eth_getTransactionByHash", txhash=tx_hash
            )
        ).get("result")
        if not isinstance(tx, dict):
            return None, None, None
        receipt = (
            await self._call(
                chain, module="proxy", action="eth_getTransactionReceipt", txhash=tx_hash
            )
        ).get("result")
        if not isinstance(receipt, dict) or receipt.get("blockNumber") is None:
            return tx, None, None
        stamp = tx.get("blockTimestamp")
        if stamp is None:
            block = (
                await self._call(
                    chain,
                    module="proxy",
                    action="eth_getBlockByNumber",
                    tag=receipt["blockNumber"],
                    boolean="false",
                )
            ).get("result")
            stamp = block.get("timestamp") if isinstance(block, dict) else None
        return tx, receipt, None if stamp is None else _moment(stamp)


# --- helpers -----------------------------------------------------------------------------------


def _number(value: Any) -> int:
    try:
        return to_int(value)
    except MalformedRow as error:
        raise ProviderBadResponse(NAME, "unexpected response shape") from error


def _moment(seconds: Any) -> datetime:
    try:
        return datetime.fromtimestamp(to_int(seconds), UTC)
    except (MalformedRow, OverflowError, OSError, ValueError) as error:
        raise ProviderBadResponse(NAME, "unexpected timestamp") from error


def _normalize(
    rows: list[dict[str, Any]], build: Callable[[dict[str, Any]], T | None]
) -> tuple[list[T], int]:
    """Build a record from each row. A row that cannot be read is skipped and counted."""
    items: list[T] = []
    skipped = 0
    for row in rows:
        try:
            item = build(row)
        except MalformedRow:
            skipped += 1
            continue
        if item is not None:
            items.append(item)
    return items, skipped


def _skipped_note(skipped: int) -> tuple[str, ...]:
    if not skipped:
        return ()
    return (f"{skipped} rows from etherscan could not be read and were skipped",)


def _truncated_note(truncated: bool) -> tuple[str, ...]:
    if not truncated:
        return ()
    return ("etherscan would not show more rows from one block than its result window holds",)


__all__ = ["EtherscanSource", "etherscan_body_error"]
