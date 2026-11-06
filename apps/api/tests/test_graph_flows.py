"""Turning stored records into the movements the graph is made of."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from app.chains.bitcoin.models import BitcoinTransaction, TxInput, TxOutput
from app.chains.models import Asset, DataQuality, Transfer, TransferType
from app.graph.flows import (
    COINBASE,
    NO_ADDRESS,
    NO_COUNTERPARTY,
    OUTSIDE_WINDOW,
    ROUNDED_TO_ZERO,
    SELF,
    ZERO_VALUE,
    account_flows,
    bitcoin_flows,
)
from app.graph.model import Basis
from tests.graph_helpers import CHAIN_ID, NOW, addr, at

ME = addr(1)
OTHER = addr(2)
THIRD = addr(3)


def transfer(sender=OTHER, to=ME, amount="1000", **changes) -> Transfer:
    base = Transfer(
        chain_id=CHAIN_ID,
        transaction_hash="0x" + "a" * 64,
        event_key="native",
        transfer_type=TransferType.NATIVE,
        block_number=100,
        timestamp=at(1),
        from_address=sender,
        to_address=to,
        asset=Asset(symbol="ETH", contract_address=None, decimals=18),
        amount_raw=amount,
        provider="etherscan",
        data_quality=DataQuality.INDEXED,
    )
    return replace(base, **changes)


# --- account chains ----------------------------------------------------------------------------


def test_a_transfer_is_a_flow_from_its_sender_to_its_receiver():
    result = account_flows([transfer(OTHER, ME, "1000")], ME, since=None)

    (item,) = result.flows
    assert (item.source, item.target, item.amount) == (OTHER, ME, 1000)
    assert item.asset.key == "native" and item.asset.symbol == "ETH" and item.asset.decimals == 18
    assert item.basis is Basis.EXACT and not result.ignored


def test_zero_value_events_are_not_flows_because_address_poisoning_leaves_them():
    result = account_flows([transfer(amount="0")], ME, since=None)

    assert result.flows == [] and result.ignored[ZERO_VALUE] == 1


def test_a_movement_to_oneself_or_with_no_other_end_is_not_a_flow():
    result = account_flows(
        [transfer(ME, ME), transfer(None, ME, event_key="mint"), transfer(ME, None)],
        ME,
        since=None,
    )

    assert result.flows == []
    assert result.ignored[SELF] == 1 and result.ignored[NO_COUNTERPARTY] == 2


def test_a_transfer_the_address_is_not_part_of_is_not_its_flow():
    result = account_flows([transfer(OTHER, THIRD)], ME, since=None)

    assert result.flows == [] and not result.ignored


def test_the_window_drops_older_movements_and_says_how_many():
    old = transfer(timestamp=at(1) - timedelta(days=400), event_key="old")
    result = account_flows([old, transfer()], ME, since=NOW - timedelta(days=365))

    assert len(result.flows) == 1 and result.ignored[OUTSIDE_WINDOW] == 1


def test_a_movement_with_no_time_cannot_be_judged_against_the_window_so_it_stays():
    result = account_flows([transfer(timestamp=None)], ME, since=NOW)

    assert len(result.flows) == 1 and result.flows[0].timestamp is None


def test_a_token_is_told_apart_by_its_contract_and_a_tron_token_by_its_id():
    token = Asset(symbol="USDT", contract_address="0xcontract", decimals=6)
    trc10 = Asset(symbol="BTT", contract_address=None, decimals=6, asset_id="1002000")
    result = account_flows(
        [
            transfer(asset=token, event_key="a"),
            transfer(asset=trc10, event_key="b"),
            transfer(asset=Asset(None, None, None), event_key="c"),
        ],
        ME,
        since=None,
        native_symbol="ETH",
    )

    keys = [(f.asset.key, f.asset.symbol, f.asset.decimals) for f in result.flows]
    assert keys == [
        ("0xcontract", "USDT", 6),
        ("1002000", "BTT", 6),
        ("native", "ETH", None),
    ]


# --- Bitcoin -----------------------------------------------------------------------------------

A, B, C, D = "bc1qa", "bc1qb", "bc1qc", "bc1qd"


def bitcoin(
    inputs: list[tuple[str | None, int]],
    outputs: list[tuple[str | None, int]],
    *,
    txid: str = "t" * 64,
    **changes,
) -> BitcoinTransaction:
    base = BitcoinTransaction(
        chain_id="bip122:000000000019d6689c085ae165831e93",
        txid=txid,
        block_height=800_000,
        block_hash="b" * 64,
        timestamp=at(1),
        inputs=tuple(
            TxInput(i, "p" * 64, i, address, str(value), "p2wpkh")
            for i, (address, value) in enumerate(inputs)
        ),
        outputs=tuple(
            TxOutput(i, address, str(value), "p2wpkh") for i, (address, value) in enumerate(outputs)
        ),
        fee_raw="0",
        size=200,
        weight=800,
        version=2,
        locktime=0,
        provider="esplora",
        data_quality=DataQuality.INDEXED,
    )
    return replace(base, **changes)


def links(result):
    return {(f.source, f.target): (f.amount, f.basis) for f in result.flows}


def test_one_paying_address_paid_every_output_so_the_links_are_exact():
    tx = bitcoin([(A, 10_000)], [(B, 6_000), (C, 3_000), (A, 900)])  # the last is change

    result = bitcoin_flows([tx], A, since=None)

    assert links(result) == {(A, B): (6_000, Basis.EXACT), (A, C): (3_000, Basis.EXACT)}


def test_with_several_paying_addresses_each_output_is_shared_in_proportion():
    tx = bitcoin([(A, 6_000), (B, 4_000)], [(C, 7_000), (D, 3_000)])

    result = bitcoin_flows([tx], C, since=None)

    assert links(result) == {(A, C): (4_200, Basis.ALLOCATED), (B, C): (2_800, Basis.ALLOCATED)}


def test_the_same_link_has_the_same_amount_whichever_end_it_is_worked_out_from():
    tx = bitcoin([(A, 6_001), (B, 3_999)], [(C, 7_001), (D, 2_999)])

    from_sender = links(bitcoin_flows([tx], A, since=None))
    from_receiver = links(bitcoin_flows([tx], C, since=None))

    assert from_sender[(A, C)] == from_receiver[(A, C)]


def test_shares_are_rounded_down_so_nothing_is_invented():
    tx = bitcoin([(A, 1), (B, 1), (C, 1)], [(D, 100)])

    amounts = [f.amount for f in bitcoin_flows([tx], D, since=None).flows]

    assert amounts == [33, 33, 33]


def test_a_share_below_one_satoshi_is_counted_not_shown():
    tx = bitcoin([(A, 1), (B, 999_999)], [(C, 1_000)])

    result = bitcoin_flows([tx], C, since=None)

    assert links(result) == {(B, C): (999, Basis.ALLOCATED)}
    assert result.ignored[ROUNDED_TO_ZERO] == 1


def test_an_input_without_an_address_makes_the_link_allocated_even_with_one_known_payer():
    tx = bitcoin([(A, 5_000), (None, 5_000)], [(B, 9_000)])

    result = bitcoin_flows([tx], B, since=None)

    assert links(result) == {(A, B): (4_500, Basis.ALLOCATED)}


def test_a_payment_back_to_the_paying_address_is_change_not_a_flow():
    tx = bitcoin([(A, 10_000)], [(A, 9_000)])

    assert bitcoin_flows([tx], A, since=None).flows == []


def test_coinbase_transactions_have_no_sender_so_there_is_no_flow():
    tx = bitcoin([(None, 5_000_000_000)], [(A, 5_000_000_000)])
    tx = replace(tx, inputs=(replace(tx.inputs[0], is_coinbase=True, value_raw=None),))

    result = bitcoin_flows([tx], A, since=None)

    assert result.flows == [] and result.ignored[COINBASE] == 1


def test_outputs_with_no_address_are_counted_and_do_not_break_the_allocation():
    tx = bitcoin([(A, 10_000)], [(B, 6_000), (None, 0), (None, 3_000)])

    result = bitcoin_flows([tx], B, since=None)

    assert links(result) == {(A, B): (6_000, Basis.EXACT)}
    assert result.ignored[NO_ADDRESS] == 2


def test_a_transaction_older_than_the_window_is_left_out_whole():
    tx = bitcoin([(A, 10_000)], [(B, 6_000)], timestamp=NOW - timedelta(days=400))

    result = bitcoin_flows([tx], B, since=NOW - timedelta(days=365))

    assert result.flows == [] and result.ignored[OUTSIDE_WINDOW] == 1


def test_a_flow_is_named_by_its_transaction_and_its_two_addresses():
    tx = bitcoin([(A, 10_000)], [(B, 6_000)], txid="9" * 64)

    (item,) = bitcoin_flows([tx], B, since=None).flows

    assert item.transaction == "9" * 64 and item.event == f"{A}>{B}"
    assert item.asset.symbol == "BTC" and item.asset.decimals == 8


# --- pinned down by mutation checks -------------------------------------------------------------


def test_a_movement_exactly_at_the_start_of_the_window_is_inside_it():
    edge = at(1)

    result = account_flows([transfer(timestamp=edge)], ME, since=edge)

    assert len(result.flows) == 1 and not result.ignored


def test_a_bitcoin_transaction_exactly_at_the_start_of_the_window_is_inside_it():
    tx = bitcoin([(A, 10_000)], [(B, 6_000)])

    assert len(bitcoin_flows([tx], B, since=tx.timestamp).flows) == 1


def test_a_transaction_with_nothing_spent_does_not_divide_by_zero():
    tx = bitcoin([(A, 0)], [(B, 6_000)])

    assert bitcoin_flows([tx], B, since=None).flows == []
