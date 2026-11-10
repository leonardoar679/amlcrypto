"""What a screening says when the providers behind it fail in part, or in whole, or lie.

The data providers are other people's servers. They go down, answer 200 with a web page, rate-limit
for an hour, reject a key, or serve a stream of transfers with one row that is not a row. The
platform's duty in every case is the same: never turn a failed or partial read into a report that
looks like a complete one. A total outage is retried, and failed with the reason once the attempts
are used up; a partial one gives a report that says what is missing, and a lower confidence; a
single bad row costs that row. No report may read as a complete look at an address that was only
partly looked at.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx2

from app.database.connection import Sessions
from app.database.repositories import risk as store
from app.providers.base import ProviderError
from app.risk.jobs import SCREEN_ADDRESS, screen_address_job
from app.services.users import create_user
from app.worker.context import JobContext, NonRetryable
from tests.fake_alchemy import asset_row
from tests.fake_etherscan import ME, OTHER, notok, tx_row
from tests.graph_store import label_address
from tests.helpers import run
from tests.test_sync_account import seeded

CHAIN = "eip155:1"
WINDOW = {"window_days": 3650}  # the fake chain's blocks are from November 2023


class Failing:
    """A fake provider that fails the requests `decide` picks and serves the others as usual."""

    def __init__(self, inner, decide: Callable[[httpx2.Request], object]) -> None:
        self.inner = inner
        self.decide = decide
        self.refused = 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        outcome = self.decide(request)
        if outcome is None:
            return await self.inner(request)
        self.refused += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def on(action: str, outcome):
    return lambda request: outcome if request.url.params.get("action") == action else None


def always(outcome):
    return lambda request: outcome


def server_error() -> httpx2.Response:
    return httpx2.Response(503, text="Service Unavailable")


def web_page() -> httpx2.Response:
    return httpx2.Response(200, text="<html><body>Down for maintenance</body></html>")


def rate_limited() -> httpx2.Response:
    return httpx2.Response(429, headers={"Retry-After": "7200"}, text="slow down")


def bad_key() -> httpx2.Response:
    return httpx2.Response(200, json=notok("Invalid API Key"))


def unauthorized() -> httpx2.Response:
    return httpx2.Response(401, text="Unauthorized")


def sessions(db) -> Sessions:
    return Sessions(db.read, db.write)


def request_screening(db) -> int:
    with db.write() as session:
        user = create_user(
            session, email="analyst@example.com", password="pw-pw-pw-pw-1", organization_name="Acme"
        )
        row = store.create_assessment(
            session,
            organization_id=user.organization_id,
            subject_type="address",
            chain_id=CHAIN,
            subject=ME,
            options=dict(WINDOW),
        )
        session.commit()
        return row.id


def context(rig, db, number, *, attempt=1, max_attempts=3) -> JobContext:
    return JobContext(
        job_id=1,
        job_type=SCREEN_ADDRESS,
        payload={"assessment_id": number},
        attempt=attempt,
        max_attempts=max_attempts,
        organization_id=None,
        providers=rig.providers,
        settings=rig.settings.model_copy(
            update={"graph_window_days": 3650, "graph_max_window_days": 3650}
        ),
        sessions=sessions(db),
    )


def row_of(db, number):
    with db.read() as session:
        row = store.get_assessment(session, number)
        session.expunge(row)
        return row


def run_job(db, rig, number, *, attempt=1, max_attempts=3):
    """What the job raised (or None), and the screening as it is kept afterwards."""
    raised = None
    try:
        run(
            screen_address_job(context(rig, db, number, attempt=attempt, max_attempts=max_attempts))
        )
    except (ProviderError, NonRetryable) as error:
        raised = error
    return raised, row_of(db, number)


def another_screening(db, number) -> int:
    with db.write() as session:
        first = store.get_assessment(session, number)
        row = store.create_assessment(
            session,
            organization_id=first.organization_id,
            subject_type="address",
            chain_id=CHAIN,
            subject=ME,
            options=dict(WINDOW, refresh=True),
        )
        session.commit()
        return row.id


def outage(rig, *, etherscan=None, alchemy=None):
    if etherscan is not None:
        rig.etherscan = Failing(rig.etherscan, etherscan)
    if alchemy is not None:
        rig.alchemy = Failing(rig.alchemy, alchemy)
    return rig


# --- part of a history cannot be read ----------------------------------------------------------


def test_a_kind_of_movement_that_cannot_be_read_stops_the_screening_and_is_picked_up_again(
    committing_db,
):
    """The token transfers are out of reach of both providers. Reporting on the rest would be a
    report on a history with a hole in it that looks whole, so the screening is not made: it is
    retried, and what was read meanwhile is kept, so the retry only has the hole to fill."""
    rig = outage(seeded(), etherscan=on("tokentx", server_error()), alchemy=always(server_error()))
    with committing_db.write() as session:
        label_address(session, OTHER, "SANCTIONS")
    number = request_screening(committing_db)

    raised, row = run_job(committing_db, rig, number, attempt=1)

    assert isinstance(raised, ProviderError) and raised.retryable
    assert "erc20" in str(raised)
    assert row.status == "PROCESSING" and row.report is None and row.error is None

    # the providers are back: the same job, on its next attempt, completes
    rig.etherscan = rig.etherscan.inner
    rig.alchemy = rig.alchemy.inner
    raised, row = run_job(committing_db, rig, number, attempt=2)
    assert raised is None and row.status == "COMPLETED"
    assert row.report["data_coverage"] == "COMPLETE"
    assert {finding["rule_id"] for finding in row.report["findings"]} >= {"AML-001"}


def test_a_provider_that_is_down_while_another_can_serve_costs_nothing_in_the_report(committing_db):
    rig = seeded()
    rig.alchemy.add(
        asset_row("external", 12, OTHER, ME, 7),
        asset_row("external", 10, ME, OTHER, 5),
        asset_row("internal", 10, OTHER, ME, 3),
    )
    outage(rig, etherscan=always(server_error()))
    with committing_db.write() as session:
        label_address(session, OTHER, "SANCTIONS")
    number = request_screening(committing_db)

    raised, row = run_job(committing_db, rig, number)

    assert raised is None and row.status == "COMPLETED"
    assert row.report["data_coverage"] == "COMPLETE"
    assert {f["rule_id"] for f in row.report["findings"]} >= {"AML-001"}
    assert rig.etherscan.refused > 0 and len(rig.alchemy.requests) > 0


# --- everything is out of reach -------------------------------------------------------------


def test_with_every_provider_down_the_job_is_retried_and_no_report_is_made_up(committing_db):
    rig = outage(seeded(), etherscan=always(server_error()), alchemy=always(server_error()))
    number = request_screening(committing_db)

    raised, row = run_job(committing_db, rig, number, attempt=1)

    assert isinstance(raised, ProviderError) and raised.retryable
    assert row.status == "PROCESSING" and row.report is None and row.error is None


def test_when_the_attempts_are_used_up_the_screening_fails_and_says_why(committing_db):
    rig = outage(seeded(), etherscan=always(server_error()), alchemy=always(server_error()))
    number = request_screening(committing_db)

    raised, row = run_job(committing_db, rig, number, attempt=3, max_attempts=3)

    assert isinstance(raised, ProviderError)
    assert row.status == "FAILED" and row.report is None
    assert "no provider could serve this" in row.error


def test_a_web_page_where_a_history_should_be_is_an_outage_and_not_an_empty_history(committing_db):
    """A 200 that is a page, not JSON, must not be read as 'no transactions': an address with a long
    history would be reported as one with none."""
    rig = outage(seeded(), etherscan=always(web_page()), alchemy=always(web_page()))
    number = request_screening(committing_db)

    raised, row = run_job(committing_db, rig, number)

    assert isinstance(raised, ProviderError) and raised.retryable
    assert row.report is None and row.status == "PROCESSING"


def test_a_provider_asking_to_wait_for_hours_is_waited_for_not_hammered_and_not_failed(
    committing_db,
):
    rig = outage(seeded(), etherscan=always(rate_limited()), alchemy=always(rate_limited()))
    number = request_screening(committing_db)

    raised, row = run_job(committing_db, rig, number)

    assert isinstance(raised, ProviderError) and raised.retryable
    assert row.status == "PROCESSING" and row.report is None
    # it asked once or twice to find out; it did not retry through the wait
    assert len(rig.etherscan.requests) + len(rig.alchemy.requests) < 12


def test_a_rejected_key_is_not_retried_and_the_key_is_not_in_the_reason(committing_db):
    rig = outage(seeded(), etherscan=always(bad_key()), alchemy=always(unauthorized()))
    number = request_screening(committing_db)

    raised, row = run_job(committing_db, rig, number, attempt=1)

    assert raised is not None and not raised.retryable
    assert row.status == "FAILED" and row.report is None
    for key in (rig.settings.etherscan_api_key, rig.settings.alchemy_api_key):
        text = key.get_secret_value() if hasattr(key, "get_secret_value") else str(key)
        assert text not in (row.error or "") and text not in str(raised)


# --- a provider that does not tell the truth ------------------------------------------------


def test_one_row_that_is_not_a_row_costs_that_row_and_the_report_says_the_history_has_a_hole(
    committing_db,
):
    rig = seeded()
    rig.etherscan.add("txlist", tx_row(14, OTHER, ME, value="not a number"))
    with committing_db.write() as session:
        label_address(session, OTHER, "SANCTIONS")
    number = request_screening(committing_db)

    raised, row = run_job(committing_db, rig, number)

    assert raised is None and row.status == "COMPLETED"
    report = row.report
    assert {f["rule_id"] for f in report["findings"]} >= {"AML-001"}, "the rest still counts"
    # the row that was skipped could have been the one that mattered, so the history is not whole
    assert report["data_coverage"] == "PARTIAL"
    (gap,) = [g for g in report["data_gaps"] if g["code"] == "rows_not_read"]
    assert "1 rows from etherscan could not be read and were skipped" in gap["message"]
    assert report["confidence"] < 0.9


# --- which notes mean that movements are missing ------------------------------------------------

LOST = [
    "3 rows from etherscan could not be read and were skipped",
    "2 rows from alchemy could not be read and were skipped",
    "5 rows from trongrid could not be read and were skipped",
    "1 transactions from esplora could not be read and were skipped",
    "1 transactions from helius could not be read and were skipped",
    "4 Transfer events in a non-ERC-20 shape were not read",
    "4 Transfer events in a non-TRC-20 shape were not read",
    "2 instructions move value in a shape this reader does not know",
    "7 transactions have inputs whose spent output esplora did not give; "
    "amounts sent in them may be short",
]
CONTEXT = [
    "the chain's current height is unknown (HTTP 503)",
    "confirmations are unknown: the chain's height could not be read",
    "token symbols and decimals are not in a receipt",
    "which outputs are spent is unknown: the spend list could not be read",
]


def coverage_with(notes):
    from datetime import UTC, datetime

    from app.database.repositories.acquisition import Coverage

    return {
        "account": Coverage(
            stream="account",
            status="COMPLETE",
            synced_at=datetime(2026, 11, 6, tzinfo=UTC),
            notes=tuple(notes),
        )
    }


def test_every_note_a_source_leaves_about_movements_it_lost_makes_a_gap():
    from app.graph.reader import judge

    for note in LOST:
        state, gaps = judge(coverage_with([note]), "ethereum:0xabc")
        assert [g.code for g in gaps] == ["rows_not_read"], note
        assert note in gaps[0].message
        # it says whose history has the hole
        assert gaps[0].node == "ethereum:0xabc"
        assert gaps[0].message.startswith("ethereum:0xabc: ")


def test_a_note_that_is_only_context_does_not_make_the_history_partial():
    from app.graph.reader import judge

    for note in CONTEXT:
        state, gaps = judge(coverage_with([note]), "ethereum:0xabc")
        assert gaps == [], note
        assert note in state.notes


def test_the_notes_the_sources_really_write_are_the_ones_listed_here():
    """The phrases above are copied from the sources; if one of them is reworded, the gap it is
    meant to raise would silently stop being raised, so look for them where they are written."""
    from pathlib import Path

    app = Path(__file__).resolve().parent.parent / "app"
    written = chr(10).join(p.read_text(encoding="utf-8") for p in app.rglob("*.py"))
    for phrase in (
        "could not be read and were skipped",
        "were not read",
        "shape this reader does not know",
        "amounts sent in them may be short",
    ):
        assert phrase in written, phrase
