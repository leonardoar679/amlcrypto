"""The FETCH_ADDRESS_HISTORY job when it has a database: it stores what it reads and reuses it."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.database.connection import Sessions
from app.database.models import FetchJob, FetchRun, StoredTransfer
from app.database.repositories import jobs as queue
from app.ingestion.jobs import FETCH_ADDRESS_HISTORY, fetch_address_history
from app.worker import Worker
from app.worker.context import JobContext, NonRetryable
from app.worker.handlers import build_default_handlers
from tests.fake_etherscan import ME, OTHER, token_row, tx_row
from tests.helpers import run
from tests.rigs import Rig


def seeded() -> Rig:
    rig = Rig()
    rig.etherscan.add(
        "txlist", *[tx_row(block, OTHER, ME, value=str(block)) for block in (5, 6, 7)]
    )
    rig.etherscan.add("tokentx", token_row(6, OTHER, ME, value="42"))
    return rig


def context(rig: Rig, db, **payload) -> JobContext:
    with db.write() as session:  # a run names the job that asked for it, which must exist
        job, _ = queue.enqueue(session, FETCH_ADDRESS_HISTORY, payload)
        job_id = job.id
    return JobContext(
        job_id=job_id,
        job_type=FETCH_ADDRESS_HISTORY,
        payload=payload,
        attempt=1,
        max_attempts=3,
        organization_id=None,
        providers=rig.providers,
        settings=rig.settings,
        sessions=Sessions(db.read, db.write),
    )


def stored_transfers(db) -> int:
    with db.read() as session:
        return session.scalar(select(func.count()).select_from(StoredTransfer))


def test_the_first_job_reads_and_stores_and_the_second_asks_the_providers_nothing(committing_db):
    rig = seeded()

    first = run(fetch_address_history(context(rig, committing_db, chain="ethereum", address=ME)))
    asked = len(rig.etherscan.requests) + len(rig.alchemy.requests)
    second = run(fetch_address_history(context(rig, committing_db, chain="ethereum", address=ME)))

    json.dumps(first), json.dumps(second)
    assert first["sync"]["mode"] == "full" and first["requests_used"] > 0
    assert second["sync"]["mode"] == "cached" and second["requests_used"] == 0
    assert len(rig.etherscan.requests) + len(rig.alchemy.requests) == asked
    assert second["transfers"] == first["transfers"] == 4 and second["complete"] is True
    assert second["sync"]["age_seconds"] is not None
    assert stored_transfers(committing_db) == 4


def test_refresh_updates_now_and_rebuild_reads_everything_again(committing_db):
    rig = seeded()
    run(fetch_address_history(context(rig, committing_db, chain="ethereum", address=ME)))

    refreshed = run(
        fetch_address_history(
            context(rig, committing_db, chain="ethereum", address=ME, refresh=True)
        )
    )
    rebuilt = run(
        fetch_address_history(
            context(rig, committing_db, chain="ethereum", address=ME, rebuild=True)
        )
    )

    assert (
        refreshed["sync"]["mode"] == "update" and refreshed["sync"]["reason"] == "refresh requested"
    )
    assert rebuilt["sync"]["mode"] == "rebuild" and rebuilt["sync"]["new"] == 0
    assert stored_transfers(committing_db) == 4


def test_a_job_that_names_a_block_range_reads_exactly_that_and_stores_nothing(committing_db):
    rig = seeded()

    result = run(
        fetch_address_history(
            context(rig, committing_db, chain="ethereum", address=ME, from_block=6, to_block=7)
        )
    )

    assert "sync" not in result and result["transfers"] == 3
    assert stored_transfers(committing_db) == 0


@pytest.mark.parametrize("name", ["refresh", "rebuild"])
def test_the_flags_must_be_true_or_false(committing_db, name):
    rig = seeded()
    with pytest.raises(NonRetryable, match=f"{name} must be true or false"):
        run(
            fetch_address_history(
                context(rig, committing_db, chain="ethereum", address=ME, **{name: "yes"})
            )
        )


def test_through_the_worker_the_second_job_is_served_from_storage_and_the_run_names_the_job(
    committing_db,
):
    rig = seeded()
    worker = Worker(
        settings=rig.settings,
        write_session_factory=committing_db.write,
        read_session_factory=committing_db.read,
        providers=rig.providers,
        handlers=build_default_handlers(),
    )
    payload = {"chain": "ethereum", "address": ME}
    with committing_db.write() as session:
        first, _ = queue.enqueue(session, FETCH_ADDRESS_HISTORY, payload)
    assert run(worker.run_once()) is True
    with committing_db.write() as session:
        second, _ = queue.enqueue(session, FETCH_ADDRESS_HISTORY, payload)
    assert run(worker.run_once()) is True

    with committing_db.read() as session:
        done = {job.id: job for job in session.scalars(select(FetchJob))}
        runs = list(session.scalars(select(FetchRun)))
    assert done[first.id].result["sync"]["mode"] == "full"
    assert done[second.id].result["sync"]["mode"] == "cached"
    assert done[second.id].result["requests_used"] == 0
    assert [(r.mode, r.job_id) for r in runs] == [("full", first.id)]
