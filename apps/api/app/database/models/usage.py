"""Provider usage counters, one row per provider per UTC hour."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IntegerPK, TimestampMixin, UTCDateTime


class ApiUsage(TimestampMixin, Base):
    __tablename__ = "api_usage"
    __table_args__ = (
        UniqueConstraint("provider", "period_start", name="uq_api_usage_provider_period_start"),
    )

    id: Mapped[int] = mapped_column(IntegerPK, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)  # start of the hour
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # network attempts
    successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_limited: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
