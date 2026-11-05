"""A token bucket for staying inside a provider's request rate."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

# A billionth of a token: far below anything meaningful, and far above floating point noise.
TOLERANCE = 1e-9


class TokenBucket:
    """Allows `rate` requests per second, with bursts up to `capacity`. Waiters go in order."""

    def __init__(
        self,
        rate: float,
        capacity: float | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.capacity = capacity if capacity is not None else max(1.0, rate)
        self._clock = clock
        self._sleep = sleep
        self._tokens = self.capacity
        self._updated = clock()
        self._blocked_until = 0.0
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    async def acquire(self, tokens: float = 1.0) -> float:
        """Wait until `tokens` are available and take them. Returns the seconds spent waiting."""
        if tokens > self.capacity:
            raise ValueError("cannot acquire more tokens than the bucket holds")
        waited = 0.0
        async with self._lock:
            while True:
                now = self._clock()
                if now < self._blocked_until:
                    delay = self._blocked_until - now
                else:
                    self._refill()
                    # Floating point can leave the bucket a hair under what it needs. Waiting for
                    # that hair would add less than the clock can represent: no progress, forever.
                    if self._tokens >= tokens - TOLERANCE:
                        self._tokens -= tokens
                        return waited
                    delay = (tokens - self._tokens) / self.rate
                await self._sleep(delay)
                waited += delay

    def penalize(self, seconds: float) -> None:
        """After a 429: release nothing for `seconds` and spend the burst allowance."""
        self._blocked_until = max(self._blocked_until, self._clock() + seconds)
        self._tokens = 0.0
        self._updated = self._clock()
