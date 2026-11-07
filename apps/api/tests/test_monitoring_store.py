"""What the monitoring repository answers from the stored histories."""

from __future__ import annotations

from dataclasses import replace

from app.chains.models import DataQuality, Transaction, Transfer, TxStatus
from app.database.repositories import monitoring as store
from app.database.repositories.membership import link_address, transaction_ids
from app.database.repositories.transfers import save_transactions
from tests.graph_store import move

ETHEREUM = "eip155:1"
BNB = "eip155:56"
ALICE = "0x" + "a1" * 20
BOB = "0x" + "b2" * 20
X = "0x" + "c3" * 20
Y = "0x" + "d4" * 20


def as_transaction(transfer: Transfer) -> Transaction:
    return Transaction(
        chain_id=transfer.chain_id,
        transaction_hash=transfer.transaction_hash,
        block_number=transfer.block_number,
        timestamp=transfer.timestamp,
        from_address=transfer.from_address,
        to_address=transfer.to_address,
        created_contract=None,
        value_raw=transfer.amount_raw,
        status=TxStatus.SUCCESS,
        provider="etherscan",
        data_quality=DataQuality.INDEXED,
    )


def read_as(session, address, transfers):
    """Store the transactions and record that reading `address` found them (what a sync does)."""
    save_transactions(session, [as_transaction(t) for t in transfers])
    for chain in {t.chain_id for t in transfers}:
        found = [t.transaction_hash for t in transfers if t.chain_id == chain]
        link_address(session, chain, address, list(transaction_ids(session, chain, found).values()))
    session.commit()


def test_the_newest_activity_is_the_addresses_own_on_its_own_chain(committing_db):
    alice_old = move(ALICE, X, days_ago=5)
    alice_older = move(X, ALICE, days_ago=9)
    alice_on_bnb = replace(move(ALICE, X, days_ago=0.5), chain_id=BNB)
    bob_new = move(BOB, Y, days_ago=1)
    with committing_db.write() as session:
        read_as(session, ALICE, [alice_old, alice_older, alice_on_bnb])
        read_as(session, BOB, [bob_new])

    with committing_db.read() as session:
        assert store.latest_activity_at(session, ETHEREUM, ALICE) == alice_old.timestamp
        assert store.latest_activity_at(session, ETHEREUM, BOB) == bob_new.timestamp
        assert store.latest_activity_at(session, BNB, ALICE) == alice_on_bnb.timestamp
        assert store.latest_activity_at(session, BNB, BOB) is None
        assert store.latest_activity_at(session, ETHEREUM, "0x" + "ee" * 20) is None
