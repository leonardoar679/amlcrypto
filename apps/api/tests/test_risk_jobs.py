"""The jobs that carry out a requested screening, and what becomes of it when they fail."""

from __future__ import annotations

import json

import pytest

from app.database.connection import Sessions
from app.database.repositories import risk as store
from app.providers.base import ProviderError, ProviderNotConfigured, ProviderUnavailable
from app.risk.jobs import (
    SCREEN_ADDRESS,
    SCREEN_TRANSACTION,
    screen_address_job,
    screen_transaction_job,
)
from app.services.users import create_user
from app.worker.context import JobContext, NonRetryable
from app.worker.handlers import build_default_handlers
from tests.fake_etherscan import ME, OTHER
from tests.graph_store import label_address
from tests.helpers import load_fixture, run
from tests.rigs import Rig
from tests.test_graph_service import seeded

CHAIN = "eip155:1"
LONG_AGO = {"window_days": 3650}  # the fake Etherscan's blocks are from November 2023


def sessions(db) -> Sessions:
    return Sessions(db.read, db.write)


def rig_with_window() -> Rig:
    rig = seeded()
    rig.settings = rig.settings.model_copy(
        update={"graph_window_days": 3650, "graph_max_window_days": 3650}
    )
    return rig


def assessment(db, *, kind="address", subject=ME, options=None) -> int:
    with db.write() as session:
        user = create_user(
            session, email="analyst@example.com", password="pw-pw-pw-pw-1", organization_name="Acme"
        )
        row = store.create_assessment(
            session,
            organization_id=user.organization_id,
            subject_type=kind,
            chain_id=CHAIN,
            subject=subject,
            options=options if options is not None else dict(LONG_AGO),
        )
        session.commit()
        return row.id


def context(
    rig, db, assessment_id, *, job_type=SCREEN_ADDRESS, attempt=1, max_attempts=3, **payload
):
    return JobContext(
        job_id=1,
        job_type=job_type,
        payload=payload or {"assessment_id": assessment_id},
        attempt=attempt,
        max_attempts=max_attempts,
        organization_id=None,
        providers=rig.providers,
        settings=rig.settings,
        sessions=sessions(db),
    )


def row_of(db, assessment_id):
    with db.read() as session:
        row = store.get_assessment(session, assessment_id)
        session.expunge(row)
        return row


def test_both_jobs_are_registered_with_the_worker():
    assert {SCREEN_ADDRESS, SCREEN_TRANSACTION} <= build_default_handlers().job_types()


# --- screening an address -----------------------------------------------------------------------


def test_a_queued_screening_is_carried_out_and_the_report_is_kept(committing_db):
    rig = rig_with_window()
    number = assessment(committing_db)
    with committing_db.write() as session:
        label_address(session, OTHER, "SANCTIONS")

    result = run(screen_address_job(context(rig, committing_db, number)))

    json.dumps(result)
    assert result["status"] == "COMPLETED" and result["screening_id"] == f"scr_{number}"
    assert result["risk_score"] == 100 and result["risk_level"] == "CRITICAL"
    assert result["outcome"] == "findings" and result["findings"] >= 1
    row = row_of(committing_db, number)
    assert row.status == "COMPLETED" and row.completed_at is not None and row.started_at is not None
    assert row.report["findings"][0]["rule_id"] == "AML-001"
    assert row.report["findings"][0]["transaction_hashes"]
    assert row.report["analysis"]["counterparties_checked"] >= 1
    with committing_db.read() as session:
        assert {f.rule_id for f in store.findings_of(session, number)} >= {"AML-001"}


def test_an_address_nobody_flags_is_a_report_with_no_findings_and_a_score_of_zero(committing_db):
    rig = rig_with_window()
    number = assessment(committing_db)

    result = run(screen_address_job(context(rig, committing_db, number)))

    assert result["risk_score"] == 0 and result["outcome"] == "no_findings"
    assert result["data_coverage"] == "COMPLETE"


def test_without_updating_only_what_is_stored_is_used_and_no_provider_is_asked(committing_db):
    rig = rig_with_window()
    number = assessment(committing_db, options={**LONG_AGO, "update": False})

    result = run(screen_address_job(context(rig, committing_db, number)))

    assert rig.etherscan.requests == [] and rig.alchemy.requests == []
    assert result["outcome"] == "insufficient_data" and result["risk_score"] is None
    assert result["data_coverage"] == "INSUFFICIENT"


def test_the_options_of_the_request_are_the_limits_of_the_graph(committing_db):
    rig = rig_with_window()
    number = assessment(committing_db, options={**LONG_AGO, "depth": 2, "per_node": 1})

    run(screen_address_job(context(rig, committing_db, number)))

    analysis = row_of(committing_db, number).report["analysis"]
    assert analysis["maximum_hops"] == 2 and analysis["expanded_addresses"] == 2


def test_running_a_finished_screening_again_does_nothing(committing_db):
    rig = rig_with_window()
    number = assessment(committing_db)
    run(screen_address_job(context(rig, committing_db, number)))
    asked = len(rig.etherscan.requests)
    first = row_of(committing_db, number).report

    again = run(screen_address_job(context(rig, committing_db, number)))

    assert again == {"screening_id": f"scr_{number}", "status": "COMPLETED"}
    assert len(rig.etherscan.requests) == asked and row_of(committing_db, number).report == first


def test_the_same_data_screened_twice_gives_the_same_findings(committing_db):
    rig = rig_with_window()
    with committing_db.write() as session:
        label_address(session, OTHER, "MIXER")
    first, second = assessment(committing_db), None
    run(screen_address_job(context(rig, committing_db, first)))
    with committing_db.write() as session:
        row = store.create_assessment(
            session,
            organization_id=row_of(committing_db, first).organization_id,
            subject_type="address",
            chain_id=CHAIN,
            subject=ME,
            options=dict(LONG_AGO),
        )
        session.commit()
        second = row.id
    run(screen_address_job(context(rig, committing_db, second)))

    one, two = (row_of(committing_db, n).report for n in (first, second))
    assert [f["rule_id"] for f in one["findings"]] == [f["rule_id"] for f in two["findings"]]
    assert one["risk_score"] == two["risk_score"] and one["confidence"] == two["confidence"]


# --- when it goes wrong -------------------------------------------------------------------------


def down(rig):
    rig.etherscan.queued = [ProviderUnavailable("etherscan", "HTTP 503")] * 60
    rig.alchemy.queued = [ProviderUnavailable("alchemy", "HTTP 503")] * 60


def test_a_provider_failure_with_attempts_left_leaves_the_screening_running_for_the_retry(
    committing_db,
):
    rig = rig_with_window()
    down(rig)
    number = assessment(committing_db)

    with pytest.raises(ProviderError):
        run(screen_address_job(context(rig, committing_db, number, attempt=1)))

    row = row_of(committing_db, number)
    assert row.status == "PROCESSING" and row.error is None and row.report is None


def test_a_provider_failure_on_the_last_attempt_fails_the_screening_and_says_why(committing_db):
    rig = rig_with_window()
    down(rig)
    number = assessment(committing_db)

    with pytest.raises(ProviderError):
        run(screen_address_job(context(rig, committing_db, number, attempt=3, max_attempts=3)))

    row = row_of(committing_db, number)
    assert row.status == "FAILED" and "503" in row.error and row.report is None


def test_a_failure_that_cannot_pass_fails_the_screening_at_once(committing_db, monkeypatch):
    rig = rig_with_window()
    number = assessment(committing_db)

    async def refused(*args, **kwargs):
        raise ProviderNotConfigured("etherscan", "no key")

    monkeypatch.setattr("app.risk.jobs.screen_address", refused)

    with pytest.raises(ProviderNotConfigured):
        run(screen_address_job(context(rig, committing_db, number, attempt=1)))

    assert row_of(committing_db, number).status == "FAILED"


def test_a_chain_with_no_provider_configured_fails_the_screening_and_says_which_key(committing_db):
    rig = Rig(etherscan_key=None, alchemy_key=None)
    number = assessment(committing_db)

    with pytest.raises(NonRetryable, match="ETHERSCAN_API_KEY"):
        run(screen_address_job(context(rig, committing_db, number)))

    row = row_of(committing_db, number)
    assert row.status == "FAILED" and "ETHERSCAN_API_KEY" in row.error


@pytest.mark.parametrize(
    "options,complaint",
    [
        ({"depth": 0}, "depth must be a positive integer"),
        ({"max_nodes": True}, "max_nodes must be a positive integer"),
        ({"direction": "up"}, 'direction must be "both"'),
        ({"update": "yes"}, "update must be true or false"),
        ({"refresh": 1}, "refresh must be true or false"),
    ],
)
def test_options_that_make_no_sense_fail_the_screening_without_a_request(
    committing_db, options, complaint
):
    rig = rig_with_window()
    number = assessment(committing_db, options=options)

    with pytest.raises(NonRetryable, match=complaint):
        run(screen_address_job(context(rig, committing_db, number)))

    assert row_of(committing_db, number).status == "FAILED"
    assert rig.etherscan.requests == []


@pytest.mark.parametrize(
    "payload", [{}, {"assessment_id": "1"}, {"assessment_id": True}, {"assessment_id": 10**6}]
)
def test_a_payload_that_names_no_screening_is_refused(committing_db, payload):
    rig = rig_with_window()
    assessment(committing_db)
    bare = JobContext(
        1, SCREEN_ADDRESS, payload, 1, 3, None, rig.providers, rig.settings, sessions(committing_db)
    )

    with pytest.raises(NonRetryable):
        run(screen_address_job(bare))


def test_a_job_does_not_screen_a_row_of_the_other_kind(committing_db):
    rig = rig_with_window()
    number = assessment(committing_db, kind="transaction", subject="0x" + "ab" * 32, options={})

    with pytest.raises(NonRetryable, match="no address screening"):
        run(screen_address_job(context(rig, committing_db, number)))

    assert row_of(committing_db, number).status == "QUEUED"


def test_a_worker_without_a_database_refuses(committing_db):
    rig = rig_with_window()
    number = assessment(committing_db)
    bare = JobContext(
        1, SCREEN_ADDRESS, {"assessment_id": number}, 1, 3, None, rig.providers, rig.settings
    )

    with pytest.raises(NonRetryable, match="no database"):
        run(screen_address_job(bare))


# --- screening a transaction --------------------------------------------------------------------


def transaction_rig():
    rig = Rig()
    data = load_fixture("evm/ethereum_erc20_single")
    tx_hash = data["tx"]["hash"]
    rig.etherscan.transactions[tx_hash] = data["tx"]
    rig.etherscan.receipts[tx_hash] = data["receipt"]
    return rig, tx_hash, data


def test_a_transaction_with_a_flagged_party_is_screened_and_the_report_kept(committing_db):
    rig, tx_hash, data = transaction_rig()
    number = assessment(committing_db, kind="transaction", subject=tx_hash, options={})
    with committing_db.write() as session:
        label_address(session, data["tx"]["to"].lower(), "SCAM")

    result = run(
        screen_transaction_job(context(rig, committing_db, number, job_type=SCREEN_TRANSACTION))
    )

    row = row_of(committing_db, number)
    assert result["status"] == "COMPLETED" and row.status == "COMPLETED"
    assert row.report["subject"]["type"] == "transaction" and row.report["subject"]["id"] == tx_hash
    assert [f["rule_id"] for f in row.report["findings"]] == ["AML-008"]
    assert row.report["findings"][0]["transaction_hashes"] == [tx_hash]
    assert row.report["analysis"]["parties_checked"] >= 2


def test_a_transaction_nobody_has_fails_the_screening_and_says_so(committing_db):
    rig = Rig()
    number = assessment(committing_db, kind="transaction", subject="0x" + "ab" * 32, options={})

    with pytest.raises(NonRetryable, match="no configured source has this transaction"):
        run(
            screen_transaction_job(context(rig, committing_db, number, job_type=SCREEN_TRANSACTION))
        )

    assert row_of(committing_db, number).status == "FAILED"


def test_a_malformed_hash_fails_the_screening_without_a_request_for_it(committing_db):
    rig = Rig()
    number = assessment(committing_db, kind="transaction", subject="0x1234", options={})

    with pytest.raises(NonRetryable, match="not a transaction"):
        run(
            screen_transaction_job(context(rig, committing_db, number, job_type=SCREEN_TRANSACTION))
        )

    row = row_of(committing_db, number)
    assert row.status == "FAILED" and rig.etherscan.requests == []


# --- pinned down by mutation checks -------------------------------------------------------------


def test_the_operators_rule_changes_apply_to_a_queued_screening(committing_db):
    rig = rig_with_window()
    number = assessment(committing_db)
    with committing_db.write() as session:
        store.set_rule_override(session, "AML-009", enabled=False, config=None)
        session.commit()

    run(screen_address_job(context(rig, committing_db, number)))

    rules = {r["rule_id"]: r for r in row_of(committing_db, number).report["rules"]}
    assert rules["AML-009"]["status"] == "not_evaluated"
    assert rules["AML-009"]["reason"].startswith("switched off")


def test_without_a_bridge_registry_the_bridge_rule_says_it_has_nothing_to_judge_by(committing_db):
    rig = rig_with_window()
    number = assessment(committing_db)

    run(screen_address_job(context(rig, committing_db, number)))

    rules = {r["rule_id"]: r for r in row_of(committing_db, number).report["rules"]}
    assert rules["AML-013"]["status"] == "not_evaluated"
    assert "no bridge is known" in rules["AML-013"]["reason"]


def test_a_failed_screening_is_not_run_again_by_a_retried_job(committing_db):
    rig = rig_with_window()
    number = assessment(committing_db)
    with committing_db.write() as session:
        store.fail_assessment(session, number, "gave up earlier")
        session.commit()

    result = run(screen_address_job(context(rig, committing_db, number)))

    assert result == {"screening_id": f"scr_{number}", "status": "FAILED"}
    assert rig.etherscan.requests == [] and row_of(committing_db, number).error == "gave up earlier"


def test_a_screening_on_a_chain_this_platform_does_not_read_is_failed(committing_db, monkeypatch):
    rig = rig_with_window()
    number = assessment(committing_db)
    monkeypatch.setattr("app.risk.jobs.find_chain", lambda name: None)

    with pytest.raises(NonRetryable, match="not a chain this platform reads"):
        run(screen_address_job(context(rig, committing_db, number)))

    assert row_of(committing_db, number).status == "FAILED"


# --- two identical requests at once -------------------------------------------------------------


def a_first_request(session):
    from app.chains.support import find_chain
    from app.risk import service

    organization = create_user(
        session, email="a@example.com", password="pw-pw-pw-pw-1", organization_name="Acme"
    ).organization_id
    request = dict(
        organization_id=organization,
        kind="address",
        chain=find_chain("ethereum"),
        subject=ME,
        options={"depth": 1},
    )
    winner, created = service.request_screening(session, **request)
    assert created
    return winner, request


def blind_to_the_winner_at_first(monkeypatch):
    """Make the next request not see the waiting screening when it first looks."""
    from app.risk import service

    real, looks = service._waiting, []

    def look(session, key):
        looks.append(key)
        return None if len(looks) == 1 else real(session, key)

    monkeypatch.setattr(service, "_waiting", look)


def count_of_screenings(session):
    from sqlalchemy import func, select

    from app.database.models import RiskAssessment

    return session.scalar(select(func.count()).select_from(RiskAssessment))


def test_a_request_that_only_finds_the_winner_at_the_insert_leaves_no_second_row(
    committing_db, monkeypatch
):
    from app.risk import service

    with committing_db.write() as session:
        winner, request = a_first_request(session)
        blind_to_the_winner_at_first(monkeypatch)

        again, created = service.request_screening(session, **request)
        session.commit()  # whatever the request left behind is kept

        assert not created and again.id == winner.id
        assert count_of_screenings(session) == 1


def test_a_request_the_database_refuses_at_the_insert_gets_the_winners_screening(
    committing_db, monkeypatch
):
    from sqlalchemy import select

    from app.database.models import FetchJob
    from app.risk import service

    with committing_db.write() as session:
        winner, request = a_first_request(session)
        blind_to_the_winner_at_first(monkeypatch)

        def refuse_the_duplicate(s, job_type, payload, **options):
            s.rollback()  # what enqueue does when the database refuses a duplicate key
            job = s.scalar(select(FetchJob).where(FetchJob.dedupe_key == options["dedupe_key"]))
            return job, False

        monkeypatch.setattr(service.queue, "enqueue", refuse_the_duplicate)

        again, created = service.request_screening(session, **request)

        assert not created and again.id == winner.id
        assert count_of_screenings(session) == 1
