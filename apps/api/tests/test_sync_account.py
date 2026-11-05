"""Screening an account-chain address again: what is reused, what is read, what is dropped.

The acceptance test of milestone 8 is the second one here: a repeated screening reuses the stored
history and asks the providers nothing.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.chains.models import Confirmation
from app.database.models import DataCoverage, FetchRun, StoredTransaction, StoredTransfer
from app.database.repositories.acquisition import load_coverage, recent_runs
from app.ingestion.fetcher import fetch_history
from app.ingestion.sync import Sessions, SyncPolicy, sync_history
from app.providers.base import ProviderUnavailable
from app.providers.budget import RequestBudget
from tests.fake_etherscan import ME, OTHER, internal_row, token_row, tx_row
from tests.helpers import run
from tests.rigs import BSC, ETHEREUM, Rig

HEAD = 24_000_000
MARGIN = ETHEREUM.reorg_margin


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def pass_seconds(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def seeded() -> Rig:
    rig = Rig()
    parent = tx_row(10, ME, OTHER, value="5", transactionIndex="2")
    rig.etherscan.add("txlist", parent, tx_row(12, OTHER, ME, value="7"), tx_row(13, ME, OTHER))
    rig.etherscan.add("txlistinternal", internal_row(10, OTHER, ME, value="3", hash=parent["hash"]))
    rig.etherscan.add(
        "tokentx",
        token_row(11, OTHER, ME, value="9"),
        token_row(10, ME, OTHER, value="1", transactionIndex="5"),
    )
    return rig


def sync(rig, db, clock, address=ME, chain=ETHEREUM, *, raw=None, budget_limit=1000, **policy):
    budget = RequestBudget(budget_limit)
    return run(
        sync_history(
            rig.adapter(chain, budget=budget),
            address,
            sessions=Sessions(db.read, db.write),
            policy=SyncPolicy(**policy),
            budget=budget,
            raw=raw,
            clock=clock,
        )
    )


def settled(item):
    """Confirmation depth is a snapshot of when a record was read, not of the record."""
    return replace(item, confirmations=None, confirmation_status=Confirmation.UNKNOWN)


def same_history(stored, live):
    assert [settled(t) for t in stored.transfers] == [settled(t) for t in live.transfers]
    assert [settled(t) for t in stored.transactions] == [settled(t) for t in live.transactions]
    assert (stored.complete, stored.head_block, stored.synced_through_block) == (
        live.complete,
        live.head_block,
        live.synced_through_block,
    )
    assert stored.coverage == live.coverage and stored.resume == live.resume
    assert stored.unavailable == live.unavailable and stored.streams == live.streams


def count(db, model):
    with db.read() as session:
        return session.scalar(select(func.count()).select_from(model))


def requests_of(rig) -> int:
    return len(rig.etherscan.requests) + len(rig.alchemy.requests)


# --- the first screening ------------------------------------------------------------------------


def test_the_first_screening_reads_the_whole_history_and_keeps_it(committing_db):
    rig, clock = seeded(), Clock()

    result = sync(rig, committing_db, clock)

    same_history(result.history, run(fetch_history(rig.adapter(), ME)))
    report = result.report
    assert (report.mode, report.reason, report.age_seconds) == ("full", "never read", None)
    assert report.new == 8 and report.updated == 0 and report.requests_used > 0
    assert (
        count(committing_db, StoredTransfer) == 5 and count(committing_db, StoredTransaction) == 3
    )
    with committing_db.read() as session:
        rows = load_coverage(session, ETHEREUM.chain_id, ME)
        runs = recent_runs(session, ETHEREUM.chain_id, ME)
    assert {r.status for r in rows.values()} == {"complete"}
    assert {r.synced_through_block for r in rows.values()} == {HEAD}
    assert [(r.mode, r.outcome, r.requests_used) for r in runs] == [
        ("full", "complete", report.requests_used)
    ]


# --- the acceptance test -----------------------------------------------------------------------


def test_screening_the_same_address_again_asks_the_providers_nothing(committing_db):
    rig, clock = seeded(), Clock()
    first = sync(rig, committing_db, clock)
    asked = requests_of(rig)

    clock.pass_seconds(60)  # within the freshness window
    second = sync(rig, committing_db, clock)

    assert requests_of(rig) == asked  # not one request
    assert second.report.mode == "cached" and second.report.requests_used == 0
    assert second.report.age_seconds == 60 and second.report.reason.startswith("fresh")
    same_history(second.history, first.history)
    assert second.history.transfers == first.history.transfers
    assert count(committing_db, FetchRun) == 1  # nothing was read, so nothing is logged


def test_an_address_that_a_provider_never_mentions_is_remembered_as_empty(committing_db):
    rig, clock = Rig(), Clock()

    first = sync(rig, committing_db, clock)
    clock.pass_seconds(10)
    second = sync(rig, committing_db, clock)

    assert first.history.complete and not first.history.transfers
    assert second.report.mode == "cached" and second.history.complete


# --- bringing it up to date --------------------------------------------------------------------


def grow(rig, *, blocks=100, extra=None):
    rig.etherscan.head += blocks
    rig.etherscan.add(
        "txlist", tx_row(rig.etherscan.head - 5, OTHER, ME, value="11", **(extra or {}))
    )
    rig.etherscan.add("tokentx", token_row(rig.etherscan.head - 4, OTHER, ME, value="12"))


def test_an_older_history_reads_only_what_is_new_and_ends_up_as_if_read_whole(committing_db):
    rig, clock = seeded(), Clock()
    first = sync(rig, committing_db, clock)
    grow(rig)
    before = len(rig.etherscan.requests)

    clock.pass_seconds(600)  # past the freshness window
    second = sync(rig, committing_db, clock)
    asked = rig.etherscan.calls[before:]

    same_history(second.history, run(fetch_history(rig.adapter(), ME)))
    assert second.report.mode == "update" and second.report.reason.startswith("older than")
    assert second.report.new == 3  # the transaction, its coin and the token event
    # Only the stretch since a little before the last sync was asked for, never the old blocks.
    reads = [c for c in asked if c["action"] in ("txlist", "txlistinternal", "tokentx")]
    assert reads and {int(c["startblock"]) for c in reads} == {HEAD - MARGIN}
    assert second.report.requests_used < first.report.requests_used + 3
    assert [p.kind for p in second.report.passes] == ["incremental"]


def test_a_refresh_is_made_even_when_the_stored_history_is_fresh(committing_db):
    rig, clock = seeded(), Clock()
    sync(rig, committing_db, clock)
    grow(rig)

    clock.pass_seconds(5)
    result = sync(rig, committing_db, clock, refresh=True)

    assert result.report.mode == "update" and result.report.reason == "refresh requested"
    same_history(result.history, run(fetch_history(rig.adapter(), ME)))


def test_what_is_read_again_in_the_overlap_is_recognised_not_stored_twice(committing_db):
    rig, clock = seeded(), Clock()
    rig.etherscan.add("txlist", tx_row(HEAD - 20, OTHER, ME, value="4"))  # inside the margin
    sync(rig, committing_db, clock)

    clock.pass_seconds(600)
    result = sync(rig, committing_db, clock)

    assert result.report.new == 0 and result.report.unchanged >= 2
    assert count(committing_db, StoredTransfer) == 6


def test_a_transaction_a_reorganisation_dropped_is_removed(committing_db):
    rig, clock = seeded(), Clock()
    doomed = tx_row(HEAD - 20, OTHER, ME, value="4")
    rig.etherscan.add("txlist", doomed)
    first = sync(rig, committing_db, clock)
    assert any(t.transaction_hash == doomed["hash"] for t in first.history.transfers)

    rig.etherscan.rows["txlist"].remove(doomed)  # the chain no longer has it
    rig.etherscan.head += 10
    clock.pass_seconds(600)
    second = sync(rig, committing_db, clock)

    assert second.report.removed == 2  # its coin movement and the transaction itself
    assert all(t.transaction_hash != doomed["hash"] for t in second.history.transfers)
    same_history(second.history, run(fetch_history(rig.adapter(), ME)))


def test_records_older_than_the_overlap_are_never_judged(committing_db):
    rig, clock = seeded(), Clock()
    sync(rig, committing_db, clock)
    rig.etherscan.rows["txlist"].clear()  # a provider that suddenly forgets its old history
    rig.etherscan.head += 10

    clock.pass_seconds(600)
    result = sync(rig, committing_db, clock)

    assert result.report.removed == 0
    assert len([t for t in result.history.transfers if t.transfer_type.value == "native"]) == 2


# --- limits and failures -----------------------------------------------------------------------


def long_history() -> Rig:
    rig = Rig()
    rig.etherscan.add("txlist", *[tx_row(b, OTHER, ME, value=str(b)) for b in range(1, 251)])
    rig.etherscan.add("tokentx", *[token_row(b, OTHER, ME, value=str(b)) for b in range(1, 251)])
    return rig


def test_a_read_stopped_by_a_limit_is_finished_by_the_next_without_reading_it_twice(committing_db):
    rig, clock = long_history(), Clock()
    first = sync(rig, committing_db, clock, max_items=120)
    assert not first.history.complete and first.history.resume
    assert first.report.mode == "full" and first.report.passes[0].complete is False

    clock.pass_seconds(600)
    second = sync(rig, committing_db, clock)

    assert second.history.complete and second.report.reason.startswith("an earlier read")
    assert [p.kind for p in second.report.passes] == ["resume"]
    same_history(second.history, run(fetch_history(rig.adapter(), ME)))
    # The second read started where the first stopped: between them, every record was read once.
    everything = len(second.history.transfers) + len(second.history.transactions)
    assert first.report.new + second.report.new == everything and second.report.unchanged == 0


def test_a_partial_history_is_served_as_it_is_while_it_is_fresh(committing_db):
    rig, clock = long_history(), Clock()
    first = sync(rig, committing_db, clock, max_items=120)
    asked = requests_of(rig)

    clock.pass_seconds(30)
    again = sync(rig, committing_db, clock, max_items=120)

    assert requests_of(rig) == asked and again.report.mode == "cached"
    assert not again.history.complete and again.history.resume == first.history.resume
    assert again.history.transfers == first.history.transfers


def test_a_provider_failure_halfway_loses_nothing_that_was_read(committing_db):
    rig, clock = long_history(), Clock()
    adapter = rig.adapter()
    real = adapter.get_account_page
    calls = []

    async def fails_on_the_second_page(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ProviderUnavailable("etherscan", "HTTP 503")
        return await real(*args, **kwargs)

    adapter.get_account_page = fails_on_the_second_page

    with pytest.raises(ProviderUnavailable):
        run(
            sync_history(
                adapter,
                ME,
                sessions=Sessions(committing_db.read, committing_db.write),
                policy=SyncPolicy(),
                clock=clock,
            )
        )

    assert count(committing_db, StoredTransfer) > 0  # the first page was kept
    with committing_db.read() as session:
        (run_logged,) = recent_runs(session, ETHEREUM.chain_id, ME)
        rows = load_coverage(session, ETHEREUM.chain_id, ME)
    assert run_logged.outcome == "failed" and "HTTP 503" in run_logged.error
    assert rows["account"].status == "partial" and rows["account"].resume_cursor
    # The row looks stale, so an immediate retry carries on instead of serving the half it got.
    retried = sync(rig, committing_db, clock)
    assert retried.report.mode == "update" and retried.history.complete
    same_history(retried.history, run(fetch_history(rig.adapter(), ME)))


def test_running_out_of_request_budget_is_a_polite_stop_that_the_next_sync_finishes(
    committing_db,
):
    rig, clock = long_history(), Clock()
    starved = sync(rig, committing_db, clock, budget_limit=3)
    assert not starved.history.complete
    assert starved.report.requests_used == 3

    clock.pass_seconds(600)
    finished = sync(rig, committing_db, clock)

    assert finished.history.complete
    same_history(finished.history, run(fetch_history(rig.adapter(), ME)))


# --- rebuilding --------------------------------------------------------------------------------


def test_a_rebuild_reads_everything_again_and_changes_nothing_that_was_right(committing_db):
    rig, clock = seeded(), Clock()
    sync(rig, committing_db, clock)

    clock.pass_seconds(5)
    result = sync(rig, committing_db, clock, rebuild=True)

    assert result.report.mode == "rebuild" and result.report.reason == "rebuild requested"
    assert result.report.new == 0 and result.report.unchanged == 8
    assert count(committing_db, StoredTransfer) == 5
    same_history(result.history, run(fetch_history(rig.adapter(), ME)))


def test_a_rebuild_drops_what_the_chain_no_longer_shows_whenever_it_happened(committing_db):
    rig, clock = seeded(), Clock()
    ancient = tx_row(7, OTHER, ME, value="4")
    rig.etherscan.add("txlist", ancient)
    sync(rig, committing_db, clock)
    rig.etherscan.rows["txlist"].remove(ancient)

    clock.pass_seconds(5)
    result = sync(rig, committing_db, clock, rebuild=True)

    assert result.report.removed == 2
    same_history(result.history, run(fetch_history(rig.adapter(), ME)))


# --- more than one chain, more than one address ------------------------------------------------


def test_each_address_and_chain_has_its_own_coverage(committing_db):
    rig, clock = seeded(), Clock()
    sync(rig, committing_db, clock)
    sync(rig, committing_db, clock, address=OTHER)

    with committing_db.read() as session:
        assert len(load_coverage(session, ETHEREUM.chain_id, ME)) == 3
        assert len(load_coverage(session, ETHEREUM.chain_id, OTHER)) == 3
        assert load_coverage(session, BSC.chain_id, ME) == {}
    assert count(committing_db, DataCoverage) == 6


def test_an_address_the_other_side_of_a_stored_transfer_reuses_what_is_stored(committing_db):
    rig, clock = seeded(), Clock()
    sync(rig, committing_db, clock)

    other = sync(rig, committing_db, clock, address=OTHER)

    # OTHER's own screening reads the same transfers: they are already stored, once.
    assert other.report.new == 0 and other.report.unchanged == 8
    assert count(committing_db, StoredTransfer) == 5


# --- what a check for reorganisations may and may not touch -------------------------------------


def test_everything_a_reorganisation_dropped_goes_not_only_the_coin(committing_db):
    rig, clock = seeded(), Clock()
    doomed = tx_row(HEAD - 20, OTHER, ME, value="4")
    rig.etherscan.add(
        "txlistinternal", internal_row(HEAD - 20, OTHER, ME, value="3", hash=doomed["hash"])
    )
    rig.etherscan.add("txlist", doomed)
    rig.etherscan.add("tokentx", token_row(HEAD - 20, OTHER, ME, value="5", hash=doomed["hash"]))
    first = sync(rig, committing_db, clock)
    assert sum(t.transaction_hash == doomed["hash"] for t in first.history.transfers) == 3

    rig.etherscan.rows["txlist"].remove(doomed)
    rig.etherscan.rows["txlistinternal"].pop()
    rig.etherscan.rows["tokentx"].pop()
    rig.etherscan.head += 10
    clock.pass_seconds(600)
    second = sync(rig, committing_db, clock)

    assert second.report.removed == 4  # the coin, the internal transfer, the token event, the tx
    same_history(second.history, run(fetch_history(rig.adapter(), ME)))
    assert count(committing_db, StoredTransfer) == 5


def window_full_of_records(rig) -> None:
    """250 transactions in the last blocks before the head: more than two pages, all inside the
    stretch an update reads again."""
    rig.etherscan.add(
        "txlist",
        *[tx_row(HEAD - 60 + n // 5, OTHER, ME, value=str(n + 1)) for n in range(250)],
    )


def test_an_update_that_a_limit_cut_short_removes_nothing_and_moves_nothing(committing_db):
    rig, clock = Rig(), Clock()
    window_full_of_records(rig)
    sync(rig, committing_db, clock)
    with committing_db.read() as session:
        before = load_coverage(session, ETHEREUM.chain_id, ME)
    rig.etherscan.head += 10

    clock.pass_seconds(600)
    cut = sync(rig, committing_db, clock, max_items=120)

    assert not cut.history.complete and cut.history.synced_through_block is None
    assert cut.report.removed == 0 and count(committing_db, StoredTransfer) == 250
    with committing_db.read() as session:
        assert load_coverage(session, ETHEREUM.chain_id, ME) == before  # the frontier stayed put

    finished = sync(rig, committing_db, clock)
    assert [p.kind for p in finished.report.passes] == ["incremental"]
    same_history(finished.history, run(fetch_history(rig.adapter(), ME)))


def test_transactions_are_not_judged_by_an_update_that_did_not_read_them(committing_db):
    from app.database.repositories.acquisition import save_coverage

    rig, clock = seeded(), Clock()
    rig.etherscan.add("txlist", tx_row(HEAD - 20, OTHER, ME, value="4"))
    sync(rig, committing_db, clock)
    with committing_db.write() as session:  # the account stream was stopped part-way, say
        row = load_coverage(session, ETHEREUM.chain_id, ME)["account"]
        stopped = replace(
            row, status="partial", resume_cursor="etherscan|0:0", synced_through_block=None
        )
        save_coverage(session, ETHEREUM.chain_id, ME, [stopped])
        session.commit()

    clock.pass_seconds(600)
    result = sync(rig, committing_db, clock)

    # The internal and token streams were updated alone: they say nothing about transactions.
    assert [p.kind for p in result.report.passes] == ["resume", "incremental"]
    assert result.report.removed == 0 and count(committing_db, StoredTransaction) == 4
    same_history(result.history, run(fetch_history(rig.adapter(), ME)))


def test_tron_is_final_so_a_record_the_feed_stops_showing_is_never_dropped(committing_db):
    from tests.fake_trongrid import ALICE, BOB, T0, trx_row
    from tests.fake_trongrid import HEAD as TRON_HEAD

    rig, clock = Rig(), Clock()
    rows = [
        trx_row(T0 + n * 3000, ALICE, BOB, amount=n + 1, blockNumber=TRON_HEAD - 40 + n)
        for n in range(3)
    ]
    rig.trongrid.add("transactions", *rows)
    rig.trongrid.block_times[TRON_HEAD - 80] = T0  # the update starts at the first of them

    def tron_sync():
        budget = RequestBudget(100)
        return run(
            sync_history(
                rig.tron_adapter(budget=budget),
                ALICE,
                sessions=Sessions(committing_db.read, committing_db.write),
                policy=SyncPolicy(),
                budget=budget,
                clock=clock,
            )
        )

    first = tron_sync()
    assert len(first.history.transfers) == 3
    rig.trongrid.feeds["transactions"].remove(rows[1])
    clock.pass_seconds(600)
    second = tron_sync()

    assert second.report.removed == 0 and len(second.history.transfers) == 3
    assert [p.kind for p in second.report.passes] == ["incremental"]


def test_an_address_whose_internal_transfers_no_source_gives_is_stored_as_complete(
    committing_db,
):
    from tests.fake_alchemy import asset_row

    rig, clock = Rig(etherscan_key=None), Clock()
    rig.alchemy.add(asset_row("external", 5, ME, OTHER))

    first = sync(rig, committing_db, clock, chain=BSC)
    again = sync(rig, committing_db, clock, chain=BSC)

    assert first.history.complete and first.history.unavailable == ["internal"]
    assert again.report.mode == "cached" and again.history.complete
    assert again.history.unavailable == ["internal"]
    same_history(again.history, run(fetch_history(rig.adapter(BSC), ME)))
    assert "no configured source provides internal transfers on bsc" in again.history.coverage


# --- when what was saved cannot be used ---------------------------------------------------------

UNUSABLE = "a saved resume point could not be used; those streams were read again from their start"


def test_a_resume_point_no_configured_source_issued_is_read_again_from_the_start(committing_db):
    from app.database.repositories.acquisition import save_coverage

    rig, clock = long_history(), Clock()
    sync(rig, committing_db, clock, max_items=120)
    with committing_db.write() as session:
        row = load_coverage(session, ETHEREUM.chain_id, ME)["account"]
        save_coverage(session, ETHEREUM.chain_id, ME, [replace(row, resume_cursor="gone|1:0")])
        session.commit()

    clock.pass_seconds(600)
    result = sync(rig, committing_db, clock)

    live = run(fetch_history(rig.adapter(), ME))
    assert result.history.complete and UNUSABLE in result.history.coverage
    assert [settled(t) for t in result.history.transfers] == [settled(t) for t in live.transfers]


def test_a_tron_transaction_listed_for_an_internal_call_is_still_in_the_stored_history(
    committing_db,
):
    """Found by reading a busy TRON address for real: the provider lists a transaction for an
    address that only took part through an internal call, so the address is neither its sender nor
    its recipient. A stored history that worked membership out from those two would lose it."""
    from tests.fake_trongrid import ALICE, BOB, CAROL, T0, to_hex, trx_row

    rig, clock = Rig(), Clock()
    call = trx_row(T0 + 1000, CAROL, BOB)  # CAROL calls the contract BOB ...
    parameter = call["raw_data"]["contract"][0]
    parameter["type"] = "TriggerSmartContract"
    value = parameter["parameter"]["value"]
    value.pop("amount"), value.pop("to_address")
    value["contract_address"], value["data"] = to_hex(BOB), "a9059cbb" + "0" * 128
    call["_also"] = [ALICE]  # ... and ALICE is involved only through what the contract did
    rig.trongrid.add("transactions", call)
    budget = RequestBudget(100)

    stored = run(
        sync_history(
            rig.tron_adapter(budget=budget),
            ALICE,
            sessions=Sessions(committing_db.read, committing_db.write),
            policy=SyncPolicy(),
            budget=budget,
            clock=clock,
        )
    )
    cached = run(
        sync_history(
            rig.tron_adapter(),
            ALICE,
            sessions=Sessions(committing_db.read, committing_db.write),
            policy=SyncPolicy(),
            clock=clock,
        )
    )

    live = run(fetch_history(rig.tron_adapter(), ALICE))
    assert [t.transaction_hash for t in live.transactions] == [call["txID"]]  # the provider says so
    assert [t.transaction_hash for t in stored.history.transactions] == [call["txID"]]
    assert cached.report.mode == "cached"
    assert [t.transaction_hash for t in cached.history.transactions] == [call["txID"]]
