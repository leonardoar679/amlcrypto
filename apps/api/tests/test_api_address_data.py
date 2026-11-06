"""What the platform holds about an address: its overview, its movements, and its labels."""

from __future__ import annotations

import pytest

from app.database.repositories import risk as store
from app.intel.model import Category
from app.services.users import create_user
from tests.graph_helpers import FakeLabels, addr, label
from tests.graph_store import label_address, move, seed
from tests.risk_helpers import ROOT, at, flow, screen

PASSWORD = "correct horse battery staple"
ME, BAD, FRIEND = addr(0xA1), addr(0xB1), addr(0xC1)
ONE_ETH = str(10**18)


@pytest.fixture
def auth(client, db_session) -> dict[str, str]:
    create_user(
        db_session, email="analyst@example.com", password=PASSWORD, organization_name="Acme"
    )
    token = client.post(
        "/api/v1/auth/login", json={"email": "analyst@example.com", "password": PASSWORD}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def get(client, auth, path, **params):
    return client.get(f"/api/v1/address/ethereum/{path}", params=params, headers=auth)


def test_every_endpoint_requires_authentication(client):
    for suffix in ("", "/transactions", "/labels"):
        assert client.get(f"/api/v1/address/ethereum/{ME}{suffix}").status_code == 401


# --- the overview -----------------------------------------------------------------------------


def test_an_address_never_read_says_so_instead_of_looking_empty(client, auth):
    body = get(client, auth, ME).json()

    assert body["success"] is True and body["chain"] == "ethereum" and body["address"] == ME
    assert body["history"]["state"] == "not_read" and body["activity"] is None
    assert [g["code"] for g in body["gaps"]] == ["history_not_read"]
    assert body["latest_screening"] is None and body["labels"]["known"] is False


def test_a_stored_history_is_summarized_and_the_labels_of_the_address_shown(
    client, auth, db_session
):
    seed(
        db_session,
        ME,
        [move(BAD, ME, ONE_ETH, days_ago=10), move(ME, FRIEND, ONE_ETH, days_ago=2)],
    )
    label_address(db_session, ME, "EXCHANGE")

    body = get(client, auth, ME).json()

    assert body["history"]["state"] == "complete" and body["gaps"] == []
    assert body["history"]["streams"] == {
        "account": "complete",
        "erc20": "complete",
        "internal": "complete",
    }
    activity = body["activity"]
    assert (activity["transfers_in"], activity["transfers_out"]) == (1, 1)
    assert (activity["counterparties_in"], activity["counterparties_out"]) == (1, 1)
    assert activity["first_seen"] < activity["last_seen"]
    assert body["labels"]["known"] is True and body["labels"]["labels"][0]["category"] == "EXCHANGE"
    assert body["display"].lower() == ME and body["address"] == ME  # shown checksummed


def test_a_differently_cased_address_is_the_same_address(client, auth, db_session):
    stored = "0x" + "a1" * 20
    seed(db_session, stored, [move(BAD, stored)])

    body = get(client, auth, "0x" + "A1" * 20).json()  # upper-case hex is accepted and normalized

    assert body["address"] == stored and body["history"]["state"] == "complete"


def test_the_organizations_latest_finished_screening_is_attached(client, auth, db_session):
    seed(db_session, ME, [move(BAD, ME)])
    label_address(db_session, BAD, "SANCTIONS")
    organization = auth_organization(db_session)
    row = store.create_assessment(
        db_session, organization_id=organization, subject_type="address", chain_id="eip155:1",
        subject=ME, options={},
    )  # fmt: skip
    store.complete_assessment(
        db_session,
        row.id,
        screen([flow(BAD, ROOT, 7, at(1))], labels=FakeLabels(label(BAD, Category.SANCTIONS))),
    )
    db_session.commit()

    latest = get(client, auth, ME).json()["latest_screening"]

    assert latest["screening_id"] == f"scr_{row.id}"
    assert latest["risk_score"] == 100 and latest["risk_level"] == "CRITICAL"
    assert latest["data_coverage"] == "COMPLETE" and 0 < latest["confidence"] <= 1


def auth_organization(db_session) -> int:
    from sqlalchemy import select

    from app.database.models import Organization

    return db_session.scalar(select(Organization.id))


def test_a_bad_address_or_chain_is_refused_in_the_usual_envelope(client, auth):
    assert get(client, auth, "0x123").status_code == 422
    assert get(client, auth, "0x123").json()["error"]["code"] == "invalid_address"
    unknown = client.get(f"/api/v1/address/dogecoin/{ME}", headers=auth)
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "unknown_chain"


# --- the movements -----------------------------------------------------------------------------


def history(db_session):
    seed(
        db_session,
        ME,
        [
            move(BAD, ME, ONE_ETH, days_ago=30),
            move(ME, FRIEND, str(2 * 10**18), days_ago=20),
            move(FRIEND, ME, str(3 * 10**17), days_ago=10),
            move(BAD, ME, "0", days_ago=5),  # address poisoning leaves these
        ],
    )
    label_address(db_session, BAD, "MIXER")


def test_the_movements_come_newest_first_with_their_counterparty_asset_and_amount(
    client, auth, db_session
):
    history(db_session)

    body = get(client, auth, f"{ME}/transactions").json()

    assert body["total"] == 3 and body["limit"] == 50 and body["offset"] == 0
    rows = body["items"]
    assert [(r["direction"], r["counterparty"]) for r in rows] == [
        ("in", FRIEND),
        ("out", FRIEND),
        ("in", BAD),
    ]
    newest = rows[0]
    assert newest["amount"] == "0.3" and newest["amount_raw"] == str(3 * 10**17)
    assert newest["asset"]["symbol"] == "ETH" and newest["asset"]["key"] == "native"
    assert newest["basis"] == "exact" and newest["transaction_hash"].startswith("0x")
    assert newest["timestamp"] > rows[1]["timestamp"] > rows[2]["timestamp"]
    assert body["ignored"] == {"zero_value": 1}
    assert body["coverage"]["state"] == "complete"


def test_the_counterparties_the_sources_flag_are_marked(client, auth, db_session):
    history(db_session)

    rows = get(client, auth, f"{ME}/transactions").json()["items"]

    flagged = {r["counterparty"]: r["counterparty_labels"] for r in rows}
    assert flagged[BAD][0]["category"] == "MIXER" and flagged[BAD][0]["source"] == "ofac_sdn"
    assert flagged[FRIEND] == []


def test_the_movements_can_be_paged_filtered_by_direction_and_limited_in_time(
    client, auth, db_session
):
    history(db_session)
    path = f"{ME}/transactions"

    page = get(client, auth, path, limit=1, offset=1).json()
    incoming = get(client, auth, path, direction="in").json()
    outgoing = get(client, auth, path, direction="out").json()
    recent = get(client, auth, path, days=15).json()

    assert page["total"] == 3 and [r["direction"] for r in page["items"]] == ["out"]
    assert incoming["total"] == 2 and {r["direction"] for r in incoming["items"]} == {"in"}
    assert outgoing["total"] == 1 and outgoing["items"][0]["counterparty"] == FRIEND
    assert recent["total"] == 1 and recent["items"][0]["counterparty"] == FRIEND


def test_nothing_stored_is_an_empty_list_that_says_why(client, auth):
    body = get(client, auth, f"{ME}/transactions").json()

    assert body["items"] == [] and body["total"] == 0
    assert body["coverage"]["state"] == "not_read"
    assert [g["code"] for g in body["gaps"]] == ["history_not_read"]


def test_nonsense_paging_is_refused(client, auth):
    path = f"{ME}/transactions"

    assert get(client, auth, path, limit=0).status_code == 422
    assert get(client, auth, path, limit=201).status_code == 422
    assert get(client, auth, path, offset=-1).status_code == 422
    assert get(client, auth, path, direction="sideways").status_code == 422
    assert get(client, auth, path, days=0).status_code == 422


# --- the labels --------------------------------------------------------------------------------


def test_the_labels_say_who_says_what_and_how_current_each_source_is(client, auth, db_session):
    label_address(db_session, BAD, "SANCTIONS")

    body = get(client, auth, f"{BAD}/labels").json()

    assert body["known"] is True and body["confidence"] == 100
    assert body["labels"][0]["category"] == "SANCTIONS"
    assert body["labels"][0]["source"]["slug"] == "ofac_sdn"
    assert body["sources"][0]["freshness"] == "fresh"


def test_an_unlabelled_address_comes_back_with_what_was_checked(client, auth):
    body = get(client, auth, f"{ME}/labels").json()

    assert body["known"] is False and body["labels"] == []
    assert body["sources"][0]["slug"] == "ofac_sdn" and "not proof" in body["explanation"]


def test_the_labels_of_a_bad_address_or_chain_are_refused(client, auth):
    assert get(client, auth, "0x123/labels").status_code == 422
    assert client.get(f"/api/v1/address/dogecoin/{ME}/labels", headers=auth).status_code == 404
