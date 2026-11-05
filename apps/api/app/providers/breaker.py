"""A circuit breaker: after repeated failures stop calling a provider for a while."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"  # normal operation
    OPEN = "open"  # failing fast
    HALF_OPEN = "half_open"  # cooling-off over; one trial request is allowed


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        cooldown: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self._clock = clock
        self._wall_clock = wall_clock
        self._state = CircuitState.CLOSED
        self._opened_at = 0.0
        self._probe_in_flight = False
        self.consecutive_failures = 0
        self.last_success_at: datetime | None = None
        self.last_error_at: datetime | None = None
        self.last_error: str | None = None

    @property
    def state(self) -> CircuitState:
        if self._state is CircuitState.OPEN and self._clock() - self._opened_at >= self.cooldown:
            return CircuitState.HALF_OPEN
        return self._state

    def retry_in(self) -> float | None:
        """Seconds until a trial request is allowed again; None unless the circuit is open."""
        if self._state is not CircuitState.OPEN:
            return None
        remaining = self.cooldown - (self._clock() - self._opened_at)
        return remaining if remaining > 0 else None

    def before_call(self) -> bool:
        """True if a request may be made now. In half-open state exactly one trial is allowed."""
        state = self.state
        if state is CircuitState.CLOSED:
            return True
        if state is CircuitState.HALF_OPEN and not self._probe_in_flight:
            self._state = CircuitState.HALF_OPEN
            self._probe_in_flight = True
            return True
        return False

    def on_success(self) -> None:
        self._state = CircuitState.CLOSED
        self._probe_in_flight = False
        self.consecutive_failures = 0
        self.last_success_at = self._wall_clock()

    def on_failure(self, reason: str) -> None:
        self.consecutive_failures += 1
        self.last_error_at = self._wall_clock()
        self.last_error = reason
        was_trial = self._state is CircuitState.HALF_OPEN
        self._probe_in_flight = False
        if was_trial or self.consecutive_failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = self._clock()

    def release_probe(self) -> None:
        """Give back the half-open trial slot when the call was abandoned before it was made."""
        self._probe_in_flight = False

    def note_error(self, reason: str) -> None:
        """Record an error that should be visible in status but must not count towards opening."""
        self.last_error_at = self._wall_clock()
        self.last_error = reason
        self._probe_in_flight = False
