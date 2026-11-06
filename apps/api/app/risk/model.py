"""The records of a risk assessment (blueprint sections 6.3, 7 and 8.2).

A risk assessment is a list of findings, each saying which rule fired, on what evidence, and how
firmly, and two numbers that must not be confused: the risk score (how much concern the findings
add up to) and the confidence (how far the evidence and the data behind them can be trusted).

NO FINDING IS NOT A CLEARANCE. A rule that could not be judged (the data was missing, the chain is
not one it applies to, it needs a setting nobody made) says so in the report; a report without
findings says what was and was not checked.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.chains.models import iso


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Level(StrEnum):
    """How a score is presented (blueprint 7.1: 0-19, 20-39, 40-69, 70-100)."""

    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RuleStatus(StrEnum):
    TRIGGERED = "triggered"
    CLEAR = "clear"  # judged, and nothing was found: not proof of anything
    NOT_EVALUATED = "not_evaluated"  # could not be judged; `reason` says why
    ERROR = "error"  # the rule itself failed


class Coverage(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"  # the subject's own history is not held


class Outcome(StrEnum):
    FINDINGS = "findings"
    NO_FINDINGS = "no_findings"  # judged what could be judged, and found nothing
    INSUFFICIENT_DATA = "insufficient_data"  # too little was held to judge


class ScreeningStatus(StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# The factors a score is made of (blueprint 7.1) and the most each can add.
FACTOR_CAPS: Mapping[str, int] = {
    "direct_sanctions": 100,
    "indirect_sanctions": 40,
    "mixer": 35,
    "scam_ransomware": 40,
    "layering": 25,
    "rapid": 15,
    "unusual": 15,
    "other": 10,
}

MAX_EVIDENCE_TRANSACTIONS = 10


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing a rule found, with the evidence for it."""

    rule_id: str
    rule_name: str
    rule_version: str
    severity: Severity
    factor: str
    description: str
    # What makes two findings the same event: a finding with a key another finding of a
    # higher-priority rule has is a duplicate and is not counted twice.
    key: tuple[str, ...]
    confidence: float  # 0..1: how firmly the evidence supports it
    hops: int | None = None
    direction: str | None = None  # "incoming" or "outgoing", for exposure
    target: str | None = None  # the node id of the other end
    transactions: tuple[str, ...] = ()
    transactions_total: int = 0
    evidence: tuple[Mapping[str, Any], ...] = ()
    sources: tuple[str, ...] = ()  # the label sources behind it, if any
    last_verified: datetime | None = None  # when the newest label behind it was confirmed
    latest_at: datetime | None = None  # when the newest movement behind it happened
    override: bool = False  # a policy that makes the whole assessment critical

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "rule_version": self.rule_version,
            "severity": self.severity.value,
            "factor": self.factor,
            "description": self.description,
            "hops": self.hops,
            "direction": self.direction,
            "target": self.target,
            "confidence": round(self.confidence, 2),
            "transaction_hashes": list(self.transactions),
            "transactions_total": self.transactions_total,
            "evidence": [dict(item) for item in self.evidence],
            "sources": list(self.sources),
            "last_verified": iso(self.last_verified),
            "latest_movement": iso(self.latest_at),
            "policy_override": self.override,
        }


@dataclass(frozen=True, slots=True)
class ScoredFinding:
    finding: Finding
    contribution: float  # score points it adds before the factor's cap; 0 when suppressed
    suppressed_by: str | None = None  # the rule whose finding of the same event is counted

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.finding.to_dict(),
            "contribution": round(self.contribution, 2),
            "suppressed_by": self.suppressed_by,
        }


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    status: RuleStatus
    findings: tuple[Finding, ...] = ()
    reason: str | None = None  # why a rule was not evaluated, or what failed

    @classmethod
    def triggered(cls, findings: list[Finding]) -> RuleOutcome:
        return cls(RuleStatus.TRIGGERED, tuple(findings))

    @classmethod
    def clear(cls) -> RuleOutcome:
        return cls(RuleStatus.CLEAR)

    @classmethod
    def not_evaluated(cls, reason: str) -> RuleOutcome:
        return cls(RuleStatus.NOT_EVALUATED, reason=reason)


@dataclass(frozen=True, slots=True)
class RuleReport:
    """What happened to one rule in one assessment."""

    rule_id: str
    name: str
    version: str
    status: RuleStatus
    reason: str | None
    config: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "version": self.version,
            "status": self.status.value,
            "reason": self.reason,
            "config": dict(self.config),
        }


@dataclass(frozen=True, slots=True)
class Score:
    """How the findings add up, with enough detail to redo the sum by hand."""

    value: int  # 0..100
    level: Level
    confidence: float  # 0..1; not a probability, and not the same thing as the score
    factors: Mapping[str, float]  # points per factor after noisy-or and the factor's cap
    override: str | None  # the rule whose policy set the score, if one did
    scored: tuple[ScoredFinding, ...]
    settings: Mapping[str, Any]  # the constants it used

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_score": self.value,
            "risk_level": self.level.value,
            "confidence": round(self.confidence, 2),
            "factors": {name: round(points, 2) for name, points in self.factors.items()},
            "policy_override": self.override,
            "settings": dict(self.settings),
        }


@dataclass(slots=True)
class Report:
    """The result of screening one address (or transaction)."""

    subject_type: str  # "address" or "transaction"
    chain_id: str
    chain: str  # slug
    subject: str
    generated_at: datetime
    coverage: Coverage
    outcome: Outcome
    score: Score | None
    rules: list[RuleReport] = field(default_factory=list)
    analysis: dict[str, Any] = field(default_factory=dict)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)

    @property
    def findings(self) -> list[ScoredFinding]:
        return [
            item for item in (self.score.scored if self.score else ()) if not item.suppressed_by
        ]

    @property
    def suppressed(self) -> list[ScoredFinding]:
        return [item for item in (self.score.scored if self.score else ()) if item.suppressed_by]

    def to_dict(self) -> dict[str, Any]:
        score = (
            self.score.to_dict()
            if self.score
            else {"risk_score": None, "risk_level": None, "confidence": None}
        )
        return {
            "subject": {
                "type": self.subject_type,
                "chain": self.chain,
                "chain_id": self.chain_id,
                "id": self.subject,
            },
            **score,
            "data_coverage": self.coverage.value,
            "outcome": self.outcome.value,
            "findings": [item.to_dict() for item in self.findings],
            "duplicates_not_counted": [item.to_dict() for item in self.suppressed],
            "rules": [rule.to_dict() for rule in self.rules],
            "analysis": self.analysis,
            "data_gaps": self.gaps,
            "warnings": self.warnings,
            "label_sources": self.sources,
            "generated_at": iso(self.generated_at),
            "disclaimer": (
                "Findings are indicators of risk, not proof of wrongdoing. The absence of a "
                "finding does not mean an address or transaction is safe: see the rules that "
                "could not be evaluated and the data gaps."
            ),
        }
