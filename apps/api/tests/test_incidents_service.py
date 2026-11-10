"""Writing incidents: a story is checked as a whole, and only a checked story is kept.

Every rule here is a promise the public pages rely on: nothing is published without something to
rest on, a published incident only grows, an address is stored in the form the chain defines, and
what an editor types cannot make text look like something it is not.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.database.models import (
    Incident,
    IncidentAddress,
    IncidentAmount,
    IncidentEvidence,
    IncidentUpdate,
)
from app.incidents import service
from app.incidents.service import (
    AddressInput,
    AmountInput,
    Content,
    EvidenceInput,
    IncidentConflict,
    IncidentError,
    UpdateInput,
)
from tests.access_helpers import make_user

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
TODAY = date(2026, 11, 10)
EVM_CHECKSUMMED = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"  # EIP-55's own example
EVM = EVM_CHECKSUMMED.lower()
TRON = "TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy"


def doc(ref: str = "e1", **changes) -> EvidenceInput:
    values = {
        "ref": ref,
        "publisher": "An Analytics Firm",
        "title": "What was taken",
        "url": "https://example.org/reports/one",
        "kind": "analytics",
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
        "role": "ATTACKER_FUNDS_RECEIVER",
        "attribution_status": "REPORTED_BY_ANALYST",
        "evidence_ref": "e1",
    }
    values.update(changes)
    return AddressInput(**values)


def story(**changes) -> Content:
    values = {
        "title": "A theft",
        "summary": "Funds were taken from a wallet.",
        "kind": "theft",
        "occurred_on": TODAY - timedelta(days=3),
        "evidence": (doc(),),
        "amounts": (AmountInput("e1", 5_000_000, 6_000_000, TODAY, "An estimate"),),
        "addresses": (address(),),
        "updates": (
            UpdateInput(NOW - timedelta(days=2), "event", "It was noticed.", evidence_ref="e1"),
        ),
    }
    values.update(changes)
    return Content(**values)


@pytest.fixture
def editor(db_session):
    user = make_user(db_session, "editor@example.com", "admin")
    return user.id


def make(session, editor_id, slug="a-theft", content=None) -> Incident:
    return service.create_incident(
        session, user_id=editor_id, slug=slug, content=content or story(), now=NOW
    )


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


# --- making and keeping a story -----------------------------------------------------------------


def test_a_story_is_kept_as_a_draft_with_everything_it_rests_on(db_session, editor):
    incident = make(db_session, editor)

    assert incident.status == "draft" and incident.published_at is None
    assert incident.created_by == editor and incident.updated_by == editor
    assert count(db_session, IncidentEvidence) == 1
    assert count(db_session, IncidentAmount) == 1
    assert count(db_session, IncidentUpdate) == 1
    (row,) = db_session.scalars(select(IncidentAddress)).all()
    assert row.chain_id == "eip155:1" and row.confidence == 80  # the status gives the confidence
    (amount,) = db_session.scalars(select(IncidentAmount)).all()
    assert (amount.amount_usd_low, amount.amount_usd_high) == (5_000_000, 6_000_000)


def test_an_address_is_kept_in_the_form_its_chain_defines(db_session, editor):
    content = story(
        addresses=(
            address(address=EVM_CHECKSUMMED),
            address(chain="tron", address=TRON, role="INTERMEDIARY"),
        )
    )
    make(db_session, editor, content=content)

    stored = {(row.chain_id, row.address) for row in db_session.scalars(select(IncidentAddress))}
    assert ("eip155:1", EVM) in stored  # an EVM address is stored in lower case
    assert ("tron:728126428", TRON) in stored


def test_the_same_address_in_the_same_role_twice_is_refused(db_session, editor):
    content = story(addresses=(address(address=EVM), address(address=EVM_CHECKSUMMED)))
    with pytest.raises(IncidentError, match="twice"):
        make(db_session, editor, content=content)
    assert count(db_session, Incident) == 0


def test_the_same_address_in_two_roles_is_allowed(db_session, editor):
    content = story(addresses=(address(), address(role="INTERMEDIARY")))
    make(db_session, editor, content=content)
    assert count(db_session, IncidentAddress) == 2


@pytest.mark.parametrize(
    "slug",
    ["", "ab", "A-Theft", "a theft", "a--theft", "-a-theft", "a-theft-", "a_theft", "x" * 81],
)
def test_the_address_of_the_page_has_one_shape(db_session, editor, slug):
    with pytest.raises(IncidentError, match="address of the page"):
        make(db_session, editor, slug=slug)


def test_two_incidents_cannot_share_an_address_of_the_page(db_session, editor):
    make(db_session, editor)
    with pytest.raises(IncidentConflict):
        make(db_session, editor)
    assert count(db_session, Incident) == 1


# --- nothing half-checked is kept ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"title": "  "}, "title"),
        ({"title": "x" * 201}, "title"),
        ({"summary": ""}, "summary"),
        ({"kind": "prank"}, "kind"),
        ({"occurred_on": TODAY + timedelta(days=30)}, "future"),
        ({"attribution_note": "x" * 2001}, "attribution note"),
        ({"title": "A‮theft"}, "direction-changing"),
        ({"title": "A\x07theft"}, "control character"),
    ],
)
def test_a_story_with_a_bad_part_is_refused_whole(db_session, editor, change, message):
    with pytest.raises(IncidentError, match=message):
        make(db_session, editor, content=story(**change))
    assert count(db_session, Incident) == 0
    assert count(db_session, IncidentEvidence) == 0


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"url": "ftp://example.org/x"}, "web address"),
        ({"url": "javascript:alert(1)"}, "web address"),
        ({"url": "https://user:pass@example.org/x"}, "user name or password"),
        ({"url": "https://example.org/a b"}, "spaces"),
        ({"url": "https://" + "a" * 500 + ".org"}, "at most"),
        ({"publisher": ""}, "publisher"),
        ({"kind": "gossip"}, "kind"),
        ({"ref": "bad ref"}, "reference"),
        ({"excerpt": "x" * 501}, "excerpt"),
        ({"retrieved_on": TODAY + timedelta(days=30)}, "future"),
    ],
)
def test_a_document_must_be_a_real_citation(db_session, editor, change, message):
    with pytest.raises(IncidentError, match=message):
        make(db_session, editor, content=story(evidence=(doc(**change),)))


def test_a_reference_is_used_once(db_session, editor):
    with pytest.raises(IncidentError, match="twice"):
        make(db_session, editor, content=story(evidence=(doc("e1"), doc("e1"))))


@pytest.mark.parametrize(
    ("amounts", "message"),
    [
        ((AmountInput("e2", 100),), "names the document"),
        ((AmountInput("e1", -1),), "between 0"),
        ((AmountInput("e1", 10**14),), "between 0"),
        ((AmountInput("e1", 200, 100),), "below"),
    ],
)
def test_an_amount_names_its_source_and_is_a_sane_number_of_dollars(
    db_session, editor, amounts, message
):
    with pytest.raises(IncidentError, match=message):
        make(db_session, editor, content=story(amounts=amounts))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"chain": "dogecoin"}, "not a chain"),
        ({"address": "not an address"}, "not a valid"),
        ({"address": EVM[:-1]}, "not a valid"),
        ({"role": "VILLAIN"}, "role"),
        ({"attribution_status": "KNOWN"}, "status"),
        ({"evidence_ref": "e9"}, "not in this incident"),
        ({"confidence": 101}, "percentage"),
        ({"note": "x" * 501}, "note"),
        ({"last_verified_at": datetime(2026, 1, 1)}, "time zone"),
    ],
)
def test_an_address_must_exist_on_a_chain_this_platform_reads(db_session, editor, change, message):
    with pytest.raises(IncidentError, match=message):
        make(db_session, editor, content=story(addresses=(address(**change),)))


@pytest.mark.parametrize(
    ("update", "message"),
    [
        (UpdateInput(datetime(2026, 1, 1), "event", "x"), "time zone"),
        (UpdateInput(NOW, "gossip", "x"), "kind"),
        (UpdateInput(NOW, "event", " "), "text"),
        (UpdateInput(NOW, "event", "x" * 2001), "at most"),
        (UpdateInput(NOW, "event", "x", evidence_ref="e9"), "not in this incident"),
        (UpdateInput(NOW, "event", "x", tx_hash="0x" + "a" * 64), "needs its chain"),
        (
            UpdateInput(NOW, "event", "x", chain="ethereum", tx_hash="not a hash"),
            "transaction hash",
        ),
        (UpdateInput(NOW, "event", "x", chain="dogecoin"), "not a chain"),
    ],
)
def test_a_timeline_entry_is_checked_like_the_rest(db_session, editor, update, message):
    with pytest.raises(IncidentError, match=message):
        make(db_session, editor, content=story(updates=(update,)))


def test_a_timeline_entry_may_name_a_transaction_and_a_document(db_session, editor):
    entry = UpdateInput(
        NOW,
        "funds_moved",
        "Funds moved.",
        evidence_ref="e1",
        chain="ethereum",
        tx_hash="0x" + "a" * 64,
    )
    make(db_session, editor, content=story(updates=(entry,)))

    (row,) = db_session.scalars(select(IncidentUpdate)).all()
    assert (
        row.chain_id == "eip155:1"
        and row.tx_hash == "0x" + "a" * 64
        and row.evidence_id is not None
    )


def test_there_are_limits_on_how_much_one_incident_holds(db_session, editor):
    too_many = tuple(address(address="0x" + f"{n:040x}") for n in range(201))
    with pytest.raises(IncidentError, match="at most 200 addresses"):
        make(db_session, editor, content=story(addresses=too_many))
    with pytest.raises(IncidentError, match="at most 50 documents"):
        make(db_session, editor, content=story(evidence=tuple(doc(f"e{n}") for n in range(51))))


# --- replacing the content ----------------------------------------------------------------------


def test_replacing_the_content_replaces_everything_it_holds(db_session, editor):
    incident = make(db_session, editor)
    new = story(
        title="A theft, corrected",
        evidence=(doc("a"), doc("b", url="https://example.org/reports/two")),
        amounts=(AmountInput("b", 1_000),),
        addresses=(address(evidence_ref="a", address="0x" + "cd" * 20),),
        updates=(),
    )

    service.replace_content(db_session, incident, new, user_id=editor, now=NOW + timedelta(hours=1))

    assert incident.title == "A theft, corrected" and incident.updated_at == NOW + timedelta(
        hours=1
    )
    assert sorted(db_session.scalars(select(IncidentEvidence.ref))) == ["a", "b"]
    assert db_session.scalar(select(IncidentAmount.amount_usd_low)) == 1_000
    assert db_session.scalar(select(IncidentAddress.address)) == "0x" + "cd" * 20
    assert count(db_session, IncidentUpdate) == 0


def test_a_refused_replacement_leaves_the_story_as_it_was(db_session, editor):
    incident = make(db_session, editor)
    with pytest.raises(IncidentError):
        service.replace_content(
            db_session,
            incident,
            story(title="New", addresses=(address(chain="nope"),)),
            user_id=editor,
        )

    assert incident.title == "A theft"
    assert count(db_session, IncidentAddress) == 1 and count(db_session, IncidentEvidence) == 1


def test_a_published_story_cannot_be_replaced_only_added_to(db_session, editor):
    incident = make(db_session, editor)
    service.transition(db_session, incident, "in_review", user_id=editor)
    service.transition(db_session, incident, "published", user_id=editor)

    with pytest.raises(IncidentConflict, match="only grows"):
        service.replace_content(db_session, incident, story(title="Changed"), user_id=editor)

    service.append_update(
        db_session,
        incident,
        UpdateInput(
            NOW, "correction", "The first report named the wrong chain.", evidence_ref="e1"
        ),
        user_id=editor,
        now=NOW + timedelta(days=1),
    )
    assert count(db_session, IncidentUpdate) == 2
    assert incident.title == "A theft" and incident.updated_at == NOW + timedelta(days=1)


def test_an_update_to_a_draft_may_name_only_the_documents_it_has(db_session, editor):
    incident = make(db_session, editor)
    with pytest.raises(IncidentError, match="not in this incident"):
        service.append_update(
            db_session, incident, UpdateInput(NOW, "event", "x", evidence_ref="e9"), user_id=editor
        )


# --- publishing ---------------------------------------------------------------------------------


def test_the_states_an_incident_moves_through(db_session, editor):
    incident = make(db_session, editor)
    service.transition(db_session, incident, "in_review", user_id=editor, now=NOW)
    service.transition(
        db_session, incident, "published", user_id=editor, now=NOW + timedelta(hours=1)
    )
    assert incident.status == "published" and incident.published_at == NOW + timedelta(hours=1)

    service.transition(
        db_session, incident, "in_review", user_id=editor, now=NOW + timedelta(hours=2)
    )
    service.transition(
        db_session, incident, "published", user_id=editor, now=NOW + timedelta(hours=3)
    )
    assert incident.published_at == NOW + timedelta(hours=1)  # the first publication stays

    service.transition(db_session, incident, "archived", user_id=editor)
    service.transition(db_session, incident, "in_review", user_id=editor)
    assert incident.status == "in_review"


@pytest.mark.parametrize(
    ("start", "target"),
    [
        ("draft", "published"),
        ("draft", "archived"),
        ("in_review", "archived"),
        ("published", "draft"),
        ("archived", "published"),
        ("draft", "draft"),
    ],
)
def test_a_state_cannot_be_skipped(db_session, editor, start, target):
    incident = make(db_session, editor)
    incident.status = start
    with pytest.raises(IncidentConflict, match="can only go to"):
        service.transition(db_session, incident, target, user_id=editor)
    assert incident.status == start


def test_an_unknown_state_is_refused(db_session, editor):
    with pytest.raises(IncidentError, match="state is"):
        service.transition(db_session, make(db_session, editor), "live", user_id=editor)


def test_nothing_is_published_without_something_to_rest_on(db_session, editor):
    bare = story(evidence=(), amounts=(), addresses=(), updates=(), occurred_on=None)
    incident = make(db_session, editor, content=bare)
    service.transition(db_session, incident, "in_review", user_id=editor)

    with pytest.raises(IncidentError) as caught:
        service.transition(db_session, incident, "published", user_id=editor)

    assert incident.status == "in_review" and incident.published_at is None
    assert caught.value.problems == ["It has no date.", "It rests on no document."]


def test_an_attribution_that_a_source_stands_behind_names_that_source(db_session, editor):
    content = story(
        addresses=(
            address(evidence_ref=None),
            address(
                address="0x" + "cd" * 20, attribution_status="COMMUNITY_REPORTED", evidence_ref=None
            ),
        )
    )
    incident = make(db_session, editor, content=content)
    service.transition(db_session, incident, "in_review", user_id=editor)

    with pytest.raises(IncidentError) as caught:
        service.transition(db_session, incident, "published", user_id=editor)

    # only the first says an analyst stands behind it; an unverified report need not cite anything
    assert len(caught.value.problems) == 1 and "1 address" in caught.value.problems[0]


# --- deleting -----------------------------------------------------------------------------------


def test_a_draft_that_was_never_shown_can_be_deleted_with_everything_it_holds(db_session, editor):
    incident = make(db_session, editor)
    service.delete_incident(db_session, incident)
    for model in (Incident, IncidentEvidence, IncidentAmount, IncidentAddress, IncidentUpdate):
        assert count(db_session, model) == 0


def test_what_was_ever_published_is_archived_never_deleted(db_session, editor):
    incident = make(db_session, editor)
    service.transition(db_session, incident, "in_review", user_id=editor)
    with pytest.raises(IncidentConflict, match="draft"):
        service.delete_incident(db_session, incident)

    service.transition(db_session, incident, "published", user_id=editor)
    service.transition(db_session, incident, "in_review", user_id=editor)
    service.transition(db_session, incident, "draft", user_id=editor)
    with pytest.raises(IncidentConflict, match="never published"):
        service.delete_incident(db_session, incident)
    assert count(db_session, Incident) == 1
