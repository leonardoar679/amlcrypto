"""The job worker against a real database: success, retry, give-up, timeouts, shutdown, recovery."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from datetime import timedelta

import httpx2
import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.core.redaction import secrets
from app.database.models import FetchJob
from app.database.models.base import utcnow
from app.database.repositories import jobs as queue
from app.providers.base import (
    AllProvidersFailed,
    BudgetExceeded,
    CircuitOpen,
    ProviderAuthError,
    ProviderBadResponse,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderUnavailable,
)
from app.providers.service import ProviderService
from app.worker import (
    PROVIDER_PROBE,
    HandlerRegistry,
    JobContext,
    NonRetryable,
    Worker,
    build_default_handlers,
)
from tests.helpers import FakeClock, Reply, Server, run, wait_until

NOOP = "NOOP"


@pytest.fixture
def worker_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "worker_concurrency": 3,
            "worker_poll_interval_seconds": 0.01,
            "job_timeout_seconds": 0.3,  # the real minimum is 1 s; tests need it quick
        }
    )


@pytest.fixture
def patient_settings(worker_settings: Settings) -> Settings:
    """For tests that keep a job running on purpose: the timeout must not cut it short."""
    return worker_settings.model_copy(update={"job_timeout_seconds": 30.0})


def make_worker(db, settings, handlers=None, *, server=None) -> Worker:
    clock = FakeClock()
    providers = ProviderService.from_settings(
        settings, transport=httpx2.MockTransport(server or Server()), clock=clock, sleep=clock.sleep
    )
    return Worker(
        settings=settings,
        write_session_factory=db.write,
        providers=providers,
        handlers=handlers if handlers is not None else build_default_handlers(),
    )


def registry_with(handler, job_type=NOOP) -> HandlerRegistry:
    registry = HandlerRegistry()
    registry.register(job_type)(handler)
    return registry


def enqueue(db, job_type=NOOP, payload=None, **kwargs) -> int:
    with db.write() as session:
        job, _ = queue.enqueue(session, job_type, payload or {}, **kwargs)
        return job.id


def job_of(db, job_id) -> FetchJob:
    with db.read() as session:
        return session.get(FetchJob, job_id)


def delay_of(job: FetchJob) -> float:
    """Seconds from now until the job is due again."""
    return (job.available_at - utcnow()).total_seconds()


# --- the built-in probe job, end to end --------------------------------------------------------


def test_a_probe_job_runs_through_the_queue_and_stores_its_result(committing_db, worker_settings):
    server = Server(Reply(text="974631"))
    worker = make_worker(committing_db, worker_settings, server=server)
    job_id = enqueue(committing_db, PROVIDER_PROBE, {"provider": "esplora"})

    async def scenario():
        return await worker.run_once(), await worker.run_once()

    ran, ran_again = run(scenario())
    job = job_of(committing_db, job_id)
    assert (ran, ran_again) == (True, False)  # the queue is empty the second time
    assert (job.status, job.attempts, job.error) == ("SUCCEEDED", 1, None)
    assert job.result["provider"] == "esplora" and job.result["ok"] is True
    assert job.result["status"] == "ok" and job.completed_at is not None
    assert len(server.requests) == 1


def test_a_probe_job_for_a_provider_that_does_not_exist_is_rejected_for_good(
    committing_db, worker_settings
):
    worker = make_worker(committing_db, worker_settings)
    job_id = enqueue(committing_db, PROVIDER_PROBE, {"provider": "nope"}, max_attempts=5)
    run(worker.run_once())
    job = job_of(committing_db, job_id)
    assert (job.status, job.attempts) == ("FAILED", 1)
    assert "known provider" in job.error


# --- what the handler is given -----------------------------------------------------------------


def test_a_handler_receives_the_job_and_the_shared_services(committing_db, worker_settings):
    seen: list[JobContext] = []

    async def handler(context):
        seen.append(context)
        return {"echo": context.payload}

    worker = make_worker(committing_db, worker_settings, registry_with(handler))
    job_id = enqueue(committing_db, payload={"address": "0xabc"}, max_attempts=4)
    run(worker.run_once())

    (context,) = seen
    assert (context.job_id, context.job_type, context.payload) == (
        job_id,
        NOOP,
        {"address": "0xabc"},
    )
    assert (context.attempt, context.max_attempts, context.organization_id) == (1, 4, None)
    assert context.providers is worker._providers and context.settings is worker_settings
    assert job_of(committing_db, job_id).result == {"echo": {"address": "0xabc"}}


# --- failures ----------------------------------------------------------------------------------


def test_a_crashing_handler_is_retried_with_backoff_and_finally_given_up_on(
    committing_db, worker_settings
):
    async def handler(context):
        raise RuntimeError("boom")

    worker = make_worker(committing_db, worker_settings, registry_with(handler))
    job_id = enqueue(committing_db, max_attempts=2, dedupe_key="k")

    run(worker.run_once())
    first = job_of(committing_db, job_id)
    assert (first.status, first.attempts, first.error) == ("QUEUED", 1, "RuntimeError: boom")
    assert 3 < delay_of(first) <= 5 and first.dedupe_key == "k"
    assert run(worker.run_once()) is False  # not due yet

    with committing_db.write() as session:  # skip the wait
        session.execute(update(FetchJob).values(available_at=utcnow() - timedelta(seconds=1)))
        session.commit()
    run(worker.run_once())
    last = job_of(committing_db, job_id)
    assert (last.status, last.attempts) == ("FAILED", 2)
    assert last.dedupe_key is None and last.completed_at is not None


def test_a_handler_can_declare_that_retrying_is_pointless(committing_db, worker_settings):
    async def handler(context):
        raise NonRetryable("the payload has no address")

    worker = make_worker(committing_db, worker_settings, registry_with(handler))
    job_id = enqueue(committing_db, max_attempts=5)
    run(worker.run_once())
    job = job_of(committing_db, job_id)
    assert (job.status, job.attempts, job.error) == ("FAILED", 1, "the payload has no address")


@pytest.mark.parametrize(
    ("error", "status", "wait"),
    [
        (ProviderAuthError("etherscan", "HTTP 403"), "FAILED", None),
        (ProviderNotConfigured("etherscan", "no API key is configured"), "FAILED", None),
        (ProviderBadResponse("etherscan", "HTTP 404"), "FAILED", None),
        (BudgetExceeded("etherscan", "request budget of 5 is used up"), "FAILED", None),
        (ProviderUnavailable("etherscan", "HTTP 503"), "QUEUED", 5),
        (ProviderRateLimited("etherscan", "HTTP 429", retry_after=120), "QUEUED", 120),
        (CircuitOpen("etherscan", "circuit open", retry_after=30), "QUEUED", 30),
        (
            AllProvidersFailed(
                "transfers",
                [
                    ("etherscan", ProviderRateLimited("etherscan", "HTTP 429", retry_after=90)),
                    ("alchemy", ProviderAuthError("alchemy", "HTTP 401")),
                ],
            ),
            "QUEUED",
            90,
        ),
        (
            AllProvidersFailed(
                "transfers", [("etherscan", ProviderNotConfigured("etherscan", "no key"))]
            ),
            "FAILED",
            None,
        ),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, Exception) else str(value),
)
def test_provider_errors_decide_whether_a_job_is_retried_and_when(
    committing_db, worker_settings, error, status, wait
):
    async def handler(context):
        raise error

    worker = make_worker(committing_db, worker_settings, registry_with(handler))
    job_id = enqueue(committing_db, max_attempts=3)
    run(worker.run_once())

    job = job_of(committing_db, job_id)
    assert job.status == status
    if wait is not None:
        assert wait - 2 < delay_of(job) <= wait
    assert job.error == str(error)


def test_a_job_with_no_handler_fails_loudly_instead_of_waiting_forever(
    committing_db, worker_settings
):
    worker = make_worker(committing_db, worker_settings, HandlerRegistry())
    job_id = enqueue(committing_db, "SOMETHING_NEW", max_attempts=5)
    run(worker.run_once())
    job = job_of(committing_db, job_id)
    assert (job.status, job.attempts) == ("FAILED", 1)
    assert "no handler is registered for job type 'SOMETHING_NEW'" in job.error


def test_a_handler_that_hangs_is_cut_off_and_the_attempt_is_retried(committing_db, worker_settings):
    async def handler(context):
        await asyncio.Event().wait()

    worker = make_worker(committing_db, worker_settings, registry_with(handler))
    job_id = enqueue(committing_db)
    run(worker.run_once())
    job = job_of(committing_db, job_id)
    assert (job.status, job.attempts, job.error) == ("QUEUED", 1, "timed out after 0.3s")


def test_secrets_are_scrubbed_before_an_error_is_stored(committing_db, worker_settings):
    key = "very-secret-key-123456"
    secrets.add(key)

    async def handler(context):
        raise RuntimeError(f"upstream rejected https://x.test/?apikey={key}")

    worker = make_worker(committing_db, worker_settings, registry_with(handler))
    job_id = enqueue(committing_db)
    run(worker.run_once())
    error = job_of(committing_db, job_id).error
    assert key not in error and "***" in error


def test_an_error_message_is_cut_to_a_sane_length(committing_db, worker_settings):
    async def handler(context):
        raise RuntimeError("x" * 50_000)

    worker = make_worker(committing_db, worker_settings, registry_with(handler))
    job_id = enqueue(committing_db)
    run(worker.run_once())
    assert len(job_of(committing_db, job_id).error) == 2000


def test_a_result_that_cannot_be_stored_fails_the_job_but_not_the_worker(
    committing_db, worker_settings
):
    async def handler(context):
        return {"not json": object()}

    worker = make_worker(committing_db, worker_settings, registry_with(handler))
    job_id = enqueue(committing_db)
    assert run(worker.run_once()) is True
    job = job_of(committing_db, job_id)
    assert job.status == "FAILED" and job.error.startswith("could not store the result")


# --- running many jobs -------------------------------------------------------------------------


def test_draining_runs_every_due_job_within_the_concurrency_limit(committing_db, worker_settings):
    running = peak = 0

    async def handler(context):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        return {"n": context.payload["n"]}

    worker = make_worker(committing_db, worker_settings, registry_with(handler))
    ids = [enqueue(committing_db, payload={"n": n}) for n in range(10)]

    processed = run(worker.run(asyncio.Event(), drain=True))

    assert processed == 10
    assert {job_of(committing_db, i).status for i in ids} == {"SUCCEEDED"}
    assert 2 <= peak <= worker_settings.worker_concurrency


def test_the_loop_survives_a_database_hiccup(committing_db, worker_settings, monkeypatch):
    async def handler(context):
        return {}

    worker = make_worker(committing_db, worker_settings, registry_with(handler))
    job_id = enqueue(committing_db)
    real_claim = worker._claim
    failures = []

    def flaky_claim():
        if not failures:
            failures.append(1)
            raise OperationalError("SELECT 1", {}, Exception("database is locked"))
        return real_claim()

    monkeypatch.setattr(worker, "_claim", flaky_claim)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(stop))
        await wait_until(lambda: job_of(committing_db, job_id).status == "SUCCEEDED")
        stop.set()
        await task

    run(scenario())
    assert failures == [1]


# --- shutdown and recovery ---------------------------------------------------------------------


def test_stopping_lets_the_running_job_finish_first(committing_db, patient_settings):
    release = None

    async def handler(context):
        await release.wait()
        return {"finished": True}

    worker = make_worker(committing_db, patient_settings, registry_with(handler))
    job_id = enqueue(committing_db)

    async def scenario():
        nonlocal release
        release = asyncio.Event()
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(stop))
        await wait_until(lambda: job_of(committing_db, job_id).status == "RUNNING")

        stop.set()
        await asyncio.sleep(0.05)
        still_running = not task.done()
        release.set()
        await task
        return still_running

    assert run(scenario()) is True
    job = job_of(committing_db, job_id)
    assert (job.status, job.result) == ("SUCCEEDED", {"finished": True})


def test_jobs_that_outlast_the_grace_period_are_handed_back_untouched(
    committing_db, patient_settings
):
    async def handler(context):
        await asyncio.Event().wait()

    worker = make_worker(committing_db, patient_settings, registry_with(handler))
    job_id = enqueue(committing_db, max_attempts=3)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run_until_stopped(stop, grace=0.05))
        await wait_until(lambda: job_of(committing_db, job_id).status == "RUNNING")
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    run(scenario())
    job = job_of(committing_db, job_id)
    assert (job.status, job.attempts, job.locked_by) == (
        "QUEUED",
        0,
        None,
    )  # the attempt is refunded
    assert delay_of(job) <= 0  # and it is due straight away


def test_cancelling_the_worker_hands_its_jobs_back_too(committing_db, patient_settings):
    async def handler(context):
        await asyncio.Event().wait()

    worker = make_worker(committing_db, patient_settings, registry_with(handler))
    job_ids = [enqueue(committing_db) for _ in range(3)]

    async def scenario():
        task = asyncio.create_task(worker.run(asyncio.Event()))
        await wait_until(lambda: all(job_of(committing_db, i).status == "RUNNING" for i in job_ids))
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    run(scenario())
    for job_id in job_ids:
        job = job_of(committing_db, job_id)
        assert (job.status, job.attempts, job.locked_by) == ("QUEUED", 0, None)


def test_cancelling_while_a_claim_is_in_flight_does_not_orphan_the_job(
    committing_db, patient_settings, monkeypatch
):
    """The claim runs on a thread that cannot be interrupted, so a cancel can arrive after the
    database says RUNNING but before the worker knows it has the job."""
    worker = make_worker(committing_db, patient_settings, registry_with(lambda context: None))
    job_id = enqueue(committing_db, max_attempts=3)
    real_claim = worker._claim
    claimed, proceed = threading.Event(), threading.Event()

    def claim_then_dawdle():
        job = real_claim()
        claimed.set()
        proceed.wait(5)  # the job is RUNNING in the database; the event loop has not heard yet
        return job

    monkeypatch.setattr(worker, "_claim", claim_then_dawdle)

    async def scenario():
        task = asyncio.create_task(worker.run_once())
        await wait_until(claimed.is_set)
        task.cancel()
        proceed.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    job = job_of(committing_db, job_id)
    assert (job.status, job.attempts, job.locked_by) == ("QUEUED", 0, None)


def test_a_job_left_running_by_a_dead_worker_is_requeued(committing_db, worker_settings):
    worker = make_worker(committing_db, worker_settings)
    job_id = enqueue(committing_db, available_at=utcnow() - timedelta(hours=3))
    with committing_db.write() as session:
        queue.claim_next(session, "dead-worker", now=utcnow() - timedelta(hours=2))

    assert run(worker.recover_stale()) == 1
    job = job_of(committing_db, job_id)
    assert (job.status, job.locked_by) == ("QUEUED", None)
    assert run(worker.recover_stale()) == 0


def test_a_running_worker_picks_up_what_a_dead_one_left_behind(committing_db, worker_settings):
    async def handler(context):
        return {"recovered": True}

    worker = make_worker(committing_db, worker_settings, registry_with(handler))
    job_id = enqueue(committing_db, available_at=utcnow() - timedelta(hours=3))
    with committing_db.write() as session:
        queue.claim_next(session, "dead-worker", now=utcnow() - timedelta(hours=2))

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(stop))
        await wait_until(lambda: job_of(committing_db, job_id).status == "SUCCEEDED")
        stop.set()
        await task

    run(scenario())
    assert job_of(committing_db, job_id).result == {"recovered": True}


def test_worker_ids_fit_the_column_and_differ_between_workers(committing_db, worker_settings):
    first = make_worker(committing_db, worker_settings)
    second = make_worker(committing_db, worker_settings)
    assert len(first.worker_id) <= 80 and first.worker_id != second.worker_id


def test_duplicate_handler_registration_is_refused():
    registry = HandlerRegistry()

    async def handler(context):
        return None

    registry.register(NOOP)(handler)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(NOOP)(handler)
