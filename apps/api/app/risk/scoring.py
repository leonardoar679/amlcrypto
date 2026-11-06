"""From findings to a score and a confidence (blueprint section 7).

The constants here are the blueprint's illustrative values and reasonable defaults, NOT calibrated
against reviewed cases. They are stored with every assessment so that a score can always be
explained, and they are meant to be changed once there are cases to calibrate against.

How findings become a score:

1. Two findings about the same event (the same key) are counted once, the one from the rule with
   the lower priority number; the other is listed as a duplicate that was not counted.
2. A finding adds `cap x hop weight x confidence x time decay x direction weight`, where the cap is
   the most its factor may add (direct sanctions 100, indirect sanctions 40, mixers 35, scams and
   ransomware 40, layering 25, rapid movement 15, unusual behaviour 15, other 10).
3. Inside a factor the contributions combine as 1 - product(1 - c / cap), times the cap, so
   several weak findings add up but never pass the factor's cap and one strong finding is not
   diluted by others.
4. The factors add up, to at most 100. A policy override (a direct match with an official sanctions
   source) makes the score 100 whatever else was found, and says which rule did.
5. The score is shown as a level: 0-19 LOW, 20-39 MODERATE, 40-69 HIGH, 70-100 CRITICAL.

Confidence is a separate number: how firm the evidence behind the findings is (their own
confidences, weighted by what they contributed), reduced when the data behind them is partial and
when a label source is stale. With no findings it says how far a negative result can be trusted.
It is not a probability, and neither number means a wallet is safe or guilty.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.risk.model import FACTOR_CAPS, Coverage, Finding, Level, Score, ScoredFinding


@dataclass(frozen=True, slots=True)
class ScoringSettings:
    factor_caps: Mapping[str, int] = field(default_factory=lambda: dict(FACTOR_CAPS))
    # What a finding is worth by how many hops away it is (blueprint 7.1), and beyond the table.
    hop_weights: Mapping[int, float] = field(
        default_factory=lambda: {1: 1.0, 2: 0.7, 3: 0.4, 4: 0.2}
    )
    farther_hops_weight: float = 0.1
    # Whether value that arrived from a risky address weighs more than value sent to one.
    direction_weights: Mapping[str, float] = field(
        default_factory=lambda: {"incoming": 1.0, "outgoing": 1.0}
    )
    # Older evidence counts for less: full for `decay_full_days`, falling to `decay_floor` by
    # `decay_floor_days`. A sanction does not expire, so its factor does not decay.
    decay_full_days: int = 90
    decay_floor: float = 0.5
    decay_floor_days: int = 365
    no_decay_factors: tuple[str, ...] = ("direct_sanctions",)
    level_bounds: tuple[int, int, int] = (20, 40, 70)  # where MODERATE, HIGH, CRITICAL begin
    # Confidence is reduced by how much of the data behind the assessment is missing ...
    coverage_multipliers: Mapping[str, float] = field(
        default_factory=lambda: {
            Coverage.COMPLETE.value: 1.0,
            Coverage.PARTIAL.value: 0.7,
            Coverage.INSUFFICIENT.value: 0.2,
        }
    )
    stale_source_multiplier: float = 0.8  # ... and when a label source is stale or never fetched
    no_finding_confidence: float = 0.9  # before those reductions

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_caps": dict(self.factor_caps),
            "hop_weights": {str(k): v for k, v in self.hop_weights.items()},
            "farther_hops_weight": self.farther_hops_weight,
            "direction_weights": dict(self.direction_weights),
            "decay": {
                "full_days": self.decay_full_days,
                "floor": self.decay_floor,
                "floor_days": self.decay_floor_days,
                "no_decay_factors": list(self.no_decay_factors),
            },
            "level_bounds": list(self.level_bounds),
            "coverage_multipliers": dict(self.coverage_multipliers),
            "stale_source_multiplier": self.stale_source_multiplier,
            "no_finding_confidence": self.no_finding_confidence,
            "calibrated": False,
        }


def level_of(value: int, settings: ScoringSettings) -> Level:
    moderate, high, critical = settings.level_bounds
    if value >= critical:
        return Level.CRITICAL
    if value >= high:
        return Level.HIGH
    if value >= moderate:
        return Level.MODERATE
    return Level.LOW


def decay(finding: Finding, now: datetime, settings: ScoringSettings) -> float:
    if finding.latest_at is None or finding.factor in settings.no_decay_factors:
        return 1.0
    age = (now - finding.latest_at).total_seconds() / 86_400
    if age <= settings.decay_full_days:
        return 1.0
    if age >= settings.decay_floor_days:
        return settings.decay_floor
    span = settings.decay_floor_days - settings.decay_full_days
    return 1.0 - (1.0 - settings.decay_floor) * (age - settings.decay_full_days) / span


def hop_weight(hops: int | None, settings: ScoringSettings) -> float:
    if hops is None or hops <= 0:
        return 1.0
    return settings.hop_weights.get(hops, settings.farther_hops_weight)


def contribution(finding: Finding, now: datetime, settings: ScoringSettings) -> float:
    cap = settings.factor_caps[finding.factor]
    direction = settings.direction_weights.get(finding.direction or "", 1.0)
    return (
        cap
        * hop_weight(finding.hops, settings)
        * finding.confidence
        * decay(finding, now, settings)
        * direction
    )


def suppress(
    findings: Iterable[Finding], priorities: Mapping[str, int]
) -> list[tuple[Finding, str | None]]:
    """Each finding with the rule whose finding of the same event is the one counted, if it is not
    this one. The order of the findings is kept."""
    ordered = list(findings)
    winners: dict[tuple[str, ...], Finding] = {}
    for finding in sorted(ordered, key=lambda f: (priorities[f.rule_id], f.rule_id)):
        winners.setdefault(finding.key, finding)
    return [(f, None if winners[f.key] is f else winners[f.key].rule_id) for f in ordered]


def score_findings(
    findings: Iterable[Finding],
    *,
    priorities: Mapping[str, int],
    coverage: Coverage,
    sources_fresh: bool,
    now: datetime,
    settings: ScoringSettings,
) -> Score:
    scored = []
    for finding, duplicate_of in suppress(findings, priorities):
        points = 0.0 if duplicate_of else contribution(finding, now, settings)
        scored.append(ScoredFinding(finding, points, duplicate_of))

    counted = [item for item in scored if item.suppressed_by is None]
    unspent: dict[str, float] = defaultdict(lambda: 1.0)
    for item in counted:
        cap = settings.factor_caps[item.finding.factor]
        unspent[item.finding.factor] *= 1.0 - min(item.contribution, cap) / cap
    factors = {
        name: settings.factor_caps[name] * (1.0 - left) for name, left in sorted(unspent.items())
    }

    override = next(
        (
            item.finding.rule_id
            for item in sorted(counted, key=lambda i: priorities[i.finding.rule_id])
            if item.finding.override
        ),
        None,
    )
    total = 100.0 if override else min(100.0, sum(factors.values()))
    value = round(total)

    multiplier = settings.coverage_multipliers[coverage.value] * (
        1.0 if sources_fresh else settings.stale_source_multiplier
    )
    if counted:
        weight = sum(item.contribution for item in counted)
        if weight > 0:
            base = sum(item.finding.confidence * item.contribution for item in counted) / weight
        else:
            base = sum(item.finding.confidence for item in counted) / len(counted)
    else:
        base = settings.no_finding_confidence
    return Score(
        value=value,
        level=level_of(value, settings),
        confidence=max(0.0, min(1.0, base * multiplier)),
        factors=factors,
        override=override,
        scored=tuple(scored),
        settings=settings.to_dict(),
    )
