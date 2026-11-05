"""Small checks and conversions every chain's normalizers share.

Provider data is untrusted. Each value is checked before it becomes a record, and one that cannot be
what it claims raises MalformedRow instead of becoming a plausible-looking wrong record.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from app.chains.models import Transfer

MAX_TEXT = 64
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class MalformedRow(ValueError):
    """A provider row is missing something, or holds a value that cannot be what it claims."""


def to_int(value: Any) -> int:
    """An integer from a decimal string, a 0x hex string, or an int."""
    if isinstance(value, bool) or value is None:
        raise MalformedRow(f"expected a number, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 16) if text.lower().startswith("0x") else int(text, 10)
        except ValueError:
            pass
    raise MalformedRow(f"expected a number, got {str(value)[:40]!r}")


def optional_int(value: Any) -> int | None:
    return None if value in (None, "") else to_int(value)


def raw_amount(value: Any) -> str:
    """The integer string for an amount; never negative."""
    number = to_int(value)
    if number < 0:
        raise MalformedRow("negative amount")
    return str(number)


def clean_text(value: Any) -> str | None:
    """Provider-controlled text (token names) with control characters removed and length capped.

    Scam tokens put anything in their name; it must not reach logs or a UI unbounded.
    """
    if value is None:
        return None
    text = _CONTROL.sub("", str(value)).strip()[:MAX_TEXT]
    return text or None


def assign_occurrences(transfers: Iterable[Transfer]) -> list[Transfer]:
    """Tell apart identical movements of one transaction by their order in `transfers`."""
    seen: Counter[tuple[str, str]] = Counter()
    result = []
    for transfer in transfers:
        marker = (transfer.transaction_hash, transfer.event_key)
        count = seen[marker]
        seen[marker] += 1
        result.append(
            replace(transfer, event_key=f"{transfer.event_key}#{count}") if count else transfer
        )
    return result
