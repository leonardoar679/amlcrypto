"""The Esplora source against an in-memory Esplora: paging, cursors, ranges, errors, details."""

from __future__ import annotations

import httpx2
import pytest

from app.chains.bitcoin.chains import BITCOIN_CHAIN
from app.chains.bitcoin.models import SpendState
from app.chains.cursors import InvalidCursor
from app.providers.base import BudgetExceeded, ProviderBadResponse
from app.providers.budget import RequestBudget
from app.providers.client import ProviderClient
from app.providers.esplora import PAGE_SIZE, EsploraSource
from app.providers.registry import PROVIDERS
from tests.fake_esplora import HEAD, FakeEsplora, p2pkh, pay, spend, tx, txid
from tests.helpers import FakeClock, FullJitter, run

CHAIN = BITCOIN_CHAIN
ME = p2pkh(1)


def make(fake: FakeEsplora, *, budget=None):
    clock = FakeClock()
    client = ProviderClient(
        {"esplora": PROVIDERS["esplora"]},
        {},
        transport=httpx2.MockTransport(fake),
        rate_limits={"esplora": 1000.0},
        clock=clock,
        sleep=clock.sleep,
        rng=FullJitter(),
    )
    return EsploraSource(client, budget=budget), clock


def fill(
    fake: FakeEsplora, count: int, *, address: str = ME, first_height: int = 101
) -> list[dict]:
    """`count` transactions paying `address`, one per block, the oldest first (id 1, block 101)."""
    added = [
        tx(
            n,
            [spend(txid(5000 + n), 0, p2pkh(9000 + n), 10_000)],
            [pay(address, 9_000)],
            height=first_height + n - 1,
        )
        for n in range(1, count + 1)
    ]
    fake.add(*added)
    return added


def read_all(source, address=ME, **options):
    """Every page until the cursor runs out: (transactions, number of pages)."""

    async def go():
        found, cursor, pages = [], None, 0
        while True:
            page = await source.address_page(CHAIN, address, cursor=cursor, **options)
            found += page.items
            pages += 1
            assert pages < 50
            if page.next_cursor is None:
                return found, pages
            cursor = page.next_cursor

    return run(go())


def paths(fake):
    return [path for path, _ in fake.calls]


# --- the chain and the node's own figures -------------------------------------------------------


def test_the_height_is_the_tip():
    source, _ = make(FakeEsplora())
    assert run(source.block_height(CHAIN)) == HEAD


@pytest.mark.parametrize("answer", ["not a number", "-5", "12.5", "null"])
def test_a_height_that_is_not_a_height_is_a_bad_response(answer):
    fake = FakeEsplora()
    fake.queued = [(200, answer)]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="unexpected response shape"):
        run(source.block_height(CHAIN))


def test_the_nodes_figures_for_an_address_are_read():
    fake = FakeEsplora()
    fill(fake, 3)
    fake.add(tx(50, [spend(txid(1), 0, ME, 9_000)], [pay(p2pkh(77), 8_000)], height=200))
    source, _ = make(fake)

    summary = run(source.address_summary(CHAIN, ME))

    assert (summary.tx_count, summary.funded_count, summary.spent_count) == (4, 3, 1)
    assert (summary.funded_raw, summary.spent_raw, summary.balance_raw) == (
        "27000",
        "9000",
        "18000",
    )
    assert summary.mempool_tx_count == 0 and summary.provider == "esplora"


@pytest.mark.parametrize(
    "body",
    [{}, {"chain_stats": {}, "mempool_stats": {}}, {"chain_stats": [], "mempool_stats": []}, []],
)
def test_figures_that_are_not_figures_are_a_bad_response(body):
    fake = FakeEsplora()
    fake.queued = [(200, body)]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="unexpected response shape"):
        run(source.address_summary(CHAIN, ME))


# --- paging ------------------------------------------------------------------------------------


def test_a_short_history_is_one_page_newest_first():
    fake = FakeEsplora()
    fill(fake, 3)
    source, _ = make(fake)

    page = run(source.address_page(CHAIN, ME))

    assert [t.txid for t in page.items] == [txid(3), txid(2), txid(1)]
    assert page.next_cursor is None and page.coverage == () and not page.truncated
    assert all(t.provider == "esplora" for t in page.items)


def test_pages_follow_each_other_through_the_last_transaction_without_gaps_or_repeats():
    fake = FakeEsplora()
    fill(fake, 60)
    source, _ = make(fake)

    found, pages = read_all(source)

    assert pages == 3
    assert [t.txid for t in found] == [txid(n) for n in range(60, 0, -1)]
    continued = [path.rsplit("/", 1)[1] for path in paths(fake) if "/txs/chain/" in path]
    assert continued == [
        txid(36),
        txid(11),
    ]  # each page continues after the last id of the one before


def test_a_history_of_exactly_one_page_ends_on_the_empty_page_after_it():
    fake = FakeEsplora()
    fill(fake, PAGE_SIZE)
    source, _ = make(fake)

    found, pages = read_all(source)

    assert len(found) == PAGE_SIZE and pages == 2  # the second page is empty


def test_an_unknown_cursor_gives_an_empty_page_as_the_real_service_does():
    fake = FakeEsplora()
    fill(fake, 3)
    source, _ = make(fake)
    page = run(source.address_page(CHAIN, ME, cursor=txid(999)))
    assert page.items == [] and page.next_cursor is None


def test_a_cursor_that_is_not_a_transaction_id_is_refused_before_any_request():
    fake = FakeEsplora()
    source, _ = make(fake)
    # The real service answers a malformed cursor with the FIRST page again, which would loop.
    for bad in ("xyz", "../etc", "ab" * 31, "AB" * 32, " " + "ab" * 32):
        with pytest.raises(InvalidCursor):
            run(source.address_page(CHAIN, ME, cursor=bad))
    assert fake.requests == []


# --- block ranges ------------------------------------------------------------------------------


def test_the_read_ends_at_the_first_page_that_reaches_below_the_start_block():
    fake = FakeEsplora()
    fill(fake, 60)  # blocks 101..160
    source, _ = make(fake)

    found, pages = read_all(source, from_block=140)

    assert sorted(t.block_height for t in found) == list(range(140, 161))
    assert pages == 1  # the first page already reached block 136
    assert len(fake.requests) == 1


def test_transactions_above_the_end_block_are_skipped_but_older_ones_are_still_reached():
    fake = FakeEsplora()
    fill(fake, 60)
    source, _ = make(fake)

    found, pages = read_all(source, to_block=120)

    assert sorted(t.block_height for t in found) == list(range(101, 121))
    assert pages == 3  # the newest pages hold nothing wanted but must be read through


def test_a_range_with_both_ends_reads_only_what_it_needs():
    fake = FakeEsplora()
    fill(fake, 60)
    source, _ = make(fake)

    found, pages = read_all(source, from_block=110, to_block=120)

    assert sorted(t.block_height for t in found) == list(range(110, 121))
    assert pages == 3 and len(fake.requests) == 3  # 136-160, 111-135, then below 110: stop


# --- when the service is not as promised --------------------------------------------------------


def test_a_cursor_the_service_no_longer_knows_is_reported_clearly():
    fake = FakeEsplora()
    fake.queued = [(404, "Transaction not found")]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="no longer in the chain"):
        run(source.address_page(CHAIN, ME, cursor=txid(5)))


def test_a_404_on_the_first_page_is_an_error_not_an_empty_history():
    fake = FakeEsplora()
    fake.queued = [(404, "Not found")]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="HTTP 404"):
        run(source.address_page(CHAIN, ME))


def test_a_bad_address_is_a_bad_request_not_a_retry_loop():
    fake = FakeEsplora()
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse) as info:
        run(source.address_page(CHAIN, "not-an-address"))
    assert not info.value.retryable and len(fake.requests) == 1


@pytest.mark.parametrize("body", [{"not": "a list"}, "oops", [1, 2], None])
def test_a_listing_that_is_not_a_list_of_transactions_is_a_bad_response(body):
    fake = FakeEsplora()
    fake.queued = [(200, body)]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="unexpected response shape"):
        run(source.address_page(CHAIN, ME))
    assert len(fake.requests) == 1


def test_unreadable_transactions_are_skipped_and_the_page_says_so():
    fake = FakeEsplora()
    good = fill(fake, 2)
    broken = {**good[0], "txid": txid(77), "vin": []}
    fake.queued = [[broken, good[1]]]
    source, _ = make(fake)

    page = run(source.address_page(CHAIN, ME))

    assert [t.txid for t in page.items] == [good[1]["txid"]]
    assert page.coverage == ("1 transactions from esplora could not be read and were skipped",)


def test_a_full_page_whose_last_id_is_unreadable_cannot_be_continued_and_says_so():
    fake = FakeEsplora()
    rows = fill(fake, PAGE_SIZE)
    rows[-1] = {**rows[-1], "txid": "garbage"}
    fake.queued = [rows]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="unexpected response shape"):
        run(source.address_page(CHAIN, ME))


def test_inputs_whose_spent_output_the_service_did_not_give_are_counted_in_the_notes():
    fake = FakeEsplora()
    (row,) = fill(fake, 1)
    row["vin"][0]["prevout"] = None
    source, _ = make(fake)

    page = run(source.address_page(CHAIN, ME))

    assert len(page.items) == 1
    assert page.coverage == (
        "1 transactions have inputs whose spent output esplora did not give; "
        "amounts sent in them may be short",
    )


@pytest.mark.parametrize("options", [{}, {"from_block": 1}, {"to_block": HEAD}])
def test_an_unconfirmed_transaction_in_a_listing_is_left_out(options):
    fake = FakeEsplora()
    (confirmed,) = fill(fake, 1)
    pending = tx(77, [spend(txid(9), 0, p2pkh(5), 9_000)], [pay(ME, 8_000)], height=None)
    fake.queued = [[pending, confirmed]]
    source, _ = make(fake)

    page = run(source.address_page(CHAIN, ME, **options))

    assert [t.txid for t in page.items] == [confirmed["txid"]]


def test_a_429_is_waited_out_and_the_read_goes_on():
    fake = FakeEsplora()
    fill(fake, 2)
    fake.queued = [(429, "Too many requests")]
    source, clock = make(fake)

    page = run(source.address_page(CHAIN, ME))

    assert len(page.items) == 2 and len(fake.requests) == 2 and clock.sleeps


# --- one transaction ---------------------------------------------------------------------------


def test_a_transaction_comes_with_its_spend_list():
    fake = FakeEsplora()
    first = tx(1, [spend(txid(900), 0, p2pkh(5), 9_000)], [pay(ME, 4_000), pay(p2pkh(6), 4_500)])
    fake.add(first, tx(2, [spend(txid(1), 0, ME, 4_000)], [pay(p2pkh(7), 3_000)]))
    source, _ = make(fake)

    raw, outspends = run(source.transaction(CHAIN, txid(1)))

    assert raw["txid"] == txid(1)
    assert [o["spent"] for o in outspends] == [True, False]
    assert paths(fake) == [f"/tx/{txid(1)}", f"/tx/{txid(1)}/outspends"]


def test_an_unknown_transaction_is_none_after_one_request():
    fake = FakeEsplora()
    source, _ = make(fake)
    assert run(source.transaction(CHAIN, txid(404))) == (None, None)
    assert len(fake.requests) == 1


def test_a_failure_to_get_the_spend_list_does_not_fail_the_lookup():
    fake = FakeEsplora()
    fill(fake, 1)
    fake.broken_outspends = True
    source, _ = make(fake)

    raw, outspends = run(source.transaction(CHAIN, txid(1)))

    assert raw["txid"] == txid(1) and outspends is None


def test_an_exhausted_budget_is_not_mistaken_for_a_missing_spend_list():
    fake = FakeEsplora()
    fill(fake, 1)
    source, _ = make(fake, budget=RequestBudget(1))
    with pytest.raises(BudgetExceeded):
        run(source.transaction(CHAIN, txid(1)))


def test_a_transaction_answer_that_is_not_an_object_is_a_bad_response():
    fake = FakeEsplora()
    fake.queued = [(200, "text")]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="unexpected response shape"):
        run(source.transaction(CHAIN, txid(1)))


def test_the_spend_list_applies_to_what_the_source_returned():
    from app.chains.bitcoin.normalize import apply_outspends, esplora_transaction

    fake = FakeEsplora()
    fake.add(
        tx(1, [spend(txid(900), 0, p2pkh(5), 9_000)], [pay(ME, 4_000), pay(None, 0, "op_return")]),
        tx(2, [spend(txid(1), 0, ME, 4_000)], [pay(p2pkh(7), 3_000)]),
    )
    source, _ = make(fake)
    raw, outspends = run(source.transaction(CHAIN, txid(1)))

    result = apply_outspends(esplora_transaction(raw, CHAIN), outspends)

    assert [o.spend_state for o in result.outputs] == [SpendState.SPENT, SpendState.UNSPENDABLE]
