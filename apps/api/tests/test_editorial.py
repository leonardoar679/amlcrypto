"""What every page an editor writes has in common, tested where it lives.

The incidents and the wallet profiles are written the same way, and these are the checks they share:
text that cannot look like something it is not (and that may be as long as its limit and no longer),
a web address that is a real one, a date that is not in the future, an address in the form its chain
defines, and a document that is a real citation. The two services test what is specific to each; the
bounds of the shared parts are held here, once.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app import editorial
from app.editorial import EditorialConflict, EditorialError, EvidenceInput

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
LIMIT = date(2026, 11, 12)  # the latest day a document may carry, for NOW
EVM = "0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed"
EVM_CHECKSUMMED = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"  # EIP-55's own example


def doc(**changes) -> EvidenceInput:
    values = {
        "ref": "e1",
        "publisher": "An analytics firm",
        "title": "What was taken",
        "url": "https://example.org/report",
        "kind": "analytics",
        "retrieved_on": LIMIT - timedelta(days=1),
        "published_on": LIMIT - timedelta(days=2),
        "excerpt": None,
    }
    values.update(changes)
    return EvidenceInput(**values)


# --- text ---------------------------------------------------------------------------------------


def test_text_is_trimmed_and_may_be_missing_unless_it_is_needed():
    assert editorial.clean("  hello  ", "The name", maximum=10) == "hello"
    assert editorial.clean(None, "The note", maximum=10) is None
    assert editorial.clean("   ", "The note", maximum=10) is None
    for missing in (None, "", "   "):
        with pytest.raises(EditorialError, match="The name is needed"):
            editorial.clean(missing, "The name", maximum=10, required=True)


def test_text_has_a_limit_which_it_may_reach():
    assert editorial.clean("x" * 10, "The name", maximum=10) == "x" * 10
    assert editorial.clean("  " + "x" * 10 + "  ", "The name", maximum=10) == "x" * 10
    with pytest.raises(EditorialError, match="at most 10 characters"):
        editorial.clean("x" * 11, "The name", maximum=10)


def test_only_text_that_asks_for_lines_may_have_them():
    kept = "one\ntwo\tthree"
    assert editorial.clean(kept, "The summary", maximum=50, lines=True) == kept
    for text in ("one\ntwo", "one\tthree"):
        with pytest.raises(EditorialError, match="control character"):
            editorial.clean(text, "The name", maximum=50)
    for text in ("one\x07two", "one\x00two", "one\x1btwo"):
        with pytest.raises(EditorialError, match="control character"):
            editorial.clean(text, "The summary", maximum=50, lines=True)


def test_text_that_looks_like_something_it_is_not_is_refused():
    for mark in ("​", "‮", "⁦", "﻿", "⁠"):
        with pytest.raises(EditorialError, match="invisible or direction-changing"):
            editorial.clean(f"pay{mark}ment", "The title", maximum=50)


# --- web addresses ------------------------------------------------------------------------------


def test_a_web_address_is_a_real_one():
    assert editorial.web_address("https://example.org/a?b=c#d", "The link") == (
        "https://example.org/a?b=c#d"
    )
    assert editorial.web_address("  http://example.org  ", "The link") == "http://example.org"
    for bad, why in (
        ("ftp://example.org", "web address"),
        ("javascript:alert(1)", "web address"),
        ("example.org", "web address"),
        ("https://", "web address"),  # no host
        ("https:///only/a/path", "web address"),
        ("https://exa mple.org", "spaces"),
        ("https://user@example.org", "user name"),
        ("https://user:secret@example.org", "user name"),
        ("", "needed"),
    ):
        with pytest.raises(EditorialError, match=why):
            editorial.web_address(bad, "The link")


def test_a_web_address_may_be_as_long_as_the_platform_keeps():
    start = "https://example.org/"
    longest = start + "a" * (500 - len(start))
    assert editorial.web_address(longest, "The link") == longest
    with pytest.raises(EditorialError, match="at most 500"):
        editorial.web_address(longest + "a", "The link")


# --- dates and times ----------------------------------------------------------------------------


def test_a_date_may_be_today_and_a_day_more_for_time_zones_and_not_later():
    assert editorial.latest_day(NOW) == LIMIT
    assert editorial.not_future(None, "The day", LIMIT) is None
    assert editorial.not_future(date(2026, 1, 1), "The day", LIMIT) == date(2026, 1, 1)
    assert editorial.not_future(LIMIT, "The day", LIMIT) == LIMIT  # the limit itself is allowed
    with pytest.raises(EditorialError, match="in the future"):
        editorial.not_future(LIMIT + timedelta(days=1), "The day", LIMIT)


def test_a_time_must_say_its_zone():
    assert editorial.aware(None, "The time") is None
    assert editorial.aware(NOW, "The time") == NOW
    with pytest.raises(EditorialError, match="time zone"):
        editorial.aware(datetime(2026, 11, 11, 12, 0), "The time")


# --- addresses ----------------------------------------------------------------------------------


def test_an_address_is_stored_in_the_form_its_chain_defines():
    assert editorial.address("ethereum", EVM_CHECKSUMMED, "Address 1") == ("eip155:1", EVM)
    assert editorial.address("eip155:56", EVM, "Address 1")[0] == "eip155:56"  # a CAIP-2 id too
    with pytest.raises(EditorialError, match="not a chain this platform reads"):
        editorial.address("dogecoin", EVM, "Address 1")
    with pytest.raises(EditorialError, match="not a valid ethereum address"):
        editorial.address("ethereum", "0x1234", "Address 1")
    # mixed case whose checksum is wrong is not an address
    wrong = EVM_CHECKSUMMED[:-1] + EVM_CHECKSUMMED[-1].upper()
    assert wrong != EVM_CHECKSUMMED
    with pytest.raises(EditorialError, match="not a valid ethereum address"):
        editorial.address("ethereum", wrong, "Address 1")


# --- documents ----------------------------------------------------------------------------------


def test_each_part_of_a_document_has_its_limit_which_it_may_reach():
    at_the_limits = doc(ref="r" * 24, publisher="p" * 100, title="t" * 255, excerpt="e" * 500)
    (checked,) = editorial.check_evidence((at_the_limits,), LIMIT)
    assert (
        len(checked.ref),
        len(checked.publisher),
        len(checked.title),
        len(checked.excerpt),
    ) == (24, 100, 255, 500)
    for change, why in (
        ({"ref": "r" * 25}, "reference"),
        ({"publisher": "p" * 101}, "at most 100"),
        ({"title": "t" * 256}, "at most 255"),
        ({"excerpt": "e" * 501}, "at most 500"),
    ):
        with pytest.raises(EditorialError, match=why):
            editorial.check_evidence((doc(**change),), LIMIT)


def test_a_document_needs_a_publisher_a_title_a_kind_and_real_dates():
    for change, why in (
        ({"publisher": ""}, "publisher is needed"),
        ({"title": ""}, "title is needed"),
        ({"title": "   "}, "title is needed"),
        ({"kind": "rumour"}, "is one of"),
        ({"url": "ftp://example.org"}, "web address"),
        ({"retrieved_on": LIMIT + timedelta(days=1)}, "in the future"),
        ({"published_on": LIMIT + timedelta(days=1)}, "in the future"),
    ):
        with pytest.raises(EditorialError, match=why):
            editorial.check_evidence((doc(**change),), LIMIT)
    (checked,) = editorial.check_evidence((doc(published_on=None),), LIMIT)
    assert checked.published_on is None  # the day a document was published is optional


def test_documents_are_limited_in_number_and_each_has_a_name_of_its_own():
    fifty = tuple(doc(ref=f"e{number}") for number in range(editorial.MAX_EVIDENCE))
    assert len(editorial.check_evidence(fifty, LIMIT)) == 50
    with pytest.raises(EditorialError, match="at most 50 documents"):
        editorial.check_evidence((*fifty, doc(ref="one-more")), LIMIT)
    with pytest.raises(EditorialError, match="A profile has at most 50"):
        editorial.check_evidence((*fifty, doc(ref="one-more")), LIMIT, what="A profile")
    with pytest.raises(EditorialError, match="used twice"):
        editorial.check_evidence((doc(ref="e1"), doc(ref="e1")), LIMIT)
    with pytest.raises(EditorialError, match="reference"):
        editorial.check_evidence((doc(ref="has space"),), LIMIT)


# --- the errors ---------------------------------------------------------------------------------


def test_a_conflict_is_an_error_that_also_says_the_state_is_wrong():
    assert issubclass(EditorialConflict, EditorialError)
    assert EditorialError("x").problems == []
    assert EditorialError("x", problems=["a", "b"]).problems == ["a", "b"]
