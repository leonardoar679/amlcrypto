"""Small checks and conversions every chain's normalizers share.

Provider data is untrusted. Each value is checked before it becomes a record, and one that cannot be
what it claims raises MalformedRow instead of becoming a plausible-looking wrong record.
"""

from __future__ import annotations

import functools
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any, TypeVar

from app.chains.models import Transfer

MAX_TEXT = 64
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


F = TypeVar("F", bound=Callable[..., Any])

# What a parser raises when it is handed a shape it did not expect: a number where an object was,
# a missing key, a list that is not a list. ValueError is MalformedRow's own parent, so a parser's
# own refusals pass through unchanged (see below).
_SHAPE_ERRORS = (
    AttributeError,
    TypeError,
    KeyError,
    IndexError,
    ValueError,
    ArithmeticError,
    RecursionError,
)


class MalformedRow(ValueError):
    """A provider row is missing something, or holds a value that cannot be what it claims."""


def refusing_malformed(parse: F) -> F:
    """Mark a parser's public entry point as one that returns what it parsed or raises MalformedRow,
    whatever it was given.

    A provider's answer is input from outside, and a parser is written for the shape the provider
    documents. When a row has another shape the parser fails somewhere inside it with whatever that
    happened to be (an AttributeError on a number that should have been an object); that must not
    cost the caller a whole history, because the layers above know what to do with a MalformedRow:
    skip the row and count it. So the entry point answers for everything: its own refusals pass,
    and anything that comes from the input being the wrong shape becomes one.
    """

    @functools.wraps(parse)
    def parser(*args: Any, **kwargs: Any) -> Any:
        try:
            return parse(*args, **kwargs)
        except MalformedRow:
            raise
        except _SHAPE_ERRORS as error:
            reason = f"{parse.__name__}: {type(error).__name__}: {error}"
            raise MalformedRow(reason[:200]) from error

    return parser  # type: ignore[return-value]


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
