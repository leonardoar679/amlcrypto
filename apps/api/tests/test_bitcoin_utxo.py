"""What is proven about Bitcoin transactions (spend links, order, one address's part) and what is
only inferred (change, CoinJoin, shared ownership), on real transactions and small synthetic ones.

A synthetic transaction is built from `tx`, `spend` and `pay` (tests/fake_esplora.py) and then goes
through the same normalizer as a real one.
"""

from __future__ import annotations

import itertools
import json

import pytest

from app.chains.bitcoin.chains import BITCOIN_CHAIN
from app.chains.bitcoin.models import SpendState
from app.chains.bitcoin.normalize import apply_outspends, esplora_transaction
from app.chains.bitcoin.utxo import (
    COINJOIN_LIKELY_EQUAL,
    Confidence,
    Likelihood,
    address_activity,
    analyze,
    assess_change,
    common_inputs,
    detect_coinjoin,
    link_spends,
    order_transactions,
)
from tests.fake_esplora import coinbase_input, p2pkh, p2sh, pay, spend, tx, txid
from tests.helpers import load_fixture

CHAIN = BITCOIN_CHAIN
ME, A, B, C, D = (p2pkh(n) for n in (1, 2, 3, 4, 5))


def norm(raw):
    return esplora_transaction(raw, CHAIN)


def real(name):
    data = load_fixture(f"bitcoin/{name}")
    transaction = norm(data["tx"])
    if data["outspends"] is not None:
        transaction = apply_outspends(transaction, data["outspends"])
    return transaction


def labels(assessment):
    return {item.index: item.likelihood for item in assessment.outputs}


# --- spend links -------------------------------------------------------------------------------


def two_linked():
    first = norm(
        tx(
            1,
            [spend(txid(100), 0, A, 12_000)],
            [pay(ME, 6_000), pay(ME, 2_000), pay(B, 3_000)],
            height=100,
        )
    )
    second = norm(tx(2, [spend(txid(1), 0, ME, 6_000)], [pay(C, 5_000)], height=101))
    return first, second


def test_a_spender_in_the_set_marks_the_output_spent_and_no_other_is_guessed():
    first, second = link_spends(list(two_linked()))

    assert first.outputs[0].spend_state is SpendState.SPENT
    assert (first.outputs[0].spent_by_txid, first.outputs[0].spent_by_input_index) == (txid(2), 0)
    # Nothing here spends the others, which says nothing about whether anyone else did.
    assert [o.spend_state for o in first.outputs[1:]] == [SpendState.UNKNOWN] * 2
    assert second.outputs[0].spend_state is SpendState.UNKNOWN


def test_with_the_addresss_complete_history_its_outputs_nobody_spent_are_unspent():
    first, _ = link_spends(list(two_linked()), complete_for=ME)

    assert first.outputs[0].spend_state is SpendState.SPENT
    assert first.outputs[1].spend_state is SpendState.UNSPENT  # pays ME; its spender would be here
    assert (
        first.outputs[2].spend_state is SpendState.UNKNOWN
    )  # pays B: B's spender may be elsewhere


def test_a_verdict_already_known_is_not_overwritten_and_data_outputs_stay_unspendable():
    first = norm(
        tx(1, [spend(txid(100), 0, A, 5_000)], [pay(ME, 4_000), pay(None, 0, "op_return")])
    )
    first = apply_outspends(first, [{"spent": True, "txid": txid(50), "vin": 3}, {"spent": False}])
    later = norm(tx(2, [spend(txid(1), 0, ME, 4_000)], [pay(C, 3_000)]))

    result, _ = link_spends([first, later], complete_for=ME)

    assert result.outputs[0].spent_by_txid == txid(50)  # not replaced by txid(2)
    assert result.outputs[1].spend_state is SpendState.UNSPENDABLE


def test_linking_does_not_change_what_it_was_given():
    first, second = two_linked()
    link_spends([first, second], complete_for=ME)
    assert all(o.spend_state is SpendState.UNKNOWN for o in first.outputs)


# --- order -------------------------------------------------------------------------------------


def test_blocks_come_oldest_first_and_the_mempool_last():
    pending = norm(tx(1, [spend(txid(90), 0, A, 5_000)], [pay(B, 4_000)], height=None))
    newer = norm(tx(2, [spend(txid(91), 0, A, 5_000)], [pay(B, 4_000)], height=200))
    older = norm(tx(3, [spend(txid(92), 0, A, 5_000)], [pay(B, 4_000)], height=100))
    assert [t.txid for t in order_transactions([pending, newer, older])] == [
        txid(3),
        txid(2),
        txid(1),
    ]


def test_within_a_block_a_spender_follows_what_it_spends_whatever_the_ids_say():
    parent = norm(tx(9, [spend(txid(80), 0, A, 9_000)], [pay(ME, 8_000)], height=100))
    child = norm(tx(1, [spend(txid(9), 0, ME, 8_000)], [pay(B, 7_000)], height=100))
    # By id alone the child (1) would come first.
    assert [t.txid for t in order_transactions([child, parent])] == [txid(9), txid(1)]


def test_the_order_of_a_block_is_the_same_whatever_order_it_was_given_in():
    a = norm(tx(5, [spend(txid(80), 0, A, 9_000)], [pay(ME, 4_000), pay(B, 4_000)], height=100))
    b = norm(tx(4, [spend(txid(5), 0, ME, 4_000)], [pay(C, 3_000)], height=100))
    c = norm(tx(3, [spend(txid(5), 1, B, 4_000)], [pay(D, 3_000)], height=100))
    d = norm(
        tx(
            2,
            [spend(txid(4), 0, C, 3_000), spend(txid(3), 0, D, 3_000)],
            [pay(A, 5_000)],
            height=100,
        )
    )
    e = norm(tx(1, [spend(txid(81), 0, A, 9_000)], [pay(B, 8_000)], height=100))

    seen = {
        tuple(t.txid for t in order_transactions(p))
        for p in itertools.permutations([a, b, c, d, e])
    }

    assert len(seen) == 1
    order = seen.pop()
    assert order.index(txid(5)) < order.index(txid(4)) < order.index(txid(2))
    assert order.index(txid(5)) < order.index(txid(3)) < order.index(txid(2))


# --- one address's part ------------------------------------------------------------------------


def test_an_address_that_only_receives_is_a_receiver():
    mine = address_activity(
        norm(tx(1, [spend(txid(9), 0, A, 9_000)], [pay(ME, 5_000), pay(B, 3_000)])), ME
    )
    assert (mine.received_raw, mine.sent_raw, mine.net_raw) == ("5000", "0", "5000")
    assert mine.role == "receiver" and mine.complete


def test_an_address_that_funds_a_transaction_is_a_sender_and_the_fee_falls_on_it():
    mine = address_activity(norm(tx(1, [spend(txid(9), 0, ME, 9_000)], [pay(B, 8_000)])), ME)
    assert (mine.received_raw, mine.sent_raw, mine.net_raw, mine.role) == (
        "0",
        "9000",
        "-9000",
        "sender",
    )


def test_an_address_that_gets_change_back_is_both_and_only_the_difference_leaves():
    mine = address_activity(
        norm(tx(1, [spend(txid(9), 0, ME, 9_000)], [pay(B, 5_000), pay(ME, 3_500)])), ME
    )
    assert (mine.received_raw, mine.sent_raw, mine.net_raw, mine.role) == (
        "3500",
        "9000",
        "-5500",
        "both",
    )
    assert (mine.inputs_from_address, mine.outputs_to_address) == (1, 1)


def test_several_inputs_and_outputs_of_one_address_are_added_up():
    raw = tx(
        1,
        [spend(txid(9), 0, ME, 4_000), spend(txid(8), 1, ME, 2_000), spend(txid(7), 0, A, 1_000)],
        [pay(ME, 1_500), pay(ME, 500), pay(B, 4_500)],
    )
    mine = address_activity(norm(raw), ME)
    assert (mine.sent_raw, mine.received_raw, mine.net_raw) == ("6000", "2000", "-4000")


def test_an_address_with_no_part_has_no_activity():
    assert address_activity(norm(tx(1, [spend(txid(9), 0, A, 9_000)], [pay(B, 8_000)])), ME) is None


def test_the_amount_sent_is_marked_possibly_short_when_an_input_could_not_be_resolved():
    raw = tx(1, [spend(txid(9), 0, ME, 4_000), spend(txid(8), 0, A, 1_000)], [pay(B, 4_500)])
    raw["vin"][1]["prevout"] = None
    mine = address_activity(norm(raw), ME)
    assert mine.sent_raw == "4000" and mine.complete is False


def test_on_a_real_transaction_that_reuses_an_address_the_net_is_the_payment_plus_the_fee():
    transaction = real("self_change")
    reused = next(
        o.destination_address
        for o in transaction.outputs
        if o.destination_address in {i.source_address for i in transaction.inputs}
    )
    mine = address_activity(transaction, reused)
    paid_out = sum(int(o.value_raw) for o in transaction.outputs if o.destination_address != reused)
    assert mine.role == "both" and -int(mine.net_raw) == paid_out + int(transaction.fee_raw)


# --- CoinJoin ----------------------------------------------------------------------------------


def mix(parties, equal, *, value=100_000, extra=1):
    """`parties` different funders, `equal` outputs of the same value to different addresses."""
    vout = [pay(p2pkh(200 + n), value) for n in range(equal)]
    vout += [pay(p2pkh(300 + n), 1_234 + n) for n in range(extra)]
    each = -(-sum(o["value"] for o in vout) // parties) + 500  # together they cover it, plus a fee
    vin = [spend(txid(900 + n), 0, p2pkh(100 + n), each) for n in range(parties)]
    return norm(tx(1, vin, vout))


def test_a_real_transaction_with_many_funders_and_equal_outputs_is_a_likely_coinjoin():
    signal = detect_coinjoin(real("coinjoin_like"))
    assert (signal.strength, signal.equal_outputs, signal.input_parties) == ("likely", 7, 18)
    assert signal.equal_value_raw == "33554432"


def test_a_real_payout_with_many_equal_outputs_but_one_funder_is_not_a_coinjoin():
    transaction = real("batch_payout_equal_outputs")
    assert len({i.source_address for i in transaction.inputs}) == 1
    assert detect_coinjoin(transaction) is None


def test_real_equal_dust_outputs_from_a_few_funders_are_not_a_coinjoin():
    assert detect_coinjoin(real("inscription_dust_batch")) is None


@pytest.mark.parametrize(
    ("parties", "equal", "strength"),
    [
        (3, 3, "possible"),
        (4, 4, "possible"),
        (COINJOIN_LIKELY_EQUAL, COINJOIN_LIKELY_EQUAL, "likely"),
        (9, 6, "likely"),
        (3, 2, None),  # two equal outputs are an ordinary payment pair
        (2, 3, None),  # fewer funders than equal outputs: one party can pay three people
        (1, 3, None),
    ],
)
def test_the_signal_needs_enough_equal_outputs_and_at_least_as_many_funders(
    parties, equal, strength
):
    signal = detect_coinjoin(mix(parties, equal))
    assert (signal.strength if signal else None) == strength


def test_equal_outputs_to_the_same_address_are_a_batch_not_a_mix():
    vin = [spend(txid(900 + n), 0, p2pkh(100 + n), 101_000) for n in range(4)]
    raw = tx(1, vin, [pay(A, 100_000)] * 3 + [pay(B, 50_000)])
    assert detect_coinjoin(norm(raw)) is None


def test_dust_sized_equal_outputs_are_never_a_mix():
    assert detect_coinjoin(mix(8, 6, value=330)) is None
    assert detect_coinjoin(mix(8, 6, value=1_000)) is not None  # the floor itself still counts


def test_a_coinbase_is_never_a_coinjoin():
    assert detect_coinjoin(real("coinbase")) is None


# --- shared ownership --------------------------------------------------------------------------


def test_addresses_that_sign_together_are_reported_as_probably_one_owner():
    result = common_inputs(real("consolidation_small"))
    assert len(result.addresses) == 4 and result.reliable
    assert len(set(result.addresses)) == 4


def test_one_funder_or_a_coinbase_says_nothing_about_ownership():
    assert (
        common_inputs(
            norm(
                tx(1, [spend(txid(9), 0, ME, 4_000), spend(txid(8), 0, ME, 2_000)], [pay(B, 5_000)])
            )
        )
        is None
    )
    assert common_inputs(real("coinbase")) is None


def test_in_a_coinjoin_signing_together_is_not_a_sign_of_one_owner():
    result = common_inputs(real("coinjoin_like"))
    assert len(result.addresses) == 18 and result.reliable is False


def test_inputs_with_no_address_form_are_left_out_of_the_count():
    raw = tx(
        1, [spend(txid(9), 0, None, 4_000, "p2pk"), spend(txid(8), 0, A, 2_000)], [pay(B, 5_000)]
    )
    assert common_inputs(norm(raw)) is None  # one known address only


# --- change ------------------------------------------------------------------------------------


def test_reusing_an_input_address_is_the_strongest_sign_of_change():
    # Both outputs are legacy like the input and odd in amount: only the reuse stands out.
    raw = tx(1, [spend(txid(9), 0, A, 9_000_000)], [pay(A, 2_345_678), pay(B, 6_543_210)])
    result = assess_change(norm(raw))
    assert labels(result) == {0: Likelihood.CHANGE, 1: Likelihood.PAYMENT}
    assert result.confidence is Confidence.HIGH
    assert "also funded the transaction" in result.outputs[0].reasons[0]
    assert "other output of a two-output transaction" in result.outputs[1].reasons[-1]


def test_a_script_type_that_only_one_output_shares_with_the_inputs_leans_change():
    raw = tx(
        1, [spend(txid(9), 0, A, 9_000_000)], [pay(B, 2_345_678), pay(p2sh(7), 6_543_210, "p2sh")]
    )
    result = assess_change(norm(raw))
    assert labels(result) == {0: Likelihood.CHANGE, 1: Likelihood.PAYMENT}
    assert result.confidence is Confidence.MEDIUM  # no address was reused, so no elimination step


def test_a_round_amount_alone_is_too_weak_to_call():
    raw = tx(1, [spend(txid(9), 0, A, 20_000_000)], [pay(B, 10_000_000), pay(C, 9_988_888)])
    result = assess_change(norm(raw))
    assert set(labels(result).values()) == {Likelihood.UNKNOWN}
    assert result.confidence is Confidence.LOW and result.notes == (
        "no signal separates the outputs clearly",
    )


def test_signals_that_agree_add_up_and_signals_that_disagree_cancel():
    agree = tx(
        1, [spend(txid(9), 0, A, 20_000_000)], [pay(B, 9_988_888), pay(p2sh(7), 10_000_000, "p2sh")]
    )
    assert labels(assess_change(norm(agree))) == {0: Likelihood.CHANGE, 1: Likelihood.PAYMENT}
    clash = tx(
        1, [spend(txid(9), 0, A, 20_000_000)], [pay(B, 10_000_000), pay(p2sh(7), 9_988_888, "p2sh")]
    )
    cancelled = assess_change(norm(clash))
    assert set(labels(cancelled).values()) == {Likelihood.UNKNOWN}
    assert cancelled.confidence is Confidence.LOW  # a signal that fired but decided nothing


def test_an_amount_is_round_only_when_it_is_a_whole_number_of_thousandths_of_a_bitcoin():
    # 150,000 satoshis is 0.0015 BTC: not round, so it does not tilt the script-type call.
    raw = tx(
        1,
        [spend(txid(9), 0, A, 12_000_000)],
        [pay(B, 150_000), pay(p2sh(7), 9_876_543, "p2sh")],
    )
    assert labels(assess_change(norm(raw))) == {0: Likelihood.CHANGE, 1: Likelihood.PAYMENT}


def test_a_script_type_shared_by_several_outputs_says_nothing():
    raw = tx(
        1,
        [spend(txid(9), 0, A, 9_000_000)],
        [pay(B, 3_333_333), pay(C, 3_111_111), pay(D, 2_444_444)],
    )
    assert set(labels(assess_change(norm(raw))).values()) == {Likelihood.UNKNOWN}


def test_the_other_output_is_only_called_a_payment_when_there_are_exactly_two():
    raw = tx(
        1,
        [spend(txid(9), 0, A, 9_000_000)],
        [pay(A, 2_345_678), pay(B, 4_000_001), pay(C, 2_654_321)],
    )
    assert labels(assess_change(norm(raw))) == {
        0: Likelihood.CHANGE,
        1: Likelihood.UNKNOWN,
        2: Likelihood.UNKNOWN,
    }


def test_outputs_that_carry_no_value_are_not_part_of_the_question():
    raw = tx(
        1,
        [spend(txid(9), 0, A, 9_000_000)],
        [pay(A, 2_345_678), pay(B, 6_543_210), pay(None, 0, "op_return"), pay(C, 0)],
    )
    assert set(labels(assess_change(norm(raw)))) == {0, 1}
    # Coins burned in an OP_RETURN are not a payment to anyone either.
    burn = tx(
        1,
        [spend(txid(9), 0, A, 9_000_000)],
        [pay(A, 2_345_678), pay(B, 6_543_210), pay(None, 1_000, "op_return")],
    )
    assert set(labels(assess_change(norm(burn)))) == {0, 1}


def test_a_single_output_or_none_leaves_nothing_to_tell_apart():
    one = assess_change(real("consolidation_small"))
    assert labels(one) == {0: Likelihood.UNKNOWN} and "single output" in one.notes[0]
    none = assess_change(real("op_return_only"))
    assert none.outputs == () and "no output carries value" in none.notes[0]


def test_a_coinbase_has_no_change():
    assessment = assess_change(real("coinbase"))
    assert assessment.outputs == () and "coinbase" in assessment.notes[0]


def test_in_a_coinjoin_the_change_heuristics_are_withheld():
    assessment = assess_change(real("coinjoin_like"))
    assert set(labels(assessment).values()) == {Likelihood.UNKNOWN}
    assert "CoinJoin" in assessment.notes[0] and assessment.confidence is Confidence.LOW


def test_a_real_payout_to_many_is_not_labelled_change_because_of_a_shared_script_type():
    # 32 of its 51 outputs have the input's script type. That is no sign of change.
    assessment = assess_change(real("many_outputs_payout"))
    assert set(labels(assessment).values()) == {Likelihood.UNKNOWN}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("payment_with_change", {Likelihood.UNKNOWN}),
        ("self_change", {Likelihood.CHANGE, Likelihood.PAYMENT}),
        ("legacy_p2pkh", {Likelihood.CHANGE, Likelihood.PAYMENT}),
        ("taproot_small", {Likelihood.CHANGE, Likelihood.PAYMENT}),
    ],
)
def test_real_transactions_are_called_only_as_far_as_their_signals_go(name, expected):
    assert set(labels(assess_change(real(name))).values()) == expected


def test_a_real_legacy_transaction_leans_on_script_type_and_says_so():
    transaction = real("legacy_p2pkh")
    result = assess_change(transaction)
    change = next(o for o in result.outputs if o.likelihood is Likelihood.CHANGE)
    assert transaction.outputs[change.index].script_type == "p2pkh"  # the inputs' own type
    assert result.confidence is Confidence.MEDIUM and "same script type" in change.reasons[0]


def test_a_real_address_reuse_is_found_with_high_confidence():
    transaction = real("self_change")
    result = assess_change(transaction)
    change = next(o for o in result.outputs if o.likelihood is Likelihood.CHANGE)
    funders = {i.source_address for i in transaction.inputs}
    assert transaction.outputs[change.index].destination_address in funders
    assert result.confidence is Confidence.HIGH


# --- everything together -----------------------------------------------------------------------


def test_an_analysis_names_every_inference_as_one_and_is_plain_json():
    result = analyze(real("coinjoin_like")).to_dict()

    json.dumps(result)
    assert result["inferred"] is True
    assert result["coinjoin"]["inferred"] is True and result["coinjoin"]["strength"] == "likely"
    assert (
        result["common_inputs"]["inferred"] is True and result["common_inputs"]["reliable"] is False
    )
    assert result["change"]["inferred"] is True
    quiet = analyze(norm(tx(1, [coinbase_input()], [pay(A, 5_000)]))).to_dict()
    assert quiet["coinjoin"] is None and quiet["common_inputs"] is None
