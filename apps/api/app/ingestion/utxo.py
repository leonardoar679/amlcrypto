"""Reading a whole Bitcoin address history through an adapter.

Same rules as the account-model fetcher (app.ingestion.fetcher): read to the end or to a limit,
say what could not be read, never let a history with a gap look complete, resume where a limit
stopped. What differs is the record (a transaction with inputs and outputs, not transfers between
accounts) and the direction: Esplora lists newest first, so a read that stops early holds the NEWEST
part of the history and `resume` carries on towards the oldest.

A whole-history read is also checked against the node's own figures for the address: the number of
transactions, the total ever paid to it and the total ever spent from it. A history that disagrees
is not called complete.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.chains.bitcoin.adapter import BitcoinAdapter
from app.chains.bitcoin.chains import BitcoinChain
from app.chains.bitcoin.models import AddressSummary, BitcoinTransaction
from app.chains.bitcoin.normalize import with_head
from app.chains.bitcoin.utxo import (
    AddressActivity,
    address_activity,
    link_spends,
    order_transactions,
)
from app.chains.models import iso
from app.ingestion.partial import attach_partial
from app.providers.base import BudgetExceeded, ProviderError

STREAM = "transactions"


@dataclass(slots=True)
class UtxoHistory:
    chain_id: str
    address: str
    transactions: list[BitcoinTransaction]  # oldest first
    activity: dict[str, AddressActivity]  # what the address did in each, by txid
    coverage: list[str]  # what is missing or doubtful, in words
    # Every page was read to the end and the node's own figures agree (when they could be checked).
    complete: bool
    head_block: int | None
    synced_through_block: int | None  # when complete: every block up to here has been read
    resume: dict[str, str] = field(default_factory=dict)
    provider_summary: AddressSummary | None = None  # the node's own figures, when checked

    @property
    def received_raw(self) -> str:
        return str(sum(int(item.received_raw) for item in self.activity.values()))

    @property
    def sent_raw(self) -> str:
        return str(sum(int(item.sent_raw) for item in self.activity.values()))

    def summary(self, *, latest: int = 5) -> dict[str, Any]:
        """A compact, JSON-ready description (a job result must not hold the whole history)."""
        heights = [tx.block_height for tx in self.transactions if tx.block_height is not None]
        return {
            "chain_id": self.chain_id,
            "address": self.address,
            "complete": self.complete,
            "head_block": self.head_block,
            "synced_through_block": self.synced_through_block,
            "transactions": len(self.transactions),
            "first_block": min(heights, default=None),
            "last_block": max(heights, default=None),
            "received_raw": self.received_raw,
            "sent_raw": self.sent_raw,
            "providers": sorted({tx.provider for tx in self.transactions}),
            "provider_summary": None
            if self.provider_summary is None
            else self.provider_summary.to_dict(),
            "coverage": list(self.coverage),
            "resume": dict(self.resume),
            "latest": [self.entry(tx) for tx in self.transactions[-latest:]] if latest else [],
        }

    def entry(self, tx: BitcoinTransaction) -> dict[str, Any]:
        """A transaction in brief, with what the address did in it."""
        return {
            "txid": tx.txid,
            "block_height": tx.block_height,
            "timestamp": iso(tx.timestamp),
            "activity": self._activity_dict(tx),
        }

    def detailed(self, tx: BitcoinTransaction) -> dict[str, Any]:
        """A transaction in full, with what the address did in it."""
        return {**tx.to_dict(), "activity": self._activity_dict(tx)}

    def _activity_dict(self, tx: BitcoinTransaction) -> dict[str, Any] | None:
        mine = self.activity.get(tx.txid)
        return None if mine is None else mine.to_dict()


async def fetch_utxo_history(
    adapter: BitcoinAdapter,
    address: str,
    *,
    from_block: int | None = None,
    to_block: int | None = None,
    max_items: int = 5000,
    resume: dict[str, str] | None = None,
    verify: bool = True,
) -> UtxoHistory:
    coverage: list[str] = []
    try:
        head, settled = await adapter.get_heights()
    except ProviderError as error:
        head = settled = None
        coverage.append(f"the chain's current height is unknown ({error.reason})")

    # Only a read of the entire history can be checked against the node's totals. A caller that
    # puts histories together from several reads checks the result itself (`verify=False`).
    whole = from_block is None and to_block is None and resume is None
    expected: AddressSummary | None = None
    if whole and verify:
        try:
            expected = await adapter.get_address_summary(address)
        except ProviderError as error:
            coverage.append(
                f"the provider's own figures for this address could not be read ({error.reason}), "
                "so the history was not checked against them"
            )

    found: dict[str, BitcoinTransaction] = {}
    unfinished: dict[str, str] = {}
    complete = True
    cursor = (resume or {}).get(STREAM) or None
    try:
        while True:
            page = await adapter.get_address_page(
                address, cursor=cursor, from_block=from_block, to_block=to_block
            )
            for tx in page.items:
                found.setdefault(tx.txid, tx)
            _remember(coverage, page.coverage)
            if page.truncated:
                complete = False
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
            if len(found) >= max_items:
                unfinished[STREAM] = cursor
                complete = False
                _remember(
                    coverage,
                    (
                        f"stopped after {len(found)} transactions, the newest ones; resume to "
                        "carry on towards the oldest",
                    ),
                )
                break
    except BudgetExceeded:
        # The page that was refused has not been read: resume from the cursor that asked for it.
        unfinished[STREAM] = cursor or ""
        complete = False
        _remember(coverage, ("stopped: the request budget for this read was used up",))
    except ProviderError as error:
        # The pages already read are good data that cost real quota: hand them to a caller that
        # keeps histories. The failed page is the one to read again.
        unfinished[STREAM] = cursor or ""
        _remember(coverage, (f"stopped: {error.reason}",))
        transactions, activity = assemble_utxo(
            found.values(), head=head, chain=adapter.chain, address=address, whole=False
        )
        attach_partial(
            error,
            UtxoHistory(
                chain_id=adapter.chain_id,
                address=address,
                transactions=transactions,
                activity=activity,
                coverage=coverage,
                complete=False,
                head_block=head,
                synced_through_block=None,
                resume=unfinished,
            ),
        )
        raise

    # With the whole confirmed history in hand, an output of the address nobody spent is unspent.
    transactions, activity = assemble_utxo(
        found.values(), head=head, chain=adapter.chain, address=address, whole=whole and complete
    )

    if verify and whole and complete and expected is not None:
        expected, agrees = await _check_against_node(
            adapter, address, transactions, activity, expected, coverage
        )
        complete = agrees

    return UtxoHistory(
        chain_id=adapter.chain_id,
        address=address,
        transactions=transactions,
        activity=activity,
        coverage=coverage,
        complete=complete,
        head_block=head,
        synced_through_block=(to_block if to_block is not None else settled) if complete else None,
        resume=unfinished,
        provider_summary=expected,
    )


def assemble_utxo(
    found: Iterable[BitcoinTransaction],
    *,
    head: int | None,
    chain: BitcoinChain,
    address: str,
    whole: bool,
) -> tuple[list[BitcoinTransaction], dict[str, AddressActivity]]:
    """Confirmations, spends and order for transactions just read or loaded from storage, and what
    the address did in each. `whole` says they are the address's complete confirmed history, in
    which case an output of the address that none of them spends is proven unspent."""
    transactions = [with_head(tx, head, chain) for tx in found]
    transactions = link_spends(transactions, complete_for=address if whole else None)
    transactions = order_transactions(transactions)
    activity = {
        tx.txid: mine for tx in transactions if (mine := address_activity(tx, address)) is not None
    }
    return transactions, activity


async def verify_utxo(
    adapter: BitcoinAdapter,
    address: str,
    transactions: list[BitcoinTransaction],
    activity: dict[str, AddressActivity],
    coverage: list[str],
) -> tuple[AddressSummary | None, bool]:
    """Check a whole history against the node's own figures for the address.

    Returns the figures (None if they could not be read, which is said in `coverage`) and whether
    the history agrees with them; a history that could not be checked is not called wrong.
    """
    try:
        expected = await adapter.get_address_summary(address)
    except ProviderError as error:
        coverage.append(
            f"the provider's own figures for this address could not be read ({error.reason}), "
            "so the history was not checked against them"
        )
        return None, True
    return await _check_against_node(adapter, address, transactions, activity, expected, coverage)


async def _check_against_node(
    adapter: BitcoinAdapter,
    address: str,
    transactions: list[BitcoinTransaction],
    activity: dict[str, AddressActivity],
    expected: AddressSummary,
    coverage: list[str],
) -> tuple[AddressSummary, bool]:
    """Compare what was read with the node's count and totals; say so loudly if they differ."""
    if not _agrees(transactions, activity, expected):
        # A block may have arrived between asking for the figures and reading the first page, in
        # which case the node's newer figures include what the read already holds.
        with contextlib.suppress(ProviderError):
            expected = await adapter.get_address_summary(address)
    agrees = _agrees(transactions, activity, expected)
    if not agrees:
        received = sum(int(item.received_raw) for item in activity.values())
        sent = sum(int(item.sent_raw) for item in activity.values())
        coverage.append(
            f"MISMATCH with the provider's own figures: it counts {expected.tx_count} "
            f"transactions, received {expected.funded_raw} and spent {expected.spent_raw}; "
            f"this read holds {len(transactions)}, received {received} and sent {sent} (satoshis)"
        )
    if expected.mempool_tx_count:
        coverage.append(
            f"{expected.mempool_tx_count} unconfirmed transactions involve this address and are "
            "not included; an output shown unspent may be about to be spent"
        )
    return expected, agrees


def _agrees(
    transactions: list[BitcoinTransaction],
    activity: dict[str, AddressActivity],
    expected: AddressSummary,
) -> bool:
    received = sum(int(item.received_raw) for item in activity.values())
    sent = sum(int(item.sent_raw) for item in activity.values())
    return (
        len(transactions) == expected.tx_count
        and received == int(expected.funded_raw)
        and sent == int(expected.spent_raw)
    )


def _remember(coverage: list[str], notes: tuple[str, ...]) -> None:
    for note in notes:
        if note not in coverage:
            coverage.append(note)
