"""The plain-text views: amounts, directions, and being plain about gaps."""

from __future__ import annotations

import pytest

from app.chains.models import Asset, DataQuality, Transfer, TransferType
from app.ingestion.fetcher import History, fetch_history
from app.ingestion.render import (
    direction,
    format_amount,
    render_history,
    render_transaction,
    short,
)
from tests.fake_alchemy import asset_row
from tests.fake_etherscan import ME, OTHER, token_row, tx_row
from tests.helpers import load_fixture, run
from tests.rigs import BSC, ETHEREUM, Rig


@pytest.mark.parametrize(
    ("raw", "decimals", "expected"),
    [
        ("1500000", 6, "1.5"),
        ("1000000", 6, "1"),
        ("42", 6, "0.000042"),
        ("1", 18, "0.000000000000000001"),
        ("0", 18, "0"),
        ("123", 0, "123"),
        ("100", 2, "1"),
        (
            str(2**256 - 1),
            18,
            "115792089237316195423570985008687907853269984665640564039457.584007913129639935",
        ),
        ("5", None, "5 (raw)"),
    ],
)
def test_amounts_are_shown_exactly_in_the_assets_own_units(raw, decimals, expected):
    assert format_amount(raw, decimals) == expected


def test_long_values_are_shortened_in_the_middle_and_short_ones_left_alone():
    assert short("0x" + "ab" * 20) == "0xababab..ababab"  # 8 characters, "..", 6 characters
    assert short("0x1234") == "0x1234" and short(None) == "-" and short("") == "-"


def make_transfer(sender, to) -> Transfer:
    return Transfer(
        chain_id="eip155:1",
        transaction_hash="0x" + "11" * 32,
        event_key="native",
        transfer_type=TransferType.NATIVE,
        block_number=1,
        timestamp=None,
        from_address=sender,
        to_address=to,
        asset=Asset("ETH", None, 18),
        amount_raw="1",
        provider="test",
        data_quality=DataQuality.INDEXED,
    )


def test_direction_is_relative_to_the_address_asked_about():
    assert direction(make_transfer(ME, OTHER), ME) == "out"
    assert direction(make_transfer(OTHER, ME), ME) == "in"
    assert direction(make_transfer(ME, ME), ME) == "self"


def read(rig, chain=ETHEREUM, **kwargs) -> History:
    return run(fetch_history(rig.adapter(chain), ME, **kwargs))


def test_a_complete_history_shows_amounts_directions_and_how_far_it_is_complete():
    rig = Rig()
    rig.etherscan.add("txlist", tx_row(5, OTHER, ME, value="1500000000000000000"))
    rig.etherscan.add("tokentx", token_row(6, ME, OTHER, value="42"))  # 6 decimals: 0.000042

    text = render_history(read(rig))

    assert f"eip155:1  {ME}" in text and "2 transfers (native 1, erc20 1)" in text
    assert "1.5 ETH" in text and "0.000042 TST" in text
    assert " in " in text and " out " in text
    assert f"Complete through block {rig.etherscan.head}." in text
    assert "INCOMPLETE" not in text


def test_an_incomplete_history_says_so_loudly_and_how_to_resume():
    rig = Rig()
    rig.etherscan.add("txlist", *[tx_row(b, OTHER, ME, value="1") for b in range(1, 30)])

    text = render_history(read(rig, page_size=5, max_items=7))

    assert "INCOMPLETE: this is not the whole history." in text
    assert "Resume with:" in text and "stopped after 7 transfers" in text
    assert "Complete through" not in text


def test_a_kind_nobody_provides_is_listed_as_a_gap_even_when_the_read_is_complete():
    rig = Rig()
    rig.alchemy.add(asset_row("external", 5, OTHER, ME))

    text = render_history(read(rig, BSC))

    assert "Not available from any configured source: internal." in text
    assert "no configured source provides internal transfers on bsc" in text


def test_a_decoded_transaction_lists_its_status_movements_and_what_it_cannot_show():
    rig = Rig()
    data = load_fixture("evm/ethereum_erc20_single")
    tx, receipt = data["tx"], data["receipt"]
    rig.etherscan.transactions[tx["hash"]] = tx
    rig.etherscan.receipts[tx["hash"]] = receipt
    rig.etherscan.head = int(receipt["blockNumber"], 16) + 20

    text = render_transaction(run(rig.adapter().get_transaction(tx["hash"])))

    assert tx["hash"] in text and "status        success" in text
    assert "21 confirmations" in text and "Movements proven by the receipt:" in text
    assert "erc20" in text and f"(log {int(receipt['logs'][0]['logIndex'], 16)})" in text
    assert "internal coin movements are not part of a receipt" in text


def test_a_failed_transaction_says_it_moved_nothing():
    rig = Rig()
    data = load_fixture("evm/ethereum_failed")
    rig.etherscan.transactions[data["tx"]["hash"]] = data["tx"]
    rig.etherscan.receipts[data["tx"]["hash"]] = data["receipt"]

    text = render_transaction(run(rig.adapter().get_transaction(data["tx"]["hash"])))

    assert "status        failed" in text and "No value movements in the receipt." in text


def test_a_contract_creation_shows_the_created_contract():
    rig = Rig()
    data = load_fixture("evm/ethereum_creation")
    rig.etherscan.transactions[data["tx"]["hash"]] = data["tx"]
    rig.etherscan.receipts[data["tx"]["hash"]] = data["receipt"]

    text = render_transaction(run(rig.adapter().get_transaction(data["tx"]["hash"])))

    assert "(contract creation)" in text and data["receipt"]["contractAddress"] in text


def test_value_and_fee_are_shown_in_the_chains_own_coin():
    rig = Rig()
    data = load_fixture("evm/ethereum_native")
    tx, receipt = data["tx"], data["receipt"]
    rig.etherscan.transactions[tx["hash"]] = tx
    rig.etherscan.receipts[tx["hash"]] = receipt

    text = render_transaction(run(rig.adapter().get_transaction(tx["hash"])))

    value = format_amount(str(int(tx["value"], 16)), 18)
    fee = format_amount(
        str(int(receipt["gasUsed"], 16) * int(receipt["effectiveGasPrice"], 16)), 18
    )
    assert f"value         {value} ETH" in text and f"fee           {fee} ETH" in text


def test_a_tron_transaction_shows_trx_not_sun():
    rig = Rig()
    data = load_fixture("tron/detail_trx")
    txid = data["info"]["id"]
    rig.trongrid.transactions[txid], rig.trongrid.infos[txid] = data["tx"], data["info"]

    text = render_transaction(run(rig.tron_adapter().get_transaction(txid)))

    amount = data["tx"]["raw_data"]["contract"][0]["parameter"]["value"]["amount"]
    assert f"value         {format_amount(str(amount), 6)} TRX" in text
    assert f"fee           {format_amount(str(data['info']['fee']), 6)} TRX" in text
    assert "(raw" not in text.split("Movements")[0]


def test_an_amount_on_a_chain_nobody_reads_is_shown_raw_and_says_so():
    from app.ingestion.render import _native

    assert _native("123", "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp") == "123 (raw, smallest unit)"
    assert _native("1500000", "tron:728126428") == "1.5 TRX"
    assert _native("150000000", "bip122:000000000019d6689c085ae165831e93") == "1.5 BTC"


def test_lookalike_token_symbols_are_spelled_out_so_they_cannot_pass_for_the_real_one():
    from app.ingestion.render import printable

    assert printable("USDC") == "USDC"
    # The right-hand sides are raw strings: a literal backslash followed by the code point.
    assert printable("US\u0421") == r"US\u0421"  # a Cyrillic capital Es, not a Latin C
    assert printable("U\u17b5S\u17b5D") == r"U\u17b5S\u17b5D"  # invisible characters made visible
    assert printable("\u0116T\u1e28") == r"\u0116T\u1e28"


# --- Bitcoin ------------------------------------------------------------------------------------


def _bitcoin_text(name):
    from app.chains.bitcoin.chains import BITCOIN_CHAIN
    from app.chains.bitcoin.models import BitcoinDetail
    from app.chains.bitcoin.normalize import apply_outspends, esplora_transaction, with_head
    from app.ingestion.render import render_bitcoin_transaction

    data = load_fixture(f"bitcoin/{name}")
    transaction = esplora_transaction(data["tx"], BITCOIN_CHAIN)
    if data["outspends"] is not None:
        transaction = apply_outspends(transaction, data["outspends"])
    head = (transaction.block_height or 0) + 9
    return render_bitcoin_transaction(BitcoinDetail(with_head(transaction, head, BITCOIN_CHAIN)))


def test_an_old_pay_to_key_payment_shows_outputs_with_no_address_and_who_spent_them():
    text = _bitcoin_text("p2pk_payment")

    assert "status        confirmed (block 170, 10 confirmations)" in text
    assert "time          2009-01-12 03:30:25 UTC" in text
    assert "Inputs (1), 50 BTC:" in text and "Outputs (2), 50 BTC:" in text
    assert text.count("(no address form: p2pk)") == 3
    assert "10 BTC  spent by ea44e97271..ee708e:0" in text
    assert "spends 0437cd7f85..a597c9:0" in text


def test_a_coinjoin_look_alike_is_named_as_an_inference_and_the_other_heuristics_are_withheld():
    text = _bitcoin_text("coinjoin_like")

    assert "Inferred, not proven by the chain (change confidence: low):" in text
    assert (
        "CoinJoin (likely): 7 outputs of exactly 0.33554432 BTC and 18 different input addresses"
        in text
    )
    assert "18 input addresses signed together: NOT a sign of one owner here" in text
    assert "the change heuristics do not apply" in text
    assert text.count("spent by 8a428652b1..20184d:") == 7  # the equal outputs were merged again


def test_a_reused_address_is_reported_as_probable_change_with_the_reason():
    text = _bitcoin_text("self_change")
    assert "is probably change: pays an address that also funded the transaction" in text
    assert "is probably a payment" in text


def test_data_outputs_and_coinbases_are_described_as_what_they_are():
    assert "unspendable (data, not coins)" in _bitcoin_text("op_return_only")
    assert "coinbase: newly created coins" in _bitcoin_text("coinbase")
    assert "fee " not in _bitcoin_text("coinbase")  # a coinbase pays no fee


def test_an_output_whose_spend_state_was_not_fetched_says_so():
    assert "spend state unknown" in _bitcoin_text("legacy_p2pkh")


def test_amounts_in_bitcoin_are_signed_for_the_address_and_shown_in_coins():
    from app.ingestion.render import _btc, _signed_btc

    assert _signed_btc("150000000") == "+1.5" and _signed_btc("-1") == "-0.00000001"
    assert _signed_btc("0") == " 0"
    assert _btc("150000000") == "1.5 BTC" and _btc(None) == "-"


def test_a_bitcoin_history_shows_the_time_the_net_amount_and_the_verdict():
    from app.ingestion.render import render_utxo_history
    from app.ingestion.utxo import fetch_utxo_history
    from tests.fake_esplora import p2pkh, pay, spend, tx, txid

    rig = Rig()
    me = p2pkh(1)
    rig.esplora.add(
        tx(
            1,
            [spend(txid(900), 0, p2pkh(2), 60_000)],
            [pay(me, 50_000), pay(p2pkh(2), 9_000)],
            height=101,
        ),
        tx(
            2, [spend(txid(1), 0, me, 50_000)], [pay(p2pkh(3), 30_000), pay(me, 19_000)], height=102
        ),
    )

    text = render_utxo_history(run(fetch_utxo_history(rig.bitcoin_adapter(), me)))

    lines = text.splitlines()
    assert lines[0].endswith(me) and "2 transactions in blocks 101..102" in lines[1]
    assert "received 0.00069 BTC and sent 0.0005 BTC" in lines[1]
    row = next(line for line in lines if "both" in line)
    assert "-0.00031" in row and "1/2" in row and "2026-" in row
    assert "Complete through block" in text and "Balance now: 0.00019 BTC" in text


def test_a_bitcoin_history_that_does_not_agree_with_the_node_is_not_called_complete():
    from app.ingestion.render import render_utxo_history
    from app.ingestion.utxo import fetch_utxo_history
    from tests.fake_esplora import p2pkh, pay, spend, tx, txid

    rig = Rig()
    me = p2pkh(1)
    rig.esplora.add(tx(1, [spend(txid(900), 0, p2pkh(2), 6_000)], [pay(me, 5_000)], height=101))
    rig.esplora.unlisted = {txid(1)}

    text = render_utxo_history(run(fetch_utxo_history(rig.bitcoin_adapter(), me)))

    assert "INCOMPLETE: this is not the whole history." in text
    assert "MISMATCH with the provider's own figures" in text and "Complete through" not in text
