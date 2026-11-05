"""An in-memory Helius for tests, behaving the way the live service was observed to (2026-11-05).

It answers the JSON-RPC methods the connector uses from a list of transactions in the shape
`getTransactionsForAddress` returns them (jsonParsed, with `transactionIndex`): the finalized slot,
one transaction, a wallet's transactions (newest first by default, paged by `paginationToken`, with
the slot and status filters) and a wallet's balances, which are worked out from the stored
transactions so that a history and the chain always agree unless a test makes them disagree.

Like the real service it refuses a request that does not allow transaction version 1 when it
meets one (JSON-RPC error -32015). Synthetic transactions are built with `address`, `signature`,
`account`, `instruction` and `item`; real ones are plain fixtures.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx2

from app.chains.encoding import b58encode

KEY = "helius-test-key-1234567890"
HEAD = 465_700_000
T0 = 1_790_000_000
SYSTEM = "11111111111111111111111111111111"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
WSOL = "So11111111111111111111111111111111111111112"


def address(n: int) -> str:
    """A valid Solana address, one per number."""
    return b58encode(hashlib.sha256(f"address {n}".encode()).digest())


def signature(n: int) -> str:
    """A valid transaction signature (64 bytes in Base58), one per number."""
    return b58encode(hashlib.sha512(f"signature {n}".encode()).digest())


def account(pubkey: str, *, signer: bool = False, writable: bool = True) -> dict:
    return {"pubkey": pubkey, "signer": signer, "writable": writable, "source": "transaction"}


def instruction(program_id: str, kind: str, info: dict, *, program: str | None = None) -> dict:
    """A parsed instruction, as the jsonParsed encoding gives it."""
    names = {SYSTEM: "system", TOKEN: "spl-token", TOKEN_2022: "spl-token"}
    return {
        "program": program or names.get(program_id, "unknown"),
        "programId": program_id,
        "parsed": {"type": kind, "info": info},
        "stackHeight": 1,
    }


def token_entry(
    accounts: list[str],
    token_account: str,
    mint: str,
    owner: str | None,
    raw: int,
    decimals: int = 6,
    program: str = TOKEN,
) -> dict:
    entry: dict[str, Any] = {
        "accountIndex": accounts.index(token_account),
        "mint": mint,
        "uiTokenAmount": {
            "amount": str(raw),
            "decimals": decimals,
            "uiAmount": raw / 10**decimals,
            "uiAmountString": str(raw / 10**decimals),
        },
        "programId": program,
    }
    if owner is not None:
        entry["owner"] = owner
    return entry


def item(
    n: int,
    accounts: list[str],
    instructions: list[dict],
    *,
    slot: int = HEAD - 1000,
    index: int = 0,
    lamports: dict[str, tuple[int, int]] | None = None,
    fee: int = 5000,
    inner: dict[int, list[dict]] | None = None,
    pre_tokens: list[dict] | None = None,
    post_tokens: list[dict] | None = None,
    err: Any = None,
    version: int | str = 0,
) -> dict:
    """A transaction. `lamports` gives (before, after) for the accounts that matter; the first
    account pays the fee and signs. Balances not given are unchanged (a million lamports)."""
    lamports = lamports or {}
    pre = [lamports.get(a, (1_000_000, 1_000_000))[0] for a in accounts]
    post = [lamports.get(a, (1_000_000, 1_000_000))[1] for a in accounts]
    return {
        "slot": slot,
        "transactionIndex": index,
        "blockTime": T0 + slot,
        "version": version,
        "transaction": {
            "signatures": [signature(n)],
            "message": {
                "accountKeys": [
                    account(a, signer=(i == 0), writable=True) for i, a in enumerate(accounts)
                ],
                "instructions": instructions,
                "recentBlockhash": "x",
            },
        },
        "meta": {
            "err": err,
            "fee": fee,
            "preBalances": pre,
            "postBalances": post,
            "preTokenBalances": pre_tokens or [],
            "postTokenBalances": post_tokens or [],
            "innerInstructions": [
                {"index": i, "instructions": ixs} for i, ixs in (inner or {}).items()
            ],
            "logMessages": [],
            "status": {"Ok": None} if err is None else {"Err": err},
        },
    }


def transfer_item(n: int, sender: str, receiver: str, lamports: int, *, slot: int, fee: int = 5000):
    """The simplest transaction there is: `sender` pays `receiver` and the fee."""
    accounts = [sender, receiver, SYSTEM]
    return item(
        n,
        accounts,
        [
            instruction(
                SYSTEM,
                "transfer",
                {"source": sender, "destination": receiver, "lamports": lamports},
            )
        ],
        slot=slot,
        index=n % 400,
        fee=fee,
        lamports={
            sender: (10**9, 10**9 - lamports - fee),
            receiver: (1_000_000, 1_000_000 + lamports),
        },
    )


class FakeHelius:
    def __init__(self, *, head: int = HEAD, key: str = KEY) -> None:
        self.items: list[dict] = []
        self.head, self.key = head, key
        self.requests: list[httpx2.Request] = []
        self.queued: list[Any] = []  # (status, body) tuples, bodies or exceptions served first
        self.unlisted: set[str] = set()  # transactions the wallet listing leaves out
        self.broken: set[str] = set()  # methods that answer with a server error
        self.snapshot_lag = 0  # balances are as of this many slots before the head
        self.skew_sol = 0  # added to getBalance: a chain that disagrees with the listing
        self.skew_tokens: dict[str, int] = {}  # added to a mint's total in getTokenAccountsByOwner
        self.unfinalized: set[str] = set()  # getTransaction answers null for these

    def add(self, *items: dict) -> None:
        self.items.extend(items)

    @property
    def calls(self) -> list[tuple[str, list]]:
        out = []
        for request in self.requests:
            body = json.loads(request.content or b"{}")
            out.append((body.get("method"), body.get("params")))
        return out

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.queued:
            queued = self.queued.pop(0)
            if isinstance(queued, Exception):
                raise queued
            if isinstance(queued, tuple):
                body = queued[1]
                return (
                    httpx2.Response(queued[0], text=body)
                    if isinstance(body, str)
                    else httpx2.Response(queued[0], json=body)
                )
            return httpx2.Response(200, json=queued)
        if request.url.params.get("api-key") != self.key:
            return httpx2.Response(
                401,
                json={
                    "jsonrpc": "2.0",
                    "error": {"code": -32001, "message": "Unauthorized"},
                    "id": 1,
                },
            )
        body = json.loads(request.content)
        method, params = body["method"], body["params"]
        if method in self.broken:
            return httpx2.Response(500, text="Internal Server Error")
        try:
            result = getattr(self, f"rpc_{method}")(*params)
        except RpcError as error:
            return httpx2.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "error": {"code": error.code, "message": error.message},
                    "id": 1,
                },
            )
        return httpx2.Response(200, json={"jsonrpc": "2.0", "result": result, "id": 1})

    # --- the methods -------------------------------------------------------------------------

    def rpc_getSlot(self, options: dict | None = None) -> int:
        return self.head

    @staticmethod
    def _check_version(options: dict, found: dict) -> None:
        allowed = options.get("maxSupportedTransactionVersion")
        version = found.get("version")
        if version not in (None, "legacy") and (allowed is None or int(version) > allowed):
            raise RpcError(
                -32015,
                f"Transaction version ({version}) is not supported by the requesting client. "
                f'Please try again with "maxSupportedTransactionVersion": {version}',
            )

    def rpc_getTransaction(self, sig: str, options: dict) -> dict | None:
        for found in self.items:
            if found["transaction"]["signatures"][0] == sig and sig not in self.unfinalized:
                self._check_version(options, found)
                return {k: v for k, v in found.items() if k != "transactionIndex"}
        return None

    @staticmethod
    def _involves(found: dict, wallet: str) -> bool:
        keys = {k["pubkey"] for k in found["transaction"]["message"]["accountKeys"]}
        if wallet in keys:
            return True
        # tokenAccounts "balanceChanged": a token account the wallet owns changed its balance
        before = {e["accountIndex"]: e for e in found["meta"]["preTokenBalances"]}
        for entry in found["meta"]["postTokenBalances"]:
            if entry.get("owner") == wallet:
                old = before.get(entry["accountIndex"])
                if (
                    old is None
                    or old["uiTokenAmount"]["amount"] != entry["uiTokenAmount"]["amount"]
                ):
                    return True
        return any(
            e.get("owner") == wallet
            for e in found["meta"]["preTokenBalances"]
            if e["accountIndex"]
            not in {p["accountIndex"] for p in found["meta"]["postTokenBalances"]}
        )

    def rpc_getTransactionsForAddress(self, wallet: str, options: dict) -> dict:
        slots = (options.get("filters") or {}).get("slot") or {}
        status = (options.get("filters") or {}).get("status", "any")
        rows = []
        for found in self.items:
            sig = found["transaction"]["signatures"][0]
            if sig in self.unlisted or not self._involves(found, wallet):
                continue
            if (
                "gte" in slots
                and found["slot"] < slots["gte"]
                or "lte" in slots
                and found["slot"] > slots["lte"]
            ):
                continue
            failed = found["meta"]["err"] is not None
            if (status == "succeeded" and failed) or (status == "failed" and not failed):
                continue
            rows.append(found)
        descending = options.get("sortOrder", "desc") == "desc"
        rows.sort(key=lambda f: (f["slot"], f["transactionIndex"]), reverse=descending)
        token = options.get("paginationToken")
        if token:
            slot, position = (int(x) for x in token.split(":"))
            after = (slot, position)
            rows = [
                f
                for f in rows
                if (
                    (f["slot"], f["transactionIndex"]) < after
                    if descending
                    else (f["slot"], f["transactionIndex"]) > after
                )
            ]
        limit = options.get("limit", 1000)
        page, more = rows[:limit], len(rows) > limit
        for found in page:
            self._check_version(options, found)
        last = page[-1] if page else None
        return {
            "data": page,
            "paginationToken": f"{last['slot']}:{last['transactionIndex']}"
            if more and last
            else None,
        }

    def rpc_getBalance(self, wallet: str, options: dict | None = None) -> dict:
        total = 0
        slot = self.head - self.snapshot_lag
        for found in self.items:
            if found["slot"] > slot:
                continue
            keys = [k["pubkey"] for k in found["transaction"]["message"]["accountKeys"]]
            if wallet in keys:
                position = keys.index(wallet)
                total += (
                    found["meta"]["postBalances"][position] - found["meta"]["preBalances"][position]
                )
        assert total >= 0, "the stored transactions leave this wallet with a negative balance"
        return {"context": {"slot": slot}, "value": total + self.skew_sol}

    def rpc_getTokenAccountsByOwner(self, wallet: str, selector: dict, options: dict) -> dict:
        program = selector["programId"]
        per_account: dict[str, list] = {}
        slot = self.head - self.snapshot_lag
        for found in sorted(self.items, key=lambda f: (f["slot"], f["transactionIndex"])):
            if found["slot"] > slot:
                continue
            keys = [k["pubkey"] for k in found["transaction"]["message"]["accountKeys"]]
            for side, sign in (("preTokenBalances", -1), ("postTokenBalances", 1)):
                for entry in found["meta"][side]:
                    if entry.get("owner") == wallet and entry.get("programId") == program:
                        slot_account = keys[entry["accountIndex"]]
                        row = per_account.setdefault(slot_account, [entry["mint"], 0])
                        row[1] += sign * int(entry["uiTokenAmount"]["amount"])
        value = []
        for key, (mint, amount) in per_account.items():
            amount += self.skew_tokens.get(mint, 0)
            assert amount >= 0, "the stored transactions leave a token account negative"
            if amount:
                info = {
                    "mint": mint,
                    "owner": wallet,
                    "tokenAmount": {"amount": str(amount), "decimals": 6},
                }
                value.append(
                    {
                        "pubkey": key,
                        "account": {
                            "data": {
                                "parsed": {"info": info, "type": "account"},
                                "program": "spl-token",
                            }
                        },
                    }
                )
        return {"context": {"slot": slot}, "value": value}


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message
