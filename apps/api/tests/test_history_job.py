"""The FETCH_ADDRESS_HISTORY job: payload checks, limits, resuming, and through the real worker."""

from __future__ import annotations

import json

import pytest

from app.database.models import FetchJob
from app.database.repositories import jobs as queue
from app.ingestion.jobs import FETCH_ADDRESS_HISTORY, fetch_address_history
from app.worker import Worker
from app.worker.context import JobContext, NonRetryable
from app.worker.handlers import build_default_handlers
from tests.fake_etherscan import ME, OTHER, token_row, tx_row
from tests.helpers import run
from tests.rigs import Rig

CHECKSUMMED_ME = "0xA1a1A1a1a1A1a1A1a1A1a1a1A1a1A1a1A1a1A1a1"  # not a valid checksum on purpose


def context(rig: Rig, **payload) -> JobContext:
    return JobContext(
        job_id=1,
        job_type=FETCH_ADDRESS_HISTORY,
        payload=payload,
        attempt=1,
        max_attempts=3,
        organization_id=None,
        providers=rig.providers,
        settings=rig.settings,
    )


def seeded(**settings) -> Rig:
    rig = Rig(**settings)
    rig.etherscan.add(
        "txlist", *[tx_row(block, OTHER, ME, value=str(block)) for block in (5, 6, 7)]
    )
    rig.etherscan.add("tokentx", token_row(6, OTHER, ME, value="42"))
    return rig


# --- running ------------------------------------------------------------------------------------


def test_the_job_reads_a_history_and_returns_a_json_ready_summary():
    rig = seeded()

    result = run(fetch_address_history(context(rig, chain="ethereum", address=ME)))

    json.dumps(result)
    assert result["complete"] is True and result["transfers"] == 4 and result["transactions"] == 3
    assert result["transfers_by_type"] == {"native": 3, "erc20": 1}
    assert result["sources"] == ["etherscan", "alchemy"] and result["providers"] == ["etherscan"]
    assert result["requests_used"] == len(rig.etherscan.requests) + len(rig.alchemy.requests)
    assert result["chain_id"] == "eip155:1" and result["address"] == ME


def test_the_chain_may_be_named_by_slug_or_caip2_id_and_the_address_by_any_valid_case():
    rig = seeded()
    mixed = "0x" + "A1" * 20  # all one case: valid, and stored lowercase
    by_slug = run(fetch_address_history(context(rig, chain="ethereum", address=mixed)))
    by_id = run(fetch_address_history(context(rig, chain="eip155:1", address=ME)))
    assert by_slug["transfers"] == by_id["transfers"] == 4 and by_slug["address"] == ME


def test_a_block_range_in_the_payload_is_honoured():
    rig = seeded()
    result = run(
        fetch_address_history(context(rig, chain="ethereum", address=ME, from_block=6, to_block=6))
    )
    assert result["first_block"] == result["last_block"] == 6
    assert result["synced_through_block"] == 6


def test_a_small_request_budget_ends_the_read_with_a_way_to_resume_it():
    rig = Rig(history_request_budget=3)
    rig.etherscan.add("txlist", *[tx_row(b, OTHER, ME, value="1") for b in range(1, 400)])

    first = run(fetch_address_history(context(rig, chain="ethereum", address=ME)))
    assert first["complete"] is False and first["resume"] and first["requests_used"] == 3

    bigger = Rig(history_request_budget=500)
    bigger.etherscan.rows = rig.etherscan.rows
    rest = run(
        fetch_address_history(context(bigger, chain="ethereum", address=ME, resume=first["resume"]))
    )
    assert rest["complete"] is True
    assert first["transfers"] + rest["transfers"] == 399


def test_the_item_limit_in_the_payload_cannot_exceed_the_deployments_ceiling():
    rig = Rig(history_max_items=10)
    rig.etherscan.add("txlist", *[tx_row(b, OTHER, ME, value="1") for b in range(1, 400)])
    result = run(
        fetch_address_history(context(rig, chain="ethereum", address=ME, max_items=100_000))
    )
    assert result["complete"] is False
    assert 10 <= result["transfers"] < 400


# --- payloads that can never work --------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, '"chain" and an "address"'),
        ({"chain": "ethereum"}, '"chain" and an "address"'),
        ({"chain": 1, "address": ME}, '"chain" and an "address"'),
        ({"chain": "bitcoin", "address": ME}, "not a chain this platform reads"),
        ({"chain": "dogecoin", "address": ME}, "not a chain this platform reads"),
        ({"chain": "ethereum", "address": "0x123"}, "not a valid ethereum address"),
        ({"chain": "ethereum", "address": CHECKSUMMED_ME}, "checksum"),
        ({"chain": "ethereum", "address": ME, "max_items": 0}, "max_items"),
        ({"chain": "ethereum", "address": ME, "max_items": "many"}, "max_items"),
        ({"chain": "ethereum", "address": ME, "from_block": -1}, "from_block"),
        ({"chain": "ethereum", "address": ME, "to_block": "latest"}, "to_block"),
        ({"chain": "ethereum", "address": ME, "resume": ["account"]}, "resume"),
        ({"chain": "ethereum", "address": ME, "resume": {"account": 5}}, "resume"),
    ],
)
def test_a_payload_that_makes_no_sense_is_rejected_for_good(payload, message):
    with pytest.raises(NonRetryable, match=message):
        run(fetch_address_history(context(Rig(), **payload)))


def test_a_chain_with_no_configured_provider_says_which_keys_to_set():
    rig = Rig(etherscan_key=None, alchemy_key=None)
    with pytest.raises(NonRetryable, match="ETHERSCAN_API_KEY or ALCHEMY_API_KEY"):
        run(fetch_address_history(context(rig, chain="ethereum", address=ME)))


# --- through the queue and the worker ----------------------------------------------------------


def make_worker(committing_db, rig) -> Worker:
    return Worker(
        settings=rig.settings,
        write_session_factory=committing_db.write,
        providers=rig.providers,
        handlers=build_default_handlers(),
    )


def job_of(db, job_id) -> FetchJob:
    with db.read() as session:
        return session.get(FetchJob, job_id)


def test_a_queued_history_job_runs_and_stores_its_summary(committing_db):
    rig = seeded()
    with committing_db.write() as session:
        job, _ = queue.enqueue(session, FETCH_ADDRESS_HISTORY, {"chain": "ethereum", "address": ME})

    assert run(make_worker(committing_db, rig).run_once()) is True

    done = job_of(committing_db, job.id)
    assert done.status == "SUCCEEDED" and done.error is None
    assert done.result["transfers"] == 4 and done.result["complete"] is True


def test_a_history_job_with_an_impossible_payload_fails_at_once_and_says_why(committing_db):
    rig = seeded()
    with committing_db.write() as session:
        job, _ = queue.enqueue(
            session,
            FETCH_ADDRESS_HISTORY,
            {"chain": "ethereum", "address": "nonsense"},
            max_attempts=5,
        )

    run(make_worker(committing_db, rig).run_once())

    failed = job_of(committing_db, job.id)
    assert (failed.status, failed.attempts) == ("FAILED", 1)  # no retries for a bad address
    assert "not a valid ethereum address" in failed.error


def test_a_provider_outage_leaves_the_history_job_to_be_retried(committing_db):
    rig = seeded()
    rig.etherscan.queued = [
        {"status": "0", "message": "NOTOK", "result": "Query Timeout occured"}
    ] * 6
    rig.alchemy.queued = [
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "busy"}}
    ] * 6
    with committing_db.write() as session:
        job, _ = queue.enqueue(session, FETCH_ADDRESS_HISTORY, {"chain": "ethereum", "address": ME})

    run(make_worker(committing_db, rig).run_once())

    waiting = job_of(committing_db, job.id)
    assert (waiting.status, waiting.attempts) == ("QUEUED", 1)
