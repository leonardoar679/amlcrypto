"""A small in-memory TTL cache for repeatable, read-only provider requests.

This is the first cache level only (see the blueprint's L1/L2/L3 note). Durable reuse of transaction
history lives in the database and arrives with milestone M8.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any

MISSING: Any = object()


class TtlCache:
    def __init__(
        self, max_entries: int = 1024, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._max_entries = max_entries
        self._clock = clock
        self._items: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()

    def get(self, key: Hashable) -> Any:
        entry = self._items.get(key)
        if entry is None:
            return MISSING
        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._items[key]
            return MISSING
        self._items.move_to_end(key)
        return value

    def set(self, key: Hashable, value: Any, ttl: float) -> None:
        self._items[key] = (self._clock() + ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self._max_entries:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)
