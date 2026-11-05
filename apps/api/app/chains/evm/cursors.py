"""Cursors of the EVM sources.

Etherscan's is "<block>:<skip>": start again at that block and ignore its first <skip> rows (they
were already delivered). Alchemy's is "<direction>:<pageKey>" (see app.providers.alchemy). The
source-name prefix is handled in app.chains.cursors, re-exported here for the EVM modules.
"""

from __future__ import annotations

import re

from app.chains.cursors import InvalidCursor, split_source, with_source

_BLOCK_CURSOR = re.compile(r"^(\d{1,12}):(\d{1,9})$")

__all__ = [
    "InvalidCursor",
    "decode_block_cursor",
    "encode_block_cursor",
    "split_source",
    "with_source",
]


def encode_block_cursor(block: int, skip: int) -> str:
    return f"{block}:{skip}"


def decode_block_cursor(cursor: str) -> tuple[int, int]:
    match = _BLOCK_CURSOR.match(cursor)
    if match is None:
        raise InvalidCursor("not a block cursor")
    return int(match.group(1)), int(match.group(2))
