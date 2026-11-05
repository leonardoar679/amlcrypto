"""Helius as a source of Solana address histories, transactions and balances.

What is used, and what was checked against the live service on 2026-11-05:

* `getTransactionsForAddress`, a Helius method (not standard Solana RPC): an address's transactions
  with their full contents, newest first, paged by `paginationToken` ("slot:position", null at the
  end). With `tokenAccounts: balanceChanged` it also returns the transactions that changed the
  balance of a token account the address owns, which plain `getSignaturesForAddress` never lists
  (a token RECEIVED lands in the token account and does not mention the wallet at all).
* `getTransaction` for one transaction, and `getSlot`, `getBalance`, `getTokenAccountsByOwner` for
  the chain's height and a wallet's balances. All at the `finalized` commitment: nothing read here
  can be rolled back.
* Every request says `maxSupportedTransactionVersion: 1`. Solana now has version 1 transactions, and
  a node answers a request that only allows version 0 with error -32015.

The cursor is Helius's `paginationToken`, checked before use. The decoding of what comes back is
ours (app.chains.solana.normalize): Helius's own parsed list (`getTransfersByAddress`) is a separate
method on a separate plan and is not used.
"""

from __future__ import annotations

import re
from typing import Any

from app.chains.cursors import InvalidCursor
from app.chains.models import Page
from app.chains.solana.chains import SolanaChain
from app.chains.solana.models import SolanaTransaction, TokenSnapshot, WalletFigures
from app.chains.solana.normalize import (
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    solana_address,
    solana_transaction,
)
from app.chains.values import MalformedRow, raw_amount, to_int
from app.providers.base import ProviderBadResponse, ProviderError
from app.providers.budget import RequestBudget
from app.providers.client import ProviderClient
from app.providers.jsonrpc import jsonrpc_body_error
from app.providers.rawsink import SOLANA_TRANSACTION, RawSink

NAME = "helius"
PAGE_SIZE = 100  # full transactions are large; Helius allows more, but one page stays small
_CURSOR = re.compile(r"^[0-9]{1,20}:[0-9]{1,10}$")
_UNSUPPORTED_VERSION = -32015
_FINALIZED = {"commitment": "finalized"}


def helius_body_error(provider: str, data: Any) -> ProviderError | None:
    """JSON-RPC errors, with Solana's 'transaction version not supported' called what it is."""
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("code") == _UNSUPPORTED_VERSION:
        # Not an outage: asking again changes nothing until the request names a newer version.
        return ProviderBadResponse(provider, "the node does not support this transaction version")
    return jsonrpc_body_error(provider, data)


class HeliusSource:
    name = NAME

    def __init__(
        self,
        client: ProviderClient,
        *,
        budget: RequestBudget | None = None,
        raw: RawSink | None = None,
    ) -> None:
        self._client = client
        self._budget = budget
        self._raw = raw

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        response = await self._client.request(
            NAME,
            "POST",
            "",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            budget=self._budget,
            body_check=helius_body_error,
        )
        data = response.data
        if not isinstance(data, dict) or "result" not in data:
            raise ProviderBadResponse(NAME, "unexpected response shape")
        return data["result"]

    # --- the chain ---------------------------------------------------------------------------

    async def block_height(self, chain: SolanaChain) -> int:
        """The newest finalized slot."""
        result = await self._rpc("getSlot", [_FINALIZED])
        if isinstance(result, bool) or not isinstance(result, int) or result < 0:
            raise ProviderBadResponse(NAME, "unexpected response shape")
        return result

    # --- address history ---------------------------------------------------------------------

    async def address_page(
        self,
        chain: SolanaChain,
        address: str,
        *,
        cursor: str | None = None,
        from_block: int | None = None,
        to_block: int | None = None,
    ) -> Page[SolanaTransaction]:
        """The next transactions of a wallet, newest first, decoded."""
        if cursor is not None and not _CURSOR.fullmatch(cursor):
            raise InvalidCursor("not a Helius cursor")
        filters: dict[str, Any] = {"tokenAccounts": "balanceChanged", "status": "any"}
        slots = {}
        if from_block is not None:
            slots["gte"] = from_block
        if to_block is not None:
            slots["lte"] = to_block
        if slots:
            filters["slot"] = slots
        options: dict[str, Any] = {
            "transactionDetails": "full",
            "encoding": "jsonParsed",
            "maxSupportedTransactionVersion": 1,
            "limit": PAGE_SIZE,
            "sortOrder": "desc",
            "commitment": "finalized",
            "filters": filters,
        }
        if cursor:
            options["paginationToken"] = cursor

        result = await self._rpc("getTransactionsForAddress", [address, options])
        rows = result.get("data") if isinstance(result, dict) else None
        if not isinstance(rows, list):
            raise ProviderBadResponse(NAME, "unexpected response shape")

        transactions: list[SolanaTransaction] = []
        skipped = 0
        for row in rows:
            try:
                decoded = solana_transaction(row, chain, NAME)
            except MalformedRow:
                skipped += 1
                continue
            transactions.append(decoded)
            if self._raw is not None:
                self._raw.offer(chain.chain_id, SOLANA_TRANSACTION, decoded.signature, NAME, row)
        token = result.get("paginationToken")
        # No rows means no more, whatever the token says: it cannot loop forever.
        next_cursor = token if rows and isinstance(token, str) and token else None
        notes = (
            (f"{skipped} transactions from helius could not be read and were skipped",)
            if skipped
            else ()
        )
        return Page(transactions, next_cursor, False, notes)

    # --- one transaction ---------------------------------------------------------------------

    async def transaction(self, chain: SolanaChain, signature: str) -> dict[str, Any] | None:
        """The transaction as the node gives it, or None if it is not (yet) finalized or unknown."""
        result = await self._rpc(
            "getTransaction",
            [
                signature,
                {
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 1,
                    "commitment": "finalized",
                },
            ],
        )
        if result is None:
            return None
        if not isinstance(result, dict):
            raise ProviderBadResponse(NAME, "unexpected response shape")
        if self._raw is not None:
            self._raw.offer(chain.chain_id, SOLANA_TRANSACTION, signature, NAME, result)
        return result

    # --- what a wallet holds -----------------------------------------------------------------

    async def wallet_figures(self, chain: SolanaChain, address: str) -> WalletFigures:
        """The wallet's SOL balance and token balances now, each with the slot it is as of."""
        sol = await self._rpc("getBalance", [address, _FINALIZED])
        try:
            sol_raw, sol_slot = raw_amount(sol["value"]), to_int(sol["context"]["slot"])
        except (KeyError, TypeError, MalformedRow) as error:
            raise ProviderBadResponse(NAME, "unexpected response shape") from error

        tokens: dict[str, TokenSnapshot] = {}
        for program in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
            held = await self._rpc(
                "getTokenAccountsByOwner",
                [address, {"programId": program}, {"encoding": "jsonParsed", **_FINALIZED}],
            )
            tokens[program] = _snapshot(held)
        return WalletFigures(address, sol_raw, sol_slot, tokens, NAME)


def _snapshot(held: Any) -> TokenSnapshot:
    try:
        slot = to_int(held["context"]["slot"])
        totals: dict[str, int] = {}
        for entry in held["value"]:
            info = entry["account"]["data"]["parsed"]["info"]
            mint = solana_address(info["mint"])
            totals[mint] = totals.get(mint, 0) + int(raw_amount(info["tokenAmount"]["amount"]))
    except (KeyError, TypeError, MalformedRow) as error:
        raise ProviderBadResponse(NAME, "unexpected response shape") from error
    return TokenSnapshot(slot, {mint: str(total) for mint, total in totals.items()})


__all__ = ["HeliusSource", "helius_body_error"]
