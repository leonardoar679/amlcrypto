"""An in-memory Esplora for tests, behaving the way the live service was observed to (2026-11-05).

It holds transactions in Esplora's own JSON shape and answers from them: an address lists its
CONFIRMED transactions newest first, 25 to a page, and /txs/chain/:last_seen_txid continues after
that transaction (an unknown one gives an empty page, as the real service does). An unknown
transaction is a 404 with a text body, a bad address is a 400, and the figures at /address/:a are
worked out from what is stored, so a test can make them disagree with the listing on purpose
(`unlisted`).

Synthetic transactions are built with `tx`, `spend` and `pay`; real ones are plain fixtures.
"""

from __future__ import annotations

from typing import Any

import httpx2

from app.chains.bitcoin.address import BitcoinAddressValidator
from app.chains.encoding import b58check_encode

HEAD = 974_000
T0 = 1_790_000_000  # seconds
PAGE = 25
COINBASE_TXID = "00" * 32
_VALIDATOR = BitcoinAddressValidator()


def p2pkh(n: int) -> str:
    """A valid legacy address, one per number."""
    return b58check_encode(b"\x00" + n.to_bytes(20, "big"))


def p2sh(n: int) -> str:
    return b58check_encode(b"\x05" + n.to_bytes(20, "big"))


def txid(n: int) -> str:
    return f"{n:064x}"


def spend(prev: str, vout: int, address: str | None, value: int, kind: str = "p2pkh") -> dict:
    """An input, with the output it spends looked up, as Esplora lists it."""
    prevout: dict[str, Any] = {"scriptpubkey_type": kind, "value": value}
    if address is not None:
        prevout["scriptpubkey_address"] = address
    return {
        "txid": prev,
        "vout": vout,
        "prevout": prevout,
        "is_coinbase": False,
        "sequence": 4294967293,
    }


def pay(address: str | None, value: int, kind: str = "p2pkh") -> dict:
    output: dict[str, Any] = {"scriptpubkey_type": kind, "value": value}
    if address is not None:
        output["scriptpubkey_address"] = address
    return output


def coinbase_input() -> dict:
    return {
        "txid": COINBASE_TXID,
        "vout": 4294967295,
        "prevout": None,
        "is_coinbase": True,
        "sequence": 4294967295,
    }


def tx(
    n: int,
    vin: list[dict],
    vout: list[dict],
    *,
    height: int | None = HEAD - 100,
    fee: int | None = None,
) -> dict:
    """A transaction with id `txid(n)`. The fee is what the inputs have over the outputs."""
    coinbase = bool(vin) and vin[0]["is_coinbase"]
    paid = sum(o["value"] for o in vout)
    if fee is None:
        fee = 0 if coinbase else sum(i["prevout"]["value"] for i in vin) - paid
    status: dict[str, Any] = {"confirmed": height is not None}
    if height is not None:
        status |= {"block_height": height, "block_hash": txid(height), "block_time": T0 + height}
    return {
        "txid": txid(n),
        "version": 2,
        "locktime": 0,
        "size": 200,
        "weight": 800,
        "fee": fee,
        "vin": vin,
        "vout": vout,
        "status": status,
    }


class FakeEsplora:
    def __init__(self, *, head: int = HEAD) -> None:
        self.head = head
        self.txs: dict[str, dict] = {}
        self._order: list[str] = []
        # Counted in the address figures but left out of its listing: a node with a gap.
        self.unlisted: set[str] = set()
        # Parts of the service that are down while the rest works.
        self.broken_summary = False
        self.broken_outspends = False
        self.broken_tip = False
        # Added to the node's own figures (for example {"tx_count": 1}): a node whose figures
        # disagree with what it lists, in one respect at a time.
        self.skew: dict[str, int] = {}
        # Inputs served without the output they spend (txid, input index), though the node's own
        # figures still know the amounts.
        self.unresolved: set[tuple[str, int]] = set()
        self.requests: list[httpx2.Request] = []
        self.queued: list[Any] = []  # (status, body) tuples, bodies or exceptions served first

    def add(self, *transactions: dict) -> None:
        for item in transactions:
            self.txs[item["txid"]] = item
            self._order.append(item["txid"])

    @property
    def calls(self) -> list[tuple[str, dict[str, str]]]:
        return [(r.url.path.removeprefix("/api"), dict(r.url.params)) for r in self.requests]

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.queued:
            item = self.queued.pop(0)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, tuple):
                body = item[1]
                return (
                    httpx2.Response(item[0], text=body)
                    if isinstance(body, str)
                    else httpx2.Response(item[0], json=body)
                )
            return httpx2.Response(200, json=item)
        return self.route(request.url.path.removeprefix("/api"))

    # --- the routes --------------------------------------------------------------------------

    def route(self, path: str) -> httpx2.Response:
        parts = path.strip("/").split("/")
        if parts == ["blocks", "tip", "height"]:
            if self.broken_tip:
                return httpx2.Response(500, text="Internal Server Error")
            return httpx2.Response(200, text=str(self.head))
        if parts[0] == "address" and len(parts) in (2, 4, 5):
            address = parts[1]
            if not _VALIDATOR.check(address).valid:
                return httpx2.Response(400, text="base58 error")
            if len(parts) == 2:
                if self.broken_summary:
                    return httpx2.Response(500, text="Internal Server Error")
                return httpx2.Response(200, json=self.stats(address))
            if parts[2:4] == ["txs", "chain"]:
                return httpx2.Response(
                    200, json=self.listing(address, parts[4] if len(parts) == 5 else None)
                )
        if parts[0] == "tx" and len(parts) in (2, 3):
            found = self.txs.get(parts[1])
            if found is None:
                return httpx2.Response(404, text="Transaction not found")
            if len(parts) == 2:
                return httpx2.Response(200, json=self.served(found))
            if parts[2] == "outspends":
                if self.broken_outspends:
                    return httpx2.Response(500, text="Internal Server Error")
                return httpx2.Response(200, json=self.outspends(found))
        return httpx2.Response(404, text="Not found")

    def touching(self, address: str, *, confirmed: bool) -> list[dict]:
        found = []
        for key in self._order:
            item = self.txs[key]
            if item["status"]["confirmed"] is not confirmed:
                continue
            addresses = {o.get("scriptpubkey_address") for o in item["vout"]}
            addresses |= {
                i["prevout"].get("scriptpubkey_address") for i in item["vin"] if i["prevout"]
            }
            if address in addresses:
                found.append(item)
        return found

    def listing(self, address: str, last_seen: str | None) -> list[dict]:
        rows = [
            item
            for item in self.touching(address, confirmed=True)
            if item["txid"] not in self.unlisted
        ]
        rows.sort(
            key=lambda item: (-item["status"]["block_height"], -self._order.index(item["txid"]))
        )
        if last_seen is not None:
            ids = [item["txid"] for item in rows]
            rows = rows[ids.index(last_seen) + 1 :] if last_seen in ids else []
        return [self.served(item) for item in rows[:PAGE]]

    def served(self, item: dict) -> dict:
        """The transaction as the API shows it: `unresolved` inputs come without their prevout."""
        blanked = [i for i in range(len(item["vin"])) if (item["txid"], i) in self.unresolved]
        if not blanked:
            return item
        vin = [{**v, "prevout": None} if i in blanked else v for i, v in enumerate(item["vin"])]
        return {**item, "vin": vin}

    def stats(self, address: str) -> dict:
        def counted(confirmed: bool) -> dict:
            funded = [
                o
                for item in self.touching(address, confirmed=confirmed)
                for o in item["vout"]
                if o.get("scriptpubkey_address") == address
            ]
            spent = [
                i["prevout"]
                for item in self.touching(address, confirmed=confirmed)
                for i in item["vin"]
                if i["prevout"] and i["prevout"].get("scriptpubkey_address") == address
            ]
            return {
                "funded_txo_count": len(funded),
                "funded_txo_sum": sum(o["value"] for o in funded),
                "spent_txo_count": len(spent),
                "spent_txo_sum": sum(o["value"] for o in spent),
                "tx_count": len(self.touching(address, confirmed=confirmed)),
            }

        chain = counted(True)
        for key, extra in self.skew.items():
            chain[key] += extra
        return {"address": address, "chain_stats": chain, "mempool_stats": counted(False)}

    def outspends(self, found: dict) -> list[dict]:
        answers = []
        for index in range(len(found["vout"])):
            answer: dict[str, Any] = {"spent": False}
            for other in self.txs.values():
                for position, item in enumerate(other["vin"]):
                    if item["txid"] == found["txid"] and item["vout"] == index:
                        answer = {
                            "spent": True,
                            "txid": other["txid"],
                            "vin": position,
                            "status": other["status"],
                        }
            answers.append(answer)
        return answers
