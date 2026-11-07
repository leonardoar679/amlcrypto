"""What the worker does on a schedule besides running jobs."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time

import httpx2
import pytest

from app.core.config import Settings
from app.providers.service import ProviderService
from app.worker import HandlerRegistry, Worker
from app.worker.context import Periodic
from app.worker.handlers import build_default_handlers, build_default_periodic
from tests.fake_etherscan import OTHER
from tests.graph_store import label_address
from tests.helpers import FakeClock, Server, run, wait_until
from tests.test_monitoring_job import alerts, monitor, monitor_of
from tests.test_risk_jobs import rig_with_window
from tests.test_worker import enqueue, job_of, registry_with


@pytest.fixture
def quick(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"worker_concurrency": 2, "worker_poll_interval_seconds": 0.01}
    )


def make_worker(db, settings, periodic=(), handlers=None) -> Worker:
    clock = FakeClock()
    providers = ProviderService.from_settings(
        settings, transport=httpx2.MockTransport(Server()), clock=clock, sleep=clock.sleep
    )
    return Worker(
        settings=settings,
        write_session_factory=db.write,
        providers=providers,
        handlers=handlers if handlers is not None else HandlerRegistry(),
        periodic=periodic,
    )


def serve(worker, until, *, timeout=5.0):
    """Run the worker until `until()` holds, then stop it and wait for it."""

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(stop))
        try:
            await wait_until(until, timeout=timeout)
        finally:
            stop.set()
            await task

    run(scenario())


def test_a_scheduled_task_runs_at_once_and_then_every_so_often(committing_db, quick):
    runs = []
    worker = make_worker(committing_db, quick, [Periodic("count", 0.02, lambda: runs.append(1))])

    serve(worker, lambda: len(runs) >= 4)

    assert len(runs) >= 4


def test_it_stops_when_the_worker_stops(committing_db, quick):
    runs = []
    worker = make_worker(committing_db, quick, [Periodic("count", 0.02, lambda: runs.append(1))])
    serve(worker, lambda: len(runs) >= 2)

    seen = len(runs)
    time.sleep(0.15)

    assert len(runs) == seen


def test_the_schedule_ends_when_the_worker_is_cancelled_too(committing_db, quick):
    runs = []
    worker = make_worker(committing_db, quick, [Periodic("count", 0.02, lambda: runs.append(1))])

    async def scenario():
        task = asyncio.create_task(worker.run(asyncio.Event()))  # `stop` is never set
        await wait_until(lambda: len(runs) >= 2)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        seen = len(runs)
        await asyncio.sleep(0.15)
        return seen, len(runs)

    before, after = run(scenario())

    assert after == before


def test_a_long_wait_between_runs_does_not_hold_the_worker_up_when_it_stops(committing_db, quick):
    runs = []
    worker = make_worker(committing_db, quick, [Periodic("rare", 3600, lambda: runs.append(1))])
    started = time.monotonic()

    serve(worker, lambda: len(runs) == 1)

    assert runs == [1] and time.monotonic() - started < 3


def test_a_scheduled_task_that_fails_is_logged_and_tried_again_and_jobs_go_on(
    committing_db, quick, caplog
):
    attempts = []

    def sometimes() -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("the database was busy")

    async def handler(context):
        return {"done": True}

    worker = make_worker(
        committing_db, quick, [Periodic("flaky", 0.02, sometimes)], registry_with(handler)
    )
    job_id = enqueue(committing_db)

    with caplog.at_level(logging.ERROR):
        serve(
            worker,
            lambda: len(attempts) >= 3 and job_of(committing_db, job_id).status == "SUCCEEDED",
        )

    failures = [r for r in caplog.records if r.getMessage() == "scheduled task failed"]
    assert len(failures) == 2 and all(r.task == "flaky" for r in failures)
    assert failures[0].exc_info[1].args == ("the database was busy",)


def test_scheduled_tasks_do_not_wait_for_each_other(committing_db, quick):
    fast = []
    slow_started = threading.Event()
    release = threading.Event()

    def slow() -> None:
        slow_started.set()
        release.wait(5)

    worker = make_worker(
        committing_db,
        quick,
        [Periodic("slow", 0.01, slow), Periodic("fast", 0.01, lambda: fast.append(1))],
    )
    try:
        serve(worker, lambda: slow_started.is_set() and len(fast) >= 3)
    finally:
        release.set()

    assert len(fast) >= 3


def test_a_scheduled_task_runs_off_the_event_loop(committing_db, quick):
    threads = []
    worker = make_worker(
        committing_db,
        quick,
        [Periodic("where", 0.01, lambda: threads.append(threading.get_ident()))],
    )
    loop_thread = []

    async def scenario():
        loop_thread.append(threading.get_ident())
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(stop))
        await wait_until(lambda: len(threads) >= 1)
        stop.set()
        await task

    run(scenario())

    assert threads[0] != loop_thread[0]


def test_a_one_off_drain_does_not_run_the_schedule(committing_db, quick):
    runs = []
    worker = make_worker(committing_db, quick, [Periodic("count", 0.01, lambda: runs.append(1))])

    processed = run(worker.run(asyncio.Event(), drain=True))

    assert processed == 0 and runs == []


def test_a_worker_with_no_schedule_has_none(committing_db, quick):
    async def handler(context):
        return {}

    worker = make_worker(committing_db, quick, (), registry_with(handler))
    job_id = enqueue(committing_db)
    serve(worker, lambda: job_of(committing_db, job_id).status == "SUCCEEDED")


# --- the schedule every worker has --------------------------------------------------------------


def test_every_worker_queues_the_checks_of_monitored_addresses(committing_db, quick):
    (periodic,) = build_default_periodic(quick, committing_db.write)
    assert isinstance(periodic, Periodic) and periodic.name == "monitoring"
    assert periodic.every == quick.monitoring_tick_seconds


def test_a_running_worker_checks_the_monitors_that_are_due_and_raises_the_alert(committing_db):
    rig = rig_with_window()
    rig.settings = rig.settings.model_copy(
        update={"worker_poll_interval_seconds": 0.01, "monitoring_tick_seconds": 0.05}
    )
    with committing_db.write() as session:
        label_address(session, OTHER, "SANCTIONS")
    number = monitor_of(committing_db, rig)  # due from the moment it is registered
    worker = Worker(
        settings=rig.settings,
        write_session_factory=committing_db.write,
        read_session_factory=committing_db.read,
        providers=rig.providers,
        handlers=build_default_handlers(),
        periodic=build_default_periodic(rig.settings, committing_db.write),
    )

    serve(worker, lambda: monitor(committing_db, number).last_checked_at is not None, timeout=20)

    (alert,) = alerts(committing_db)
    assert alert.monitored_address_id == number and alert.kind == "baseline_risk"
    row = monitor(committing_db, number)
    assert row.next_check_at > row.last_checked_at
