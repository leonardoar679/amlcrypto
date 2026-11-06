"""FETCH_COUNTERPARTIES: gathers the histories a graph needs and says what the graph looks like."""

from __future__ import annotations

import json

import pytest

from app.database.connection import Sessions
from app.database.repositories import jobs as queue
from app.graph.jobs import FETCH_COUNTERPARTIES, fetch_counterparties
from app.providers.base import ProviderError, ProviderUnavailable
from app.worker.context import JobContext, NonRetryable
from app.worker.handlers import build_default_handlers
from tests.fake_etherscan import ME, OTHER
from tests.helpers import run
from tests.rigs import Rig
from tests.test_graph_service import seeded


def context(rig: Rig, db, *, sessions=True, **payload) -> JobContext:
    with db.write() as session:
        job, _ = queue.enqueue(session, FETCH_COUNTERPARTIES, payload)
        job_id = job.id
    return JobContext(
        job_id=job_id,
        job_type=FETCH_COUNTERPARTIES,
        payload=payload,
        attempt=1,
        max_attempts=3,
        organization_id=None,
        providers=rig.providers,
        settings=rig.settings,
        sessions=Sessions(db.read, db.write) if sessions else None,
    )


def days(rig: Rig) -> Rig:
    # The fake Etherscan's blocks are from November 2023.
    rig.settings = rig.settings.model_copy(
        update={"graph_window_days": 3650, "graph_max_window_days": 3650}
    )
    return rig


def test_the_job_is_registered_with_the_worker():
    assert FETCH_COUNTERPARTIES in build_default_handlers().job_types()


def test_the_job_reads_the_histories_and_reports_the_graph_without_returning_it(committing_db):
    rig = days(seeded())

    result = run(
        fetch_counterparties(
            context(rig, committing_db, chain="ethereum", address=ME, depth=2, window_days=3650)
        )
    )

    json.dumps(result)
    assert result["complete"] is True and result["gaps"] == []
    assert result["nodes"] == 2 and result["expanded"] == 2 and result["edges"] >= 2
    assert result["requests_used"] > 0
    assert result["limits"]["depth"] == 2 and result["limits"]["direction"] == "both"
    assert "edges_list" not in result and "edge_list" not in result


def test_a_second_job_reuses_what_was_read_and_costs_nothing(committing_db):
    rig = days(seeded())
    payload = {"chain": "ethereum", "address": ME, "depth": 2, "window_days": 3650}
    run(fetch_counterparties(context(rig, committing_db, **payload)))
    asked = len(rig.etherscan.requests)

    again = run(fetch_counterparties(context(rig, committing_db, **payload)))

    assert again["requests_used"] == 0 and len(rig.etherscan.requests) == asked


def test_a_refresh_asks_again(committing_db):
    rig = days(seeded())
    payload = {"chain": "ethereum", "address": ME, "window_days": 3650}
    run(fetch_counterparties(context(rig, committing_db, **payload)))
    asked = len(rig.etherscan.requests)

    again = run(fetch_counterparties(context(rig, committing_db, refresh=True, **payload)))

    assert again["requests_used"] > 0 and len(rig.etherscan.requests) > asked


def test_the_limits_of_a_payload_only_tighten_the_configured_ones(committing_db):
    rig = days(seeded())

    result = run(
        fetch_counterparties(
            context(
                rig,
                committing_db,
                chain="ethereum",
                address=ME,
                depth=99,
                max_nodes=10**6,
                per_node=1,
                direction="in",
            )
        )
    )

    limits = result["limits"]
    assert limits["depth"] == 3 and limits["max_nodes"] == 500
    assert limits["counterparties_per_node"] == 1 and limits["direction"] == "in"


def test_a_root_the_providers_cannot_read_is_an_error_the_worker_can_retry(committing_db):
    rig = days(seeded())
    rig.etherscan.queued = [ProviderUnavailable("etherscan", "HTTP 503")] * 40
    rig.alchemy.queued = [ProviderUnavailable("alchemy", "HTTP 503")] * 40

    with pytest.raises(ProviderError):
        run(fetch_counterparties(context(rig, committing_db, chain="ethereum", address=ME)))


@pytest.mark.parametrize(
    "payload,complaint",
    [
        ({}, 'must name a "chain" and an "address"'),
        ({"chain": "ethereum"}, 'must name a "chain" and an "address"'),
        ({"chain": 1, "address": ME}, 'must name a "chain" and an "address"'),
        ({"chain": "dogecoin", "address": ME}, "not a chain this platform reads"),
        ({"chain": "ethereum", "address": "0x123"}, "not a valid ethereum address"),
        ({"chain": "ethereum", "address": ME, "depth": 0}, "depth must be a positive integer"),
        ({"chain": "ethereum", "address": ME, "depth": True}, "depth must be a positive integer"),
        ({"chain": "ethereum", "address": ME, "max_nodes": "9"}, "max_nodes must be a positive"),
        ({"chain": "ethereum", "address": ME, "direction": "up"}, 'direction must be "both"'),
        ({"chain": "ethereum", "address": ME, "refresh": "yes"}, "refresh must be true or false"),
    ],
)
def test_a_payload_that_cannot_work_is_refused_without_spending_a_request(
    committing_db, payload, complaint
):
    rig = days(seeded())

    with pytest.raises(NonRetryable, match=complaint):
        run(fetch_counterparties(context(rig, committing_db, **payload)))

    assert rig.etherscan.requests == [] and rig.alchemy.requests == []


def test_a_worker_without_a_database_refuses_the_job(committing_db):
    with pytest.raises(NonRetryable, match="no database"):
        run(
            fetch_counterparties(
                context(seeded(), committing_db, sessions=False, chain="ethereum", address=OTHER)
            )
        )


def test_a_bridge_registry_that_cannot_be_read_is_not_retried(committing_db, tmp_path):
    rig = days(seeded())
    broken = tmp_path / "bridges.json"
    broken.write_text("{")
    rig.settings = rig.settings.model_copy(update={"bridge_registry_file": str(broken)})

    with pytest.raises(NonRetryable, match="cannot read the bridge registry"):
        run(fetch_counterparties(context(rig, committing_db, chain="ethereum", address=ME)))
