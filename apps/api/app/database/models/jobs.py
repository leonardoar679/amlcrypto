"""The background job queue. Claiming is a portable compare-and-set (see repositories/jobs)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IntegerPK, TimestampMixin, UTCDateTime


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"  # out of attempts; terminal


class FetchJob(TimestampMixin, Base):
    __tablename__ = "fetch_jobs"
    __table_args__ = (Index("ix_fetch_jobs_status_available_at", "status", "available_at"),)

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    # Who asked for the work. Null for system jobs (provider probes, scheduled refreshes).
    organization_id: Mapped[int | None] = mapped_column(
        IntegerPK,
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=JobStatus.QUEUED.value)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # higher runs first
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Held only while the job is active (cleared on completion), so an identical request can be
    # queued again afterwards. NULLs do not collide in a unique index on SQLite or MySQL.
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
