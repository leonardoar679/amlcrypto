"""The editor's side of wallet profiles: who may write them, and what writing them does.

A profile is global: it belongs to the platform, so only a platform editor may write it (a person
with `users.is_platform_editor`, never a role and never an API key), and what an editor does is
audited for the platform, not for the editor's organization.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.access.permissions import Role
from app.database.models import AuditLog, WalletProfile
from app.wallets.model import KIND_WORDS, STATUS_WORDS
from tests.access_helpers import person
from tests.wallet_helpers import DOCUMENT, EVM, OTHER, body, make_editor
from tests.wallet_helpers import EDITOR_ROUTES as V1


@pytest.fixture
def staff(client, db_session):
    """An editor, an administrator who is not one, an analyst and a viewer, in one organization."""
    editor = make_editor(client, db_session)
    _, admin = person(client, db_session, Role.ADMIN, email="admin@example.com")
    _, analyst = person(client, db_session, Role.ANALYST)
    _, viewer = person(client, db_session, Role.VIEWER)
    return {"editor": editor, "admin": admin, "analyst": analyst, "viewer": viewer}


def make(client, staff, slug="example-exchange", **changes):
    response = client.post(V1, json={"slug": slug, **body(**changes)}, headers=staff["editor"])
    assert response.status_code == 201, response.text
    return response.json()["wallet"]


# --- an editor writes a profile from start to finish --------------------------------------------


def test_an_editor_writes_publishes_changes_and_archives_a_profile(client, staff):
    created = make(client, staff)
    assert created["status"] == "draft" and created["slug"] == "example-exchange"
    assert created["allowed_transitions"] == ["in_review"] and created["editable"] is True
    assert created["content"]["internal_note"] == "Re-check the cold wallet next quarter."
    assert created["content"]["addresses"][0]["address"] == EVM
    assert created["publish_problems"] == []
    assert created["words"]["kinds"]["exchange"] == "Exchange"
    number = created["id"]

    again = client.get(f"{V1}/{number}", headers=staff["editor"]).json()["wallet"]
    assert again["content"] == created["content"]  # what is read is what can be sent back
    resent = client.put(f"{V1}/{number}/content", json=created["content"], headers=staff["editor"])
    assert resent.status_code == 200 and resent.json()["wallet"]["content"] == created["content"]

    replaced = client.put(
        f"{V1}/{number}/content", json=body(name="Example Exchange Ltd"), headers=staff["editor"]
    )
    assert (
        replaced.status_code == 200 and replaced.json()["wallet"]["name"] == "Example Exchange Ltd"
    )

    for state in ("in_review", "published"):
        moved = client.post(
            f"{V1}/{number}/transition", json={"to": state}, headers=staff["editor"]
        )
        assert moved.status_code == 200 and moved.json()["wallet"]["status"] == state
    published = client.get(f"{V1}/{number}", headers=staff["editor"]).json()["wallet"]
    assert published["editable"] is False and published["published_at"] is not None

    # a published profile is changed by taking it back to review first
    refused = client.put(f"{V1}/{number}/content", json=body(), headers=staff["editor"])
    assert refused.status_code == 409 and "back to review" in refused.json()["error"]["message"]

    for state in ("archived",):
        moved = client.post(
            f"{V1}/{number}/transition", json={"to": state}, headers=staff["editor"]
        )
        assert moved.status_code == 200


def test_a_draft_is_deleted_and_a_published_one_never_is(client, staff):
    draft = make(client, staff, slug="a-draft")
    assert client.delete(f"{V1}/{draft['id']}", headers=staff["editor"]).status_code == 204
    assert client.get(f"{V1}/{draft['id']}", headers=staff["editor"]).status_code == 404

    shown = make(client, staff, slug="shown")
    for state in ("in_review", "published"):
        client.post(f"{V1}/{shown['id']}/transition", json={"to": state}, headers=staff["editor"])
    refused = client.delete(f"{V1}/{shown['id']}", headers=staff["editor"])
    assert refused.status_code == 409


def test_the_list_pages_and_filters_by_state(client, staff):
    for number in range(3):
        make(client, staff, slug=f"profile-{number}")
    first = make(client, staff, slug="profile-3")
    client.post(f"{V1}/{first['id']}/transition", json={"to": "in_review"}, headers=staff["editor"])

    everything = client.get(V1, params={"limit": 2}, headers=staff["editor"]).json()
    assert everything["total"] == 4 and len(everything["items"]) == 2
    item = everything["items"][0]
    assert {"id", "slug", "name", "kind", "status", "evidence", "addresses"} <= set(item)
    assert "summary" not in item and "internal_note" not in item  # a list shows no content

    reviewing = client.get(V1, params={"status": "in_review"}, headers=staff["editor"]).json()
    assert [row["slug"] for row in reviewing["items"]] == ["profile-3"]


# --- a profile that is not whole is refused -----------------------------------------------------


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"addresses": [{**DOCUMENT["addresses"][0], "address": "not an address"}]}, "not a valid"),
        (
            {"addresses": [{**DOCUMENT["addresses"][0], "evidence_ref": "e9"}]},
            "not in this profile",
        ),
        ({"website": "javascript:alert(1)"}, "web address"),
        ({"name": "Exam‮ple"}, "direction-changing"),
        ({"evidence": [{**DOCUMENT["evidence"][0], "url": "ftp://example.org"}]}, "web address"),
    ],
)
def test_a_bad_profile_is_refused_with_a_reason_and_nothing_is_kept(
    client, staff, db_session, change, message
):
    response = client.post(
        V1, json={"slug": "a-bad-one", **body(**change)}, headers=staff["editor"]
    )

    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_wallet"
    assert message in response.json()["error"]["message"]
    assert db_session.scalars(select(WalletProfile)).all() == []


def test_the_address_of_a_page_is_taken_once(client, staff):
    make(client, staff)
    again = client.post(V1, json={"slug": "example-exchange", **body()}, headers=staff["editor"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "wallet_conflict"


def test_publishing_says_what_stands_in_the_way(client, staff):
    bare = make(client, staff, evidence=[], addresses=[])
    client.post(f"{V1}/{bare['id']}/transition", json={"to": "in_review"}, headers=staff["editor"])

    response = client.post(
        f"{V1}/{bare['id']}/transition", json={"to": "published"}, headers=staff["editor"]
    )

    assert response.status_code == 422
    message = response.json()["error"]["message"]
    assert "cannot be published yet" in message and "no document" in message
    assert "names no address" in message


def test_the_wallet_of_a_public_figure_needs_the_owner_to_have_confirmed_it(client, staff):
    figure = make(
        client,
        staff,
        slug="a-public-figure",
        kind="public_figure",
        website=None,
        addresses=[{**DOCUMENT["addresses"][0], "attribution_status": "REPORTED_BY_ANALYST"}],
    )
    client.post(
        f"{V1}/{figure['id']}/transition", json={"to": "in_review"}, headers=staff["editor"]
    )

    response = client.post(
        f"{V1}/{figure['id']}/transition", json={"to": "published"}, headers=staff["editor"]
    )

    assert response.status_code == 422
    assert "public figure" in response.json()["error"]["message"]


def test_a_state_cannot_be_skipped(client, staff):
    made = make(client, staff)
    response = client.post(
        f"{V1}/{made['id']}/transition", json={"to": "published"}, headers=staff["editor"]
    )
    assert response.status_code == 409 and "can only go to" in response.json()["error"]["message"]


def test_what_is_not_there_is_not_found(client, staff):
    for method, path in (
        ("get", f"{V1}/999999"),
        ("delete", f"{V1}/999999"),
        ("put", f"{V1}/999999/content"),
        ("post", f"{V1}/999999/transition"),
    ):
        kwargs = {"json": body()} if method == "put" else {}
        if method == "post":
            kwargs = {"json": {"to": "in_review"}}
        response = getattr(client, method)(path, headers=staff["editor"], **kwargs)
        assert response.status_code == 404, (method, path)


def test_a_body_may_carry_only_what_it_offers(client, staff):
    made = make(client, staff)
    for path, method, payload in (
        (V1, "post", {"slug": "extra-one", **body(), "status": "published"}),
        (f"{V1}/{made['id']}/content", "put", {**body(), "created_by": 1}),
        (f"{V1}/{made['id']}/transition", "post", {"to": "in_review", "force": True}),
        (
            V1,
            "post",
            {"slug": "extra-two", **body(addresses=[{**DOCUMENT["addresses"][0], "x": 1}])},
        ),
    ):
        response = getattr(client, method)(path, json=payload, headers=staff["editor"])
        assert response.status_code == 422, (path, response.text)


def test_a_profile_is_bounded(client, staff):
    too_many = [{**DOCUMENT["addresses"][0], "address": "0x" + f"{n:040x}"} for n in range(301)]
    response = client.post(
        V1, json={"slug": "too-many", **body(addresses=too_many)}, headers=staff["editor"]
    )
    assert response.status_code == 422


# --- who may ------------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["admin", "analyst", "viewer"])
def test_only_a_platform_editor_may_write_or_read_the_editor_routes(client, staff, who):
    made = make(client, staff)
    headers = staff[who]
    for method, path, payload in (
        ("get", V1, None),
        ("post", V1, {"slug": "nope-nope", **body()}),
        ("get", f"{V1}/{made['id']}", None),
        ("put", f"{V1}/{made['id']}/content", body()),
        ("post", f"{V1}/{made['id']}/transition", {"to": "in_review"}),
        ("delete", f"{V1}/{made['id']}", None),
    ):
        kwargs = {} if payload is None else {"json": payload}
        response = getattr(client, method)(path, headers=headers, **kwargs)
        assert response.status_code == 403, (who, method, path, response.status_code)


def test_nobody_gets_in_without_signing_in(client, staff):
    assert client.get(V1).status_code == 401
    assert client.post(V1, json={"slug": "nope-nope", **body()}).status_code == 401


# --- the audit log ------------------------------------------------------------------------------


def test_every_change_is_audited_for_the_platform_and_no_organization_reads_it(
    client, staff, db_session
):
    made = make(client, staff)
    number = made["id"]
    client.put(f"{V1}/{number}/content", json=body(name="Reworded"), headers=staff["editor"])
    client.post(f"{V1}/{number}/transition", json={"to": "in_review"}, headers=staff["editor"])
    client.delete(f"{V1}/{make(client, staff, slug='to-delete')['id']}", headers=staff["editor"])

    db_session.rollback()
    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action.like("wallet.%")).order_by(AuditLog.id)
    ).all()
    assert [row.action for row in rows] == [
        "wallet.create",
        "wallet.content",
        "wallet.status",
        "wallet.create",
        "wallet.delete",
    ]
    assert {row.organization_id for row in rows} == {None}
    assert {(row.actor_type, row.actor_label) for row in rows} == {("user", "editor@example.com")}
    assert rows[2].details == {
        "slug": "example-exchange",
        "from": "draft",
        "to": "in_review",
        "labels_in_force": 0,
    }
    assert rows[1].details["addresses"] == 1 and rows[0].details["name"] == "Example Exchange"

    # the editor's own organization sees none of it in its log
    listed = client.get("/api/v1/audit", params={"limit": 200}, headers=staff["admin"]).json()
    assert not [row for row in listed["items"] if row["action"].startswith("wallet.")]
    db_session.rollback()


def test_the_audit_entry_of_a_publication_says_how_many_labels_it_put_in_force(
    client, staff, db_session
):
    made = make(client, staff)
    for state in ("in_review", "published", "in_review"):
        client.post(f"{V1}/{made['id']}/transition", json={"to": state}, headers=staff["editor"])

    db_session.rollback()
    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "wallet.status").order_by(AuditLog.id)
    ).all()
    assert [(r.details["to"], r.details["labels_in_force"]) for r in rows] == [
        ("in_review", 0),
        ("published", 1),  # an exchange's confirmed address is context for screening
        ("in_review", 0),
    ]
    db_session.rollback()
    assert OTHER  # (the second address of the other tests)


# --- what the first mutation run found missing --------------------------------------------------


def test_what_an_editor_sends_is_what_the_editor_reads_back(client, staff):
    sent = body(
        attribution_note="How the addresses were attributed.",
        evidence=[{**DOCUMENT["evidence"][0], "excerpt": "A short quotation."}],
        addresses=[
            {
                **DOCUMENT["addresses"][0],
                "attribution_status": "CORROBORATED",
                "confidence": 77,
                "note": "Listed on the page.",
                "last_verified_at": "2026-01-17T09:30:00Z",
            }
        ],
    )

    content = make(client, staff, **sent)["content"]

    for part in ("name", "kind", "summary", "website", "attribution_note", "internal_note"):
        assert content[part] == sent[part], part
    (document,) = content["evidence"]
    for part, value in sent["evidence"][0].items():
        assert document[part] == value, part
    (address,) = content["addresses"]
    for part, value in sent["addresses"][0].items():
        if part == "last_verified_at":
            assert address[part].startswith("2026-01-17T09:30")
        else:
            assert address[part] == value, part


def test_the_audit_entry_of_a_deletion_says_what_was_deleted(client, staff, db_session):
    number = make(client, staff, slug="to-delete")["id"]
    assert client.delete(f"{V1}/{number}", headers=staff["editor"]).status_code == 204

    db_session.rollback()
    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "wallet.delete")).one()
    assert row.details == {"slug": "to-delete", "name": "Example Exchange"}
    db_session.rollback()


def test_the_editor_reads_who_wrote_it_and_how_much_it_holds(client, staff):
    documents = [
        {**DOCUMENT["evidence"][0], "ref": f"e{number}", "title": f"Document {number}"}
        for number in (1, 2)
    ]
    named = [
        {**DOCUMENT["addresses"][0], "address": where, "label": f"Wallet {number}"}
        for number, where in enumerate((EVM, OTHER, "0x" + "ab" * 20), 1)
    ]
    made = make(client, staff, evidence=documents, addresses=named)

    assert made["evidence"] == 2 and made["addresses"] == 3
    for who in (made["created_by"], made["updated_by"]):
        assert who["email"] == "editor@example.com" and isinstance(who["id"], int)
    assert made["created_by"] == made["updated_by"]
    # what was written is read back in the order it was written
    assert [item["ref"] for item in made["content"]["evidence"]] == ["e1", "e2"]
    assert [item["label"] for item in made["content"]["addresses"]] == [
        "Wallet 1",
        "Wallet 2",
        "Wallet 3",
    ]

    (item,) = client.get(V1, headers=staff["editor"]).json()["items"]
    assert (item["evidence"], item["addresses"]) == (2, 3)
    assert item["updated_by"]["email"] == "editor@example.com"


def test_the_editor_reads_the_list_newest_change_first_and_in_pages(client, staff):
    first = make(client, staff, slug="first-one", name="First")
    make(client, staff, slug="second-one", name="Second")
    make(client, staff, slug="third-one", name="Third")
    # a change puts a profile at the top of the list again
    changed = client.put(
        f"{V1}/{first['id']}/content", json=body(name="First, changed"), headers=staff["editor"]
    )
    assert changed.status_code == 200

    def slugs(**params):
        found = client.get(V1, params=params, headers=staff["editor"]).json()
        return [item["slug"] for item in found["items"]]

    assert slugs() == ["first-one", "third-one", "second-one"]
    assert slugs(limit=1, offset=1) == ["third-one"]
    assert slugs(limit=2, offset=2) == ["second-one"]


def test_the_editor_is_told_what_stands_in_the_way_and_what_may_come_next(client, staff):
    bare = make(client, staff, slug="bare-one", evidence=[], addresses=[])
    assert bare["publish_problems"] == ["It rests on no document.", "It names no address."]
    assert (bare["allowed_transitions"], bare["editable"]) == (["in_review"], True)

    def go(number, state):
        moved = client.post(
            f"{V1}/{number}/transition", json={"to": state}, headers=staff["editor"]
        )
        assert moved.status_code == 200, moved.text
        return moved.json()["wallet"]

    in_review = go(bare["id"], "in_review")
    assert (in_review["allowed_transitions"], in_review["editable"]) == (
        ["draft", "published"],
        True,
    )

    whole = make(client, staff)
    go(whole["id"], "in_review")
    published = go(whole["id"], "published")
    assert published["publish_problems"] == []
    assert (published["allowed_transitions"], published["editable"]) == (
        ["archived", "in_review"],
        False,
    )
    archived = go(whole["id"], "archived")
    assert (archived["allowed_transitions"], archived["editable"]) == (["in_review"], False)


def test_the_editor_reads_the_words_the_public_will_read(client, staff):
    words = make(client, staff)["words"]

    assert words["statuses"] == {status.value: text for status, text in STATUS_WORDS.items()}
    assert words["kinds"] == {kind.value: text for kind, text in KIND_WORDS.items()}
