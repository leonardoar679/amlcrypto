"""An in-memory Alchemy JSON-RPC endpoint for tests (single and batched requests)."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime
from typing import Any

import httpx2

from tests.fake_etherscan import ME, OTHER, next_hash

HEAD = 90_000_000
_ids = itertools.count(1)


def asset_row(
    category: str,
    block: int,
    sender: str = ME,
    to: str = OTHER,
    value: int = 10**18,
    *,
    contract: str | None = None,
    decimals: int | None = 18,
    symbol: str = "ETH",
    **extra: Any,
) -> dict:
    tx_hash = extra.pop("hash", next_hash())
    suffix = {"external": "external", "internal": f"internal:{next(_ids)}"}.get(
        category, f"log:{next(_ids)}"
    )
    return {
        "blockNum": hex(block),
        "uniqueId": f"{tx_hash}:{suffix}",
        "hash": tx_hash,
        "from": sender,
        "to": to,
        "value": value / 10 ** (decimals or 0),
        "asset": symbol,
        "category": category,
        "rawContract": {
            "value": hex(value),
            "address": contract,
            "decimal": None if decimals is None else hex(decimals),
        },
        **extra,
    }


class FakeAlchemy:
    def __init__(self, *, head: int = HEAD) -> None:
        self.rows: list[dict] = []
        self.transactions: dict[str, dict] = {}
        self.receipts: dict[str, dict] = {}
        self.block_times: dict[int, int] = {}  # block number -> unix seconds
        self.head = head
        self.requests: list[httpx2.Request] = []
        self.payloads: list[Any] = []  # what was posted, parsed
        self.queued: list[Any] = []  # bodies (or exceptions) to serve before the normal answers

    def add(self, *rows: dict) -> None:
        self.rows.extend(rows)

    @property
    def methods(self) -> list[str]:
        """The method of every call made, batches flattened."""
        found = []
        for payload in self.payloads:
            for call in payload if isinstance(payload, list) else [payload]:
                found.append(call["method"])
        return found

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        import json

        self.requests.append(request)
        payload = json.loads(request.content)
        self.payloads.append(payload)
        if self.queued:
            item = self.queued.pop(0)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, tuple):  # (status, body)
                return httpx2.Response(item[0], json=item[1])
            return httpx2.Response(200, json=item)
        if isinstance(payload, list):
            return httpx2.Response(200, json=[self.call(item) for item in payload])
        return httpx2.Response(200, json=self.call(payload))

    def call(self, item: dict) -> dict:
        method, params = item["method"], item.get("params") or []
        try:
            if method == "eth_blockNumber":
                result: Any = hex(self.head)
            elif method == "eth_getBlockByNumber":
                number = int(params[0], 16)
                seconds = self.block_times.get(number)
                result = (
                    None if seconds is None else {"number": params[0], "timestamp": hex(seconds)}
                )
            elif method == "eth_getTransactionByHash":
                result = self.transactions.get(params[0])
            elif method == "eth_getTransactionReceipt":
                result = self.receipts.get(params[0])
            elif method == "alchemy_getAssetTransfers":
                result = self.asset_transfers(params[0])
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": item["id"],
                    "error": {"code": -32601, "message": "Method not found"},
                }
        except ValueError as error:
            return {
                "jsonrpc": "2.0",
                "id": item["id"],
                "error": {"code": -32602, "message": str(error)},
            }
        return {"jsonrpc": "2.0", "id": item["id"], "result": result}

    def asset_transfers(self, request: dict) -> dict:
        categories = set(request["category"])
        if not categories <= {"external", "internal", "erc20", "erc721", "erc1155", "specialnft"}:
            raise ValueError("Invalid category")
        low = int(request.get("fromBlock", "0x0"), 16)
        high = (
            self.head
            if request.get("toBlock", "latest") == "latest"
            else int(request["toBlock"], 16)
        )
        matching = [
            row
            for row in self.rows
            if row["category"] in categories
            and low <= int(row["blockNum"], 16) <= high
            and ("fromAddress" not in request or row["from"].lower() == request["fromAddress"])
            and ("toAddress" not in request or (row["to"] or "").lower() == request["toAddress"])
        ]
        matching.sort(key=lambda row: int(row["blockNum"], 16))
        offset = int(request["pageKey"].removeprefix("pk-")) if request.get("pageKey") else 0
        count = int(request.get("maxCount", "0x3e8"), 16)
        chunk = [dict(row) for row in matching[offset : offset + count]]
        if request.get("withMetadata"):
            for row in chunk:
                seconds = self.block_times.get(int(row["blockNum"], 16), 1_700_000_000)
                stamp = datetime.fromtimestamp(seconds, UTC).isoformat(timespec="milliseconds")
                row["metadata"] = {"blockTimestamp": stamp.replace("+00:00", "Z")}
        result: dict[str, Any] = {"transfers": chunk}
        if offset + count < len(matching):
            result["pageKey"] = f"pk-{offset + count}"
        return result
