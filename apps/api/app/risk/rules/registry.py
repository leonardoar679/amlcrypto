"""The rules (blueprint 6.3), their defaults, and the changes an operator may make to them.

The rules and their logic are code; what an operator can change is whether a rule is on and its
configuration (the thresholds). A change is kept as an override, so the defaults stay in one place
and an assessment records the configuration it actually ran with.

A rule's `version` is raised whenever its logic or its defaults change.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.risk.model import Severity
from app.risk.rules import behavior, exposure
from app.risk.rules.base import Rule

ALL = ("*",)
DAY = 86_400
INFERRED_DISCOUNT = 0.85  # a route resting on an allocated amount or an unproven order


class RuleConfigError(ValueError):
    """A rule configuration naming a setting the rule lacks, or giving the wrong kind of value."""


RULES: tuple[Rule, ...] = (
    Rule(
        "AML-001",
        "Direct sanctions exposure",
        "The address is itself on a sanctions list, or sent value to or received value from an "
        "address that is. Any amount counts.",
        "1.0.0",
        Severity.CRITICAL,
        "direct_sanctions",
        10,
        ALL,
        {"override_min_confidence": 90, "inferred_discount": INFERRED_DISCOUNT},
        exposure.direct_sanctions,
    ),
    Rule(
        "AML-002",
        "Indirect sanctions exposure",
        "Value could have travelled between the address and a sanctioned one through other "
        "addresses, in the order of time.",
        "1.0.0",
        Severity.HIGH,
        "indirect_sanctions",
        20,
        ALL,
        {"max_hops": 4, "inferred_discount": INFERRED_DISCOUNT},
        exposure.indirect_sanctions,
    ),
    Rule(
        "AML-003",
        "Mixer interaction",
        "Value moved between the address and a known mixing service, directly or through "
        "a few others.",
        "1.0.0",
        Severity.HIGH,
        "mixer",
        30,
        ALL,
        {"max_hops": 3, "inferred_discount": INFERRED_DISCOUNT},
        exposure.mixer,
    ),
    Rule(
        "AML-004",
        "Rapid fund movement",
        "Most of what the address receives leaves again within minutes (first in, first out).",
        "1.0.0",
        Severity.MEDIUM,
        "rapid",
        40,
        ALL,
        {"window_seconds": 3600, "min_ratio": 0.8, "min_transfers": 3, "confidence": 0.6},
        behavior.rapid_movement,
    ),
    Rule(
        "AML-005",
        "Layering pattern",
        "Value keeps moving through several addresses, each hop soon after the last, in the "
        "same asset and for about the same amount. Needs a graph at least `min_hops` deep.",
        "1.0.0",
        Severity.MEDIUM,
        "layering",
        50,
        ALL,
        {
            "min_hops": 3,
            "max_hop_delay_seconds": DAY,
            "min_ratio_percent": 50,
            "max_starts": 50,
            "max_steps": 20_000,
            "confidence": 0.5,
        },
        behavior.layering,
    ),
    Rule(
        "AML-006",
        "Peel chain",
        "A chain of Bitcoin transactions that each pay a small part to one address and the "
        "rest onward. Needs a graph at least `min_steps` deep.",
        "1.0.0",
        Severity.MEDIUM,
        "layering",
        60,
        ("bitcoin",),
        {"min_steps": 3, "max_peel_percent": 20, "confidence": 0.5},
        behavior.peel_chain,
    ),
    Rule(
        "AML-007",
        "High-risk incoming funds",
        "Value could have reached the address from one a source flags as risky (a mixer, a "
        "scam, ransomware, stolen funds, a darknet market or phishing, or sanctions).",
        "1.0.0",
        Severity.HIGH,
        "scam_ransomware",
        70,
        ALL,
        {"max_hops": 3, "inferred_discount": INFERRED_DISCOUNT},
        exposure.incoming_risk,
    ),
    Rule(
        "AML-008",
        "High-risk outgoing funds",
        "Value from the address could have reached one a source flags as risky.",
        "1.0.0",
        Severity.HIGH,
        "scam_ransomware",
        80,
        ALL,
        {"max_hops": 3, "inferred_discount": INFERRED_DISCOUNT},
        exposure.outgoing_risk,
    ),
    Rule(
        "AML-009",
        "Fan-in",
        "Many different addresses paid the address within a short time.",
        "1.0.0",
        Severity.LOW,
        "unusual",
        90,
        ALL,
        {"threshold": 10, "window_seconds": 86_400, "confidence": 0.4},
        behavior.fan_in,
    ),
    Rule(
        "AML-010",
        "Fan-out",
        "The address paid many different addresses within a short time.",
        "1.0.0",
        Severity.LOW,
        "unusual",
        100,
        ALL,
        {"threshold": 10, "window_seconds": 86_400, "confidence": 0.4},
        behavior.fan_out,
    ),
    Rule(
        "AML-011",
        "Unusual transaction velocity",
        "The busiest day is far busier than the address's usual day.",
        "1.0.0",
        Severity.LOW,
        "unusual",
        110,
        ALL,
        {"min_per_day": 50, "factor": 10.0, "min_active_days": 3, "confidence": 0.4},
        behavior.velocity,
    ),
    Rule(
        "AML-012",
        "Dormant wallet activation",
        "The address moved value again after a long silence.",
        "1.0.0",
        Severity.LOW,
        "other",
        120,
        ALL,
        {"dormant_days": 180, "recent_days": 30, "confidence": 0.4},
        behavior.dormant_activation,
    ),
    Rule(
        "AML-013",
        "Cross-chain bridge exposure",
        "Value moved between the address and a cross-chain bridge, or a route passes one. Needs "
        "bridge data: none ships with the platform.",
        "1.0.0",
        Severity.LOW,
        "other",
        130,
        ALL,
        {"confidence": 0.6},
        exposure.bridge_exposure,
    ),
    Rule(
        "AML-014",
        "Suspicious concentration",
        "Nearly all of the value in or out of an asset goes through a single counterparty.",
        "1.0.0",
        Severity.LOW,
        "unusual",
        140,
        ALL,
        {"share_percent": 90, "min_transfers": 5, "confidence": 0.3},
        behavior.concentration,
    ),
    Rule(
        "AML-015",
        "Structuring heuristic",
        "Several movements just under a reporting threshold within a few days. Needs "
        "thresholds per asset, which are not shipped: `thresholds` maps an asset key "
        "(`native` or a contract) to amounts in its smallest unit.",
        "1.0.0",
        Severity.MEDIUM,
        "other",
        150,
        ALL,
        {
            "thresholds": {},
            "margin_percent": 10,
            "within_days": 3,
            "min_count": 3,
            "confidence": 0.5,
        },
        behavior.structuring,
    ),
)

BY_ID: Mapping[str, Rule] = {rule.id: rule for rule in RULES}


@dataclass(frozen=True, slots=True)
class EffectiveRule:
    rule: Rule
    enabled: bool
    config: Mapping[str, Any]
    overridden: bool = False

    def to_dict(self) -> dict[str, Any]:
        rule = self.rule
        return {
            "rule_id": rule.id,
            "rule_name": rule.name,
            "description": rule.description,
            "version": rule.version,
            "enabled": self.enabled,
            "severity": rule.severity.value,
            "factor": rule.factor,
            "supported_chains": list(rule.supported_chains),
            "config": dict(self.config),
            "defaults": dict(rule.config),
            "overridden": self.overridden,
        }


def merge_config(rule: Rule, changes: Mapping[str, Any] | None) -> dict[str, Any]:
    """The rule's defaults with `changes` applied. Only settings the rule has can be changed, to a
    value of the same kind."""
    merged = dict(rule.config)
    for name, value in (changes or {}).items():
        if name not in rule.config:
            raise RuleConfigError(f"{rule.id} has no setting {name!r}")
        default = rule.config[name]
        if isinstance(default, bool) or isinstance(value, bool):
            ok = isinstance(default, bool) and isinstance(value, bool)
        elif isinstance(default, (int, float)):
            ok = isinstance(value, (int, float)) and value >= 0
        else:
            ok = isinstance(value, type(default))
        if not ok:
            raise RuleConfigError(f"{rule.id} setting {name!r} must be like {default!r}")
        merged[name] = value
    return merged


def effective_rules(
    overrides: Mapping[str, tuple[bool | None, Mapping[str, Any] | None]] | None = None,
) -> list[EffectiveRule]:
    """Every rule with the operator's overrides applied: rule id -> (enabled, config changes)."""
    overrides = overrides or {}
    unknown = set(overrides) - set(BY_ID)
    if unknown:
        raise RuleConfigError(f"unknown rule(s): {', '.join(sorted(unknown))}")
    result = []
    for rule in RULES:
        enabled, changes = overrides.get(rule.id, (None, None))
        result.append(
            EffectiveRule(
                rule,
                True if enabled is None else enabled,
                merge_config(rule, changes),
                overridden=rule.id in overrides,
            )
        )
    return result
