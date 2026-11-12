"""Incidents for anybody: what is published is readable without signing in, and nothing else is.

The promises tested here are the ones a public page depends on: a draft is not found in the same
way as an incident that does not exist, every public answer holds exactly the fields that are
meant to be public, what an editor alone should see never reaches the public, and no record of any
organization can appear in an answer that anybody may read.
"""

from __future__ import annotations

import json

import pytest

from app.incidents.model import NOTHING_FOUND_NOTICE, PUBLIC_NOTICE
from tests.incident_helpers import (
    EVM,
    EVM_CHECKSUMMED,
    PUBLIC_ROUTES,
    body,
    create,
    make_editor,
    move,
    publish,
)
from tests.world import World

OTHER = "0x" + "cd" * 20
TX = "0x" + "ab" * 32

ITEM_FIELDS = {
    "slug",
    "title",
    "kind",
    "occurred_on",
    "published_at",
    "updated_at",
    "excerpt",
    "reported",
    "chains",
    "sources",
    "addresses",
}
DETAIL_FIELDS = {
    "slug",
    "title",
    "kind",
    "summary",
    "attribution_note",
    "occurred_on",
    "published_at",
    "updated_at",
    "reported_amounts",
    "sources",
    "addresses",
    "flows",
    "timeline",
    "notice",
}
FLOW_FIELDS = {"from", "to", "amount_text", "occurred_on", "tx_hash", "note", "source"}
SOURCE_FIELDS = {"ref", "publisher", "title", "url", "kind", "published_on"}


@pytest.fixture
def editor(client, db_session):
    return make_editor(client, db_session)


def words(response) -> dict:
    """What an error says (the request id differs from one request to the next)."""
    return {key: value for key, value in response.json()["error"].items() if key != "request_id"}


def keys_anywhere(value) -> set[str]:
    """Every key of every object inside a JSON value."""
    if isinstance(value, dict):
        found = set(value)
        for item in value.values():
            found |= keys_anywhere(item)
        return found
    if isinstance(value, list):
        return set().union(*(keys_anywhere(item) for item in value)) if value else set()
    return set()


# --- only what has been published is found ------------------------------------------------------


def test_nothing_is_shown_until_it_is_published_and_it_goes_when_it_is_taken_back(client, editor):
    made = create(client, editor)
    number = made["id"]
    unknown = client.get(f"{PUBLIC_ROUTES}/no-such-incident")
    assert unknown.status_code == 404

    def hidden() -> None:
        # the same answer as for an incident that does not exist, so it says nothing about drafts
        found = client.get(f"{PUBLIC_ROUTES}/a-theft")
        assert (found.status_code, words(found)) == (404, words(unknown))
        assert client.get(PUBLIC_ROUTES).json()["items"] == []
        named = client.get(f"/api/v1/public/addresses/ethereum/{EVM}/incidents").json()
        assert named["incidents"] == []

    hidden()  # a draft
    move(client, editor, number, "in_review")
    hidden()  # in review

    publish_state = move(client, editor, number, "published")
    assert publish_state["status"] == "published"
    assert client.get(f"{PUBLIC_ROUTES}/a-theft").status_code == 200
    assert [row["slug"] for row in client.get(PUBLIC_ROUTES).json()["items"]] == ["a-theft"]
    assert client.get(f"/api/v1/public/addresses/ethereum/{EVM}/incidents").json()["incidents"]

    move(client, editor, number, "in_review")
    hidden()  # taken back: gone at once
    move(client, editor, number, "published", "archived")
    hidden()  # archived


def test_an_incident_is_read_without_signing_in(client, editor):
    publish(client, editor, create(client, editor)["id"])

    listed = client.get(PUBLIC_ROUTES)
    one = client.get(f"{PUBLIC_ROUTES}/a-theft")

    assert listed.status_code == 200 and one.status_code == 200
    (item,) = listed.json()["items"]
    assert set(item) == ITEM_FIELDS
    assert item["reported"] == {"low": 5000000, "high": 6000000}
    assert item["chains"] == ["ethereum"] and item["sources"] == 1 and item["addresses"] == 1
    assert listed.json()["notice"] == PUBLIC_NOTICE and listed.json()["total"] == 1

    incident = one.json()["incident"]
    assert set(incident) == DETAIL_FIELDS
    assert incident["notice"] == PUBLIC_NOTICE
    assert set(incident["sources"][0]) == SOURCE_FIELDS | {"retrieved_on", "excerpt"}
    (amount,) = incident["reported_amounts"]
    assert set(amount) == {"label", "amount_usd_low", "amount_usd_high", "as_of", "source"}
    assert set(amount["source"]) == SOURCE_FIELDS
    (address,) = incident["addresses"]
    assert set(address) == {
        "chain",
        "address",
        "role",
        "role_words",
        "attribution_status",
        "status_words",
        "confidence",
        "source",
        "note",
        "last_verified_at",
        "profile",
    }
    assert address["profile"] is None  # no profile names it
    assert address["address"] == EVM and address["status_words"].startswith(
        "Reported by an analyst"
    )
    assert address["role_words"] == "Received funds reported as taken"
    (entry,) = incident["timeline"]
    assert set(entry) == {"occurred_at", "kind", "body", "source", "chain", "tx_hash"}
    assert incident["flows"] == []  # an incident without a trail says so


def two_addresses_and_a_trail(**movement):
    named = {
        "chain": "ethereum",
        "address": OTHER,
        "role": "INTERMEDIARY",
        "attribution_status": "COMMUNITY_REPORTED",
    }
    return {
        "addresses": [*body()["addresses"], named],
        "flows": [
            {
                "from_chain": "ethereum",
                "from_address": EVM,
                "to_chain": "ethereum",
                "to_address": OTHER,
                "evidence_ref": "e1",
                "tx_hash": TX,
                "amount_text": "about 4,500 ETH",
                "occurred_on": "2026-01-15",
                "note": "As the firm reported.",
                **movement,
            },
            {
                "from_chain": "ethereum",
                "from_address": OTHER,
                "to_chain": "ethereum",
                "to_address": EVM,
                "evidence_ref": "e1",
            },
        ],
    }


def test_the_funds_trail_is_read_with_its_incident_and_only_then(client, editor):
    made = create(client, editor, **two_addresses_and_a_trail())
    assert client.get(f"{PUBLIC_ROUTES}/a-theft").status_code == 404  # a draft shows nothing
    publish(client, editor, made["id"])

    incident = client.get(f"{PUBLIC_ROUTES}/a-theft").json()["incident"]

    first, second = incident["flows"]  # in the order the editor gave
    assert set(first) == FLOW_FIELDS and set(second) == FLOW_FIELDS
    assert first["from"] == {"chain": "ethereum", "address": EVM}
    assert first["to"] == {"chain": "ethereum", "address": OTHER}
    assert (first["amount_text"], first["occurred_on"], first["tx_hash"]) == (
        "about 4,500 ETH",
        "2026-01-15",
        TX,
    )
    assert first["note"] == "As the firm reported."
    assert set(first["source"]) == SOURCE_FIELDS and first["source"]["ref"] == "e1"
    assert (second["tx_hash"], second["amount_text"], second["note"]) == (None, None, None)
    assert set(set(first["from"]) | set(first["to"])) == {"chain", "address"}

    # taken back to review, the trail goes with the incident: it is not found in the same way
    move(client, editor, made["id"], "in_review")
    assert client.get(f"{PUBLIC_ROUTES}/a-theft").status_code == 404


def test_nothing_an_editor_alone_should_see_reaches_the_public(client, editor):
    publish(client, editor, create(client, editor, internal_note="zq7-internal-only")["id"])

    answers = [
        client.get(PUBLIC_ROUTES),
        client.get(f"{PUBLIC_ROUTES}/a-theft"),
        client.get(f"/api/v1/public/addresses/ethereum/{EVM}/incidents"),
    ]
    for response in answers:
        assert response.status_code == 200
        text = json.dumps(response.json())
        assert "zq7-internal-only" not in text and "editor@example.com" not in text
        found = keys_anywhere(response.json())
        assert not found & {"id", "internal_note", "created_by", "updated_by", "user_id", "email"}


def test_the_answers_may_be_cached_for_a_minute_and_errors_never_are(client, editor):
    publish(client, editor, create(client, editor)["id"])
    for path in (PUBLIC_ROUTES, f"{PUBLIC_ROUTES}/a-theft"):
        assert client.get(path).headers["cache-control"] == "public, max-age=60"
    for path in (
        f"{PUBLIC_ROUTES}/nothing-here",
        f"{PUBLIC_ROUTES}/Not A Slug",
        f"{PUBLIC_ROUTES}?limit=0",
    ):
        refused = client.get(path)
        assert refused.status_code in (404, 422) and refused.headers["cache-control"] == "no-store"
    assert client.get(PUBLIC_ROUTES).headers["x-content-type-options"] == "nosniff"


# --- listing --------------------------------------------------------------------------------------


def test_the_list_is_newest_first_and_filters_by_kind_and_chain_and_pages(client, editor):
    older = create(client, editor, slug="an-older-hack", kind="hack", occurred_on="2025-06-01")
    newer = create(client, editor, slug="a-newer-scam", kind="scam", occurred_on="2026-03-01")
    tron = create(
        client,
        editor,
        slug="a-tron-theft",
        occurred_on="2025-12-01",
        evidence=[],
        amounts=[],
        updates=[],
        addresses=[],
    )
    for made in (older, newer):
        publish(client, editor, made["id"])
    client.put(
        f"/api/v1/editor/incidents/{tron['id']}/content",
        json={
            **{key: value for key, value in tron["content"].items()},
            "evidence": [
                {
                    "ref": "e1",
                    "publisher": "A firm",
                    "title": "T",
                    "url": "https://example.org/t",
                    "kind": "news",
                    "retrieved_on": "2026-01-01",
                }
            ],
            "addresses": [
                {
                    "chain": "tron",
                    "address": "TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy",
                    "role": "INTERMEDIARY",
                    "attribution_status": "COMMUNITY_REPORTED",
                }
            ],
        },
        headers=editor,
    )
    publish(client, editor, tron["id"])

    everything = client.get(PUBLIC_ROUTES).json()
    assert [row["slug"] for row in everything["items"]] == [
        "a-newer-scam",
        "a-tron-theft",
        "an-older-hack",
    ]
    assert everything["total"] == 3
    assert [
        r["slug"] for r in client.get(PUBLIC_ROUTES, params={"kind": "hack"}).json()["items"]
    ] == ["an-older-hack"]
    on_tron = client.get(PUBLIC_ROUTES, params={"chain": "tron"}).json()
    assert [row["slug"] for row in on_tron["items"]] == ["a-tron-theft"] and on_tron["total"] == 1
    page = client.get(PUBLIC_ROUTES, params={"limit": 1, "offset": 1}).json()
    assert [row["slug"] for row in page["items"]] == ["a-tron-theft"] and page["total"] == 3

    for params in ({"kind": "prank"}, {"chain": "dogecoin"}, {"limit": 101}, {"offset": -1}):
        assert client.get(PUBLIC_ROUTES, params=params).status_code == 422


def test_amounts_are_shown_per_source_and_never_as_one_number(client, editor):
    second = {
        "ref": "e2",
        "publisher": "Another firm",
        "title": "Its own estimate",
        "url": "https://example.org/other",
        "kind": "analytics",
        "retrieved_on": "2026-01-17",
    }
    made = create(client, editor)
    document = made["content"]
    client.put(
        f"/api/v1/editor/incidents/{made['id']}/content",
        json={
            **document,
            "evidence": [*document["evidence"], second],
            "amounts": [
                *document["amounts"],
                {"evidence_ref": "e2", "amount_usd_low": 4900000, "label": "Another estimate"},
            ],
        },
        headers=editor,
    )
    publish(client, editor, made["id"])

    incident = client.get(f"{PUBLIC_ROUTES}/a-theft").json()["incident"]
    sources = [row["source"]["publisher"] for row in incident["reported_amounts"]]
    assert sources == ["An analytics firm", "Another firm"]
    listed = client.get(PUBLIC_ROUTES).json()["items"][0]
    assert listed["reported"] == {"low": 4900000, "high": 6000000}  # a range, never a total


# --- a page is found by its address, an address by what names it ---------------------------------


@pytest.mark.parametrize("slug", ["A-Theft", "a theft", "ab", "a--theft", "a_theft", "x" * 81])
def test_a_page_is_found_by_its_address_only(client, slug):
    assert client.get(f"{PUBLIC_ROUTES}/{slug}").status_code in (404, 422)


def test_an_address_finds_the_incidents_that_name_it_in_either_spelling(client, editor):
    publish(client, editor, create(client, editor)["id"])
    create(client, editor, slug="an-unpublished-one")

    for spelling in (EVM, EVM_CHECKSUMMED, EVM.upper().replace("0X", "0x")):
        found = client.get(f"/api/v1/public/addresses/ethereum/{spelling}/incidents")
        assert found.status_code == 200, spelling
        body = found.json()
        assert [row["slug"] for row in body["incidents"]] == ["a-theft"]
        assert body["address"] == EVM and body["notice"] == PUBLIC_NOTICE
        assert set(body["incidents"][0]) == {
            "slug",
            "title",
            "kind",
            "occurred_on",
            "role",
            "role_words",
            "attribution_status",
            "status_words",
        }

    other = client.get(f"/api/v1/public/addresses/ethereum/0x{'cd' * 20}/incidents").json()
    assert other["incidents"] == [] and other["notice"] == NOTHING_FOUND_NOTICE
    assert client.get("/api/v1/public/addresses/dogecoin/x/incidents").status_code == 404
    assert (
        client.get("/api/v1/public/addresses/ethereum/not-an-address/incidents").status_code == 422
    )
    assert (
        client.get(f"/api/v1/public/addresses/ethereum/{EVM}/incidents?limit=51").status_code == 422
    )


# --- the public and the organizations never meet -------------------------------------------------


def test_no_record_of_any_organization_appears_in_an_answer_anybody_may_read(
    client, db_session, settings
):
    world = World(client, db_session, settings)
    for team in (world.acme, world.rival):
        world.monitor(team)
        world.case(team)
        world.screening(team)
        world.alert(team)
    published = world.incident(publish=True)
    slug = world.slugs[published]
    asked = [
        PUBLIC_ROUTES,
        f"{PUBLIC_ROUTES}/{slug}",
        "/api/v1/public/addresses/ethereum/" + EVM + "/incidents",
    ]

    for path in asked:
        text = client.get(path).text.lower()
        for team in ("Acme", "Rival"):
            leaked = [mark for mark in world.marks[team] if mark.lower() in text]
            assert not leaked, (path, team, leaked)
