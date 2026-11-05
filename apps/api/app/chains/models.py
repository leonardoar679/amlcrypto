"""The chain-independent records every connector produces (blueprint section 4.4).

Providers disagree about names, units and what a "transaction" is. Connectors map whatever they
get into these records and nothing else in the platform sees a provider's shape.

Amounts are integers in the asset's smallest unit, carried as strings (`amount_raw`): a uint256
does not fit a float, a 64-bit integer or a MySQL DECIMAL. Addresses are in the chain's storage
form (lowercase for EVM). Timestamps are timezone-aware UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class TransferType(StrEnum):
    NATIVE = "native"  # the chain's own coin, sent by a transaction
    INTERNAL = "internal"  # the chain's own coin, moved by a contract during a transaction
    ERC20 = "erc20"  # a fungible token event (BEP-20 on BNB Smart Chain)


class DataQuality(StrEnum):
    INDEXED = "indexed"  # as reported by an indexing provider; not checked against the chain
    DECODED = "decoded"  # decoded by us from the chain's own receipt


class Confirmation(StrEnum):
    CONFIRMED = "confirmed"  # at least the chain's confirmation depth
    UNCONFIRMED = "unconfirmed"  # mined, but still shallow enough to be reorganised
    UNKNOWN = "unknown"  # the provider did not say how deep it is


class TxStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Asset:
    symbol: str | None
    contract_address: str | None  # None for the chain's native coin
    decimals: int | None  # None when the provider did not say
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "contract_address": self.contract_address,
            "decimals": self.decimals,
        }


@dataclass(frozen=True, slots=True)
class Transfer:
    """One movement of value from one address to another."""

    chain_id: str
    transaction_hash: str
    # Identifies the movement inside its transaction, the same way whichever provider reported it.
    # See app.chains.evm.normalize for how it is built; (chain_id, transaction_hash, event_key)
    # is the deduplication key.
    event_key: str
    transfer_type: TransferType
    block_number: int
    timestamp: datetime | None
    from_address: str | None
    to_address: str | None  # None only when a transaction creates a contract with no value
    asset: Asset
    amount_raw: str
    provider: str
    data_quality: DataQuality
    confirmation_status: Confirmation = Confirmation.UNKNOWN
    confirmations: int | None = None
    transaction_index: int | None = None
    log_index: int | None = None  # the position of the token event in the block, when known
    trace_id: str | None = None  # the position of an internal call in its transaction's trace

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.chain_id, self.transaction_hash, self.event_key)

    def to_dict(self) -> dict[str, Any]:
        """The shape of the blueprint's example, plus the fields that make it self-describing."""
        return {
            "chain_id": self.chain_id,
            "transaction_hash": self.transaction_hash,
            "event_key": self.event_key,
            "event_index": self.log_index,
            "trace_id": self.trace_id,
            "block_number": self.block_number,
            "transaction_index": self.transaction_index,
            "timestamp": _iso(self.timestamp),
            "from_address": self.from_address,
            "to_address": self.to_address,
            "asset": self.asset.to_dict(),
            "amount_raw": self.amount_raw,
            "transfer_type": self.transfer_type.value,
            "confirmation_status": self.confirmation_status.value,
            "confirmations": self.confirmations,
            "provider": self.provider,
            "data_quality": self.data_quality.value,
        }


@dataclass(frozen=True, slots=True)
class Transaction:
    """A transaction as an account sees it: sent, received or created by it."""

    chain_id: str
    transaction_hash: str
    block_number: int
    timestamp: datetime | None
    from_address: str
    to_address: str | None  # None for a contract creation
    created_contract: str | None
    value_raw: str  # the native coin sent with it; 0 for most contract calls
    status: TxStatus
    provider: str
    data_quality: DataQuality
    fee_raw: str | None = None  # gas used times the price paid, in the native coin's smallest unit
    method_id: str | None = None
    function_name: str | None = None
    is_contract_call: bool = False
    confirmation_status: Confirmation = Confirmation.UNKNOWN
    confirmations: int | None = None
    transaction_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "transaction_hash": self.transaction_hash,
            "block_number": self.block_number,
            "transaction_index": self.transaction_index,
            "timestamp": _iso(self.timestamp),
            "from_address": self.from_address,
            "to_address": self.to_address,
            "created_contract": self.created_contract,
            "value_raw": self.value_raw,
            "status": self.status.value,
            "fee_raw": self.fee_raw,
            "method_id": self.method_id,
            "function_name": self.function_name,
            "is_contract_call": self.is_contract_call,
            "confirmation_status": self.confirmation_status.value,
            "confirmations": self.confirmations,
            "provider": self.provider,
            "data_quality": self.data_quality.value,
        }


@dataclass(frozen=True, slots=True)
class TransactionDetail:
    transaction: Transaction
    transfers: tuple[Transfer, ...]
    # What this answer does not include, in words. A missing finding is not a clean finding.
    coverage: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction": self.transaction.to_dict(),
            "transfers": [transfer.to_dict() for transfer in self.transfers],
            "coverage": list(self.coverage),
        }


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    """One page of a longer history. `next_cursor` is None when the history is exhausted."""

    items: list[T]
    next_cursor: str | None
    # True when the provider's window ran out before the history did: more exists but cannot be
    # reached with this query.
    truncated: bool = False
    coverage: tuple[str, ...] = field(default=())
    # True when no configured source can provide this kind of record on this chain at all. The
    # empty page is then a gap, not an empty history.
    unavailable: bool = False


@dataclass(frozen=True, slots=True)
class AccountPage:
    """One page of an address's normal transactions, and the coin each of them sent.

    Both come from the same request, so they are delivered together instead of fetching twice.
    """

    transactions: list[Transaction]
    native_transfers: list[Transfer]
    next_cursor: str | None
    truncated: bool = False
    coverage: tuple[str, ...] = field(default=())


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
