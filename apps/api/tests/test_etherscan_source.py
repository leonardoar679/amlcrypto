"""The Etherscan source against an in-memory Etherscan: paging, windows, errors, transactions."""

from __future__ import annotations

import random
from datetime import UTC, datetime

import httpx2
import pytest

from app.chains.evm.chains import EVM_CHAINS
from app.chains.evm.cursors import InvalidCursor
from app.chains.models import TransferType, TxStatus
from app.core.redaction import secrets
from app.providers.base import (
    ProviderAuthError,
    ProviderBadResponse,
    ProviderRateLimited,
    ProviderUnavailable,
)
from app.providers.client import ProviderClient
from app.providers.etherscan import (
    EtherscanSource,
    error_from_text,
    etherscan_body_error,
    seconds_until_reset,
)
from app.providers.registry import PROVIDERS
from tests.fake_etherscan import (
    HEAD,
    KEY,
    ME,
    OTHER,
    FakeEtherscan,
    internal_row,
    notok,
    token_row,
    tx_row,
)
from tests.helpers import FakeClock, FullJitter, run

ETHEREUM = EVM_CHAINS["eip155:1"]


def make(fake: FakeEtherscan, **source_options):
    clock = FakeClock()
    client = ProviderClient(
        {"etherscan": PROVIDERS["etherscan"]},
        {"etherscan": KEY},
        transport=httpx2.MockTransport(fake),
        rate_limits={"etherscan": 1000.0},
        clock=clock,
        sleep=clock.sleep,
        rng=FullJitter(),
    )
    return EtherscanSource(client, **source_options), client, clock


async def read_everything(fetch, *, limit, pages_cap=2000, **kwargs):
    """Follow cursors to the end. Returns every item, the page count and the last page."""
    items, cursor, pages = [], None, 0
    while True:
        page = await fetch(ETHEREUM, ME, cursor=cursor, limit=limit, **kwargs)
        items += (
            getattr(page, "transactions", None) or page.items
            if hasattr(page, "items")
            else page.transactions
        )
        pages += 1
        assert pages < pages_cap, "the cursor never reached the end"
        if page.next_cursor is None:
            return items, pages, page
        cursor = page.next_cursor


def hashes(items):
    return [item.transaction_hash for item in items]


# --- paging ------------------------------------------------------------------------------------


def test_a_short_history_is_one_page_and_asks_for_ascending_order():
    fake = FakeEtherscan()
    rows = [tx_row(block) for block in (10, 20, 30)]
    fake.add("txlist", *rows)
    source, _, _ = make(fake)

    page = run(source.account_page(ETHEREUM, ME, limit=100))

    assert hashes(page.transactions) == [r["hash"] for r in rows] and page.next_cursor is None
    (call,) = fake.calls
    assert call["chainid"] == "1" and call["apikey"] == KEY
    assert (call["module"], call["action"], call["sort"]) == ("account", "txlist", "asc")
    assert (call["startblock"], call["page"], call["offset"]) == ("0", "1", "100")


def test_a_history_that_fills_a_page_exactly_ends_on_the_following_empty_page():
    fake = FakeEtherscan()
    fake.add("txlist", *[tx_row(block) for block in range(1, 21)])
    source, _, _ = make(fake)

    async def scenario():
        first = await source.account_page(ETHEREUM, ME, limit=10)
        second = await source.account_page(ETHEREUM, ME, limit=10, cursor=first.next_cursor)
        third = await source.account_page(ETHEREUM, ME, limit=10, cursor=second.next_cursor)
        return first, second, third

    first, second, third = run(scenario())
    assert first.next_cursor and second.next_cursor and third.next_cursor is None
    assert third.transactions == []  # Etherscan's "No transactions found" is not an error


def test_pages_follow_one_another_without_gaps_or_repeats():
    fake = FakeEtherscan()
    rows = [tx_row(block) for block in range(1, 251)]
    fake.add("txlist", *rows)
    source, _, _ = make(fake)

    transactions, pages, _ = run(read_everything(source.account_page, limit=100))

    assert hashes(transactions) == [r["hash"] for r in rows] and pages == 3


def test_rows_of_one_block_are_not_lost_or_repeated_when_a_page_ends_inside_the_block():
    fake = FakeEtherscan()
    rows = (
        [tx_row(10)] * 0 + [tx_row(7), tx_row(7)] + [tx_row(10) for _ in range(25)] + [tx_row(11)]
    )
    fake.add("txlist", *rows)
    source, _, _ = make(fake)

    transactions, _, _ = run(read_everything(source.account_page, limit=10))

    assert hashes(transactions) == [r["hash"] for r in rows]


def test_the_block_range_is_passed_on_and_honoured():
    fake = FakeEtherscan()
    fake.add("txlist", *[tx_row(block) for block in (5, 15, 25, 35)])
    source, _, _ = make(fake)

    page = run(source.account_page(ETHEREUM, ME, from_block=10, to_block=30))

    assert [tx.block_number for tx in page.transactions] == [15, 25]
    assert (fake.calls[0]["startblock"], fake.calls[0]["endblock"]) == ("10", "30")


def test_an_address_with_no_history_is_an_empty_page_not_an_error():
    source, _, _ = make(FakeEtherscan())
    page = run(source.account_page(ETHEREUM, ME))
    assert page.transactions == [] and page.native_transfers == [] and page.next_cursor is None


def test_a_cursor_that_is_not_ours_is_refused():
    source, _, _ = make(FakeEtherscan())
    with pytest.raises(InvalidCursor):
        run(source.account_page(ETHEREUM, ME, cursor="alchemy|out:abc"))


# --- the result window -------------------------------------------------------------------------


def test_a_history_larger_than_the_window_is_read_completely_by_moving_the_start_block():
    fake = FakeEtherscan(window=100)
    rows = [tx_row(block) for block in range(1, 251) for _ in range(2)]  # two rows per block
    fake.add("txlist", *rows)
    source, _, _ = make(fake, window=100)

    transactions, pages, last = run(read_everything(source.account_page, limit=20))

    assert hashes(transactions) == [r["hash"] for r in rows]
    assert not last.truncated and pages > 100 // 20  # it really had to slide
    assert all(int(c["page"]) * int(c["offset"]) <= 100 for c in fake.calls)  # never asked too much


def test_one_block_with_more_rows_than_the_window_is_reported_as_cut_not_silently_dropped():
    fake = FakeEtherscan(window=50)
    fake.add("txlist", *[tx_row(5) for _ in range(120)], *[tx_row(9)])
    source, _, _ = make(fake, window=50)

    transactions, _, last = run(read_everything(source.account_page, limit=10))

    assert last.truncated and last.next_cursor is None
    assert len(transactions) == 50  # exactly what the window shows
    assert "result window" in last.coverage[0]


@pytest.mark.parametrize("seed", range(40))
def test_random_histories_are_read_exactly_once_whatever_the_page_size_and_window(seed):
    rng = random.Random(seed)
    limit = rng.randint(2, 30)
    window = limit * rng.randint(2, 9)
    fake = FakeEtherscan(window=window)
    rows, block = [], rng.randint(0, 50)
    for _ in range(rng.randint(0, 150)):
        block += rng.randint(0, 3)  # runs of several rows per block, sometimes none between
        rows.append(tx_row(block))
    # No block may fill a whole page (that is the "cut" case, tested above).
    for number in {int(r["blockNumber"]) for r in rows}:
        in_block = [r for r in rows if int(r["blockNumber"]) == number]
        for extra in in_block[limit - 1 :]:
            rows.remove(extra)
    fake.add("txlist", *rows)
    source, _, _ = make(fake, window=window)

    transactions, _, last = run(read_everything(source.account_page, limit=limit))

    assert hashes(transactions) == [r["hash"] for r in rows], (seed, limit, window)
    assert not last.truncated


# --- the three streams -------------------------------------------------------------------------


def test_account_pages_carry_transactions_and_the_coin_they_sent_from_one_request():
    sent = tx_row(10, ME, OTHER, value="1000000000000000000")
    failed = tx_row(11, ME, OTHER, value="5", isError="1", txreceipt_status="0")
    call = tx_row(12, ME, OTHER, value="0", methodId="0xa9059cbb", input="0xa9059cbb00")
    fake = FakeEtherscan()
    fake.add("txlist", sent, failed, call)
    source, _, _ = make(fake)

    page = run(source.account_page(ETHEREUM, ME))

    assert len(fake.requests) == 1
    assert [tx.status for tx in page.transactions] == [TxStatus.SUCCESS, TxStatus.FAILED] + [
        TxStatus.SUCCESS
    ]
    assert [t.amount_raw for t in page.native_transfers] == ["1000000000000000000"]
    assert page.transactions[2].is_contract_call


def test_internal_transfers_skip_calls_that_moved_nothing_or_reverted():
    fake = FakeEtherscan()
    fake.add(
        "txlistinternal",
        internal_row(10, value="5"),
        internal_row(11, value="0"),
        internal_row(12, value="9", isError="1"),
    )
    source, _, _ = make(fake)

    page = run(source.internal_page(ETHEREUM, ME))

    assert [t.amount_raw for t in page.items] == ["5"]
    assert page.items[0].transfer_type is TransferType.INTERNAL


def test_token_transfers_get_distinct_keys_for_identical_movements_in_one_transaction():
    same_tx = "0x" + "ee" * 32
    fake = FakeEtherscan()
    fake.add(
        "tokentx",
        token_row(10, hash=same_tx),
        token_row(10, hash=same_tx),
        token_row(10, hash=same_tx, value="8"),
    )
    source, _, _ = make(fake)

    page = run(source.token_page(ETHEREUM, ME))

    keys = [t.event_key for t in page.items]
    assert len(keys) == 3 and len(set(keys)) == 3
    assert keys[1] == keys[0] + "#1"


def test_unreadable_rows_are_skipped_and_the_page_says_so():
    fake = FakeEtherscan()
    fake.add("tokentx", token_row(10), token_row(11, contractAddress="garbage"), token_row(12))
    source, _, _ = make(fake)

    page = run(source.token_page(ETHEREUM, ME))

    assert [t.block_number for t in page.items] == [10, 12]
    assert page.coverage == ("1 rows from etherscan could not be read and were skipped",)


# --- errors that arrive as 200 -----------------------------------------------------------------


def test_a_rate_limit_reply_is_retried_and_the_history_still_arrives():
    fake = FakeEtherscan()
    fake.add("txlist", tx_row(10))
    fake.queued = [notok("Max rate limit reached, please use API Key for higher rate limit")]
    source, _, clock = make(fake)

    page = run(source.account_page(ETHEREUM, ME))

    assert len(page.transactions) == 1 and len(fake.requests) == 2
    assert clock.sleeps  # it waited before the second try


def test_a_rejected_key_is_reported_once_and_never_retried():
    fake = FakeEtherscan(key="another-key-entirely-98765")
    source, _, _ = make(fake)
    with pytest.raises(ProviderAuthError) as info:
        run(source.account_page(ETHEREUM, ME))
    assert len(fake.requests) == 1 and not info.value.retryable
    assert KEY not in str(info.value)


def test_a_chain_missing_from_the_plan_says_so_in_plain_words():
    fake = FakeEtherscan()
    fake.queued = [
        notok(
            "Free API access is not supported for this chain. "
            "Please upgrade your api plan for full chain coverage."
        )
    ]
    source, _, _ = make(fake)
    with pytest.raises(ProviderAuthError, match="plan does not include this chain"):
        run(source.account_page(ETHEREUM, ME))


def test_the_shared_free_pool_running_out_blocks_the_provider_until_it_resets():
    message = (
        "Community Free API limit reached. Resets 2030-01-01 12:00:00 UTC. "
        "For higher, uninterrupted limits, please upgrade"
    )
    fake = FakeEtherscan()
    fake.queued = [notok(message)]
    source, _, _ = make(fake)

    with pytest.raises(ProviderRateLimited) as info:
        run(source.account_page(ETHEREUM, ME))

    assert info.value.retry_after > 24 * 3600  # far in the future, so it is not slept through
    assert len(fake.requests) == 1


def test_a_response_that_is_not_what_the_endpoint_promises_is_a_bad_response():
    fake = FakeEtherscan()
    fake.queued = [{"status": "1", "message": "OK", "result": "surprise"}]
    source, _, _ = make(fake)
    with pytest.raises(ProviderBadResponse):
        run(source.account_page(ETHEREUM, ME))


@pytest.mark.parametrize(
    ("text", "kind", "retry_after"),
    [
        (
            "Max rate limit reached, please use API Key for higher rate limit",
            ProviderRateLimited,
            None,
        ),
        ("Invalid API Key", ProviderAuthError, None),
        ("Too many invalid api key attempts, please try again later", ProviderRateLimited, 30.0),
        (
            "Free API access is not supported for this chain. Please upgrade",
            ProviderAuthError,
            None,
        ),
        (
            "Missing or unsupported chainid parameter (required for v2 api)",
            ProviderBadResponse,
            None,
        ),
        (
            "Query Timeout occured. Please select a smaller result dataset",
            ProviderUnavailable,
            None,
        ),
        (
            "Unexpected err, timeout occurred or server too busy. Please try again later",
            ProviderUnavailable,
            None,
        ),
        ("Error! Missing Or invalid Action name", ProviderBadResponse, None),
        ("Something nobody has seen before", ProviderBadResponse, None),
    ],
)
def test_every_documented_error_text_maps_to_the_right_kind_of_failure(text, kind, retry_after):
    error = error_from_text("etherscan", text)
    assert type(error) is kind and error.retry_after == retry_after


def test_an_unknown_error_text_is_repeated_scrubbed_and_short():
    secrets.add("sekrit-key-0123456789")
    error = error_from_text("etherscan", "bad thing with sekrit-key-0123456789 " + "x" * 500)
    assert "sekrit-key-0123456789" not in error.reason and len(error.reason) < 200


def test_the_reset_time_in_the_pool_message_becomes_a_bounded_wait():
    now = datetime(2026, 6, 24, 11, 0, 0, tzinfo=UTC)
    assert seconds_until_reset("Resets 2026-06-24 12:00:00 UTC", now) == 3600.0
    assert seconds_until_reset("Resets 2026-06-24 11:00:05 UTC", now) == 60.0  # at least a minute
    assert seconds_until_reset("Resets 2099-01-01 00:00:00 UTC", now) == 7 * 24 * 3600.0
    assert seconds_until_reset("no time given", now) == 3600.0


def test_bodies_of_every_shape_etherscan_uses_are_classified():
    check = etherscan_body_error
    assert check("etherscan", {"status": "1", "message": "OK", "result": []}) is None
    assert (
        check("etherscan", {"status": "0", "message": "No transactions found", "result": []})
        is None
    )
    assert check("etherscan", {"jsonrpc": "2.0", "id": 1, "result": "0x1"}) is None
    assert isinstance(
        check("etherscan", {"jsonrpc": "2.0", "id": 1, "error": {"code": 429, "message": "x"}}),
        ProviderRateLimited,
    )
    assert isinstance(check("etherscan", ["not", "an", "object"]), ProviderBadResponse)


# --- a single transaction ----------------------------------------------------------------------

TX_HASH = "0x" + "ab" * 32


def mined_transaction(fake, *, with_block_time):
    tx = {
        "hash": TX_HASH,
        "from": ME,
        "to": OTHER,
        "value": "0x10",
        "input": "0x",
        "blockNumber": "0x64",
    }
    if with_block_time:
        tx["blockTimestamp"] = "0x6553f100"
    fake.transactions[TX_HASH] = tx
    fake.receipts[TX_HASH] = {
        "transactionHash": TX_HASH,
        "blockNumber": "0x64",
        "status": "0x1",
        "logs": [],
    }
    fake.blocks["0x64"] = {"timestamp": "0x6553f100"}


def test_a_mined_transaction_comes_back_with_its_receipt_and_block_time():
    fake = FakeEtherscan()
    mined_transaction(fake, with_block_time=True)
    source, _, _ = make(fake)

    tx, receipt, when = run(source.transaction(ETHEREUM, TX_HASH))

    assert tx["hash"] == TX_HASH and receipt["status"] == "0x1"
    assert when == datetime.fromtimestamp(0x6553F100, UTC)
    assert [c["action"] for c in fake.calls] == [
        "eth_getTransactionByHash",
        "eth_getTransactionReceipt",
    ]


def test_the_block_time_is_looked_up_when_the_transaction_does_not_carry_it():
    fake = FakeEtherscan()
    mined_transaction(fake, with_block_time=False)
    source, _, _ = make(fake)

    _, _, when = run(source.transaction(ETHEREUM, TX_HASH))

    assert when == datetime.fromtimestamp(0x6553F100, UTC)
    assert fake.calls[-1]["action"] == "eth_getBlockByNumber" and fake.calls[-1]["tag"] == "0x64"


def test_unknown_and_unmined_transactions_are_distinguished():
    fake = FakeEtherscan()
    source, _, _ = make(fake)
    assert run(source.transaction(ETHEREUM, TX_HASH)) == (None, None, None)

    fake.transactions[TX_HASH] = {"hash": TX_HASH, "blockNumber": None}
    tx, receipt, when = run(source.transaction(ETHEREUM, TX_HASH))
    assert tx is not None and receipt is None and when is None


def test_the_block_height_is_read_from_the_proxy_module():
    source, _, _ = make(FakeEtherscan())
    assert run(source.block_height(ETHEREUM)) == HEAD
