"""Where provider usage events go: nowhere, memory (tests), or the api_usage table."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.database.repositories.usage import record_usage


@dataclass(frozen=True, slots=True)
class UsageEvent:
    provider: str
    outcome: str  # "success" | "error" | "rate_limited"
    retry: bool  # this attempt was a retry of an earlier one
    status_code: int | None
    latency_ms: float
    at: datetime


class UsageSink(Protocol):
    async def record(self, event: UsageEvent) -> None: ...


class NullUsageSink:
    async def record(self, event: UsageEvent) -> None:
        return None


class MemoryUsageSink:
    def __init__(self) -> None:
        self.events: list[UsageEvent] = []

    async def record(self, event: UsageEvent) -> None:
        self.events.append(event)


class DbUsageSink:
    """Counts each network attempt in api_usage.

    Takes a function returning the *write* session factory, looked up on every event so that a
    factory swapped in later (tests do this) is the one used.
    """

    def __init__(self, get_write_session_factory: Callable[[], sessionmaker[Session]]) -> None:
        self._get_factory = get_write_session_factory

    async def record(self, event: UsageEvent) -> None:
        await asyncio.to_thread(self._write, event)

    def _write(self, event: UsageEvent) -> None:
        with self._get_factory()() as session:
            record_usage(
                session,
                provider=event.provider,
                at=event.at,
                successes=int(event.outcome == "success"),
                errors=int(event.outcome == "error"),
                rate_limited=int(event.outcome == "rate_limited"),
                retries=int(event.retry),
            )
