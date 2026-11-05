"""Try several providers for the same job and use the first one that answers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from app.providers.base import AllProvidersFailed, BudgetExceeded, ProviderError

T = TypeVar("T")


async def first_success(
    capability: str, attempts: Sequence[tuple[str, Callable[[], Awaitable[T]]]]
) -> tuple[str, T]:
    """Run each (provider name, call) in order; return the name and result of the first that works.

    Any provider error, including "not configured", moves on to the next provider. If none works
    the error carries every provider's reason and the shortest wait any of them asked for. An
    exhausted request budget stops the search: no other provider can help.
    """
    failures: list[tuple[str, ProviderError]] = []
    for name, call in attempts:
        try:
            return name, await call()
        except BudgetExceeded:
            raise
        except ProviderError as error:
            failures.append((name, error))
    raise AllProvidersFailed(capability, failures)
