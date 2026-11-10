"""What an incident does to screening: a narrow, evidence-gated, reversible set of labels.

The public pages may show every address an incident names. The engine acts on far fewer, because it
reads a label by its category alone: a victim's wallet or an exchange's hot wallet labelled
STOLEN_FUNDS would make everyone who deals with it look risky. Every test here is one half of that
promise, and the matrix at the top checks all of it at once.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.database.models import (
    AddressLabel,
    AuditLog,
    Entity,
    Incident,
    IncidentAddress,
    LabelImport,
    LabelSource,
)
from app.incidents import projection, service
from app.incidents.model import SOURCED_STATUSES, AddressRole, AttributionStatus
from app.intel.lookup import lookup, source_status, source_statuses
from app.intel.model import Category, Freshness, SourceKind
from app.intel.sources import Claim, EntityRecord
from tests.access_helpers import make_user
from tests.incident_helpers import EVM, create, make_editor, move
from tests.test_incidents_service import NOW, address, story

ETHEREUM = "eip155:1"
BSC = "eip155:56"
SCREENING_STATUSES = {
    AttributionStatus.OFFICIALLY_CONFIRMED,
    AttributionStatus.CORROBORATED,
    AttributionStatus.REPORTED_BY_ANALYST,
}


@pytest.fixture
def editor(db_session):
    return make_user(db_session, "editor@example.com", "admin").id


def evm(number: int) -> str:
    return "0x" + f"{number:040x}"


def written(session, editor_id, content, slug="a-theft") -> Incident:
    return service.create_incident(session, user_id=editor_id, slug=slug, content=content, now=NOW)


def go(session, incident, editor_id, *states, at=NOW) -> Incident:
    for state in states:
        service.transition(session, incident, state, user_id=editor_id, now=at)
    return incident


def publish(session, incident, editor_id, at=NOW) -> Incident:
    return go(session, incident, editor_id, "in_review", "published", at=at)


def labels(session, *, active: bool | None = None) -> list[AddressLabel]:
    query = (
        select(AddressLabel)
        .join(LabelSource, LabelSource.id == AddressLabel.source_id)
        .where(LabelSource.slug == projection.SLUG)
        .order_by(AddressLabel.id)
    )
    if active is not None:
        query = query.where(AddressLabel.active.is_(active))
    return list(session.scalars(query))


def tracker(session) -> LabelSource | None:
    return session.scalar(select(LabelSource).where(LabelSource.slug == projection.SLUG))


def every_role_in_every_status():
    """One address for each role in each status, so that what is shown and what is acted on can be
    told apart address by address."""
    items, meaning = [], {}
    for role in AddressRole:
        for status in AttributionStatus:
            number = len(items) + 1
            items.append(
                address(address=evm(number), role=role.value, attribution_status=status.value)
            )
            meaning[evm(number)] = (role, status)
    return story(addresses=tuple(items)), meaning


# --- what may become a label ---------------------------------------------------------------------


def test_of_every_address_an_incident_names_only_attacker_receivers_with_a_source_are_acted_on(
    db_session, editor
):
    content, meaning = every_role_in_every_status()
    incident = written(db_session, editor, content)

    publish(db_session, incident, editor)

    expected = {
        where
        for where, (role, status) in meaning.items()
        if role is AddressRole.ATTACKER_FUNDS_RECEIVER and status in SCREENING_STATUSES
    }
    # shown: all forty-eight. acted on: three.
    assert len(meaning) == 48 and len(expected) == 3
    assert {label.address for label in labels(db_session)} == expected


@pytest.mark.parametrize(
    "role", [role for role in AddressRole if role is not AddressRole.ATTACKER_FUNDS_RECEIVER]
)
def test_an_address_in_any_other_role_is_shown_and_never_flagged(db_session, editor, role):
    # The victim, an intermediary, an exchange's wallet, a bridge, a mixer, a freezing address, a
    # recovery address: even at the firmest status, none is a reason to doubt who deals with it.
    content = story(
        addresses=(
            address(address=evm(1), role=role.value, attribution_status="OFFICIALLY_CONFIRMED"),
        )
    )

    publish(db_session, written(db_session, editor, content), editor)

    assert labels(db_session) == [] and tracker(db_session) is None


@pytest.mark.parametrize(
    "status", [status for status in AttributionStatus if status not in SCREENING_STATUSES]
)
def test_an_attribution_nobody_stands_behind_is_shown_and_never_acted_on(
    db_session, editor, status
):
    content = story(
        addresses=(address(address=evm(1), attribution_status=status.value, evidence_ref=None),)
    )

    publish(db_session, written(db_session, editor, content), editor)

    assert labels(db_session) == [] and tracker(db_session) is None


def test_what_screening_acts_on_is_a_subset_of_what_has_to_cite_a_document():
    assert projection.SCREENING_STATUSES <= SOURCED_STATUSES
    assert projection.SCREENING_STATUSES == SCREENING_STATUSES
    assert {AddressRole.ATTACKER_FUNDS_RECEIVER} == projection.SCREENING_ROLES


def test_an_address_whose_document_has_gone_is_not_acted_on_although_the_story_still_says_it(
    db_session, editor
):
    incident = publish(db_session, written(db_session, editor, story()), editor)
    assert len(projection.wanted_claims(db_session)) == 1

    db_session.execute(
        update(IncidentAddress)
        .where(IncidentAddress.incident_id == incident.id)
        .values(evidence_id=None)
    )

    assert projection.wanted_claims(db_session) == []


# --- what a label says ---------------------------------------------------------------------------


def test_a_label_says_where_it_comes_from_how_sure_the_editor_is_and_names_the_incident(
    db_session, editor
):
    content = story(
        addresses=(
            address(address=evm(1), attribution_status="OFFICIALLY_CONFIRMED"),
            address(address=evm(2), attribution_status="CORROBORATED"),
            address(address=evm(3)),  # REPORTED_BY_ANALYST: 80, by default
            address(address=evm(4), attribution_status="REPORTED_BY_ANALYST", confidence=72),
        )
    )
    incident = publish(db_session, written(db_session, editor, content), editor)

    by_address = {label.address: label for label in labels(db_session)}

    assert {a: label.confidence for a, label in by_address.items()} == {
        evm(1): 95,
        evm(2): 85,
        evm(3): 80,
        evm(4): 72,  # the editor's own figure is the one that is used
    }
    one = by_address[evm(1)]
    assert (one.category, one.chain_id, one.source_ref, one.active) == (
        Category.STOLEN_FUNDS.value,
        ETHEREUM,
        incident.slug,
        True,
    )
    assert (one.listed_as, one.source_url, one.first_observed, one.last_verified) == (
        None,
        None,
        NOW,
        NOW,
    )
    (entity,) = db_session.scalars(select(Entity).where(Entity.id == one.entity_id))
    assert (entity.name, entity.entity_type, entity.source_ref) == (
        "A theft",
        "incident",
        "a-theft",
    )


def test_a_lookup_explains_a_label_and_a_victim_of_the_same_incident_has_none(db_session, editor):
    content = story(
        addresses=(
            address(address=evm(1)),
            address(
                address=evm(2), role="VICTIM_WALLET", attribution_status="OFFICIALLY_CONFIRMED"
            ),
            address(address=evm(3), role="EXCHANGE", attribution_status="OFFICIALLY_CONFIRMED"),
        )
    )
    publish(db_session, written(db_session, editor, content), editor)
    later = NOW + timedelta(hours=1)

    thief = lookup(db_session, "ethereum", evm(1), now=later)
    victim = lookup(db_session, "ethereum", evm(2), now=later)
    exchange = lookup(db_session, "ethereum", evm(3), now=later)

    (label,) = thief.labels
    assert (label.category, label.confidence, label.source.slug) == (
        Category.STOLEN_FUNDS,
        80,
        "incident_tracker",
    )
    text = label.explain()
    assert "STOLEN_FUNDS (80% confidence)" in text
    assert "Incident tracker [analytics]" in text and "'A theft' (incident)" in text
    assert not victim.known and not exchange.known
    assert "not proof" in victim.explain()


def test_an_address_is_labelled_on_the_chain_the_editor_named_and_no_other(db_session, editor):
    content = story(addresses=(address(address=evm(1), chain="bsc"),))

    publish(db_session, written(db_session, editor, content), editor)

    assert [(label.chain_id, label.address) for label in labels(db_session)] == [(BSC, evm(1))]
    assert not lookup(db_session, "ethereum", evm(1), now=NOW).known


# --- following what is published -----------------------------------------------------------------


def test_only_a_published_incident_is_acted_on_and_taking_it_back_switches_it_off_at_once(
    db_session, editor
):
    incident = written(db_session, editor, story())
    go(db_session, incident, editor, "in_review")
    assert labels(db_session) == [] and tracker(db_session) is None  # not published: nothing

    go(db_session, incident, editor, "published", at=NOW + timedelta(days=1))
    (label,) = labels(db_session)
    assert label.active and label.first_observed == NOW + timedelta(days=1)
    assert lookup(db_session, "ethereum", label.address, now=NOW + timedelta(days=1)).known

    go(db_session, incident, editor, "in_review", at=NOW + timedelta(days=2))
    (label,) = labels(db_session)
    assert not label.active and label.removed_at == NOW + timedelta(days=2)
    assert not lookup(db_session, "ethereum", label.address, now=NOW + timedelta(days=2)).known


def test_a_label_that_was_switched_off_comes_back_with_its_history_when_published_again(
    db_session, editor
):
    incident = publish(db_session, written(db_session, editor, story()), editor)
    go(db_session, incident, editor, "in_review", at=NOW + timedelta(days=2))

    go(db_session, incident, editor, "published", at=NOW + timedelta(days=5))

    (label,) = labels(db_session)
    assert label.active and label.removed_at is None
    assert label.first_observed == NOW  # first seen on the first day, not on the second time
    assert label.last_verified == NOW + timedelta(days=5)


def test_an_archived_incident_is_not_acted_on(db_session, editor):
    incident = publish(db_session, written(db_session, editor, story()), editor)

    go(db_session, incident, editor, "archived", at=NOW + timedelta(days=9))

    (label,) = labels(db_session)
    assert not label.active and label.removed_at == NOW + timedelta(days=9)


def test_a_step_that_is_not_about_publication_does_not_touch_screening(db_session, editor):
    incident = publish(db_session, written(db_session, editor, story()), editor)
    go(db_session, incident, editor, "archived")  # out of "published": projected
    logged = db_session.scalar(select(func.count()).select_from(LabelImport))

    go(db_session, incident, editor, "in_review", "draft")  # neither is "published": not

    assert db_session.scalar(select(func.count()).select_from(LabelImport)) == logged


def test_a_reworded_title_reaches_the_label_the_next_time_it_is_published(db_session, editor):
    incident = publish(db_session, written(db_session, editor, story()), editor)
    go(db_session, incident, editor, "in_review", at=NOW + timedelta(days=1))
    service.replace_content(
        db_session, incident, story(title="A theft, renamed"), user_id=editor, now=NOW
    )

    go(db_session, incident, editor, "published", at=NOW + timedelta(days=2))

    (label,) = labels(db_session)
    (entity,) = db_session.scalars(select(Entity).where(Entity.id == label.entity_id))
    assert entity.name == "A theft, renamed" and label.active


def test_what_an_editor_changes_in_review_is_what_screening_acts_on_once_it_is_published_again(
    db_session, editor
):
    before = story(addresses=(address(address=evm(1)), address(address=evm(2))))
    incident = publish(db_session, written(db_session, editor, before), editor)
    go(db_session, incident, editor, "in_review", at=NOW + timedelta(days=1))
    after = story(addresses=(address(address=evm(1), confidence=70), address(address=evm(3))))
    service.replace_content(db_session, incident, after, user_id=editor, now=NOW)

    go(db_session, incident, editor, "published", at=NOW + timedelta(days=2))

    by_address = {label.address: label for label in labels(db_session)}
    assert set(by_address) == {evm(1), evm(2), evm(3)}
    assert (by_address[evm(1)].active, by_address[evm(1)].confidence) == (True, 70)  # re-weighed
    assert not by_address[evm(2)].active  # no longer named
    assert by_address[evm(2)].removed_at == NOW + timedelta(days=1)
    assert by_address[evm(3)].active and by_address[evm(3)].first_observed == NOW + timedelta(
        days=2
    )


def test_two_incidents_naming_one_address_are_two_claims_and_each_can_go_on_its_own(
    db_session, editor
):
    first = publish(db_session, written(db_session, editor, story(), "first-theft"), editor)
    second = publish(db_session, written(db_session, editor, story(), "second-theft"), editor)
    assert sorted(label.source_ref for label in labels(db_session)) == [
        "first-theft",
        "second-theft",
    ]

    go(db_session, first, editor, "archived", at=NOW + timedelta(days=1))

    assert [label.source_ref for label in labels(db_session, active=True)] == ["second-theft"]
    assert second.status == "published"
    intel = lookup(db_session, "ethereum", EVM, now=NOW + timedelta(days=1, hours=1))
    assert [m.source_ref for m in intel.labels] == ["second-theft"]
    assert [m.source_ref for m in intel.former] == ["first-theft"]


def test_the_labels_follow_the_status_in_the_same_transaction(db_session, editor):
    incident = go(db_session, written(db_session, editor, story()), editor, "in_review")
    attempt = db_session.begin_nested()

    service.transition(db_session, incident, "published", user_id=editor, now=NOW)
    assert len(labels(db_session)) == 1
    attempt.rollback()

    db_session.refresh(incident)
    assert incident.status == "in_review"
    assert labels(db_session) == [] and tracker(db_session) is None


def test_an_incident_that_cannot_be_published_changes_nothing_in_screening(db_session, editor):
    content = story(addresses=(address(address=evm(1), evidence_ref=None),))  # sourced, no source
    incident = go(db_session, written(db_session, editor, content), editor, "in_review")

    with pytest.raises(service.IncidentError):
        service.transition(db_session, incident, "published", user_id=editor, now=NOW)

    assert labels(db_session) == [] and tracker(db_session) is None


# --- the source ----------------------------------------------------------------------------------


def test_the_source_does_not_exist_until_an_incident_gives_it_something_to_say(db_session, editor):
    assert [s.slug for s in source_statuses(db_session, NOW)] == ["ofac_sdn"]
    only_a_victim = story(addresses=(address(address=evm(1), role="VICTIM_WALLET"),))

    publish(db_session, written(db_session, editor, only_a_victim, "quiet"), editor)
    assert tracker(db_session) is None and projection.project(db_session) is None

    publish(db_session, written(db_session, editor, story(), "loud"), editor)

    row = tracker(db_session)
    assert (row.name, row.kind, row.url, row.status) == (
        "Incident tracker",
        SourceKind.ANALYTICS.value,
        None,
        "ok",
    )
    assert (row.record_count, row.last_checked_at, row.publish_date) == (1, NOW, NOW.date())
    assert row.refresh_seconds == projection.TEN_YEARS


def test_an_editorial_source_does_not_go_stale_like_a_feed(db_session, editor):
    publish(db_session, written(db_session, editor, story()), editor)
    row = tracker(db_session)

    soon = source_status(row, NOW + timedelta(days=30))
    years_on = source_status(row, NOW + timedelta(days=365 * 5))

    assert soon.freshness is Freshness.FRESH and years_on.freshness is Freshness.FRESH


def test_every_change_to_what_screening_acts_on_is_logged_like_an_import(db_session, editor):
    incident = publish(db_session, written(db_session, editor, story()), editor)
    go(db_session, incident, editor, "in_review", at=NOW + timedelta(days=1))

    logged = list(db_session.scalars(select(LabelImport).order_by(LabelImport.id)))

    assert [(i.outcome, i.added, i.removed, i.records_seen) for i in logged] == [
        ("changed", 1, 0, 1),  # published: one address in
        ("changed", 0, 1, 0),  # taken back: the same address out
    ]
    assert tracker(db_session).record_count == 0


# --- the same operation, run again ---------------------------------------------------------------


def test_projecting_again_changes_nothing_and_says_so(db_session, editor):
    publish(db_session, written(db_session, editor, story()), editor)
    before = [(label.id, label.last_verified) for label in labels(db_session)]

    report = projection.project(db_session, now=NOW + timedelta(hours=3))

    assert report.outcome == "unchanged"
    assert [(label.id, label.last_verified) for label in labels(db_session)] == before
    assert tracker(db_session).last_checked_at == NOW + timedelta(hours=3)


def test_forcing_it_confirms_every_label_again_and_repairs_a_label_that_was_altered(
    db_session, editor
):
    publish(db_session, written(db_session, editor, story()), editor)
    (label,) = labels(db_session)
    label.active, label.removed_at = False, NOW  # somebody switched it off by hand

    assert projection.project(db_session, now=NOW + timedelta(days=1)).outcome == "unchanged"
    assert not label.active  # a projection that sees no change does not look

    report = projection.project(db_session, now=NOW + timedelta(days=1), force=True)

    assert (report.outcome, report.updated) == ("changed", 1)
    assert label.active and label.removed_at is None
    assert label.last_verified == NOW + timedelta(days=1)


def claim(**changes) -> Claim:
    values = {
        "chain_id": ETHEREUM,
        "address": evm(1),
        "source_ref": "a-theft",
        "listed_as": None,
        "entity": EntityRecord("a-theft", "A theft", "incident", (), None),
        "category": Category.STOLEN_FUNDS,
        "confidence": 80,
    }
    values.update(changes)
    return Claim(**values)


def test_the_fingerprint_follows_anything_a_claim_says_and_not_the_order_it_is_listed_in():
    one, two = claim(), claim(address=evm(2))
    same = projection.fingerprint([one, two])

    assert projection.fingerprint([two, one]) == same
    renamed = EntityRecord("a-theft", "Another title", "incident", (), None)
    for changed in (
        claim(confidence=79),
        claim(chain_id=BSC),
        claim(address=evm(3)),
        claim(source_ref="another"),
        claim(entity=renamed),
    ):
        assert projection.fingerprint([changed, two]) != same
    assert projection.fingerprint([one]) != same and projection.fingerprint([]) != same


def test_the_number_of_labels_in_force_is_that_of_one_incident(db_session, editor):
    first = publish(db_session, written(db_session, editor, story(), "first-theft"), editor)
    two = story(addresses=(address(address=evm(1)), address(address=evm(2))))
    second = publish(db_session, written(db_session, editor, two, "second-theft"), editor)

    assert projection.labels_in_force(db_session, first) == 1
    assert projection.labels_in_force(db_session, second) == 2

    go(db_session, second, editor, "archived", at=NOW + timedelta(days=1))

    assert projection.labels_in_force(db_session, second) == 0
    assert projection.labels_in_force(db_session, first) == 1


# --- through the editor's API --------------------------------------------------------------------


def lookup_as(client, headers, where=EVM):
    response = client.post(
        "/api/v1/intel/lookup", json={"chain": "ethereum", "address": where}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_through_the_api_an_incident_goes_into_screening_when_published_and_out_when_it_is_not(
    client, db_session
):
    editor = make_editor(client, db_session)
    number = create(client, editor)["id"]

    move(client, editor, number, "in_review")
    assert lookup_as(client, editor)["known"] is False

    move(client, editor, number, "published")
    body = lookup_as(client, editor)
    assert body["known"] is True and body["confidence"] == 80
    (label,) = body["labels"]
    assert label["category"] == "STOLEN_FUNDS" and label["entity"]["name"] == "A theft"
    assert label["source"]["slug"] == "incident_tracker" and label["source_ref"] == "a-theft"

    move(client, editor, number, "archived")
    body = lookup_as(client, editor)
    assert body["known"] is False and [m["source_ref"] for m in body["former"]] == ["a-theft"]


def test_the_audit_entry_of_a_publication_says_how_many_labels_it_put_in_force(client, db_session):
    editor = make_editor(client, db_session)
    number = create(client, editor)["id"]

    move(client, editor, number, "in_review")
    move(client, editor, number, "published")
    move(client, editor, number, "archived")

    db_session.rollback()
    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "incident.status").order_by(AuditLog.id)
    ).all()
    assert [(r.details["to"], r.details["labels_in_force"]) for r in rows] == [
        ("in_review", 0),
        ("published", 1),
        ("archived", 0),
    ]
    db_session.rollback()
