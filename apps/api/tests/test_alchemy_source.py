"""The Alchemy source against an in-memory Alchemy: both directions, block times, BNB limits."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx2
import pytest

from app.chains.evm.chains import EVM_CHAINS
from app.chains.evm.cursors import InvalidCursor
from app.chains.models import TransferType, TxStatus
from app.providers.alchemy import AlchemySource, decode_cursor
from app.providers.base import ProviderAuthError, ProviderBadResponse, ProviderRateLimited
from app.providers.client import ProviderClient
from app.providers.registry import PROVIDERS
from tests.fake_alchemy import HEAD, FakeAlchemy, asset_row
from tests.fake_etherscan import ME, OTHER
from tests.helpers import FakeClock, FullJitter, run

ETHEREUM = EVM_CHAINS["eip155:1"]
BSC = EVM_CHAINS["eip155:56"]
KEY = "alchemy-test-key-abcdef123456"
USDT = "0x" + "dd" * 20


def make(fake: FakeAlchemy):
    clock = FakeClock()
    client = ProviderClient(
        {"alchemy": PROVIDERS["alchemy"]},
        {"alchemy": KEY},
        transport=httpx2.MockTransport(fake),
        rate_limits={"alchemy": 1000.0},
        clock=clock,
        sleep=clock.sleep,
        rng=FullJitter(),
    )
    return AlchemySource(client), clock


async def read_everything(source, chain, kind, *, limit):
    items, cursor, pages = [], None, 0
    while True:
        page = await source.transfer_page(chain, ME, kind, cursor=cursor, limit=limit)
        items += page.items
        pages += 1
        assert pages < 500
        if page.next_cursor is None:
            return items, pages
        cursor = page.next_cursor


# --- reading a history -------------------------------------------------------------------------


def test_outgoing_then_incoming_transfers_are_read_in_two_passes_with_page_keys():
    fake = FakeAlchemy()
    out = [asset_row("external", block, ME, OTHER) for block in range(1, 6)]
    incoming = [asset_row("external", block, OTHER, ME) for block in range(10, 14)]
    fake.add(*out, *incoming)
    source, _ = make(fake)

    items, pages = run(read_everything(source, ETHEREUM, TransferType.NATIVE, limit=3))

    assert [t.transaction_hash for t in items] == [r["hash"] for r in out + incoming]
    assert pages == 4  # out: 3 + 2, in: 3 + 1
    first, *_, last = [payload["params"][0] for payload in fake.payloads]
    assert first["fromAddress"] == ME and "toAddress" not in first
    assert last["toAddress"] == ME and "fromAddress" not in last
    assert fake.payloads[1]["params"][0]["pageKey"] == "pk-3"


def test_the_request_asks_for_ascending_nonzero_transfers_of_one_category_in_hex():
    fake = FakeAlchemy()
    source, _ = make(fake)

    run(
        source.transfer_page(
            ETHEREUM, ME, TransferType.ERC20, limit=250, from_block=100, to_block=900
        )
    )

    request = fake.payloads[0]["params"][0]
    assert fake.payloads[0]["method"] == "alchemy_getAssetTransfers"
    assert request["category"] == ["erc20"] and request["order"] == "asc"
    assert request["excludeZeroValue"] is False  # token transfers keep zero-value events
    assert (request["fromBlock"], request["toBlock"], request["maxCount"]) == (
        "0x64",
        "0x384",
        "0xfa",
    )


def test_an_open_ended_range_asks_for_the_latest_block():
    fake = FakeAlchemy()
    source, _ = make(fake)
    run(source.transfer_page(ETHEREUM, ME, TransferType.NATIVE))
    request = fake.payloads[0]["params"][0]
    assert (request["fromBlock"], request["toBlock"]) == ("0x0", "latest")


def test_a_transfer_to_oneself_appears_in_both_passes_with_the_same_identity():
    fake = FakeAlchemy()
    fake.add(asset_row("external", 5, ME, ME))
    source, _ = make(fake)

    items, _ = run(read_everything(source, ETHEREUM, TransferType.NATIVE, limit=10))

    assert len(items) == 2 and len({t.identity for t in items}) == 1


def test_token_transfers_carry_their_contract_decimals_symbol_and_log_position():
    fake = FakeAlchemy()
    fake.add(asset_row("erc20", 7, ME, OTHER, 2_500_000, contract=USDT, decimals=6, symbol="USDT"))
    source, _ = make(fake)

    items, _ = run(read_everything(source, ETHEREUM, TransferType.ERC20, limit=10))

    (transfer,) = items
    assert transfer.transfer_type is TransferType.ERC20 and transfer.amount_raw == "2500000"
    assert (transfer.asset.symbol, transfer.asset.decimals) == ("USDT", 6)
    assert transfer.asset.contract_address == USDT and transfer.log_index is not None


def test_nfts_in_the_reply_are_left_out_and_unreadable_rows_are_counted():
    fake = FakeAlchemy()
    fake.add(
        asset_row("external", 1),
        {**asset_row("external", 2), "rawContract": {"value": None, "address": None}},
        asset_row("external", 4),
    )
    fake.rows.append({**asset_row("external", 5), "category": "erc721"})
    source, _ = make(fake)

    page = run(source.transfer_page(ETHEREUM, ME, TransferType.NATIVE, limit=10))

    assert [t.block_number for t in page.items] == [1, 4]
    assert page.coverage == ("1 rows from alchemy could not be read and were skipped",)


# --- block times -------------------------------------------------------------------------------


def test_on_ethereum_the_block_time_comes_with_the_transfers():
    fake = FakeAlchemy()
    fake.block_times[5] = 1_700_000_500
    fake.add(asset_row("external", 5))
    source, _ = make(fake)

    page = run(source.transfer_page(ETHEREUM, ME, TransferType.NATIVE))

    assert page.items[0].timestamp == datetime.fromtimestamp(1_700_000_500, UTC)
    assert fake.payloads[0]["params"][0]["withMetadata"] is True
    assert fake.methods == ["alchemy_getAssetTransfers"]  # no per-block lookups


def test_on_bnb_block_times_are_looked_up_in_batches_and_remembered():
    fake = FakeAlchemy()
    for block in range(1, 121):  # 120 distinct blocks: three batches of 50, 50 and 20
        fake.block_times[block] = 1_700_000_000 + block
        fake.add(asset_row("external", block))
    source, _ = make(fake)

    page = run(source.transfer_page(BSC, ME, TransferType.NATIVE, limit=1000))

    assert fake.payloads[0]["params"][0]["withMetadata"] is False
    assert page.items[0].timestamp == datetime.fromtimestamp(1_700_000_001, UTC)
    assert all(t.timestamp is not None for t in page.items)
    batches = [p for p in fake.payloads if isinstance(p, list)]
    assert [len(b) for b in batches] == [50, 50, 20]

    run(source.transfer_page(BSC, ME, TransferType.NATIVE, limit=1000))  # the same blocks again
    assert len([p for p in fake.payloads if isinstance(p, list)]) == 3  # nothing looked up twice


def test_a_block_whose_time_cannot_be_found_leaves_the_time_unknown_instead_of_wrong():
    fake = FakeAlchemy()
    fake.add(asset_row("external", 9))  # no time recorded for block 9
    source, _ = make(fake)
    page = run(source.transfer_page(BSC, ME, TransferType.NATIVE))
    assert page.items[0].timestamp is None


# --- what BNB cannot do ------------------------------------------------------------------------


def test_internal_transfers_are_not_available_on_bnb_and_the_page_says_so_without_a_request():
    fake = FakeAlchemy()
    source, _ = make(fake)

    page = run(source.transfer_page(BSC, ME, TransferType.INTERNAL))

    assert page.items == [] and page.next_cursor is None
    assert page.coverage == ("alchemy has no internal transfers on bsc",)
    assert fake.requests == []
    assert source.covers(ETHEREUM, TransferType.INTERNAL) and not source.covers(
        BSC, TransferType.INTERNAL
    )


def test_ethereum_internal_transfers_are_requested_in_their_own_category():
    fake = FakeAlchemy()
    fake.add(asset_row("internal", 3, OTHER, ME, 7))
    source, _ = make(fake)
    items, _ = run(read_everything(source, ETHEREUM, TransferType.INTERNAL, limit=10))
    assert [t.transfer_type for t in items] == [TransferType.INTERNAL]


def test_activity_is_the_value_carrying_transactions_and_says_it_cannot_see_the_rest():
    fake = FakeAlchemy()
    fake.add(asset_row("external", 3))
    source, _ = make(fake)

    page = run(source.account_page(ETHEREUM, ME))

    (tx,) = page.transactions
    assert tx.status is TxStatus.SUCCESS and tx.value_raw == str(10**18) and tx.fee_raw is None
    assert [t.amount_raw for t in page.native_transfers] == [str(10**18)]  # the same request
    assert "alchemy lists only transactions that carried value" in page.coverage


# --- cursors -----------------------------------------------------------------------------------


def test_cursors_name_a_direction_and_a_page_key():
    assert decode_cursor(None) == ("out", None)
    assert decode_cursor("out:") == ("out", None)
    assert decode_cursor("in:abc-123_x") == ("in", "abc-123_x")
    for bad in ("sideways:1", "out", "out:has space", "etherscan|1:2", "out:" + "x" * 400):
        with pytest.raises(InvalidCursor):
            decode_cursor(bad)


# --- errors ------------------------------------------------------------------------------------


def test_json_rpc_errors_in_a_200_are_classified():
    fake = FakeAlchemy()
    source, _ = make(fake)

    fake.queued = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": 429, "message": "Monthly capacity limit exceeded"},
        }
    ] * 5
    with pytest.raises(ProviderRateLimited):
        run(source.transfer_page(ETHEREUM, ME, TransferType.NATIVE))

    fake.queued = [
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "Invalid params"}}
    ]
    with pytest.raises(ProviderBadResponse, match="refused"):
        run(source.transfer_page(ETHEREUM, ME, TransferType.NATIVE))

    fake.queued = [
        {"jsonrpc": "2.0", "id": 1, "error": {"code": 401, "message": "Must be authenticated"}}
    ]
    with pytest.raises(ProviderAuthError):
        run(source.transfer_page(ETHEREUM, ME, TransferType.NATIVE))


def test_a_reply_that_is_not_json_rpc_is_a_bad_response():
    fake = FakeAlchemy()
    fake.queued = [{"unexpected": True}]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse):
        run(source.block_height(ETHEREUM))


# --- the chain and single transactions ---------------------------------------------------------

TX_HASH = "0x" + "ab" * 32


def test_the_block_height_is_a_json_rpc_call():
    source, _ = make(FakeAlchemy())
    assert run(source.block_height(BSC)) == HEAD


def test_a_transaction_and_its_receipt_are_fetched_in_one_batch():
    fake = FakeAlchemy()
    fake.transactions[TX_HASH] = {"hash": TX_HASH, "blockTimestamp": "0x6553f100"}
    fake.receipts[TX_HASH] = {"transactionHash": TX_HASH, "blockNumber": "0x64"}
    source, _ = make(fake)

    tx, receipt, when = run(source.transaction(BSC, TX_HASH))

    assert tx["hash"] == TX_HASH and receipt["blockNumber"] == "0x64"
    assert when == datetime.fromtimestamp(0x6553F100, UTC)
    assert len(fake.requests) == 1 and fake.methods == [
        "eth_getTransactionByHash",
        "eth_getTransactionReceipt",
    ]


def test_without_a_block_time_on_the_transaction_the_block_is_asked_for():
    fake = FakeAlchemy()
    fake.transactions[TX_HASH] = {"hash": TX_HASH}
    fake.receipts[TX_HASH] = {"transactionHash": TX_HASH, "blockNumber": hex(100)}
    fake.block_times[100] = 1_700_000_100
    source, _ = make(fake)

    _, _, when = run(source.transaction(BSC, TX_HASH))

    assert when == datetime.fromtimestamp(1_700_000_100, UTC)
    assert fake.methods[-1] == "eth_getBlockByNumber"


def test_unknown_and_unmined_transactions_are_distinguished():
    fake = FakeAlchemy()
    source, _ = make(fake)
    assert run(source.transaction(BSC, TX_HASH)) == (None, None, None)

    fake.transactions[TX_HASH] = {"hash": TX_HASH, "blockNumber": None}
    tx, receipt, when = run(source.transaction(BSC, TX_HASH))
    assert tx is not None and receipt is None and when is None


def test_zero_value_coin_transfers_are_left_out_but_zero_value_token_events_are_kept():
    fake = FakeAlchemy()
    source, _ = make(fake)

    run(source.transfer_page(ETHEREUM, ME, TransferType.NATIVE))
    run(source.transfer_page(ETHEREUM, ME, TransferType.INTERNAL))
    run(source.transfer_page(ETHEREUM, ME, TransferType.ERC20))

    flags = {
        p["params"][0]["category"][0]: p["params"][0]["excludeZeroValue"] for p in fake.payloads
    }
    assert flags == {"external": True, "internal": True, "erc20": False}


NOT_ENABLED = {
    "jsonrpc": "2.0",
    "id": 1,
    "error": {
        "code": -32600,
        "message": (
            "BNB_MAINNET is not enabled for this app. Visit this page to enable the network: "
            "https://dashboard.alchemy.com/apps/abc123/networks"
        ),
    },
}


def test_a_network_not_enabled_for_the_app_is_explained_not_called_a_bad_key():
    fake = FakeAlchemy()
    fake.queued = [(403, NOT_ENABLED)]
    source, _ = make(fake)

    with pytest.raises(ProviderAuthError) as info:
        run(source.transfer_page(BSC, ME, TransferType.NATIVE))

    assert "not enabled for the API key" in info.value.reason
    assert "dashboard.alchemy.com" not in str(info.value)  # our words, not the app-specific link
    assert not info.value.retryable and len(fake.requests) == 1


def test_the_same_message_in_a_200_body_is_explained_too():
    fake = FakeAlchemy()
    fake.queued = [NOT_ENABLED]
    source, _ = make(fake)
    with pytest.raises(ProviderAuthError, match="not enabled for the API key"):
        run(source.block_height(BSC))
