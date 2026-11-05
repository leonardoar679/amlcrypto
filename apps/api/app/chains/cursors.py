"""Cursors: where to carry on reading a long history.

A cursor is an opaque string to everyone but the source that issued it. The adapter prefixes the
name of the source ("etherscan|...", "trongrid|..."), so a cursor is never handed to a source that
did not issue it: a position in one provider's history means nothing to another.
"""

from __future__ import annotations

import re

_SOURCE_PREFIX = re.compile(r"^([a-z0-9_]{1,20})\|(.*)$", re.DOTALL)


class InvalidCursor(ValueError):
    """The cursor was not issued by this source (or was altered)."""


def with_source(source: str, cursor: str | None) -> str | None:
    return None if cursor is None else f"{source}|{cursor}"


def split_source(cursor: str) -> tuple[str, str]:
    match = _SOURCE_PREFIX.match(cursor)
    if match is None:
        raise InvalidCursor("the cursor does not name a source")
    return match.group(1), match.group(2)
