"""Wallet profiles for anybody: what is published is readable without signing in, and nothing else.

The promises tested here are the ones a public page depends on: a draft is not found in the same
way as a profile that does not exist, every public answer holds exactly the fields that are meant to
be public, what an editor alone should see never reaches the public, the name search takes what a
visitor types as letters, and the profiles and the incidents point at each other only through what
is published.
"""

from __future__ import annotations

import json

import pytest

from app.wallets import public
from app.wallets.model import (
    NOTHING_FOUND_NOTICE,
    PUBLIC_NOTICE,
    STATUS_WORDS,
    AttributionStatus,
)
from tests import incident_helpers
from tests.wallet_helpers import (
    DOCUMENT,
    EVM,
    OTHER,
    PUBLIC_ROUTES,
    TRON,
    create,
    make_editor,
    move,
    publish,
)

ITEM_FIELDS = {
    "slug",
    "name",
    "kind",
    "kind_words",
    "website",
    "published_at",
    "updated_at",
    "excerpt",
    "chains",
    "sources",
    "addresses",
}
DETAIL_FIELDS = {
    "slug",
    "name",
    "kind",
    "kind_words",
    "summary",
    "website",
    "attribution_note",
    "published_at",
    "updated_at",
    "sources",
    "addresses",
    "incidents",
    "notice",
}
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
        found = set()
        for item in value:
            found |= keys_anywhere(item)
        return found
    return set()


# --- what is published, and only that -----------------------------------------------------------


def test_a_profile_is_read_without_signing_in(client, editor):
    publish(client, editor, create(client, editor)["id"])

    listed = client.get(PUBLIC_ROUTES)
    one = client.get(f"{PUBLIC_ROUTES}/example-exchange")

    assert listed.status_code == 200 and one.status_code == 200
    (item,) = listed.json()["items"]
    assert set(item) == ITEM_FIELDS
    assert (item["name"], item["kind"], item["kind_words"]) == (
        "Example Exchange",
        "exchange",
        "Exchange",
    )
    assert item["chains"] == ["ethereum"] and item["sources"] == 1 and item["addresses"] == 1
    assert listed.json()["notice"] == PUBLIC_NOTICE and listed.json()["total"] == 1

    profile = one.json()["wallet"]
    assert set(profile) == DETAIL_FIELDS
    assert profile["notice"] == PUBLIC_NOTICE
    assert profile["website"] == "https://example.org"
    assert set(profile["sources"][0]) == SOURCE_FIELDS | {"retrieved_on", "excerpt"}
    (address,) = profile["addresses"]
    assert set(address) == {
        "chain",
        "address",
        "label",
        "attribution_status",
        "status_words",
        "confidence",
        "source",
        "note",
        "last_verified_at",
    }
    assert address["address"] == EVM and address["label"] == "Hot wallet"
    assert address["status_words"].startswith("Confirmed by the affected party")
    assert set(address["source"]) == SOURCE_FIELDS
    assert profile["incidents"] == []


def test_a_draft_a_profile_in_review_and_an_archived_one_are_not_found(client, editor):
    made = create(client, editor)
    gone = client.get(f"{PUBLIC_ROUTES}/nothing-here")
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "wallet_not_found"

    seen = []
    for states in ((), ("in_review",), ("published",), ("archived",)):
        if states:
            move(client, editor, made["id"], *states)
        shown = client.get(f"{PUBLIC_ROUTES}/example-exchange")
        seen.append((shown.status_code, len(client.get(PUBLIC_ROUTES).json()["items"])))

    # draft, in review: not found; published: found; archived: not found, in the same words
    assert seen == [(404, 0), (404, 0), (200, 1), (404, 0)]
    assert words(client.get(f"{PUBLIC_ROUTES}/example-exchange")) == words(gone)


def test_nothing_an_editor_alone_should_see_reaches_the_public(client, editor):
    publish(client, editor, create(client, editor, internal_note="zq7-internal-only")["id"])

    answers = [
        client.get(PUBLIC_ROUTES),
        client.get(f"{PUBLIC_ROUTES}/example-exchange"),
        client.get(f"/api/v1/public/addresses/ethereum/{EVM}/wallets"),
    ]
    for response in answers:
        assert response.status_code == 200
        text = json.dumps(response.json())
        assert "zq7-internal-only" not in text and "editor@example.com" not in text
        found = keys_anywhere(response.json())
        assert not found & {"id", "internal_note", "created_by", "updated_by", "user_id", "email"}


def test_the_answers_may_be_cached_for_a_minute_and_errors_never_are(client, editor):
    publish(client, editor, create(client, editor)["id"])
    for path in (
        PUBLIC_ROUTES,
        f"{PUBLIC_ROUTES}/example-exchange",
        f"/api/v1/public/addresses/ethereum/{EVM}/wallets",
    ):
        assert client.get(path).headers["cache-control"] == "public, max-age=60"
    for path in (
        f"{PUBLIC_ROUTES}/nothing-here",
        f"{PUBLIC_ROUTES}/Not_A_Slug",
        f"{PUBLIC_ROUTES}?chain=dogecoin",
    ):
        response = client.get(path)
        assert response.status_code >= 400
        assert "public" not in response.headers.get("cache-control", "")


# --- the list -----------------------------------------------------------------------------------


def test_the_list_is_by_name_and_filters_by_kind_and_chain(client, editor):
    for slug, name, kind, chain, address in (
        ("zeta-bridge", "Zeta Bridge", "bridge", "ethereum", OTHER),
        ("alpha-exchange", "alpha exchange", "exchange", "ethereum", EVM),
        ("tron-exchange", "Tron Exchange", "exchange", "tron", TRON),
    ):
        made = create(
            client,
            editor,
            slug=slug,
            name=name,
            kind=kind,
            addresses=[{**DOCUMENT["addresses"][0], "chain": chain, "address": address}],
        )
        publish(client, editor, made["id"])

    everyone = client.get(PUBLIC_ROUTES).json()
    assert [i["name"] for i in everyone["items"]] == [
        "alpha exchange",
        "Tron Exchange",
        "Zeta Bridge",
    ]
    by_kind = client.get(PUBLIC_ROUTES, params={"kind": "exchange"}).json()
    assert [i["slug"] for i in by_kind["items"]] == ["alpha-exchange", "tron-exchange"]
    by_chain = client.get(PUBLIC_ROUTES, params={"chain": "tron"}).json()
    assert [i["slug"] for i in by_chain["items"]] == ["tron-exchange"]
    paged = client.get(PUBLIC_ROUTES, params={"limit": 1, "offset": 1}).json()
    assert paged["total"] == 3 and [i["slug"] for i in paged["items"]] == ["tron-exchange"]


def test_the_name_search_takes_what_is_typed_as_letters(client, editor):
    for slug, name in (("first-one", "100% Exchange"), ("second-one", "Plain Exchange")):
        publish(client, editor, create(client, editor, slug=slug, name=name)["id"])

    def found(q):
        return [i["slug"] for i in client.get(PUBLIC_ROUTES, params={"q": q}).json()["items"]]

    assert found("exchange") == ["first-one", "second-one"]  # any case, anywhere in the name
    assert found("PLAIN") == ["second-one"]
    assert found("100%") == ["first-one"]  # a percent sign is a percent sign, not "anything"
    assert found("%") == ["first-one"]
    assert found("_") == []  # and an underscore is not "any one letter"
    assert found("nothing like it") == []
    assert found("   ") == ["first-one", "second-one"]  # nothing typed is no filter


@pytest.mark.parametrize(
    "query", [{"kind": "person"}, {"limit": 0}, {"limit": 101}, {"offset": -1}, {"q": "x" * 101}]
)
def test_a_list_query_that_makes_no_sense_is_refused(client, query):
    assert client.get(PUBLIC_ROUTES, params=query).status_code == 422


# --- an address ---------------------------------------------------------------------------------


def test_an_address_says_which_published_profiles_name_it(client, editor):
    publish(client, editor, create(client, editor)["id"])

    named = client.get(f"/api/v1/public/addresses/ethereum/{EVM}/wallets")
    spelled = client.get(
        f"/api/v1/public/addresses/ethereum/{EVM.upper().replace('0X', '0x')}/wallets"
    )

    assert named.status_code == 200 and named.json() == spelled.json()  # any spelling of it
    answer = named.json()
    assert (answer["chain"], answer["address"], answer["notice"]) == (
        "ethereum",
        EVM,
        PUBLIC_NOTICE,
    )
    (hit,) = answer["wallets"]
    assert hit == {
        "slug": "example-exchange",
        "name": "Example Exchange",
        "kind": "exchange",
        "kind_words": "Exchange",
        "label": "Hot wallet",
        "attribution_status": "OFFICIALLY_CONFIRMED",
        "status_words": hit["status_words"],
    }


def test_an_address_no_profile_names_is_not_thereby_unknown(client, editor):
    publish(client, editor, create(client, editor)["id"])

    answer = client.get(f"/api/v1/public/addresses/ethereum/{OTHER}/wallets").json()

    assert answer["wallets"] == [] and answer["notice"] == NOTHING_FOUND_NOTICE


def test_an_address_on_the_wrong_chain_or_no_chain_is_refused(client):
    wrong = client.get("/api/v1/public/addresses/ethereum/not-an-address/wallets")
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "invalid_address"
    unknown = client.get(f"/api/v1/public/addresses/dogecoin/{EVM}/wallets")
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "unknown_chain"


# --- the profiles and the incidents point at each other -----------------------------------------


def incident_naming(client, editor, address, **changes):
    """A published incident whose attacker address is `address` (written by the same editor)."""
    document = incident_helpers.body(
        addresses=[{**incident_helpers.DOCUMENT["addresses"][0], "address": address}], **changes
    )
    made = client.post(
        incident_helpers.EDITOR_ROUTES,
        json={"slug": "a-theft", **document},
        headers=editor,
    ).json()["incident"]
    incident_helpers.publish(client, editor, made["id"])
    return made


def test_a_published_incident_says_which_profile_names_the_address_it_names(client, editor):
    incident_naming(client, editor, EVM)
    before = client.get(f"{incident_helpers.PUBLIC_ROUTES}/a-theft").json()["incident"]
    assert before["addresses"][0]["profile"] is None  # nobody has said who it is

    made = create(client, editor)
    after_draft = client.get(f"{incident_helpers.PUBLIC_ROUTES}/a-theft").json()["incident"]
    assert after_draft["addresses"][0]["profile"] is None  # a draft says nothing

    publish(client, editor, made["id"])
    linked = client.get(f"{incident_helpers.PUBLIC_ROUTES}/a-theft").json()["incident"]
    assert linked["addresses"][0]["profile"] == {
        "slug": "example-exchange",
        "name": "Example Exchange",
        "kind": "exchange",
        "kind_words": "Exchange",
    }

    move(client, editor, made["id"], "in_review")
    gone = client.get(f"{incident_helpers.PUBLIC_ROUTES}/a-theft").json()["incident"]
    assert gone["addresses"][0]["profile"] is None  # taken back: the link goes with it


def test_a_published_profile_lists_the_published_incidents_that_name_its_addresses(client, editor):
    publish(client, editor, create(client, editor)["id"])
    incident_naming(client, editor, EVM)

    profile = client.get(f"{PUBLIC_ROUTES}/example-exchange").json()["wallet"]

    (mention,) = profile["incidents"]
    assert set(mention) == {"slug", "title", "kind", "occurred_on", "addresses"}
    assert (mention["slug"], mention["kind"]) == ("a-theft", "theft")
    (named,) = mention["addresses"]
    assert set(named) == {"chain", "address", "role_words", "status_words"}
    assert named["address"] == EVM and named["role_words"] == "Received funds reported as taken"

    # an incident that is not published is not listed
    incident_helpers.move(client, editor, 1, "in_review")
    assert client.get(f"{PUBLIC_ROUTES}/example-exchange").json()["wallet"]["incidents"] == []


# --- what the first mutation run found missing --------------------------------------------------


def evm(number: int) -> str:
    return "0x" + f"{number:02x}" * 20


def incident_on(client, editor, slug, chain, addresses, **changes):
    """A published incident that names these addresses on one chain."""
    named = [
        {**incident_helpers.DOCUMENT["addresses"][0], "chain": chain, "address": where}
        for where in addresses
    ]
    document = incident_helpers.body(addresses=named, **changes)
    made = client.post(
        incident_helpers.EDITOR_ROUTES, json={"slug": slug, **document}, headers=editor
    ).json()["incident"]
    incident_helpers.publish(client, editor, made["id"])
    return made


def test_a_list_item_counts_what_it_says_it_counts(client, editor):
    documents = [
        {**DOCUMENT["evidence"][0], "ref": "e1", "publisher": "The exchange itself"},
        {
            **DOCUMENT["evidence"][0],
            "ref": "e2",
            "publisher": "The exchange itself",
            "title": "More",
        },
        {
            **DOCUMENT["evidence"][0],
            "ref": "e3",
            "publisher": "An analytics firm",
            "title": "Report",
        },
    ]
    named = [
        {**DOCUMENT["addresses"][0], "chain": "ethereum", "address": EVM},
        {**DOCUMENT["addresses"][0], "chain": "ethereum", "address": OTHER},
        {**DOCUMENT["addresses"][0], "chain": "bsc", "address": EVM},
    ]
    publish(client, editor, create(client, editor, evidence=documents, addresses=named)["id"])

    (item,) = client.get(PUBLIC_ROUTES).json()["items"]

    assert item["addresses"] == 3
    assert item["sources"] == 2  # two publishers, three documents
    assert item["chains"] == ["bsc", "ethereum"]  # each chain once, by name


def test_the_documents_and_the_addresses_of_a_profile_are_in_the_order_they_were_written(
    client, editor
):
    documents = [
        {**DOCUMENT["evidence"][0], "ref": ref, "title": f"Document {ref}"}
        for ref in ("e1", "e2", "e3")
    ]
    named = [
        {**DOCUMENT["addresses"][0], "address": where, "label": f"Wallet {number}"}
        for number, where in enumerate((EVM, OTHER, evm(7)), 1)
    ]
    publish(client, editor, create(client, editor, evidence=documents, addresses=named)["id"])

    profile = client.get(f"{PUBLIC_ROUTES}/example-exchange").json()["wallet"]

    assert [item["ref"] for item in profile["sources"]] == ["e1", "e2", "e3"]
    assert [item["label"] for item in profile["addresses"]] == ["Wallet 1", "Wallet 2", "Wallet 3"]


def test_everything_an_address_and_a_document_say_is_in_the_public_answer(client, editor):
    document = {
        **DOCUMENT["evidence"][0],
        "excerpt": "A short quotation.",
        "published_on": "2026-01-10",
        "retrieved_on": "2026-01-16",
    }
    named = {
        **DOCUMENT["addresses"][0],
        "confidence": 77,
        "note": "Listed on the page.",
        "last_verified_at": "2026-01-17T09:30:00Z",
        "attribution_status": "CORROBORATED",
    }
    publish(client, editor, create(client, editor, evidence=[document], addresses=[named])["id"])

    profile = client.get(f"{PUBLIC_ROUTES}/example-exchange").json()["wallet"]

    (address,) = profile["addresses"]
    assert address["confidence"] == 77
    assert address["note"] == "Listed on the page."
    assert address["last_verified_at"].startswith("2026-01-17T09:30")
    assert address["attribution_status"] == "CORROBORATED"
    assert address["status_words"] == STATUS_WORDS[AttributionStatus.CORROBORATED]
    (source,) = profile["sources"]
    assert source["excerpt"] == "A short quotation."
    assert source["retrieved_on"] == "2026-01-16" and source["published_on"] == "2026-01-10"
    assert source["url"] == "https://example.org/proof-of-reserves" and source["kind"] == "official"
    assert profile["attribution_note"] == "Addresses as the exchange publishes them."
    assert profile["website"] == "https://example.org"


def test_an_excerpt_is_cut_at_a_word_and_says_so():
    limit = public.EXCERPT_LENGTH
    exact = "x" * limit
    assert public._excerpt(exact) == exact  # at the limit nothing is cut
    assert public._excerpt("a short one.") == "a short one."

    # cut at the limit, not in the middle of a word, with no mark left before the dots
    long = "a" * (limit - 3) + ", bbbb cccc"
    assert public._excerpt(long) == "a" * (limit - 3) + "..."

    cut = public._excerpt(" ".join(["word"] * 200))
    assert cut.endswith("word...") and len(cut) <= limit + 3


def test_a_profile_lists_an_incident_only_for_the_chain_it_names_the_address_on(client, editor):
    publish(client, editor, create(client, editor)["id"])  # names EVM on ethereum
    incident_on(client, editor, "on-another-chain", "bsc", [EVM])

    assert client.get(f"{PUBLIC_ROUTES}/example-exchange").json()["wallet"]["incidents"] == []

    incident_on(client, editor, "on-the-same-chain", "ethereum", [EVM])
    (mention,) = client.get(f"{PUBLIC_ROUTES}/example-exchange").json()["wallet"]["incidents"]
    assert mention["slug"] == "on-the-same-chain"


def test_the_incidents_of_a_profile_are_newest_first_and_each_shows_a_few_addresses(client, editor):
    many = [evm(number) for number in range(1, 12)]  # one more than an incident shows
    named = [
        {**DOCUMENT["addresses"][0], "address": where, "label": f"Wallet {number}"}
        for number, where in enumerate(many, 1)
    ]
    publish(client, editor, create(client, editor, addresses=named)["id"])
    incident_on(client, editor, "older", "ethereum", many, occurred_on="2026-01-05")
    incident_on(client, editor, "newer", "ethereum", many[:1], occurred_on="2026-02-05")

    mentions = client.get(f"{PUBLIC_ROUTES}/example-exchange").json()["wallet"]["incidents"]

    assert [mention["slug"] for mention in mentions] == ["newer", "older"]
    assert public.ADDRESSES_PER_INCIDENT == 10
    assert len(mentions[1]["addresses"]) == public.ADDRESSES_PER_INCIDENT


def test_a_profile_lists_at_most_so_many_incidents(client, editor, db_session):
    publish(client, editor, create(client, editor)["id"])
    for number in range(3):
        incident_on(
            client,
            editor,
            f"theft-{number}",
            "ethereum",
            [EVM],
            occurred_on=f"2026-01-0{number + 1}",
        )

    db_session.rollback()
    pairs = [("eip155:1", EVM)]
    shown = public.incident_mentions(db_session, pairs, limit=2)
    assert [mention["slug"] for mention in shown] == ["theft-2", "theft-1"]  # the two newest
    assert len(public.incident_mentions(db_session, pairs, limit=3)) == 3
    assert public.INCIDENTS_SHOWN == 20


def test_an_address_lookup_finds_only_published_profiles_on_the_chain_asked(client, editor):
    made = create(client, editor)
    url = f"/api/v1/public/addresses/ethereum/{EVM}/wallets"
    seen = []
    for states in ((), ("in_review",), ("published",), ("archived",)):
        if states:
            move(client, editor, made["id"], *states)
        seen.append(len(client.get(url).json()["wallets"]))
    assert seen == [0, 0, 1, 0]  # a draft, a profile in review and an archived one name nobody

    move(client, editor, made["id"], "in_review", "published")
    on_another = client.get(f"/api/v1/public/addresses/bsc/{EVM}/wallets").json()
    assert on_another["wallets"] == []  # the same key on another chain is another address


def test_an_address_lookup_has_a_limit_and_an_incident_links_the_first_profile(client, editor):
    for slug in ("first-profile", "second-profile"):
        name = slug.replace("-", " ").title()
        publish(client, editor, create(client, editor, slug=slug, name=name)["id"])
    url = f"/api/v1/public/addresses/ethereum/{EVM}/wallets"

    both = client.get(url).json()["wallets"]
    assert [item["slug"] for item in both] == ["first-profile", "second-profile"]
    assert [item["slug"] for item in client.get(url, params={"limit": 1}).json()["wallets"]] == [
        "first-profile"
    ]

    incident_on(client, editor, "a-theft", "ethereum", [EVM])
    linked = client.get(f"{incident_helpers.PUBLIC_ROUTES}/a-theft").json()["incident"]
    assert linked["addresses"][0]["profile"]["slug"] == "first-profile"


@pytest.mark.parametrize("name", ["Not_A_Slug", "UPPER", "a--b", "-start", "end-", "ab", "x" * 81])
def test_a_name_that_cannot_be_a_profiles_is_refused_before_anything_is_looked_up(client, name):
    assert client.get(f"{PUBLIC_ROUTES}/{name}").status_code == 422
    assert client.get(f"{PUBLIC_ROUTES}/no-such-profile").status_code == 404


def test_each_address_of_an_incident_links_to_the_profile_that_names_it(client, editor):
    publish(
        client, editor, create(client, editor, slug="first-profile", name="First Profile")["id"]
    )
    other = [{**DOCUMENT["addresses"][0], "address": OTHER}]
    second = create(client, editor, slug="second-profile", name="Second Profile", addresses=other)
    publish(client, editor, second["id"])
    incident_on(client, editor, "a-theft", "ethereum", [EVM, OTHER, evm(9)])

    found = client.get(f"{incident_helpers.PUBLIC_ROUTES}/a-theft").json()["incident"]["addresses"]

    linked = {item["address"]: (item["profile"] or {}).get("slug") for item in found}
    assert linked == {EVM: "first-profile", OTHER: "second-profile", evm(9): None}
