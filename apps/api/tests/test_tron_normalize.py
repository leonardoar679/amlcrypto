"""TronGrid rows and transaction details become our records, checked against real TRON data.

The files in tests/fixtures/tron are real recent responses from TronGrid: a TRX transfer, a TRC-10
transfer, a contract call that carries TRX, a call that ran out of energy, a TRC-20 row, an
internal transfer, and the full details of three transactions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.chains.models import Confirmation, DataQuality, TransferType, TxStatus
from app.chains.tron.chains import TRON_CHAIN, get_tron_chain
from app.chains.tron.normalize import (
    TRANSFER_TOPIC,
    asset_id_of,
    confirmation_of,
    hex_to_base58,
    tron_address,
    tron_internal_transfer,
    tron_native_transfers,
    tron_transaction,
    tron_transaction_from_details,
    tron_transfers_from_details,
    tron_trc20_transfer,
    txid_of,
)
from app.chains.values import MalformedRow
from tests.helpers import load_fixture

CHAIN = TRON_CHAIN


def row_of(name):
    return load_fixture(f"tron/{name}")["row"]


def details_of(name):
    data = load_fixture(f"tron/{name}")
    return data["tx"], data["info"]


# --- addresses and scalars ---------------------------------------------------------------------


def test_hex_and_base58_addresses_are_the_same_address():
    usdt_hex = "41a614f803b6fd780986a42c78ec9c7f77e6ded13c"
    assert hex_to_base58(usdt_hex) == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    assert tron_address(usdt_hex) == tron_address("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
    assert tron_address(usdt_hex.upper()) == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        None,
        "T123",
        "41zz" + "0" * 38,
        "0x" + "ab" * 20,
        "a0" + "ab" * 20,  # 21 bytes of hex, but not a mainnet address: those start with 41
        "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6u",
    ],
)
def test_things_that_are_not_tron_addresses_are_refused(bad):
    with pytest.raises(MalformedRow):
        tron_address(bad)


def test_transaction_ids_are_64_hex_characters_in_lower_case():
    assert txid_of("AB" * 32) == "ab" * 32
    assert txid_of("0x" + "ab" * 32) == "ab" * 32
    for bad in ("", "12", "zz" * 32, None):
        with pytest.raises(MalformedRow):
            txid_of(bad)


def test_a_trc10_token_id_may_arrive_as_plain_text_or_as_hex_encoded_ascii():
    assert asset_id_of("1005190") == "1005190"  # the address feed
    assert asset_id_of("31303035313933") == "1005193"  # the block endpoints
    assert asset_id_of("100519") == "100519"  # even length but not hex of digits: plain
    assert asset_id_of("BitTorrent") == "BitTorrent"  # a token named before ids existed
    with pytest.raises(MalformedRow):
        asset_id_of("")


def test_the_chain_lookup_knows_tron_only():
    assert get_tron_chain("tron") is CHAIN and get_tron_chain("tron:728126428") is CHAIN
    assert get_tron_chain("ethereum") is None and get_tron_chain("nonsense") is None
    assert CHAIN.incremental_start(1000) == 940 and CHAIN.incremental_start(10) == 0


def test_trx_is_counted_in_sun_six_decimals_down():
    assert (CHAIN.native.symbol, CHAIN.native.decimals) == ("TRX", 6)
    assert CHAIN.native.contract_address is None


def test_a_transaction_is_confirmed_exactly_when_it_is_as_deep_as_the_chain_says():
    depth = CHAIN.confirmation_depth
    assert confirmation_of(None, CHAIN) is Confirmation.UNKNOWN
    assert confirmation_of(0, CHAIN) is Confirmation.UNCONFIRMED
    assert confirmation_of(depth - 1, CHAIN) is Confirmation.UNCONFIRMED
    assert confirmation_of(depth, CHAIN) is Confirmation.CONFIRMED
    assert confirmation_of(depth + 1, CHAIN) is Confirmation.CONFIRMED


# --- address feed rows (real) ------------------------------------------------------------------


def test_a_real_trx_transfer_is_a_transaction_and_a_native_movement():
    row = row_of("feed_trx")
    transaction = tron_transaction(row, CHAIN)
    (transfer,) = tron_native_transfers(row, CHAIN)

    value = row["raw_data"]["contract"][0]["parameter"]["value"]
    assert transaction.status is TxStatus.SUCCESS and not transaction.is_contract_call
    assert transaction.confirmation_status is Confirmation.CONFIRMED  # read with only_confirmed
    assert transaction.fee_raw == str(row["ret"][0]["fee"])
    assert transaction.block_number == row["blockNumber"]
    assert transaction.timestamp == datetime.fromtimestamp(row["block_timestamp"] / 1000, UTC)
    assert transfer.transfer_type is TransferType.NATIVE and transfer.event_key == "native"
    assert transfer.amount_raw == str(value["amount"]) and transfer.asset == CHAIN.native
    assert (transfer.from_address, transfer.to_address) == (
        hex_to_base58(value["owner_address"]),
        hex_to_base58(value["to_address"]),
    )
    assert transfer.confirmation_status is Confirmation.CONFIRMED
    assert transfer.data_quality is DataQuality.INDEXED


def test_a_real_trc10_transfer_keeps_the_token_id_and_does_not_guess_the_decimals():
    row = row_of("feed_trc10")
    (transfer,) = tron_native_transfers(row, CHAIN)
    value = row["raw_data"]["contract"][0]["parameter"]["value"]

    assert transfer.transfer_type is TransferType.TRC10
    assert transfer.asset.asset_id == asset_id_of(value["asset_name"])
    assert (transfer.asset.symbol, transfer.asset.decimals) == (None, None)
    assert transfer.amount_raw == str(value["amount"])
    assert transfer.event_key.startswith(f"trc10:{transfer.asset.asset_id}:")


def test_a_real_contract_call_that_carries_trx_moves_it_to_the_contract():
    row = row_of("feed_trigger_value")
    transaction = tron_transaction(row, CHAIN)
    (transfer,) = tron_native_transfers(row, CHAIN)
    value = row["raw_data"]["contract"][0]["parameter"]["value"]

    assert transaction.is_contract_call and transaction.method_id == "0x" + value["data"][:8]
    assert transaction.value_raw == str(value["call_value"])
    assert transfer.transfer_type is TransferType.NATIVE
    assert transfer.to_address == hex_to_base58(value["contract_address"])


def test_a_real_failed_call_is_recorded_but_moved_nothing():
    row = row_of("feed_failed")
    transaction = tron_transaction(row, CHAIN)
    assert transaction.status is TxStatus.FAILED and transaction.is_contract_call
    assert tron_native_transfers(row, CHAIN) == []


@pytest.mark.parametrize("result", ["OUT_OF_ENERGY", "REVERT", "FAILED", "SOMETHING_NEW"])
@pytest.mark.parametrize("name", ["feed_trx", "feed_trc10", "feed_trigger_value"])
def test_a_transaction_that_did_not_succeed_moved_nothing_even_if_it_carried_value(name, result):
    row = row_of(name)
    row["ret"][0]["contractRet"] = result

    assert tron_transaction(row, CHAIN).status is TxStatus.FAILED
    assert tron_native_transfers(row, CHAIN) == []


@pytest.mark.parametrize("name", ["feed_trx", "feed_trc10", "feed_trigger_value"])
def test_a_contract_that_moves_zero_moves_nothing(name):
    row = row_of(name)
    value = row["raw_data"]["contract"][0]["parameter"]["value"]
    value["call_value" if name == "feed_trigger_value" else "amount"] = 0

    assert tron_native_transfers(row, CHAIN) == []
    assert tron_transaction(row, CHAIN).value_raw == "0"  # the transaction itself is still there


def test_a_real_trc20_row_carries_its_token_but_no_block_number():
    row = row_of("trc20_row")
    transfer = tron_trc20_transfer(row, CHAIN)

    assert transfer.confirmation_status is Confirmation.CONFIRMED
    assert transfer.transfer_type is TransferType.TRC20
    assert (transfer.asset.symbol, transfer.asset.decimals) == ("USDT", 6)
    assert transfer.asset.contract_address == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    assert transfer.amount_raw == row["value"]
    assert transfer.block_number is None  # the feed gives only a time
    assert transfer.timestamp == datetime.fromtimestamp(row["block_timestamp"] / 1000, UTC)
    assert transfer.event_key == (
        f"trc20:{transfer.asset.contract_address}:{row['from']}:{row['to']}:{row['value']}"
    )


def test_a_real_internal_row_is_keyed_by_the_id_tron_gave_it():
    row = row_of("internal_row")
    transfer = tron_internal_transfer(row, CHAIN)

    assert transfer.transfer_type is TransferType.INTERNAL
    assert transfer.confirmation_status is Confirmation.CONFIRMED
    assert transfer.event_key == f"internal:{row['internal_tx_id']}"
    assert transfer.amount_raw == str(row["data"]["call_value"]["_"])
    assert transfer.transaction_hash == row["tx_id"] and transfer.block_number is None


def test_internal_calls_that_moved_no_trx_or_were_rejected_are_not_transfers():
    row = row_of("internal_row")
    zero = {**row, "data": {"note": "call", "rejected": False}}
    rejected = {**row, "data": {**row["data"], "rejected": True}}
    other_token = {**row, "data": {"note": "call", "rejected": False, "call_value": {"1002000": 5}}}
    assert tron_internal_transfer(zero, CHAIN) is None
    assert tron_internal_transfer(rejected, CHAIN) is None
    assert tron_internal_transfer(other_token, CHAIN) is None  # a TRC-10 amount is not TRX


def test_only_transfer_events_are_token_transfers():
    row = {**row_of("trc20_row"), "type": "Approval"}
    assert tron_trc20_transfer(row, CHAIN) is None


@pytest.mark.parametrize(
    ("builder", "name", "change"),
    [
        (tron_transaction, "feed_trx", {"txID": "short"}),
        (tron_transaction, "feed_trx", {"block_timestamp": "yesterday"}),
        (tron_native_transfers, "feed_trx", {"raw_data": {"contract": []}}),
        (tron_trc20_transfer, "trc20_row", {"value": "-5"}),
        (tron_trc20_transfer, "trc20_row", {"from": "not an address"}),
        (tron_trc20_transfer, "trc20_row", {"token_info": {"symbol": "X"}}),
        (tron_internal_transfer, "internal_row", {"from_address": "41zz"}),
    ],
)
def test_rows_that_cannot_be_what_they_claim_are_refused(builder, name, change):
    with pytest.raises(MalformedRow):
        builder({**row_of(name), **change}, CHAIN)


def test_a_dust_transfer_of_one_sun_is_a_transfer_not_noise_to_drop():
    row = row_of("feed_trx")
    row["raw_data"]["contract"][0]["parameter"]["value"]["amount"] = 1
    (transfer,) = tron_native_transfers(row, CHAIN)
    assert transfer.amount_raw == "1"


# --- transaction details (real) ----------------------------------------------------------------


def test_the_details_of_a_real_usdt_transfer_give_the_log_position_and_confirmations():
    tx, info = details_of("detail_trc20")
    head = info["blockNumber"] + 40

    transaction = tron_transaction_from_details(tx, info, CHAIN, head_block=head)
    transfers, unreadable = tron_transfers_from_details(tx, info, CHAIN, head_block=head)

    assert (
        transaction.status is TxStatus.SUCCESS and transaction.data_quality is DataQuality.DECODED
    )
    assert transaction.block_number == info["blockNumber"] and transaction.confirmations == 41
    assert transaction.confirmation_status is Confirmation.CONFIRMED and unreadable == 0
    (transfer,) = transfers
    log = info["log"][0]
    assert transfer.transfer_type is TransferType.TRC20 and transfer.log_index == 0
    assert transfer.confirmations == 41  # the movement is as deep as its transaction
    assert transfer.confirmation_status is Confirmation.CONFIRMED
    assert transfer.amount_raw == str(int(log["data"], 16))
    assert transfer.asset.contract_address == hex_to_base58("41" + log["address"])
    assert transfer.asset.decimals is None  # a log does not say; never guessed
    assert transfer.block_number == info["blockNumber"]


def test_the_details_of_a_real_trx_transfer_include_the_fee_and_one_movement():
    tx, info = details_of("detail_trx")
    transaction = tron_transaction_from_details(tx, info, CHAIN)
    transfers, _ = tron_transfers_from_details(tx, info, CHAIN)

    assert transaction.fee_raw == str(info["fee"])
    assert transaction.confirmation_status is Confirmation.UNKNOWN  # no head was given
    (transfer,) = transfers
    assert transfer.transfer_type is TransferType.NATIVE
    assert transfer.amount_raw == str(tx["raw_data"]["contract"][0]["parameter"]["value"]["amount"])


def test_the_details_of_a_real_swap_list_every_token_event_and_the_internal_trx():
    tx, info = details_of("detail_internal")
    transfers, unreadable = tron_transfers_from_details(tx, info, CHAIN)

    tokens = [t for t in transfers if t.transfer_type is TransferType.TRC20]
    internal = [t for t in transfers if t.transfer_type is TransferType.INTERNAL]
    topics = [log for log in info["log"] if log["topics"] and log["topics"][0] == TRANSFER_TOPIC]
    assert len(tokens) == len([log for log in topics if len(log["topics"]) == 3]) >= 3
    assert [t.log_index for t in tokens] == sorted(t.log_index for t in tokens)
    assert len({t.identity for t in transfers}) == len(transfers)  # nothing collides
    valued = [
        i
        for i in info["internal_transactions"]
        if any(v.get("callValue") for v in i["callValueInfo"])
    ]
    assert len(internal) == len(valued) == 2
    assert {t.event_key for t in internal} == {f"internal:{i['hash']}" for i in valued}
    assert unreadable == 0


def test_rejected_calls_and_token_values_inside_internal_calls_are_not_trx_movements():
    tx, info = details_of("detail_internal")
    valued = [
        i
        for i in info["internal_transactions"]
        if any(v.get("callValue") for v in i["callValueInfo"])
    ]
    assert len(valued) == 2

    def internal_of(change) -> list:
        changed = json.loads(json.dumps(info))
        change([i for i in changed["internal_transactions"] if i["hash"] == valued[0]["hash"]][0])
        found, _ = tron_transfers_from_details(tx, changed, CHAIN)
        return [t for t in found if t.transfer_type is TransferType.INTERNAL]

    baseline = internal_of(lambda i: None)
    assert len(baseline) == 2

    # A call that was rejected moved nothing.
    assert len(internal_of(lambda i: i.update(rejected=True))) == 1

    # A TRC-10 amount is not TRX: only the java-tron "tokenId" entry marks it, so a call that
    # carries just a token moves no TRX, and one that carries both moves only the TRX.
    token = {"tokenId": "1002000", "callValue": 5}
    assert len(internal_of(lambda i: i.update(callValueInfo=[token]))) == 1
    both = internal_of(lambda i: i.update(callValueInfo=[{"callValue": 3}, token]))
    (mixed,) = [t for t in both if t.event_key == f"internal:{valued[0]['hash']}"]
    assert mixed.amount_raw == "3" and len(both) == 2


def test_the_details_of_a_failed_transaction_prove_no_movement():
    row = row_of("feed_failed")
    tx = {"txID": row["txID"], "ret": row["ret"], "raw_data": row["raw_data"]}
    info = {
        "id": row["txID"],
        "blockNumber": row["blockNumber"],
        "blockTimeStamp": row["block_timestamp"],
    }
    assert tron_transaction_from_details(tx, info, CHAIN).status is TxStatus.FAILED
    assert tron_transfers_from_details(tx, info, CHAIN) == ([], 0)


def test_identical_token_events_in_one_transaction_are_told_apart_by_their_order():
    tx, info = details_of("detail_trc20")
    info = json.loads(json.dumps(info))
    info["log"] = [info["log"][0], info["log"][0], info["log"][0]]

    transfers, _ = tron_transfers_from_details(tx, info, CHAIN)

    keys = [t.event_key for t in transfers]
    assert keys[1] == keys[0] + "#1" and keys[2] == keys[0] + "#2"
    assert [t.log_index for t in transfers] == [0, 1, 2]


def test_nft_events_are_skipped_and_odd_shaped_ones_are_counted():
    tx, info = details_of("detail_trc20")
    info = json.loads(json.dumps(info))
    log = info["log"][0]
    nft = {**log, "topics": [*log["topics"], "00" * 32], "data": ""}
    odd = {**log, "data": "00ff"}
    info["log"] = [nft, odd, log]

    transfers, unreadable = tron_transfers_from_details(tx, info, CHAIN)

    assert len(transfers) == 1 and unreadable == 1
