"""GET /graph: the bounded graph, from stored histories, in the shape the UI draws."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.chains.models import StreamStatus
from app.database.connection import Sessions
from app.graph.bridges import BridgeRegistry
from app.intel.importer import refresh_source
from app.services.users import create_user
from tests.graph_helpers import addr
from tests.graph_store import move, seed
from tests.helpers import run

PASSWORD = "correct horse battery staple"
SDN_SAMPLE = Path(__file__).parent / "fixtures" / "intel" / "sdn_sample.xml"
LAZARUS = "0x098b716b8aaf21512996dc57eb0615e2383e2f96"  # on OFAC's list (see the sample)
ME, FRIEND, FOE = addr(0xA1), addr(0xA2), addr(0xA3)


@pytest.fixture
def auth(client, db_session) -> dict[str, str]:
    create_user(
        db_session, email="analyst@example.com", password=PASSWORD, organization_name="Acme"
    )
    token = client.post(
        "/api/v1/auth/login", json={"email": "analyst@example.com", "password": PASSWORD}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def graph(client, auth, address=ME, **params):
    return client.get(
        "/api/v1/graph", params={"chain": "ethereum", "address": address, **params}, headers=auth
    )


def test_the_endpoint_requires_authentication(client):
    response = client.get("/api/v1/graph", params={"chain": "ethereum", "address": ME})

    assert response.status_code == 401


def test_a_stored_history_comes_back_as_nodes_and_edges(client, auth, db_session):
    seed(db_session, ME, [move(FRIEND, ME, str(3 * 10**18)), move(ME, FOE, str(10**18))])

    response = graph(client, auth)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True and body["complete"] is True and body["gaps"] == []
    assert body["root"] == f"ethereum:{ME}"
    assert [n["id"] for n in body["nodes"]][0] == f"ethereum:{ME}"
    assert {n["id"] for n in body["nodes"]} == {f"ethereum:{a}" for a in (ME, FRIEND, FOE)}
    edges = {(e["source"], e["target"]): e for e in body["edges"]}
    paid_me = edges[(f"ethereum:{FRIEND}", f"ethereum:{ME}")]
    assert paid_me["asset"] == "ETH" and paid_me["amount"] == "3" and paid_me["transfers"] == 1
    assert paid_me["basis"] == "exact" and paid_me["transaction_hash"].startswith("0x")
    assert body["scope"]["depth"] == 1 and body["scope"]["window_days"] == 365
    assert body["requests_used"] == 0  # a request never reaches a provider


def test_an_address_that_was_never_read_says_so_instead_of_looking_empty(client, auth):
    body = graph(client, auth).json()

    assert body["complete"] is False
    assert [g["code"] for g in body["gaps"]] == ["history_not_read"]
    assert body["nodes"][0]["coverage"]["state"] == "not_read"


def test_a_partly_read_history_is_reported_as_a_gap(client, auth, db_session):
    seed(db_session, ME, [move(FRIEND, ME)], status=StreamStatus.PARTIAL)

    body = graph(client, auth).json()

    assert body["complete"] is False and body["gaps"][0]["code"] == "history_partial"
    assert body["edges"]  # what was read is still shown


def test_a_sanctioned_counterparty_is_labelled_and_makes_an_exposure(
    client, auth, db_session, tmp_path
):
    state = client.app.state
    run(
        refresh_source(
            Sessions(state.session_factory, state.write_session_factory),
            data_dir=tmp_path,
            file=SDN_SAMPLE,
        )
    )
    seed(db_session, ME, [move(LAZARUS, ME, str(10**18))])

    body = graph(client, auth).json()

    node = next(n for n in body["nodes"] if n["id"] == f"ethereum:{LAZARUS}")
    assert node["label"] == "LAZARUS GROUP" and node["labels"][0]["category"] == "SANCTIONS"
    assert node["labels"][0]["source"] == "ofac_sdn" and node["labels"][0]["confidence"] == 100
    (exposure,) = body["exposures"]
    assert exposure["direction"] == "incoming" and exposure["hops"] == 1
    assert exposure["target"] == f"ethereum:{LAZARUS}" and exposure["risk_categories"] == [
        "SANCTIONS"
    ]
    assert node["risk_level"] is None  # scoring is a later milestone: evidence, not a verdict
    assert body["sources"][0]["slug"] == "ofac_sdn"


def test_the_limits_in_a_request_can_only_tighten_the_configured_ones(client, auth, db_session):
    seed(db_session, ME, [move(addr(500 + i), ME) for i in range(5)])

    body = graph(client, auth, depth=6, counterparties=2, max_nodes=3, days=7).json()

    scope = body["scope"]
    assert scope["depth"] == 3  # GRAPH_MAX_DEPTH
    assert scope["counterparties_per_node"] == 2 and scope["max_nodes"] == 3
    assert scope["window_days"] == 7
    assert len(body["nodes"]) == 3
    assert body["complete"] is False
    assert {g["code"] for g in body["gaps"]} >= {"counterparties_not_followed"}


def test_a_limit_above_the_configured_one_is_brought_down_to_it(client, auth):
    body = graph(client, auth, max_nodes=10**6, max_edges=10**6, counterparties=10**6).json()

    assert body["scope"]["max_nodes"] == 500 and body["scope"]["max_edges"] == 2000
    assert body["scope"]["counterparties_per_node"] == 25


def test_the_direction_can_be_chosen(client, auth, db_session):
    seed(db_session, ME, [move(FRIEND, ME), move(ME, FOE)])

    arrived = graph(client, auth, direction="in").json()
    left = graph(client, auth, direction="out").json()

    assert [n["address"].lower() for n in arrived["nodes"]] == [ME, FRIEND]
    assert [n["address"].lower() for n in left["nodes"]] == [ME, FOE]
    assert arrived["scope"]["direction"] == "in"


def test_a_bridge_contract_from_the_registry_is_marked(client, auth, db_session):
    client.app.state.bridges = BridgeRegistry.from_dict(
        {"bridges": [{"name": "Example Bridge", "contracts": [
            {"chain": "ethereum", "address": FOE, "role": "deposit"}]}]}
    )  # fmt: skip
    seed(db_session, ME, [move(ME, FOE)])

    body = graph(client, auth).json()

    marked = {n["id"]: n["bridge"] for n in body["nodes"]}
    assert marked[f"ethereum:{FOE}"] == "Example Bridge" and marked[f"ethereum:{ME}"] is None


def test_a_bad_address_or_chain_is_refused_in_the_usual_envelope(client, auth):
    bad = graph(client, auth, address="0x123")
    chain = client.get("/api/v1/graph", params={"chain": "dogecoin", "address": ME}, headers=auth)
    nonsense = graph(client, auth, depth=0)
    sideways = graph(client, auth, direction="sideways")

    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_address"
    assert chain.status_code == 404 and chain.json()["error"]["code"] == "unknown_chain"
    assert nonsense.status_code == 422 and sideways.status_code == 422


def test_the_response_says_what_a_graph_does_and_does_not_prove(client, auth):
    notes = " ".join(graph(client, auth).json()["notes"])

    assert "not that they share an owner" in notes and "not proof" in notes


def test_the_openapi_description_lists_the_endpoint(client):
    assert "/api/v1/graph" in client.get("/api/v1/openapi.json").json()["paths"]
