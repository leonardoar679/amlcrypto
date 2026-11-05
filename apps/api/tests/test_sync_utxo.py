"""Screening a Bitcoin address again: what is reused, what is read, and the check against the node.

A stored history is never trusted on the strength of an earlier check: whenever it is complete after
an update, the whole of it (the old part and the new part together) is compared with the node's own
figures for the address.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.chains.bitcoin.chains import BITCOIN_CHAIN
from app.chains.bitcoin.models import SpendState
from app.chains.bitcoin.normalize import esplora_transaction
from app.database.models import AddressTransaction, StoredTransaction
from app.database.repositories.acquisition import load_coverage, load_raw, recent_runs
from app.ingestion.raw import RawCollector, RawPolicy, unpack
from app.ingestion.sync import Sessions, SyncPolicy, sync_history
from app.ingestion.utxo import fetch_utxo_history
from app.providers.base import ProviderUnavailable
from app.providers.budget import RequestBudget
from tests.fake_esplora import HEAD, p2pkh, pay, spend, tx, txid
from tests.helpers import run
from tests.rigs import Rig

ME, A, B, C = (p2pkh(n) for n in (1, 2, 3, 4))
CHAIN_ID = BITCOIN_CHAIN.chain_id
MARGIN = BITCOIN_CHAIN.reorg_margin


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def pass_seconds(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def flow(rig: Rig) -> None:
    """ME is paid, spends part of it (getting change), and is paid again."""
    rig.esplora.add(
        tx(1, [spend(txid(900), 0, A, 60_000)], [pay(ME, 50_000), pay(A, 9_000)], height=101),
        tx(2, [spend(txid(1), 0, ME, 50_000)], [pay(B, 30_000), pay(ME, 19_000)], height=102),
        tx(3, [spend(txid(901), 0, C, 8_000)], [pay(ME, 7_000)], height=103),
    )


def incoming(rig: Rig, count: int) -> None:
    rig.esplora.add(
        *[
            tx(
                n,
                [spend(txid(5000 + n), 0, p2pkh(9000 + n), 10_000)],
                [pay(ME, 9_000)],
                height=100 + n,
            )
            for n in range(1, count + 1)
        ]
    )


def arrive(rig: Rig, n: int, *inputs, blocks: int = 20) -> None:
    """A new confirmed transaction after the chain has moved on."""
    rig.esplora.head += blocks
    rig.esplora.add(tx(n, list(inputs), [pay(ME, 4_000)], height=rig.esplora.head - 3))


def sync(rig, db, clock, *, raw=None, budget_limit=1000, adapter=None, **policy):
    budget = RequestBudget(budget_limit)
    return run(
        sync_history(
            adapter or rig.bitcoin_adapter(budget=budget, raw=raw),
            ME,
            sessions=Sessions(db.read, db.write),
            policy=SyncPolicy(**policy),
            budget=budget,
            raw=raw,
            clock=clock,
        )
    )


def live(rig):
    return run(fetch_utxo_history(rig.bitcoin_adapter(), ME))


def same_history(stored, fresh):
    assert stored.transactions == fresh.transactions
    assert stored.activity == fresh.activity
    assert (stored.complete, stored.head_block, stored.synced_through_block) == (
        fresh.complete,
        fresh.head_block,
        fresh.synced_through_block,
    )
    assert stored.coverage == fresh.coverage and stored.resume == fresh.resume
    assert stored.provider_summary == fresh.provider_summary


def pages(rig) -> int:
    return sum(1 for path, _ in rig.esplora.calls if "/txs/chain" in path)


def count(db, model):
    with db.read() as session:
        return session.scalar(select(func.count()).select_from(model))


# --- the first screening and the acceptance test -----------------------------------------------


def test_the_first_screening_reads_checks_and_keeps_the_whole_history(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)

    result = sync(rig, committing_db, clock)

    same_history(result.history, live(rig))
    assert result.history.complete and result.history.provider_summary is not None
    assert (result.report.mode, result.report.verified) == ("full", "ok")
    assert count(committing_db, StoredTransaction) == 3
    assert count(committing_db, AddressTransaction) == 3
    with committing_db.read() as session:
        row = load_coverage(session, CHAIN_ID, ME)["transactions"]
        (logged,) = recent_runs(session, CHAIN_ID, ME)
    assert (row.status, row.verified, row.synced_through_block) == ("complete", "ok", HEAD)
    assert row.details["figures"]["tx_count"] == 3
    assert (logged.mode, logged.outcome) == ("full", "complete")
    # the log counts the check of the merged history too, like the report does
    assert logged.requests_used == result.report.requests_used > 0


def test_screening_the_same_address_again_asks_the_node_nothing(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    first = sync(rig, committing_db, clock)
    asked = len(rig.esplora.requests)

    clock.pass_seconds(60)
    second = sync(rig, committing_db, clock)

    assert len(rig.esplora.requests) == asked
    assert second.report.mode == "cached" and second.report.requests_used == 0
    assert second.report.verified == "ok" and second.report.age_seconds == 60
    same_history(second.history, first.history)
    assert second.history.provider_summary == first.history.provider_summary


def test_an_address_the_node_has_never_seen_is_a_complete_empty_history(committing_db):
    rig, clock = Rig(), Clock()

    first = sync(rig, committing_db, clock)
    second = sync(rig, committing_db, clock)

    assert first.history.complete and not first.history.transactions
    assert second.report.mode == "cached" and second.history.complete


# --- bringing it up to date --------------------------------------------------------------------


def test_an_update_reads_one_page_where_the_first_read_needed_three(committing_db):
    rig, clock = Rig(), Clock()
    incoming(rig, 60)
    first = sync(rig, committing_db, clock)
    assert pages(rig) == 3
    arrive(rig, 61, spend(txid(7000), 0, p2pkh(77), 5_000))
    before = pages(rig)

    clock.pass_seconds(600)
    second = sync(rig, committing_db, clock)

    assert pages(rig) - before == 1  # the newest 25: the new one, then older ones that are stored
    same_history(second.history, live(rig))
    assert second.report.mode == "update" and second.report.new == 1
    assert [p.kind for p in second.report.passes] == ["incremental"]
    assert second.report.verified == "ok" and len(second.history.transactions) == 61
    assert first.history.complete


def test_what_is_read_again_in_the_overlap_is_recognised_not_stored_twice(committing_db):
    rig, clock = Rig(), Clock()
    rig.esplora.add(
        tx(1, [spend(txid(900), 0, A, 60_000)], [pay(ME, 50_000)], height=HEAD - 5)  # in the margin
    )
    sync(rig, committing_db, clock)

    clock.pass_seconds(600)
    result = sync(rig, committing_db, clock)

    assert result.report.new == 0 and result.report.unchanged == 1
    assert count(committing_db, StoredTransaction) == 1


def test_a_spend_of_a_stored_output_is_linked_when_it_arrives(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    first = sync(rig, committing_db, clock)
    (stored,) = [t for t in first.history.transactions if t.txid == txid(3)]
    assert stored.outputs[0].spend_state is SpendState.UNSPENT  # the last output ME holds

    rig.esplora.head += 20
    rig.esplora.add(
        tx(4, [spend(txid(3), 0, ME, 7_000)], [pay(B, 6_000)], height=rig.esplora.head - 3)
    )
    clock.pass_seconds(600)
    second = sync(rig, committing_db, clock)

    (now,) = [t for t in second.history.transactions if t.txid == txid(3)]
    assert now.outputs[0].spend_state is SpendState.SPENT and now.outputs[0].spent_by_txid == txid(
        4
    )
    same_history(second.history, live(rig))


def test_the_whole_stored_history_is_checked_again_after_an_update(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    sync(rig, committing_db, clock)

    rig.esplora.skew = {"tx_count": 1}  # the node now counts a transaction it does not list
    clock.pass_seconds(600)
    wrong = sync(rig, committing_db, clock)

    assert wrong.report.verified == "mismatch" and not wrong.history.complete
    assert any(note.startswith("MISMATCH") for note in wrong.history.coverage)

    rig.esplora.skew = {}
    clock.pass_seconds(600)
    right = sync(rig, committing_db, clock)

    assert right.report.verified == "ok" and right.history.complete
    assert not any(note.startswith("MISMATCH") for note in right.history.coverage)


def test_a_mismatch_on_the_first_read_is_remembered_with_the_stored_history(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    rig.esplora.skew = {"funded_txo_sum": 1}

    first = sync(rig, committing_db, clock)
    clock.pass_seconds(10)
    again = sync(rig, committing_db, clock)

    assert not first.history.complete and first.report.verified == "mismatch"
    assert again.report.mode == "cached" and not again.history.complete
    assert again.history.coverage == first.history.coverage


def test_a_node_whose_figures_cannot_be_read_leaves_the_history_unchecked_not_wrong(
    committing_db,
):
    rig, clock = Rig(), Clock()
    flow(rig)
    rig.esplora.broken_summary = True

    result = sync(rig, committing_db, clock)

    assert result.history.complete and result.report.verified is None
    assert any("could not be read" in note for note in result.history.coverage)


# --- reorganisations ---------------------------------------------------------------------------


def test_a_transaction_a_reorganisation_dropped_is_removed(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    doomed = tx(9, [spend(txid(902), 0, C, 8_000)], [pay(ME, 7_000)], height=HEAD - 5)
    rig.esplora.add(doomed)
    first = sync(rig, committing_db, clock)
    assert txid(9) in {t.txid for t in first.history.transactions}

    del rig.esplora.txs[txid(9)]
    rig.esplora._order.remove(txid(9))
    rig.esplora.head += 3
    clock.pass_seconds(600)
    second = sync(rig, committing_db, clock)

    assert second.report.removed == 1
    assert txid(9) not in {t.txid for t in second.history.transactions}
    same_history(second.history, live(rig))
    assert count(committing_db, StoredTransaction) == 3


def test_transactions_older_than_the_overlap_are_never_judged(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    sync(rig, committing_db, clock)
    rig.esplora.head += 3
    rig.esplora.unlisted.update(txid(n) for n in (1, 2, 3))  # a node that forgets old history

    clock.pass_seconds(600)
    result = sync(rig, committing_db, clock)

    assert result.report.removed == 0 and len(result.history.transactions) == 3


# --- limits and failures -----------------------------------------------------------------------


def test_a_read_stopped_by_a_limit_is_finished_by_the_next_without_reading_it_twice(committing_db):
    rig, clock = Rig(), Clock()
    incoming(rig, 60)
    first = sync(rig, committing_db, clock, max_items=30)
    assert not first.history.complete and first.history.resume["transactions"]
    assert first.report.verified is None  # a partial history is not checked
    first_pages = pages(rig)

    clock.pass_seconds(600)
    second = sync(rig, committing_db, clock)
    second_pages = pages(rig) - first_pages

    assert second.history.complete and second.report.verified == "ok"
    assert [p.kind for p in second.report.passes] == ["incremental", "resume"]
    same_history(second.history, live(rig))
    # One page for the newest part, then only the pages the first read had not reached.
    assert second_pages == 1 + (3 - 2)
    assert first.report.new + second.report.new == 60


def test_a_partial_history_is_served_as_it_is_while_it_is_fresh(committing_db):
    rig, clock = Rig(), Clock()
    incoming(rig, 60)
    first = sync(rig, committing_db, clock, max_items=30)
    asked = len(rig.esplora.requests)

    clock.pass_seconds(30)
    again = sync(rig, committing_db, clock, max_items=30)

    assert len(rig.esplora.requests) == asked and again.report.mode == "cached"
    assert not again.history.complete and again.history.resume == first.history.resume


def test_a_provider_failure_halfway_loses_nothing_that_was_read(committing_db):
    rig, clock = Rig(), Clock()
    incoming(rig, 60)
    adapter = rig.bitcoin_adapter()
    real = adapter.get_address_page
    calls = []

    async def fails_on_the_second_page(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ProviderUnavailable("esplora", "HTTP 503")
        return await real(*args, **kwargs)

    adapter.get_address_page = fails_on_the_second_page

    with pytest.raises(ProviderUnavailable):
        sync(rig, committing_db, clock, adapter=adapter)

    assert count(committing_db, StoredTransaction) == 25  # the first page was kept
    with committing_db.read() as session:
        (logged,) = recent_runs(session, CHAIN_ID, ME)
        row = load_coverage(session, CHAIN_ID, ME)["transactions"]
    assert logged.outcome == "failed" and "HTTP 503" in logged.error
    assert row.status == "partial" and row.resume_cursor

    retried = sync(rig, committing_db, clock)  # at once: the half-read row looks stale
    assert retried.history.complete and retried.report.verified == "ok"
    same_history(retried.history, live(rig))
    assert retried.report.new == 35  # only what the failed read had not stored


def test_running_out_of_request_budget_is_a_polite_stop_that_the_next_sync_finishes(
    committing_db,
):
    rig, clock = Rig(), Clock()
    incoming(rig, 60)
    starved = sync(rig, committing_db, clock, budget_limit=3)  # the tip, the figures, one page
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
        for item in result.history.transactions:
            (kept,) = load_raw(session, CHAIN_ID, "bitcoin_transaction", item.txid)
            body = unpack(kept)
            assert body == rig.esplora.txs[item.txid]  # what the node said, nothing else
            again = esplora_transaction(body, BITCOIN_CHAIN, "esplora")
            assert (again.inputs, again.fee_raw) == (item.inputs, item.fee_raw)
            assert [o.value_raw for o in again.outputs] == [o.value_raw for o in item.outputs]


def test_an_answer_over_the_size_limit_is_not_kept_but_the_decoded_record_is(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    raw = RawCollector(RawPolicy(max_bytes=10))

    result = sync(rig, committing_db, clock, raw=raw)

    assert raw.too_large == 3 and len(result.history.transactions) == 3
    with committing_db.read() as session:
        assert load_raw(session, CHAIN_ID, "bitcoin_transaction", txid(1)) == []


def test_a_sync_that_reads_nothing_keeps_no_new_answers(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    raw = RawCollector()
    sync(rig, committing_db, clock, raw=raw)

    clock.pass_seconds(10)
    sync(rig, committing_db, clock, raw=raw)

    assert len(raw) == 0


def test_the_stored_coverage_names_the_provider_figures_it_was_checked_with(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    sync(rig, committing_db, clock)

    with committing_db.read() as session:
        row = load_coverage(session, CHAIN_ID, ME)["transactions"]
    figures = row.details["figures"]
    assert figures["address"] == ME and figures["balance_raw"] == str(
        50_000 - 50_000 + 19_000 + 7_000
    )
    assert replace(row, details=None).details is None


# --- what a check for reorganisations may and may not touch -------------------------------------


def busy_window(rig: Rig, count: int = 60) -> None:
    """Transactions in the last blocks before the head, inside the stretch an update reads."""
    rig.esplora.add(
        *[
            tx(
                n,
                [spend(txid(6000 + n), 0, p2pkh(9000 + n), 10_000)],
                [pay(ME, 9_000)],
                height=HEAD - 10 + n % 10,
            )
            for n in range(1, count + 1)
        ]
    )


def test_an_update_that_a_limit_cut_short_removes_nothing_and_moves_nothing(committing_db):
    rig, clock = Rig(), Clock()
    busy_window(rig)
    sync(rig, committing_db, clock)
    with committing_db.read() as session:
        before = load_coverage(session, CHAIN_ID, ME)
    rig.esplora.head += 2

    clock.pass_seconds(600)
    cut = sync(rig, committing_db, clock, max_items=30)

    assert cut.report.removed == 0 and count(committing_db, StoredTransaction) == 60
    with committing_db.read() as session:
        assert load_coverage(session, CHAIN_ID, ME) == before  # the frontier stayed put

    finished = sync(rig, committing_db, clock)
    assert [p.kind for p in finished.report.passes] == ["incremental"]
    same_history(finished.history, live(rig))


def test_an_update_that_a_limit_cut_short_leaves_no_gap_when_more_arrives(committing_db):
    rig, clock = Rig(), Clock()
    incoming(rig, 10)
    sync(rig, committing_db, clock)
    rig.esplora.head += 30
    rig.esplora.add(
        *[
            tx(
                200 + n,
                [spend(txid(7000 + n), 0, p2pkh(9500 + n), 10_000)],
                [pay(ME, 9_000)],
                height=rig.esplora.head - 20 + n // 4,
            )
            for n in range(60)
        ]
    )

    clock.pass_seconds(600)
    cut = sync(rig, committing_db, clock, max_items=30)
    assert not cut.history.complete and len(cut.history.transactions) == 60
    assert any(note.startswith("stopped after") for note in cut.history.coverage)
    assert cut.history.synced_through_block is None

    clock.pass_seconds(600)
    finished = sync(rig, committing_db, clock)

    assert finished.history.complete and len(finished.history.transactions) == 70
    same_history(finished.history, live(rig))


def test_a_rebuild_drops_what_the_chain_no_longer_shows_whenever_it_happened(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    sync(rig, committing_db, clock)
    del rig.esplora.txs[txid(1)]
    rig.esplora._order.remove(txid(1))

    clock.pass_seconds(5)
    result = sync(rig, committing_db, clock, rebuild=True)

    assert result.report.removed == 1 and len(result.history.transactions) == 2
    same_history(result.history, live(rig))


def test_a_partial_history_claims_no_output_is_unspent_just_as_a_fresh_partial_read_does(
    committing_db,
):
    rig, clock = Rig(), Clock()
    incoming(rig, 60)

    partial = sync(rig, committing_db, clock, max_items=30)
    fresh = run(fetch_utxo_history(rig.bitcoin_adapter(), ME, max_items=30, verify=False))

    assert not partial.history.complete
    same_history(partial.history, fresh)
    assert all(
        output.spend_state is not SpendState.UNSPENT
        for item in partial.history.transactions
        for output in item.outputs
    )


def test_an_update_makes_the_history_fresh_again(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    sync(rig, committing_db, clock)

    clock.pass_seconds(600)
    sync(rig, committing_db, clock)
    asked = len(rig.esplora.requests)
    clock.pass_seconds(60)
    again = sync(rig, committing_db, clock)

    assert again.report.mode == "cached" and len(rig.esplora.requests) == asked


# --- when what was saved cannot be used ---------------------------------------------------------

UNUSABLE = "a saved resume point could not be used; those streams were read again from their start"


def test_a_resume_point_no_configured_source_issued_is_read_again_from_the_start(committing_db):
    from app.database.repositories.acquisition import save_coverage

    rig, clock = Rig(), Clock()
    incoming(rig, 60)
    sync(rig, committing_db, clock, max_items=30)
    with committing_db.write() as session:
        row = load_coverage(session, CHAIN_ID, ME)["transactions"]
        save_coverage(session, CHAIN_ID, ME, [replace(row, resume_cursor="gone|abc")])
        session.commit()

    clock.pass_seconds(600)
    result = sync(rig, committing_db, clock)

    assert result.history.complete and UNUSABLE in result.history.coverage
    assert result.history.transactions == live(rig).transactions


def test_answers_taken_for_a_write_that_fails_are_put_back_for_the_next_attempt(
    committing_db, monkeypatch
):
    from app.ingestion import sync as sync_module

    rig, clock = Rig(), Clock()
    flow(rig)
    raw = RawCollector()
    real = sync_module.family_for

    def disk_full(adapter):
        family = real(adapter)

        def save(*args, **kwargs):
            raise RuntimeError("disk full")

        family.save = save
        return family

    monkeypatch.setattr(sync_module, "family_for", disk_full)
    with pytest.raises(RuntimeError, match="disk full"):
        sync(rig, committing_db, clock, raw=raw)

    assert len(raw) == 3  # nothing was lost: the next attempt has them
    monkeypatch.undo()
    result = sync(rig, committing_db, clock, raw=raw)
    with committing_db.read() as session:
        assert load_raw(session, CHAIN_ID, "bitcoin_transaction", txid(1)) != []
    assert result.history.complete


# --- several reads in one sync, and the log of what they did ------------------------------------


def test_a_pass_that_used_up_the_budget_leaves_the_next_one_for_the_next_sync(committing_db):
    rig, clock = Rig(), Clock()
    incoming(rig, 60)
    sync(rig, committing_db, clock, max_items=30)  # the newest 50 are stored

    clock.pass_seconds(600)
    starved = sync(rig, committing_db, clock, budget_limit=2)  # the tip and one page

    assert [p.kind for p in starved.report.passes] == ["incremental"]
    assert starved.report.requests_used == 2 and not starved.history.complete
    clock.pass_seconds(600)
    finished = sync(rig, committing_db, clock)
    assert finished.history.complete
    same_history(finished.history, live(rig))


def test_after_a_failure_the_passes_still_to_make_wait_for_the_next_sync(committing_db):
    rig, clock = Rig(), Clock()
    incoming(rig, 60)
    sync(rig, committing_db, clock, max_items=30)
    assert count(committing_db, StoredTransaction) == 50
    adapter = rig.bitcoin_adapter()
    real = adapter.get_address_page
    calls = []

    async def first_one_fails(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise ProviderUnavailable("esplora", "HTTP 503")
        return await real(*args, **kwargs)

    adapter.get_address_page = first_one_fails

    clock.pass_seconds(600)
    with pytest.raises(ProviderUnavailable):
        sync(rig, committing_db, clock, adapter=adapter)

    assert len(calls) == 1  # the older part was not read after the update failed
    assert count(committing_db, StoredTransaction) == 50


def test_each_run_is_logged_with_where_it_began_what_it_asked_and_how_it_ended(committing_db):
    rig, clock = Rig(), Clock()
    incoming(rig, 60)
    sync(rig, committing_db, clock, max_items=30)
    clock.pass_seconds(600)
    sync(rig, committing_db, clock)

    with committing_db.read() as session:
        newest, oldest = recent_runs(session, CHAIN_ID, ME)

    assert (oldest.mode, oldest.outcome, oldest.from_block) == ("full", "partial", None)
    assert oldest.sources == "esplora" and oldest.error is None
    assert (newest.mode, newest.outcome) == ("update", "complete")
    assert newest.from_block == BITCOIN_CHAIN.incremental_start(HEAD)
    assert newest.finished_at >= newest.started_at


def test_a_read_that_adds_a_proven_spend_to_a_stored_transaction_keeps_it(committing_db):
    rig, clock = Rig(), Clock()
    flow(rig)
    sync(rig, committing_db, clock)
    (stored,) = [t for t in live(rig).transactions if t.txid == txid(3)]
    spent = replace(
        stored.outputs[0],
        spend_state=SpendState.SPENT,
        spent_by_txid=txid(77),
        spent_by_input_index=1,
    )
    from app.database.repositories.bitcoin import save_bitcoin_transactions

    with committing_db.write() as session:
        save_bitcoin_transactions(session, [replace(stored, outputs=(spent,))])
        session.commit()
        save_bitcoin_transactions(session, [stored])  # a later read that knows nothing of it
        session.commit()

    with committing_db.read() as session:
        from app.database.repositories.bitcoin import load_bitcoin_transaction

        kept = load_bitcoin_transaction(session, CHAIN_ID, txid(3))
    assert kept.outputs[0].spend_state is SpendState.SPENT
    assert kept.outputs[0].spent_by_txid == txid(77)
