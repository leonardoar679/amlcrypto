"""Token bucket, circuit breaker, TTL cache and request budget, on a fake clock."""

from __future__ import annotations

import asyncio

import pytest

from app.providers.base import BudgetExceeded
from app.providers.breaker import CircuitBreaker, CircuitState
from app.providers.budget import RequestBudget
from app.providers.cache import MISSING, TtlCache
from app.providers.throttle import TokenBucket
from tests.helpers import FakeClock, run

# --- token bucket ----------------------------------------------------------------------------


def make_bucket(rate=2.0, capacity=None):
    clock = FakeClock()
    return TokenBucket(rate, capacity, clock=clock, sleep=clock.sleep), clock


def test_a_full_bucket_allows_a_burst_then_spaces_requests_by_the_rate():
    bucket, clock = make_bucket(rate=2.0)  # capacity defaults to 2

    async def scenario():
        return [await bucket.acquire() for _ in range(5)]

    waits = run(scenario())
    assert waits[:2] == [0.0, 0.0]  # the burst
    assert waits[2:] == pytest.approx([0.5, 0.5, 0.5])  # then 2 per second
    assert clock.now == pytest.approx(1000.0 + 1.5)


def test_tokens_refill_while_idle_but_never_beyond_capacity():
    bucket, clock = make_bucket(rate=2.0, capacity=3.0)

    async def scenario():
        for _ in range(3):
            await bucket.acquire()
        clock.advance(100)  # long idle period
        return [await bucket.acquire() for _ in range(4)]

    waits = run(scenario())
    assert waits[:3] == [0.0, 0.0, 0.0]  # refilled to capacity, no further
    assert waits[3] == pytest.approx(0.5)


def test_penalize_blocks_everything_for_the_stated_time():
    bucket, clock = make_bucket(rate=10.0)

    async def scenario():
        bucket.penalize(3.0)
        return await bucket.acquire()

    assert run(scenario()) == pytest.approx(3.0)


def test_concurrent_waiters_are_all_served_at_the_configured_rate():
    bucket, clock = make_bucket(rate=2.0, capacity=1.0)

    async def scenario():
        await asyncio.gather(*(bucket.acquire() for _ in range(5)))

    run(scenario())
    assert clock.now == pytest.approx(1000.0 + 2.0)  # 1 burst + 4 more at 0.5 s each


def test_bucket_rejects_nonsense():
    with pytest.raises(ValueError):
        TokenBucket(0)
    bucket, _ = make_bucket(rate=1.0, capacity=1.0)
    with pytest.raises(ValueError):
        run(bucket.acquire(2.0))


# --- circuit breaker -------------------------------------------------------------------------


def make_breaker(threshold=3, cooldown=30.0):
    clock = FakeClock()
    return CircuitBreaker(failure_threshold=threshold, cooldown=cooldown, clock=clock), clock


def test_the_breaker_opens_after_the_threshold_and_then_refuses_calls():
    breaker, _ = make_breaker(threshold=3)
    for _ in range(2):
        assert breaker.before_call()
        breaker.on_failure("boom")
    assert breaker.state is CircuitState.CLOSED
    assert breaker.before_call()
    breaker.on_failure("boom")
    assert breaker.state is CircuitState.OPEN
    assert not breaker.before_call()


def test_a_success_in_between_resets_the_failure_count():
    breaker, _ = make_breaker(threshold=3)
    breaker.on_failure("x")
    breaker.on_failure("x")
    breaker.on_success()
    breaker.on_failure("x")
    breaker.on_failure("x")
    assert breaker.state is CircuitState.CLOSED


def test_after_the_cooldown_exactly_one_trial_request_is_allowed():
    breaker, clock = make_breaker(threshold=1, cooldown=30.0)
    breaker.on_failure("boom")
    clock.advance(29.9)
    assert not breaker.before_call()
    clock.advance(0.2)
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.before_call()  # the trial
    assert not breaker.before_call()  # nobody else while it is in flight


def test_a_successful_trial_closes_the_breaker_and_a_failed_one_reopens_it():
    breaker, clock = make_breaker(threshold=1, cooldown=30.0)
    breaker.on_failure("boom")
    clock.advance(31)
    assert breaker.before_call()
    breaker.on_success()
    assert breaker.state is CircuitState.CLOSED and breaker.consecutive_failures == 0

    breaker.on_failure("boom again")
    clock.advance(31)
    assert breaker.before_call()
    breaker.on_failure("still broken")
    assert breaker.state is CircuitState.OPEN
    clock.advance(10)
    assert not breaker.before_call()  # the cooldown restarted


def test_note_error_is_visible_but_does_not_count_towards_opening():
    breaker, _ = make_breaker(threshold=1)
    breaker.note_error("HTTP 401")
    assert breaker.state is CircuitState.CLOSED
    assert breaker.last_error == "HTTP 401" and breaker.consecutive_failures == 0


# --- TTL cache -------------------------------------------------------------------------------


def test_entries_expire_after_their_ttl():
    clock = FakeClock()
    cache = TtlCache(clock=clock)
    cache.set("k", "v", ttl=10)
    assert cache.get("k") == "v"
    clock.advance(10)
    assert cache.get("k") is MISSING
    assert len(cache) == 0


def test_the_least_recently_used_entry_is_evicted_first():
    cache = TtlCache(max_entries=2, clock=FakeClock())
    cache.set("a", 1, 100)
    cache.set("b", 2, 100)
    assert cache.get("a") == 1  # touch a, so b is now the oldest
    cache.set("c", 3, 100)
    assert cache.get("b") is MISSING
    assert cache.get("a") == 1 and cache.get("c") == 3


def test_a_cached_none_is_distinguishable_from_a_miss():
    cache = TtlCache(clock=FakeClock())
    cache.set("k", None, 10)
    assert cache.get("k") is None
    assert cache.get("other") is MISSING


# --- request budget --------------------------------------------------------------------------


def test_budget_counts_per_provider_and_refuses_the_request_over_the_limit():
    budget = RequestBudget(limit=3)
    budget.consume("etherscan")
    budget.consume("trongrid", 2)
    assert budget.used == 3 and budget.exhausted and budget.remaining == 0
    assert budget.by_provider == {"etherscan": 1, "trongrid": 2}
    with pytest.raises(BudgetExceeded):
        budget.consume("etherscan")
    assert budget.used == 3  # the refused request was not counted
