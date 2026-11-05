"""GET /providers and POST /providers/{name}/probe."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx2
import pytest
from pydantic import SecretStr

from app.database.repositories.usage import record_usage
from app.providers.service import ProviderService
from app.providers.usage import DbUsageSink
from app.services.users import create_user
from tests.helpers import Reply, Server

PASSWORD = "correct horse battery staple"
ETHERSCAN_KEY = "etherscan-key-9f8e7d6c"
TRONGRID_KEY = "trongrid-key-1a2b3c4d"


@pytest.fixture
def settings(settings):
    """The shared test settings, plus two provider keys."""
    return settings.model_copy(
        update={
            "etherscan_api_key": SecretStr(ETHERSCAN_KEY),
            "trongrid_api_key": SecretStr(TRONGRID_KEY),
        }
    )


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
def fake_network(app, settings) -> Server:
    """Route the app's provider calls to a scripted server instead of the internet."""
    server = Server(Reply(json={"jsonrpc": "2.0", "id": 83, "result": "0x14e6a8f"}))
    app.state.providers = ProviderService.from_settings(
        settings,
        usage=DbUsageSink(lambda: app.state.write_session_factory),
        transport=httpx2.MockTransport(server),
    )
    return server


# --- access ------------------------------------------------------------------------------------


def test_both_endpoints_require_authentication(client):
    assert client.get("/api/v1/providers").status_code == 401
    assert client.post("/api/v1/providers/etherscan/probe").status_code == 401


# --- GET /providers ----------------------------------------------------------------------------


def test_providers_are_listed_with_their_configuration_and_limits(client, auth):
    response = client.get("/api/v1/providers", headers=auth)
    assert response.status_code == 200
    providers = {p["name"]: p for p in response.json()}

    assert list(providers) == ["etherscan", "alchemy", "trongrid", "helius", "esplora"]
    etherscan = providers["etherscan"]
    assert (etherscan["configured"], etherscan["needs_key"], etherscan["rate_limit_rps"]) == (
        True,
        True,
        3.0,
    )
    assert etherscan["chains"] == ["eip155:1"] and etherscan["paid_chains"] == ["eip155:56"]
    assert etherscan["circuit"] == "closed" and etherscan["last_error"] is None
    assert etherscan["usage_24h"] == {
        "requests": 0,
        "successes": 0,
        "errors": 0,
        "rate_limited": 0,
        "retries": 0,
    }
    assert providers["alchemy"]["configured"] is False
    assert providers["esplora"]["configured"] is True and providers["esplora"]["needs_key"] is False
    assert providers["trongrid"]["notes"]


def test_no_response_ever_contains_a_key(client, auth, fake_network):
    listing = client.get("/api/v1/providers", headers=auth)
    probe = client.post("/api/v1/providers/etherscan/probe", headers=auth)
    for response in (listing, probe):
        assert ETHERSCAN_KEY not in response.text and TRONGRID_KEY not in response.text


def test_usage_over_the_last_day_comes_from_the_database(client, auth, db_session):
    now = datetime.now(UTC)
    record_usage(db_session, provider="etherscan", at=now, successes=1)
    record_usage(db_session, provider="etherscan", at=now, errors=1, retries=1)
    record_usage(db_session, provider="trongrid", at=now, rate_limited=1)

    providers = {p["name"]: p for p in client.get("/api/v1/providers", headers=auth).json()}
    assert providers["etherscan"]["usage_24h"] == {
        "requests": 2,
        "successes": 1,
        "errors": 1,
        "rate_limited": 0,
        "retries": 1,
    }
    assert providers["trongrid"]["usage_24h"]["rate_limited"] == 1
    assert providers["helius"]["usage_24h"]["requests"] == 0


# --- POST /providers/{name}/probe --------------------------------------------------------------


def test_probing_a_provider_makes_one_request_and_counts_it(client, auth, fake_network, db_session):
    response = client.post("/api/v1/providers/etherscan/probe", headers=auth)

    assert response.status_code == 200
    body = response.json()
    assert (body["provider"], body["status"], body["ok"]) == ("etherscan", "ok", True)
    assert body["latency_ms"] is not None and body["checked_at"]
    assert len(fake_network.requests) == 1
    assert fake_network.requests[0].url.params["apikey"] == ETHERSCAN_KEY

    after = {p["name"]: p for p in client.get("/api/v1/providers", headers=auth).json()}
    assert after["etherscan"]["usage_24h"]["requests"] == 1
    assert after["etherscan"]["usage_24h"]["successes"] == 1


def test_a_failing_provider_is_reported_in_the_body_not_as_an_http_error(
    client, auth, fake_network
):
    fake_network.script[:] = [Reply(500)]
    response = client.post("/api/v1/providers/etherscan/probe", headers=auth)

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["ok"]) == ("unreachable", False)

    listing = {p["name"]: p for p in client.get("/api/v1/providers", headers=auth).json()}
    assert listing["etherscan"]["last_error"] == "HTTP 500"
    assert listing["etherscan"]["consecutive_failures"] == 1
    assert listing["etherscan"]["usage_24h"]["errors"] == 1


def test_probing_a_provider_without_a_key_reports_it_and_makes_no_request(
    client, auth, fake_network
):
    response = client.post("/api/v1/providers/alchemy/probe", headers=auth)
    assert response.json()["status"] == "not_configured"
    assert fake_network.requests == []


def test_probing_an_unknown_provider_is_a_404_in_the_standard_envelope(client, auth):
    response = client.post("/api/v1/providers/nope/probe", headers=auth)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "provider_not_found"
