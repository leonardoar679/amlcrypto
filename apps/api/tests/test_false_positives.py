"""Ordinary behaviour the rules must not make into an accusation.

A tool that flags everyone is as useless as one that flags no one: analysts stop reading, and the
one real finding is lost among the rest. This is a set of benign patterns, each built from movements
and labels as the engine receives them, with what the engine may say about it: how high the level
may go and which rules may fire at all.

Some of what an honest person does is, by the rules' own definition, a thing to look at (a payment
processor forwards what it receives within minutes; a payroll run pays many people in a day). Those
are allowed to fire, at the low severity their rules carry, and no higher. Two patterns are
flagged on purpose and said so here: value from a sanctioned address in any amount (a dusting attack
is a false positive for the person it is done to, and the policy is that any amount counts), and a
route through an exchange to a sanctioned address (reported, with the exchange named, because the
rules do not discount it; see the limitations register).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import pytest

from app.intel.model import Category
from app.risk.model import Level, Outcome
from app.risk.render import render_report
from tests.risk_helpers import ROOT, FakeLabels, addr, at, flow, label, screen

EXCHANGE = addr(9_000)
MERCHANT = addr(9_003)
BAD = addr(9_001)


def people(count: int, start: int = 100) -> list[str]:
    return [addr(start + n) for n in range(count)]


@dataclass(frozen=True)
class Benign:
    name: str
    flows: list
    labels: tuple = ()
    depth: int = 1
    level: Level = Level.LOW
    rules: frozenset[str] = field(default_factory=frozenset)  # the only ones that may fire
    limits: dict = field(default_factory=dict)


def _ordinary():
    moves = []
    for n, other in enumerate(people(5)):
        moves.append(flow(other, ROOT, 10**17, at(72 * n)))
        moves.append(flow(ROOT, other, 5 * 10**16, at(72 * n + 5)))
    return moves


def _exchange_customer():
    deposits = [flow(ROOT, EXCHANGE, 10**18, at(24 * n)) for n in range(8)]
    withdrawals = [flow(EXCHANGE, ROOT, 10**18, at(24 * n + 3)) for n in range(4)]
    return deposits + withdrawals


def _payroll():
    return [flow(ROOT, payee, 10**17, at(n * 0.1)) for n, payee in enumerate(people(30))]


def _donations():
    return [flow(donor, ROOT, 10**16, at(n * 0.2)) for n, donor in enumerate(people(40))]


def _processor():
    moves = []
    for n, customer in enumerate(people(6)):
        moves.append(flow(customer, ROOT, 10**17, at(n * 2)))
        moves.append(flow(ROOT, MERCHANT, 10**17, at(n * 2 + 0.05)))
    return moves


def _day_trader():
    trio = people(3)
    moves = []
    for n in range(60):
        other = trio[n % 3]
        moves.append(
            flow(other, ROOT, 10**16, at(n * 0.3))
            if n % 2 == 0
            else flow(ROOT, other, 10**16, at(n * 0.3 + 0.01))
        )
    return moves


def _consolidation():
    a, b, c, d = people(4, 500)
    return [
        flow(a, b, 10**18, at(1)),
        flow(b, c, 10**18 - 10**15, at(2)),
        flow(c, d, 10**18 - 2 * 10**15, at(3)),
        flow(d, ROOT, 10**18 - 3 * 10**15, at(4)),
    ]


def _cold_storage():
    return [flow(sender, ROOT, 10**18, at(24 * n)) for n, sender in enumerate(people(3, 700))]


def _poisoned():
    """Look-alike addresses send nothing of value (zero amounts are not movements of value)."""
    return [flow(lookalike, ROOT, 0, at(n)) for n, lookalike in enumerate(people(5, 800))]


def _dormant_then_active():
    old, new = people(2, 900)
    return [flow(old, ROOT, 10**18, at(-24 * 400)), flow(ROOT, new, 10**17, at(5))]


BENIGN = [
    Benign("an ordinary wallet", _ordinary()),
    Benign(
        "an exchange customer",
        _exchange_customer(),
        (label(EXCHANGE, Category.EXCHANGE, entity="Some Exchange"),),
    ),
    Benign(
        "a payroll run: one sender, thirty payees in a day",
        _payroll(),
        rules=frozenset({"AML-010"}),
    ),
    Benign("donations: forty donors in a day", _donations(), rules=frozenset({"AML-009"})),
    Benign(
        "a payment processor that forwards at once",
        _processor(),
        rules=frozenset({"AML-004", "AML-014"}),
    ),
    Benign("a busy trader with three counterparties", _day_trader(), rules=frozenset({"AML-004"})),
    Benign("funds moved between one's own addresses", _consolidation(), depth=4),
    Benign("cold storage that only ever receives", _cold_storage()),
    Benign("address poisoning: look-alikes that send nothing", _poisoned()),
    Benign(
        "a wallet that wakes after a year",
        _dormant_then_active(),
        rules=frozenset({"AML-012"}),
        limits={"window_days": 800},
    ),
    Benign("an address with no history at all", []),
]


def run(case: Benign):
    return screen(case.flows, labels=FakeLabels(*case.labels), depth=case.depth, **case.limits)


@pytest.mark.parametrize("case", BENIGN, ids=[case.name for case in BENIGN])
def test_ordinary_behaviour_is_not_made_into_an_accusation(case):
    report = run(case)

    fired = {item.finding.rule_id for item in report.findings}
    assert fired <= case.rules, f"{sorted(fired - case.rules)} fired on {case.name}"
    assert report.score is not None and report.score.level <= case.level


def test_what_may_fire_is_never_more_than_low_severity_noise():
    """Whatever the benign patterns trip is a LOW-severity rule: a pattern worth a look, never an
    exposure to anyone."""
    for case in BENIGN:
        for item in run(case).findings:
            assert item.finding.severity.value in {"LOW", "MEDIUM"}, (
                case.name,
                item.finding.rule_id,
            )
            assert item.finding.rule_id not in {
                "AML-001",
                "AML-002",
                "AML-003",
                "AML-007",
                "AML-008",
            }


def test_no_report_of_a_benign_pattern_says_the_address_is_clear():
    """What a report may say about an address it found nothing on is that it found nothing: its own
    disclaimer says that no finding is not a clearance and may use the words to say so; no other
    sentence may call the address clean, safe or trusted."""
    asserted = re.compile(
        r"\b(is|are|was|looks?|appears?|seems?|considered|deemed|rated|found)\s+"
        r"(clean|safe|legitimate|trusted|cleared|innocent|harmless)\b"
        r"|\bno risk\b|\brisk[- ]free\b"
    )
    for case in BENIGN:
        lines = [
            line
            for line in render_report(run(case)).lower().splitlines()
            if "not a clearance" not in line and "does not mean" not in line
        ]
        said = [m.group(0) for line in lines for m in asserted.finditer(line)]
        assert said == [], (case.name, said)


def test_finding_nothing_is_said_as_finding_nothing_and_not_as_a_clearance():
    report = run(BENIGN[0])

    assert report.outcome is Outcome.NO_FINDINGS
    document = report.to_dict()
    assert document["outcome"] == "no_findings"
    assert any(rule["status"] == "clear" for rule in document["rules"])


# --- what is flagged on purpose -------------------------------------------------------------


def test_value_from_a_sanctioned_address_counts_in_any_amount_and_the_report_shows_how_little():
    """A dusting attack puts one unit of value in a stranger's address. By policy any amount counts
    (OFAC does not set a threshold), and the evidence says how much it was, so that an analyst can
    see at once what happened."""
    report = screen([flow(BAD, ROOT, 1, at(5))], labels=FakeLabels(label(BAD)))

    assert report.score is not None and report.score.level is Level.CRITICAL
    (item,) = [i for i in report.findings if i.finding.rule_id == "AML-001"]
    assert '"amount_raw": "1"' in json.dumps(item.finding.to_dict()) or "1" in json.dumps(
        item.finding.to_dict()["evidence"]
    )


def test_a_route_through_an_exchange_is_reported_with_the_exchange_named_but_not_discounted():
    """Funds an exchange received from a sanctioned address may have left it for anyone. The rules
    report the route (it is the evidence an analyst needs) at two hops' weight and name the exchange
    in the middle; they do not discount it for passing through a service."""
    flows = [flow(BAD, EXCHANGE, 10**20, at(10)), flow(EXCHANGE, ROOT, 10**17, at(50))]
    labels = FakeLabels(label(BAD), label(EXCHANGE, Category.EXCHANGE, entity="Some Exchange"))

    report = screen(flows, labels=labels, depth=2)

    (item,) = [i for i in report.findings if i.finding.rule_id == "AML-002"]
    assert item.finding.hops == 2
    assert report.score is not None and report.score.level is Level.MODERATE
    assert item.finding.description.endswith("2 hops away, via Some Exchange")
    (via,) = item.finding.to_dict()["evidence"][0]["via"]
    assert via["address"] == EXCHANGE and via["labels"][0]["category"] == "EXCHANGE"


def test_a_route_through_an_address_nobody_has_said_anything_about_has_no_via():
    flows = [flow(BAD, EXCHANGE, 10**20, at(10)), flow(EXCHANGE, ROOT, 10**17, at(50))]

    report = screen(flows, labels=FakeLabels(label(BAD)), depth=2)

    (item,) = [i for i in report.findings if i.finding.rule_id == "AML-002"]
    assert "via" not in item.finding.description
    assert "via" not in item.finding.to_dict()["evidence"][0]


def test_concentration_on_a_service_is_not_suspicious_but_on_an_unlabelled_address_it_is():
    flows = _exchange_customer()
    with_label = screen(flows, labels=FakeLabels(label(EXCHANGE, Category.EXCHANGE)))
    without = screen(flows)

    assert "AML-014" not in {i.finding.rule_id for i in with_label.findings}
    assert "AML-014" in {i.finding.rule_id for i in without.findings}


@pytest.mark.parametrize(
    "category", [Category.EXCHANGE, Category.DEFI, Category.BRIDGE, Category.GAMBLING]
)
def test_every_kind_of_service_is_exempt_from_the_concentration_rule(category):
    report = screen(_exchange_customer(), labels=FakeLabels(label(EXCHANGE, category)))
    assert "AML-014" not in {i.finding.rule_id for i in report.findings}


@pytest.mark.parametrize("category", [Category.MIXER, Category.SCAM, Category.UNKNOWN])
def test_a_label_that_is_not_a_service_does_not_excuse_concentration(category):
    report = screen(_exchange_customer(), labels=FakeLabels(label(EXCHANGE, category)))
    assert "AML-014" in {i.finding.rule_id for i in report.findings}


def test_a_route_through_two_services_names_both_in_the_order_the_value_moved():
    other = addr(9_004)
    flows = [
        flow(BAD, EXCHANGE, 10**20, at(10)),
        flow(EXCHANGE, other, 10**19, at(30)),
        flow(other, ROOT, 10**17, at(50)),
    ]
    labels = FakeLabels(
        label(BAD),
        label(EXCHANGE, Category.EXCHANGE, entity="First Exchange"),
        label(other, Category.DEFI, entity="Second Protocol"),
    )

    report = screen(flows, labels=labels, depth=3)

    (item,) = [i for i in report.findings if i.finding.rule_id == "AML-002"]
    assert item.finding.hops == 3
    assert item.finding.description.endswith("3 hops away, via First Exchange, Second Protocol")
    via = item.finding.to_dict()["evidence"][0]["via"]
    assert [entry["address"] for entry in via] == [EXCHANGE, other]
