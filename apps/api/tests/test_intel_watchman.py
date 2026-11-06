"""The Watchman client against an in-memory copy of its documented API.

Not run against a real Watchman, which needs Docker (see app/intel/watchman.py): these tests pin
down what this side does with the answers it is documented to give.
"""

from __future__ import annotations

import json

import httpx2
import pytest

from app.chains.registry import get_chain
from app.intel.watchman import CURRENCIES, WatchmanClient
from tests.helpers import run

ETH = get_chain("ethereum")
TRON = get_chain("tron")
BITCOIN = get_chain("bitcoin")
LAZARUS = "0x098b716b8aaf21512996dc57eb0615e2383e2f96"


def entity(address=LAZARUS, currency="ETH", **extra):
    return {
        "name": "LAZARUS GROUP",
        "entityType": "business",
        "sourceList": "us_ofac",
        "sourceID": "27307",
        "match": 1.0,
        "sanctionsInfo": {"programs": ["DPRK3"]},
        "cryptoAddresses": [{"currency": currency, "address": address}],
        **extra,
    }


def server(answer, requests=None):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if requests is not None:
            requests.append(request)
        return answer(request)

    return WatchmanClient("http://watchman:8084/", transport=httpx2.MockTransport(handler))


def ok(entities):
    return lambda request: httpx2.Response(200, json={"query": {}, "entities": entities})


def test_it_asks_for_the_address_under_each_currency_it_may_be_listed_as():
    seen = []
    client = server(ok([]), seen)

    run(client.check(ETH, LAZARUS))

    asked = sorted(r.url.params["cryptoAddress"] for r in seen)
    assert asked == sorted(f"{c}:{LAZARUS}" for c in CURRENCIES["ethereum"])
    assert all(r.url.path == "/v2/search" and r.method == "GET" for r in seen)
    assert {r.url.host for r in seen} == {"watchman"}


def test_a_hit_is_an_entity_that_really_lists_the_address_with_what_watchman_says_about_it():
    check = run(server(ok([entity()])).check(ETH, LAZARUS))

    assert (
        check.available and len(check.hits) == 1
    )  # the same entity under several currencies: once
    hit = check.hits[0]
    assert (hit["name"], hit["source_list"], hit["source_id"], hit["match"]) == (
        "LAZARUS GROUP",
        "us_ofac",
        "27307",
        1.0,
    )
    assert hit["programs"] == ["DPRK3"] and hit["currencies"] == ["ETH"]


def test_a_result_that_does_not_list_the_address_is_not_a_hit_however_it_scored():
    near = entity(address="0x" + "11" * 20, match=0.97)

    check = run(server(ok([near])).check(ETH, LAZARUS))

    assert check.available and check.hits == ()


def test_evm_addresses_match_whatever_the_case_but_base58_addresses_do_not():
    shouty = entity(address=LAZARUS.upper().replace("0X", "0x"))
    assert run(server(ok([shouty])).check(ETH, LAZARUS)).hits

    tron = "TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE"
    assert run(server(ok([entity(tron, "TRX")])).check(TRON, tron)).hits
    assert not run(server(ok([entity(tron.lower(), "TRX")])).check(TRON, tron)).hits


def test_an_unreachable_or_broken_watchman_is_said_not_raised():
    def down(request):
        raise httpx2.ConnectError("no route to watchman")

    for answer in (
        down,
        lambda r: httpx2.Response(500),
        lambda r: httpx2.Response(200, text="<html>"),
        lambda r: httpx2.Response(200, json={"oops": 1}),
    ):
        check = run(server(answer).check(ETH, LAZARUS))
        assert not check.available and check.hits == () and check.error


def test_a_chain_with_no_currencies_asks_nothing():
    seen = []
    chain = type("Unlisted", (), {"slug": "nowhere", "family": ETH.family})()

    check = run(server(ok([]), seen).check(chain, "x"))

    assert check.available and seen == []


def test_the_lists_it_holds_can_be_read():
    body = {
        "lists": {"us_ofac": 19555},
        "startedAt": "2026-11-05T00:00:00Z",
        "endedAt": "2026-11-05T00:01:00Z",
    }
    client = server(lambda r: httpx2.Response(200, json=body))

    assert run(client.list_info()) == body


def test_bitcoin_is_asked_about_under_its_own_and_tethers_currency():
    assert set(CURRENCIES["bitcoin"]) == {"XBT", "USDT"}
    assert json.dumps(CURRENCIES)  # plain data
    assert BITCOIN.slug in CURRENCIES


@pytest.mark.parametrize("slug", ["ethereum", "bsc", "tron", "bitcoin", "solana"])
def test_every_chain_the_platform_reads_has_currencies_to_ask_about(slug):
    assert CURRENCIES[slug]
