"""Opening, changing and documenting cases: what is allowed, and what each step writes down."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.access.audit import Actor
from app.access.permissions import Role
from app.cases import service
from app.cases.service import CaseConflict, CaseError
from app.database.models import Case, CaseEvent, CaseLink
from app.database.repositories import cases as store
from app.database.repositories import risk as risk_store
from tests.access_helpers import make_user
from tests.monitoring_helpers import ADDRESS, NOW, OTHER_ADDRESS, alert_for, watch

ETHEREUM = "eip155:1"


@pytest.fixture
def ana(db_session):
    return make_user(db_session, "ana@example.com", Role.ANALYST)


def actor_of(user):
    return Actor("user", user.id, user.email, user.organization_id)


def open_one(db_session, user, **fields):
    values = {
        "organization_id": user.organization_id,
        "created_by": user.id,
        "actor": actor_of(user),
        "title": "Suspicious deposits",
        "description": None,
        "priority": None,
        "assignee_id": None,
        "subject": None,
        "alert": None,
        "now": NOW,
        **fields,
    }
    case = service.open_case(db_session, **values)
    db_session.commit()
    return case


def events(db_session, case, kind=None):
    rows = store.events_of(db_session, case.id)
    return [e for e in rows if kind is None or e.kind == kind]


def change(db_session, case, user, **fields):
    changes = service.change_case(
        db_session, case, actor=actor_of(user), by=user.id, now=fields.pop("now", NOW), **fields
    )
    db_session.commit()
    return changes


def test_the_statuses_priorities_outcomes_and_ways_between_are_these():
    assert service.STATUSES == ("open", "in_progress", "closed")
    assert service.PRIORITIES == ("low", "medium", "high", "critical")
    assert service.OUTCOMES == ("confirmed", "dismissed", "escalated")
    assert {
        "open": {"in_progress", "closed"},
        "in_progress": {"open", "closed"},
        "closed": {"open"},
    } == service.TRANSITIONS
    assert service.LINK_KINDS == ("address", "transaction", "screening", "alert")


# --- opening ------------------------------------------------------------------------------------


def test_a_case_starts_open_with_a_medium_priority_and_a_first_entry_in_its_timeline(
    db_session, ana
):
    case = open_one(db_session, ana, title="  Suspicious deposits  ", description="  Look at it  ")

    assert case.id and case.organization_id == ana.organization_id and case.created_by == ana.id
    assert (case.title, case.description) == ("Suspicious deposits", "Look at it")
    assert (case.status, case.priority, case.outcome) == ("open", "medium", None)
    assert case.assignee_id is None and case.source_alert_id is None and case.closed_at is None
    assert (case.chain_id, case.address) == (None, None)
    (created,) = events(db_session, case)
    assert (created.kind, created.created_at) == ("created", NOW)
    assert (created.actor_type, created.actor_id, created.actor_label) == (
        "user",
        ana.id,
        "ana@example.com",
    )
    assert created.data == {"priority": "medium", "source_alert_id": None, "assignee_id": None}
    assert case.updated_at == NOW


def test_what_was_chosen_is_what_is_kept(db_session, ana):
    bob = make_user(db_session, "bob@example.com", Role.ANALYST)
    case = open_one(
        db_session,
        ana,
        priority="critical",
        assignee_id=bob.id,
        subject=(ETHEREUM, ADDRESS),
        description="  ",
    )
    assert (case.priority, case.assignee_id, case.chain_id, case.address) == (
        "critical",
        bob.id,
        ETHEREUM,
        ADDRESS,
    )
    assert case.description is None
    assert events(db_session, case)[0].data["assignee_id"] == bob.id


@pytest.mark.parametrize("title", ["", "   ", "\n"])
def test_a_case_needs_a_title(db_session, ana, title):
    with pytest.raises(CaseError, match="needs a title"):
        open_one(db_session, ana, title=title)


def test_a_title_may_be_two_hundred_characters_and_no_more(db_session, ana):
    assert len(open_one(db_session, ana, title="t" * 200).title) == 200
    with pytest.raises(CaseError, match="at most 200"):
        open_one(db_session, ana, title="t" * 201)


def test_a_description_has_a_limit_too(db_session, ana):
    assert len(open_one(db_session, ana, description="d" * 5000).description) == 5000
    with pytest.raises(CaseError, match="at most 5000"):
        open_one(db_session, ana, description="d" * 5001)


@pytest.mark.parametrize("priority", ["", "urgent", "HIGH", "normal"])
def test_only_the_four_priorities_exist(db_session, ana, priority):
    with pytest.raises(CaseError, match="low, medium, high or critical"):
        open_one(db_session, ana, priority=priority)


def test_the_assignee_must_be_an_active_person_of_the_organization(db_session, ana):
    stranger = make_user(db_session, "sam@example.com", Role.ANALYST, "Other Org")
    gone = make_user(db_session, "gone@example.com", Role.VIEWER)
    gone.is_active = False
    db_session.commit()
    for wrong in (stranger.id, gone.id, 10**6):
        with pytest.raises(CaseError, match="active person of this organization"):
            open_one(db_session, ana, assignee_id=wrong)
    assert db_session.scalar(select(Case)) is None  # none of the attempts left a case behind


# --- opening from an alert ----------------------------------------------------------------------


@pytest.fixture
def alert(db_session, settings, ana):
    assessment = risk_store.create_assessment(
        db_session,
        organization_id=ana.organization_id,
        subject_type="address",
        chain_id=ETHEREUM,
        subject=ADDRESS,
        options={},
    )
    db_session.commit()
    return alert_for(db_session, watch(db_session, settings, ana), assessment_id=assessment.id)


def test_a_case_from_an_alert_is_about_its_address_and_links_what_raised_it(db_session, ana, alert):
    case = open_one(db_session, ana, title="Look into it", alert=alert)

    assert (case.source_alert_id, case.chain_id, case.address) == (alert.id, ETHEREUM, ADDRESS)
    links = {(x.kind, x.chain_id, x.reference) for x in store.links_of(db_session, case.id)}
    assert links == {
        ("alert", None, str(alert.id)),
        ("address", ETHEREUM, ADDRESS),
        ("screening", None, f"scr_{alert.assessment_id}"),
    }
    assert events(db_session, case)[0].data["source_alert_id"] == alert.id


def test_an_alert_without_a_report_links_the_alert_and_the_address_only(db_session, settings, ana):
    bare = alert_for(db_session, watch(db_session, settings, ana, address=OTHER_ADDRESS))
    case = open_one(db_session, ana, alert=bare)
    assert {x.kind for x in store.links_of(db_session, case.id)} == {"alert", "address"}


def test_an_alert_has_one_unfinished_case_at_a_time(db_session, ana, alert):
    first = open_one(db_session, ana, alert=alert)

    with pytest.raises(CaseConflict, match=f"Case #{first.id} is already open"):
        open_one(db_session, ana, alert=alert)

    change(db_session, first, ana, status="in_progress")
    with pytest.raises(CaseConflict):
        open_one(db_session, ana, alert=alert)

    change(db_session, first, ana, status="closed", outcome="dismissed")
    second = open_one(db_session, ana, alert=alert)  # the first is over: a new look is allowed
    assert second.id != first.id and second.source_alert_id == alert.id


def test_an_alert_of_another_organization_cannot_be_the_source(db_session, settings, ana, alert):
    stranger = make_user(db_session, "sam@example.com", Role.ANALYST, "Other Org")
    with pytest.raises(CaseError, match="No such alert"):
        open_one(db_session, stranger, alert=alert)


# --- changing -----------------------------------------------------------------------------------


@pytest.fixture
def case(db_session, ana):
    return open_one(db_session, ana)


def test_work_starts_and_the_timeline_says_so(db_session, ana, case):
    changes = change(db_session, case, ana, status="in_progress", now=NOW + timedelta(hours=1))

    assert changes == {"status": {"from": "open", "to": "in_progress"}}
    assert case.status == "in_progress" and case.updated_at == NOW + timedelta(hours=1)
    (event,) = events(db_session, case, "status")
    assert event.data == {
        "from": "open",
        "to": "in_progress",
        "outcome": None,
        "outcome_from": None,
    }
    assert event.actor_label == "ana@example.com"


def test_a_case_is_closed_with_how_it_ended_and_who_and_when(db_session, ana, case):
    later = NOW + timedelta(days=2)
    changes = change(db_session, case, ana, status="closed", outcome="confirmed", now=later)

    assert changes == {
        "status": {"from": "open", "to": "closed"},
        "outcome": {"from": None, "to": "confirmed"},
    }
    assert (case.status, case.outcome, case.closed_at, case.closed_by) == (
        "closed",
        "confirmed",
        later,
        ana.id,
    )
    (event,) = events(db_session, case, "status")  # one entry for moving and concluding
    assert event.data == {
        "from": "open",
        "to": "closed",
        "outcome": "confirmed",
        "outcome_from": None,
    }


@pytest.mark.parametrize("outcome", service.OUTCOMES)
def test_each_outcome_closes_a_case(db_session, ana, case, outcome):
    change(db_session, case, ana, status="closed", outcome=outcome)
    assert case.outcome == outcome


def test_closing_without_saying_how_it_ended_is_refused(db_session, ana, case):
    with pytest.raises(CaseError, match="Say how it ended"):
        change(db_session, case, ana, status="closed")
    assert case.status == "open" and events(db_session, case, "status") == []


@pytest.mark.parametrize("status", [None, "open", "in_progress"])
def test_an_outcome_goes_only_with_closing(db_session, ana, case, status):
    with pytest.raises(CaseError, match="goes with closing"):
        change(db_session, case, ana, status=status, outcome="confirmed")
    assert case.outcome is None


@pytest.mark.parametrize(
    "fields,complaint",
    [
        ({"status": "done"}, "open, in_progress or closed"),
        ({"outcome": "won"}, "confirmed, dismissed or escalated"),
        ({"priority": "urgent"}, "low, medium, high or critical"),
        ({"title": ""}, "needs a title"),
        ({"title": "t" * 201}, "at most 200"),
        ({"description": "d" * 5001}, "at most 5000"),
        ({"assignee_id": 10**6}, "active person"),
    ],
)
def test_a_part_that_makes_no_sense_refuses_the_whole_change(
    db_session, ana, case, fields, complaint
):
    before = (case.title, case.priority, case.status)
    with pytest.raises(CaseError, match=complaint):
        change(db_session, case, ana, **{"priority": "high", "status": "in_progress", **fields})
    assert (case.title, case.priority, case.status) == before
    assert len(events(db_session, case)) == 1  # only the first entry, "created"


def test_a_closed_case_can_only_be_reopened(db_session, ana, case):
    change(db_session, case, ana, status="closed", outcome="dismissed")

    with pytest.raises(CaseConflict, match="reopen it first"):
        change(db_session, case, ana, status="in_progress")

    changes = change(db_session, case, ana, status="open")
    assert changes == {
        "status": {"from": "closed", "to": "open"},
        "outcome": {"from": "dismissed", "to": None},
    }
    assert (case.status, case.outcome, case.closed_at, case.closed_by) == ("open", None, None, None)
    assert events(db_session, case, "status")[-1].data["outcome_from"] == "dismissed"


def test_a_case_can_go_back_from_in_progress_to_open(db_session, ana, case):
    change(db_session, case, ana, status="in_progress")
    assert change(db_session, case, ana, status="open") == {
        "status": {"from": "in_progress", "to": "open"}
    }


def test_how_a_case_ended_can_be_corrected_without_moving_when_it_closed(db_session, ana, case):
    bob = make_user(db_session, "bob@example.com", Role.ANALYST)
    change(db_session, case, ana, status="closed", outcome="dismissed")
    closed_at = case.closed_at

    changes = change(
        db_session, case, bob, status="closed", outcome="escalated", now=NOW + timedelta(days=1)
    )

    assert changes == {"outcome": {"from": "dismissed", "to": "escalated"}}
    assert (case.closed_at, case.closed_by) == (closed_at, ana.id)
    assert change(db_session, case, bob, status="closed") == {}
    assert change(db_session, case, bob, outcome="escalated") == {}


def test_giving_what_is_already_so_changes_nothing_and_writes_nothing(db_session, ana, case):
    assert (
        change(
            db_session,
            case,
            ana,
            title=case.title,
            description=None,
            priority="medium",
            status="open",
            assignee_id=None,
        )
        == {}
    )
    assert change(db_session, case, ana) == {}
    assert len(events(db_session, case)) == 1


def test_the_title_and_the_description_are_edited_and_the_timeline_remembers(db_session, ana, case):
    changes = change(db_session, case, ana, title="  New title ", description="  A story ")

    assert changes == {
        "title": {"from": "Suspicious deposits", "to": "New title"},
        "description": {"changed": True},
    }
    assert (case.title, case.description) == ("New title", "A story")
    edits = events(db_session, case, "edited")
    assert [e.data for e in edits] == [
        {"field": "title", "from": "Suspicious deposits", "to": "New title"},
        {"field": "description"},  # the text itself is not copied into the timeline
    ]
    assert change(db_session, case, ana, description=None) == {"description": {"changed": True}}
    assert case.description is None


def test_the_priority_is_changed_and_remembered(db_session, ana, case):
    assert change(db_session, case, ana, priority="critical") == {
        "priority": {"from": "medium", "to": "critical"}
    }
    (event,) = events(db_session, case, "priority")
    assert event.data == {"from": "medium", "to": "critical"}


def test_a_case_is_handed_to_someone_and_taken_back(db_session, ana, case):
    bob = make_user(db_session, "bob@example.com", Role.ANALYST)

    assert change(db_session, case, ana, assignee_id=bob.id) == {
        "assignee_id": {"from": None, "to": bob.id}
    }
    assert case.assignee_id == bob.id and case.status == "open"  # handing over is not starting work
    assert change(db_session, case, ana, assignee_id=bob.id) == {}
    assert change(db_session, case, ana, assignee_id=None) == {
        "assignee_id": {"from": bob.id, "to": None}
    }
    handed, taken = events(db_session, case, "assignment")
    assert handed.data == {"from": None, "to": bob.id, "to_label": "bob@example.com"}
    assert taken.data == {"from": bob.id, "to": None, "to_label": None}


def test_leaving_the_assignee_out_keeps_it(db_session, ana, case):
    change(db_session, case, ana, assignee_id=ana.id)
    change(db_session, case, ana, status="in_progress")
    assert case.assignee_id == ana.id


def test_several_changes_at_once_are_several_entries(db_session, ana, case):
    change(db_session, case, ana, priority="high", status="in_progress", assignee_id=ana.id)
    assert [e.kind for e in events(db_session, case)] == [
        "created",
        "priority",
        "status",
        "assignment",
    ]


# --- notes --------------------------------------------------------------------------------------


def test_a_note_is_kept_as_written_with_who_wrote_it_and_when(db_session, ana, case):
    later = NOW + timedelta(minutes=5)
    event = service.add_note(
        db_session, case, body="  Asked the customer.\nNo answer.  ", actor=actor_of(ana), now=later
    )
    db_session.commit()

    assert (event.kind, event.body, event.created_at) == (
        "note",
        "Asked the customer.\nNo answer.",
        later,
    )
    assert (event.actor_id, event.actor_label) == (ana.id, "ana@example.com")
    assert case.updated_at == later  # the case shows its last activity


def test_a_note_cannot_be_empty_or_huge(db_session, ana, case):
    for body in ("", "  \n "):
        with pytest.raises(CaseError, match="cannot be empty"):
            service.add_note(db_session, case, body=body, actor=actor_of(ana), now=NOW)
    service.add_note(db_session, case, body="n" * 4000, actor=actor_of(ana), now=NOW)
    with pytest.raises(CaseError, match="at most 4000"):
        service.add_note(db_session, case, body="n" * 4001, actor=actor_of(ana), now=NOW)


def test_a_closed_case_can_still_be_written_on(db_session, ana, case):
    change(db_session, case, ana, status="closed", outcome="dismissed")
    service.add_note(db_session, case, body="Filed for the record.", actor=actor_of(ana), now=NOW)
    assert events(db_session, case, "note")[0].body == "Filed for the record."


def test_an_api_key_is_named_as_itself_in_the_timeline(db_session, ana, case):
    key = Actor("api_key", 9, "Triage bot", ana.organization_id)
    event = service.add_note(db_session, case, body="Seen.", actor=key, now=NOW)
    assert (event.actor_type, event.actor_id, event.actor_label) == ("api_key", 9, "Triage bot")


# --- links --------------------------------------------------------------------------------------


def link(db_session, case, ana, kind, reference, chain=None, note=None):
    made = service.add_link(
        db_session,
        case,
        kind=kind,
        chain=chain,
        reference=reference,
        note=note,
        actor=actor_of(ana),
        created_by=ana.id,
        now=NOW,
    )
    db_session.commit()
    return made


def test_a_link_key_names_the_kind_the_chain_and_the_reference():
    assert service.link_key("address", ETHEREUM, ADDRESS) == f"address:{ETHEREUM}:{ADDRESS}"
    assert service.link_key("alert", None, "7") == "alert::7"


def test_an_address_is_linked_in_storage_form_on_its_chain(db_session, ana, case):
    made = link(
        db_session,
        case,
        ana,
        "address",
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        "ethereum",
        " Seen here ",
    )

    assert (made.kind, made.chain_id, made.note) == ("address", ETHEREUM, "Seen here")
    assert made.reference == "0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed"
    assert made.link_key == f"address:{ETHEREUM}:{made.reference}" and made.created_by == ana.id
    (event,) = events(db_session, case, "link_added")
    assert event.data == {
        "kind": "address",
        "chain_id": ETHEREUM,
        "reference": made.reference,
        "note": "Seen here",
    }


@pytest.mark.parametrize(
    "chain,reference,complaint",
    [
        (None, ADDRESS, "needs its chain"),
        ("ethereum", "0x123", "not a valid"),
        ("dogecoin", ADDRESS, "not a chain this platform reads"),
        ("bitcoin", ADDRESS, "not a valid"),
        ("ethereum", "", "Say what to link to"),
        ("ethereum", "0x" + "ab" * 60, "Say what to link to"),
    ],
)
def test_an_address_that_is_not_one_is_not_linked(
    db_session, ana, case, chain, reference, complaint
):
    with pytest.raises(CaseError, match=complaint):
        link(db_session, case, ana, "address", reference, chain)
    assert store.links_of(db_session, case.id) == []


def test_a_transaction_is_linked_on_its_chain(db_session, ana, case):
    evm = "0x" + "AB" * 32
    made = link(db_session, case, ana, "transaction", f" {evm} ", "ethereum")
    assert (made.chain_id, made.reference) == (ETHEREUM, evm.lower())

    signature = "5" * 87  # a Solana signature: case matters
    solana = link(db_session, case, ana, "transaction", signature, "solana")
    assert solana.reference == signature
    mixed = "Ab" * 44
    assert link(db_session, case, ana, "transaction", mixed, "solana").reference == mixed


@pytest.mark.parametrize(
    "chain,reference",
    [
        (None, "0x" + "ab" * 32),
        ("dogecoin", "0x" + "ab" * 32),
        ("ethereum", "0xab cd"),
        ("ethereum", ""),
    ],
)
def test_a_transaction_needs_a_chain_and_a_hash(db_session, ana, case, chain, reference):
    with pytest.raises(CaseError):
        link(db_session, case, ana, "transaction", reference, chain)


def test_a_screening_of_the_organization_is_linked(db_session, ana, case):
    assessment = risk_store.create_assessment(
        db_session,
        organization_id=ana.organization_id,
        subject_type="address",
        chain_id=ETHEREUM,
        subject=ADDRESS,
        options={},
    )
    db_session.commit()

    made = link(db_session, case, ana, "screening", f" scr_{assessment.id} ")

    assert (made.kind, made.chain_id, made.reference) == ("screening", None, f"scr_{assessment.id}")
    for wrong in ("scr_999999", "scr_x", "12", "scr_", "report"):
        with pytest.raises(CaseError, match="No such screening"):
            link(db_session, case, ana, "screening", wrong)


def test_a_screening_of_another_organization_is_not_linked(db_session, ana, case):
    stranger = make_user(db_session, "sam@example.com", Role.ANALYST, "Other Org")
    theirs = risk_store.create_assessment(
        db_session,
        organization_id=stranger.organization_id,
        subject_type="address",
        chain_id=ETHEREUM,
        subject=ADDRESS,
        options={},
    )
    db_session.commit()
    with pytest.raises(CaseError, match="No such screening"):
        link(db_session, case, ana, "screening", f"scr_{theirs.id}")


def test_an_alert_of_the_organization_is_linked_and_another_ones_is_not(
    db_session, settings, ana, case
):
    mine = alert_for(db_session, watch(db_session, settings, ana))
    stranger = make_user(db_session, "sam@example.com", Role.ANALYST, "Other Org")
    theirs = alert_for(db_session, watch(db_session, settings, stranger))

    made = link(db_session, case, ana, "alert", str(mine.id))

    assert (made.kind, made.chain_id, made.reference) == ("alert", None, str(mine.id))
    for wrong in (str(theirs.id), "999999", "abc", "-1", "1.5", "٣"):
        with pytest.raises(CaseError, match="No such alert"):
            link(db_session, case, ana, "alert", wrong)


def test_an_unknown_kind_is_not_a_link(db_session, ana, case):
    with pytest.raises(CaseError, match="address, a transaction, a screening or an alert"):
        link(db_session, case, ana, "wallet", ADDRESS, "ethereum")


def test_the_same_thing_is_linked_once_however_it_is_spelled(db_session, ana, case):
    link(db_session, case, ana, "address", ADDRESS.upper().replace("0X", "0x"), "ethereum")

    with pytest.raises(CaseConflict, match="already has that link"):
        link(db_session, case, ana, "address", ADDRESS, "ethereum")
    assert len(store.links_of(db_session, case.id)) == 1
    # the same address on another chain is another link, and another case may link it too
    assert link(db_session, case, ana, "address", ADDRESS, "bsc").chain_id == "eip155:56"
    other = open_one(db_session, ana, title="Another")
    assert link(db_session, other, ana, "address", ADDRESS, "ethereum")


def test_a_race_to_add_the_same_link_is_a_conflict_not_a_crash(db_session, ana, case, monkeypatch):
    link(db_session, case, ana, "address", ADDRESS, "ethereum")
    monkeypatch.setattr(store, "find_link", lambda *args: None)  # blind to the winner

    with pytest.raises(CaseConflict, match="already has that link"):
        link(db_session, case, ana, "address", ADDRESS, "ethereum")


def test_a_case_holds_a_bounded_number_of_links(db_session, ana, case):
    for number in range(service.MAX_LINKS):
        link(db_session, case, ana, "address", "0x" + f"{number + 1:040x}", "ethereum")

    with pytest.raises(CaseConflict, match=f"at most {service.MAX_LINKS} links"):
        link(db_session, case, ana, "address", "0x" + f"{10**6:040x}", "ethereum")
    assert store.count_links(db_session, case.id) == service.MAX_LINKS


def test_a_reference_may_be_as_long_as_a_hash_can_be_and_no_longer(db_session, ana, case):
    longest = "A" * service.MAX_REFERENCE_LENGTH
    made = link(db_session, case, ana, "transaction", longest, "ethereum")
    assert made.reference == longest.lower()
    with pytest.raises(CaseError, match="Say what to link to"):
        link(
            db_session,
            case,
            ana,
            "transaction",
            "A" * (service.MAX_REFERENCE_LENGTH + 1),
            "ethereum",
        )


def test_the_cap_on_links_is_each_cases_own(db_session, ana, case):
    other = open_one(db_session, ana, title="Another")
    for number in range(service.MAX_LINKS):
        link(db_session, case, ana, "address", "0x" + f"{number + 1:040x}", "ethereum")

    assert link(db_session, other, ana, "address", "0x" + f"{10**6:040x}", "ethereum")
    assert store.count_links(db_session, case.id) == service.MAX_LINKS
    assert store.count_links(db_session, other.id) == 1


def test_a_link_note_has_a_limit(db_session, ana, case):
    assert len(link(db_session, case, ana, "address", ADDRESS, "ethereum", "n" * 200).note) == 200
    with pytest.raises(CaseError, match="at most 200"):
        link(db_session, case, ana, "address", OTHER_ADDRESS, "ethereum", "n" * 201)
    assert link(db_session, case, ana, "address", "0x" + "ee" * 20, "ethereum", "  ").note is None


def test_taking_a_link_away_leaves_a_trace_in_the_timeline(db_session, ana, case):
    made = link(db_session, case, ana, "address", ADDRESS, "ethereum")

    service.remove_link(db_session, case, made, actor=actor_of(ana), now=NOW + timedelta(hours=1))
    db_session.commit()

    assert store.links_of(db_session, case.id) == []
    removed = events(db_session, case, "link_removed")[0]
    assert removed.data == {"kind": "address", "chain_id": ETHEREUM, "reference": ADDRESS}
    assert db_session.scalar(select(CaseLink)) is None
    assert db_session.scalar(select(CaseEvent).where(CaseEvent.kind == "link_added")) is not None
