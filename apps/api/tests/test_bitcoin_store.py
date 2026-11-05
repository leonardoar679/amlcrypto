"""Storing Bitcoin transactions: inputs and outputs, history membership, spends, reorganisations."""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import func, select

from app.chains.bitcoin.chains import BITCOIN_CHAIN
from app.chains.bitcoin.models import BitcoinTransaction, SpendState
from app.chains.bitcoin.normalize import apply_outspends, esplora_transaction
from app.database.models import AddressTransaction, StoredInput, StoredOutput, StoredTransaction
from app.database.repositories.bitcoin import (
    load_bitcoin_history,
    load_bitcoin_transaction,
    remove_unseen_bitcoin,
    save_bitcoin_transactions,
)
from app.database.repositories.rows import SaveCounts
from tests.fake_esplora import p2pkh, pay, spend, tx, txid
from tests.helpers import load_fixture
from tests.test_bitcoin_normalize import NAMES

CHAIN = BITCOIN_CHAIN.chain_id
ADDRESS = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
OTHER = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"


def synthetic(n: int, *, height: int = 800_000, **changes) -> BitcoinTransaction:
    raw = tx(
        n,
        [spend(txid(900 + n), 0, p2pkh(1), 9000)],
        [pay(p2pkh(2), 4000), pay(p2pkh(3), 3000)],
        height=height,
    )
    return replace(esplora_transaction(raw, BITCOIN_CHAIN), **changes)


def save(db, transactions, **options):
    with db.write() as session:
        counts = save_bitcoin_transactions(session, transactions, **options)
        session.commit()
    return counts


def history(db, address=ADDRESS):
    with db.read() as session:
        return load_bitcoin_history(session, CHAIN, address)


def count(db, model):
    with db.read() as session:
        return session.scalar(select(func.count()).select_from(model))


def forget_unspent(item: BitcoinTransaction) -> BitcoinTransaction:
    """Unspent is true as of a moment, so it is not kept: it comes back as unknown."""
    outputs = tuple(
        replace(o, spend_state=SpendState.UNKNOWN) if o.spend_state is SpendState.UNSPENT else o
        for o in item.outputs
    )
    return replace(item, outputs=outputs)


# --- fidelity ----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_every_real_transaction_comes_back_as_it_went_in(committing_db, name):
    data = load_fixture(f"bitcoin/{name}")
    plain = esplora_transaction(data["tx"], BITCOIN_CHAIN)
    save(committing_db, [plain], address=ADDRESS)

    assert history(committing_db) == [plain]


@pytest.mark.parametrize(
    "name", [n for n in NAMES if load_fixture(f"bitcoin/{n}").get("outspends")]
)
def test_what_is_proven_about_spends_is_kept_and_what_is_only_true_for_now_is_not(
    committing_db, name
):
    data = load_fixture(f"bitcoin/{name}")
    known = apply_outspends(esplora_transaction(data["tx"], BITCOIN_CHAIN), data["outspends"])
    save(committing_db, [known], address=ADDRESS)

    assert history(committing_db) == [forget_unspent(known)]


def test_a_coinbase_has_no_previous_output_and_no_fee(committing_db):
    coinbase = esplora_transaction(load_fixture("bitcoin/coinbase")["tx"], BITCOIN_CHAIN)
    assert coinbase.is_coinbase and coinbase.fee_raw is None
    save(committing_db, [coinbase], address=ADDRESS)

    (loaded,) = history(committing_db)
    assert loaded.is_coinbase and loaded.fee_raw is None
    assert loaded.inputs[0].previous_txid is None


def test_a_transaction_is_found_by_its_id(committing_db):
    item = synthetic(1)
    save(committing_db, [item])

    with committing_db.read() as session:
        assert load_bitcoin_transaction(session, CHAIN, item.txid) == item
        assert load_bitcoin_transaction(session, CHAIN, txid(404)) is None


def test_inputs_and_outputs_keep_their_order(committing_db):
    many = esplora_transaction(load_fixture("bitcoin/many_outputs_payout")["tx"], BITCOIN_CHAIN)
    save(committing_db, [many], address=ADDRESS)

    (loaded,) = history(committing_db)
    assert [o.index for o in loaded.outputs] == list(range(len(many.outputs)))
    assert loaded.outputs == many.outputs


# --- identity and merging ------------------------------------------------------------------------


def test_the_same_transaction_read_twice_is_stored_once_with_its_children(committing_db):
    item = synthetic(1)
    first = save(committing_db, [item], address=ADDRESS)
    second = save(committing_db, [item], address=ADDRESS)

    assert first == SaveCounts(new=1) and second == SaveCounts(unchanged=1)
    assert (count(committing_db, StoredTransaction), count(committing_db, StoredInput)) == (1, 1)
    assert (count(committing_db, StoredOutput), count(committing_db, AddressTransaction)) == (2, 1)


def test_a_spend_proven_by_one_read_is_not_erased_by_one_that_does_not_know(committing_db):
    item = synthetic(1)
    spent = replace(
        item.outputs[0],
        spend_state=SpendState.SPENT,
        spent_by_txid=txid(77),
        spent_by_input_index=2,
    )
    save(committing_db, [replace(item, outputs=(spent, item.outputs[1]))], address=ADDRESS)

    save(committing_db, [replace(item, confirmations=50)], address=ADDRESS)  # knows no spends

    (loaded,) = history(committing_db)
    assert loaded.outputs[0].spend_state is SpendState.SPENT
    assert (loaded.outputs[0].spent_by_txid, loaded.outputs[0].spent_by_input_index) == (
        txid(77),
        2,
    )
    assert loaded.confirmations == 50


def test_replacing_a_transaction_rewrites_its_inputs_and_outputs(committing_db):
    item = synthetic(1)
    save(committing_db, [item], address=ADDRESS)
    fewer = replace(item, outputs=item.outputs[:1])

    save(committing_db, [fewer], address=ADDRESS, replace=True)

    assert history(committing_db) == [fewer]
    assert count(committing_db, StoredOutput) == 1


# --- whose history it is -------------------------------------------------------------------------


def test_a_transaction_is_part_of_the_histories_it_was_read_for(committing_db):
    shared, only_mine = synthetic(1), synthetic(2)
    save(committing_db, [shared, only_mine], address=ADDRESS)
    save(committing_db, [shared], address=OTHER)

    assert sorted(t.txid for t in history(committing_db)) == sorted([shared.txid, only_mine.txid])
    assert [t.txid for t in history(committing_db, OTHER)] == [shared.txid]
    assert history(committing_db, "bc1qnothingreadforthisone") == []
    assert count(committing_db, StoredTransaction) == 2  # stored once, whoever it was read for


def test_saving_without_an_address_stores_the_transaction_but_no_history(committing_db):
    save(committing_db, [synthetic(1)])

    assert history(committing_db) == []
    assert count(committing_db, StoredTransaction) == 1


# --- reorganisations ---------------------------------------------------------------------------


def test_what_a_complete_reread_no_longer_shows_is_removed_with_its_children(committing_db):
    kept = synthetic(1, height=800_010)
    dropped = synthetic(2, height=800_011)
    first_block = synthetic(4, height=800_000)  # the first block read again is judged too
    older = synthetic(3, height=799_999)
    save(committing_db, [kept, dropped, first_block, older], address=ADDRESS)

    with committing_db.write() as session:
        removed = remove_unseen_bitcoin(
            session, CHAIN, ADDRESS, from_height=800_000, seen_txids={kept.txid}
        )
        session.commit()

    assert removed == 2
    assert sorted(t.txid for t in history(committing_db)) == sorted([kept.txid, older.txid])
    assert count(committing_db, StoredInput) == 2 and count(committing_db, StoredOutput) == 4
    assert count(committing_db, AddressTransaction) == 2


def test_another_addresss_transactions_are_not_removed(committing_db):
    mine, theirs = synthetic(1, height=800_010), synthetic(2, height=800_010)
    save(committing_db, [mine], address=ADDRESS)
    save(committing_db, [theirs], address=OTHER)

    with committing_db.write() as session:
        removed = remove_unseen_bitcoin(
            session, CHAIN, ADDRESS, from_height=0, seen_txids={mine.txid}
        )
        session.commit()

    assert removed == 0 and len(history(committing_db, OTHER)) == 1
