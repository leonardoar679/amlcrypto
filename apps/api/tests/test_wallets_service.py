"""Writing wallet profiles: a profile is checked as a whole, and only a checked profile is kept.

Every rule here is a promise the public pages rely on: nothing is published without something to
rest on, every address cites a document, the address of a public figure is published only when the
owner or an authority has confirmed it, an address is stored in the form the chain defines, and what
an editor types cannot make text look like something it is not.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.database.models import WalletProfile, WalletProfileAddress, WalletProfileEvidence
from app.editorial import EditorialConflict, EditorialError, EvidenceInput
from app.wallets import service
from app.wallets.service import AddressInput, Content
from tests.access_helpers import make_user

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
TODAY = date(2026, 11, 11)
EVM_CHECKSUMMED = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"  # EIP-55's own example
EVM = EVM_CHECKSUMMED.lower()
OTHER = "0x" + "cd" * 20
TRON = "TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy"


def doc(ref: str = "e1", **changes) -> EvidenceInput:
    values = {
        "ref": ref,
        "publisher": "The exchange itself",
        "title": "Our wallet addresses",
        "url": "https://example.org/proof-of-reserves",
        "kind": "official",
        "retrieved_on": TODAY,
        "published_on": TODAY - timedelta(days=1),
        "excerpt": None,
    }
    values.update(changes)
    return EvidenceInput(**values)


def address(**changes) -> AddressInput:
    values = {
        "chain": "ethereum",
        "address": EVM,
        "label": "Hot wallet",
        "attribution_status": "OFFICIALLY_CONFIRMED",
        "evidence_ref": "e1",
    }
    values.update(changes)
    return AddressInput(**values)


def profile(**changes) -> Content:
    values = {
        "name": "Example Exchange",
        "kind": "exchange",
        "summary": "A centralized exchange.",
        "website": "https://example.org",
        "evidence": (doc(),),
        "addresses": (address(),),
    }
    values.update(changes)
    return Content(**values)


@pytest.fixture
def editor(db_session):
    return make_user(db_session, "editor@example.com", "admin").id


def make(session, editor_id, slug="example-exchange", content=None) -> WalletProfile:
    return service.create_profile(
        session, user_id=editor_id, slug=slug, content=content or profile(), now=NOW
    )


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def publish(session, editor_id, content=None, slug="example-exchange") -> WalletProfile:
    made = make(session, editor_id, slug=slug, content=content)
    service.transition(session, made, "in_review", user_id=editor_id, now=NOW)
    service.transition(session, made, "published", user_id=editor_id, now=NOW)
    return made


# --- making and keeping a profile ---------------------------------------------------------------


def test_a_profile_is_kept_as_a_draft_with_everything_it_rests_on(db_session, editor):
    made = make(db_session, editor)

    assert made.status == "draft" and made.published_at is None
    assert made.created_by == editor and made.updated_by == editor
    assert (made.name, made.kind, made.website) == (
        "Example Exchange",
        "exchange",
        "https://example.org",
    )
    assert count(db_session, WalletProfileEvidence) == 1
    (row,) = db_session.scalars(select(WalletProfileAddress)).all()
    assert (row.chain_id, row.address, row.label) == ("eip155:1", EVM, "Hot wallet")
    assert row.confidence == 95  # the status gives the confidence
    assert row.evidence_id == db_session.scalar(select(WalletProfileEvidence.id))


def test_an_address_is_kept_in_the_form_its_chain_defines(db_session, editor):
    content = profile(
        addresses=(
            address(address=EVM_CHECKSUMMED),
            address(chain="tron", address=TRON, label="Deposit wallet"),
        )
    )
    make(db_session, editor, content=content)

    stored = {
        (row.chain_id, row.address) for row in db_session.scalars(select(WalletProfileAddress))
    }
    assert ("eip155:1", EVM) in stored  # an EVM address is stored in lower case
    assert ("tron:728126428", TRON) in stored


def test_the_same_address_twice_is_refused_and_on_two_chains_it_is_two(db_session, editor):
    twice = profile(addresses=(address(address=EVM), address(address=EVM_CHECKSUMMED)))
    with pytest.raises(EditorialError, match="twice"):
        make(db_session, editor, content=twice)
    assert count(db_session, WalletProfile) == 0

    both = profile(addresses=(address(), address(chain="bsc")))
    make(db_session, editor, content=both)
    assert count(db_session, WalletProfileAddress) == 2


def test_the_website_is_optional_and_an_empty_one_is_none(db_session, editor):
    made = make(db_session, editor, content=profile(website="   "))
    assert made.website is None
    made = make(db_session, editor, slug="another-one", content=profile(website=None))
    assert made.website is None


@pytest.mark.parametrize(
    "slug",
    ["", "ab", "A-Wallet", "a wallet", "a--wallet", "-a-wallet", "a-wallet-", "a_wallet", "x" * 81],
)
def test_the_address_of_the_page_has_one_shape(db_session, editor, slug):
    with pytest.raises(EditorialError, match="address of the page"):
        make(db_session, editor, slug=slug)


def test_two_profiles_cannot_share_an_address_of_the_page(db_session, editor):
    make(db_session, editor)
    with pytest.raises(EditorialConflict):
        make(db_session, editor)
    assert count(db_session, WalletProfile) == 1


# --- nothing half-checked is kept ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"name": "  "}, "name"),
        ({"name": "x" * 121}, "name"),
        ({"summary": ""}, "summary"),
        ({"kind": "person"}, "kind"),
        ({"website": "ftp://example.org"}, "web address"),
        ({"website": "javascript:alert(1)"}, "web address"),
        ({"website": "https://user:pass@example.org"}, "user name or password"),
        ({"website": "https://example.org/a b"}, "spaces"),
        ({"attribution_note": "x" * 2001}, "attribution note"),
        ({"name": "Exam‮ple"}, "direction-changing"),
        ({"name": "Exam\x07ple"}, "control character"),
    ],
)
def test_a_profile_with_a_bad_part_is_refused_whole(db_session, editor, change, message):
    with pytest.raises(EditorialError, match=message):
        make(db_session, editor, content=profile(**change))
    assert count(db_session, WalletProfile) == 0 and count(db_session, WalletProfileEvidence) == 0


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"url": "ftp://example.org/x"}, "web address"),
        ({"publisher": ""}, "publisher"),
        ({"kind": "gossip"}, "kind"),
        ({"ref": "bad ref"}, "reference"),
        ({"retrieved_on": TODAY + timedelta(days=30)}, "future"),
    ],
)
def test_a_document_must_be_a_real_citation(db_session, editor, change, message):
    with pytest.raises(EditorialError, match=message):
        make(db_session, editor, content=profile(evidence=(doc(**change),)))


def test_the_words_of_a_refusal_name_a_profile_not_an_incident(db_session, editor):
    too_many = tuple(doc(f"e{n}") for n in range(51))
    with pytest.raises(EditorialError, match="A profile has at most 50 documents"):
        make(db_session, editor, content=profile(evidence=too_many))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"chain": "dogecoin"}, "not a chain this platform reads"),
        ({"address": "0x123"}, "not a valid ethereum address"),
        ({"label": ""}, "what it is"),
        ({"label": "x" * 61}, "at most 60"),
        ({"attribution_status": "KNOWN_FOR_SURE"}, "status"),
        ({"evidence_ref": "e9"}, "not in this profile"),
        ({"confidence": 101}, "percentage"),
        ({"confidence": -1}, "percentage"),
        ({"note": "x" * 501}, "at most 500"),
        ({"last_verified_at": datetime(2026, 1, 1)}, "time zone"),
    ],
)
def test_an_address_is_checked_like_the_rest(db_session, editor, change, message):
    with pytest.raises(EditorialError, match=message):
        make(db_session, editor, content=profile(addresses=(address(**change),)))
    assert count(db_session, WalletProfile) == 0


def test_there_is_a_limit_on_how_many_addresses_one_profile_names(db_session, editor):
    many = tuple(address(address="0x" + f"{n:040x}") for n in range(301))
    with pytest.raises(EditorialError, match="at most 300 addresses"):
        make(db_session, editor, content=profile(addresses=many))
    make(db_session, editor, content=profile(addresses=many[:300]))
    assert count(db_session, WalletProfileAddress) == 300


# --- replacing the content ----------------------------------------------------------------------


def test_replacing_the_content_replaces_everything_it_holds(db_session, editor):
    made = make(db_session, editor)
    new = profile(
        name="Example Exchange, renamed",
        evidence=(doc("a"), doc("b", url="https://example.org/two")),
        addresses=(address(address=OTHER, evidence_ref="b", label="Cold wallet"),),
    )

    service.replace_content(db_session, made, new, user_id=editor, now=NOW + timedelta(hours=1))

    assert made.name == "Example Exchange, renamed"
    assert made.updated_at == NOW + timedelta(hours=1)
    assert sorted(db_session.scalars(select(WalletProfileEvidence.ref))) == ["a", "b"]
    assert db_session.scalar(select(WalletProfileAddress.address)) == OTHER


def test_a_refused_replacement_leaves_the_profile_as_it_was(db_session, editor):
    made = make(db_session, editor)
    with pytest.raises(EditorialError):
        service.replace_content(
            db_session,
            made,
            profile(name="New", addresses=(address(chain="nope"),)),
            user_id=editor,
        )

    assert made.name == "Example Exchange"
    assert count(db_session, WalletProfileAddress) == 1
    assert count(db_session, WalletProfileEvidence) == 1


def test_a_published_profile_is_changed_by_taking_it_back_to_review(db_session, editor):
    made = publish(db_session, editor)

    with pytest.raises(EditorialConflict, match="back to review"):
        service.replace_content(db_session, made, profile(name="Changed"), user_id=editor)
    assert made.name == "Example Exchange"

    service.transition(db_session, made, "in_review", user_id=editor, now=NOW)
    service.replace_content(db_session, made, profile(name="Changed"), user_id=editor)
    assert made.name == "Changed"


# --- states -------------------------------------------------------------------------------------


def test_the_states_a_profile_moves_through(db_session, editor):
    made = make(db_session, editor)
    for state in ("in_review", "published", "archived", "in_review", "draft"):
        service.transition(db_session, made, state, user_id=editor, now=NOW)
        assert made.status == state
    assert made.published_at == NOW  # the first time it was published, kept


@pytest.mark.parametrize(
    ("start", "target"),
    [
        ("draft", "published"),
        ("draft", "archived"),
        ("in_review", "archived"),
        ("archived", "draft"),
    ],
)
def test_a_state_cannot_be_skipped(db_session, editor, start, target):
    made = make(db_session, editor)
    made.status = start
    with pytest.raises(EditorialConflict, match="can only go to"):
        service.transition(db_session, made, target, user_id=editor)
    assert made.status == start


def test_an_unknown_state_is_refused(db_session, editor):
    with pytest.raises(EditorialError, match="state is"):
        service.transition(db_session, make(db_session, editor), "live", user_id=editor)


# --- what may be published ----------------------------------------------------------------------


def test_nothing_is_published_without_something_to_rest_on(db_session, editor):
    bare = profile(evidence=(), addresses=())
    made = make(db_session, editor, content=bare)
    service.transition(db_session, made, "in_review", user_id=editor, now=NOW)

    with pytest.raises(EditorialError) as caught:
        service.transition(db_session, made, "published", user_id=editor, now=NOW)

    assert made.status == "in_review" and made.published_at is None
    assert caught.value.problems == ["It rests on no document.", "It names no address."]


def test_every_address_must_cite_a_document_to_be_published(db_session, editor):
    content = profile(
        addresses=(address(), address(address=OTHER, evidence_ref=None, label="Cold wallet"))
    )
    made = make(db_session, editor, content=content)
    service.transition(db_session, made, "in_review", user_id=editor, now=NOW)

    with pytest.raises(EditorialError) as caught:
        service.transition(db_session, made, "published", user_id=editor, now=NOW)

    assert caught.value.problems == ["1 address(es) cite no document."]


def test_the_wallet_of_a_public_figure_is_published_only_when_the_owner_has_confirmed_it(
    db_session, editor
):
    owner = dict(kind="public_figure", name="A Public Figure", website=None)
    # the owner's own statement, in an official document: published
    ok = publish(db_session, editor, profile(**owner), slug="a-public-figure")
    assert ok.status == "published"

    # a report by an analyst, or a statement that is not official, is not enough
    for weak_address, weak_doc in (
        (address(attribution_status="REPORTED_BY_ANALYST"), doc()),
        (address(attribution_status="CORROBORATED"), doc()),
        (address(), doc(kind="analytics")),
        (address(), doc(kind="news")),
    ):
        status = weak_address.attribution_status.lower().replace("_", "-")
        made = make(
            db_session,
            editor,
            slug=f"weak-{status}-{weak_doc.kind}",
            content=profile(**owner, addresses=(weak_address,), evidence=(weak_doc,)),
        )
        service.transition(db_session, made, "in_review", user_id=editor, now=NOW)
        with pytest.raises(EditorialError) as caught:
            service.transition(db_session, made, "published", user_id=editor, now=NOW)
        assert len(caught.value.problems) == 1 and "public figure" in caught.value.problems[0]
        assert made.status == "in_review"


def test_the_rule_for_a_public_figure_is_not_asked_of_anybody_else(db_session, editor):
    # an exchange's address may rest on an analyst's report, as an incident's may
    content = profile(
        addresses=(address(attribution_status="REPORTED_BY_ANALYST"),),
        evidence=(doc(kind="analytics"),),
    )
    assert publish(db_session, editor, content).status == "published"


# --- deleting -----------------------------------------------------------------------------------


def test_a_draft_that_was_never_shown_can_be_deleted_with_everything_it_holds(db_session, editor):
    made = make(db_session, editor)
    service.delete_profile(db_session, made)
    for model in (WalletProfile, WalletProfileEvidence, WalletProfileAddress):
        assert count(db_session, model) == 0


def test_what_was_ever_published_is_archived_never_deleted(db_session, editor):
    made = publish(db_session, editor)
    for state in ("in_review", "draft"):
        service.transition(db_session, made, state, user_id=editor, now=NOW)
    with pytest.raises(EditorialConflict, match="never published"):
        service.delete_profile(db_session, made)
    assert count(db_session, WalletProfile) == 1


# --- what the first mutation run found missing --------------------------------------------------


def test_the_summary_and_the_notes_have_limits_and_may_run_over_several_lines(db_session, editor):
    from app.wallets.model import MAX_NOTE, MAX_SUMMARY

    full = profile(
        summary="s" * MAX_SUMMARY, internal_note="n" * MAX_NOTE, attribution_note="a" * MAX_NOTE
    )
    make(db_session, editor, slug="at-the-limits", content=full)

    for change in (
        {"summary": "s" * (MAX_SUMMARY + 1)},
        {"internal_note": "n" * (MAX_NOTE + 1)},
        {"attribution_note": "a" * (MAX_NOTE + 1)},
    ):
        with pytest.raises(EditorialError, match="at most"):
            make(db_session, editor, slug="over-the-limits", content=profile(**change))
    assert count(db_session, WalletProfile) == 1

    # a summary and a note may be written in paragraphs; a name is one line
    paragraphs = profile(
        summary="First paragraph.\n\nSecond paragraph.",
        internal_note="one\ntwo",
        attribution_note="first\nsecond",
    )
    made = make(db_session, editor, slug="in-paragraphs", content=paragraphs)
    assert made.summary == "First paragraph.\n\nSecond paragraph."
    assert made.internal_note == "one\ntwo"
    assert made.attribution_note == "first\nsecond"
    with pytest.raises(EditorialError, match="control character"):
        make(db_session, editor, slug="in-two-lines", content=profile(name="Example\nExchange"))


def test_a_profile_whose_summary_has_gone_is_not_published(db_session, editor):
    made = make(db_session, editor)
    made.summary = ""  # not reachable through the service, which asks for one: a row edited by hand
    db_session.flush()
    assert "It has no summary." in service.publish_problems(db_session, made)

    made.summary = "   "
    assert "It has no summary." in service.publish_problems(db_session, made)

    made.summary = "Something."
    assert "It has no summary." not in service.publish_problems(db_session, made)


def test_the_day_a_profile_was_first_published_is_kept_when_it_is_published_again(
    db_session, editor
):
    first = NOW
    later = NOW + timedelta(days=3)
    made = make(db_session, editor)
    service.transition(db_session, made, "in_review", user_id=editor, now=first)
    service.transition(db_session, made, "published", user_id=editor, now=first)
    stamped = made.published_at
    assert stamped is not None

    service.transition(db_session, made, "in_review", user_id=editor, now=later)
    service.transition(db_session, made, "published", user_id=editor, now=later)

    assert made.published_at == stamped


def test_a_move_records_who_made_it(db_session, editor):
    other = make_user(db_session, "second-editor@example.com", "admin").id
    made = make(db_session, editor)
    assert made.updated_by == editor

    service.transition(db_session, made, "in_review", user_id=other, now=NOW)

    assert made.updated_by == other
    assert made.created_by == editor


def test_a_profile_in_review_is_not_deleted_even_if_it_was_never_published(db_session, editor):
    made = make(db_session, editor)
    service.transition(db_session, made, "in_review", user_id=editor, now=NOW)

    with pytest.raises(EditorialConflict, match="draft that was never published"):
        service.delete_profile(db_session, made)
    assert count(db_session, WalletProfile) == 1

    service.transition(db_session, made, "draft", user_id=editor, now=NOW)
    service.delete_profile(db_session, made)
    assert count(db_session, WalletProfile) == 0
