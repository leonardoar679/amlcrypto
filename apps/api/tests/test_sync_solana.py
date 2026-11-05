"""Screening a Solana wallet again: what is reused, what is read, and the check against the chain.

The wallet's SOL and token balances are compared with what its stored history adds up to every time
the history is complete after an update, the new part and the old together.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.chains.solana.chains import SOLANA_CHAIN
from app.chains.solana.normalize import solana_transaction
from app.database.models import StoredTransaction
from app.database.repositories.acquisition import load_coverage, load_raw, recent_runs
from app.ingestion.raw import RawCollector, unpack
from app.ingestion.solana import fetch_solana_history
from app.ingestion.sync import Sessions, SyncPolicy, sync_history
from app.providers.base import ProviderUnavailable
from app.providers.budget import RequestBudget
from tests.fake_helius import HEAD, address, signature, transfer_item
from tests.helpers import run
from tests.rigs import Rig
from tests.test_solana_history import MINT, TOKEN, W, flow, incoming

CHAIN_ID = SOLANA_CHAIN.chain_id
MARGIN = SOLANA_CHAIN.lag_margin


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def pass_seconds(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def arrive(rig: Rig, n: int, *, slots: int = 1_000) -> None:
    """A new payment to the wallet after the chain has moved on."""
    rig.helius.head += slots
    rig.helius.add(transfer_item(n, address(3000 + n), W, 20_000 + n, slot=rig.helius.head - 10))


def sync(rig, db, clock, *, raw=None, budget_limit=1000, adapter=None, **policy):
    budget = RequestBudget(budget_limit)
    return run(
        sync_history(
            adapter or rig.solana_adapter(budget=budget, raw=raw),
            W,
            sessions=Sessions(db.read, db.write),
            policy=SyncPolicy(**policy),
            budget=budget,
            raw=raw,
            clock=clock,
        )
    )


def live(rig):
    return run(fetch_solana_history(rig.solana_adapter(), W))


def same_history(stored, fresh):
    assert stored.transactions == fresh.transactions
    assert stored.activity == fresh.activity
    assert (stored.complete, stored.head_block, stored.synced_through_block) == (
        fresh.complete,
        fresh.head_block,
        fresh.synced_through_block,
    )
    assert stored.coverage == fresh.coverage and stored.resume == fresh.resume
    assert stored.figures == fresh.figures


def listings(rig) -> int:
    return rig.helius.methods().count("getTransactionsForAddress")


def count(db, model):
    with db.read() as session:
        return session.scalar(select(func.count()).select_from(model))


# --- the first screening and the acceptance test -----------------------------------------------


def test_the_first_screening_reads_checks_and_keeps_the_whole_history(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)

    result = sync(rig, committing_db, clock)

    same_history(result.history, live(rig))
    assert result.history.complete and result.history.figures is not None
    assert (result.report.mode, result.report.verified) == ("full", "ok")
    assert count(committing_db, StoredTransaction) == 3
    with committing_db.read() as session:
        row = load_coverage(session, CHAIN_ID, W)["transactions"]
        (logged,) = recent_runs(session, CHAIN_ID, W)
    assert (row.status, row.verified, row.synced_through_block) == ("complete", "ok", HEAD)
    assert row.details["figures"]["tokens"][TOKEN]["balances"] == {MINT: "300"}
    assert (logged.mode, logged.outcome) == ("full", "complete")
    # the log counts the check of the merged history too, like the report does
    assert logged.requests_used == result.report.requests_used > 0


def test_screening_the_same_wallet_again_asks_the_provider_nothing(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    first = sync(rig, committing_db, clock)
    asked = len(rig.helius.requests)

    clock.pass_seconds(60)
    second = sync(rig, committing_db, clock)

    assert len(rig.helius.requests) == asked
    assert second.report.mode == "cached" and second.report.requests_used == 0
    assert second.report.verified == "ok" and second.report.age_seconds == 60
    same_history(second.history, first.history)
    assert second.history.figures == first.history.figures


def test_the_token_payment_that_never_mentions_the_wallet_survives_storage(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    sync(rig, committing_db, clock)

    clock.pass_seconds(10)
    cached = sync(rig, committing_db, clock)

    token = cached.history.activity[signature(2)]
    assert token.sol_delta_raw == "0" and token.token_deltas == ((MINT, 6, "300"),)
    assert signature(2) in {t.signature for t in cached.history.transactions}


def test_a_wallet_the_provider_has_never_seen_is_a_complete_empty_history(committing_db):
    rig, clock = Rig(), Clock()

    first = sync(rig, committing_db, clock)
    second = sync(rig, committing_db, clock)

    assert first.history.complete and not first.history.transactions
    assert second.report.mode == "cached" and second.history.complete


# --- bringing it up to date --------------------------------------------------------------------


def test_an_update_asks_for_the_newest_slots_only_and_one_page_not_three(committing_db):
    rig, clock = Rig(), Clock()
    incoming(rig, 250)
    first = sync(rig, committing_db, clock)
    assert listings(rig) == 3
    arrive(rig, 251)
    before = len(rig.helius.requests)

    clock.pass_seconds(600)
    second = sync(rig, committing_db, clock)
    asked = rig.helius.calls[before:]

    listing = [params for method, params in asked if method == "getTransactionsForAddress"]
    assert len(listing) == 1
    assert listing[0][1]["filters"]["slot"] == {"gte": HEAD - MARGIN}
    same_history(second.history, live(rig))
    assert second.report.mode == "update" and second.report.new == 1
    assert [p.kind for p in second.report.passes] == ["incremental"]
    assert second.report.verified == "ok" and len(second.history.transactions) == 251
    assert first.history.complete


def test_what_is_read_again_in_the_overlap_is_recognised_not_stored_twice(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)  # all three transactions are within the margin of the head
    sync(rig, committing_db, clock)

    clock.pass_seconds(600)
    result = sync(rig, committing_db, clock)

    assert result.report.new == 0 and result.report.unchanged == 3
    assert count(committing_db, StoredTransaction) == 3


def test_the_whole_stored_history_is_checked_again_after_an_update(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    sync(rig, committing_db, clock)

    rig.helius.skew_sol = 7  # the chain now says the wallet holds lamports the history lacks
    clock.pass_seconds(600)
    wrong = sync(rig, committing_db, clock)

    assert wrong.report.verified == "mismatch" and not wrong.history.complete
    assert any(note.startswith("MISMATCH") for note in wrong.history.coverage)

    rig.helius.skew_sol = 0
    clock.pass_seconds(600)
    right = sync(rig, committing_db, clock)

    assert right.report.verified == "ok" and right.history.complete
    assert not any(note.startswith("MISMATCH") for note in right.history.coverage)


def test_a_mismatch_on_the_first_read_is_remembered_with_the_stored_history(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    rig.helius.skew_tokens = {MINT: 1}

    first = sync(rig, committing_db, clock)
    clock.pass_seconds(10)
    again = sync(rig, committing_db, clock)

    assert not first.history.complete and first.report.verified == "mismatch"
    assert again.report.mode == "cached" and not again.history.complete
    assert again.history.coverage == first.history.coverage


def test_balances_that_cannot_be_read_leave_the_history_unchecked_not_wrong(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    rig.helius.broken = {"getBalance"}

    result = sync(rig, committing_db, clock)

    assert result.history.complete and result.report.verified is None
    assert any("could not be read" in note for note in result.history.coverage)


def test_a_transaction_that_lands_in_the_overlap_late_is_still_found(committing_db):
    # The provider's index lags the finalized head: a payment in slot HEAD - 50 is not listed yet
    # when the wallet is first read, and appears afterwards. A margin of zero would never see it.
    rig, clock = Rig(), Clock()
    flow(rig)
    late = transfer_item(9, address(4000), W, 31_337, slot=HEAD - 50)
    rig.helius.unlisted.add(signature(9))
    rig.helius.add(late)
    first = sync(rig, committing_db, clock)
    assert signature(9) not in {t.signature for t in first.history.transactions}

    rig.helius.unlisted.clear()  # the index has caught up
    clock.pass_seconds(600)
    second = sync(rig, committing_db, clock)

    assert signature(9) in {t.signature for t in second.history.transactions}
    assert second.report.new == 1


# --- finalized data is not second-guessed ------------------------------------------------------


def test_a_transaction_the_provider_stops_listing_is_kept_because_finalized_means_final(
    committing_db,
):
    rig, clock = Rig(), Clock()
    flow(rig)
    sync(rig, committing_db, clock)
    rig.helius.unlisted.add(signature(3))

    clock.pass_seconds(600)
    result = sync(rig, committing_db, clock)

    assert result.report.removed == 0 and len(result.history.transactions) == 3


# --- limits and failures -----------------------------------------------------------------------


def test_a_read_stopped_by_a_limit_is_finished_by_the_next_without_reading_it_twice(committing_db):
    rig, clock = Rig(), Clock()
    incoming(rig, 250)
    first = sync(rig, committing_db, clock, max_items=120)
    assert not first.history.complete and first.history.resume["transactions"]
    assert first.report.verified is None  # a partial history is not checked
    first_listings = listings(rig)

    clock.pass_seconds(600)
    second = sync(rig, committing_db, clock)
    second_listings = listings(rig) - first_listings

    assert second.history.complete and second.report.verified == "ok"
    assert [p.kind for p in second.report.passes] == ["incremental", "resume"]
    same_history(second.history, live(rig))
    # One page for the newest part, then only the page the first read had not reached.
    assert second_listings == 1 + (3 - 2)
    assert first.report.new + second.report.new == 250


def test_a_partial_history_is_served_as_it_is_while_it_is_fresh(committing_db):
    rig, clock = Rig(), Clock()
    incoming(rig, 250)
    first = sync(rig, committing_db, clock, max_items=120)
    asked = len(rig.helius.requests)

    clock.pass_seconds(30)
    again = sync(rig, committing_db, clock, max_items=120)

    assert len(rig.helius.requests) == asked and again.report.mode == "cached"
    assert not again.history.complete and again.history.resume == first.history.resume


def test_a_provider_failure_halfway_loses_nothing_that_was_read(committing_db):
    rig, clock = Rig(), Clock()
    incoming(rig, 250)
    adapter = rig.solana_adapter()
    real = adapter.get_address_page
    calls = []

    async def fails_on_the_second_page(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ProviderUnavailable("helius", "HTTP 503")
        return await real(*args, **kwargs)

    adapter.get_address_page = fails_on_the_second_page

    with pytest.raises(ProviderUnavailable):
        sync(rig, committing_db, clock, adapter=adapter)

    assert count(committing_db, StoredTransaction) == 100  # the first page was kept
    with committing_db.read() as session:
        (logged,) = recent_runs(session, CHAIN_ID, W)
        row = load_coverage(session, CHAIN_ID, W)["transactions"]
    assert logged.outcome == "failed" and "HTTP 503" in logged.error
    assert row.status == "partial" and row.resume_cursor

    retried = sync(rig, committing_db, clock)  # at once: the half-read row looks stale
    assert retried.history.complete and retried.report.verified == "ok"
    same_history(retried.history, live(rig))
    assert retried.report.new == 150  # only what the failed read had not stored


def test_running_out_of_request_budget_is_a_polite_stop_that_the_next_sync_finishes(
    committing_db,
):
    rig, clock = Rig(), Clock()
    incoming(rig, 250)
    starved = sync(rig, committing_db, clock, budget_limit=2)  # the slot, one page
    assert not starved.history.complete

    clock.pass_seconds(600)
    finished = sync(rig, committing_db, clock)

    assert finished.history.complete
    same_history(finished.history, live(rig))


def test_a_rebuild_reads_everything_again_and_changes_nothing_that_was_right(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    sync(rig, committing_db, clock)

    clock.pass_seconds(5)
    result = sync(rig, committing_db, clock, rebuild=True)

    assert result.report.mode == "rebuild" and result.report.new == 0
    assert result.report.unchanged == 3 and result.report.verified == "ok"
    same_history(result.history, live(rig))


# --- the raw answers ---------------------------------------------------------------------------


def test_the_answer_for_each_transaction_is_kept_so_it_can_be_decoded_again(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    raw = RawCollector()

    result = sync(rig, committing_db, clock, raw=raw)

    assert len(raw) == 0  # handed over to storage
    with committing_db.read() as session:
        for stored in result.history.transactions:
            (kept,) = load_raw(session, CHAIN_ID, "solana_transaction", stored.signature)
            again = solana_transaction(unpack(kept), SOLANA_CHAIN, "helius")
            assert again == stored  # decoding the kept answer gives exactly the stored record


def test_a_sync_that_reads_nothing_keeps_no_new_answers(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    raw = RawCollector()
    sync(rig, committing_db, clock, raw=raw)

    clock.pass_seconds(10)
    sync(rig, committing_db, clock, raw=raw)

    assert len(raw) == 0


# --- what an update may and may not leave behind -----------------------------------------------


def test_an_update_that_a_limit_cut_short_moves_nothing_and_leaves_no_gap(committing_db):
    rig, clock = Rig(), Clock()
    incoming(rig, 10)
    sync(rig, committing_db, clock)
    with committing_db.read() as session:
        before = load_coverage(session, CHAIN_ID, W)
    rig.helius.head += 1_000
    rig.helius.add(
        *[
            transfer_item(300 + n, address(5000 + n), W, 1_000 + n, slot=HEAD + 1 + n)
            for n in range(250)
        ]
    )

    clock.pass_seconds(600)
    cut = sync(rig, committing_db, clock, max_items=120)
    with committing_db.read() as session:
        assert load_coverage(session, CHAIN_ID, W) == before  # the frontier stayed put
    assert not cut.history.complete and len(cut.history.transactions) == 210
    assert any(note.startswith("stopped after") for note in cut.history.coverage)
    assert cut.history.synced_through_block is None

    clock.pass_seconds(600)
    finished = sync(rig, committing_db, clock)

    assert finished.history.complete and len(finished.history.transactions) == 260
    same_history(finished.history, live(rig))


def test_a_partial_history_is_the_same_as_a_fresh_partial_read(committing_db):
    rig, clock = Rig(), Clock()
    incoming(rig, 250)

    partial = sync(rig, committing_db, clock, max_items=120)
    fresh = run(fetch_solana_history(rig.solana_adapter(), W, max_items=120, verify=False))

    assert not partial.history.complete
    same_history(partial.history, fresh)


def test_an_update_makes_the_history_fresh_again(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    sync(rig, committing_db, clock)

    clock.pass_seconds(600)
    sync(rig, committing_db, clock)
    asked = len(rig.helius.requests)
    clock.pass_seconds(60)
    again = sync(rig, committing_db, clock)

    assert again.report.mode == "cached" and len(rig.helius.requests) == asked


# --- when what was saved cannot be used ---------------------------------------------------------

UNUSABLE = "a saved resume point could not be used; those streams were read again from their start"


def test_a_resume_point_no_configured_source_issued_is_read_again_from_the_start(committing_db):
    from dataclasses import replace

    from app.database.repositories.acquisition import save_coverage

    rig, clock = Rig(), Clock()
    incoming(rig, 250)
    sync(rig, committing_db, clock, max_items=120)
    with committing_db.write() as session:
        row = load_coverage(session, CHAIN_ID, W)["transactions"]
        save_coverage(session, CHAIN_ID, W, [replace(row, resume_cursor="gone|1:0")])
        session.commit()

    clock.pass_seconds(600)
    result = sync(rig, committing_db, clock)

    assert result.history.complete and UNUSABLE in result.history.coverage
    assert result.history.transactions == live(rig).transactions
