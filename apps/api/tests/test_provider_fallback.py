"""Trying several providers for one job."""

from __future__ import annotations

import pytest

from app.providers.base import (
    AllProvidersFailed,
    BudgetExceeded,
    CircuitOpen,
    ProviderAuthError,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderUnavailable,
)
from app.providers.fallback import first_success
from tests.helpers import run


def succeeds(value):
    async def call():
        return value

    return call


def fails(error):
    async def call():
        raise error

    return call


def test_the_first_provider_that_works_wins_and_later_ones_are_not_called():
    called = []

    async def never():
        called.append("never")

    result = run(first_success("transfers", [("a", succeeds(1)), ("b", never)]))
    assert result == ("a", 1)
    assert called == []


def test_any_provider_error_moves_on_to_the_next_provider():
    attempts = [
        ("a", fails(ProviderNotConfigured("a", "no API key is configured"))),
        ("b", fails(ProviderUnavailable("b", "HTTP 503"))),
        ("c", fails(ProviderAuthError("c", "HTTP 403"))),
        ("d", succeeds("data")),
    ]
    assert run(first_success("transfers", attempts)) == ("d", "data")


def test_when_nothing_works_the_error_lists_every_reason_and_the_shortest_wait():
    attempts = [
        ("a", fails(ProviderRateLimited("a", "HTTP 429", retry_after=120))),
        ("b", fails(CircuitOpen("b", "circuit open after repeated failures", retry_after=30))),
        ("c", fails(ProviderAuthError("c", "HTTP 403"))),
    ]
    with pytest.raises(AllProvidersFailed) as info:
        run(first_success("transfers", attempts))

    error = info.value
    assert [name for name, _ in error.failures] == ["a", "b", "c"]
    assert "HTTP 429" in str(error) and "HTTP 403" in str(error)
    assert error.retry_after == 30
    assert error.retryable  # two of the three failures are temporary


def test_it_is_not_worth_retrying_when_every_failure_is_permanent():
    attempts = [
        ("a", fails(ProviderNotConfigured("a", "no API key is configured"))),
        ("b", fails(ProviderAuthError("b", "HTTP 401"))),
    ]
    with pytest.raises(AllProvidersFailed) as info:
        run(first_success("transfers", attempts))
    assert not info.value.retryable and info.value.retry_after is None


def test_with_no_provider_to_try_the_request_fails_without_hope_of_retry():
    with pytest.raises(AllProvidersFailed) as info:
        run(first_success("transfers", []))
    assert not info.value.retryable
    assert "none tried" in str(info.value)


def test_an_exhausted_budget_stops_the_search():
    reached = []

    async def second():
        reached.append("b")

    attempts = [("a", fails(BudgetExceeded("a", "request budget of 5 is used up"))), ("b", second)]
    with pytest.raises(BudgetExceeded):
        run(first_success("transfers", attempts))
    assert reached == []


def test_a_bug_in_a_call_is_not_mistaken_for_a_provider_failure():
    with pytest.raises(ZeroDivisionError):
        run(first_success("transfers", [("a", fails(ZeroDivisionError())), ("b", succeeds(1))]))
