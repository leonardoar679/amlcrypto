"""An in-memory Etherscan V2 for tests, behaving as its documentation describes.

It filters by address and block range, sorts ascending, pages with `page` and `offset`, refuses a
`page * offset` beyond its result window the way Etherscan's classic API does, and answers errors
as HTTP 200 with {"status": "0", "message": "NOTOK", "result": "<cause>"}. An empty result is
status "0" with an empty list.
"""

from __future__ import annotations

import itertools
from typing import Any

import httpx2

ME = "0x" + "a1" * 20
OTHER = "0x" + "b2" * 20
KEY = "etherscan-test-key-123456"
HEAD = 24_000_000

_hashes = itertools.count(1)


def next_hash() -> str:
    return f"0x{next(_hashes):064x}"


def tx_row(block: int, sender: str = ME, to: str = OTHER, value: str = "0", **extra: Any) -> dict:
    return {
        "blockNumber": str(block),
        "timeStamp": str(1_700_000_000 + block),
        "hash": next_hash(),
        "transactionIndex": "0",
        "from": sender,
        "to": to,
        "value": value,
        "gas": "21000",
        "gasPrice": "1000000000",
        "gasUsed": "21000",
        "input": "0x",
        "methodId": "0x",
        "functionName": "",
        "contractAddress": "",
        "txreceipt_status": "1",
        "isError": "0",
        "confirmations": "100",
        **extra,
    }


def internal_row(block: int, sender: str = OTHER, to: str = ME, value: str = "5", **extra) -> dict:
    return {
        "blockNumber": str(block),
        "timeStamp": str(1_700_000_000 + block),
        "hash": next_hash(),
        "from": sender,
        "to": to,
        "value": value,
        "contractAddress": "",
        "input": "",
        "type": "call",
        "gas": "0",
        "gasUsed": "0",
        "traceId": "0_1",
        "isError": "0",
        "errCode": "",
        **extra,
    }


def token_row(block: int, sender: str = OTHER, to: str = ME, value: str = "7", **extra) -> dict:
    return {
        "blockNumber": str(block),
        "timeStamp": str(1_700_000_000 + block),
        "hash": next_hash(),
        "nonce": "1",
        "blockHash": "0x" + "00" * 32,
        "from": sender,
        "contractAddress": "0x" + "c3" * 20,
        "to": to,
        "value": value,
        "tokenName": "Test Token",
        "tokenSymbol": "TST",
        "tokenDecimal": "6",
        "transactionIndex": "0",
        "gas": "0",
        "gasPrice": "0",
        "gasUsed": "0",
        "cumulativeGasUsed": "0",
        "input": "deprecated",
        "confirmations": "100",
        **extra,
    }


def notok(cause: str) -> dict:
    return {"status": "0", "message": "NOTOK", "result": cause}


class FakeEtherscan:
    def __init__(self, *, window: int = 10_000, key: str = KEY, head: int = HEAD) -> None:
        self.rows: dict[str, list[dict]] = {"txlist": [], "txlistinternal": [], "tokentx": []}
        self.transactions: dict[str, dict] = {}  # eth_getTransactionByHash
        self.receipts: dict[str, dict] = {}  # eth_getTransactionReceipt
        self.blocks: dict[str, dict] = {}  # eth_getBlockByNumber, by hex number
        self.window, self.key, self.head = window, key, head
        self.requests: list[httpx2.Request] = []
        self.queued: list[
            Any
        ] = []  # responses (dicts) or exceptions to serve before the normal ones

    def add(self, action: str, *rows: dict) -> None:
        self.rows[action].extend(rows)

    @property
    def calls(self) -> list[dict[str, str]]:
        return [dict(request.url.params) for request in self.requests]

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.queued:
            item = self.queued.pop(0)
            if isinstance(item, Exception):
                raise item
            return httpx2.Response(200, json=item)
        return httpx2.Response(200, json=self.answer(dict(request.url.params)))

    def answer(self, params: dict[str, str]) -> dict:
        if params.get("apikey") != self.key:
            return notok("Invalid API Key")
        if not params.get("chainid"):
            return notok("Missing or unsupported chainid parameter (required for v2 api)")

        module, action = params.get("module"), params.get("action")
        if module == "proxy":
            return self.proxy(action, params)
        if module != "account" or action not in self.rows:
            return notok("Error! Missing Or invalid Action name")

        address = params["address"].lower()
        start, end = int(params["startblock"]), int(params["endblock"])
        page, offset = int(params["page"]), int(params["offset"])
        if page * offset > self.window:
            return notok(
                f"Result window is too large, PageNo x Offset size must be less than or equal "
                f"to {self.window}"
            )
        matching = [
            row
            for row in self.rows[action]
            if address in (row["from"].lower(), row["to"].lower())
            and start <= int(row["blockNumber"]) <= end
        ]
        matching.sort(key=lambda row: int(row["blockNumber"]))  # stable: insertion order inside
        if params.get("sort") == "desc":
            matching.reverse()
        chunk = matching[(page - 1) * offset : page * offset]
        if not chunk:
            return {"status": "0", "message": "No transactions found", "result": []}
        return {"status": "1", "message": "OK", "result": chunk}

    def proxy(self, action: str | None, params: dict[str, str]) -> dict:
        if action == "eth_blockNumber":
            result: Any = hex(self.head)
        elif action == "eth_getTransactionByHash":
            result = self.transactions.get(params["txhash"])
        elif action == "eth_getTransactionReceipt":
            result = self.receipts.get(params["txhash"])
        elif action == "eth_getBlockByNumber":
            result = self.blocks.get(params["tag"])
        else:
            return notok("Error! Missing Or invalid Action name")
        return {"jsonrpc": "2.0", "id": 1, "result": result}
