"""Reading a whole Solana wallet history through an adapter.

The same rules as the other readers: read to the end or to a limit, say what could not be read,
never let a history with a gap look complete, resume where a limit stopped. Pages come newest first,
so a read that stops early holds the NEWEST part and `resume` carries on towards the oldest.

A whole-history read is checked against the chain: what the wallet's balance changes add up to over
its history must be exactly what the chain says it holds now, in SOL and in every token. A
transaction that was missed breaks that sum, however it was missed. Each balance is compared only
up to the slot it was read at, so a wallet that transacts during the read is not wrongly accused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.chains.models import Transfer, iso
from app.chains.solana.activity import WalletActivity, wallet_activity
from app.chains.solana.adapter import SolanaAdapter
from app.chains.solana.models import SolanaTransaction, WalletFigures
from app.providers.base import BudgetExceeded, ProviderError

STREAM = "transactions"


@dataclass(slots=True)
class SolanaHistory:
    chain_id: str
    address: str
    transactions: list[SolanaTransaction]  # oldest first
    activity: dict[str, WalletActivity]  # what the wallet did in each, by signature
    coverage: list[str]  # what is missing or doubtful, in words
    # Every page was read to the end and the chain's own balances agree (if they could be checked).
    complete: bool
    head_block: int | None  # the newest finalized slot when the read began
    synced_through_block: int | None  # when complete: every slot up to here has been read
    resume: dict[str, str] = field(default_factory=dict)
    figures: WalletFigures | None = None  # what the chain says the wallet holds, when checked

    def wallet_transfers(self) -> list[Transfer]:
        """The decoded movements that involve this wallet (the transactions hold all of them)."""
        return [
            transfer
            for tx in self.transactions
            for transfer in tx.transfers
            if self.address in (transfer.from_address, transfer.to_address)
        ]

    def summary(self, *, latest: int = 5) -> dict[str, Any]:
        """A compact, JSON-ready description (a job result must not hold the whole history)."""
        slots = [tx.slot for tx in self.transactions]
        by_type: dict[str, int] = {}
        for transfer in self.wallet_transfers():
            by_type[transfer.transfer_type.value] = by_type.get(transfer.transfer_type.value, 0) + 1
        return {
            "chain_id": self.chain_id,
            "address": self.address,
            "complete": self.complete,
            "head_block": self.head_block,
            "synced_through_block": self.synced_through_block,
            "transactions": len(self.transactions),
            "failed": sum(1 for tx in self.transactions if tx.status.value == "failed"),
            "wallet_transfers": sum(by_type.values()),
            "transfers_by_type": by_type,
            "first_block": min(slots, default=None),
            "last_block": max(slots, default=None),
            "providers": sorted({tx.provider for tx in self.transactions}),
            "figures": None if self.figures is None else self.figures.to_dict(),
            "coverage": list(self.coverage),
            "resume": dict(self.resume),
            "latest": [self.entry(tx) for tx in self.transactions[-latest:]] if latest else [],
        }

    def entry(self, tx: SolanaTransaction) -> dict[str, Any]:
        """A transaction in brief, with what the wallet did in it."""
        return {
            "signature": tx.signature,
            "slot": tx.slot,
            "timestamp": iso(tx.timestamp),
            "status": tx.status.value,
            "activity": self.activity[tx.signature].to_dict(),
        }

    def detailed(self, tx: SolanaTransaction) -> dict[str, Any]:
        """A transaction in full, with what the wallet did in it."""
        return {**tx.to_dict(), "activity": self.activity[tx.signature].to_dict()}


async def fetch_solana_history(
    adapter: SolanaAdapter,
    address: str,
    *,
    from_block: int | None = None,
    to_block: int | None = None,
    max_items: int = 5000,
    resume: dict[str, str] | None = None,
) -> SolanaHistory:
    coverage: list[str] = []
    try:
        head, settled = await adapter.get_heights()
    except ProviderError as error:
        head = settled = None
        coverage.append(f"the chain's current height is unknown ({error.reason})")

    found: dict[str, SolanaTransaction] = {}
    unfinished: dict[str, str] = {}
    complete = True
    cursor = (resume or {}).get(STREAM) or None
    try:
        while True:
            page = await adapter.get_address_page(
                address, cursor=cursor, from_block=from_block, to_block=to_block
            )
            for tx in page.items:
                found.setdefault(tx.signature, tx)
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

    transactions = sorted(
        found.values(), key=lambda tx: (tx.slot, tx.transaction_index or 0, tx.signature)
    )
    activity = {tx.signature: wallet_activity(tx, address) for tx in transactions}
    _note_limits(coverage, transactions, address)

    # Only a read of the entire history can be checked against what the chain holds now.
    whole = from_block is None and to_block is None and resume is None
    figures: WalletFigures | None = None
    if whole and complete:
        try:
            figures = await adapter.get_wallet_figures(address)
        except ProviderError as error:
            coverage.append(
                f"the chain's own balances for this wallet could not be read ({error.reason}), so "
                "the history was not checked against them"
            )
        else:
            mismatches = _check_against_chain(address, transactions, activity, figures)
            coverage.extend(mismatches)
            complete = not mismatches

    return SolanaHistory(
        chain_id=adapter.chain_id,
        address=address,
        transactions=transactions,
        activity=activity,
        coverage=coverage,
        complete=complete,
        head_block=head,
        synced_through_block=(to_block if to_block is not None else settled) if complete else None,
        resume=unfinished,
        figures=figures,
    )


def _check_against_chain(
    address: str,
    transactions: list[SolanaTransaction],
    activity: dict[str, WalletActivity],
    figures: WalletFigures,
) -> list[str]:
    """MISMATCH notes for every balance the history does not add up to; none when all agree."""
    notes = []
    sol = sum(
        int(activity[tx.signature].sol_delta_raw)
        for tx in transactions
        if tx.slot <= figures.sol_slot
    )
    if sol != int(figures.sol_raw):
        notes.append(
            f"MISMATCH with the chain's own balance: the history adds up to {sol} lamports of SOL, "
            f"the chain says {figures.sol_raw} (as of slot {figures.sol_slot})"
        )
    for program, snapshot in figures.tokens.items():
        summed: dict[str, int] = {}
        for tx in transactions:
            if tx.slot > snapshot.slot:
                continue
            for change in tx.token_changes:
                if change.owner == address and change.program_id == program:
                    summed[change.mint] = summed.get(change.mint, 0) + int(change.delta_raw)
        for mint in sorted(set(summed) | set(snapshot.balances)):
            expected, actual = summed.get(mint, 0), int(snapshot.balances.get(mint, "0"))
            if expected != actual:
                notes.append(
                    f"MISMATCH with the chain's own balance of token {mint}: the history adds "
                    f"up to {expected} (raw), the chain says {actual} (as of slot {snapshot.slot})"
                )
    return notes


def _note_limits(coverage: list[str], transactions: list[SolanaTransaction], wallet: str) -> None:
    """What the instruction view of these transactions cannot show, once, in words."""
    # Other accounts' unexplained changes (a pool's) are not this wallet's concern; its own are.
    unattributed = sum(
        1
        for tx in transactions
        if any(wallet in (item.account, item.owner) for item in tx.unattributed)
    )
    if unattributed:
        _remember(
            coverage,
            (
                f"{unattributed} transactions changed this wallet's balance with no instruction to "
                "explain it (a program moved SOL or tokens directly); the wallet's net change in "
                "each is exact, but the listed transfers do not show where that value came from",
            ),
        )
    unreadable = sum(tx.unreadable for tx in transactions)
    if unreadable:
        _remember(
            coverage,
            (f"{unreadable} instructions move value in a shape this reader does not know",),
        )
    unresolved = sum(1 for tx in transactions if tx.unresolved_owners)
    if unresolved:
        _remember(
            coverage,
            (
                f"{unresolved} transactions have token accounts whose owner they do not show; "
                "the account itself stands in for its owner",
            ),
        )


def _remember(coverage: list[str], notes: tuple[str, ...]) -> None:
    for note in notes:
        if note not in coverage:
            coverage.append(note)
