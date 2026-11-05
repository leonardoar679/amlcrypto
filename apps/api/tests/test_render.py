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
    assert short("0x" + "ab" * 20) == "0xababab…ababab"  # 8 characters, …, 6 characters
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
