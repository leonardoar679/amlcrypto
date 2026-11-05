"""Storing Solana transactions: movements, balance changes, history membership, removal."""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import func, select

from app.chains.solana.chains import SOLANA_CHAIN
from app.chains.solana.models import SolanaTransaction, SolChange, TokenChange, Unattributed
from app.chains.solana.normalize import solana_transaction
from app.database.models import (
    AddressTransaction,
    StoredBalanceChange,
    StoredTransaction,
    StoredTransfer,
    StoredUnattributed,
)
from app.database.repositories.rows import SaveCounts
from app.database.repositories.solana import (
    _position,
    load_solana_history,
    load_solana_transaction,
    save_solana_transactions,
)
from app.database.repositories.transfers import load_transfers
from tests.helpers import load_fixture
from tests.test_solana_normalize import NAMES, WALLET

CHAIN = SOLANA_CHAIN
CHAIN_ID = SOLANA_CHAIN.chain_id
OTHER = "8x2dR8Mpzuz2YqyZyZjUbYWKSWesBo5jMx2Q9Y86udVk"


def decode(name: str) -> SolanaTransaction:
    return solana_transaction(load_fixture(f"solana/{name}")["item"], CHAIN)


def save(db, transactions, **options):
    with db.write() as session:
        counts = save_solana_transactions(session, transactions, **options)
        session.commit()
    return counts


def history(db, address=WALLET):
    with db.read() as session:
        return load_solana_history(session, CHAIN_ID, address)


def count(db, model):
    with db.read() as session:
        return session.scalar(select(func.count()).select_from(model))


# --- fidelity ----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_every_real_transaction_comes_back_as_it_went_in(committing_db, name):
    original = decode(name)
    save(committing_db, [original], address=WALLET)

    assert history(committing_db) == [original]


@pytest.mark.parametrize("name", NAMES)
def test_the_movements_of_a_real_transaction_have_distinct_identities(name):
    # Two movements with one identity would be stored as one: the store would lose a transfer.
    keys = [transfer.event_key for transfer in decode(name).transfers]
    assert len(keys) == len(set(keys))


def test_nothing_is_lost_that_the_swaps_balance_changes_and_movements_hold(committing_db):
    swap = decode("swap_temp_wsol_v1")
    assert swap.transfers and swap.token_changes and swap.sol_changes
    save(committing_db, [swap], address=WALLET)

    (loaded,) = history(committing_db)
    assert loaded.transfers == swap.transfers  # same movements, same order
    assert loaded.token_changes == swap.token_changes
    assert loaded.sol_changes == swap.sol_changes
    assert loaded.unattributed == swap.unattributed
    assert (loaded.version, loaded.signers, loaded.programs) == (
        swap.version,
        swap.signers,
        swap.programs,
    )


def test_a_failed_transaction_keeps_what_the_chain_said_went_wrong(committing_db):
    failed = decode("failed")
    assert failed.error
    save(committing_db, [failed], address=WALLET)

    (loaded,) = history(committing_db)
    assert (loaded.status, loaded.error, loaded.transfers) == (
        failed.status,
        failed.error,
        failed.transfers,
    )


def test_changes_nobody_explains_and_the_counts_of_what_was_not_understood_are_kept(
    committing_db,
):
    odd = replace(
        decode("system_transfer_v0"),
        unattributed=(
            Unattributed("sol", WALLET, None, None, "-250"),
            Unattributed("token", "T" * 44, WALLET, "M" * 44, "17"),
        ),
        unreadable=3,
        unresolved_owners=2,
    )
    save(committing_db, [odd], address=WALLET)

    (loaded,) = history(committing_db)
    assert loaded.unattributed == odd.unattributed
    assert (loaded.unreadable, loaded.unresolved_owners) == (3, 2)


def test_a_transaction_is_found_by_its_signature(committing_db):
    item = decode("system_transfer_legacy")
    save(committing_db, [item])

    with committing_db.read() as session:
        assert load_solana_transaction(session, CHAIN_ID, item.signature) == item
        assert load_solana_transaction(session, CHAIN_ID, "1" * 88) is None


# --- movements as transfers --------------------------------------------------------------------


def test_the_movements_are_ordinary_transfers_the_address_queries_can_reach(committing_db):
    item = decode("token_transfer_checked")
    save(committing_db, [item], address=WALLET)

    with committing_db.read() as session:
        found = load_transfers(session, CHAIN_ID, item.transfers[0].from_address)
    assert [t.event_key for t in found] == [item.transfers[0].event_key]
    assert found[0].source_account == item.transfers[0].source_account


@pytest.mark.parametrize(
    ("keys", "ordered"),
    [
        (["3.1", "2", "3.0", "3", "10", "2.9"], ["2", "2.9", "3", "3.0", "3.1", "10"]),
        (["1#2", "1", "1#1"], ["1", "1#1", "1#2"]),
        (["0.10", "0.2", "0.1"], ["0.1", "0.2", "0.10"]),
    ],
)
def test_instruction_positions_sort_as_the_chain_ran_them(keys, ordered):
    assert sorted(keys, key=_position) == ordered


# --- identity and merging ------------------------------------------------------------------------


def test_the_same_transaction_read_twice_is_stored_once_with_everything_in_it(committing_db):
    item = decode("swap_temp_wsol_v1")
    first = save(committing_db, [item], address=WALLET)
    second = save(committing_db, [item], address=WALLET)

    assert first == SaveCounts(new=1) and second == SaveCounts(unchanged=1)
    assert count(committing_db, StoredTransaction) == 1
    assert count(committing_db, StoredTransfer) == len(item.transfers)
    assert count(committing_db, StoredBalanceChange) == len(item.sol_changes) + len(
        item.token_changes
    )
    assert count(committing_db, AddressTransaction) == 1


def test_a_transaction_read_for_two_wallets_is_stored_once_and_belongs_to_both(committing_db):
    item = decode("system_transfer_legacy")
    save(committing_db, [item], address=WALLET)
    save(committing_db, [item], address=OTHER)

    assert history(committing_db) == [item] and history(committing_db, OTHER) == [item]
    assert count(committing_db, StoredTransaction) == 1


def test_a_transaction_that_only_changed_a_token_account_is_still_part_of_the_history(
    committing_db,
):
    # Neither the wallet nor its token accounts are in the movements: only the read says so.
    item = replace(decode("system_transfer_legacy"), transfers=())
    save(committing_db, [item], address="W" * 44)

    assert history(committing_db, "W" * 44) == [item]


def test_replacing_a_transaction_rewrites_its_balance_changes(committing_db):
    item = decode("swap_temp_wsol_v1")
    save(committing_db, [item], address=WALLET)
    fewer = replace(item, sol_changes=item.sol_changes[:1], unattributed=())

    save(committing_db, [fewer], address=WALLET, replace=True)

    (loaded,) = history(committing_db)
    assert loaded.sol_changes == fewer.sol_changes and loaded.unattributed == ()
    assert count(committing_db, StoredUnattributed) == 0


def test_a_balance_change_keeps_what_the_chain_said_about_the_accounts_owner(committing_db):
    item = replace(
        decode("system_transfer_legacy"),
        sol_changes=(SolChange("A" * 44, "10", "7"),),
        token_changes=(TokenChange("T" * 44, None, "M" * 44, 6, None, "0", "0"),),
    )
    save(committing_db, [item], address=WALLET)

    (loaded,) = history(committing_db)
    assert loaded.token_changes[0].owner is None and loaded.token_changes[0].program_id is None
    assert loaded.sol_changes == item.sol_changes
