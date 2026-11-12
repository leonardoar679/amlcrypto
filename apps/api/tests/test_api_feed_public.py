"""The news feed and the hot addresses, for anybody: what editors published, and only that.

The promises tested here are the ones the public pages and the Atom feed depend on: a draft, an
incident in review and an archived one have no item and no rank; an item is an incident's first
publication, a development dated at or after it, or a profile's first publication, and is announced
once; the ranking counts only firm attacker-receiver and intermediary addresses, by the day of the
event and not the day of publication; every answer holds exactly the fields meant to be public.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select, update

from app.database.models import Incident, IncidentAddress, IncidentUpdate, WalletProfile
from app.feed import public
from app.feed.model import (
    DEFAULT_WINDOW,
    FEED_NOTICE,
    HOT_NOTICE,
    INCIDENT_LABEL,
    PROFILE_LABEL,
    ROLE_WEIGHTS,
    SHOWN_INCIDENTS,
    STATUS_WEIGHTS,
    UPDATE_LABELS,
    WINDOWS,
)
from app.incidents.model import (
    ROLE_WORDS,
    SOURCED_STATUSES,
    STATUS_WORDS,
    AddressRole,
    AttributionStatus,
    UpdateKind,
)
from tests import incident_helpers, wallet_helpers
from tests.incident_helpers import make_editor

FEED = "/api/v1/public/feed"
HOT = "/api/v1/public/hot"
TRON = "TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy"

ITEM_FIELDS = {
    "kind",
    "at",
    "key",
    "label",
    "title",
    "summary",
    "link",
    "update_kind",
    "chain",
    "tx_hash",
}
HOT_FIELDS = {
    "rank",
    "chain",
    "address",
    "reported_in",
    "latest_event_on",
    "strongest",
    "incidents",
    "known_as",
}
INCIDENT_FIELDS = {"slug", "title", "kind", "occurred_on", "role_words", "status_words"}
# what readers see, written out here and not taken from the code under test: a change of wording
# is a change of a test
LABELS = {
    "event": "Development",
    "funds_moved": "Funds moved",
    "frozen": "Funds frozen",
    "recovered": "Funds recovered",
    "statement": "Statement",
    "correction": "Correction",
}


@pytest.fixture
def editor(client, db_session):
    return make_editor(client, db_session)


# --- helpers ------------------------------------------------------------------------------------


def evm(number: int) -> str:
    return "0x" + f"{number:040x}"


def today() -> date:
    return datetime.now(UTC).date()


def named(address, role="ATTACKER_FUNDS_RECEIVER", status="OFFICIALLY_CONFIRMED", chain="ethereum"):
    return {
        "chain": chain,
        "address": address,
        "role": role,
        "attribution_status": status,
        "evidence_ref": "e1",
    }


def incident(client, editor, slug, addresses=(), *, days_ago=0, publish=True, **changes) -> int:
    """An incident that happened `days_ago` days ago, published unless said otherwise."""
    document = incident_helpers.body(
        title=changes.pop("title", f"Incident {slug}"),
        occurred_on=(today() - timedelta(days=days_ago)).isoformat(),
        addresses=list(addresses),
        **changes,
    )
    made = client.post(
        incident_helpers.EDITOR_ROUTES, json={"slug": slug, **document}, headers=editor
    )
    assert made.status_code == 201, made.text
    number = made.json()["incident"]["id"]
    if publish:
        incident_helpers.publish(client, editor, number)
    return number


def add_update(client, editor, number, kind="event", text="Something happened.", at=None, **extra):
    moment = (at or datetime.now(UTC)).isoformat().replace("+00:00", "Z")
    response = client.post(
        f"{incident_helpers.EDITOR_ROUTES}/{number}/updates",
        json={"occurred_at": moment, "kind": kind, "body": text, **extra},
        headers=editor,
    )
    assert response.status_code in (200, 201), response.text


def profile(client, editor, slug="a-profile", name="A Profile", publish=True, **changes) -> int:
    made = wallet_helpers.create(client, editor, slug=slug, name=name, **changes)
    if publish:
        wallet_helpers.publish(client, editor, made["id"])
    return made["id"]


def feed(client, **params) -> dict:
    response = client.get(FEED, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def hot(client, **params) -> dict:
    response = client.get(HOT, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def keys_anywhere(value) -> set[str]:
    if isinstance(value, dict):
        found = set(value)
        for item in value.values():
            found |= keys_anywhere(item)
        return found
    if isinstance(value, list):
        found = set()
        for item in value:
            found |= keys_anywhere(item)
        return found
    return set()


# --- the weights are the ones the rest of the platform acts on ----------------------------------


def test_the_ranking_counts_exactly_the_statuses_screening_acts_on_and_two_roles():
    assert set(STATUS_WEIGHTS) == set(SOURCED_STATUSES)
    assert set(ROLE_WEIGHTS) == {AddressRole.ATTACKER_FUNDS_RECEIVER, AddressRole.INTERMEDIARY}
    assert (
        STATUS_WEIGHTS[AttributionStatus.OFFICIALLY_CONFIRMED]
        > STATUS_WEIGHTS[AttributionStatus.CORROBORATED]
        > STATUS_WEIGHTS[AttributionStatus.REPORTED_BY_ANALYST]
        > 0
    )
    assert (
        ROLE_WEIGHTS[AddressRole.ATTACKER_FUNDS_RECEIVER] > ROLE_WEIGHTS[AddressRole.INTERMEDIARY]
    )
    assert WINDOWS == (7, 30, 90) and DEFAULT_WINDOW in WINDOWS
    assert set(UPDATE_LABELS) == set(UpdateKind)  # every kind of entry has a name
    # and the names and the limits are the ones that readers and the pages know
    assert INCIDENT_LABEL == "New incident" and PROFILE_LABEL == "New wallet profile"
    assert {kind.value: label for kind, label in UPDATE_LABELS.items()} == LABELS
    assert SHOWN_INCIDENTS == 5


def test_the_notices_say_what_the_pages_and_the_feed_must_say():
    # an answer carries the platform's own words about what it is; comparing an answer with the
    # constant would pass for any wording, so the words that matter are held here
    assert "the platform has not verified them" in FEED_NOTICE
    assert "A correction is an item of its own" in FEED_NOTICE
    assert "not because of any attention they draw" in HOT_NOTICE
    assert "not as a finding about their owners" in HOT_NOTICE
    assert (
        "Victims, exchanges, bridges and attributions that nobody stands behind are never ranked."
        in HOT_NOTICE
    )


# --- the feed: what an item is ------------------------------------------------------------------


def test_the_feed_is_read_without_signing_in_and_holds_only_what_is_meant_to_be_public(
    client, editor
):
    number = incident(client, editor, "a-theft", internal_note="zq7-internal-only")
    add_update(client, editor, number, text="A development.")
    profile(client, editor, internal_note="zq7-internal-only")

    response = client.get(FEED)

    assert response.status_code == 200
    answer = response.json()
    assert set(answer) == {"success", "total", "limit", "offset", "items", "notice"}
    assert answer["success"] is True
    assert (answer["total"], answer["limit"], answer["offset"]) == (3, 20, 0)
    assert answer["notice"] == FEED_NOTICE
    assert all(set(item) == ITEM_FIELDS for item in answer["items"])
    text = json.dumps(answer)
    assert "zq7-internal-only" not in text and "editor@example.com" not in text
    assert not keys_anywhere(answer) & {"id", "internal_note", "created_by", "email", "user_id"}
    assert response.headers["cache-control"] == "public, max-age=60"
    assert "public" not in client.get(FEED, params={"kind": "person"}).headers.get(
        "cache-control", ""
    )


def test_each_kind_of_item_says_what_it_is(client, editor):
    number = incident(client, editor, "a-theft", title="A theft", summary="Funds were taken.")
    add_update(
        client,
        editor,
        number,
        kind="funds_moved",
        text="They moved on.",
        chain="ethereum",
        tx_hash="0x" + "ab" * 32,
    )
    profile(
        client, editor, slug="example-exchange", name="Example Exchange", summary="An exchange."
    )

    items = {item["kind"]: item for item in feed(client)["items"]}

    assert items["incident"] == {
        **items["incident"],
        "key": "incident:a-theft",
        "label": "New incident",
        "title": "A theft",
        "summary": "Funds were taken.",
        "link": {"type": "incident", "slug": "a-theft"},
        "update_kind": None,
        "chain": None,
        "tx_hash": None,
    }
    moved = items["update"]
    assert (moved["label"], moved["title"], moved["summary"]) == (
        "Funds moved",
        "A theft",
        "They moved on.",
    )
    assert (moved["update_kind"], moved["chain"], moved["tx_hash"]) == (
        "funds_moved",
        "ethereum",
        "0x" + "ab" * 32,
    )
    assert moved["link"] == {"type": "incident", "slug": "a-theft"}
    assert moved["key"].startswith("update:a-theft:") and moved["key"].count(":") == 4
    assert items["profile"] == {
        **items["profile"],
        "key": "profile:example-exchange",
        "label": "New wallet profile",
        "title": "Example Exchange",
        "summary": "An exchange.",
        "link": {"type": "wallet", "slug": "example-exchange"},
        "update_kind": None,
    }


def test_an_item_carries_the_moment_it_happened_and_a_key_that_names_it(client, editor, db_session):
    number = incident(client, editor, "a-theft")
    profile(client, editor)
    published = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    development = published + timedelta(days=3)
    db_session.rollback()
    db_session.execute(update(Incident).values(published_at=published))
    db_session.execute(update(WalletProfile).values(published_at=published + timedelta(hours=1)))
    db_session.add(
        IncidentUpdate(incident_id=number, occurred_at=development, kind="statement", body="Later.")
    )
    db_session.commit()

    items = {item["kind"]: item for item in feed(client)["items"]}

    def moment(item):
        return datetime.fromisoformat(item["at"].replace("Z", "+00:00"))

    # not the moment a row was made (just now): the moment of the publication, or of the entry
    assert moment(items["incident"]) == published
    assert moment(items["profile"]) == published + timedelta(hours=1)
    assert moment(items["update"]) == development
    digest = hashlib.sha256(b"Later.").hexdigest()[:8]
    assert items["update"]["key"] == (
        f"update:a-theft:{int(development.timestamp())}:statement:{digest}"
    )


@pytest.mark.parametrize("kind", [kind.value for kind in UpdateKind])
def test_every_kind_of_development_has_its_own_name_and_a_correction_is_one(client, editor, kind):
    number = incident(client, editor, "a-theft")
    add_update(client, editor, number, kind=kind, text=f"A {kind}.")

    (item,) = [i for i in feed(client)["items"] if i["kind"] == "update"]

    assert item["label"] == LABELS[kind]
    assert (item["update_kind"], item["summary"]) == (kind, f"A {kind}.")


def test_a_long_text_is_an_excerpt_cut_at_a_word(client, editor):
    long = ("word " * 200).strip()
    number = incident(client, editor, "a-theft", summary=long)
    add_update(client, editor, number, text=long)
    profile(client, editor, summary=long)

    items = feed(client)["items"]

    assert {item["kind"] for item in items} == {"incident", "update", "profile"}
    for item in items:
        assert item["summary"].endswith("word...") and len(item["summary"]) <= 283, item["kind"]


# --- the feed: only what is published, and announced once ---------------------------------------


def test_a_draft_a_profile_in_review_and_an_archived_one_have_no_item(client, editor):
    drafts = incident(client, editor, "a-draft", publish=False)
    reviewed = incident(client, editor, "in-review", publish=False)
    incident_helpers.move(client, editor, reviewed, "in_review")
    archived = incident(client, editor, "archived-one")
    incident_helpers.move(client, editor, archived, "archived")
    for slug, states in (("draft-profile", ()), ("review-profile", ("in_review",))):
        made = wallet_helpers.create(client, editor, slug=slug)
        if states:
            wallet_helpers.move(client, editor, made["id"], *states)
    assert drafts and reviewed

    assert feed(client) == {**feed(client), "items": [], "total": 0}


def test_taking_a_page_back_removes_its_items_and_publishing_it_again_announces_nothing_new(
    client, editor
):
    number = incident(client, editor, "taken-back")
    add_update(client, editor, number, text="A development.")
    made = profile(client, editor)
    first = {item["key"]: item["at"] for item in feed(client)["items"]}
    assert len(first) == 3

    incident_helpers.move(client, editor, number, "in_review")
    wallet_helpers.move(client, editor, made, "in_review")
    assert feed(client)["items"] == []

    incident_helpers.move(client, editor, number, "published")
    wallet_helpers.move(client, editor, made, "published")
    again = {item["key"]: item["at"] for item in feed(client)["items"]}
    assert again == first  # the same three, at the same moments: nothing is new


def test_a_development_is_news_only_when_it_is_dated_at_or_after_the_first_publication(
    client, editor, db_session
):
    number = incident(client, editor, "developing")  # its own timeline holds one entry from January
    db_session.rollback()
    stamp = db_session.scalar(select(Incident.published_at).where(Incident.slug == "developing"))
    for moment, text in (
        (stamp - timedelta(microseconds=1), "before"),
        (stamp, "at the moment"),
        (stamp + timedelta(seconds=1), "after"),
    ):
        db_session.add(
            IncidentUpdate(incident_id=number, occurred_at=moment, kind="event", body=text)
        )
    db_session.commit()

    updates = [item for item in feed(client)["items"] if item["kind"] == "update"]

    assert [item["summary"] for item in updates] == ["after", "at the moment"]
    assert feed(client, kind="update")["total"] == 2


# --- the feed: order, paging and bounds ---------------------------------------------------------


def test_the_feed_is_newest_first(client, editor):
    first = incident(client, editor, "first-one")
    incident(client, editor, "second-one")
    now = datetime.now(UTC)
    add_update(client, editor, first, text="Earlier.", at=now + timedelta(hours=1))
    add_update(client, editor, first, text="Later.", at=now + timedelta(hours=2))

    keys = [(item["kind"], item["summary"]) for item in feed(client)["items"]]

    assert keys[:2] == [("update", "Later."), ("update", "Earlier.")]
    assert [item["link"]["slug"] for item in feed(client)["items"][2:]] == [
        "second-one",
        "first-one",
    ]


def test_items_at_the_same_moment_come_in_a_fixed_order(client, editor, db_session):
    first = incident(client, editor, "first-one")
    incident(client, editor, "second-one")
    profile(client, editor)
    moment = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    db_session.execute(update(Incident).values(published_at=moment))
    db_session.execute(update(WalletProfile).values(published_at=moment))
    db_session.add(
        IncidentUpdate(incident_id=first, occurred_at=moment, kind="event", body="Same moment.")
    )
    db_session.commit()

    keys = [item["key"] for item in feed(client)["items"]]

    # an incident, then a development, then a profile; the newest row first inside a kind
    assert keys[:2] == ["incident:second-one", "incident:first-one"]
    assert keys[2].startswith("update:first-one:") and keys[3] == "profile:a-profile"


def test_the_feed_pages_and_filters_by_kind(client, editor):
    first = incident(client, editor, "first-one")
    incident(client, editor, "second-one")
    add_update(client, editor, first, text="A development.")
    profile(client, editor, slug="one-profile", name="One")
    profile(client, editor, slug="two-profile", name="Two")

    everything = feed(client)
    assert everything["total"] == 5
    by_kind = {kind: feed(client, kind=kind) for kind in ("incident", "update", "profile")}
    assert [by_kind[k]["total"] for k in by_kind] == [2, 1, 2]
    assert {item["kind"] for item in by_kind["incident"]["items"]} == {"incident"}

    pages = [feed(client, limit=2, offset=offset) for offset in (0, 2, 4)]
    assert [len(page["items"]) for page in pages] == [2, 2, 1]
    seen = [item["key"] for page in pages for item in page["items"]]
    assert seen == [item["key"] for item in everything["items"]] and len(set(seen)) == 5
    assert pages[1]["offset"] == 2 and pages[1]["limit"] == 2 and pages[1]["total"] == 5


@pytest.mark.parametrize(
    "query",
    [{"kind": "person"}, {"limit": 0}, {"limit": 51}, {"offset": -1}, {"offset": 1001}],
)
def test_a_feed_query_that_makes_no_sense_is_refused(client, query):
    assert client.get(FEED, params=query).status_code == 422


def test_the_edges_of_what_may_be_asked_for_are_allowed(client, editor):
    incident(client, editor, "a-theft")

    assert feed(client, limit=50)["limit"] == 50
    far = feed(client, offset=1000)
    assert far["items"] == [] and far["total"] == 1
    assert hot(client, limit=50)["limit"] == 50
    assert hot(client, offset=200)["offset"] == 200
    for days in WINDOWS:
        assert hot(client, window=days)["window_days"] == days


@pytest.mark.parametrize("kind", ["incident", "update", "profile"])
@pytest.mark.parametrize("same_moment", [False, True])
def test_a_page_that_cuts_a_kind_short_keeps_the_newest_of_it(
    client, editor, db_session, kind, same_moment
):
    base = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    numbers = [incident(client, editor, f"incident-{n}") for n in range(4)]
    made = [profile(client, editor, slug=f"profile-{n}", name=f"Profile {n}") for n in range(4)]
    db_session.rollback()
    for n, number in enumerate(numbers):
        moment = base if same_moment else base + timedelta(hours=n)
        db_session.execute(
            update(Incident).where(Incident.id == number).values(published_at=moment)
        )
    for n, number in enumerate(made):
        moment = base if same_moment else base + timedelta(hours=n)
        db_session.execute(
            update(WalletProfile).where(WalletProfile.id == number).values(published_at=moment)
        )
    for n in range(4):
        moment = base + timedelta(days=1, hours=0 if same_moment else n)
        db_session.add(
            IncidentUpdate(
                incident_id=numbers[0], occurred_at=moment, kind="event", body=f"Development {n}."
            )
        )
    db_session.commit()

    everything = feed(client, kind=kind, limit=50)
    first_page = feed(client, kind=kind, limit=2)
    second_page = feed(client, kind=kind, limit=2, offset=2)

    # a page asks for no more than it needs of each kind, and the ones it keeps are the newest
    assert len(everything["items"]) == 4
    assert first_page["total"] == second_page["total"] == 4
    assert first_page["items"] == everything["items"][:2]
    assert second_page["items"] == everything["items"][2:]


def test_a_published_page_that_has_no_date_of_publication_is_not_announced(
    client, editor, db_session
):
    incident(client, editor, "a-theft")
    profile(client, editor)
    db_session.rollback()
    db_session.execute(update(Incident).values(published_at=None))
    db_session.execute(update(WalletProfile).values(published_at=None))
    db_session.commit()

    answer = feed(client)

    assert answer["items"] == [] and answer["total"] == 0


# --- the hot addresses: who is ranked -----------------------------------------------------------


def test_only_firm_attacker_receivers_and_intermediaries_are_ranked(client, editor):
    rows, where = [], {}
    for r, role in enumerate(AddressRole):
        for s, status in enumerate(AttributionStatus):
            # the addresses run the other way from what each is worth, so that an order that
            # ignores it shows
            address = evm(1000 - (r * 10 + s))
            rows.append(named(address, role.value, status.value))
            where[(role.value, status.value)] = address
    assert len(rows) == 48
    incident(client, editor, "everything", rows)

    answer = hot(client)

    # three firm statuses of two roles, in the order of what each is worth (written here, not
    # taken from the code)
    worth = [
        ("ATTACKER_FUNDS_RECEIVER", "OFFICIALLY_CONFIRMED"),
        ("ATTACKER_FUNDS_RECEIVER", "CORROBORATED"),
        ("ATTACKER_FUNDS_RECEIVER", "REPORTED_BY_ANALYST"),
        ("INTERMEDIARY", "OFFICIALLY_CONFIRMED"),
        ("INTERMEDIARY", "CORROBORATED"),
        ("INTERMEDIARY", "REPORTED_BY_ANALYST"),
    ]
    assert [item["address"] for item in answer["items"]] == [where[pair] for pair in worth]
    assert answer["total"] == 6 and [item["rank"] for item in answer["items"]] == [1, 2, 3, 4, 5, 6]


def test_each_weight_is_held_between_two_fades_of_a_confirmed_receiver(client, editor):
    """A confirmed receiver is worth 1.0, and in a window of thirty days a weight halves every
    ten, so one of n days ago is worth 0.5 ** (n / 10): 0.871 after two days, 0.812 after three,
    0.707 after five, 0.660 after six, 0.616 after seven, 0.574 after eight, 0.536 after nine, 0.5
    after ten, 0.435 after twelve and 0.406 after thirteen. Every other standing is then placed
    between two of those, with numbers written here and not taken from the code: 0.85 for a
    corroborated receiver, 0.70 for an analyst's report, 0.60 for one who passed funds on, and 0.51
    and 0.42 for the same at the lower statuses."""
    receiver, middle = "ATTACKER_FUNDS_RECEIVER", "INTERMEDIARY"
    confirmed, corroborated, analyst = "OFFICIALLY_CONFIRMED", "CORROBORATED", "REPORTED_BY_ANALYST"
    in_order_of_worth = [
        (receiver, confirmed, 2),  # 0.871
        (receiver, corroborated, 0),  # 0.85
        (receiver, confirmed, 3),  # 0.812
        (receiver, confirmed, 5),  # 0.707
        (receiver, analyst, 0),  # 0.70
        (receiver, confirmed, 6),  # 0.660
        (receiver, confirmed, 7),  # 0.616
        (middle, confirmed, 0),  # 0.60
        (receiver, confirmed, 8),  # 0.574
        (receiver, confirmed, 9),  # 0.536
        (middle, corroborated, 0),  # 0.51
        (receiver, confirmed, 10),  # 0.5
        (receiver, confirmed, 12),  # 0.435
        (middle, analyst, 0),  # 0.42
        (receiver, confirmed, 13),  # 0.406
    ]
    addresses = []
    for number, (role, status, days) in enumerate(in_order_of_worth):
        address = evm(500 - number)  # the other way round from the order of worth
        addresses.append(address)
        incident(
            client, editor, f"incident-{number:02d}", [named(address, role, status)], days_ago=days
        )

    answer = hot(client)

    assert [item["address"] for item in answer["items"]] == addresses


def test_an_address_whose_document_has_gone_is_not_ranked(client, editor, db_session):
    incident(client, editor, "an-incident", [named(evm(1)), named(evm(2))])
    db_session.rollback()
    db_session.execute(
        update(IncidentAddress).where(IncidentAddress.address == evm(2)).values(evidence_id=None)
    )
    db_session.commit()

    assert [item["address"] for item in hot(client)["items"]] == [evm(1)]


# --- the hot addresses: when ---------------------------------------------------------------------


def test_the_day_of_the_event_counts_and_the_day_of_publication_does_not(client, editor):
    # all four were published just now; only what happened inside the window is ranked
    incident(client, editor, "today", [named(evm(1))], days_ago=0)
    incident(client, editor, "twenty-days", [named(evm(2))], days_ago=20)
    incident(client, editor, "forty-days", [named(evm(3))], days_ago=40)
    incident(client, editor, "back-filled", [named(evm(4))], days_ago=400)

    def ranked(window):
        return [item["address"] for item in hot(client, window=window)["items"]]

    assert ranked(7) == [evm(1)]
    assert ranked(30) == [evm(1), evm(2)]
    assert ranked(90) == [evm(1), evm(2), evm(3)]  # and the one back-filled today is in none


def test_a_development_brings_an_old_incident_back_inside_the_window(client, editor):
    number = incident(client, editor, "old-but-developing", [named(evm(2))], days_ago=60)
    assert hot(client, window=30)["items"] == []

    add_update(client, editor, number, text="It is moving again.")

    (item,) = hot(client, window=30)["items"]
    assert item["address"] == evm(2) and item["latest_event_on"] == today().isoformat()


def test_a_development_counts_from_the_first_moment_of_the_first_day_inside_the_window(
    client, editor, db_session
):
    number = incident(client, editor, "old-but-developing", [named(evm(1))], days_ago=90)
    start = datetime.combine(today() - timedelta(days=30), time.min, tzinfo=UTC)
    db_session.add(
        IncidentUpdate(
            incident_id=number, occurred_at=start, kind="event", body="On the first day inside."
        )
    )
    db_session.commit()

    assert [item["address"] for item in hot(client, window=30)["items"]] == [evm(1)]

    db_session.execute(
        update(IncidentUpdate)
        .where(IncidentUpdate.body == "On the first day inside.")
        .values(occurred_at=start - timedelta(microseconds=1))
    )
    db_session.commit()

    assert hot(client, window=30)["items"] == []  # a moment earlier, and it is the day before


def test_a_fresher_incident_outranks_an_older_one_of_the_same_weight(client, editor):
    incident(client, editor, "older", [named(evm(1))], days_ago=15)
    incident(client, editor, "fresher", [named(evm(2))], days_ago=0)

    assert [item["address"] for item in hot(client, window=30)["items"]] == [evm(2), evm(1)]


def test_the_standing_of_an_address_adds_up_over_the_incidents_that_name_it(client, editor):
    analyst = "REPORTED_BY_ANALYST"
    incident(client, editor, "one", [named(evm(1), status=analyst), named(evm(3))])
    incident(client, editor, "two", [named(evm(1), status=analyst)])

    items = hot(client)["items"]

    # 0.7 + 0.7 = 1.4 beats one confirmed receiver (1.0), and counts the incidents it is named in
    assert [(item["address"], item["reported_in"]) for item in items] == [(evm(1), 2), (evm(3), 1)]


@pytest.mark.parametrize("worst_first", [False, True])
def test_an_address_named_twice_in_one_incident_counts_once_at_what_is_worth_the_most(
    client, editor, worst_first
):
    both = [
        named(evm(1), "ATTACKER_FUNDS_RECEIVER", "REPORTED_BY_ANALYST"),  # 0.70
        named(evm(1), "INTERMEDIARY", "OFFICIALLY_CONFIRMED"),  # 0.60
    ]
    if worst_first:
        both.reverse()  # which row is written first makes no difference
    incident(client, editor, "one", [*both, named(evm(2), "INTERMEDIARY", "CORROBORATED")])  # 0.51

    items = hot(client)["items"]

    assert [(item["address"], item["reported_in"]) for item in items] == [(evm(1), 1), (evm(2), 1)]
    assert items[0]["strongest"]["status"] == "REPORTED_BY_ANALYST"
    assert items[0]["incidents"][0]["role_words"] == ROLE_WORDS[AddressRole.ATTACKER_FUNDS_RECEIVER]


def test_equal_standing_is_ordered_by_chain_and_address_so_that_the_order_never_moves(
    client, editor
):
    incident(client, editor, "one", [named(evm(3)), named(evm(1)), named(evm(2))])

    assert [item["address"] for item in hot(client)["items"]] == [evm(1), evm(2), evm(3)]


def test_equal_standing_on_different_chains_is_ordered_by_chain_first(client, editor):
    incident(
        client,
        editor,
        "one",
        [named(TRON, chain="tron"), named(evm(1), chain="bsc"), named(evm(2), chain="ethereum")],
    )

    assert [(item["chain"], item["address"]) for item in hot(client)["items"]] == [
        ("ethereum", evm(2)),
        ("bsc", evm(1)),
        ("tron", TRON),
    ]


def test_equal_standing_is_ordered_by_the_latest_event_before_the_address(client, editor):
    # 1.0 for a confirmed receiver of today; 0.5 + 0.5 for one that two incidents of ten days ago
    # name (a weight halves every ten days in a window of thirty): the same, and the one that was
    # reported most recently comes first
    incident(client, editor, "now", [named(evm(9))], days_ago=0)
    incident(client, editor, "ten-a", [named(evm(1))], days_ago=10)
    incident(client, editor, "ten-b", [named(evm(1))], days_ago=10)

    items = hot(client)["items"]

    assert [item["address"] for item in items] == [evm(9), evm(1)]
    assert items[0]["latest_event_on"] > items[1]["latest_event_on"]


def test_a_development_dated_ahead_counts_as_today_and_no_more(client, editor):
    incident(client, editor, "a-theft", [named(evm(1))])  # a confirmed receiver of today: 1.0
    number = incident(
        client, editor, "analyst", [named(evm(2), status="REPORTED_BY_ANALYST")]
    )  # 0.7
    add_update(
        client, editor, number, text="Dated ahead.", at=datetime.now(UTC) + timedelta(days=10)
    )

    # a date in the future does not make an incident worth more than one of today
    assert [item["address"] for item in hot(client)["items"]] == [evm(1), evm(2)]


def test_how_fast_an_incident_fades_follows_the_window(client, editor):
    # X: an analyst's report from today (0.70). Y: a confirmation from a week ago (1.00 x its fade)
    incident(client, editor, "report-x", [named(evm(1), status="REPORTED_BY_ANALYST")], days_ago=0)
    incident(client, editor, "report-y", [named(evm(2))], days_ago=7)

    def ranked(window):
        return [item["address"] for item in hot(client, window=window)["items"]]

    # a window of 30 days halves every ten: Y is worth 0.62 and X comes first. A window of 90 days
    # halves every thirty: Y is worth 0.85 and comes first
    assert ranked(30) == [evm(1), evm(2)]
    assert ranked(90) == [evm(2), evm(1)]


def test_the_edge_of_the_window_is_inside_it_and_the_day_after_is_not(db_session, client, editor):
    incident(client, editor, "an-incident", [named(evm(1))], days_ago=0)
    db_session.rollback()

    def count(days_on):
        _, total = public.hot(
            db_session,
            window=30,
            chain_id=None,
            limit=10,
            offset=0,
            today=today() + timedelta(days=days_on),
        )
        return total

    # thirty days on, the incident is exactly thirty days old: still inside; a day later it is not
    assert [count(0), count(30), count(31)] == [1, 1, 0]


# --- the hot addresses: filters, paging and what a rank says ------------------------------------


def test_the_ranking_filters_by_window_and_chain_and_pages(client, editor):
    incident(
        client,
        editor,
        "on-two-chains",
        [
            named(evm(1)),
            named(evm(1), chain="bsc"),
            named(evm(2), status="CORROBORATED"),
            named(TRON, chain="tron", status="REPORTED_BY_ANALYST"),
        ],
    )

    everything = hot(client)
    assert everything["total"] == 4 and everything["window_days"] == 30
    assert hot(client, chain="tron")["total"] == 1
    assert [i["chain"] for i in hot(client, chain="bsc")["items"]] == ["bsc"]
    assert {i["chain"] for i in hot(client, chain="ethereum")["items"]} == {"ethereum"}
    assert [hot(client, window=window)["window_days"] for window in WINDOWS] == [7, 30, 90]
    pages = [hot(client, limit=2, offset=offset) for offset in (0, 2)]
    assert [item["rank"] for page in pages for item in page["items"]] == [1, 2, 3, 4]
    assert pages[0]["items"] + pages[1]["items"] == everything["items"]
    assert [page["total"] for page in pages] == [4, 4]  # the whole count, not what is on the page
    assert (pages[0]["limit"], pages[0]["offset"], pages[1]["offset"]) == (2, 0, 2)


@pytest.mark.parametrize(
    "query",
    [
        {"window": 10},
        {"window": 6},
        {"window": 91},
        {"chain": "dogecoin"},
        {"limit": 0},
        {"limit": 51},
        {"offset": -1},
        {"offset": 201},
    ],
)
def test_a_ranking_query_that_makes_no_sense_is_refused(client, query):
    assert client.get(HOT, params=query).status_code == 422


def test_a_ranking_query_that_is_refused_says_why(client):
    window = client.get(HOT, params={"window": 10})
    chain = client.get(HOT, params={"chain": "dogecoin"})

    assert (window.status_code, window.json()["error"]["code"]) == (422, "invalid_window")
    assert "7, 30, 90" in window.json()["error"]["message"]
    assert (chain.status_code, chain.json()["error"]["code"]) == (422, "unknown_chain")
    assert "ethereum" in chain.json()["error"]["message"]


def test_what_a_ranked_address_says_and_who_a_profile_says_it_is(client, editor):
    incident(client, editor, "older", [named(evm(1), status="REPORTED_BY_ANALYST")], days_ago=5)
    incident(client, editor, "newer", [named(evm(1), "INTERMEDIARY", "CORROBORATED")], days_ago=1)
    answer = hot(client)

    (item,) = answer["items"]
    assert set(answer) == {"success", "window_days", "total", "limit", "offset", "items", "notice"}
    assert answer["notice"] == HOT_NOTICE
    assert (answer["success"], answer["limit"], answer["offset"]) == (True, 20, 0)
    assert set(item) == HOT_FIELDS and item["known_as"] is None
    assert (item["rank"], item["chain"], item["address"], item["reported_in"]) == (
        1,
        "ethereum",
        evm(1),
        2,
    )
    assert item["latest_event_on"] == (today() - timedelta(days=1)).isoformat()
    assert item["strongest"] == {
        "status": "CORROBORATED",
        "status_words": STATUS_WORDS[AttributionStatus.CORROBORATED],
    }
    # the incidents that name it, the latest event first, each in the platform's fixed words
    assert [entry["slug"] for entry in item["incidents"]] == ["newer", "older"]
    assert all(set(entry) == INCIDENT_FIELDS for entry in item["incidents"])
    assert item["incidents"][0]["role_words"] == ROLE_WORDS[AddressRole.INTERMEDIARY]
    assert (
        item["incidents"][1]["status_words"] == STATUS_WORDS[AttributionStatus.REPORTED_BY_ANALYST]
    )
    assert item["incidents"][0]["occurred_on"] == (today() - timedelta(days=1)).isoformat()
    assert [(entry["title"], entry["kind"]) for entry in item["incidents"]] == [
        ("Incident newer", "theft"),
        ("Incident older", "theft"),
    ]

    its_address = {**wallet_helpers.DOCUMENT["addresses"][0], "address": evm(1)}
    made = profile(client, editor, publish=False, addresses=[its_address])
    assert hot(client)["items"][0]["known_as"] is None  # a draft says nothing
    wallet_helpers.publish(client, editor, made)
    assert hot(client)["items"][0]["known_as"] == {
        "slug": "a-profile",
        "name": "A Profile",
        "kind": "exchange",
        "kind_words": "Exchange",
    }


def test_a_ranked_address_names_only_five_of_its_incidents(client, editor):
    for number in range(6):
        incident(client, editor, f"incident-{number}", [named(evm(1))], days_ago=number)

    (item,) = hot(client)["items"]

    assert item["reported_in"] == 6
    assert [entry["slug"] for entry in item["incidents"]] == [
        f"incident-{number}" for number in range(5)
    ]


def test_only_the_most_recently_published_incidents_are_read_for_a_ranking(
    client, editor, monkeypatch
):
    monkeypatch.setattr(public, "SCANNED_INCIDENTS", 2)
    incident(client, editor, "first-one", [named(evm(1))])
    incident(client, editor, "second-one", [named(evm(2))])
    incident(client, editor, "third-one", [named(evm(3))])

    assert [item["address"] for item in hot(client)["items"]] == [evm(2), evm(3)]


def test_a_taken_back_incident_is_no_longer_ranked_or_announced(client, editor):
    number = incident(client, editor, "a-theft", [named(evm(1))])
    assert hot(client)["total"] == 1 and feed(client)["total"] == 1

    incident_helpers.move(client, editor, number, "in_review")

    assert hot(client)["items"] == [] and feed(client)["items"] == []


def test_nothing_internal_reaches_the_ranking(client, editor):
    incident(client, editor, "a-theft", [named(evm(1))], internal_note="zq7-internal-only")

    answer = hot(client)

    text = json.dumps(answer)
    assert "zq7-internal-only" not in text and "editor@example.com" not in text
    assert not keys_anywhere(answer) & {"id", "internal_note", "created_by", "email", "user_id"}
    assert client.get(HOT).headers["cache-control"] == "public, max-age=60"
