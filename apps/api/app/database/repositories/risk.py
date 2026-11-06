"""Reading and writing risk assessments and the operator's changes to the rules.

Assessments belong to an organization and are never read across organizations: every lookup here
takes the organization, and an assessment of another one is simply not found.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database.models import RiskAssessment, RiskFinding, RiskRule
from app.risk.model import Report, ScreeningStatus

# --- assessments -------------------------------------------------------------------------------


def create_assessment(
    session: Session,
    *,
    organization_id: int,
    subject_type: str,
    chain_id: str,
    subject: str,
    options: Mapping[str, Any],
    job_id: int | None = None,
) -> RiskAssessment:
    row = RiskAssessment(
        organization_id=organization_id,
        job_id=job_id,
        subject_type=subject_type,
        chain_id=chain_id,
        subject=subject,
        status=ScreeningStatus.QUEUED.value,
        options=dict(options),
    )
    session.add(row)
    session.flush()
    return row


def get_assessment(
    session: Session, assessment_id: int, organization_id: int | None = None
) -> RiskAssessment | None:
    """The assessment, or None if there is none or it belongs to another organization. Pass no
    organization only from the worker, which acts on a row it was handed."""
    row = session.get(RiskAssessment, assessment_id)
    if row is None or (organization_id is not None and row.organization_id != organization_id):
        return None
    return row


def mark_processing(session: Session, assessment_id: int, now: datetime | None = None) -> None:
    row = session.get(RiskAssessment, assessment_id)
    if row is not None and row.status in (ScreeningStatus.QUEUED, ScreeningStatus.PROCESSING):
        row.status = ScreeningStatus.PROCESSING.value
        row.started_at = row.started_at or now or datetime.now(UTC)


def complete_assessment(
    session: Session, assessment_id: int, report: Report, now: datetime | None = None
) -> RiskAssessment:
    """Keep the report, as a document and as findings, and mark the screening done."""
    row = session.get(RiskAssessment, assessment_id)
    assert row is not None
    document = report.to_dict()
    row.status = ScreeningStatus.COMPLETED.value
    row.risk_score = document["risk_score"]
    row.risk_level = document["risk_level"]
    row.confidence_percent = (
        None if document["confidence"] is None else round(document["confidence"] * 100)
    )
    row.data_coverage = report.coverage.value
    row.outcome = report.outcome.value
    row.report = document
    row.error = None
    row.completed_at = now or datetime.now(UTC)

    session.execute(delete(RiskFinding).where(RiskFinding.assessment_id == assessment_id))
    for item in report.score.scored if report.score else ():
        finding = item.finding
        session.add(
            RiskFinding(
                assessment_id=assessment_id,
                rule_id=finding.rule_id,
                rule_version=finding.rule_version,
                severity=finding.severity.value,
                factor=finding.factor,
                description=finding.description,
                hops=finding.hops,
                direction=finding.direction,
                target=finding.target,
                confidence_percent=round(finding.confidence * 100),
                contribution_hundredths=round(item.contribution * 100),
                counted=item.suppressed_by is None,
                suppressed_by=item.suppressed_by,
                transaction_hashes=list(finding.transactions),
                evidence=[dict(entry) for entry in finding.to_dict()["evidence"]],
                sources=list(finding.sources),
                latest_movement_at=finding.latest_at,
                last_verified_at=finding.last_verified,
            )
        )
    session.flush()
    return row


def fail_assessment(
    session: Session, assessment_id: int, error: str, now: datetime | None = None
) -> None:
    row = session.get(RiskAssessment, assessment_id)
    if row is not None:
        row.status = ScreeningStatus.FAILED.value
        row.error = error[:2000]
        row.completed_at = now or datetime.now(UTC)


def completed_assessments(
    session: Session,
    organization_id: int,
    chain_id: str,
    subject: str,
    *,
    limit: int = 20,
) -> list[RiskAssessment]:
    """The organization's finished screenings of one subject, newest first."""
    return list(
        session.scalars(
            select(RiskAssessment)
            .where(
                RiskAssessment.organization_id == organization_id,
                RiskAssessment.chain_id == chain_id,
                RiskAssessment.subject == subject,
                RiskAssessment.status == ScreeningStatus.COMPLETED.value,
            )
            .order_by(RiskAssessment.id.desc())
            .limit(limit)
        )
    )


def findings_of(session: Session, assessment_id: int) -> list[RiskFinding]:
    return list(
        session.scalars(
            select(RiskFinding)
            .where(RiskFinding.assessment_id == assessment_id)
            .order_by(RiskFinding.id)
        )
    )


# --- rule overrides ----------------------------------------------------------------------------


def rule_overrides(session: Session) -> dict[str, tuple[bool | None, Mapping[str, Any] | None]]:
    """What an operator changed: rule id -> (enabled or None for the default, settings changed)."""
    return {row.rule_id: (row.enabled, row.config) for row in session.scalars(select(RiskRule))}


def set_rule_override(
    session: Session,
    rule_id: str,
    *,
    enabled: bool | None,
    config: Mapping[str, Any] | None,
    note: str | None = None,
) -> None:
    """Replace the override of a rule. Validate it first (see registry.merge_config)."""
    row = session.scalar(select(RiskRule).where(RiskRule.rule_id == rule_id))
    if row is None:
        row = RiskRule(rule_id=rule_id)
        session.add(row)
    row.enabled = enabled
    row.config = None if config is None else dict(config)
    row.note = note


def clear_rule_override(session: Session, rule_id: str) -> bool:
    row = session.scalar(select(RiskRule).where(RiskRule.rule_id == rule_id))
    if row is None:
        return False
    session.delete(row)
    return True
