"""The TronGrid source against an in-memory TronGrid: paging, time ranges, errors, details."""

from __future__ import annotations

import httpx2
import pytest

from app.chains.cursors import InvalidCursor
from app.chains.models import TransferType
from app.chains.tron.chains import TRON_CHAIN
from app.chains.tron.normalize import hex_to_base58
from app.providers.base import (
    ProviderAuthError,
    ProviderBadResponse,
    ProviderRateLimited,
)
from app.providers.client import ProviderClient
from app.providers.registry import PROVIDERS
from app.providers.trongrid import TronGridSource, error_from_text, trongrid_body_error
from tests.fake_trongrid import (
    ALICE,
    BOB,
    HEAD,
    KEY,
    T0,
    FakeTronGrid,
    internal_row,
    trc20_row,
    trx_row,
)
from tests.helpers import FakeClock, FullJitter, load_fixture, run

CHAIN = TRON_CHAIN


def make(fake: FakeTronGrid):
    clock = FakeClock()
    client = ProviderClient(
        {"trongrid": PROVIDERS["trongrid"]},
        {"trongrid": KEY},
        transport=httpx2.MockTransport(fake),
        rate_limits={"trongrid": 1000.0},
        clock=clock,
        sleep=clock.sleep,
        rng=FullJitter(),
    )
    return TronGridSource(client), clock


def feed_calls(fake):
    return [params for path, params in fake.calls if path.startswith("/v1/accounts/")]


# --- the chain ---------------------------------------------------------------------------------


def test_the_block_height_is_read_from_the_latest_block():
    source, _ = make(FakeTronGrid())
    assert run(source.block_height(CHAIN)) == HEAD


# --- paging ------------------------------------------------------------------------------------


def test_a_short_history_is_one_page_asking_for_confirmed_ascending_rows():
    fake = FakeTronGrid()
    rows = [trx_row(T0 + i * 3000, ALICE, BOB, amount=i + 1) for i in range(3)]
    fake.add("transactions", *rows)
    source, _ = make(fake)

    page = run(source.account_page(CHAIN, ALICE, limit=50))

    assert [t.transaction_hash for t in page.transactions] == [r["txID"] for r in rows]
    assert [t.amount_raw for t in page.native_transfers] == ["1", "2", "3"]
    assert page.next_cursor is None
    (params,) = feed_calls(fake)
    assert params["only_confirmed"] == "true" and params["order_by"] == "block_timestamp,asc"
    assert params["limit"] == "50" and "fingerprint" not in params


def test_pages_follow_each_other_through_the_fingerprint_without_gaps_or_repeats():
    fake = FakeTronGrid()
    rows = [trc20_row(T0 + i * 3000, ALICE, BOB, value=i + 1) for i in range(25)]
    fake.add("transactions/trc20", *rows)
    source, _ = make(fake)

    async def read_all():
        items, cursor, pages = [], None, 0
        while True:
            page = await source.transfer_page(
                CHAIN, ALICE, TransferType.TRC20, cursor=cursor, limit=10
            )
            items += page.items
            pages += 1
            assert pages < 20
            if page.next_cursor is None:
                return items, pages
            cursor = page.next_cursor

    items, pages = run(read_all())

    assert [t.amount_raw for t in items] == [str(i + 1) for i in range(25)] and pages == 3


def test_a_page_with_no_rows_ends_the_read_even_if_a_fingerprint_comes_with_it():
    fake = FakeTronGrid()
    fake.queued = [{"data": [], "success": True, "meta": {"fingerprint": "abc", "page_size": 0}}]
    source, _ = make(fake)
    assert run(source.account_page(CHAIN, ALICE)).next_cursor is None


def test_the_page_size_is_capped_at_what_tron_allows():
    fake = FakeTronGrid()
    source, _ = make(fake)
    run(source.account_page(CHAIN, ALICE, limit=5000))
    assert feed_calls(fake)[0]["limit"] == "200"


def test_a_cursor_that_is_not_a_fingerprint_is_refused_before_any_request():
    fake = FakeTronGrid()
    source, _ = make(fake)
    for bad in ("../etc", "has space", "x" * 2000, "a|b", "abc\n"):
        with pytest.raises(InvalidCursor):
            run(source.account_page(CHAIN, ALICE, cursor=bad))
    assert fake.requests == []


# --- block ranges are times --------------------------------------------------------------------


def test_a_block_range_becomes_a_time_range_and_each_block_is_looked_up_once():
    fake = FakeTronGrid()
    fake.block_times.update({100: T0 + 10_000, 200: T0 + 40_000})
    fake.add(
        "transactions/trc20",
        *[trc20_row(T0 + i * 10_000, ALICE, BOB, value=i + 1) for i in range(8)],
    )
    source, _ = make(fake)

    async def read():
        first = await source.transfer_page(
            CHAIN, ALICE, TransferType.TRC20, limit=2, from_block=100, to_block=200
        )
        second = await source.transfer_page(
            CHAIN,
            ALICE,
            TransferType.TRC20,
            cursor=first.next_cursor,
            limit=2,
            from_block=100,
            to_block=200,
        )
        return first, second

    first, second = run(read())

    assert [t.amount_raw for t in first.items + second.items] == ["2", "3", "4", "5"]
    lookups = [path for path, _ in fake.calls if path == "/wallet/getblockbynum"]
    assert len(lookups) == 2  # blocks 100 and 200, however many pages
    assert feed_calls(fake)[0]["min_timestamp"] == str(T0 + 10_000)
    assert feed_calls(fake)[0]["max_timestamp"] == str(T0 + 40_000)


def test_a_block_that_does_not_exist_is_reported_not_guessed():
    source, _ = make(FakeTronGrid())
    with pytest.raises(ProviderBadResponse, match="block 5 is unknown"):
        run(source.account_page(CHAIN, ALICE, from_block=5))


# --- the three streams -------------------------------------------------------------------------


def test_internal_and_token_streams_skip_what_moved_nothing_or_is_not_a_transfer():
    fake = FakeTronGrid()
    fake.add(
        "internal-transactions",
        internal_row(T0, sun=7),
        internal_row(T0 + 1, sun=0),
        {**internal_row(T0 + 2), "data": {"rejected": True, "call_value": {"_": 5}}},
    )
    fake.add("transactions/trc20", trc20_row(T0), {**trc20_row(T0 + 1), "type": "Approval"})
    source, _ = make(fake)

    internal = run(source.transfer_page(CHAIN, ALICE, TransferType.INTERNAL))
    tokens = run(source.transfer_page(CHAIN, ALICE, TransferType.TRC20))

    assert [t.amount_raw for t in internal.items] == ["7"]
    assert len(tokens.items) == 1
    with pytest.raises(ValueError):
        run(source.transfer_page(CHAIN, ALICE, TransferType.NATIVE))


def test_unreadable_rows_are_skipped_and_the_page_says_so():
    fake = FakeTronGrid()
    fake.add("transactions/trc20", trc20_row(T0), {**trc20_row(T0 + 1), "value": "-1"})
    source, _ = make(fake)

    page = run(source.transfer_page(CHAIN, ALICE, TransferType.TRC20))

    assert len(page.items) == 1
    assert page.coverage == ("1 rows from trongrid could not be read and were skipped",)


def test_a_row_whose_transfer_cannot_be_read_is_skipped_whole_and_counted():
    fake = FakeTronGrid()
    good = load_fixture("tron/feed_trc10")["row"]
    bad = load_fixture("tron/feed_trc10")["row"]
    bad["txID"] = "ef" * 32
    bad["raw_data"]["contract"][0]["parameter"]["value"]["asset_name"] = ""  # the token is unknown
    owner = good["raw_data"]["contract"][0]["parameter"]["value"]["owner_address"]
    fake.add("transactions", good, bad)
    source, _ = make(fake)

    page = run(source.account_page(CHAIN, hex_to_base58(owner)))

    # Not a transaction kept without its transfer: both or neither, and the page says so.
    assert [t.transaction_hash for t in page.transactions] == [good["txID"]]
    assert [t.transaction_hash for t in page.native_transfers] == [good["txID"]]
    assert page.coverage == ("1 rows from trongrid could not be read and were skipped",)


def test_real_rows_pass_through_the_feeds_untouched_by_the_fake():
    fake = FakeTronGrid()
    row = load_fixture("tron/feed_trc10")["row"]
    owner = row["raw_data"]["contract"][0]["parameter"]["value"]["owner_address"]

    fake.add("transactions", row)
    source, _ = make(fake)

    page = run(source.account_page(CHAIN, hex_to_base58(owner)))

    assert [t.transfer_type for t in page.native_transfers] == [TransferType.TRC10]
    assert page.transactions[0].fee_raw == "0"


# --- errors ------------------------------------------------------------------------------------


def test_an_invalid_address_is_a_bad_request_not_a_retry_loop():
    fake = FakeTronGrid()
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="not a valid TRON address") as info:
        run(source.account_page(CHAIN, "not-an-address"))
    assert not info.value.retryable and len(fake.requests) == 1


def test_a_rejected_key_is_reported_once_and_never_retried():
    fake = FakeTronGrid(key="some-other-key-entirely-9999")
    source, _ = make(fake)
    with pytest.raises(ProviderAuthError):
        run(source.block_height(CHAIN))
    assert len(fake.requests) == 1


def test_a_429_is_waited_out_and_the_read_goes_on():
    fake = FakeTronGrid()
    fake.add("transactions", trx_row(T0))
    fake.queued = [(429, {"Error": "Too many requests"})]
    source, clock = make(fake)

    page = run(source.account_page(CHAIN, ALICE))

    assert len(page.transactions) == 1 and len(fake.requests) == 2 and clock.sleeps


@pytest.mark.parametrize(
    "body",
    [
        {"success": True, "data": "oops"},
        {"success": True, "data": [1, 2]},
        {"success": True},
        ["a", "list"],
    ],
)
def test_a_feed_that_is_not_a_list_of_rows_is_a_bad_response_not_an_empty_history(body):
    fake = FakeTronGrid()
    fake.queued = [body]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="unexpected response shape"):
        run(source.account_page(CHAIN, ALICE))
    assert len(fake.requests) == 1  # a malformed answer is not retried


@pytest.mark.parametrize("body", [{}, {"block_header": {}}, {"block_header": {"raw_data": {}}}])
def test_a_height_that_cannot_be_read_is_a_bad_response(body):
    fake = FakeTronGrid()
    fake.queued = [body]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="unexpected response shape"):
        run(source.block_height(CHAIN))


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Rate limit exceeded", ProviderRateLimited),
        ("Too many requests", ProviderRateLimited),
        ("Invalid api key", ProviderAuthError),
        ("A valid account address is required.", ProviderBadResponse),
        ("something nobody has seen", ProviderBadResponse),
    ],
)
def test_error_texts_map_to_the_right_kind_of_failure(text, kind):
    assert type(error_from_text("trongrid", text)) is kind


def test_bodies_of_both_error_styles_are_recognised_and_healthy_ones_pass():
    check = trongrid_body_error
    assert check("trongrid", {"success": True, "data": []}) is None
    assert check("trongrid", {"block_header": {}}) is None
    assert isinstance(
        check("trongrid", {"success": False, "error": "Rate limit exceeded"}), ProviderRateLimited
    )
    assert isinstance(check("trongrid", {"Error": "Invalid api key"}), ProviderAuthError)
    assert check("trongrid", ["a", "list"]) is None


# --- one transaction ---------------------------------------------------------------------------


def test_a_transaction_comes_back_with_its_details_and_block_time():
    fake = FakeTronGrid()
    data = load_fixture("tron/detail_trx")
    txid = data["info"]["id"]
    fake.transactions[txid], fake.infos[txid] = data["tx"], data["info"]
    source, _ = make(fake)

    tx, info, when = run(source.transaction(CHAIN, txid))

    assert tx["txID"] == txid and info["blockNumber"] == data["info"]["blockNumber"]
    assert int(when.timestamp() * 1000) == data["info"]["blockTimeStamp"]
    bodies = [r.content for r in fake.requests]
    assert b'"visible":true' in bodies[0].replace(b" ", b"")  # Base58 addresses, please


def test_unknown_and_unmined_transactions_are_distinguished():
    fake = FakeTronGrid()
    source, _ = make(fake)
    txid = "ab" * 32
    assert run(source.transaction(CHAIN, txid)) == (None, None, None)

    fake.transactions[txid] = {"txID": txid, "raw_data": {}}
    tx, info, when = run(source.transaction(CHAIN, txid))
    assert tx is not None and info is None and when is None
