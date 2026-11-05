"""Bitcoin records: transactions made of inputs and outputs (blueprint section 4.5).

Bitcoin has no accounts and no transfers from one address to another. A transaction destroys some
unspent outputs (its inputs) and creates new ones (its outputs); which input paid which output is
not recorded anywhere, and anything that says otherwise is an inference. So these records state only
what the chain proves, keep inputs and outputs apart, and leave every inference to `utxo.py`, where
it is labelled as one.

Amounts are integers of satoshis (1 BTC = 10**8), carried as strings like every other amount.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.chains.models import Confirmation, DataQuality, Page, iso


class SpendState(StrEnum):
    SPENT = "spent"  # spent_by_txid says by what
    UNSPENT = "unspent"  # known to be unspent
    UNSPENDABLE = "unspendable"  # an OP_RETURN output: it can never be spent
    UNKNOWN = "unknown"  # nobody has said, which is not the same as unspent


@dataclass(frozen=True, slots=True)
class TxInput:
    """An output of an earlier transaction, destroyed by this one."""

    index: int
    # What it spends. None for a coinbase input, which creates new coins instead.
    previous_txid: str | None
    previous_output_index: int | None
    # Where the coins came from. None when the spent output has no address form (an old pay-to-key
    # output, a bare multisig, a non-standard script) and for a coinbase.
    source_address: str | None
    value_raw: str | None  # satoshis; None for a coinbase
    script_type: (
        str | None
    )  # the provider's name for the spent script: p2pkh, v0_p2wpkh, v1_p2tr ...
    is_coinbase: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "previous_txid": self.previous_txid,
            "previous_output_index": self.previous_output_index,
            "source_address": self.source_address,
            "value_raw": self.value_raw,
            "script_type": self.script_type,
            "is_coinbase": self.is_coinbase,
        }


@dataclass(frozen=True, slots=True)
class TxOutput:
    """A new unspent output created by a transaction."""

    index: int
    destination_address: str | None  # None for OP_RETURN and for scripts with no address form
    value_raw: str  # satoshis
    script_type: str
    spend_state: SpendState = SpendState.UNKNOWN
    spent_by_txid: str | None = None
    spent_by_input_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "destination_address": self.destination_address,
            "value_raw": self.value_raw,
            "script_type": self.script_type,
            "spend_state": self.spend_state.value,
            "spent_by_txid": self.spent_by_txid,
            "spent_by_input_index": self.spent_by_input_index,
        }


@dataclass(frozen=True, slots=True)
class BitcoinTransaction:
    chain_id: str
    txid: str
    # None while the transaction is only in the mempool.
    block_height: int | None
    block_hash: str | None
    timestamp: datetime | None  # the block's time
    inputs: tuple[TxInput, ...]
    outputs: tuple[TxOutput, ...]
    fee_raw: str | None  # None for a coinbase, which pays no fee
    size: int  # bytes
    weight: int  # weight units; the virtual size is a quarter of it, rounded up
    version: int
    locktime: int
    provider: str
    data_quality: DataQuality
    confirmation_status: Confirmation = Confirmation.UNKNOWN
    confirmations: int | None = None

    @property
    def is_coinbase(self) -> bool:
        return any(item.is_coinbase for item in self.inputs)

    @property
    def vsize(self) -> int:
        return -(-self.weight // 4)

    @property
    def identity(self) -> tuple[str, str]:
        return (self.chain_id, self.txid)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "txid": self.txid,
            "block_height": self.block_height,
            "block_hash": self.block_hash,
            "timestamp": iso(self.timestamp),
            "inputs": [item.to_dict() for item in self.inputs],
            "outputs": [item.to_dict() for item in self.outputs],
            "fee_raw": self.fee_raw,
            "size": self.size,
            "vsize": self.vsize,
            "weight": self.weight,
            "version": self.version,
            "locktime": self.locktime,
            "is_coinbase": self.is_coinbase,
            "confirmation_status": self.confirmation_status.value,
            "confirmations": self.confirmations,
            "provider": self.provider,
            "data_quality": self.data_quality.value,
        }


@dataclass(frozen=True, slots=True)
class AddressSummary:
    """What a provider counts for an address, to check a history against."""

    address: str
    tx_count: int  # confirmed transactions that pay or spend it
    funded_count: int  # outputs ever paid to it
    funded_raw: str  # their total
    spent_count: int
    spent_raw: str  # the total of the ones since spent
    mempool_tx_count: int
    provider: str

    @property
    def balance_raw(self) -> str:
        return str(int(self.funded_raw) - int(self.spent_raw))

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "tx_count": self.tx_count,
            "funded_count": self.funded_count,
            "funded_raw": self.funded_raw,
            "spent_count": self.spent_count,
            "spent_raw": self.spent_raw,
            "balance_raw": self.balance_raw,
            "mempool_tx_count": self.mempool_tx_count,
            "provider": self.provider,
        }


@dataclass(frozen=True, slots=True)
class BitcoinDetail:
    """One transaction and what this answer does not include, in words."""

    transaction: BitcoinTransaction
    coverage: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {"transaction": self.transaction.to_dict(), "coverage": list(self.coverage)}


# One page of an address's transactions, newest first as providers give them.
UtxoPage = Page[BitcoinTransaction]
