"""What every page an editor writes has in common: the errors an editor is shown, and the checks
that keep text from looking like something it is not, a web address a real one, an address in the
form its chain defines, and a document a real citation.

The incidents (app.incidents) and the wallet profiles (app.wallets) are written the same way: an
editor sends the whole content of a page as one document, it is checked as a whole, and only a
checked document is kept. These are the parts of that check that do not depend on what the page is
about.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from urllib.parse import urlsplit

from app.chains.registry import get_chain
from app.chains.support import SUPPORTED, find_chain

SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
MAX_EVIDENCE = 50
MAX_EXCERPT = 500


class EvidenceKind(StrEnum):
    OFFICIAL = "official"  # the affected party or an authority
    ANALYTICS = "analytics"  # a blockchain analytics firm
    NEWS = "news"  # a news organization
    ON_CHAIN = "on_chain"  # a transaction anybody can look up
    COMMUNITY = "community"  # a community report, not checked


class EditorialError(ValueError):
    """The request is not acceptable (shown to the editor who made it)."""

    def __init__(self, message: str, *, problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.problems = problems or []


class EditorialConflict(EditorialError):
    """The request does not fit what exists: the page's state, or a slug that is taken."""


@dataclass(frozen=True, slots=True)
class EvidenceInput:
    ref: str  # the editor's name for the document within this page ("e1")
    publisher: str
    title: str
    url: str
    kind: str
    retrieved_on: date
    published_on: date | None = None
    excerpt: str | None = None


# --- checking text ------------------------------------------------------------------------------

SLUG = re.compile(SLUG_PATTERN)
REF = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
# Characters that make text look like something it is not: zero-width and direction-changing marks.
SPOOFING = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿]")


def clean(
    value: str | None, name: str, *, maximum: int, required: bool = False, lines: bool = False
):
    """The text, trimmed. Control characters and look-alike marks are refused, not dropped."""
    if value is None:
        if required:
            raise EditorialError(f"{name} is needed.")
        return None
    text = value.strip()
    if not text:
        if required:
            raise EditorialError(f"{name} is needed.")
        return None
    if len(text) > maximum:
        raise EditorialError(f"{name} is at most {maximum} characters.")
    allowed = "\n\t" if lines else ""
    for character in text:
        if unicodedata.category(character) == "Cc" and character not in allowed:
            raise EditorialError(f"{name} holds a control character.")
    if SPOOFING.search(text):
        raise EditorialError(f"{name} holds an invisible or direction-changing character.")
    return text


def web_address(value: str, name: str) -> str:
    text = clean(value, name, maximum=500, required=True)
    assert text is not None
    if any(character.isspace() for character in text):
        raise EditorialError(f"{name} cannot hold spaces.")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise EditorialError(f"{name} must be a web address (http or https).")
    if parts.username is not None or parts.password is not None:
        raise EditorialError(f"{name} cannot carry a user name or password.")
    return text


def choice(value: str, choices: type, name: str) -> str:
    try:
        return choices(value).value
    except ValueError:
        words = ", ".join(item.value for item in choices)
        raise EditorialError(f"{name} is one of: {words}.") from None


def latest_day(now: datetime) -> date:
    """The latest date a document may carry: today, and a day's slack for time zones."""
    return now.date() + timedelta(days=1)


def not_future(value: date | None, name: str, limit: date) -> date | None:
    if value is not None and value > limit:
        raise EditorialError(f"{name} is in the future.")
    return value


def aware(value: datetime | None, name: str) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise EditorialError(f"{name} must say its time zone.")
    return value


def address(chain_name: str, where: str, name: str) -> tuple[str, str]:
    """(the chain's id, the address in its storage form), refusing what is not readable here."""
    chain = find_chain(chain_name)
    if chain is None:
        raise EditorialError(
            f"{name}: {chain_name!r} is not a chain this platform reads ({', '.join(SUPPORTED)})."
        )
    check = get_chain(chain.chain_id).validator.check(where)
    if not check.valid or check.normalized is None:
        raise EditorialError(f"{name}: not a valid {chain.slug} address ({check.message}).")
    return chain.chain_id, check.normalized


def check_evidence(
    items: tuple[EvidenceInput, ...], limit: date, *, what: str = "An incident"
) -> tuple[EvidenceInput, ...]:
    """The documents a page rests on: each a real citation, each with a name of its own."""
    if len(items) > MAX_EVIDENCE:
        raise EditorialError(f"{what} has at most {MAX_EVIDENCE} documents.")
    seen: set[str] = set()
    checked = []
    for number, item in enumerate(items, 1):
        label = f"Document {number}"
        if not REF.match(item.ref):
            raise EditorialError(f"{label}: the reference is 1 to 24 letters, digits, - or _.")
        if item.ref in seen:
            raise EditorialError(f"{label}: the reference {item.ref!r} is used twice.")
        seen.add(item.ref)
        checked.append(
            EvidenceInput(
                ref=item.ref,
                publisher=clean(
                    item.publisher, f"{label}: the publisher", maximum=100, required=True
                ),
                title=clean(item.title, f"{label}: the title", maximum=255, required=True),
                url=web_address(item.url, f"{label}: the address"),
                kind=choice(item.kind, EvidenceKind, f"{label}: the kind"),
                retrieved_on=not_future(item.retrieved_on, f"{label}: the date it was read", limit),
                published_on=not_future(item.published_on, f"{label}: the publication date", limit),
                excerpt=clean(item.excerpt, f"{label}: the excerpt", maximum=MAX_EXCERPT),
            )
        )
    return tuple(checked)
