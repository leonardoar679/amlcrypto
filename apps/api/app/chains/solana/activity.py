"""What one wallet did in a Solana transaction, from the chain's own balance changes.

The balances in a transaction's `meta` are the ground truth: whatever the instructions say, the
wallet's SOL balance went from one number to another, and the token accounts it owns did too. So
this counts those, not instructions, and is correct even for a swap that moves tokens through ten
programs or a program that pays out lamports without any instruction saying so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.chains.solana.models import SolanaTransaction


@dataclass(frozen=True, slots=True)
class WalletActivity:
    signature: str
    sol_delta_raw: str  # the wallet's SOL balance change, fee included
    fee_paid_raw: str  # the fee, if this wallet paid it; "0" otherwise
    # Net change per token across all token accounts the wallet owns: (mint, decimals, delta).
    token_deltas: tuple[tuple[str, int, str], ...]
    signed: bool  # the wallet was a signer

    @property
    def role(self) -> str:
        """ "sender", "receiver" or "both" by the direction value moved; "signer" or "involved"
        when only the fee, or nothing, changed."""
        deltas = [int(self.sol_delta_raw) + int(self.fee_paid_raw)]  # SOL without the fee
        deltas += [int(delta) for _, _, delta in self.token_deltas]
        out, into = any(delta < 0 for delta in deltas), any(delta > 0 for delta in deltas)
        if out and into:
            return "both"
        if out:
            return "sender"
        if into:
            return "receiver"
        return "signer" if self.signed else "involved"

    def to_dict(self) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "sol_delta_raw": self.sol_delta_raw,
            "fee_paid_raw": self.fee_paid_raw,
            "role": self.role,
            "signed": self.signed,
            "token_deltas": [
                {"mint": mint, "decimals": decimals, "delta_raw": delta}
                for mint, decimals, delta in self.token_deltas
            ],
        }


def wallet_activity(tx: SolanaTransaction, wallet: str) -> WalletActivity:
    """The wallet's part in a transaction (every transaction in its history has one)."""
    sol = next((int(change.delta_raw) for change in tx.sol_changes if change.account == wallet), 0)
    tokens: dict[str, list[int]] = {}
    for change in tx.token_changes:
        if change.owner == wallet:
            entry = tokens.setdefault(change.mint, [change.decimals, 0])
            entry[1] += int(change.delta_raw)
    return WalletActivity(
        signature=tx.signature,
        sol_delta_raw=str(sol),
        fee_paid_raw=tx.fee_raw if tx.fee_payer == wallet else "0",
        token_deltas=tuple(
            (mint, decimals, str(delta)) for mint, (decimals, delta) in tokens.items() if delta
        ),
        signed=wallet in tx.signers,
    )
