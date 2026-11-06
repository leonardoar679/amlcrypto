"""Risk assessments: what was found about an address or a transaction, kept so that a screening can
be read again, compared with an earlier one, and explained.

* `risk_assessments` are tenant-owned (they carry `organization_id`): one row per screening, from
  the request (QUEUED) to the report. The whole report is kept as a document, so a stored
  assessment is returned exactly as it was produced, whatever the rules have become since.
* `risk_findings` are the same findings as rows, to ask questions across assessments ("every
  screening in which AML-001 fired"). They carry the rule's version and whether the finding was
  counted or was a duplicate of one that was.
* `risk_rules` hold only what an operator changed about a rule (switched off, a threshold). The
  rules themselves and their defaults are code (app.risk.rules.registry); a missing row means the
  defaults. There is no `exposure_paths` table: a path is evidence inside its finding.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IntegerPK, TimestampMixin, UTCDateTime, identifier
from app.database.models.chain_data import CHAIN_ID_LENGTH, HASH_LENGTH


class RiskAssessment(TimestampMixin, Base):
    __tablename__ = "risk_assessments"
    __table_args__ = (
        Index("ix_risk_assessments_subject", "organization_id", "chain_id", "subject", "id"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    job_id: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("fetch_jobs.id", ondelete="SET NULL"), nullable=True
    )
    subject_type: Mapped[str] = mapped_column(String(12), nullable=False)  # address, transaction
    chain_id: Mapped[str] = mapped_column(
        identifier(CHAIN_ID_LENGTH), ForeignKey("chains.id", ondelete="RESTRICT"), nullable=False
    )
    subject: Mapped[str] = mapped_column(identifier(HASH_LENGTH), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # QUEUED ... FAILED
    options: Mapped[Any] = mapped_column(JSON, nullable=False)  # what was asked for
    risk_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    confidence_percent: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    data_coverage: Mapped[str | None] = mapped_column(String(14), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    report: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # why a screening FAILED
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class RiskFinding(Base):
    __tablename__ = "risk_findings"
    __table_args__ = (Index("ix_risk_findings_rule_id_severity", "rule_id", "severity"),)

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    assessment_id: Mapped[int] = mapped_column(
        IntegerPK,
        ForeignKey("risk_assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_id: Mapped[str] = mapped_column(String(16), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    factor: Mapped[str] = mapped_column(String(24), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    hops: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    target: Mapped[str | None] = mapped_column(String(200), nullable=True)
    confidence_percent: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # Score points, in hundredths (an integer: no float is ever stored).
    contribution_hundredths: Mapped[int] = mapped_column(Integer, nullable=False)
    counted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    suppressed_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    transaction_hashes: Mapped[Any] = mapped_column(JSON, nullable=False)
    evidence: Mapped[Any] = mapped_column(JSON, nullable=False)
    sources: Mapped[Any] = mapped_column(JSON, nullable=False)
    latest_movement_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class RiskRule(TimestampMixin, Base):
    """What an operator changed about a rule; the rule's code and defaults live in the registry."""

    __tablename__ = "risk_rules"

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # NULL: the default
    config: Mapped[Any | None] = mapped_column(JSON, nullable=True)  # only the settings changed
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
