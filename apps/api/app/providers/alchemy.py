"""Alchemy as a source of address history, receipts, block heights and block times.

What the documentation says, and what this code therefore assumes (checked 2026-11-05):

* `alchemy_getAssetTransfers` is in the Free plan at 120 compute units a call. BNB Smart Chain's
  overview lists the Transfers API as supported, but which categories work there is not stated;
* `internal` exists only on Ethereum, Polygon, Base and Arc, and `withMetadata` (which carries
  block timestamps) only on Ethereum, Base, Polygon, Arbitrum and Optimism. Elsewhere a timestamp
  costs a block lookup, done in batches here;
* whether one request returns transfers from an address, to it, or both is not stated, so outgoing
  and incoming are two passes over the same query, told apart by the cursor ("out:<pageKey>",
  then "in:<pageKey>"). A transfer from an address to itself is therefore listed twice; the
  identity of a transfer makes that harmless to anything that deduplicates;
* only `<hash>:external` is documented as a `uniqueId`; the log position of a token transfer is
  read from `<hash>:log:<n>` when the id has that form, and is otherwise left unknown.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.chains.evm.chains import EvmChain
from app.chains.evm.cursors import InvalidCursor
from app.chains.evm.normalize import (
    MalformedRow,
    alchemy_transfer,
    assign_occurrences,
    to_int,
)
from app.chains.models import AccountPage, Page, Transaction, Transfer, TransferType, TxStatus
from app.providers.base import ProviderBadResponse
from app.providers.budget import RequestBudget
from app.providers.client import ProviderClient
from app.providers.jsonrpc import jsonrpc_body_error

NAME = "alchemy"
MAX_COUNT = 1000
BLOCKS_PER_BATCH = 50

_CATEGORY = {
    TransferType.NATIVE: "external",
    TransferType.INTERNAL: "internal",
    TransferType.ERC20: "erc20",
}
_CURSOR = re.compile(r"^(out|in):([A-Za-z0-9._~+/=-]{0,300})$")


def decode_cursor(cursor: str | None) -> tuple[str, str | None]:
    if not cursor:
        return "out", None
    match = _CURSOR.match(cursor)
    if match is None:
        raise InvalidCursor("not an alchemy cursor")
    return match.group(1), match.group(2) or None


class AlchemySource:
    name = NAME

    def __init__(self, client: ProviderClient, *, budget: RequestBudget | None = None) -> None:
        self._client = client
        self._budget = budget
        self._block_times: dict[tuple[str, int], datetime | None] = {}

    # --- JSON-RPC ----------------------------------------------------------------------------

    async def _post(self, chain: EvmChain, payload: Any) -> Any:
        response = await self._client.request(
            NAME,
            "POST",
            chain=chain.chain_id,
            json=payload,
            budget=self._budget,
            body_check=jsonrpc_body_error,
        )
        return response.data

    async def _rpc(self, chain: EvmChain, method: str, params: list[Any]) -> Any:
        data = await self._post(
            chain, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        )
        if not isinstance(data, dict) or "result" not in data:
            raise ProviderBadResponse(NAME, "unexpected response shape")
        return data["result"]

    async def _batch(self, chain: EvmChain, calls: list[tuple[str, list[Any]]]) -> list[Any]:
        """Several calls in one request. Results in the order asked; None where one is missing."""
        payload = [
            {"jsonrpc": "2.0", "id": index, "method": method, "params": params}
            for index, (method, params) in enumerate(calls)
        ]
        data = await self._post(chain, payload)
        if not isinstance(data, list):
            raise ProviderBadResponse(NAME, "unexpected response shape")
        answers = {item.get("id"): item.get("result") for item in data if isinstance(item, dict)}
        return [answers.get(index) for index in range(len(calls))]

    # --- the chain ---------------------------------------------------------------------------

    async def block_height(self, chain: EvmChain) -> int:
        try:
            return to_int(await self._rpc(chain, "eth_blockNumber", []))
        except MalformedRow as error:
            raise ProviderBadResponse(NAME, "unexpected response shape") from error

    def covers(self, chain: EvmChain, kind: TransferType) -> bool:
        return kind is not TransferType.INTERNAL or chain.alchemy_internal

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
        if not self.covers(chain, kind):
            return Page(
                [],
                None,
                coverage=(f"alchemy has no internal transfers on {chain.slug}",),
                unavailable=True,
            )

        direction, page_key = decode_cursor(cursor)
        request: dict[str, Any] = {
            "fromBlock": hex(from_block or 0),
            "toBlock": "latest" if to_block is None else hex(to_block),
            "category": [_CATEGORY[kind]],
            "order": "asc",
            "maxCount": hex(max(1, min(limit, MAX_COUNT))),
            "excludeZeroValue": True,
            "withMetadata": chain.alchemy_timestamps,
            "fromAddress" if direction == "out" else "toAddress": address,
        }
        if page_key:
            request["pageKey"] = page_key

        result = await self._rpc(chain, "alchemy_getAssetTransfers", [request])
        rows = result.get("transfers") if isinstance(result, dict) else None
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ProviderBadResponse(NAME, "unexpected response shape")

        times = await self._times(chain, rows)
        items: list[Transfer] = []
        skipped = 0
        for row in rows:
            try:
                transfer = alchemy_transfer(row, chain, times.get(to_int(row.get("blockNum"))))
            except MalformedRow:
                skipped += 1
                continue
            if transfer is not None:
                items.append(transfer)

        next_key = result.get("pageKey")
        if next_key:
            next_cursor: str | None = f"{direction}:{next_key}"
        else:
            next_cursor = "in:" if direction == "out" else None  # outgoing done; now incoming
        note = (
            (f"{skipped} rows from alchemy could not be read and were skipped",) if skipped else ()
        )
        return Page(assign_occurrences(items), next_cursor, False, note)

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
        """The coin sent, and the transactions that sent it. Alchemy cannot list the others."""
        page = await self.transfer_page(
            chain,
            address,
            TransferType.NATIVE,
            cursor=cursor,
            limit=limit,
            from_block=from_block,
            to_block=to_block,
        )
        return AccountPage(
            transactions=[_as_transaction(transfer) for transfer in page.items],
            native_transfers=page.items,
            next_cursor=page.next_cursor,
            truncated=page.truncated,
            coverage=(*page.coverage, "alchemy lists only transactions that carried value"),
        )

    # --- block times -------------------------------------------------------------------------

    async def _times(self, chain: EvmChain, rows: list[dict[str, Any]]) -> dict[int, datetime]:
        """Block number to time for the rows: from their metadata, or else looked up."""
        times: dict[int, datetime] = {}
        missing: set[int] = set()
        for row in rows:
            try:
                block = to_int(row.get("blockNum"))
            except MalformedRow:
                continue
            stamp = _metadata_time((row.get("metadata") or {}).get("blockTimestamp"))
            if stamp is not None:
                times[block] = stamp
            else:
                missing.add(block)
        times.update(await self.block_times(chain, missing - set(times)))
        return times

    async def block_times(self, chain: EvmChain, blocks: set[int]) -> dict[int, datetime]:
        """The time of each block, in batches, remembering what was asked before."""
        wanted = sorted(
            block for block in blocks if (chain.chain_id, block) not in self._block_times
        )
        for start in range(0, len(wanted), BLOCKS_PER_BATCH):
            chunk = wanted[start : start + BLOCKS_PER_BATCH]
            answers = await self._batch(
                chain, [("eth_getBlockByNumber", [hex(block), False]) for block in chunk]
            )
            for block, answer in zip(chunk, answers, strict=True):
                stamp = answer.get("timestamp") if isinstance(answer, dict) else None
                self._block_times[(chain.chain_id, block)] = _from_seconds(stamp)
        return {
            block: stamp
            for block in blocks
            if (stamp := self._block_times.get((chain.chain_id, block))) is not None
        }

    # --- one transaction ---------------------------------------------------------------------

    async def transaction(
        self, chain: EvmChain, tx_hash: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, datetime | None]:
        """The node's transaction, its receipt and its block time; None parts when not mined."""
        tx, receipt = await self._batch(
            chain,
            [("eth_getTransactionByHash", [tx_hash]), ("eth_getTransactionReceipt", [tx_hash])],
        )
        if not isinstance(tx, dict):
            return None, None, None
        if not isinstance(receipt, dict) or receipt.get("blockNumber") is None:
            return tx, None, None
        stamp = _from_seconds(tx.get("blockTimestamp"))
        if stamp is None:
            try:
                block = to_int(receipt["blockNumber"])
            except (KeyError, MalformedRow) as error:
                raise ProviderBadResponse(NAME, "unexpected response shape") from error
            stamp = (await self.block_times(chain, {block})).get(block)
        return tx, receipt, stamp


# --- helpers -----------------------------------------------------------------------------------


def _metadata_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _from_seconds(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(to_int(value), UTC)
    except (MalformedRow, OverflowError, OSError, ValueError):
        return None


def _as_transaction(transfer: Transfer) -> Transaction:
    """The part of a transaction an external transfer proves: it happened and moved value."""
    return Transaction(
        chain_id=transfer.chain_id,
        transaction_hash=transfer.transaction_hash,
        block_number=transfer.block_number,
        timestamp=transfer.timestamp,
        from_address=transfer.from_address or "",
        to_address=transfer.to_address,
        created_contract=None,
        value_raw=transfer.amount_raw,
        status=TxStatus.SUCCESS,
        provider=transfer.provider,
        data_quality=transfer.data_quality,
    )


__all__ = ["AlchemySource", "decode_cursor"]
