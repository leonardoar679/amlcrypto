"""transactions, transfers and the Bitcoin and Solana detail tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept local on purpose: a migration must not change meaning when app code changes.
INTEGER_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
UTC_DATETIME = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def identifier(length: int) -> sa.types.TypeEngine:
    """A case-sensitive string: MySQL's default collation would merge Base58 addresses."""
    return sa.String(length).with_variant(sa.String(length, collation="utf8mb4_bin"), "mysql")


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", UTC_DATETIME, nullable=False),
        sa.Column("updated_at", UTC_DATETIME, nullable=False),
    ]


def child_of_transaction(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["transaction_id"],
        ["transactions.id"],
        name=op.f(f"fk_{table}_transaction_id_transactions"),
        ondelete="CASCADE",
    )


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("chain_id", identifier(64), nullable=False),
        sa.Column("transaction_hash", identifier(100), nullable=False),
        sa.Column("block_number", sa.BigInteger(), nullable=True),
        sa.Column("block_hash", identifier(80), nullable=True),
        sa.Column("transaction_index", sa.Integer(), nullable=True),
        sa.Column("timestamp", UTC_DATETIME, nullable=True),
        sa.Column("status", sa.String(12), nullable=True),
        sa.Column("from_address", identifier(128), nullable=True),
        sa.Column("to_address", identifier(128), nullable=True),
        sa.Column("created_contract", identifier(128), nullable=True),
        sa.Column("value_raw", sa.String(80), nullable=True),
        sa.Column("fee_raw", sa.String(80), nullable=True),
        sa.Column("fee_payer", identifier(128), nullable=True),
        sa.Column("method_id", sa.String(16), nullable=True),
        sa.Column("function_name", sa.String(255), nullable=True),
        sa.Column("is_contract_call", sa.Boolean(), nullable=False),
        sa.Column("confirmation_status", sa.String(12), nullable=False),
        sa.Column("confirmations", sa.BigInteger(), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("data_quality", sa.String(12), nullable=False),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("weight", sa.Integer(), nullable=True),
        sa.Column("version", sa.String(12), nullable=True),
        sa.Column("locktime", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("signers", sa.JSON(), nullable=True),
        sa.Column("programs", sa.JSON(), nullable=True),
        sa.Column("unreadable", sa.Integer(), nullable=True),
        sa.Column("unresolved_owners", sa.Integer(), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["chains.id"],
            name=op.f("fk_transactions_chain_id_chains"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transactions")),
        sa.UniqueConstraint(
            "chain_id", "transaction_hash", name="uq_transactions_chain_id_transaction_hash"
        ),
    )
    op.create_index(
        "ix_transactions_chain_id_from_address", "transactions", ["chain_id", "from_address"]
    )
    op.create_index(
        "ix_transactions_chain_id_to_address", "transactions", ["chain_id", "to_address"]
    )
    op.create_index(
        "ix_transactions_chain_id_created_contract",
        "transactions",
        ["chain_id", "created_contract"],
    )
    op.create_index(
        "ix_transactions_chain_id_block_number", "transactions", ["chain_id", "block_number"]
    )

    op.create_table(
        "transfers",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("chain_id", identifier(64), nullable=False),
        sa.Column("transaction_hash", identifier(100), nullable=False),
        sa.Column("event_key", identifier(255), nullable=False),
        sa.Column("transfer_type", sa.String(12), nullable=False),
        sa.Column("block_number", sa.BigInteger(), nullable=True),
        sa.Column("timestamp", UTC_DATETIME, nullable=True),
        sa.Column("from_address", identifier(128), nullable=True),
        sa.Column("to_address", identifier(128), nullable=True),
        sa.Column("asset_symbol", sa.String(64), nullable=True),
        sa.Column("asset_name", sa.String(128), nullable=True),
        sa.Column("asset_contract_address", identifier(128), nullable=True),
        sa.Column("asset_id", identifier(64), nullable=True),
        sa.Column("asset_decimals", sa.Integer(), nullable=True),
        sa.Column("amount_raw", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("data_quality", sa.String(12), nullable=False),
        sa.Column("confirmation_status", sa.String(12), nullable=False),
        sa.Column("confirmations", sa.BigInteger(), nullable=True),
        sa.Column("transaction_index", sa.Integer(), nullable=True),
        sa.Column("log_index", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.String(128), nullable=True),
        sa.Column("source_account", identifier(128), nullable=True),
        sa.Column("destination_account", identifier(128), nullable=True),
        sa.Column("program_id", identifier(128), nullable=True),
        sa.Column("operation", sa.String(32), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["chains.id"],
            name=op.f("fk_transfers_chain_id_chains"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transfers")),
        sa.UniqueConstraint(
            "chain_id",
            "transaction_hash",
            "event_key",
            name="uq_transfers_chain_id_transaction_hash_event_key",
        ),
    )
    op.create_index("ix_transfers_chain_id_from_address", "transfers", ["chain_id", "from_address"])
    op.create_index("ix_transfers_chain_id_to_address", "transfers", ["chain_id", "to_address"])
    op.create_index("ix_transfers_chain_id_block_number", "transfers", ["chain_id", "block_number"])
    op.create_index("ix_transfers_chain_id_timestamp", "transfers", ["chain_id", "timestamp"])

    op.create_table(
        "transaction_inputs",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("transaction_id", INTEGER_PK, nullable=False),
        sa.Column("chain_id", identifier(64), nullable=False),
        sa.Column("input_index", sa.Integer(), nullable=False),
        sa.Column("previous_txid", identifier(80), nullable=True),
        sa.Column("previous_output_index", sa.Integer(), nullable=True),
        sa.Column("source_address", identifier(128), nullable=True),
        sa.Column("value_raw", sa.String(80), nullable=True),
        sa.Column("script_type", sa.String(32), nullable=True),
        sa.Column("is_coinbase", sa.Boolean(), nullable=False),
        child_of_transaction("transaction_inputs"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transaction_inputs")),
        sa.UniqueConstraint(
            "transaction_id", "input_index", name="uq_transaction_inputs_transaction_id_input_index"
        ),
    )
    op.create_index(
        "ix_transaction_inputs_chain_id_source_address",
        "transaction_inputs",
        ["chain_id", "source_address"],
    )
    op.create_index(
        "ix_transaction_inputs_chain_id_previous_outpoint",
        "transaction_inputs",
        ["chain_id", "previous_txid", "previous_output_index"],
    )

    op.create_table(
        "transaction_outputs",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("transaction_id", INTEGER_PK, nullable=False),
        sa.Column("chain_id", identifier(64), nullable=False),
        sa.Column("output_index", sa.Integer(), nullable=False),
        sa.Column("destination_address", identifier(128), nullable=True),
        sa.Column("value_raw", sa.String(80), nullable=False),
        sa.Column("script_type", sa.String(32), nullable=False),
        sa.Column("spend_state", sa.String(12), nullable=True),
        sa.Column("spent_by_txid", identifier(80), nullable=True),
        sa.Column("spent_by_input_index", sa.Integer(), nullable=True),
        child_of_transaction("transaction_outputs"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transaction_outputs")),
        sa.UniqueConstraint(
            "transaction_id",
            "output_index",
            name="uq_transaction_outputs_transaction_id_output_index",
        ),
    )
    op.create_index(
        "ix_transaction_outputs_chain_id_destination_address",
        "transaction_outputs",
        ["chain_id", "destination_address"],
    )

    op.create_table(
        "balance_changes",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("transaction_id", INTEGER_PK, nullable=False),
        sa.Column("chain_id", identifier(64), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("account", identifier(128), nullable=False),
        sa.Column("owner", identifier(128), nullable=True),
        sa.Column("mint", identifier(128), nullable=True),
        sa.Column("decimals", sa.Integer(), nullable=True),
        sa.Column("program_id", identifier(128), nullable=True),
        sa.Column("before_raw", sa.String(80), nullable=False),
        sa.Column("after_raw", sa.String(80), nullable=False),
        child_of_transaction("balance_changes"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_balance_changes")),
        sa.UniqueConstraint(
            "transaction_id",
            "kind",
            "account",
            name="uq_balance_changes_transaction_id_kind_account",
        ),
    )
    op.create_index(
        "ix_balance_changes_chain_id_owner_mint", "balance_changes", ["chain_id", "owner", "mint"]
    )
    op.create_index(
        "ix_balance_changes_chain_id_account", "balance_changes", ["chain_id", "account"]
    )

    op.create_table(
        "unattributed_changes",
        sa.Column("id", INTEGER_PK, autoincrement=True, nullable=False),
        sa.Column("transaction_id", INTEGER_PK, nullable=False),
        sa.Column("chain_id", identifier(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("account", identifier(128), nullable=False),
        sa.Column("owner", identifier(128), nullable=True),
        sa.Column("mint", identifier(128), nullable=True),
        sa.Column("amount_raw", sa.String(80), nullable=False),
        child_of_transaction("unattributed_changes"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_unattributed_changes")),
        sa.UniqueConstraint(
            "transaction_id", "position", name="uq_unattributed_changes_transaction_id_position"
        ),
    )

    op.create_table(
        "address_transactions",
        sa.Column("chain_id", identifier(64), nullable=False),
        sa.Column("address", identifier(128), nullable=False),
        sa.Column("transaction_id", INTEGER_PK, nullable=False),
        child_of_transaction("address_transactions"),
        sa.PrimaryKeyConstraint(
            "chain_id", "address", "transaction_id", name="pk_address_transactions"
        ),
    )
    op.create_index(
        "ix_address_transactions_transaction_id", "address_transactions", ["transaction_id"]
    )


def downgrade() -> None:
    # Tables only, children first: dropping a table drops its indexes, and MySQL refuses to drop an
    # index that backs a foreign key (see 0001).
    op.drop_table("address_transactions")
    op.drop_table("unattributed_changes")
    op.drop_table("balance_changes")
    op.drop_table("transaction_outputs")
    op.drop_table("transaction_inputs")
    op.drop_table("transfers")
    op.drop_table("transactions")
