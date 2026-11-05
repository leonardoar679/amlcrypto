"""Coverage, the log of network reads, and the raw answers kept."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.database.repositories.acquisition import (
    COMPLETE,
    PARTIAL,
    Coverage,
    RawItem,
    RunRecord,
    forget_coverage,
    load_coverage,
    load_raw,
    recent_runs,
    record_run,
    save_coverage,
    save_raw,
)
from app.database.repositories.jobs import enqueue

ETH = "eip155:1"
ME = "0x" + "a1" * 20
OTHER = "0x" + "b2" * 20
T0 = datetime(2026, 11, 5, 12, 0, 0, 250_000, tzinfo=UTC)


def coverage(stream="account", **changes) -> Coverage:
    base = Coverage(
        stream=stream, status=COMPLETE, synced_at=T0, synced_through_block=900, head_block=910
    )
    return replace(base, **changes)


def put(db, *items, address=ME):
    with db.write() as session:
        save_coverage(session, ETH, address, items)
        session.commit()


def get(db, address=ME):
    with db.read() as session:
        return load_coverage(session, ETH, address)


# --- coverage ----------------------------------------------------------------------------------


def test_an_address_never_read_has_no_coverage(committing_db):
    assert get(committing_db) == {}


def test_every_field_of_a_coverage_row_comes_back_as_it_went_in(committing_db):
    full = Coverage(
        stream="transactions",
        status=PARTIAL,
        synced_at=T0,
        synced_through_block=123,
        resume_cursor="esplora|" + "ab" * 32,
        head_block=130,
        verified="ok",
        notes=("stopped after 500 transactions", "50 skipped"),
        details={"tx_count": 7, "tokens": {"program": {"slot": 5, "balances": {"M": "9"}}}},
    )
    bare = Coverage(stream="account", status=COMPLETE, synced_at=T0)
    put(committing_db, full, bare)

    assert get(committing_db) == {"transactions": full, "account": bare}


def test_saving_a_stream_again_replaces_it_and_leaves_the_others(committing_db):
    put(committing_db, coverage("account"), coverage("internal"))

    later = T0 + timedelta(minutes=5)
    put(
        committing_db,
        coverage("account", synced_at=later, synced_through_block=950, notes=("new",)),
    )

    rows = get(committing_db)
    assert rows["account"].synced_at == later and rows["account"].synced_through_block == 950
    assert rows["account"].notes == ("new",)
    assert rows["internal"].synced_at == T0


def test_coverage_is_kept_per_address(committing_db):
    put(committing_db, coverage("account"))

    assert get(committing_db, OTHER) == {}


def test_forgetting_an_address_makes_it_look_never_read(committing_db):
    put(committing_db, coverage("account"), coverage("internal"))
    put(committing_db, coverage("account"), address=OTHER)

    with committing_db.write() as session:
        forget_coverage(session, ETH, ME)
        session.commit()

    assert get(committing_db) == {} and len(get(committing_db, OTHER)) == 1


# --- the log -----------------------------------------------------------------------------------


def run(started: datetime, **changes) -> RunRecord:
    values = {
        "chain_id": ETH,
        "address": ME,
        "mode": "update",
        "outcome": "complete",
        "started_at": started,
        "finished_at": started + timedelta(seconds=2),
        "requests_used": 3,
        "from_block": 900,
        "new": 4,
        "updated": 1,
        "unchanged": 7,
        "removed": 0,
        "sources": ("etherscan", "alchemy"),
    }
    values.update(changes)
    return RunRecord(**values)


def test_runs_are_logged_newest_first(committing_db):
    with committing_db.write() as session:
        record_run(session, run(T0))
        record_run(session, run(T0 + timedelta(hours=1), mode="full", error="boom", notes=("a",)))
        session.commit()

    with committing_db.read() as session:
        rows = recent_runs(session, ETH, ME)
    assert [r.mode for r in rows] == ["full", "update"]
    newest = rows[0]
    assert (newest.error, newest.notes, newest.sources) == ("boom", ["a"], "etherscan,alchemy")
    assert (rows[1].new_count, rows[1].updated_count, rows[1].requests_used) == (4, 1, 3)
    assert rows[1].finished_at == T0 + timedelta(seconds=2)


def test_a_run_may_name_the_job_that_asked_for_it(committing_db):
    with committing_db.write() as session:
        job, _ = enqueue(session, "FETCH_ADDRESS_HISTORY", {"chain": "ethereum"})
        record_run(session, run(T0, job_id=job.id))
        session.commit()
        job_id = job.id

    with committing_db.read() as session:
        assert recent_runs(session, ETH, ME)[0].job_id == job_id


# --- raw answers -------------------------------------------------------------------------------


def raw(provider="helius", payload=b"payload", **changes) -> RawItem:
    values = {
        "chain_id": ETH,
        "kind": "solana_transaction",
        "record_key": "sig1",
        "provider": provider,
        "payload": payload,
        "size_bytes": len(payload) * 10,
        "sha256": "f" * 64,
        "fetched_at": T0,
    }
    values.update(changes)
    return RawItem(**values)


def test_a_raw_answer_comes_back_byte_for_byte_including_large_ones(committing_db):
    big = bytes(range(256)) * 1000  # 256 KB, well over a plain MySQL BLOB
    with committing_db.write() as session:
        save_raw(session, [raw(payload=big)])
        session.commit()

    with committing_db.read() as session:
        (loaded,) = load_raw(session, ETH, "solana_transaction", "sig1")
    assert loaded == raw(payload=big)


def test_the_latest_answer_replaces_the_earlier_one_of_the_same_provider(committing_db):
    with committing_db.write() as session:
        save_raw(session, [raw(payload=b"old")])
        save_raw(session, [raw(payload=b"new", fetched_at=T0 + timedelta(hours=1))])
        session.commit()

    with committing_db.read() as session:
        (loaded,) = load_raw(session, ETH, "solana_transaction", "sig1")
    assert loaded.payload == b"new" and loaded.fetched_at == T0 + timedelta(hours=1)


def test_answers_of_different_providers_are_kept_side_by_side(committing_db):
    with committing_db.write() as session:
        save_raw(session, [raw("helius"), raw("other")])
        session.commit()

    with committing_db.read() as session:
        both = load_raw(session, ETH, "solana_transaction", "sig1")
        one = load_raw(session, ETH, "solana_transaction", "sig1", "other")
        none = load_raw(session, ETH, "solana_transaction", "sig2")
    assert [r.provider for r in both] == ["helius", "other"]
    assert [r.provider for r in one] == ["other"] and none == []
