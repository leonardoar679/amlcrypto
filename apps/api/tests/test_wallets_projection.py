"""What a wallet profile does to screening: context, narrow, evidence-gated and reversible.

A profile is a record for people to read; a label is a fact the engine acts on. What a profile gives
screening is only what an address *is*, for the kinds of entity whose address says something about a
counterparty (an exchange, a bridge, a protocol), and only at a status somebody stands behind with
the document that says so, and only while the profile is published. These tests hold each half of
that, and the last two follow it through the engine: a bridge in the history is noted, an exchange
is not mistaken for a concentration of value, and both stop when the profile is withdrawn.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.chains.registry import get_chain
from app.database.connection import Sessions
from app.database.models import AddressLabel, Entity, LabelImport, LabelSource
from app.graph.builder import StoredLabels
from app.intel.lookup import source_status, source_statuses
from app.intel.model import Category, Freshness, SourceKind
from app.wallets import projection, service
from app.wallets.model import CONTEXT_CATEGORY, SOURCED_STATUSES, AttributionStatus, WalletKind
from app.wallets.service import Content
from tests.access_helpers import make_user
from tests.risk_helpers import NOW, ROOT, at, flow, graph_of
from tests.test_wallets_service import address, doc
from tests.test_wallets_service import profile as base_profile

ETHEREUM = get_chain("ethereum").id
LATER = NOW + timedelta(days=2)


@pytest.fixture
def editor(db_session):
    return make_user(db_session, "editor@example.com", "admin").id


def profile(**changes) -> Content:
    """A profile whose document was read on the day of the engine's clock (a later date would be in
    the future for it)."""
    values = {
        "evidence": (doc(retrieved_on=NOW.date(), published_on=NOW.date() - timedelta(days=1)),)
    }
    values.update(changes)
    return base_profile(**values)


def evm(number: int) -> str:
    return "0x" + f"{number:040x}"


def written(session, editor_id, content, slug="example-exchange"):
    return service.create_profile(session, user_id=editor_id, slug=slug, content=content, now=NOW)


def go(session, made, editor_id, *states, at=NOW):
    for state in states:
        service.transition(session, made, state, user_id=editor_id, now=at)
    return made


def publish(session, made, editor_id, at=NOW):
    return go(session, made, editor_id, "in_review", "published", at=at)


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


def source(session) -> LabelSource | None:
    return session.scalar(select(LabelSource).where(LabelSource.slug == projection.SLUG))


# --- what may become a label --------------------------------------------------------------------


def test_only_an_exchange_a_bridge_or_a_protocol_at_a_firm_status_gives_screening_anything(
    db_session, editor
):
    expected: dict[str, str] = {}
    shown = 0
    for number, kind in enumerate(WalletKind, 1):
        if kind is WalletKind.PUBLIC_FIGURE:
            # published only with an address the owner confirmed in an official document
            addresses = (address(address=evm(number * 10)),)
            statuses = [AttributionStatus.OFFICIALLY_CONFIRMED]
        else:
            statuses = list(AttributionStatus)
            addresses = tuple(
                address(address=evm(number * 10 + i), attribution_status=status.value)
                for i, status in enumerate(statuses)
            )
        content = profile(kind=kind.value, name=f"A {kind.value}", addresses=addresses)
        publish(
            db_session,
            written(db_session, editor, content, f"a-{kind.value}".replace("_", "-")),
            editor,
        )
        for i, status in enumerate(statuses):
            shown += 1
            if kind in CONTEXT_CATEGORY and status in SOURCED_STATUSES:
                expected[evm(number * 10 + i)] = CONTEXT_CATEGORY[kind].value

    got = {label.address: label.category for label in labels(db_session)}

    # shown: every address of every profile. acted on: three firm statuses of three kinds.
    assert shown == 8 * 6 - 5 and len(expected) == 9
    assert got == expected
    assert set(got.values()) == {"EXCHANGE", "BRIDGE", "DEFI"}  # never a risk category


@pytest.mark.parametrize("status", [s for s in AttributionStatus if s not in SOURCED_STATUSES])
def test_an_attribution_nobody_stands_behind_is_shown_and_never_acted_on(
    db_session, editor, status
):
    content = profile(addresses=(address(attribution_status=status.value),))

    publish(db_session, written(db_session, editor, content), editor)

    assert labels(db_session) == [] and source(db_session) is None


def test_an_address_whose_document_has_gone_is_not_acted_on_although_the_profile_still_says_it(
    db_session, editor
):
    from app.database.models import WalletProfileAddress

    made = publish(db_session, written(db_session, editor, profile()), editor)
    row = db_session.scalar(select(WalletProfileAddress))
    row.evidence_id = None  # the document row is gone, the statement is not
    db_session.flush()

    projection.project(db_session, now=LATER)

    assert [label.active for label in labels(db_session)] == [False]
    assert made.status == "published"


def test_a_label_says_where_it_comes_from_what_the_address_is_and_names_the_entity(
    db_session, editor
):
    content = profile(
        kind="bridge",
        name="Example Bridge",
        addresses=(
            address(address=evm(1), label="Deposit contract", attribution_status="CORROBORATED"),
            address(address=evm(2), label="Vault", confidence=72),
        ),
    )
    made = publish(db_session, written(db_session, editor, content, "example-bridge"), editor)

    by_address = {label.address: label for label in labels(db_session)}

    assert {a: label.confidence for a, label in by_address.items()} == {evm(1): 85, evm(2): 72}
    one = by_address[evm(1)]
    assert (one.category, one.chain_id, one.source_ref, one.active) == (
        Category.BRIDGE.value,
        ETHEREUM,
        made.slug,
        True,
    )
    assert (one.listed_as, one.source_url, one.first_observed, one.last_verified) == (
        "Deposit contract",
        None,
        NOW,
        NOW,
    )
    (entity,) = db_session.scalars(select(Entity).where(Entity.id == one.entity_id))
    assert (entity.name, entity.entity_type, entity.source_ref) == (
        "Example Bridge",
        "bridge",
        "example-bridge",
    )


def test_an_address_is_labelled_on_the_chain_the_editor_named_and_no_other(db_session, editor):
    content = profile(addresses=(address(address=evm(1), chain="bsc"),))

    publish(db_session, written(db_session, editor, content), editor)

    assert [label.chain_id for label in labels(db_session)] == [get_chain("bsc").id]


# --- only while published, and at once ----------------------------------------------------------


def test_only_a_published_profile_is_acted_on_and_taking_it_back_switches_it_off_at_once(
    db_session, editor
):
    made = written(db_session, editor, profile())
    go(db_session, made, editor, "in_review")
    assert labels(db_session) == []  # a draft and one in review say nothing to the engine

    go(db_session, made, editor, "published")
    assert [label.active for label in labels(db_session)] == [True]
    assert projection.labels_in_force(db_session, made) == 1

    go(db_session, made, editor, "in_review", at=LATER)
    (label,) = labels(db_session)
    assert not label.active and label.removed_at == LATER  # kept, with the time it was
    assert projection.labels_in_force(db_session, made) == 0


def test_a_label_that_was_switched_off_comes_back_with_its_history_when_published_again(
    db_session, editor
):
    made = publish(db_session, written(db_session, editor, profile()), editor)
    (first,) = labels(db_session)
    go(db_session, made, editor, "in_review", at=LATER)

    go(db_session, made, editor, "published", at=LATER + timedelta(days=1))

    (again,) = labels(db_session)
    assert again.id == first.id and again.active and again.removed_at is None
    assert again.first_observed == NOW


def test_an_archived_profile_is_not_acted_on(db_session, editor):
    made = publish(db_session, written(db_session, editor, profile()), editor)

    go(db_session, made, editor, "archived", at=LATER)

    assert [label.active for label in labels(db_session)] == [False]


def test_a_step_that_is_not_about_publication_does_not_touch_screening(db_session, editor):
    made = written(db_session, editor, profile())

    go(db_session, made, editor, "in_review", "draft")

    assert labels(db_session) == [] and source(db_session) is None


def test_what_an_editor_changes_in_review_is_what_screening_takes_once_published_again(
    db_session, editor
):
    made = publish(db_session, written(db_session, editor, profile()), editor)
    go(db_session, made, editor, "in_review", at=LATER)

    service.replace_content(
        db_session,
        made,
        profile(addresses=(address(address=evm(2), label="Cold wallet"),)),
        user_id=editor,
        now=LATER,
    )
    go(db_session, made, editor, "published", at=LATER)

    by_address = {label.address: label.active for label in labels(db_session)}
    assert by_address == {"0x" + "5aaeb6053f3e94c9b9a09f33669435e7ef1beaed": False, evm(2): True}


def test_two_profiles_naming_one_address_are_two_claims_and_each_can_go_on_its_own(
    db_session, editor
):
    first = publish(db_session, written(db_session, editor, profile(), "first-one"), editor)
    second = publish(
        db_session, written(db_session, editor, profile(name="Second"), "second-one"), editor
    )
    assert len(labels(db_session, active=True)) == 2

    go(db_session, first, editor, "in_review", at=LATER)

    assert projection.labels_in_force(db_session, first) == 0
    assert projection.labels_in_force(db_session, second) == 1


def test_the_labels_follow_the_status_in_the_same_transaction(db_session, editor):
    made = go(db_session, written(db_session, editor, profile()), editor, "in_review")
    attempt = db_session.begin_nested()

    service.transition(db_session, made, "published", user_id=editor, now=NOW)
    assert len(labels(db_session)) == 1
    attempt.rollback()

    db_session.refresh(made)
    assert made.status == "in_review"
    assert labels(db_session) == [] and source(db_session) is None


def test_a_profile_that_cannot_be_published_changes_nothing_in_screening(db_session, editor):
    content = profile(addresses=(address(address=evm(1), evidence_ref=None),))  # firm, no document
    made = go(db_session, written(db_session, editor, content), editor, "in_review")

    with pytest.raises(service.WalletError):
        service.transition(db_session, made, "published", user_id=editor, now=NOW)

    assert labels(db_session) == [] and source(db_session) is None


# --- the source ---------------------------------------------------------------------------------


def test_the_source_does_not_exist_until_a_profile_gives_it_something_to_say(db_session, editor):
    assert [s.slug for s in source_statuses(db_session, NOW)] == ["ofac_sdn"]
    only_a_fund = profile(kind="fund", name="A fund")

    publish(db_session, written(db_session, editor, only_a_fund, "a-fund"), editor)
    assert source(db_session) is None and projection.project(db_session) is None

    publish(db_session, written(db_session, editor, profile()), editor)

    row = source(db_session)
    assert (row.name, row.kind, row.url, row.status) == (
        "Public wallet profiles",
        SourceKind.ANALYTICS.value,
        None,
        "ok",
    )
    assert (row.record_count, row.last_checked_at, row.publish_date) == (1, NOW, NOW.date())


def test_an_editorial_source_does_not_go_stale_like_a_feed(db_session, editor):
    publish(db_session, written(db_session, editor, profile()), editor)
    row = source(db_session)

    soon = source_status(row, NOW + timedelta(days=30))
    years_on = source_status(row, NOW + timedelta(days=365 * 5))

    assert soon.freshness is Freshness.FRESH and years_on.freshness is Freshness.FRESH


def test_every_change_to_what_screening_takes_is_logged_like_an_import(db_session, editor):
    made = publish(db_session, written(db_session, editor, profile()), editor)
    go(db_session, made, editor, "in_review", at=NOW + timedelta(days=1))

    logged = list(db_session.scalars(select(LabelImport).order_by(LabelImport.id)))

    assert [(i.outcome, i.added, i.removed, i.records_seen) for i in logged] == [
        ("changed", 1, 0, 1),  # published: one address in
        ("changed", 0, 1, 0),  # taken back: the same address out
    ]
    assert source(db_session).record_count == 0


def test_projecting_again_changes_nothing_and_says_so(db_session, editor):
    publish(db_session, written(db_session, editor, profile()), editor)
    before = [(label.id, label.last_verified) for label in labels(db_session)]

    report = projection.project(db_session, now=NOW + timedelta(hours=3))

    assert report.outcome == "unchanged"
    assert [(label.id, label.last_verified) for label in labels(db_session)] == before
    assert source(db_session).last_checked_at == NOW + timedelta(hours=3)


def test_the_incident_tracker_and_the_profiles_are_two_sources_that_do_not_touch_each_other(
    db_session, editor
):
    from app.incidents import projection as incidents
    from app.incidents import service as incident_service
    from tests.test_incidents_service import TODAY as INCIDENT_DAY
    from tests.test_incidents_service import make as make_incident

    assert INCIDENT_DAY is not None
    incident = make_incident(db_session, editor)
    incident_service.transition(db_session, incident, "in_review", user_id=editor, now=NOW)
    incident_service.transition(db_session, incident, "published", user_id=editor, now=NOW)
    made = publish(db_session, written(db_session, editor, profile()), editor)
    assert incidents.labels_in_force(db_session, incident) == 1
    assert projection.labels_in_force(db_session, made) == 1

    go(db_session, made, editor, "in_review", at=LATER)

    assert incidents.labels_in_force(db_session, incident) == 1  # the incident's label stays
    assert projection.labels_in_force(db_session, made) == 0


# --- through the engine -------------------------------------------------------------------------


def screened(db, flows, **limits):
    """Screen ROOT against what the database says, with the movements given."""
    from app.risk.engine import screen_graph
    from app.risk.rules.registry import effective_rules
    from app.risk.scoring import ScoringSettings

    graph = graph_of(
        flows, labels=StoredLabels(Sessions(db.read, db.write), ETHEREUM, NOW), **limits
    )
    return screen_graph(
        graph,
        chain="ethereum",
        rules=effective_rules(None),
        settings=ScoringSettings(),
        now=NOW,
        bridges_known=False,
    )


def publish_for_real(db, content, slug, *, withdraw=False):
    with db.write() as session:
        editor = make_user(session, "editor@example.com", "admin").id
        made = service.create_profile(session, user_id=editor, slug=slug, content=content, now=NOW)
        publish(session, made, editor)
        if withdraw:
            go(session, made, editor, "in_review", at=LATER)
        session.commit()


def ids(report):
    return {item.finding.rule_id for item in report.findings}


BRIDGE = evm(9001)
EXCHANGE = evm(9002)


def test_a_published_bridge_is_noted_in_a_screening_and_a_withdrawn_one_is_not(committing_db):
    flows = [flow(ROOT, BRIDGE, 10**18, at(10))]
    before = screened(committing_db, flows)
    assert "AML-013" not in ids(before)  # nobody has said the address is a bridge

    content = profile(
        kind="bridge", name="Example Bridge", addresses=(address(address=BRIDGE, label="Vault"),)
    )
    publish_for_real(committing_db, content, "example-bridge")
    after = screened(committing_db, flows)

    assert "AML-013" in ids(after)
    (item,) = [i for i in after.findings if i.finding.rule_id == "AML-013"]
    assert item.finding.severity.value == "LOW"  # context, not a warning


def test_a_withdrawn_bridge_is_not_noted(committing_db):
    content = profile(
        kind="bridge", name="Example Bridge", addresses=(address(address=BRIDGE, label="Vault"),)
    )
    publish_for_real(committing_db, content, "example-bridge", withdraw=True)

    report = screened(committing_db, [flow(ROOT, BRIDGE, 10**18, at(10))])

    assert "AML-013" not in ids(report)


def _customer():
    deposits = [flow(ROOT, EXCHANGE, 10**18, at(24 * n)) for n in range(8)]
    withdrawals = [flow(EXCHANGE, ROOT, 10**18, at(24 * n + 3)) for n in range(4)]
    return deposits + withdrawals


def test_a_published_exchange_is_not_mistaken_for_a_concentration_of_value(committing_db):
    flows = _customer()
    assert "AML-014" in ids(screened(committing_db, flows))  # an unlabelled address would be

    content = profile(addresses=(address(address=EXCHANGE, label="Hot wallet"),))
    publish_for_real(committing_db, content, "example-exchange")

    assert "AML-014" not in ids(screened(committing_db, flows))


# --- what the first mutation run found missing --------------------------------------------------


def test_the_report_counts_the_addresses_and_the_profiles_they_come_from(db_session, editor):
    two = profile(addresses=(address(address=evm(1)), address(address=evm(2))))
    publish(db_session, written(db_session, editor, two, "first-exchange"), editor)
    one = profile(name="Another Exchange", addresses=(address(address=evm(3)),))
    publish(db_session, written(db_session, editor, one, "second-exchange"), editor)

    report = projection.project(db_session, now=NOW + timedelta(hours=1), force=True)

    assert (report.claims, report.entries) == (3, 2)  # three addresses, from two profiles
