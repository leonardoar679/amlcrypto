"""No rule fails on data that is odd rather than risky.

A rule that raises is reported as `error` and the rest of the screening goes on, so one bad
assumption does not lose a report; but an error is a rule that did not look, on exactly the
addresses that are strange enough to need it to. So each rule is run over data that real chains
produce and that a careless rule divides by: nothing but zero-value events (address poisoning),
single units of dust, amounts as large as a token can hold, movements with no time or all at one
second or in the future, many assets, many counterparties, and a very long history.
"""

from __future__ import annotations

import pytest

from app.graph.model import AssetRef, Basis
from app.risk.model import RuleStatus
from tests.risk_helpers import FakeLabels, addr, at, flow, label, screen

ROOT = "0x" + "0" * 39 + "1"
BAD = addr(9_001)
MAX_UINT256 = 2**256 - 1


def counterparties(count: int, start: int = 100) -> list[str]:
    return [addr(start + n) for n in range(count)]


def token(number: int) -> AssetRef:
    return AssetRef(addr(50_000 + number), f"TK{number}", 18)


SCENARIOS = {
    "only zero-value events from one address": [flow(addr(7), ROOT, 0, at(n)) for n in range(8)],
    "only zero-value events from many addresses": [
        flow(who, ROOT, 0, at(n)) for n, who in enumerate(counterparties(30))
    ],
    "zero-value events both ways": [
        flow(addr(7), ROOT, 0, at(n)) if n % 2 else flow(ROOT, addr(7), 0, at(n)) for n in range(12)
    ],
    "a single unit of dust": [flow(addr(7), ROOT, 1, at(1))],
    "the largest amount a token can hold": [
        flow(who, ROOT, MAX_UINT256, at(n)) for n, who in enumerate(counterparties(12))
    ],
    "the largest amount, in and out": [
        flow(addr(7), ROOT, MAX_UINT256, at(n * 2))
        if n % 2 == 0
        else flow(ROOT, addr(8), MAX_UINT256, at(n * 2))
        for n in range(12)
    ],
    "movements with no known time": [
        flow(who, ROOT, 10**17, timed=False) for who in counterparties(12)
    ],
    "some timed and some not": [
        flow(who, ROOT, 10**17, at(n), timed=n % 2 == 0) for n, who in enumerate(counterparties(12))
    ],
    "everything in the same second": [flow(who, ROOT, 10**17, at(5)) for who in counterparties(60)],
    "movements in the future": [
        flow(who, ROOT, 10**17, at(24 * 365 * 3 + n)) for n, who in enumerate(counterparties(12))
    ],
    "movements a long time ago": [
        flow(who, ROOT, 10**17, at(-24 * 365 * 8 + n)) for n, who in enumerate(counterparties(12))
    ],
    "forty assets": [flow(addr(7), ROOT, 10**17, at(n), asset=token(n % 40)) for n in range(80)],
    "five hundred counterparties": [
        flow(who, ROOT, 10**16, at(n * 0.01)) for n, who in enumerate(counterparties(500))
    ],
    "a very long history with one counterparty": [
        flow(addr(7), ROOT, 10**16, at(n * 0.05)) for n in range(1500)
    ],
    "a counterparty that is the subject itself": [
        flow(ROOT, ROOT, 10**17, at(n)) for n in range(6)
    ],
    "unproven amounts and orders": [
        flow(who, ROOT, 10**17, at(n), basis=Basis.ALLOCATED, timed=n % 3 != 0)
        for n, who in enumerate(counterparties(12))
    ],
}


@pytest.mark.parametrize("name", list(SCENARIOS))
@pytest.mark.parametrize("chain", ["ethereum", "bitcoin"])
def test_no_rule_fails_on_odd_data(name, chain):
    flows = SCENARIOS[name]

    report = screen(flows, labels=FakeLabels(), depth=2, chain=chain, window_days=4_000)

    failed = [rule.rule_id for rule in report.rules if rule.status is RuleStatus.ERROR]
    assert failed == [], f"{failed} failed on {name}"


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_no_rule_fails_on_odd_data_next_to_a_risky_address(name):
    flows = [*SCENARIOS[name], flow(BAD, counterparties(1)[0], 10**18, at(2))]

    report = screen(flows, labels=FakeLabels(label(BAD)), depth=3, window_days=4_000)

    failed = [rule.rule_id for rule in report.rules if rule.status is RuleStatus.ERROR]
    assert failed == [], f"{failed} failed on {name}"


def test_a_screening_of_odd_data_still_has_a_score_that_is_a_number_between_zero_and_a_hundred():
    for name, flows in SCENARIOS.items():
        report = screen(flows, depth=2, window_days=4_000)
        assert report.score is not None, name
        assert 0 <= report.score.value <= 100, name
