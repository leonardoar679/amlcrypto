"""The bounds of the numbers a request may carry.

Every integer a route accepts has both bounds. An unbounded one is an overflow waiting somewhere
behind it: SQLite integers are 64-bit, a timedelta holds at most 999,999,999 days, and a limit of a
few billion is a request for the whole database. Out of range, a value is refused with a 422 at the
edge instead of being answered with a 500 from inside; tests/test_hostile_input.py reads the OpenAPI
schema and fails for any integer that is left unbounded.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

MAX_ID = 2**63 - 1  # the largest id a row can have
MAX_COUNT = 1_000_000  # a number of nodes, edges, transfers, counterparties
MAX_DAYS = 36_500  # a hundred years
MAX_HOPS = 6  # how far from an address a graph reaches

# An id in a path: a positive number a row could have.
IdPath = Annotated[int, Path(ge=1, le=MAX_ID)]
