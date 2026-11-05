"""What the connectors read from the chains, stored once per chain identity.

These tables are global, not tenant-owned: a transfer is a fact about a chain, and the same one is
reached from both of its addresses. Every identifier carries its chain id and is unique within it,
so reading an address again (or reading its counterparty) never stores a record twice.

* `transactions` holds a transaction as every family sees it: an account's own transaction
  (Ethereum, BNB Smart Chain, TRON), a Bitcoin transaction, a Solana transaction. The columns that
  belong to one family are NULL for the others.
* `transfers` is the movement of value between two addresses (account chains and Solana). Its
  identity is (chain, transaction, event key); see app.chains.models.Transfer.
* `transaction_inputs` and `transaction_outputs` are Bitcoin's coins destroyed and created.
* `balance_changes` and `unattributed_changes` are what a Solana transaction did to every balance,
  and what no instruction explains.
* `address_transactions` remembers which transactions belong to which address's history for the
  families that are read as whole transactions (Bitcoin, Solana), because that is a fact about the
  read and cannot always be derived from the transaction itself. On the account chains an address's
  history is simply every movement it is a party to.

Amounts are integer strings in the asset's smallest unit. Text that comes from a provider is
bounded by the normalizers (app.chains.values.clean_text); the repositories clamp it to the column
anyway, so a hostile token name can never make a write fail.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IntegerPK, TimestampMixin, UTCDateTime, identifier

CHAIN_ID_LENGTH = 64
ADDRESS_LENGTH = 128
HASH_LENGTH = 100  # a Solana signature is at most 88 characters, an EVM hash 66
EVENT_KEY_LENGTH = 255  # the longest, an ERC-20 key with a uint256 amount, is about 220
AMOUNT_LENGTH = 80  # a uint256 has 78 digits


class StoredTransaction(TimestampMixin, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint(
            "chain_id", "transaction_hash", name="uq_transactions_chain_id_transaction_hash"
        ),
        Index("ix_transactions_chain_id_from_address", "chain_id", "from_address"),
        Index("ix_transactions_chain_id_to_address", "chain_id", "to_address"),
        Index("ix_transactions_chain_id_created_contract", "chain_id", "created_contract"),
        Index("ix_transactions_chain_id_block_number", "chain_id", "block_number"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    chain_id: Mapped[str] = mapped_column(
        identifier(CHAIN_ID_LENGTH), ForeignKey("chains.id", ondelete="RESTRICT"), nullable=False
    )
    # The hash on Ethereum, BNB Smart Chain and TRON; the txid on Bitcoin; the signature on Solana.
    transaction_hash: Mapped[str] = mapped_column(identifier(HASH_LENGTH), nullable=False)
    # The block height; the slot on Solana. NULL while a Bitcoin transaction is in the mempool.
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    block_hash: Mapped[str | None] = mapped_column(identifier(80), nullable=True)  # Bitcoin
    transaction_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str | None] = mapped_column(String(12), nullable=True)  # NULL on Bitcoin
    from_address: Mapped[str | None] = mapped_column(identifier(ADDRESS_LENGTH), nullable=True)
    to_address: Mapped[str | None] = mapped_column(identifier(ADDRESS_LENGTH), nullable=True)
    created_contract: Mapped[str | None] = mapped_column(identifier(ADDRESS_LENGTH), nullable=True)
    value_raw: Mapped[str | None] = mapped_column(String(AMOUNT_LENGTH), nullable=True)
    fee_raw: Mapped[str | None] = mapped_column(String(AMOUNT_LENGTH), nullable=True)
    fee_payer: Mapped[str | None] = mapped_column(identifier(ADDRESS_LENGTH), nullable=True)
    method_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    function_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_contract_call: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confirmation_status: Mapped[str] = mapped_column(String(12), nullable=False)
    confirmations: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    data_quality: Mapped[str] = mapped_column(String(12), nullable=False)
    # Bitcoin
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[str | None] = mapped_column(String(12), nullable=True)  # also Solana's
    locktime: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Solana
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    signers: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    programs: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    unreadable: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unresolved_owners: Mapped[int | None] = mapped_column(Integer, nullable=True)


class StoredTransfer(TimestampMixin, Base):
    __tablename__ = "transfers"
    __table_args__ = (
        UniqueConstraint(
            "chain_id",
            "transaction_hash",
            "event_key",
            name="uq_transfers_chain_id_transaction_hash_event_key",
        ),
        Index("ix_transfers_chain_id_from_address", "chain_id", "from_address"),
        Index("ix_transfers_chain_id_to_address", "chain_id", "to_address"),
        Index("ix_transfers_chain_id_block_number", "chain_id", "block_number"),
        Index("ix_transfers_chain_id_timestamp", "chain_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    chain_id: Mapped[str] = mapped_column(
        identifier(CHAIN_ID_LENGTH), ForeignKey("chains.id", ondelete="RESTRICT"), nullable=False
    )
    # No foreign key to `transactions`: a token event is stored without the transaction that
    # emitted it unless the address read is also a party to that transaction.
    transaction_hash: Mapped[str] = mapped_column(identifier(HASH_LENGTH), nullable=False)
    event_key: Mapped[str] = mapped_column(identifier(EVENT_KEY_LENGTH), nullable=False)
    transfer_type: Mapped[str] = mapped_column(String(12), nullable=False)
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    from_address: Mapped[str | None] = mapped_column(identifier(ADDRESS_LENGTH), nullable=True)
    to_address: Mapped[str | None] = mapped_column(identifier(ADDRESS_LENGTH), nullable=True)
    # The asset as the provider reported it. The contract address (or TRC-10 id) is what identifies
    # a token; the symbol and name are display text and say nothing about which token it is.
    asset_symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    asset_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    asset_contract_address: Mapped[str | None] = mapped_column(
        identifier(ADDRESS_LENGTH), nullable=True
    )
    asset_id: Mapped[str | None] = mapped_column(identifier(64), nullable=True)
    asset_decimals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_raw: Mapped[str] = mapped_column(String(AMOUNT_LENGTH), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    data_quality: Mapped[str] = mapped_column(String(12), nullable=False)
    confirmation_status: Mapped[str] = mapped_column(String(12), nullable=False)
    confirmations: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    transaction_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Solana: the token accounts and program behind a movement between wallets.
    source_account: Mapped[str | None] = mapped_column(identifier(ADDRESS_LENGTH), nullable=True)
    destination_account: Mapped[str | None] = mapped_column(
        identifier(ADDRESS_LENGTH), nullable=True
    )
    program_id: Mapped[str | None] = mapped_column(identifier(ADDRESS_LENGTH), nullable=True)
    operation: Mapped[str | None] = mapped_column(String(32), nullable=True)


class StoredInput(Base):
    """A Bitcoin output destroyed by a transaction."""

    __tablename__ = "transaction_inputs"
    __table_args__ = (
        UniqueConstraint(
            "transaction_id", "input_index", name="uq_transaction_inputs_transaction_id_input_index"
        ),
        Index("ix_transaction_inputs_chain_id_source_address", "chain_id", "source_address"),
        Index(
            "ix_transaction_inputs_chain_id_previous_outpoint",
            "chain_id",
            "previous_txid",
            "previous_output_index",
        ),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    transaction_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False
    )
    # Always the chain of the transaction; kept here so an address can be looked up in one index.
    chain_id: Mapped[str] = mapped_column(identifier(CHAIN_ID_LENGTH), nullable=False)
    input_index: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_txid: Mapped[str | None] = mapped_column(identifier(80), nullable=True)
    previous_output_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_address: Mapped[str | None] = mapped_column(identifier(ADDRESS_LENGTH), nullable=True)
    value_raw: Mapped[str | None] = mapped_column(String(AMOUNT_LENGTH), nullable=True)
    script_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_coinbase: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class StoredOutput(Base):
    """A Bitcoin output created by a transaction.

    `spend_state` keeps only what is proven for good: SPENT (with the spender) or UNSPENDABLE, and
    is NULL when nothing is known. "Unspent" is true only as of a moment, so it is worked out again
    whenever a history is loaded and never stored; a later read that knows less (NULL) therefore
    never erases a spend an earlier one proved.
    """

    __tablename__ = "transaction_outputs"
    __table_args__ = (
        UniqueConstraint(
            "transaction_id",
            "output_index",
            name="uq_transaction_outputs_transaction_id_output_index",
        ),
        Index(
            "ix_transaction_outputs_chain_id_destination_address",
            "chain_id",
            "destination_address",
        ),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    transaction_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False
    )
    chain_id: Mapped[str] = mapped_column(identifier(CHAIN_ID_LENGTH), nullable=False)
    output_index: Mapped[int] = mapped_column(Integer, nullable=False)
    destination_address: Mapped[str | None] = mapped_column(
        identifier(ADDRESS_LENGTH), nullable=True
    )
    value_raw: Mapped[str] = mapped_column(String(AMOUNT_LENGTH), nullable=False)
    script_type: Mapped[str] = mapped_column(String(32), nullable=False)
    spend_state: Mapped[str | None] = mapped_column(String(12), nullable=True)
    spent_by_txid: Mapped[str | None] = mapped_column(identifier(80), nullable=True)
    spent_by_input_index: Mapped[int | None] = mapped_column(Integer, nullable=True)


class StoredBalanceChange(Base):
    """What one Solana account's balance did over a transaction: SOL, or a token."""

    __tablename__ = "balance_changes"
    __table_args__ = (
        UniqueConstraint(
            "transaction_id",
            "kind",
            "account",
            name="uq_balance_changes_transaction_id_kind_account",
        ),
        Index("ix_balance_changes_chain_id_owner_mint", "chain_id", "owner", "mint"),
        Index("ix_balance_changes_chain_id_account", "chain_id", "account"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    transaction_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False
    )
    chain_id: Mapped[str] = mapped_column(identifier(CHAIN_ID_LENGTH), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # "sol" or "token"
    position: Mapped[int] = mapped_column(Integer, nullable=False)  # the order in the transaction
    account: Mapped[str] = mapped_column(identifier(ADDRESS_LENGTH), nullable=False)
    owner: Mapped[str | None] = mapped_column(identifier(ADDRESS_LENGTH), nullable=True)
    mint: Mapped[str | None] = mapped_column(identifier(ADDRESS_LENGTH), nullable=True)
    decimals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    program_id: Mapped[str | None] = mapped_column(identifier(ADDRESS_LENGTH), nullable=True)
    before_raw: Mapped[str] = mapped_column(String(AMOUNT_LENGTH), nullable=False)
    after_raw: Mapped[str] = mapped_column(String(AMOUNT_LENGTH), nullable=False)


class StoredUnattributed(Base):
    """A balance change of a Solana transaction that no visible instruction explains."""

    __tablename__ = "unattributed_changes"
    __table_args__ = (
        UniqueConstraint(
            "transaction_id", "position", name="uq_unattributed_changes_transaction_id_position"
        ),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    transaction_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False
    )
    chain_id: Mapped[str] = mapped_column(identifier(CHAIN_ID_LENGTH), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    account: Mapped[str] = mapped_column(identifier(ADDRESS_LENGTH), nullable=False)
    owner: Mapped[str | None] = mapped_column(identifier(ADDRESS_LENGTH), nullable=True)
    mint: Mapped[str | None] = mapped_column(identifier(ADDRESS_LENGTH), nullable=True)
    amount_raw: Mapped[str] = mapped_column(String(AMOUNT_LENGTH), nullable=False)  # signed


class AddressTransaction(Base):
    """A transaction that was read as part of an address's history."""

    __tablename__ = "address_transactions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "chain_id", "address", "transaction_id", name="pk_address_transactions"
        ),
        Index("ix_address_transactions_transaction_id", "transaction_id"),
    )

    chain_id: Mapped[str] = mapped_column(identifier(CHAIN_ID_LENGTH), nullable=False)
    address: Mapped[str] = mapped_column(identifier(ADDRESS_LENGTH), nullable=False)
    transaction_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False
    )
