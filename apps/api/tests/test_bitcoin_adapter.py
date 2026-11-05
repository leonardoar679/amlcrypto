"""The Bitcoin adapter over the in-memory Esplora: routing, cursors, the height and transaction
details with their spend lists."""

from __future__ import annotations

import pytest

from app.chains.bitcoin.models import SpendState
from app.chains.cursors import InvalidCursor
from app.chains.models import Confirmation, DataQuality
from app.providers.base import AllProvidersFailed
from tests.fake_esplora import HEAD, p2pkh, pay, spend, tx, txid
from tests.helpers import load_fixture, run
from tests.rigs import Rig

ME = p2pkh(1)


def history(rig: Rig, count: int) -> None:
    rig.esplora.add(
        *[
            tx(
                n,
                [spend(txid(5000 + n), 0, p2pkh(9000 + n), 10_000)],
                [pay(ME, 9_000)],
                height=100 + n,
            )
            for n in range(1, count + 1)
        ]
    )


def stock_real(rig: Rig, name: str, *, depth: int = 10):
    data = load_fixture(f"bitcoin/{name}")
    rig.esplora.add(data["tx"])
    rig.esplora.head = data["tx"]["status"]["block_height"] + depth - 1
    return data["tx"]


# --- sources, height and the node's figures -------------------------------------------------------


def test_bitcoin_is_read_through_esplora_which_needs_no_key():
    assert Rig().bitcoin_adapter().source_names == ["esplora"]
    no_keys = Rig(etherscan_key=None, alchemy_key=None, trongrid_key=None)
    assert no_keys.bitcoin_adapter().source_names == ["esplora"]


def test_a_read_started_now_is_complete_through_the_tip():
    # Only confirmed transactions are read, and the tip's own are among them.
    assert run(Rig().bitcoin_adapter().get_heights()) == (HEAD, HEAD)


def test_the_nodes_figures_for_an_address_come_through():
    rig = Rig()
    history(rig, 4)
    summary = run(rig.bitcoin_adapter().get_address_summary(ME))
    assert (summary.tx_count, summary.funded_raw, summary.balance_raw) == (4, "36000", "36000")


# --- cursors -----------------------------------------------------------------------------------


def test_a_page_cursor_names_its_source_and_leads_to_the_next_page():
    rig = Rig()
    history(rig, 40)
    adapter = rig.bitcoin_adapter()

    async def scenario():
        first = await adapter.get_address_page(ME)
        second = await adapter.get_address_page(ME, cursor=first.next_cursor)
        return first, second

    first, second = run(scenario())

    assert first.next_cursor == f"esplora|{txid(16)}" and second.next_cursor is None
    assert [t.txid for t in first.items + second.items] == [txid(n) for n in range(40, 0, -1)]


def test_a_cursor_from_somewhere_else_is_refused_before_any_request():
    rig = Rig()
    adapter = rig.bitcoin_adapter()
    for cursor in ("etherscan|5:0", "trongrid|fp3", "nonsense", "|", "esplora|not-an-id"):
        with pytest.raises(InvalidCursor):
            run(adapter.get_address_page(ME, cursor=cursor))
    assert rig.esplora.requests == []


# --- one transaction ---------------------------------------------------------------------------


def test_a_real_transaction_comes_with_its_depth_and_which_outputs_are_spent():
    rig = Rig()
    raw = stock_real(rig, "payment_with_change", depth=6)
    rig.esplora.add(
        tx(
            1,
            [
                spend(
                    raw["txid"],
                    0,
                    raw["vout"][0]["scriptpubkey_address"],
                    raw["vout"][0]["value"],
                    "v0_p2wpkh",
                )
            ],
            [pay(p2pkh(7), 1_000)],
        )
    )

    detail = run(rig.bitcoin_adapter().get_transaction(raw["txid"]))

    transaction = detail.transaction
    assert transaction.provider == "esplora" and transaction.data_quality is DataQuality.INDEXED
    assert transaction.confirmations == 6
    assert transaction.confirmation_status is Confirmation.CONFIRMED
    assert [o.spend_state for o in transaction.outputs] == [SpendState.SPENT, SpendState.UNSPENT]
    assert transaction.outputs[0].spent_by_txid == txid(1)
    assert detail.coverage == ()


def test_one_block_short_of_the_depth_is_not_yet_confirmed():
    rig = Rig()
    raw = stock_real(rig, "payment_with_change", depth=5)
    detail = run(rig.bitcoin_adapter().get_transaction(raw["txid"]))
    assert detail.transaction.confirmations == 5
    assert detail.transaction.confirmation_status is Confirmation.UNCONFIRMED


def test_a_transaction_still_in_the_mempool_is_unconfirmed_with_no_block():
    rig = Rig()
    rig.esplora.add(tx(1, [spend(txid(9), 0, ME, 5_000)], [pay(p2pkh(2), 4_000)], height=None))
    detail = run(rig.bitcoin_adapter().get_transaction(txid(1)))
    assert detail.transaction.block_height is None and detail.transaction.confirmations == 0
    assert detail.transaction.confirmation_status is Confirmation.UNCONFIRMED


def test_the_id_may_be_typed_in_capitals_or_with_spaces():
    rig = Rig()
    stock_real(rig, "payment_with_change")
    wanted = load_fixture("bitcoin/payment_with_change")["tx"]["txid"]
    adapter = rig.bitcoin_adapter()
    assert run(adapter.get_transaction(wanted.upper())).transaction.txid == wanted
    assert run(adapter.get_transaction(f"  {wanted}  ")).transaction.txid == wanted


def test_an_unknown_transaction_is_none_and_a_bad_id_is_an_error_that_costs_nothing():
    rig = Rig()
    adapter = rig.bitcoin_adapter()
    assert run(adapter.get_transaction("ab" * 32)) is None
    sent = len(rig.esplora.requests)
    for bad in ("", "1234", "zz" * 32, "ab" * 33, "0x" + "ab" * 32):
        with pytest.raises(ValueError, match="not a transaction id"):
            run(adapter.get_transaction(bad))
    assert len(rig.esplora.requests) == sent


def test_not_found_is_not_claimed_when_the_source_could_not_answer():
    rig = Rig()
    rig.esplora.queued = [(403, "Forbidden")]
    with pytest.raises(AllProvidersFailed) as info:
        run(rig.bitcoin_adapter().get_transaction("ab" * 32))
    assert [name for name, _ in info.value.failures] == ["esplora"]


def test_without_the_spend_list_the_transaction_is_still_given_with_the_gap_stated():
    rig = Rig()
    raw = stock_real(rig, "payment_with_change")
    rig.esplora.broken_outspends = True

    detail = run(rig.bitcoin_adapter().get_transaction(raw["txid"]))

    assert all(o.spend_state is SpendState.UNKNOWN for o in detail.transaction.outputs)
    assert detail.coverage == (
        "which outputs are spent is unknown: the spend list could not be read",
    )


def test_without_the_height_the_depth_is_unknown_and_the_gap_is_stated():
    rig = Rig()
    raw = stock_real(rig, "payment_with_change")
    rig.esplora.broken_tip = True

    detail = run(rig.bitcoin_adapter().get_transaction(raw["txid"]))

    assert detail.transaction.confirmations is None
    assert detail.transaction.confirmation_status is Confirmation.UNKNOWN
    assert detail.coverage == ("confirmations are unknown: the chain's height could not be read",)


def test_a_spend_list_that_does_not_fit_the_transaction_leaves_the_state_unknown_and_says_so():
    rig = Rig()
    raw = stock_real(rig, "payment_with_change")
    original = rig.esplora.outspends  # a spend list one entry short of the outputs
    rig.esplora.outspends = lambda found: original(found)[:1]

    detail = run(rig.bitcoin_adapter().get_transaction(raw["txid"]))

    assert all(o.spend_state is SpendState.UNKNOWN for o in detail.transaction.outputs)
    assert "the spend list was unreadable" in detail.coverage[0]
