"""The editor's side of incidents: who may write them, and what writing them does.

An incident is global: it belongs to the platform, so only a platform editor may write it (a
person with `users.is_platform_editor`, never a role and never an API key), and what an editor does
is audited for the platform, not for the editor's organization.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from app.access.permissions import Role
from app.database.models import AuditLog, Incident, User
from tests.access_helpers import make_user, person
from tests.incident_helpers import DOCUMENT, EVM, body, make_editor
from tests.incident_helpers import EDITOR_ROUTES as V1


@pytest.fixture
def staff(client, db_session):
    """An editor, an administrator who is not one, an analyst and a viewer, in one organization."""
    editor = make_editor(client, db_session)
    _, admin = person(client, db_session, Role.ADMIN, email="admin@example.com")
    _, analyst = person(client, db_session, Role.ANALYST)
    _, viewer = person(client, db_session, Role.VIEWER)
    return {"editor": editor, "admin": admin, "analyst": analyst, "viewer": viewer}


def make(client, staff, slug="a-theft", **changes):
    response = client.post(V1, json={"slug": slug, **body(**changes)}, headers=staff["editor"])
    assert response.status_code == 201, response.text
    return response.json()["incident"]


# --- an editor writes an incident from start to finish ------------------------------------------


def test_an_editor_writes_publishes_corrects_and_archives_an_incident(client, staff):
    created = make(client, staff)
    assert created["status"] == "draft" and created["slug"] == "a-theft"
    assert created["allowed_transitions"] == ["in_review"] and created["editable"] is True
    assert created["content"]["internal_note"] == "Waiting for a second source."
    assert created["content"]["addresses"][0]["address"] == EVM
    assert created["publish_problems"] == []
    number = created["id"]

    again = client.get(f"{V1}/{number}", headers=staff["editor"]).json()["incident"]
    assert again["content"] == created["content"]  # what is read is what can be sent back

    replaced = client.put(
        f"{V1}/{number}/content", json=body(title="A theft, reworded"), headers=staff["editor"]
    )
    assert (
        replaced.status_code == 200 and replaced.json()["incident"]["title"] == "A theft, reworded"
    )

    for state in ("in_review", "published"):
        moved = client.post(
            f"{V1}/{number}/transition", json={"to": state}, headers=staff["editor"]
        )
        assert moved.status_code == 200, moved.text
    published = moved.json()["incident"]
    assert published["status"] == "published" and published["published_at"]
    assert published["editable"] is False
    assert published["allowed_transitions"] == ["archived", "in_review"]

    refused = client.put(f"{V1}/{number}/content", json=body(), headers=staff["editor"])
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "incident_conflict"

    correction = {
        "occurred_at": "2026-01-20T09:00:00Z",
        "kind": "correction",
        "body": "The first report gave the wrong date.",
        "evidence_ref": "e1",
    }
    grown = client.post(f"{V1}/{number}/updates", json=correction, headers=staff["editor"])
    assert grown.status_code == 201
    assert [u["kind"] for u in grown.json()["incident"]["content"]["updates"]] == [
        "event",
        "correction",
    ]

    archived = client.post(
        f"{V1}/{number}/transition", json={"to": "archived"}, headers=staff["editor"]
    )
    assert archived.json()["incident"]["status"] == "archived"


def test_a_draft_is_deleted_and_a_published_one_never_is(client, staff):
    draft = make(client, staff, slug="a-draft")
    assert client.delete(f"{V1}/{draft['id']}", headers=staff["editor"]).status_code == 204
    assert client.get(f"{V1}/{draft['id']}", headers=staff["editor"]).status_code == 404

    shown = make(client, staff, slug="a-shown-one")
    for state in ("in_review", "published"):
        client.post(f"{V1}/{shown['id']}/transition", json={"to": state}, headers=staff["editor"])
    refused = client.delete(f"{V1}/{shown['id']}", headers=staff["editor"])
    assert refused.status_code == 409


def test_the_list_pages_and_filters_by_state(client, staff):
    for number in range(3):
        make(client, staff, slug=f"incident-{number}")
    first = make(client, staff, slug="incident-3")
    client.post(f"{V1}/{first['id']}/transition", json={"to": "in_review"}, headers=staff["editor"])

    everything = client.get(V1, params={"limit": 2}, headers=staff["editor"]).json()
    assert everything["total"] == 4 and len(everything["items"]) == 2 and everything["limit"] == 2
    item = everything["items"][0]
    assert {"id", "slug", "title", "status", "evidence", "addresses", "updates"} <= set(item)
    assert "summary" not in item and "internal_note" not in item  # a list shows no content

    reviewing = client.get(V1, params={"status": "in_review"}, headers=staff["editor"]).json()
    assert [row["slug"] for row in reviewing["items"]] == ["incident-3"]
    assert (
        client.get(V1, params={"offset": 3}, headers=staff["editor"]).json()["items"].__len__() == 1
    )


# --- a story that is not whole is refused -------------------------------------------------------


def test_a_bad_story_is_refused_with_a_reason_and_nothing_is_kept(client, staff, db_session):
    bad = body(addresses=[{**DOCUMENT["addresses"][0], "address": "not an address"}])
    response = client.post(V1, json={"slug": "a-bad-one", **bad}, headers=staff["editor"])

    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_incident"
    assert "not a valid ethereum address" in response.json()["error"]["message"]
    assert db_session.scalars(select(Incident)).all() == []


def test_a_date_that_is_not_a_date_is_refused(client, staff):
    response = client.post(
        V1, json={"slug": "a-bad-date", **body(occurred_on="2026-13-45")}, headers=staff["editor"]
    )
    assert response.status_code == 422 and "not a date" in response.json()["error"]["message"]


def test_the_address_of_a_page_is_taken_once(client, staff):
    make(client, staff)
    again = client.post(V1, json={"slug": "a-theft", **body()}, headers=staff["editor"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "incident_conflict"


def test_publishing_says_what_stands_in_the_way(client, staff):
    bare = make(client, staff, slug="a-bare-one", evidence=[], amounts=[], addresses=[], updates=[])
    assert bare["publish_problems"] == ["It rests on no document."]
    client.post(f"{V1}/{bare['id']}/transition", json={"to": "in_review"}, headers=staff["editor"])

    refused = client.post(
        f"{V1}/{bare['id']}/transition", json={"to": "published"}, headers=staff["editor"]
    )

    assert refused.status_code == 422
    assert "cannot be published yet" in refused.json()["error"]["message"]
    assert "rests on no document" in refused.json()["error"]["message"]


def test_a_state_cannot_be_skipped(client, staff):
    made = make(client, staff)
    response = client.post(
        f"{V1}/{made['id']}/transition", json={"to": "published"}, headers=staff["editor"]
    )
    assert response.status_code == 409 and "can only go to" in response.json()["error"]["message"]
    assert (
        client.post(
            f"{V1}/{made['id']}/transition", json={"to": "live"}, headers=staff["editor"]
        ).status_code
        == 422
    )


def test_what_is_not_there_is_not_found(client, staff):
    assert client.get(f"{V1}/987654321", headers=staff["editor"]).status_code == 404
    assert (
        client.put(f"{V1}/987654321/content", json=body(), headers=staff["editor"]).status_code
        == 404
    )
    assert client.delete(f"{V1}/987654321", headers=staff["editor"]).status_code == 404
    # an id no row could have is refused before it reaches the database
    assert client.get(f"{V1}/{2**64}", headers=staff["editor"]).status_code == 422


def test_a_body_may_carry_only_what_it_offers(client, staff):
    made = make(client, staff)
    for path, method, payload in (
        (V1, "post", {"slug": "extra-one", **body(), "status": "published"}),
        (f"{V1}/{made['id']}/content", "put", {**body(), "created_by": 1}),
        (f"{V1}/{made['id']}/transition", "post", {"to": "in_review", "force": True}),
    ):
        response = getattr(client, method)(path, json=payload, headers=staff["editor"])
        assert response.status_code == 422, (path, response.text)


# --- who may --------------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["admin", "analyst", "viewer"])
def test_only_a_platform_editor_may_write_or_read_the_editor_routes(client, staff, who):
    made = make(client, staff)
    number = made["id"]
    attempts = [
        ("get", V1, None),
        ("post", V1, {"slug": "someone-elses", **body()}),
        ("get", f"{V1}/{number}", None),
        ("put", f"{V1}/{number}/content", body()),
        ("post", f"{V1}/{number}/transition", {"to": "in_review"}),
        (
            "post",
            f"{V1}/{number}/updates",
            {"occurred_at": "2026-01-20T09:00:00Z", "kind": "event", "body": "x"},
        ),
        ("delete", f"{V1}/{number}", None),
    ]
    for method, path, payload in attempts:
        kwargs = {} if payload is None else {"json": payload}
        response = getattr(client, method)(path, headers=staff[who], **kwargs)
        assert response.status_code == 403, (who, method, path, response.text)
    assert (
        client.get(f"{V1}/{number}", headers=staff["editor"]).json()["incident"]["status"]
        == "draft"
    )


def test_nobody_gets_in_without_signing_in(client, staff):
    assert client.get(V1).status_code == 401
    assert client.post(V1, json={"slug": "anon-incident", **body()}).status_code == 401


def test_an_editor_who_is_stopped_is_refused_at_once_and_one_who_is_switched_off_cannot_sign_in(
    client, staff, db_session
):
    assert client.get(V1, headers=staff["editor"]).status_code == 200

    db_session.execute(
        update(User).where(User.email == "editor@example.com").values(is_platform_editor=False)
    )
    db_session.commit()
    db_session.rollback()
    assert (
        client.get(V1, headers=staff["editor"]).status_code == 403
    )  # the same token, the next request

    db_session.execute(
        update(User)
        .where(User.email == "editor@example.com")
        .values(is_platform_editor=True, is_active=False)
    )
    db_session.commit()
    db_session.rollback()
    assert client.get(V1, headers=staff["editor"]).status_code == 401


def test_the_editor_flag_cannot_be_given_through_the_people_routes(client, staff, db_session):
    colleague = make_user(db_session, "colleague@example.com", Role.VIEWER)
    db_session.rollback()

    made = client.post(
        "/api/v1/users",
        json={
            "email": "new@example.com",
            "password": "A long enough password 1!",
            "role": "viewer",
            "is_platform_editor": True,
        },
        headers=staff["admin"],
    )
    assert made.status_code == 422
    changed = client.patch(
        f"/api/v1/users/{colleague.id}", json={"is_platform_editor": True}, headers=staff["admin"]
    )
    assert changed.status_code == 422
    assert (
        db_session.scalar(
            select(User.is_platform_editor).where(User.email == "colleague@example.com")
        )
        is False
    )


# --- what is audited, and for whom ----------------------------------------------------------------


def test_every_change_is_audited_for_the_platform_and_no_organization_reads_it(
    client, staff, db_session
):
    made = make(client, staff)
    number = made["id"]
    client.put(f"{V1}/{number}/content", json=body(title="Reworded"), headers=staff["editor"])
    client.post(f"{V1}/{number}/transition", json={"to": "in_review"}, headers=staff["editor"])
    client.post(
        f"{V1}/{number}/updates",
        json={"occurred_at": "2026-01-20T09:00:00Z", "kind": "statement", "body": "Said."},
        headers=staff["editor"],
    )
    client.delete(f"{V1}/{make(client, staff, slug='to-delete')['id']}", headers=staff["editor"])

    db_session.rollback()
    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action.like("incident.%")).order_by(AuditLog.id)
    ).all()
    assert [row.action for row in rows] == [
        "incident.create",
        "incident.content",
        "incident.status",
        "incident.update",
        "incident.create",
        "incident.delete",
    ]
    assert {row.organization_id for row in rows} == {None}
    assert {(row.actor_type, row.actor_label) for row in rows} == {("user", "editor@example.com")}
    assert rows[2].details == {"slug": "a-theft", "from": "draft", "to": "in_review"}
    assert rows[1].details["addresses"] == 1 and rows[0].details["title"] == "A theft"

    # the editor's own organization sees none of it in its log
    listed = client.get("/api/v1/audit", params={"limit": 200}, headers=staff["admin"]).json()
    assert not [row for row in listed["items"] if row["action"].startswith("incident.")]
    db_session.rollback()


def test_a_refused_attempt_is_audited_like_any_other_denial(client, staff, db_session):
    assert client.get(V1, headers=staff["analyst"]).status_code == 403
    db_session.rollback()
    (row,) = db_session.scalars(select(AuditLog).where(AuditLog.action == "access.denied")).all()
    assert row.details["needs"] == ["incidents:curate"] and row.details["role"] == "analyst"
    db_session.rollback()
