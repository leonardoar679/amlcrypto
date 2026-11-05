"""Solana records: a transaction, the value it moved, and what each account's balance did.

Solana is account-based, so what moves is a `Transfer` like on the other account chains. What is
different is how a transfer is found. Value moves between TOKEN ACCOUNTS, each owned by a wallet;
most of it moves inside instructions that other programs call (a swap is a dozen of them); and a
program can change an account's SOL balance without any instruction saying so. So a transaction
carries three views of the same event:

* `transfers`: what the instructions say moved, between wallets, with the token accounts and the
  instruction position behind each;
* `sol_changes` and `token_changes`: what the chain says every balance did, which is the ground
  truth whatever the instructions say;
* `unattributed`: SOL that moved with no instruction to explain it, so that nothing is hidden.

Amounts are integers in the asset's smallest unit (lamports for SOL, 1 SOL = 10**9), as strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.chains.models import Confirmation, DataQuality, Transfer, TxStatus, iso


@dataclass(frozen=True, slots=True)
class SolChange:
    """What one account's SOL balance did over a transaction (fees included)."""

    account: str
    before_raw: str
    after_raw: str

    @property
    def delta_raw(self) -> str:
        return str(int(self.after_raw) - int(self.before_raw))

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "before_raw": self.before_raw,
            "after_raw": self.after_raw,
            "delta_raw": self.delta_raw,
        }


@dataclass(frozen=True, slots=True)
class TokenChange:
    """What one token account's balance did over a transaction."""

    account: str  # the token account
    owner: str | None  # the wallet that owns it, as the transaction's balances say
    mint: str
    decimals: int
    program_id: str | None  # the Token or Token-2022 program
    before_raw: str  # "0" when the account did not exist before
    after_raw: str  # "0" when it was closed

    @property
    def delta_raw(self) -> str:
        return str(int(self.after_raw) - int(self.before_raw))

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "owner": self.owner,
            "mint": self.mint,
            "decimals": self.decimals,
            "program_id": self.program_id,
            "before_raw": self.before_raw,
            "after_raw": self.after_raw,
            "delta_raw": self.delta_raw,
        }


@dataclass(frozen=True, slots=True)
class Unattributed:
    """A balance change that no visible instruction explains.

    A program can move lamports by editing accounts directly, and a token balance can change
    through an instruction this reader does not know. The change is real; only its counterparty and
    cause are missing. `amount_raw` is signed: how much more (or less) the account holds than the
    instructions account for.
    """

    kind: str  # "sol" or "token"
    account: str
    owner: str | None  # for a token account, the wallet that owns it
    mint: str | None  # for a token account
    amount_raw: str  # signed

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "account": self.account,
            "owner": self.owner,
            "mint": self.mint,
            "amount_raw": self.amount_raw,
        }


@dataclass(frozen=True, slots=True)
class SolanaTransaction:
    chain_id: str
    signature: str
    slot: int
    timestamp: datetime | None
    version: str  # "legacy", "0" or "1"
    status: TxStatus
    error: str | None  # what the chain said went wrong, when it failed
    fee_raw: str
    fee_payer: str
    signers: tuple[str, ...]
    programs: tuple[str, ...]  # the programs the transaction called directly, in order, once each
    transfers: tuple[Transfer, ...]
    sol_changes: tuple[SolChange, ...]  # accounts whose SOL balance changed
    token_changes: tuple[TokenChange, ...]
    # Balance changes (the fee excluded) that no instruction accounts for. Real movements with no
    # known counterparty: a program editing lamports directly, or an instruction not understood.
    unattributed: tuple[Unattributed, ...]
    # Instructions that move value in a shape this reader does not know.
    unreadable: int
    provider: str
    data_quality: DataQuality
    transaction_index: int | None = None  # the position in the block, when the provider says
    confirmation_status: Confirmation = Confirmation.CONFIRMED  # always read at `finalized`
    # Token accounts of this transaction whose owner it does not show.
    unresolved_owners: int = 0

    @property
    def identity(self) -> tuple[str, str]:
        return (self.chain_id, self.signature)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "signature": self.signature,
            "slot": self.slot,
            "transaction_index": self.transaction_index,
            "timestamp": iso(self.timestamp),
            "version": self.version,
            "status": self.status.value,
            "error": self.error,
            "fee_raw": self.fee_raw,
            "fee_payer": self.fee_payer,
            "signers": list(self.signers),
            "programs": list(self.programs),
            "transfers": [transfer.to_dict() for transfer in self.transfers],
            "sol_changes": [item.to_dict() for item in self.sol_changes],
            "token_changes": [item.to_dict() for item in self.token_changes],
            "unattributed": [item.to_dict() for item in self.unattributed],
            "unreadable": self.unreadable,
            "unresolved_owners": self.unresolved_owners,
            "confirmation_status": self.confirmation_status.value,
            "provider": self.provider,
            "data_quality": self.data_quality.value,
        }


@dataclass(frozen=True, slots=True)
class SolanaDetail:
    """One transaction and what this answer does not include, in words."""

    transaction: SolanaTransaction
    coverage: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {"transaction": self.transaction.to_dict(), "coverage": list(self.coverage)}


@dataclass(frozen=True, slots=True)
class TokenSnapshot:
    """What a wallet's token accounts under one token program held, as of a slot."""

    slot: int
    balances: dict[str, str]  # mint -> total across the wallet's token accounts, raw


@dataclass(frozen=True, slots=True)
class WalletFigures:
    """What the chain says a wallet holds now, to check a history against.

    Each figure carries the slot it is as of, because a wallet that transacts while it is being read
    must not make a correct history look wrong: only transactions up to that slot are compared.
    """

    address: str
    sol_raw: str
    sol_slot: int
    tokens: dict[str, TokenSnapshot]  # by token program id (Token and Token-2022 are read apart)
    provider: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WalletFigures:
        """The figures as `to_dict` wrote them (a check that was stored with a history)."""
        return cls(
            address=data["address"],
            sol_raw=data["sol_raw"],
            sol_slot=data["sol_slot"],
            tokens={
                program: TokenSnapshot(snapshot["slot"], dict(snapshot["balances"]))
                for program, snapshot in data["tokens"].items()
            },
            provider=data["provider"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "sol_raw": self.sol_raw,
            "sol_slot": self.sol_slot,
            "tokens": {
                program: {"slot": snapshot.slot, "balances": dict(snapshot.balances)}
                for program, snapshot in self.tokens.items()
            },
            "provider": self.provider,
        }
