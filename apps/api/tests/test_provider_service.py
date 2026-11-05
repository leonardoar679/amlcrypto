"""The provider service built from Settings: health probes, chain lookup, and usage counting."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx2
import pytest

from app.core.config import Settings
from app.database.repositories.usage import usage_since
from app.providers.base import AuthStyle, Probe, ProviderSpec
from app.providers.client import ProviderClient
from app.providers.registry import BITCOIN, BSC, ETHEREUM, SOLANA, TRON
from app.providers.service import ProviderService
from app.providers.usage import DbUsageSink
from tests.helpers import FakeClock, FullJitter, Reply, Server, run

KEYS = {
    "etherscan_api_key": "etherscan-key-0001",
    "alchemy_api_key": "alchemy-key-0002",
    "trongrid_api_key": "trongrid-key-0003",
    "helius_api_key": "helius-key-0004",
}
TEST_SECRET = "test-secret-key-that-is-comfortably-over-32-characters"


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, environment="test", secret_key=TEST_SECRET, **overrides)


def build(handler, *, usage=None, **overrides) -> ProviderService:
    clock = FakeClock()
    return ProviderService.from_settings(
        make_settings(**overrides),
        usage=usage,
        transport=httpx2.MockTransport(handler),
        clock=clock,
        sleep=clock.sleep,
        rng=FullJitter(),
    )


def healthy_everywhere(request: httpx2.Request) -> httpx2.Response:
    """What each provider's real endpoint answers when all is well."""
    host = request.url.host
    if host == "api.etherscan.io" or host.endswith("alchemy.com"):
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x14e6a8f"})
    if host == "api.trongrid.io":
        return httpx2.Response(200, json={"blockID": "00000000053aa2ee", "block_header": {}})
    if host == "mainnet.helius-rpc.com":
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": 412345678})
    if host == "blockstream.info":
        return httpx2.Response(200, text="974631")
    return httpx2.Response(404)


# --- probes ------------------------------------------------------------------------------------


def test_each_providers_probe_reaches_the_right_endpoint_with_the_right_credentials():
    seen: dict[str, httpx2.Request] = {}

    def handler(request):
        seen[request.url.host] = request
        return healthy_everywhere(request)

    service = build(handler, **KEYS)

    async def scenario():
        return [await service.probe(name) for name in service.names()]

    results = run(scenario())
    assert [(r.provider, r.status, r.ok) for r in results] == [
        (name, "ok", True) for name in ("etherscan", "alchemy", "trongrid", "helius", "esplora")
    ]

    etherscan = seen["api.etherscan.io"]
    assert etherscan.url.path == "/v2/api"
    assert dict(etherscan.url.params) == {
        "chainid": "1",
        "module": "proxy",
        "action": "eth_blockNumber",
        "apikey": KEYS["etherscan_api_key"],
    }
    alchemy = seen["eth-mainnet.g.alchemy.com"]
    assert alchemy.url.path == f"/v2/{KEYS['alchemy_api_key']}"
    assert json.loads(alchemy.content)["method"] == "eth_blockNumber"
    tron = seen["api.trongrid.io"]
    assert (tron.method, tron.url.path) == ("POST", "/wallet/getnowblock")
    assert tron.headers["tron-pro-api-key"] == KEYS["trongrid_api_key"]
    helius = seen["mainnet.helius-rpc.com"]
    assert helius.url.params["api-key"] == KEYS["helius_api_key"]
    assert json.loads(helius.content)["method"] == "getSlot"
    esplora = seen["blockstream.info"]
    assert (esplora.method, esplora.url.path) == ("GET", "/api/blocks/tip/height")


@pytest.mark.parametrize(
    ("answer", "status", "detail_contains"),
    [
        (Reply(json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}), "ok", "latest Ethereum block"),
        (
            Reply(json={"status": "0", "message": "NOTOK", "result": "Invalid API Key"}),
            "unhealthy",
            "check the API key",
        ),
        (Reply(401), "auth_failed", "HTTP 401"),
        (Reply(429, headers={"Retry-After": "120"}), "rate_limited", "retry in about 120s"),
        (Reply(500), "unreachable", "HTTP 500"),
        (httpx2.ConnectError("refused"), "unreachable", "ConnectError"),
        (httpx2.ReadTimeout("slow"), "unreachable", "timeout"),
        (Reply(404), "bad_response", "HTTP 404"),
    ],
)
def test_a_probe_says_what_went_wrong_in_words_a_person_can_act_on(answer, status, detail_contains):
    server = Server(answer)
    result = run(build(server, etherscan_api_key=KEYS["etherscan_api_key"]).probe("etherscan"))

    assert result.status == status and result.ok == (status == "ok")
    assert detail_contains in result.detail
    assert (
        len(server.requests) == 1
    )  # a health check is never retried: it reports, it does not heal
    assert KEYS["etherscan_api_key"] not in result.detail


def test_a_probe_reports_an_open_circuit_without_calling_the_provider():
    server = Server(Reply(500))
    service = build(
        server, etherscan_api_key=KEYS["etherscan_api_key"], provider_breaker_threshold=1
    )

    async def scenario():
        return await service.probe("etherscan"), await service.probe("etherscan")

    first, second = run(scenario())
    assert (first.status, second.status) == ("unreachable", "circuit_open")
    assert "retry in about 30s" in second.detail
    assert len(server.requests) == 1


def test_a_provider_without_a_key_is_reported_not_called():
    server = Server()
    result = run(build(server).probe("etherscan"))
    assert (result.status, result.ok) == ("not_configured", False)
    assert server.requests == []


def test_a_provider_with_no_health_request_defined_says_so():
    bare = ProviderSpec(
        name="bare", display_name="Bare", base_url="https://bare.test", auth=AuthStyle.NONE
    )
    service = ProviderService(ProviderClient({"bare": bare}, {}))
    assert run(service.probe("bare")).status == "no_probe"


def test_a_probe_check_that_blows_up_counts_as_unhealthy_instead_of_failing_the_request():
    fragile = ProviderSpec(
        name="fragile",
        display_name="Fragile",
        base_url="https://fragile.test",
        auth=AuthStyle.NONE,
        probe=Probe("GET", check=lambda data: data["missing"], describe="a thing"),
    )
    client = ProviderClient(
        {"fragile": fragile}, {}, transport=httpx2.MockTransport(Server(Reply(json={"x": 1})))
    )
    assert run(ProviderService(client).probe("fragile")).status == "unhealthy"


def test_probing_an_unknown_provider_is_an_error():
    with pytest.raises(ValueError, match="unknown provider"):
        run(build(Server()).probe("nope"))


# --- choosing providers ------------------------------------------------------------------------


def test_providers_for_a_chain_are_the_configured_ones_in_catalogue_order():
    nothing = build(Server())
    assert nothing.providers_for_chain(ETHEREUM) == []
    assert nothing.providers_for_chain(BITCOIN) == ["esplora"]  # keyless, so always available

    service = build(Server(), **KEYS)
    assert service.providers_for_chain(ETHEREUM) == ["etherscan", "alchemy"]
    assert service.providers_for_chain(TRON) == ["trongrid"]
    assert service.providers_for_chain(SOLANA) == ["alchemy", "helius"]


def test_paid_chains_are_only_offered_when_asked_for():
    service = build(Server(), etherscan_api_key=KEYS["etherscan_api_key"])
    assert service.providers_for_chain(BSC) == []
    assert service.providers_for_chain(BSC, include_paid=True) == ["etherscan"]


# --- configuration reaches the client ----------------------------------------------------------


def test_rate_limits_and_defaults_from_settings_are_applied():
    service = build(Server(), provider_rate_limits={"etherscan": 2.5})
    rates = {status.name: status.rate_limit_rps for status in service.statuses()}
    assert rates == {
        "etherscan": 2.5,
        "alchemy": 5.0,
        "trongrid": 5.0,
        "helius": 2.0,
        "esplora": 1.0,
    }


def test_statuses_never_contain_a_key():
    service = build(Server(), **KEYS)
    assert not any(value in repr(service.statuses()) for value in KEYS.values())


# --- usage is counted in the database ----------------------------------------------------------


def test_every_network_attempt_is_counted_in_api_usage(committing_db):
    server = Server(Reply(503), Reply(json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}))
    service = build(
        server,
        usage=DbUsageSink(lambda: committing_db.write),
        etherscan_api_key=KEYS["etherscan_api_key"],
    )

    async def scenario():
        await service.client.request("etherscan", "GET", params={"chainid": "1"})

    run(scenario())

    with committing_db.read() as session:
        totals = usage_since(session, datetime.now(UTC) - timedelta(hours=1))
    etherscan = totals["etherscan"]
    assert (etherscan.requests, etherscan.successes, etherscan.errors, etherscan.retries) == (
        2,
        1,
        1,
        1,
    )
