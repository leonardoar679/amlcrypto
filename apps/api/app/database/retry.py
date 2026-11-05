"""Repeating database work that MySQL asked to be repeated."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

T = TypeVar("T")

# MySQL picks a victim when two transactions wait on each other, and gives up on a lock that stays
# held too long. Both are safe to repeat: the victim's work was rolled back.
MYSQL_RETRYABLE_ERRORS = frozenset({1205, 1213})  # lock wait timeout, deadlock
RETRIES = 4


def worth_repeating(error: DBAPIError) -> bool:
    arguments = getattr(error.orig, "args", ())
    return bool(arguments) and arguments[0] in MYSQL_RETRYABLE_ERRORS


def commit_with_retry(session: Session, work: Callable[[], T]) -> T:
    """Run `work`, then commit, and return what `work` returned. When the database chose this
    transaction as a deadlock victim, roll back and run it all again, so `work` must be safe to
    repeat from the start."""
    for attempt in range(RETRIES + 1):
        try:
            result = work()
            session.commit()
            return result
        except OperationalError as error:
            session.rollback()
            if attempt == RETRIES or not worth_repeating(error):
                raise
            time.sleep(random.uniform(0, 0.02) * (attempt + 1))
