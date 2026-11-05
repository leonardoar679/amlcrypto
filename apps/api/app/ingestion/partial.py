"""What a read that dies half way leaves behind.

A history read is many requests. When one of them fails for good (a rate limit that outlasts the
retries, an outage), the pages already read are still good data and cost real quota. The readers
attach what they had to the error before re-raising it, so a caller that stores histories can keep
it, while a caller that does not sees the same exception as ever.
"""

from __future__ import annotations

from typing import Any

_ATTRIBUTE = "partial_history"


def attach_partial(error: BaseException, history: Any) -> None:
    setattr(error, _ATTRIBUTE, history)


def partial_of(error: BaseException) -> Any | None:
    return getattr(error, _ATTRIBUTE, None)
