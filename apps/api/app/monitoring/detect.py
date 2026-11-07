"""Deciding whether a new report on a monitored address deserves an alert.

Pure functions: a report in, a decision out, with no database and no clock, so every rule can be
tested exactly. An alert says what is NEW since the last time the address was judged, so that an
analyst is not told again about what they have already seen:

* a finding (a rule, the other end, the direction) that was not known before;
* the risk level having risen;
* the score having risen by at least a set amount (when the level is one worth hearing about);
* at the very first report, the address already being at a level worth hearing about.

"Worth hearing about" is the monitor's `min_severity`: below it, findings and levels are still in
the stored report, they just do not raise an alert. A report that could not be scored (too little
data) has no level and no score, so it can only raise an alert through its findings.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
LEVELS = ("LOW", "MODERATE", "HIGH", "CRITICAL")
# What a risk level is worth as an alert's severity.
LEVEL_SEVERITY = {"LOW": "LOW", "MODERATE": "MEDIUM", "HIGH": "HIGH", "CRITICAL": "CRITICAL"}

KIND_BASELINE = "baseline_risk"
KIND_NEW_FINDING = "new_finding"
KIND_RISK_INCREASE = "risk_increase"
KIND_DEGRADED = "monitoring_degraded"
KINDS = (KIND_BASELINE, KIND_NEW_FINDING, KIND_RISK_INCREASE, KIND_DEGRADED)

MAX_FINDINGS_SHOWN = 10
MAX_HASHES_SHOWN = 5
MAX_KNOWN = 500


def severity_rank(severity: str | None) -> int:
    """0 (LOW) to 3 (CRITICAL); -1 for anything else, which is below every threshold."""
    return SEVERITIES.index(severity) if severity in SEVERITIES else -1


def level_rank(level: str | None) -> int:
    return LEVELS.index(level) if level in LEVELS else -1


def finding_key(finding: Mapping[str, Any]) -> str:
    """What makes two findings the same finding over time: the rule, the other end and the way the
    value went. Not the evidence (which grows) and not the confidence (which moves)."""
    return (
        f"{finding.get('rule_id')}|{finding.get('target') or ''}|{finding.get('direction') or ''}"
    )


@dataclass(frozen=True, slots=True)
class Snapshot:
    """What was found, boiled down to what is compared from one report to the next."""

    score: int | None
    level: str | None
    keys: frozenset[str]


def snapshot_of(report: Mapping[str, Any]) -> Snapshot:
    return Snapshot(
        score=report.get("risk_score"),
        level=report.get("risk_level"),
        keys=frozenset(finding_key(f) for f in report.get("findings") or []),
    )


@dataclass(frozen=True, slots=True)
class Reason:
    code: str  # new_finding, level_up, score_up, baseline
    detail: str


@dataclass(frozen=True, slots=True)
class AlertDraft:
    kind: str
    severity: str
    title: str
    summary: str
    reasons: tuple[Reason, ...]
    findings: tuple[dict[str, Any], ...]
    previous: Snapshot | None
    current: Snapshot

    def evidence(self) -> dict[str, Any]:
        """The alert's stored evidence: what changed, and the findings that changed it."""
        return {
            "reasons": [{"code": r.code, "detail": r.detail} for r in self.reasons],
            "findings": list(self.findings),
            "previous": None
            if self.previous is None
            else {"score": self.previous.score, "level": self.previous.level},
            "current": {"score": self.current.score, "level": self.current.level},
        }


def trimmed(finding: Mapping[str, Any]) -> dict[str, Any]:
    """The part of a finding worth keeping in an alert (the whole report is kept apart)."""
    hashes = list(finding.get("transaction_hashes") or [])[:MAX_HASHES_SHOWN]
    return {
        "rule_id": finding.get("rule_id"),
        "rule_name": finding.get("rule_name"),
        "severity": finding.get("severity"),
        "description": finding.get("description"),
        "hops": finding.get("hops"),
        "direction": finding.get("direction"),
        "target": finding.get("target"),
        "confidence": finding.get("confidence"),
        "transaction_hashes": hashes,
        "sources": list(finding.get("sources") or []),
    }


def _strongest(severities: Collection[str | None]) -> str:
    ranked = [s for s in severities if severity_rank(s) >= 0]
    return max(ranked, key=severity_rank) if ranked else "LOW"


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def decide(
    *,
    previous: Snapshot | None,
    report: Mapping[str, Any],
    known: Collection[str],
    min_severity: str,
    score_delta: int,
    label: str,
) -> AlertDraft | None:
    """The alert this report calls for, or None. `previous` is what the last report found (None for
    the first), `known` the finding keys already seen, `label` how to name the address."""
    current = snapshot_of(report)
    counted = list(report.get("findings") or [])
    threshold = severity_rank(min_severity)
    level_severity = LEVEL_SEVERITY.get(current.level or "")
    level_loud = level_severity is not None and severity_rank(level_severity) >= threshold

    if previous is None:
        if not level_loud:
            return None
        reasons = (Reason("baseline", f"already {current.level} risk when monitoring began"),)
        shown = tuple(trimmed(f) for f in counted[:MAX_FINDINGS_SHOWN])
        return AlertDraft(
            kind=KIND_BASELINE,
            severity=_strongest([level_severity, *(f.get("severity") for f in counted)]),
            title=f"{label} is already {current.level} risk",
            summary=_summary(reasons, shown),
            reasons=reasons,
            findings=shown,
            previous=None,
            current=current,
        )

    fresh = [f for f in counted if finding_key(f) not in known]
    loud_new = [f for f in fresh if severity_rank(f.get("severity")) >= threshold]
    reasons: list[Reason] = []
    if loud_new:
        reasons.append(
            Reason(
                "new_finding",
                f"{len(loud_new)} new {_plural(len(loud_new), 'finding', 'findings')}",
            )
        )
    if level_loud and level_rank(current.level) > level_rank(previous.level):
        reasons.append(Reason("level_up", f"{previous.level or 'not scored'} to {current.level}"))
    if (
        level_loud
        and current.score is not None
        and previous.score is not None
        and current.score - previous.score >= score_delta
    ):
        reasons.append(Reason("score_up", f"score {previous.score} to {current.score}"))
    if not reasons:
        return None

    shown = tuple(trimmed(f) for f in loud_new[:MAX_FINDINGS_SHOWN])
    severity = _strongest(
        [f.get("severity") for f in loud_new] + ([level_severity] if level_loud else [])
    )
    if loud_new:
        kind = KIND_NEW_FINDING
        title = f"{len(loud_new)} new {_plural(len(loud_new), 'finding', 'findings')} on {label}"
    elif any(r.code == "level_up" for r in reasons):
        kind = KIND_RISK_INCREASE
        title = f"Risk on {label} rose from {previous.level or 'not scored'} to {current.level}"
    else:
        kind = KIND_RISK_INCREASE
        title = f"Risk score on {label} rose from {previous.score} to {current.score}"
    return AlertDraft(
        kind=kind,
        severity=severity,
        title=title,
        summary=_summary(tuple(reasons), shown),
        reasons=tuple(reasons),
        findings=shown,
        previous=previous,
        current=current,
    )


def _summary(reasons: tuple[Reason, ...], findings: tuple[dict[str, Any], ...]) -> str:
    # Not str.capitalize(), which would also turn "CRITICAL" into "critical".
    lines = [f"{r.detail[:1].upper()}{r.detail[1:]}." for r in reasons]
    lines += [f"{f['rule_id']}: {f['description']}" for f in findings[:3]]
    return "\n".join(lines)


def degraded(label: str, failures: int, error: str | None) -> AlertDraft:
    """The alert for a monitored address that could not be checked several times in a row: the
    monitoring is blind, which is not the same as the address being quiet."""
    reasons = (Reason("degraded", f"{failures} checks in a row failed"),)
    detail = f" The last error: {error}" if error else ""
    return AlertDraft(
        kind=KIND_DEGRADED,
        severity="MEDIUM",
        title=f"Monitoring of {label} is not working",
        summary=f"{failures} checks in a row could not be completed, so nothing is known about "
        f"new activity on this address.{detail}",
        reasons=reasons,
        findings=(),
        previous=None,
        current=Snapshot(None, None, frozenset()),
    )


def remember(known: Collection[str], report: Mapping[str, Any]) -> list[str]:
    """The finding keys to remember after this report: what was known and what is in it, the most
    recently seen last, kept to a bounded size."""
    keys = [k for k in known if k not in snapshot_of(report).keys]
    keys += sorted(snapshot_of(report).keys)
    return keys[-MAX_KNOWN:]
