"""What a job handler receives and how handlers are registered."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.providers.service import ProviderService


class NonRetryable(Exception):
    """Raised by a handler when running it again cannot help (a payload that makes no sense)."""


@dataclass(frozen=True, slots=True)
class JobContext:
    job_id: int
    job_type: str
    payload: dict[str, Any]
    attempt: int  # 1 on the first run
    max_attempts: int
    organization_id: int | None
    providers: ProviderService
    settings: Settings


# A handler returns the dict to store as the job's result (or None). Raising ends the attempt:
# NonRetryable and ProviderError decide for themselves whether the job is tried again.
Handler = Callable[[JobContext], Awaitable[dict[str, Any] | None]]


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, job_type: str) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            if job_type in self._handlers:
                raise ValueError(f"a handler for {job_type!r} is already registered")
            self._handlers[job_type] = handler
            return handler

        return decorator

    def get(self, job_type: str) -> Handler | None:
        return self._handlers.get(job_type)

    def job_types(self) -> set[str]:
        return set(self._handlers)
