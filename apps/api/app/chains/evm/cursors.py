"""Cursors: where to carry on reading a long history.

A cursor is an opaque string to everyone but the source that issued it. Etherscan's is
"<block>:<skip>": start again at that block and ignore its first <skip> rows (they were already
delivered). Alchemy's is "<direction>:<pageKey>". The adapter prefixes the name of the source, so a
cursor is never handed to a source that did not issue it.
"""

from __future__ import annotations

import re

_BLOCK_CURSOR = re.compile(r"^(\d{1,12}):(\d{1,9})$")
_SOURCE_PREFIX = re.compile(r"^([a-z0-9_]{1,20})\|(.*)$", re.DOTALL)


class InvalidCursor(ValueError):
    """The cursor was not issued by this source (or was altered)."""


def encode_block_cursor(block: int, skip: int) -> str:
    return f"{block}:{skip}"


def decode_block_cursor(cursor: str) -> tuple[int, int]:
    match = _BLOCK_CURSOR.match(cursor)
    if match is None:
        raise InvalidCursor("not a block cursor")
    return int(match.group(1)), int(match.group(2))


def with_source(source: str, cursor: str | None) -> str | None:
    return None if cursor is None else f"{source}|{cursor}"


def split_source(cursor: str) -> tuple[str, str]:
    match = _SOURCE_PREFIX.match(cursor)
    if match is None:
        raise InvalidCursor("the cursor does not name a source")
    return match.group(1), match.group(2)
