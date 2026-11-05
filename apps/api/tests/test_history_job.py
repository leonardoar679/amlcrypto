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
        ({"chain": "dogecoin", "address": ME}, "not a chain this platform reads"),
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


# --- Bitcoin ------------------------------------------------------------------------------------


def _bitcoin(rig: Rig):
    from tests.fake_esplora import p2pkh, pay, spend, tx, txid

    me = p2pkh(1)
    rig.esplora.add(
        tx(
            1,
            [spend(txid(900), 0, p2pkh(2), 60_000)],
            [pay(me, 50_000), pay(p2pkh(2), 9_000)],
            height=101,
        ),
        tx(
            2, [spend(txid(1), 0, me, 50_000)], [pay(p2pkh(3), 30_000), pay(me, 19_000)], height=102
        ),
        tx(3, [spend(txid(901), 0, p2pkh(4), 8_000)], [pay(me, 7_000)], height=103),
    )
    return me


def test_the_job_reads_a_bitcoin_history_and_returns_a_json_ready_summary():
    rig = Rig()
    me = _bitcoin(rig)

    result = run(fetch_address_history(context(rig, chain="bitcoin", address=me)))

    json.dumps(result)
    assert result["complete"] is True and result["transactions"] == 3
    assert (result["received_raw"], result["sent_raw"]) == ("76000", "50000")
    assert result["sources"] == result["providers"] == ["esplora"]
    assert result["requests_used"] == len(rig.esplora.requests)
    assert result["chain_id"] == "bip122:000000000019d6689c085ae165831e93"


def test_a_bitcoin_payload_may_name_the_chain_by_id_and_limit_the_blocks():
    rig = Rig()
    me = _bitcoin(rig)
    result = run(
        fetch_address_history(
            context(
                rig, chain="bip122:000000000019d6689c085ae165831e93", address=me, from_block=102
            )
        )
    )
    assert result["transactions"] == 2 and result["first_block"] == 102


def test_a_bitcoin_job_that_stops_at_its_limit_hands_back_where_to_resume():
    rig = Rig()
    me = _bitcoin(rig)
    first = run(fetch_address_history(context(rig, chain="bitcoin", address=me, max_items=1)))
    assert first["complete"] is True  # three transactions fit on one page: the limit is per page

    from tests.fake_esplora import p2pkh, pay, spend, tx, txid

    rig.esplora.add(
        *[
            tx(
                n,
                [spend(txid(5000 + n), 0, p2pkh(9000 + n), 10_000)],
                [pay(me, 9_000)],
                height=200 + n,
            )
            for n in range(10, 40)
        ]
    )
    cut = run(fetch_address_history(context(rig, chain="bitcoin", address=me, max_items=1)))
    assert cut["complete"] is False and cut["resume"]["transactions"].startswith("esplora|")
    rest = run(
        fetch_address_history(context(rig, chain="bitcoin", address=me, resume=cut["resume"]))
    )
    assert rest["complete"] is True and rest["transactions"] + cut["transactions"] == 33


def test_a_bad_bitcoin_address_in_the_payload_is_rejected_for_good():
    with pytest.raises(NonRetryable, match="not a valid bitcoin address"):
        run(fetch_address_history(context(Rig(), chain="bitcoin", address="not-an-address")))


# --- Solana -------------------------------------------------------------------------------------


def _solana(rig: Rig):
    from tests.fake_helius import HEAD, address, transfer_item

    wallet = address(1)
    rig.helius.add(
        transfer_item(1, address(2), wallet, 2_000_000, slot=HEAD - 300),
        transfer_item(2, wallet, address(3), 500_000, slot=HEAD - 100),
    )
    return wallet


def test_the_job_reads_a_solana_history_and_returns_a_json_ready_summary():
    rig = Rig()
    wallet = _solana(rig)

    result = run(fetch_address_history(context(rig, chain="solana", address=wallet)))

    json.dumps(result)
    assert result["complete"] is True and result["transactions"] == 2
    assert result["wallet_transfers"] == 2 and result["transfers_by_type"] == {"native": 2}
    assert result["sources"] == result["providers"] == ["helius"]
    assert result["requests_used"] == len(rig.helius.requests)
    assert result["chain_id"] == "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"


def test_a_solana_payload_may_name_the_chain_by_id_and_limit_the_slots():
    from tests.fake_helius import HEAD

    rig = Rig()
    wallet = _solana(rig)
    result = run(
        fetch_address_history(
            context(
                rig,
                chain="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
                address=wallet,
                from_block=HEAD - 200,
            )
        )
    )
    assert result["transactions"] == 1 and result["first_block"] == HEAD - 100


def test_a_solana_job_that_stops_at_its_limit_hands_back_where_to_resume():
    from tests.fake_helius import HEAD, address, transfer_item

    rig = Rig()
    wallet = address(1)
    rig.helius.add(
        *[
            transfer_item(n, address(1000 + n), wallet, 5_000, slot=HEAD - 10_000 + n)
            for n in range(1, 251)
        ]
    )

    cut = run(fetch_address_history(context(rig, chain="solana", address=wallet, max_items=1)))
    assert cut["complete"] is False and cut["resume"]["transactions"].startswith("helius|")
    rest = run(
        fetch_address_history(context(rig, chain="solana", address=wallet, resume=cut["resume"]))
    )
    assert rest["complete"] is True and rest["transactions"] + cut["transactions"] == 250


def test_a_solana_job_without_a_key_or_with_a_bad_address_is_rejected_for_good():
    from tests.fake_helius import address

    unkeyed = Rig(helius_key=None)
    with pytest.raises(NonRetryable, match="HELIUS_API_KEY"):
        run(fetch_address_history(context(unkeyed, chain="solana", address=address(1))))
    with pytest.raises(NonRetryable, match="not a valid solana address"):
        run(fetch_address_history(context(Rig(), chain="solana", address="not-an-address")))
