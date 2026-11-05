"""An in-memory TronGrid for tests, behaving the way the live API was observed to.

The address feeds filter by the addresses a row involves and by time (milliseconds), sort by time,
and page by `fingerprint`: a page that has more after it carries `meta.fingerprint`, the last page
does not. A bad address is HTTP 400 {"success": false, "error": ...}; an unknown transaction id
answers {}.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

import httpx2

from app.chains.encoding import b58check_encode
from app.chains.tron.address import TronAddressValidator

KEY = "trongrid-test-key-1234567890"
USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
ALICE = "TRCf3GLcaPEvBSKYwgJPW24Ti1EKmeJp6T"
BOB = "TSWsmaEDgWaE2VsP9tCBGh5GbBRbrdHSuZ"
CAROL = "TF7RV5mRMaUXRR3ok7EUh1Ua1SxKFY9u27"
HEAD = 87_000_000
T0 = 1_790_000_000_000  # a time in milliseconds

_ids = itertools.count(1)
_validator = TronAddressValidator()


def to_hex(address: str) -> str:
    from app.chains.encoding import b58check_decode

    return b58check_decode(address).hex()


def next_id() -> str:
    return f"{next(_ids):064x}"


def trx_row(
    ms: int, sender: str = ALICE, to: str = BOB, amount: int = 1_000_000, **extra: Any
) -> dict:
    """A row of /v1/accounts/{address}/transactions for a TRX transfer."""
    return {
        "ret": [{"contractRet": "SUCCESS", "fee": 0}],
        "txID": next_id(),
        "net_usage": 0,
        "blockNumber": HEAD - 1000 + (ms - T0) // 3000,
        "block_timestamp": ms,
        "raw_data": {
            "contract": [
                {
                    "parameter": {
                        "value": {
                            "amount": amount,
                            "owner_address": to_hex(sender),
                            "to_address": to_hex(to),
                        }
                    },
                    "type": "TransferContract",
                }
            ]
        },
        "internal_transactions": [],
        **extra,
    }


def trc20_row(ms: int, sender: str = ALICE, to: str = BOB, value: int = 5_000_000, **extra) -> dict:
    return {
        "transaction_id": next_id(),
        "token_info": {"symbol": "USDT", "address": USDT, "decimals": 6, "name": "Tether USD"},
        "block_timestamp": ms,
        "from": sender,
        "to": to,
        "type": "Transfer",
        "value": str(value),
        **extra,
    }


def internal_row(ms: int, sender: str = CAROL, to: str = ALICE, sun: int = 7, **extra) -> dict:
    return {
        "internal_tx_id": next_id(),
        "data": {"note": "call", "rejected": False, "call_value": {"_": sun}},
        "block_timestamp": ms,
        "to_address": to_hex(to),
        "tx_id": next_id(),
        "from_address": to_hex(sender),
        **extra,
    }


def involved(row: dict) -> set[str]:
    """The Base58 addresses a feed row involves. A row may list more (`_also`): TronGrid returns a
    transaction for an address that took part only through an internal call."""
    if "raw_data" in row:
        value = row["raw_data"]["contract"][0]["parameter"]["value"]
        hexes = [value.get(k) for k in ("owner_address", "to_address", "contract_address")]
        return {b58check_encode(bytes.fromhex(h)) for h in hexes if h} | set(row.get("_also", ()))
    if "from" in row:
        return {row["from"], row["to"]}
    return {
        b58check_encode(bytes.fromhex(row["from_address"])),
        b58check_encode(bytes.fromhex(row["to_address"])),
    }


class FakeTronGrid:
    def __init__(self, *, key: str = KEY, head: int = HEAD) -> None:
        self.feeds: dict[str, list[dict]] = {
            "transactions": [],
            "transactions/trc20": [],
            "internal-transactions": [],
        }
        self.transactions: dict[str, dict] = {}  # gettransactionbyid answers
        self.infos: dict[str, dict] = {}  # gettransactioninfobyid answers
        self.block_times: dict[int, int] = {}  # block number -> milliseconds
        self.key, self.head = key, head
        self.requests: list[httpx2.Request] = []
        self.queued: list[Any] = []  # (status, body) tuples, bodies or exceptions served first

    def add(self, feed: str, *rows: dict) -> None:
        self.feeds[feed].extend(rows)

    @property
    def calls(self) -> list[tuple[str, dict[str, str]]]:
        return [(r.url.path, dict(r.url.params)) for r in self.requests]

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.queued:
            item = self.queued.pop(0)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, tuple):
                return httpx2.Response(item[0], json=item[1])
            return httpx2.Response(200, json=item)
        if request.headers.get("tron-pro-api-key") != self.key:
            return httpx2.Response(401, json={"Error": "Invalid api key"})
        path = request.url.path
        if path.startswith("/wallet/"):
            body = json.loads(request.content or b"{}")
            return httpx2.Response(200, json=self.wallet(path, body))
        return self.feed(path, dict(request.url.params))

    def wallet(self, path: str, body: dict) -> dict:
        if path == "/wallet/getnowblock":
            return {"block_header": {"raw_data": {"number": self.head, "timestamp": T0}}}
        if path == "/wallet/getblockbynum":
            ms = self.block_times.get(body["num"])
            if ms is None:
                return {}
            return {"block_header": {"raw_data": {"number": body["num"], "timestamp": ms}}}
        if path == "/wallet/gettransactionbyid":
            return self.transactions.get(body["value"], {})
        if path == "/wallet/gettransactioninfobyid":
            return self.infos.get(body["value"], {})
        return {"Error": "unknown method"}

    def feed(self, path: str, params: dict[str, str]) -> httpx2.Response:
        # /v1/accounts/{address}/{feed}
        _, _, _, address, *rest = path.split("/")
        name = "/".join(rest)
        if not _validator.check(address).valid:
            return httpx2.Response(
                400,
                json={
                    "success": False,
                    "error": "A valid account address is required.",
                    "statusCode": 400,
                },
            )
        low = int(params.get("min_timestamp", 0))
        high = int(params.get("max_timestamp", 2**62))
        rows = [
            row
            for row in self.feeds[name]
            if address in involved(row) and low <= row["block_timestamp"] <= high
        ]
        rows.sort(key=lambda row: row["block_timestamp"])
        offset = int(params["fingerprint"].removeprefix("fp")) if params.get("fingerprint") else 0
        limit = min(int(params.get("limit", 20)), 200)
        page = rows[offset : offset + limit]
        meta: dict[str, Any] = {"at": T0, "page_size": len(page)}
        if offset + limit < len(rows):
            meta["fingerprint"] = f"fp{offset + limit}"
        return httpx2.Response(200, json={"data": page, "success": True, "meta": meta})
