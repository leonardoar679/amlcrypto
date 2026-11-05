"""The provider HTTP client: credentials, throttling, retries, the breaker, budgets and caching.

Nothing here touches the network: requests go to a scripted fake server through httpx2's
MockTransport, and time is a fake clock whose sleep() returns at once.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx2
import pytest
from pydantic import SecretStr

from app.core.redaction import secrets
from app.providers.base import (
    AuthStyle,
    BudgetExceeded,
    CircuitOpen,
    ProviderAuthError,
    ProviderBadResponse,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderSpec,
    ProviderUnavailable,
)
from app.providers.budget import RequestBudget
from app.providers.client import ProviderClient
from app.providers.usage import MemoryUsageSink
from tests.helpers import FakeClock, FullJitter, Reply, Server, run

KEY = "k3y-0123456789-abcdef"


def spec(name: str, base_url: str, auth: AuthStyle, auth_name: str | None = None, **kwargs):
    return ProviderSpec(
        name=name,
        display_name=name.title(),
        base_url=base_url,
        auth=auth,
        auth_name=auth_name,
        key_setting=None if auth is AuthStyle.NONE else f"{name}_key",
        chains=("test:1",),
        **kwargs,
    )


QUERY = spec("query", "https://query.test/api", AuthStyle.QUERY, "apikey")
HEADER = spec("header", "https://header.test", AuthStyle.HEADER, "X-Api-Key")
PATH = spec("path", "https://path.test/v2/{key}", AuthStyle.PATH)
OPEN = spec("open", "https://open.test", AuthStyle.NONE)
SPECS = {s.name: s for s in (QUERY, HEADER, PATH, OPEN)}


@dataclass
class Rig:
    client: ProviderClient
    server: Server
    clock: FakeClock
    usage: MemoryUsageSink


def make(*script: Reply | Exception, specs=None, keys=None, usage=None, **options) -> Rig:
    server = Server(*script)
    clock = FakeClock()
    usage = MemoryUsageSink() if usage is None else usage
    specs = specs or SPECS
    options.setdefault("backoff_base", 1.0)
    options.setdefault("rate_limits", {name: 1000.0 for name in specs})  # effectively unthrottled
    client = ProviderClient(
        specs,
        {"query": KEY, "header": KEY, "path": KEY} if keys is None else keys,
        usage=usage,
        transport=httpx2.MockTransport(server),
        clock=clock,
        sleep=clock.sleep,
        rng=FullJitter(),
        **options,
    )
    return Rig(client, server, clock, usage)


def get(rig: Rig, provider: str = "query", path: str = "", **kwargs):
    return rig.client.request(provider, "GET", path, **kwargs)


# --- credentials -------------------------------------------------------------------------------


def test_a_query_key_is_sent_as_the_named_parameter_and_the_body_is_parsed():
    rig = make(Reply(json={"result": "0x10"}))
    response = run(get(rig, path="/stats", params={"module": "proxy"}))

    request = rig.server.requests[0]
    assert request.url.path == "/api/stats"
    assert dict(request.url.params) == {"module": "proxy", "apikey": KEY}
    assert (response.data, response.status_code, response.attempts) == ({"result": "0x10"}, 200, 1)


def test_a_header_key_is_sent_in_the_named_header_and_never_in_the_url():
    rig = make()
    run(get(rig, "header", "/wallet"))
    request = rig.server.requests[0]
    assert request.headers["x-api-key"] == KEY
    assert KEY not in str(request.url)


def test_a_path_key_is_put_into_the_url_path():
    rig = make()
    run(get(rig, "path", "blocks/tip"))
    assert rig.server.requests[0].url.path == f"/v2/{KEY}/blocks/tip"


def test_a_keyless_provider_needs_no_key_and_sends_none():
    rig = make(Reply(text="870123"), keys={})
    response = run(get(rig, "open", "/height"))
    request = rig.server.requests[0]
    assert response.data == 870123  # a bare number is valid JSON
    assert str(request.url) == "https://open.test/height"
    assert not {"x-api-key", "authorization"} & set(request.headers)


def test_a_body_that_is_not_json_comes_back_as_text():
    rig = make(Reply(text="<html>maintenance</html>"))
    assert run(get(rig)).data == "<html>maintenance</html>"


def test_json_bodies_are_posted_and_every_request_identifies_the_platform():
    rig = make()
    run(rig.client.request("query", "POST", json={"method": "eth_blockNumber"}))
    request = rig.server.requests[0]
    assert request.method == "POST"
    assert json.loads(request.content) == {"method": "eth_blockNumber"}
    assert request.headers["user-agent"].startswith("aml-platform/")
    assert request.headers["accept"] == "application/json"


def test_a_provider_that_needs_a_key_refuses_without_making_a_request():
    rig = make(keys={})
    with pytest.raises(ProviderNotConfigured) as info:
        run(get(rig))
    assert rig.server.requests == []
    assert not info.value.retryable


def test_unknown_providers_are_a_programming_error():
    with pytest.raises(ValueError, match="unknown provider"):
        run(get(make(), "nope"))


def test_secret_string_keys_are_accepted_and_every_key_is_registered_for_scrubbing():
    rig = make(keys={"query": SecretStr(KEY)})
    run(get(rig))
    assert rig.server.requests[0].url.params["apikey"] == KEY
    assert KEY in secrets
    assert (
        secrets.scrub(f"GET https://x.test/?apikey={KEY}&a=1")
        == "GET https://x.test/?apikey=***&a=1"
    )


def test_a_transport_error_that_mentions_the_key_never_reaches_the_caller_or_the_logs(caplog):
    caplog.set_level(logging.DEBUG)
    leaky = httpx2.ConnectError(f"cannot connect to https://query.test/api?apikey={KEY}")
    rig = make(leaky, max_retries=1)

    with pytest.raises(ProviderUnavailable) as info:
        run(get(rig))

    error = info.value
    assert error.reason == "ConnectError"
    assert KEY not in str(error) and KEY not in repr(error)
    assert error.__cause__ is None and error.__context__ is None  # no chained original either
    assert KEY not in caplog.text


# --- error handling ----------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_are_not_retried_and_do_not_open_the_circuit(status):
    rig = make(Reply(status), breaker_threshold=2)

    async def scenario():
        for _ in range(5):
            with pytest.raises(ProviderAuthError) as info:
                await get(rig)
        return info.value

    error = run(scenario())
    assert len(rig.server.requests) == 5  # one attempt each, never retried
    assert not error.retryable
    assert rig.client.statuses()[0].circuit == "closed"
    assert rig.clock.sleeps == []
    assert [e.outcome for e in rig.usage.events] == ["error"] * 5


@pytest.mark.parametrize("status", [400, 404, 422])
def test_other_client_errors_are_not_retried(status):
    rig = make(Reply(status))
    with pytest.raises(ProviderBadResponse) as info:
        run(get(rig))
    assert len(rig.server.requests) == 1
    assert info.value.reason == f"HTTP {status}" and not info.value.retryable


def test_server_errors_are_retried_with_exponential_backoff_then_reported():
    rig = make(Reply(500), max_retries=3)
    with pytest.raises(ProviderUnavailable) as info:
        run(get(rig))
    assert len(rig.server.requests) == 4
    assert rig.clock.sleeps == [1.0, 2.0, 4.0]
    assert info.value.reason == "HTTP 500" and info.value.retryable
    assert [e.retry for e in rig.usage.events] == [False, True, True, True]


def test_a_failure_that_clears_up_is_invisible_to_the_caller_but_counted():
    rig = make(Reply(503), Reply(json={"ok": True}))
    response = run(get(rig))
    assert (response.attempts, response.data) == (2, {"ok": True})
    assert [(e.outcome, e.retry, e.status_code) for e in rig.usage.events] == [
        ("error", False, 503),
        ("success", True, 200),
    ]
    assert rig.client.statuses()[0].consecutive_failures == 0


def test_timeouts_are_retried_and_reported_as_such():
    rig = make(httpx2.ReadTimeout("slow"), max_retries=1)
    with pytest.raises(ProviderUnavailable) as info:
        run(get(rig))
    assert info.value.reason == "timeout"
    assert len(rig.server.requests) == 2


def test_the_retry_count_can_be_overridden_for_a_single_call():
    rig = make(Reply(500), max_retries=5)
    with pytest.raises(ProviderUnavailable):
        run(get(rig, max_retries=0))
    assert len(rig.server.requests) == 1


def test_redirects_are_not_followed_so_a_key_cannot_be_forwarded_elsewhere():
    rig = make(Reply(302, headers={"Location": "https://evil.test/steal"}))
    with pytest.raises(ProviderBadResponse):
        run(get(rig))
    assert [r.url.host for r in rig.server.requests] == ["query.test"]


# --- rate limiting -----------------------------------------------------------------------------


def test_a_short_retry_after_is_waited_out_and_the_request_retried():
    rig = make(Reply(429, headers={"Retry-After": "2"}), Reply(json={"ok": True}))
    response = run(get(rig))
    assert response.attempts == 2
    assert sum(rig.clock.sleeps) == pytest.approx(2.0)
    assert [e.outcome for e in rig.usage.events] == ["rate_limited", "success"]


def test_a_429_without_retry_after_backs_off_and_is_finally_reported_as_rate_limited():
    rig = make(Reply(429), max_retries=2)
    with pytest.raises(ProviderRateLimited) as info:
        run(get(rig))
    assert len(rig.server.requests) == 3
    assert rig.clock.sleeps == [1.0, 2.0]
    assert info.value.retry_after is None and info.value.retryable


def test_a_long_retry_after_is_not_slept_through_and_blocks_the_provider_without_network():
    rig = make(Reply(429, headers={"Retry-After": "120"}), Reply(json={"ok": True}))

    async def scenario():
        with pytest.raises(ProviderRateLimited) as first:
            await get(rig)
        with pytest.raises(ProviderRateLimited) as second:  # still blocked: no request is made
            await get(rig)
        blocked = rig.client.statuses()[0].retry_after_seconds
        rig.clock.advance(121)
        later = await get(rig)
        return first.value, second.value, blocked, later

    first, second, blocked, later = run(scenario())
    assert first.retry_after == 120 and first.retryable
    assert 119 <= second.retry_after <= 120
    assert 119 <= blocked <= 120
    assert len(rig.server.requests) == 2  # the 429, then the success after the block ended
    assert rig.clock.sleeps == []  # nothing waited inline
    assert later.data == {"ok": True}


def test_retry_after_may_be_an_http_date():
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=90), usegmt=True)
    rig = make(Reply(429, headers={"Retry-After": when}))
    with pytest.raises(ProviderRateLimited) as info:
        run(get(rig))
    assert 80 <= info.value.retry_after <= 90


def test_an_unreadable_retry_after_is_ignored():
    rig = make(Reply(429, headers={"Retry-After": "soon"}), max_retries=0)
    with pytest.raises(ProviderRateLimited) as info:
        run(get(rig))
    assert info.value.retry_after is None


def test_requests_are_spaced_by_the_providers_rate_limit():
    rig = make(rate_limits={"query": 1.0})

    async def scenario():
        await get(rig)
        await get(rig)

    run(scenario())
    assert rig.clock.sleeps == [pytest.approx(1.0)]


def test_no_more_requests_are_in_flight_than_the_provider_allows():
    capped = spec("capped", "https://capped.test", AuthStyle.NONE, max_concurrency=2)
    clock = FakeClock()
    running = peak = 0

    async def handler(request):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        for _ in range(5):
            await asyncio.sleep(0)  # let the other requests try to start
        running -= 1
        return httpx2.Response(200, json={})

    client = ProviderClient(
        {"capped": capped},
        {},
        transport=httpx2.MockTransport(handler),
        rate_limits={"capped": 1000.0},
        clock=clock,
        sleep=clock.sleep,
    )

    async def scenario():
        await asyncio.gather(*(client.request("capped", "GET") for _ in range(8)))

    run(scenario())
    assert peak == 2


# --- circuit breaker ---------------------------------------------------------------------------


def test_repeated_failures_open_the_circuit_and_calls_then_skip_the_network():
    rig = make(
        Reply(500), Reply(500), Reply(500), Reply(json={"ok": True}),
        breaker_threshold=3, breaker_cooldown=30.0, max_retries=0,
    )  # fmt: skip

    async def scenario():
        for _ in range(3):
            with pytest.raises(ProviderUnavailable):
                await get(rig)
        with pytest.raises(CircuitOpen) as blocked:
            await get(rig)
        opened = rig.client.statuses()[0]
        rig.clock.advance(31)  # cooling-off over: one trial request is allowed
        response = await get(rig)
        return blocked.value, opened, response

    blocked, opened, response = run(scenario())
    assert opened.circuit == "open" and opened.last_error == "HTTP 500"
    assert blocked.retry_after == pytest.approx(30.0) and blocked.retryable
    assert len(rig.server.requests) == 4  # the circuit-open call made no request
    assert response.data == {"ok": True}
    assert rig.client.statuses()[0].circuit == "closed"


def test_a_failed_trial_request_opens_the_circuit_again():
    rig = make(Reply(500), breaker_threshold=1, breaker_cooldown=30.0, max_retries=0)

    async def scenario():
        with pytest.raises(ProviderUnavailable):
            await get(rig)
        rig.clock.advance(31)
        with pytest.raises(ProviderUnavailable):  # the trial: it reaches the network and fails
            await get(rig)
        with pytest.raises(CircuitOpen):
            await get(rig)

    run(scenario())
    assert len(rig.server.requests) == 2


def test_cancelling_the_trial_request_does_not_leave_the_circuit_stuck():
    clock = FakeClock()
    mode = {"answer": "500"}
    entered = None

    async def handler(request):
        if mode["answer"] == "hang":
            entered.set()
            await asyncio.Event().wait()  # never returns
        return httpx2.Response(500 if mode["answer"] == "500" else 200, json={})

    client = ProviderClient(
        {"open": OPEN},
        {},
        transport=httpx2.MockTransport(handler),
        rate_limits={"open": 1000.0},
        breaker_threshold=1,
        breaker_cooldown=10.0,
        max_retries=0,
        clock=clock,
        sleep=clock.sleep,
    )

    async def scenario():
        nonlocal entered
        entered = asyncio.Event()
        with pytest.raises(ProviderUnavailable):
            await client.request("open", "GET")  # opens the circuit
        clock.advance(11)

        mode["answer"] = "hang"
        trial = asyncio.create_task(client.request("open", "GET"))
        await entered.wait()
        trial.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await trial

        mode["answer"] = "200"
        return await client.request("open", "GET")  # must be allowed to try, not CircuitOpen

    assert run(scenario()).status_code == 200


# --- budget ------------------------------------------------------------------------------------


def test_every_attempt_counts_against_the_budget_retries_included():
    rig = make(Reply(500), max_retries=5)
    budget = RequestBudget(limit=2)
    with pytest.raises(BudgetExceeded) as info:
        run(get(rig, budget=budget))
    assert len(rig.server.requests) == 2
    assert (budget.used, budget.by_provider) == (2, {"query": 2})
    assert not info.value.retryable


def test_a_refused_request_hands_back_the_circuits_trial_slot():
    rig = make(Reply(500), Reply(json={"ok": True}), breaker_threshold=1, max_retries=0)

    async def scenario():
        with pytest.raises(ProviderUnavailable):
            await get(rig)  # opens the circuit
        rig.clock.advance(31)
        with pytest.raises(BudgetExceeded):
            await get(rig, budget=RequestBudget(limit=0))  # claims the trial, then is refused
        return await get(rig)  # the trial is still available

    assert run(scenario()).data == {"ok": True}


# --- cache -------------------------------------------------------------------------------------


def test_repeated_gets_are_served_from_the_cache_until_it_expires():
    rig = make(Reply(json={"n": 1}))
    budget = RequestBudget(limit=10)

    async def scenario():
        kwargs = {"path": "/p", "cache_ttl": 60, "budget": budget}
        first = await get(rig, params={"a": "1"}, **kwargs)
        second = await get(rig, params={"a": "1"}, **kwargs)
        other = await get(rig, params={"a": "2"}, **kwargs)
        rig.clock.advance(61)
        third = await get(rig, params={"a": "1"}, **kwargs)
        return first, second, other, third

    first, second, other, third = run(scenario())
    assert [r.from_cache for r in (first, second, other, third)] == [False, True, False, False]
    assert len(rig.server.requests) == 3
    assert len(rig.usage.events) == 3 and budget.used == 3  # hits cost no request


def test_a_cached_response_is_a_copy_the_caller_may_edit():
    rig = make(Reply(json={"items": [1, 2]}))

    async def scenario():
        await get(rig, cache_ttl=60)
        first = await get(rig, cache_ttl=60)
        first.data["items"].append(99)
        return await get(rig, cache_ttl=60)

    assert run(scenario()).data == {"items": [1, 2]}


def test_only_gets_are_cached():
    rig = make()

    async def scenario():
        for _ in range(2):
            await rig.client.request("query", "POST", cache_ttl=60)

    run(scenario())
    assert len(rig.server.requests) == 2


# --- usage and status --------------------------------------------------------------------------


def test_a_failing_usage_sink_never_breaks_a_provider_call(caplog):
    class Broken:
        async def record(self, event):
            raise RuntimeError("database is down")

    rig = make(Reply(json={"ok": True}), usage=Broken())
    assert run(get(rig)).data == {"ok": True}
    assert "could not record provider usage" in caplog.text


def test_statuses_report_configuration_and_the_last_error():
    rig = make(Reply(500), keys={"query": KEY}, max_retries=0)
    with pytest.raises(ProviderUnavailable):
        run(get(rig))

    by_name = {s.name: s for s in rig.client.statuses()}
    assert list(by_name) == ["query", "header", "path", "open"]
    assert [by_name[n].configured for n in ("query", "header", "path", "open")] == [
        True,
        False,
        False,
        True,
    ]
    assert by_name["open"].needs_key is False
    assert (by_name["query"].last_error, by_name["query"].consecutive_failures) == ("HTTP 500", 1)
    assert by_name["query"].last_error_at is not None
    assert by_name["header"].last_error is None


# --- errors that arrive inside a 200 response --------------------------------------------------


def failing_with(*problems):
    """A body check that reports these problems in turn, then accepts everything."""
    queue = list(problems)

    def check(provider, data):
        return queue.pop(0) if queue else None

    return check


def test_the_body_check_sees_the_provider_name_and_the_parsed_body():
    seen = []

    def check(provider, data):
        seen.append((provider, data))

    rig = make(Reply(json={"status": "1", "result": []}))
    run(get(rig, body_check=check))
    assert seen == [("query", {"status": "1", "result": []})]


def test_a_rate_limit_reported_in_a_200_body_is_waited_out_and_retried():
    problem = ProviderRateLimited("query", "rate limit reached")
    rig = make(Reply(json={"status": "0"}), Reply(json={"ok": True}))

    response = run(get(rig, body_check=failing_with(problem)))

    assert response.attempts == 2 and response.data == {"ok": True}
    assert [e.outcome for e in rig.usage.events] == ["rate_limited", "success"]
    assert rig.clock.sleeps == [1.0]  # no Retry-After: the first backoff step, via the throttle


def test_a_long_rate_limit_in_a_body_blocks_the_provider_like_a_long_retry_after():
    problem = ProviderRateLimited("query", "daily pool used up", retry_after=3600)
    rig = make(Reply(json={"status": "0"}), Reply(json={"ok": True}))

    async def scenario():
        with pytest.raises(ProviderRateLimited) as first:
            await get(rig, body_check=failing_with(problem))
        with pytest.raises(ProviderRateLimited) as second:  # blocked: no request at all
            await get(rig)
        return first.value, second.value

    first, second = run(scenario())
    assert first.retry_after == 3600 and 3590 <= second.retry_after <= 3600
    assert len(rig.server.requests) == 1


def test_a_rejected_key_in_a_body_is_not_retried_and_does_not_open_the_circuit():
    problem = ProviderAuthError("query", "the API key was rejected")
    rig = make(Reply(json={"status": "0"}), breaker_threshold=1)

    with pytest.raises(ProviderAuthError):
        run(get(rig, body_check=failing_with(problem)))

    assert len(rig.server.requests) == 1
    assert rig.client.statuses()[0].circuit == "closed"
    assert rig.client.statuses()[0].last_error == "the API key was rejected"


def test_a_server_error_in_a_body_is_retried_and_counts_towards_the_breaker():
    problem = ProviderUnavailable("query", "provider timed out")
    rig = make(Reply(json={"status": "0"}), Reply(json={"ok": True}), max_retries=2)

    response = run(get(rig, body_check=failing_with(problem)))

    assert response.attempts == 2
    assert [e.outcome for e in rig.usage.events] == ["error", "success"]

    rig = make(Reply(json={"status": "0"}), breaker_threshold=2, max_retries=1)
    with pytest.raises(ProviderUnavailable):
        run(get(rig, body_check=failing_with(problem, problem)))
    assert rig.client.statuses()[0].circuit == "open"


def test_a_request_the_provider_will_never_accept_is_not_retried():
    problem = ProviderBadResponse("query", "unsupported chain")
    rig = make(Reply(json={"status": "0"}))
    with pytest.raises(ProviderBadResponse):
        run(get(rig, body_check=failing_with(problem)))
    assert len(rig.server.requests) == 1


def test_a_response_that_failed_its_check_is_never_cached():
    problem = ProviderUnavailable("query", "provider timed out")
    rig = make(Reply(json={"first": 1}), Reply(json={"second": 2}))

    async def scenario():
        kwargs = {"cache_ttl": 60, "max_retries": 0}
        with pytest.raises(ProviderUnavailable):
            await get(rig, body_check=failing_with(problem), **kwargs)
        return await get(rig, **kwargs)

    response = run(scenario())
    assert response.data == {"second": 2} and not response.from_cache


def test_a_checker_that_crashes_becomes_a_bad_response_instead_of_an_exception_in_the_caller(
    caplog,
):
    def broken(provider, data):
        raise KeyError("result")

    rig = make(Reply(json={"x": 1}))
    with pytest.raises(ProviderBadResponse) as info:
        run(get(rig, body_check=broken))
    assert "could not be interpreted" in info.value.reason
    assert "a response checker failed" in caplog.text


def test_the_body_check_is_not_asked_about_responses_the_status_already_condemned():
    called = []
    rig = make(Reply(500), max_retries=0)
    with pytest.raises(ProviderUnavailable):
        run(get(rig, body_check=lambda provider, data: called.append(1)))
    assert called == []
