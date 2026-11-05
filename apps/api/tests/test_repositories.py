"""The job queue and the usage counters, including the races between concurrent writers."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.database.models import ApiUsage, FetchJob, JobStatus
from app.database.repositories import jobs as queue
from app.database.repositories.usage import UsageTotals, hour_start, record_usage, usage_since

T0 = datetime(2026, 11, 5, 12, 30, 15, 500_000, tzinfo=UTC)


def run_threads(count: int, target) -> list[BaseException]:
    errors: list[BaseException] = []

    def guarded(index: int) -> None:
        try:
            target(index)
        except BaseException as exc:  # noqa: BLE001 - surface any failure from a worker thread
            errors.append(exc)

    threads = [threading.Thread(target=guarded, args=(n,)) for n in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


# --- usage counters --------------------------------------------------------------------------


def test_usage_is_counted_per_provider_per_hour(committing_db):
    with committing_db.write() as session:
        record_usage(session, provider="etherscan", at=T0, successes=1)
        record_usage(
            session, provider="etherscan", at=T0 + timedelta(minutes=10), errors=1, retries=1
        )
        record_usage(session, provider="etherscan", at=T0 + timedelta(hours=1), successes=1)
        record_usage(session, provider="trongrid", at=T0, rate_limited=1)

    with committing_db.read() as session:
        rows = {(r.provider, r.period_start): r for r in session.scalars(select(ApiUsage))}
    first_hour = rows[("etherscan", hour_start(T0))]
    assert (first_hour.requests, first_hour.successes, first_hour.errors, first_hour.retries) == (
        2,
        1,
        1,
        1,
    )
    assert rows[("etherscan", hour_start(T0 + timedelta(hours=1)))].requests == 1
    assert rows[("trongrid", hour_start(T0))].rate_limited == 1
    assert len(rows) == 3


def test_usage_since_sums_the_hours_in_range_and_rounds_down(committing_db):
    with committing_db.write() as session:
        record_usage(session, provider="etherscan", at=T0 - timedelta(hours=3), successes=1)
        record_usage(session, provider="etherscan", at=T0, successes=1)
        record_usage(session, provider="etherscan", at=T0 + timedelta(hours=2), errors=1)

    with committing_db.read() as session:
        recent = usage_since(session, T0)
        everything = usage_since(session, T0 - timedelta(days=1))
        nothing = usage_since(session, T0 + timedelta(days=1))
    assert recent["etherscan"] == UsageTotals(requests=2, successes=1, errors=1)
    assert everything["etherscan"].requests == 3
    assert nothing == {}


def test_concurrent_increments_of_one_row_are_all_counted(committing_db):
    def work(_index: int) -> None:
        for _ in range(20):
            with committing_db.write() as session:
                record_usage(session, provider="etherscan", at=T0, successes=1)

    assert run_threads(5, work) == []
    with committing_db.read() as session:
        assert usage_since(session, T0)["etherscan"].requests == 100  # and exactly one row
        assert len(session.scalars(select(ApiUsage.id)).all()) == 1


# --- enqueue and dedupe ----------------------------------------------------------------------


def test_enqueue_creates_a_queued_job_with_defaults(committing_db):
    with committing_db.write() as session:
        job, created = queue.enqueue(session, "NOOP", {"x": 1})
    assert created
    assert (job.status, job.attempts, job.max_attempts, job.priority) == ("QUEUED", 0, 3, 0)
    assert job.payload == {"x": 1}


def test_an_active_job_with_the_same_dedupe_key_is_returned_instead_of_a_duplicate(committing_db):
    with committing_db.write() as session:
        first, created_first = queue.enqueue(session, "NOOP", dedupe_key="addr:1")
        second, created_second = queue.enqueue(session, "NOOP", dedupe_key="addr:1")
    assert created_first and not created_second
    assert first.id == second.id


def test_the_dedupe_key_is_released_when_the_job_completes(committing_db):
    with committing_db.write() as session:
        job, _ = queue.enqueue(session, "NOOP", dedupe_key="addr:1")
        claimed = queue.claim_next(session, "w1")
        queue.complete(session, claimed.id, {"ok": True})
        again, created = queue.enqueue(session, "NOOP", dedupe_key="addr:1")
    assert created and again.id != job.id


def test_many_threads_enqueueing_the_same_key_produce_exactly_one_job(committing_db):
    ids: list[int] = []
    created_flags: list[bool] = []
    lock = threading.Lock()

    def work(_index: int) -> None:
        with committing_db.write() as session:
            job, created = queue.enqueue(session, "NOOP", dedupe_key="same")
        with lock:
            ids.append(job.id)
            created_flags.append(created)

    assert run_threads(8, work) == []
    assert len(set(ids)) == 1
    assert created_flags.count(True) == 1


# --- claiming --------------------------------------------------------------------------------


def test_jobs_are_claimed_by_priority_then_by_when_they_became_available(committing_db):
    with committing_db.write() as session:
        queue.enqueue(session, "NOOP", {"n": "low-early"}, priority=0, available_at=T0)
        queue.enqueue(
            session, "NOOP", {"n": "high-late"}, priority=5, available_at=T0 + timedelta(seconds=5)
        )
        queue.enqueue(session, "NOOP", {"n": "high-early"}, priority=5, available_at=T0)
        queue.enqueue(
            session, "NOOP", {"n": "future"}, priority=9, available_at=T0 + timedelta(hours=1)
        )

        now = T0 + timedelta(minutes=1)
        order = [queue.claim_next(session, "w", now=now).payload["n"] for _ in range(3)]
        assert order == ["high-early", "high-late", "low-early"]
        assert queue.claim_next(session, "w", now=now) is None  # "future" is not due yet


def test_a_claimed_job_is_running_and_counts_the_attempt(committing_db):
    with committing_db.write() as session:
        queue.enqueue(session, "NOOP", available_at=T0)
        job = queue.claim_next(session, "worker-7", now=T0)
    assert (job.status, job.attempts, job.locked_by) == ("RUNNING", 1, "worker-7")
    assert job.locked_at == T0


def test_claiming_can_be_limited_to_some_job_types(committing_db):
    with committing_db.write() as session:
        queue.enqueue(session, "A", available_at=T0)
        queue.enqueue(session, "B", available_at=T0)
        job = queue.claim_next(session, "w", job_types={"B"}, now=T0)
        assert job.job_type == "B"
        assert queue.claim_next(session, "w", job_types={"B"}, now=T0) is None


def test_a_running_job_cannot_be_claimed_again(committing_db):
    with committing_db.write() as session:
        job, _ = queue.enqueue(session, "NOOP", available_at=T0)
        queue.claim_next(session, "first", now=T0)
        # The compare-and-set that decides every claim race: it no longer matches.
        lost = session.execute(
            update(FetchJob)
            .where(FetchJob.id == job.id, FetchJob.status == JobStatus.QUEUED.value)
            .values(status=JobStatus.RUNNING.value, locked_by="second")
        )
        assert lost.rowcount == 0
        session.rollback()
        assert queue.claim_next(session, "second", now=T0) is None


def test_many_workers_claiming_at_once_never_take_the_same_job(committing_db):
    with committing_db.write() as session:
        for number in range(30):
            queue.enqueue(session, "NOOP", {"n": number}, available_at=T0)

    claimed: list[int] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        while True:
            with committing_db.write() as session:
                job = queue.claim_next(session, f"w{index}", now=T0 + timedelta(minutes=1))
            if job is None:
                return
            with lock:
                claimed.append(job.payload["n"])

    assert run_threads(6, worker) == []
    assert sorted(claimed) == list(range(30))  # every job exactly once, none twice


# --- completing, failing, recovering ---------------------------------------------------------


def test_completing_records_the_result_and_clears_the_lock(committing_db):
    with committing_db.write() as session:
        queue.enqueue(session, "NOOP", available_at=T0)
        job = queue.claim_next(session, "w", now=T0)
        queue.complete(session, job.id, {"rows": 3})
    with committing_db.read() as session:
        done = session.get(FetchJob, job.id)
    assert (done.status, done.result, done.locked_by, done.error) == (
        "SUCCEEDED",
        {"rows": 3},
        None,
        None,
    )
    assert done.completed_at is not None


def test_a_failed_attempt_is_requeued_with_growing_backoff_then_given_up_on(committing_db):
    with committing_db.write() as session:
        job, _ = queue.enqueue(session, "NOOP", max_attempts=3, available_at=T0, dedupe_key="k")
        for attempt, expected_delay in ((1, 5), (2, 10)):
            queue.claim_next(session, "w", now=T0 + timedelta(days=attempt))
            outcome = queue.fail(session, job.id, f"boom {attempt}", now=T0)
            assert outcome is JobStatus.QUEUED
            requeued = session.get(FetchJob, job.id, populate_existing=True)
            assert requeued.available_at == T0 + timedelta(seconds=expected_delay)
            assert requeued.error == f"boom {attempt}" and requeued.dedupe_key == "k"

        queue.claim_next(session, "w", now=T0 + timedelta(days=3))
        assert queue.fail(session, job.id, "boom 3", now=T0) is JobStatus.FAILED
        final = session.get(FetchJob, job.id, populate_existing=True)
        assert final.attempts == 3 and final.completed_at == T0 and final.dedupe_key is None


def test_a_provider_supplied_retry_delay_overrides_the_backoff(committing_db):
    with committing_db.write() as session:
        job, _ = queue.enqueue(session, "NOOP", available_at=T0)
        queue.claim_next(session, "w", now=T0)
        queue.fail(session, job.id, "rate limited", retry_after=120.0, now=T0)
        assert session.get(FetchJob, job.id, populate_existing=True).available_at == T0 + timedelta(
            minutes=2
        )


def test_backoff_doubles_and_is_capped():
    assert [queue.backoff_delay(n) for n in (1, 2, 3, 4)] == [5, 10, 20, 40]
    assert queue.backoff_delay(20) == 900.0
    assert queue.backoff_delay(0) == 5


def test_stale_running_jobs_are_requeued_or_failed_if_out_of_attempts(committing_db):
    with committing_db.write() as session:
        queue.enqueue(session, "NOOP", {"n": "retry"}, max_attempts=3, available_at=T0)
        queue.enqueue(
            session, "NOOP", {"n": "last"}, max_attempts=1, available_at=T0, dedupe_key="x"
        )
        queue.enqueue(session, "NOOP", {"n": "fresh"}, available_at=T0)
        queue.claim_next(session, "dead-worker", now=T0)  # retry
        queue.claim_next(session, "dead-worker", now=T0)  # last
        queue.claim_next(session, "live-worker", now=T0 + timedelta(minutes=9))  # fresh

        recovered = queue.requeue_stale(
            session, older_than=timedelta(minutes=10), now=T0 + timedelta(minutes=11)
        )
        assert recovered == 2
        states = {job.payload["n"]: job for job in session.scalars(select(FetchJob)).all()}
    assert states["retry"].status == "QUEUED" and states["retry"].locked_by is None
    assert states["last"].status == "FAILED" and states["last"].dedupe_key is None
    assert states["fresh"].status == "RUNNING"
