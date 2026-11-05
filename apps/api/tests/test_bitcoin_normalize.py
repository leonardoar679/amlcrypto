"""Esplora transactions become our records, checked against real Bitcoin transactions.

The files in tests/fixtures/bitcoin are real responses from a public Esplora (2026-11-05): a payment
with change, address reuse, legacy and taproot spends, a consolidation, a payout batch, two
CoinJoin look-alikes and two that are not, a data-only transaction, coinbases (one from 2009 with
a pay-to-key output) and the first bitcoin payment. Where a spend list was fetched it is kept too.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.chains.bitcoin.chains import BITCOIN_CHAIN, get_bitcoin_chain
from app.chains.bitcoin.models import SpendState
from app.chains.bitcoin.normalize import (
    apply_outspends,
    esplora_transaction,
    txid_of,
    unresolved_inputs,
    with_head,
)
from app.chains.models import Confirmation
from app.chains.values import MalformedRow
from tests.fake_esplora import coinbase_input, p2pkh, pay, spend, tx, txid
from tests.helpers import load_fixture

CHAIN = BITCOIN_CHAIN
NAMES = [
    "payment_with_change",
    "self_change",
    "legacy_p2pkh",
    "taproot_small",
    "consolidation_small",
    "batch_payout_equal_outputs",
    "coinjoin_like",
    "inscription_dust_batch",
    "op_return_only",
    "many_outputs_payout",
    "coinbase",
    "coinbase_p2pk",
    "p2pk_payment",
]


def real(name):
    return load_fixture(f"bitcoin/{name}")


def tx_of(name):
    return esplora_transaction(real(name)["tx"], CHAIN)


# --- the chain ---------------------------------------------------------------------------------


def test_the_chain_lookup_knows_bitcoin_only():
    assert get_bitcoin_chain("bitcoin") is CHAIN
    assert get_bitcoin_chain("bip122:000000000019d6689c085ae165831e93") is CHAIN
    assert get_bitcoin_chain("ethereum") is None and get_bitcoin_chain("nonsense") is None


def test_bitcoin_is_counted_in_satoshis_eight_decimals_down():
    assert (CHAIN.native.symbol, CHAIN.native.decimals) == ("BTC", 8)
    assert CHAIN.confirmation_depth == 6


def test_an_update_starts_a_reorg_margin_before_the_synced_block():
    assert CHAIN.incremental_start(1000) == 1000 - CHAIN.reorg_margin
    assert CHAIN.incremental_start(3) == 0


# --- real transactions -------------------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_what_goes_in_and_does_not_come_out_is_the_fee_for_every_real_transaction(name):
    transaction = tx_of(name)
    if transaction.is_coinbase:
        assert transaction.fee_raw is None  # a coinbase pays no fee
        return
    total_in = sum(int(item.value_raw) for item in transaction.inputs)
    total_out = sum(int(item.value_raw) for item in transaction.outputs)
    assert total_in - total_out == int(transaction.fee_raw)
    assert unresolved_inputs(transaction) == 0


def test_inputs_and_outputs_are_kept_apart_with_what_each_one_says():
    raw = real("payment_with_change")["tx"]
    transaction = esplora_transaction(raw, CHAIN)

    (item,) = transaction.inputs
    spent = raw["vin"][0]
    assert (item.previous_txid, item.previous_output_index) == (spent["txid"], spent["vout"])
    assert item.source_address == spent["prevout"]["scriptpubkey_address"]
    assert item.value_raw == str(spent["prevout"]["value"])
    assert item.script_type == "v0_p2wpkh" and not item.is_coinbase
    assert [o.destination_address for o in transaction.outputs] == [
        o["scriptpubkey_address"] for o in raw["vout"]
    ]
    assert [o.index for o in transaction.outputs] == [0, 1]
    assert transaction.fee_raw == str(raw["fee"])
    assert (transaction.size, transaction.weight) == (raw["size"], raw["weight"])
    assert transaction.vsize == -(-raw["weight"] // 4)
    assert transaction.identity == (CHAIN.chain_id, raw["txid"])


def test_the_block_and_its_time_come_from_the_status():
    raw = real("payment_with_change")["tx"]
    transaction = esplora_transaction(raw, CHAIN)
    status = raw["status"]
    assert transaction.block_height == status["block_height"]
    assert transaction.block_hash == status["block_hash"]
    assert transaction.timestamp == datetime.fromtimestamp(status["block_time"], UTC)


def test_a_coinbase_spends_nothing_and_creates_coins():
    transaction = tx_of("coinbase")
    (item,) = transaction.inputs
    assert transaction.is_coinbase and item.is_coinbase
    assert (item.previous_txid, item.previous_output_index) == (None, None)
    assert (item.source_address, item.value_raw, item.script_type) == (None, None, None)
    assert transaction.fee_raw is None


def test_old_pay_to_key_outputs_have_no_address_and_that_is_kept_as_none():
    transaction = tx_of("p2pk_payment")
    assert [o.destination_address for o in transaction.outputs] == [None, None]
    assert {o.script_type for o in transaction.outputs} == {"p2pk"}
    assert [o.value_raw for o in transaction.outputs] == [
        "1000000000",
        "4000000000",
    ]  # 10 and 40 BTC
    assert (
        transaction.inputs[0].source_address is None and transaction.inputs[0].script_type == "p2pk"
    )


def test_data_outputs_can_never_be_spent_and_are_marked_so():
    transaction = tx_of("op_return_only")
    (output,) = transaction.outputs
    assert output.script_type == "op_return" and output.destination_address is None
    assert output.spend_state is SpendState.UNSPENDABLE
    assert all(o.spend_state is SpendState.UNKNOWN for o in tx_of("payment_with_change").outputs)


# --- confirmations -----------------------------------------------------------------------------


def test_a_transaction_is_confirmed_exactly_when_it_is_as_deep_as_the_chain_says():
    transaction = tx_of("payment_with_change")
    height, depth = transaction.block_height, CHAIN.confirmation_depth

    shallow = with_head(transaction, height + depth - 2, CHAIN)
    exact = with_head(transaction, height + depth - 1, CHAIN)

    assert (shallow.confirmations, shallow.confirmation_status) == (
        depth - 1,
        Confirmation.UNCONFIRMED,
    )
    assert (exact.confirmations, exact.confirmation_status) == (depth, Confirmation.CONFIRMED)
    assert with_head(transaction, height, CHAIN).confirmations == 1  # in the newest block


def test_a_height_nobody_knows_leaves_the_depth_unknown_and_the_mempool_has_none():
    transaction = tx_of("payment_with_change")
    unknown = with_head(transaction, None, CHAIN)
    assert (unknown.confirmations, unknown.confirmation_status) == (None, Confirmation.UNKNOWN)

    pending = esplora_transaction(
        tx(1, [spend(txid(9), 0, p2pkh(1), 5000)], [pay(p2pkh(2), 4000)], height=None), CHAIN
    )
    assert pending.block_height is None and pending.timestamp is None
    waiting = with_head(pending, 100, CHAIN)
    assert (waiting.confirmations, waiting.confirmation_status) == (0, Confirmation.UNCONFIRMED)


# --- refusing what is not a transaction ---------------------------------------------------------


def good():
    return tx(1, [spend(txid(9), 0, p2pkh(1), 5000)], [pay(p2pkh(2), 4000)])


def corrupt(change):
    raw = good()
    change(raw)
    return raw


@pytest.mark.parametrize(
    "raw",
    [
        corrupt(lambda r: r.update(txid="short")),
        corrupt(lambda r: r.update(vin=[])),
        corrupt(lambda r: r.update(vout=[])),
        corrupt(lambda r: r.pop("status")),
        corrupt(lambda r: r["status"].pop("block_height")),
        corrupt(lambda r: r["status"].update(block_height=-1)),
        corrupt(lambda r: r["status"].update(block_hash="nope")),
        corrupt(lambda r: r.update(fee=-1)),
        corrupt(lambda r: r.update(fee="a lot")),
        corrupt(lambda r: r.update(weight=None)),
        corrupt(lambda r: r["vout"][0].update(value=-5)),
        corrupt(lambda r: r["vout"][0].update(value=1.5)),
        corrupt(lambda r: r["vout"][0].update(scriptpubkey_address="1NotAnAddressAtAll")),
        corrupt(lambda r: r["vout"][0].pop("scriptpubkey_type")),
        corrupt(lambda r: r["vin"][0].update(txid="xyz")),
        corrupt(lambda r: r["vin"][0]["prevout"].update(value=-1)),
        corrupt(lambda r: r["vin"].append(coinbase_input())),
        corrupt(lambda r: r["vin"].append("not an object")),
        "not an object",
    ],
)
def test_data_that_cannot_be_what_it_claims_is_refused(raw):
    with pytest.raises(MalformedRow):
        esplora_transaction(raw, CHAIN)


def test_an_input_whose_spent_output_the_provider_could_not_give_is_kept_and_counted():
    raw = good()
    raw["vin"][0]["prevout"] = None
    transaction = esplora_transaction(raw, CHAIN)
    (item,) = transaction.inputs
    assert (item.value_raw, item.source_address) == (None, None)
    assert item.previous_txid == txid(9) and unresolved_inputs(transaction) == 1


def test_transaction_ids_are_64_hex_characters_in_lower_case():
    assert txid_of("AB" * 32) == "ab" * 32
    assert txid_of(f"  {'cd' * 32}\n") == "cd" * 32
    for bad in ("", "12", "zz" * 32, "ab" * 33, None, 5):
        with pytest.raises(MalformedRow):
            txid_of(bad)


# --- which outputs are spent -------------------------------------------------------------------


def test_a_real_spend_list_says_which_outputs_are_spent_and_by_what():
    data = real("p2pk_payment")
    transaction = apply_outspends(esplora_transaction(data["tx"], CHAIN), data["outspends"])
    for output, entry in zip(transaction.outputs, data["outspends"], strict=True):
        assert output.spend_state is SpendState.SPENT
        assert (output.spent_by_txid, output.spent_by_input_index) == (entry["txid"], entry["vin"])


def test_unspent_outputs_and_data_outputs_are_told_apart_from_spent_ones():
    transaction = esplora_transaction(
        tx(
            1,
            [spend(txid(9), 0, p2pkh(1), 9000)],
            [pay(p2pkh(2), 4000), pay(p2pkh(3), 3000), pay(None, 0, "op_return")],
        ),
        CHAIN,
    )
    spent = {"spent": True, "txid": txid(7), "vin": 2, "status": {"confirmed": True}}
    result = apply_outspends(transaction, [spent, {"spent": False}, {"spent": False}])
    assert [o.spend_state for o in result.outputs] == [
        SpendState.SPENT,
        SpendState.UNSPENT,
        SpendState.UNSPENDABLE,  # never spent, whatever a provider says
    ]
    assert result.outputs[0].spent_by_txid == txid(7) and result.outputs[1].spent_by_txid is None


def test_a_spend_list_that_does_not_fit_the_transaction_is_refused():
    transaction = esplora_transaction(good(), CHAIN)
    for bad in (
        None,
        [],
        [{"spent": False}, {"spent": False}],
        {"spent": False},
        [{"spent": "yes"}],
        [{}],
        [{"spent": True, "txid": "nope", "vin": 0}],
        [{"spent": True, "txid": txid(1)}],
    ):
        with pytest.raises(MalformedRow):
            apply_outspends(transaction, bad)


def test_the_real_coinjoin_look_alike_had_its_equal_outputs_spent_together_by_one_transaction():
    data = real("coinjoin_like")
    transaction = apply_outspends(esplora_transaction(data["tx"], CHAIN), data["outspends"])
    equal = [o for o in transaction.outputs if o.value_raw == "33554432"]
    assert len(equal) == 7 and all(o.spend_state is SpendState.SPENT for o in equal)
    assert len({o.spent_by_txid for o in equal}) == 1  # merged again: the mix did not stay mixed
    assert sorted(o.spent_by_input_index for o in equal) == list(range(7))
