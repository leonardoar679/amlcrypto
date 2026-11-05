"""The EVM adapter over both fake providers: routing, fallback, cursors and transaction details."""

from __future__ import annotations

import pytest

from app.chains.evm.cursors import InvalidCursor
from app.chains.models import Confirmation, TransferType, TxStatus
from app.providers.base import AllProvidersFailed
from tests.fake_alchemy import asset_row
from tests.fake_etherscan import ME, OTHER, internal_row, token_row, tx_row
from tests.helpers import load_fixture, run
from tests.rigs import BSC, ETHEREUM, Rig

INVALID_KEY = {"status": "0", "message": "NOTOK", "result": "Invalid API Key"}


# --- which sources a chain gets ----------------------------------------------------------------


def test_ethereum_prefers_etherscan_and_keeps_alchemy_as_the_fallback():
    assert Rig().adapter(ETHEREUM).source_names == ["etherscan", "alchemy"]


def test_bnb_uses_alchemy_unless_the_etherscan_plan_includes_it():
    assert Rig().adapter(BSC).source_names == ["alchemy"]
    assert Rig(paid_plan=True).adapter(BSC).source_names == ["etherscan", "alchemy"]


def test_sources_without_a_key_are_left_out():
    assert Rig(etherscan_key=None).adapter(ETHEREUM).source_names == ["alchemy"]
    assert Rig(alchemy_key=None).adapter(ETHEREUM).source_names == ["etherscan"]
    assert Rig(etherscan_key=None, alchemy_key=None).adapter(ETHEREUM).source_names == []


# --- fallback and cursors ----------------------------------------------------------------------


def test_a_history_starts_on_the_first_source_and_its_cursor_stays_with_it():
    rig = Rig()
    rig.etherscan.add("txlist", *[tx_row(block) for block in range(1, 6)])
    adapter = rig.adapter()

    async def scenario():
        first = await adapter.get_account_page(ME, limit=3)
        second = await adapter.get_account_page(ME, cursor=first.next_cursor, limit=3)
        return first, second

    first, second = run(scenario())

    assert first.next_cursor.startswith("etherscan|") and second.next_cursor is None
    assert [t.block_number for t in first.transactions + second.transactions] == [1, 2, 3, 4, 5]
    assert rig.alchemy.requests == []  # never consulted


def test_when_the_first_source_cannot_serve_the_chain_the_next_one_does():
    rig = Rig()
    rig.etherscan.queued = [
        {
            "status": "0",
            "message": "NOTOK",
            "result": "Free API access is not supported for this chain.",
        }
    ]
    rig.alchemy.add(asset_row("external", 4, ME, OTHER))

    page = run(rig.adapter().get_account_page(ME))

    assert [t.block_number for t in page.native_transfers] == [4]
    assert page.next_cursor.startswith("alchemy|")


def test_a_failure_in_the_middle_of_a_history_is_reported_not_papered_over_by_another_source():
    rig = Rig()
    rig.etherscan.add("txlist", *[tx_row(block) for block in range(1, 6)])
    rig.alchemy.add(asset_row("external", 4, ME, OTHER))
    adapter = rig.adapter()
    first = run(adapter.get_account_page(ME, limit=3))

    rig.etherscan.queued = [INVALID_KEY]
    with pytest.raises(Exception, match="rejected"):
        run(adapter.get_account_page(ME, cursor=first.next_cursor, limit=3))
    assert rig.alchemy.requests == []  # positions are not portable between sources


def test_all_sources_failing_reports_every_reason():
    rig = Rig()
    rig.etherscan.queued = [INVALID_KEY]
    rig.alchemy.queued = [
        {"jsonrpc": "2.0", "id": 1, "error": {"code": 401, "message": "Unauthorized"}}
    ]
    with pytest.raises(AllProvidersFailed) as info:
        run(rig.adapter().get_account_page(ME))
    assert [name for name, _ in info.value.failures] == ["etherscan", "alchemy"]


def test_a_cursor_from_somewhere_else_is_refused():
    adapter = Rig(etherscan_key=None).adapter()
    for cursor in ("etherscan|5:0", "nonsense", "|"):
        with pytest.raises(InvalidCursor):
            run(adapter.get_account_page(ME, cursor=cursor))


def test_the_block_height_falls_back_too():
    rig = Rig()
    rig.etherscan.queued = [INVALID_KEY]
    assert run(rig.adapter().get_block_height()) == rig.alchemy.head


# --- the kinds of transfer ---------------------------------------------------------------------


def test_each_kind_of_transfer_has_its_own_stream():
    rig = Rig()
    rig.etherscan.add("txlist", tx_row(10, ME, OTHER, value="9"))
    rig.etherscan.add("txlistinternal", internal_row(11, OTHER, ME, value="4"))
    rig.etherscan.add("tokentx", token_row(12, OTHER, ME, value="3"))
    adapter = rig.adapter()

    native = run(adapter.get_transfers(ME, TransferType.NATIVE))
    internal = run(adapter.get_transfers(ME, TransferType.INTERNAL))
    tokens = run(adapter.get_token_transfers(ME))
    activity = run(adapter.get_address_activity(ME))

    assert [t.amount_raw for t in native.items] == ["9"]
    assert [t.amount_raw for t in internal.items] == ["4"]
    assert [t.amount_raw for t in tokens.items] == ["3"]
    assert [t.block_number for t in activity.items] == [10]


def test_internal_transfers_on_bnb_without_a_source_for_them_are_reported_missing_not_empty():
    rig = Rig()
    page = run(rig.adapter(BSC).get_transfers(ME, TransferType.INTERNAL))

    assert page.items == [] and page.next_cursor is None
    assert page.coverage == ("no configured source provides internal transfers on bsc",)
    assert rig.alchemy.requests == [] and rig.etherscan.requests == []


# --- one transaction ---------------------------------------------------------------------------


def stock(rig, name, *, to_alchemy=False):
    data = load_fixture(f"evm/{name}")
    tx, receipt = data["tx"], data["receipt"]
    target = rig.alchemy if to_alchemy else rig.etherscan
    target.transactions[tx["hash"]] = tx
    target.receipts[tx["hash"]] = receipt
    target.head = int(receipt["blockNumber"], 16) + 40
    return tx["hash"], data


def test_a_real_token_transaction_is_decoded_through_etherscan_with_confirmations():
    rig = Rig()
    tx_hash, data = stock(rig, "ethereum_erc20_single")

    detail = run(rig.adapter(ETHEREUM).get_transaction(tx_hash))

    assert detail.transaction.status is TxStatus.SUCCESS
    assert detail.transaction.provider == "etherscan"
    (transfer,) = [t for t in detail.transfers if t.transfer_type is TransferType.ERC20]
    assert transfer.log_index == int(data["receipt"]["logs"][0]["logIndex"], 16)
    assert detail.transaction.confirmations == 41
    assert detail.transaction.confirmation_status is Confirmation.CONFIRMED
    assert detail.transaction.timestamp is not None  # from the transaction's own block time
    assert any("internal coin movements" in note for note in detail.coverage)
    assert any("symbols and decimals" in note for note in detail.coverage)


def test_a_real_bnb_swap_is_decoded_through_alchemy_in_one_batch():
    rig = Rig()
    tx_hash, _ = stock(rig, "bsc_erc20_multi", to_alchemy=True)

    detail = run(rig.adapter(BSC).get_transaction(tx_hash))

    tokens = [t for t in detail.transfers if t.transfer_type is TransferType.ERC20]
    assert len(tokens) >= 3 and detail.transaction.provider == "alchemy"
    assert len({t.identity for t in tokens}) == len(tokens)
    batch_calls = [p for p in rig.alchemy.payloads if isinstance(p, list) and len(p) == 2]
    assert len(batch_calls) == 1  # the transaction and its receipt together


def test_an_unknown_transaction_is_none_and_a_bad_hash_is_an_error():
    adapter = Rig().adapter()
    assert run(adapter.get_transaction("0x" + "ab" * 32)) is None
    with pytest.raises(ValueError):
        run(adapter.get_transaction("0x1234"))


def test_not_found_is_not_claimed_when_a_source_could_not_answer():
    rig = Rig()
    rig.etherscan.queued = [INVALID_KEY]
    with pytest.raises(AllProvidersFailed):
        run(rig.adapter().get_transaction("0x" + "ab" * 32))


def test_a_transaction_unknown_to_the_first_source_is_found_by_the_second():
    rig = Rig()
    tx_hash, _ = stock(rig, "ethereum_native", to_alchemy=True)  # only Alchemy has it
    detail = run(rig.adapter(ETHEREUM).get_transaction(tx_hash))
    assert detail.transaction.provider == "alchemy"
    assert [t.transfer_type for t in detail.transfers] == [TransferType.NATIVE]
