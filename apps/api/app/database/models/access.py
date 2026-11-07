"""API keys and the audit log.

* `api_keys` are tenant-owned credentials for programs. Only a SHA-256 of the secret is kept: the
  key itself is shown once, when it is made. A key is as strong as its role, never stronger than
  the person who made it, and dies with them (see app.access.apikeys).
* `audit_logs` are append-only: nothing in the application updates or deletes a row. Every row
  says who (a user, a key, the system or nobody known), what, to what, with what outcome, from
  where, and never carries a secret. `organization_id` is empty for events that belong to no
  organization (a sign-in as an address nobody has registered).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IntegerPK, TimestampMixin, UTCDateTime, utcnow


class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_by: Mapped[int] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)  # public id
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 of the secret
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_by: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_organization_id_created_at", "organization_id", "created_at"),
        # What the sign-in lockout counts: recent failures of one account.
        Index("ix_audit_logs_action_subject_key_created_at", "action", "subject_key", "created_at"),
        Index("ix_audit_logs_target_type_target_id", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    organization_id: Mapped[int | None] = mapped_column(
        IntegerPK, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True
    )
    actor_type: Mapped[str] = mapped_column(
        String(12), nullable=False
    )  # user, api_key, system, anonymous
    actor_id: Mapped[int | None] = mapped_column(
        IntegerPK, nullable=True
    )  # no FK: rows outlive them
    actor_label: Mapped[str | None] = mapped_column(String(320), nullable=True)  # email or key name
    action: Mapped[str] = mapped_column(String(60), nullable=False)  # "screening.request"
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)  # success, denied, failure
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # A hash that groups events about one account without storing what was typed (the sign-in
    # lockout counts by it; a mistyped email field may hold a password).
    subject_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[Any] = mapped_column(JSON, nullable=False, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
