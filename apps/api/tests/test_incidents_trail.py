"""The funds trail: movements between the addresses an incident names, each citing its document.

A movement is a report (the document says funds went from one address to another), so it is held to
the same rules as everything else an editor writes: both ends are addresses the incident already
names (so each has a role and a status on the page), the document is cited, text is clean, and
nothing half-checked is kept. What the trail must never do is reach screening: the projection to
labels reads addresses, not movements, and one test here holds that.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.database.models import (
    Incident,
    IncidentAddress,
    IncidentEvidence,
    IncidentFlow,
)
from app.incidents import service
from app.incidents.model import AddressRole
from app.incidents.service import Content, FlowInput, IncidentConflict, IncidentError
from tests.access_helpers import make_user
from tests.test_incidents_projection import (
    SCREENING_STATUSES,
    every_role_in_every_status,
    evm,
    labels,
    publish,
    written,
)
from tests.test_incidents_service import (
    EVM,
    EVM_CHECKSUMMED,
    NOW,
    TODAY,
    TRON,
    address,
    doc,
    make,
    story,
)

OTHER = "0x" + "cd" * 20
THIRD = "0x" + "ef" * 20
TX = "0x" + "ab" * 32


@pytest.fixture
def editor(db_session):
    return make_user(db_session, "editor@example.com", "admin").id


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def flow(**changes) -> FlowInput:
    values = {
        "from_chain": "ethereum",
        "from_address": EVM,
        "to_chain": "ethereum",
        "to_address": OTHER,
        "evidence_ref": "e1",
    }
    values.update(changes)
    return FlowInput(**values)


def trail(*flows: FlowInput, **changes) -> Content:
    """A story that names three addresses (two on Ethereum, one on TRON) and the movements given."""
    values = {
        "addresses": (
            address(),
            address(address=OTHER, role="INTERMEDIARY"),
            address(chain="tron", address=TRON, role="EXCHANGE"),
        ),
        "flows": flows,
    }
    values.update(changes)
    return story(**values)


def flows_of(session) -> list[IncidentFlow]:
    return list(session.scalars(select(IncidentFlow).order_by(IncidentFlow.position)))


# --- keeping a trail ----------------------------------------------------------------------------


def test_a_movement_is_kept_in_order_with_what_its_source_reported(db_session, editor):
    first = flow(
        tx_hash=TX,
        amount_text="about 4,500 ETH",
        occurred_on=TODAY - timedelta(days=2),
        note="  Reported by the firm.  ",
    )
    second = flow(from_address=OTHER, to_address=EVM)
    incident = make(db_session, editor, content=trail(first, second))

    one, two = flows_of(db_session)
    assert (one.position, two.position) == (0, 1)
    assert (one.from_chain_id, one.from_address) == ("eip155:1", EVM)
    assert (one.to_chain_id, one.to_address) == ("eip155:1", OTHER)
    assert one.tx_hash == TX and one.amount_text == "about 4,500 ETH"
    assert one.occurred_on == TODAY - timedelta(days=2)
    assert one.note == "Reported by the firm."  # text is trimmed, like everywhere else
    assert (two.from_address, two.to_address) == (OTHER, EVM)
    assert (two.tx_hash, two.amount_text, two.occurred_on, two.note) == (None, None, None, None)
    evidence_id = db_session.scalar(select(IncidentEvidence.id))
    assert one.evidence_id == evidence_id == two.evidence_id
    assert one.incident_id == incident.id and one.created_by == editor


def test_a_movement_may_cross_chains(db_session, editor):
    make(db_session, editor, content=trail(flow(to_chain="tron", to_address=TRON, tx_hash=TX)))

    (row,) = flows_of(db_session)
    assert (row.from_chain_id, row.to_chain_id) == ("eip155:1", "tron:728126428")
    assert row.to_address == TRON


def test_both_ends_of_a_movement_are_kept_in_the_form_their_chain_defines(db_session, editor):
    make(db_session, editor, content=trail(flow(from_address=EVM_CHECKSUMMED)))

    (row,) = flows_of(db_session)
    assert row.from_address == EVM  # an EVM address is stored in lower case


def test_a_transaction_hash_is_kept_without_the_spaces_around_it(db_session, editor):
    make(db_session, editor, content=trail(flow(tx_hash=f"  {TX}  ")))

    (row,) = flows_of(db_session)
    assert row.tx_hash == TX


def test_each_movement_cites_the_document_it_names(db_session, editor):
    two_documents = (doc("e1"), doc("e2", url="https://example.org/reports/two"))
    content = trail(
        flow(evidence_ref="e2"), flow(from_address=OTHER, to_address=EVM), flow(evidence_ref="e2"),
        evidence=two_documents,
    )  # fmt: skip
    make(db_session, editor, content=content)

    ids = {row.ref: row.id for row in db_session.scalars(select(IncidentEvidence))}
    assert [row.evidence_id for row in flows_of(db_session)] == [ids["e2"], ids["e1"], ids["e2"]]


def test_the_same_pair_may_be_listed_again_but_not_the_same_transaction(db_session, editor):
    several = (flow(tx_hash=TX), flow(tx_hash="0x" + "cd" * 32), flow(), flow())
    make(db_session, editor, content=trail(*several))
    assert count(db_session, IncidentFlow) == 4

    with pytest.raises(IncidentError, match="already listed"):
        make(db_session, editor, slug="another", content=trail(flow(tx_hash=TX), flow(tx_hash=TX)))
    # the same hash between another pair is another movement
    both_ways = (flow(tx_hash=TX), flow(from_address=OTHER, to_address=EVM, tx_hash=TX))
    make(db_session, editor, slug="a-third", content=trail(*both_ways))


def test_a_movement_stands_whatever_the_role_of_its_ends(db_session, editor):
    # What a role means is on the address, not on the line between two: funds that went on to an
    # exchange or came back to a victim's wallet are drawn as readily as the first hop.
    content = trail(
        flow(from_address=OTHER, to_chain="tron", to_address=TRON),
        flow(from_chain="tron", from_address=TRON, to_address=EVM),
    )
    make(db_session, editor, content=content)
    assert count(db_session, IncidentFlow) == 2


def test_an_incident_holds_at_most_a_hundred_and_fifty_movements(db_session, editor):
    many = tuple(flow(tx_hash=f"{n:064x}") for n in range(150))
    make(db_session, editor, content=trail(*many))
    assert count(db_session, IncidentFlow) == 150

    with pytest.raises(IncidentError, match="at most 150 movements"):
        make(db_session, editor, slug="too-many", content=trail(*many, flow()))


# --- what is refused ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"from_address": THIRD}, "sender is not one of the addresses"),
        ({"to_address": THIRD}, "receiver is not one of the addresses"),
        ({"to_chain": "tron"}, "not a valid tron address"),  # an Ethereum address is not TRON's
        ({"to_address": EVM}, "same address"),
        ({"to_address": EVM_CHECKSUMMED}, "same address"),  # a second spelling does not hide it
        ({"evidence_ref": "nope"}, "names the document"),
        ({"tx_hash": "not a hash"}, "not a transaction hash"),
        ({"tx_hash": "0x12"}, "not a transaction hash"),
        ({"from_chain": "nope"}, "not a chain this platform reads"),
        ({"from_address": "0x123"}, "not a valid ethereum address"),
        ({"amount_text": "x" * 81}, "at most 80"),
        ({"note": "x" * 301}, "at most 300"),
        ({"amount_text": "about\x07 4,500 ETH"}, "control character"),
        ({"note": "a‮b"}, "direction-changing"),
        ({"occurred_on": TODAY + timedelta(days=30)}, "future"),
    ],
)
def test_a_movement_must_be_between_named_addresses_and_cite_a_document(
    db_session, editor, change, message
):
    with pytest.raises(IncidentError, match=message):
        make(db_session, editor, content=trail(flow(**change)))
    assert count(db_session, Incident) == 0 and count(db_session, IncidentFlow) == 0


def test_a_movement_whose_sender_is_named_on_another_chain_only_is_refused(db_session, editor):
    # The ends are compared as (chain, address): naming 0x..cd on Ethereum is not naming it on BNB.
    content = trail(flow(from_chain="bsc", from_address=EVM))
    with pytest.raises(IncidentError, match="sender is not one of the addresses"):
        make(db_session, editor, content=content)


# --- replacing, publishing, deleting ------------------------------------------------------------


def test_replacing_the_content_replaces_the_trail_too(db_session, editor):
    incident = make(db_session, editor, content=trail(flow(tx_hash=TX), flow()))

    service.replace_content(
        db_session,
        incident,
        trail(flow(from_address=OTHER, to_address=EVM, amount_text="1,000 USDT")),
        user_id=editor,
        now=NOW + timedelta(hours=1),
    )

    (row,) = flows_of(db_session)
    assert (row.position, row.from_address, row.amount_text) == (0, OTHER, "1,000 USDT")

    service.replace_content(db_session, incident, trail(), user_id=editor, now=NOW)
    assert count(db_session, IncidentFlow) == 0


def test_a_replacement_that_drops_an_address_a_movement_uses_is_refused_whole(db_session, editor):
    incident = make(db_session, editor, content=trail(flow(tx_hash=TX)))
    without_the_receiver = story(addresses=(address(),), flows=(flow(tx_hash=TX),), title="New")

    with pytest.raises(IncidentError, match="receiver is not one of the addresses"):
        service.replace_content(db_session, incident, without_the_receiver, user_id=editor)

    assert incident.title == "A theft"
    (row,) = flows_of(db_session)
    assert row.tx_hash == TX and count(db_session, IncidentAddress) == 3


def test_a_published_trail_changes_only_when_the_incident_is_taken_back_to_review(
    db_session, editor
):
    incident = make(db_session, editor, content=trail(flow(tx_hash=TX)))
    service.transition(db_session, incident, "in_review", user_id=editor)
    service.transition(db_session, incident, "published", user_id=editor)

    with pytest.raises(IncidentConflict, match="only grows"):
        service.replace_content(db_session, incident, trail(), user_id=editor)
    assert count(db_session, IncidentFlow) == 1

    service.transition(db_session, incident, "in_review", user_id=editor)
    service.replace_content(db_session, incident, trail(), user_id=editor)
    assert count(db_session, IncidentFlow) == 0


def test_a_trail_is_not_a_condition_of_publishing(db_session, editor):
    incident = make(db_session, editor, content=trail())
    service.transition(db_session, incident, "in_review", user_id=editor)
    service.transition(db_session, incident, "published", user_id=editor)
    assert incident.status == "published"


def test_a_draft_that_was_never_shown_is_deleted_with_its_movements(db_session, editor):
    incident = make(db_session, editor, content=trail(flow(tx_hash=TX), flow()))
    service.delete_incident(db_session, incident)
    assert count(db_session, IncidentFlow) == 0 and count(db_session, Incident) == 0


# --- what a trail does not do -------------------------------------------------------------------


def test_a_trail_gives_screening_nothing_a_list_of_addresses_did_not(db_session, editor):
    # Forty-eight addresses, every role in every status, each handed on to the next in a chain of
    # movements. Screening acts on the same three addresses it would act on without any movement.
    content, meaning = every_role_in_every_status()
    ends = [item.address for item in content.addresses]
    movements = tuple(
        flow(from_address=sender, to_address=receiver)
        for sender, receiver in zip(ends, ends[1:], strict=False)  # n addresses, n - 1 pairs
    )
    incident = written(db_session, editor, dataclasses.replace(content, flows=movements))

    publish(db_session, incident, editor)

    expected = {
        where
        for where, (role, status) in meaning.items()
        if role is AddressRole.ATTACKER_FUNDS_RECEIVER and status in SCREENING_STATUSES
    }
    assert count(db_session, IncidentFlow) == 47 and len(expected) == 3
    assert {label.address for label in labels(db_session)} == expected
    assert evm(1) in ends  # the helper's addresses are the ones the movements joined
