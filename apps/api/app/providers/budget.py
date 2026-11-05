"""A cap on how many provider requests one investigation may spend."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.providers.base import BudgetExceeded


@dataclass
class RequestBudget:
    """Each network attempt costs one request, retries included: they spend provider quota too."""

    limit: int
    used: int = 0
    by_provider: dict[str, int] = field(default_factory=dict)

    def consume(self, provider: str, count: int = 1) -> None:
        if self.used + count > self.limit:
            raise BudgetExceeded(provider, f"request budget of {self.limit} is used up")
        self.used += count
        self.by_provider[provider] = self.by_provider.get(provider, 0) + count

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit
