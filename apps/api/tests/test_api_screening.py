"""The screening endpoints: request a screening, read the report, its history, and the rules."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.database.connection import Sessions
from app.database.models import FetchJob
from app.database.repositories import risk as store
from app.risk.jobs import SCREEN_ADDRESS, screen_address_job
from app.services.users import create_user
from app.worker.context import JobContext
from tests.graph_helpers import addr
from tests.graph_store import label_address, move, seed
from tests.helpers import run
from tests.rigs import Rig

PASSWORD = "correct horse battery staple"
ME, BAD, OTHER = addr(0xA1), addr(0xB1), addr(0xC1)


def login(client, db_session, email, organization):
    create_user(db_session, email=email, password=PASSWORD, organization_name=organization)
    token = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth(client, db_session) -> dict[str, str]:
    return login(client, db_session, "analyst@example.com", "Acme")


@pytest.fixture
def rival(client, db_session, auth) -> dict[str, str]:
    return login(client, db_session, "spy@example.com", "Rival")


def screen(client, auth, address=ME, **options):
    return client.post(
        "/api/v1/screen/address",
        json={"chain": "ethereum", "address": address, "options": {"update": False, **options}},
        headers=auth,
    )


def work(client, screening_id):
    """What the worker does with the queued screening."""
    state = client.app.state
    context = JobContext(
        job_id=1,
        job_type=SCREEN_ADDRESS,
        payload={"assessment_id": int(screening_id.removeprefix("scr_"))},
        attempt=1,
        max_attempts=3,
        organization_id=None,
        providers=Rig().providers,
        settings=state.settings,
        sessions=Sessions(state.session_factory, state.write_session_factory),
    )
    return run(screen_address_job(context))


def test_every_endpoint_requires_authentication(client):
    calls = [
        client.post("/api/v1/screen/address", json={"chain": "ethereum", "address": ME}),
        client.post("/api/v1/screen/transaction", json={"chain": "ethereum", "hash": "0x1"}),
        client.get("/api/v1/screen/scr_1"),
        client.get(f"/api/v1/address/ethereum/{ME}/risk"),
        client.get(f"/api/v1/address/ethereum/{ME}/risk/history"),
        client.get("/api/v1/risk/rules"),
    ]

    assert [call.status_code for call in calls] == [401] * 6


# --- asking for a screening ---------------------------------------------------------------------


def test_a_request_records_the_screening_and_queues_the_job(client, auth, db_session):
    response = screen(client, auth, max_hops=2, historical_days=30)

    assert response.status_code == 202
    body = response.json()
    assert (
        body["success"] is True
        and body["status"] == "QUEUED"
        and body["already_requested"] is False
    )
    assert body["screening_id"].startswith("scr_")

    progress = client.get(f"/api/v1/screen/{body['screening_id']}", headers=auth).json()
    assert progress["status"] == "QUEUED" and progress["report"] is None
    assert progress["subject"] == {
        "type": "address",
        "chain": "ethereum",
        "chain_id": "eip155:1",
        "id": ME,
    }
    # The options as the job will read them: the blueprint's names mapped to the graph's.
    assert progress["options"] == {
        "depth": 2,
        "window_days": 30,
        "direction": "both",
        "update": False,
        "refresh": False,
    }
    job = db_session.scalar(select(FetchJob).where(FetchJob.job_type == "ADDRESS_SCREENING"))
    assert job.payload == {"assessment_id": int(body["screening_id"].removeprefix("scr_"))}
    assert job.organization_id is not None


def test_asking_again_for_what_is_already_queued_returns_the_same_screening(client, auth):
    first = screen(client, auth, max_hops=2).json()
    again = screen(client, auth, max_hops=2).json()
    different = screen(client, auth, max_hops=3).json()

    assert again["screening_id"] == first["screening_id"] and again["already_requested"] is True
    assert different["screening_id"] != first["screening_id"]


def test_different_organizations_are_not_deduplicated_against_each_other(client, auth, rival):
    mine = screen(client, auth).json()
    theirs = screen(client, rival).json()

    assert mine["screening_id"] != theirs["screening_id"] and theirs["already_requested"] is False


def test_a_bad_address_a_bad_chain_and_nonsense_options_are_refused(client, auth):
    assert screen(client, auth, address="0x123").status_code == 422
    unknown = client.post(
        "/api/v1/screen/address", json={"chain": "dogecoin", "address": ME}, headers=auth
    )
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "unknown_chain"
    assert screen(client, auth, max_hops=0).status_code == 422
    assert screen(client, auth, direction="sideways").status_code == 422
    assert screen(client, auth, include_graph=True).status_code == 422  # not an option: say so


def test_a_transaction_is_queued_and_its_hash_normalized(client, auth):
    shout = "0X" + "AB" * 32

    response = client.post(
        "/api/v1/screen/transaction", json={"chain": "ethereum", "hash": shout}, headers=auth
    )

    assert response.status_code == 202
    progress = client.get(f"/api/v1/screen/{response.json()['screening_id']}", headers=auth).json()
    assert progress["subject"]["type"] == "transaction"
    assert progress["subject"]["id"] == "0x" + "ab" * 32


def test_a_solana_signature_keeps_its_case(client, auth):
    signature = "5" + "aB" * 43

    response = client.post(
        "/api/v1/screen/transaction", json={"chain": "solana", "hash": signature}, headers=auth
    )

    progress = client.get(f"/api/v1/screen/{response.json()['screening_id']}", headers=auth).json()
    assert progress["subject"]["id"] == signature


def test_a_transaction_on_an_unknown_chain_is_refused(client, auth):
    response = client.post(
        "/api/v1/screen/transaction", json={"chain": "dogecoin", "hash": "0x1"}, headers=auth
    )

    assert response.status_code == 404


# --- reading it ---------------------------------------------------------------------------------


def test_a_finished_screening_returns_its_report_and_it_is_the_latest_for_the_address(
    client, auth, db_session
):
    seed(db_session, ME, [move(BAD, ME, str(10**18))])
    label_address(db_session, BAD, "SANCTIONS")
    queued = screen(client, auth).json()["screening_id"]

    work(client, queued)
    done = client.get(f"/api/v1/screen/{queued}", headers=auth).json()

    assert done["status"] == "COMPLETED" and done["error"] is None
    report = done["report"]
    assert report["risk_score"] == 100 and report["risk_level"] == "CRITICAL"
    assert report["findings"][0]["rule_id"] == "AML-001"
    assert report["findings"][0]["sources"] == ["ofac_sdn"]
    assert report["data_coverage"] == "COMPLETE" and report["outcome"] == "findings"
    assert done["started_at"] and done["completed_at"]

    latest = client.get(f"/api/v1/address/ethereum/{ME}/risk", headers=auth).json()
    assert latest["screening_id"] == queued and latest["report"] == report


def test_the_history_lists_the_screenings_newest_first_and_only_finished_ones(
    client, auth, db_session
):
    seed(db_session, ME, [move(OTHER, ME)])
    first = screen(client, auth).json()["screening_id"]
    work(client, first)
    label_address(db_session, OTHER, "SCAM")
    second = screen(client, auth, historical_days=60).json()["screening_id"]
    work(client, second)
    screen(client, auth, historical_days=90)  # queued, not finished

    history = client.get(f"/api/v1/address/ethereum/{ME}/risk/history", headers=auth).json()

    assert [a["screening_id"] for a in history["assessments"]] == [second, first]
    newer, older = history["assessments"]
    assert older["risk_score"] == 0 and newer["risk_score"] > 0
    assert newer["data_coverage"] == "COMPLETE" and 0 < newer["confidence"] <= 1
    assert (
        client.get(f"/api/v1/address/ethereum/{ME}/risk/history?limit=1", headers=auth).json()[
            "assessments"
        ][0]["screening_id"]
        == second
    )
    assert (
        client.get(f"/api/v1/address/ethereum/{ME}/risk/history?limit=0", headers=auth).status_code
        == 422
    )


def test_an_address_never_screened_says_so(client, auth):
    response = client.get(f"/api/v1/address/ethereum/{ME}/risk", headers=auth)

    assert response.status_code == 404 and response.json()["error"]["code"] == "no_screening"
    assert client.get(f"/api/v1/address/ethereum/{ME}/risk/history", headers=auth).json() == {
        "success": True,
        "assessments": [],
    }


def test_a_screening_with_too_little_data_is_reported_as_such_with_no_score(
    client, auth, db_session
):
    queued = screen(client, auth).json()["screening_id"]  # nothing is stored for ME

    work(client, queued)

    report = client.get(f"/api/v1/screen/{queued}", headers=auth).json()["report"]
    assert report["outcome"] == "insufficient_data" and report["data_coverage"] == "INSUFFICIENT"
    assert report["risk_score"] is None and report["risk_level"] is None
    history = client.get(f"/api/v1/address/ethereum/{ME}/risk/history", headers=auth).json()
    assert history["assessments"][0]["risk_score"] is None


def test_another_organization_cannot_see_a_screening_or_an_address_history(
    client, auth, rival, db_session
):
    seed(db_session, ME, [move(BAD, ME)])
    label_address(db_session, BAD, "SANCTIONS")
    queued = screen(client, auth).json()["screening_id"]
    work(client, queued)

    assert client.get(f"/api/v1/screen/{queued}", headers=rival).status_code == 404
    assert client.get(f"/api/v1/address/ethereum/{ME}/risk", headers=rival).status_code == 404
    assert (
        client.get(f"/api/v1/address/ethereum/{ME}/risk/history", headers=rival).json()[
            "assessments"
        ]
        == []
    )
    assert client.get(f"/api/v1/screen/{queued}", headers=auth).status_code == 200


@pytest.mark.parametrize(
    "text", ["scr_", "scr_x", "scr_-1", "abc_1", "1", "scr_1_2", "scr_" + "9" * 30]
)
def test_a_screening_id_that_is_not_one_is_not_found(client, auth, text):
    response = client.get(f"/api/v1/screen/{text}", headers=auth)

    assert response.status_code == 404 and response.json()["error"]["code"] == "screening_not_found"


def test_an_id_that_does_not_exist_is_not_found(client, auth):
    assert client.get("/api/v1/screen/scr_424242", headers=auth).status_code == 404


def test_a_bad_address_or_chain_on_the_read_endpoints_is_refused(client, auth):
    assert client.get("/api/v1/address/ethereum/0x123/risk", headers=auth).status_code == 422
    assert client.get(f"/api/v1/address/dogecoin/{ME}/risk", headers=auth).status_code == 404


# --- the rules ----------------------------------------------------------------------------------


def test_the_rules_are_listed_with_their_configuration_and_overrides(client, auth, db_session):
    store.set_rule_override(db_session, "AML-004", enabled=False, config={"window_seconds": 600})
    db_session.commit()

    body = client.get("/api/v1/risk/rules", headers=auth).json()

    rules = {r["rule_id"]: r for r in body["rules"]}
    assert len(rules) == 15 and rules["AML-001"]["enabled"] is True
    assert rules["AML-004"]["enabled"] is False and rules["AML-004"]["overridden"] is True
    assert rules["AML-004"]["config"]["window_seconds"] == 600
    assert rules["AML-004"]["defaults"]["window_seconds"] == 3600
    assert rules["AML-006"]["supported_chains"] == ["bitcoin"]
    for field in ("rule_name", "description", "version", "severity", "factor"):
        assert rules["AML-001"][field]


def test_a_stored_override_that_no_longer_fits_the_rule_is_a_clear_server_error(
    client, auth, db_session
):
    store.set_rule_override(db_session, "AML-004", enabled=None, config={"gone": 1})
    db_session.commit()

    response = client.get("/api/v1/risk/rules", headers=auth)

    assert response.status_code == 500 and response.json()["error"]["code"] == "rule_configuration"


# --- pinned down by mutation checks -------------------------------------------------------------


def test_a_repeated_request_leaves_one_screening_not_two(client, auth, db_session):
    from app.database.models import RiskAssessment

    first = screen(client, auth, max_hops=2).json()
    screen(client, auth, max_hops=2)

    rows = db_session.scalars(select(RiskAssessment)).all()
    assert [f"scr_{row.id}" for row in rows] == [first["screening_id"]]


def test_the_screening_knows_the_job_that_will_do_it(client, auth, db_session):
    from app.database.models import RiskAssessment

    body = screen(client, auth).json()

    row = db_session.get(RiskAssessment, int(body["screening_id"].removeprefix("scr_")))
    job = db_session.scalar(select(FetchJob).where(FetchJob.job_type == "ADDRESS_SCREENING"))
    assert row.job_id == job.id


def test_a_transaction_screening_is_a_different_job_from_an_address_screening(
    client, auth, db_session
):
    client.post(
        "/api/v1/screen/transaction",
        json={"chain": "ethereum", "hash": "0x" + "ab" * 32},
        headers=auth,
    )

    job = db_session.scalar(select(FetchJob))
    assert job.job_type == "TRANSACTION_SCREENING"


def test_a_screening_id_with_non_ascii_digits_is_not_one():
    from app.risk.service import parse_screening_id

    assert parse_screening_id("scr_12") == 12
    assert parse_screening_id("scr_\u0661\u0662") is None  # Arabic-Indic digits one and two


def test_two_identical_requests_that_race_each_other_leave_one_screening(
    client, auth, db_session, monkeypatch
):
    """The second request looks, sees nothing waiting, and then loses the race at the insert."""
    from app.database.models import RiskAssessment
    from app.risk import service

    first = screen(client, auth, max_hops=2).json()["screening_id"]
    real = service._waiting
    looks = []

    def blind_the_first_time(session, key):
        looks.append(key)
        return None if len(looks) == 1 else real(session, key)

    monkeypatch.setattr(service, "_waiting", blind_the_first_time)

    second = screen(client, auth, max_hops=2).json()

    assert second["screening_id"] == first and second["already_requested"] is True
    assert db_session.scalars(select(RiskAssessment)).all() != []
    assert len(db_session.scalars(select(RiskAssessment)).all()) == 1
