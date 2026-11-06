"""The address intelligence endpoints: who says what about an address, and how current that is."""

from __future__ import annotations

from pathlib import Path

import httpx2
import pytest

from app.database.connection import Sessions
from app.intel.importer import refresh_source
from app.intel.watchman import WatchmanClient
from app.services.users import create_user
from tests.helpers import run

PASSWORD = "correct horse battery staple"
FIXTURE = Path(__file__).parent / "fixtures" / "intel" / "sdn_sample.xml"
LAZARUS = "0x098B716B8Aaf21512996dC57EB0615e2383E2f96"
NOBODY = "0x" + "ab" * 20


@pytest.fixture
def auth(client, db_session) -> dict[str, str]:
    create_user(
        db_session, email="analyst@example.com", password=PASSWORD, organization_name="Acme"
    )
    token = client.post(
        "/api/v1/auth/login", json={"email": "analyst@example.com", "password": PASSWORD}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def imported(client, tmp_path):
    state = client.app.state
    run(
        refresh_source(
            Sessions(state.session_factory, state.write_session_factory),
            data_dir=tmp_path,
            file=FIXTURE,
        )
    )


def ask(client, auth, address, chain="ethereum"):
    return client.post(
        "/api/v1/intel/lookup", json={"chain": chain, "address": address}, headers=auth
    )


def test_both_endpoints_require_authentication(client):
    assert (
        client.post(
            "/api/v1/intel/lookup", json={"chain": "ethereum", "address": NOBODY}
        ).status_code
        == 401
    )
    assert client.get("/api/v1/intel/sources").status_code == 401


def test_a_sanctioned_address_comes_back_with_its_source_confidence_and_an_explanation(
    client, auth, imported
):
    response = ask(client, auth, LAZARUS)

    assert response.status_code == 200
    body = response.json()
    assert body["known"] is True and body["confidence"] == 100 and body["sources_stale"] is False
    (label,) = body["labels"]
    assert label["category"] == "SANCTIONS" and label["entity"]["name"] == "LAZARUS GROUP"
    assert label["programs"] == ["DPRK3"] and label["listed_as"] == "ETH"
    assert label["source"]["slug"] == "ofac_sdn" and label["source"]["kind"] == "official"
    assert label["source_url"].endswith("Details.aspx?id=27307")
    assert "LAZARUS GROUP" in label["explanation"] and "OFAC" in body["explanation"]
    assert body["watchman"] is None


def test_an_unknown_address_is_answered_with_what_was_checked(client, auth, imported):
    body = ask(client, auth, NOBODY).json()

    assert body["known"] is False and body["labels"] == [] and body["confidence"] == 0
    assert body["sources"][0]["freshness"] == "fresh"
    assert "not proof" in body["explanation"]


def test_an_address_before_any_list_was_fetched_says_nothing_was_checked(client, auth):
    body = ask(client, auth, NOBODY).json()

    assert body["sources"][0]["freshness"] == "never" and body["sources_stale"] is True


def test_a_bad_address_or_chain_is_refused_in_the_usual_envelope(client, auth):
    bad = ask(client, auth, "0x123")
    chain = ask(client, auth, NOBODY, chain="dogecoin")

    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_address"
    assert chain.status_code == 404 and chain.json()["error"]["code"] == "unknown_chain"


def test_the_sources_are_listed_with_their_freshness(client, auth, imported):
    (source,) = client.get("/api/v1/intel/sources", headers=auth).json()

    assert source["slug"] == "ofac_sdn" and source["freshness"] == "fresh"
    assert source["publish_date"] == "2026-11-05" and source["status"] == "ok"


def watchman(answer):
    return WatchmanClient("http://watchman:8084", transport=httpx2.MockTransport(answer))


def test_a_configured_watchman_adds_its_opinion(client, auth, imported):
    hit = {
        "name": "LAZARUS GROUP",
        "sourceList": "us_ofac",
        "sourceID": "27307",
        "match": 1.0,
        "cryptoAddresses": [{"currency": "ETH", "address": LAZARUS.lower()}],
    }
    client.app.state.watchman = watchman(lambda r: httpx2.Response(200, json={"entities": [hit]}))

    body = ask(client, auth, LAZARUS).json()

    assert body["watchman"]["available"] is True
    assert body["watchman"]["hits"][0]["source_id"] == "27307"
    assert "Watchman second opinion: 1 hit" in body["explanation"]


def test_a_watchman_that_is_down_is_said_and_does_not_fail_the_lookup(client, auth, imported):
    def down(request):
        raise httpx2.ConnectError("no route")

    client.app.state.watchman = watchman(down)

    response = ask(client, auth, LAZARUS)

    assert response.status_code == 200 and response.json()["known"] is True
    assert response.json()["watchman"]["available"] is False
    assert "unavailable" in response.json()["explanation"]
