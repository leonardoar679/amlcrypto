"""The audit log: what an entry holds and never holds, and who can read it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select

from app.access import audit
from app.access.audit import Actor, Origin
from app.access.permissions import Role
from app.database.models import AuditLog
from tests.access_helpers import make_user, person

# --- what is kept ------------------------------------------------------------------------------


def test_nothing_named_like_a_secret_is_kept_at_any_depth():
    kept = audit.scrub(
        {
            "email": "a@example.com",
            "password": "hunter2",
            "new_password": "hunter3",
            "Authorization": "Bearer abc",
            "api_key": "k",
            "X-Api-Key": "k",
            "secret": "s",
            "client_secret": "s",
            "access_token": "t",
            "credentials": "c",
            "nested": {"token": "t", "ok": 1, "deeper": [{"passphrase": "p", "fine": "yes"}]},
        }
    )
    assert kept["email"] == "a@example.com"
    for name in (
        "password",
        "new_password",
        "Authorization",
        "api_key",
        "X-Api-Key",
        "secret",
        "client_secret",
        "access_token",
        "credentials",
    ):
        assert kept[name] == "[removed]", name
    assert kept["nested"]["token"] == "[removed]" and kept["nested"]["ok"] == 1
    assert kept["nested"]["deeper"] == [{"passphrase": "[removed]", "fine": "yes"}]
    assert "hunter" not in str(kept)


def test_an_entry_stays_small():
    kept = audit.scrub(
        {
            "long": "x" * 10_000,
            "many": list(range(500)),
            "wide": {str(n): n for n in range(500)},
            "deep": {"a": {"b": {"c": {"d": {"e": 1}}}}},
        }
    )
    assert len(kept["long"]) == audit.MAX_DETAIL_TEXT
    assert len(kept["many"]) == audit.MAX_DETAIL_ITEMS
    assert len(kept["wide"]) == audit.MAX_DETAIL_ITEMS
    assert kept["deep"]["a"]["b"] == {"c": "..."}  # nothing is followed past four levels


def test_scrubbing_leaves_the_input_alone_and_makes_everything_storable():
    original = {
        "password": "p",
        "when": datetime(2026, 1, 1, tzinfo=UTC),
        "n": 1.5,
        "flag": True,
        "none": None,
        "tuple": (1, 2),
    }
    kept = audit.scrub(original)
    assert original["password"] == "p"
    assert kept["when"].startswith("2026-01-01") and kept["n"] == 1.5 and kept["flag"] is True
    assert kept["none"] is None and kept["tuple"] == [1, 2]
    assert audit.scrub(None) == {} and audit.scrub({}) == {}


def test_a_secret_name_is_recognised_by_what_it_contains_not_by_its_case():
    for name in (
        "PASSWORD",
        "Pass",
        "user_password_hash",
        "my-api-key",
        "ApiKey",
        "bearer_token",
        "SECRET",
    ):
        assert audit.scrub({name: "x"})[name] == "[removed]", name
    for name in ("email", "role", "prefix", "name", "outcome", "path"):
        assert audit.scrub({name: "x"})[name] == "x", name


# --- where from --------------------------------------------------------------------------------


def test_without_a_trusted_proxy_the_connection_is_the_client_and_the_header_is_ignored():
    assert audit.client_ip("203.0.113.9", "198.51.100.1", 0) == "203.0.113.9"
    assert audit.client_ip("203.0.113.9", None, 0) == "203.0.113.9"


def test_behind_trusted_proxies_the_address_they_saw_is_the_client():
    # one proxy: the last entry is what it saw; whatever the client put before it is theirs
    assert audit.client_ip("10.0.0.2", "6.6.6.6, 198.51.100.1", 1) == "198.51.100.1"
    # two proxies: the one before the last
    assert audit.client_ip("10.0.0.2", "198.51.100.1, 10.0.0.1", 2) == "198.51.100.1"
    assert audit.client_ip("10.0.0.2", "6.6.6.6, 198.51.100.1, 10.0.0.1", 2) == "198.51.100.1"
    # fewer entries than trusted proxies: the header cannot be right, so the connection is used
    assert audit.client_ip("10.0.0.2", "198.51.100.1", 2) == "10.0.0.2"
    assert audit.client_ip("10.0.0.2", "", 1) == "10.0.0.2"


def test_only_a_real_address_is_kept_and_it_is_normalised():
    assert audit.client_ip("not an address", None, 0) is None
    assert audit.client_ip(None, None, 0) is None
    assert audit.client_ip("10.0.0.2", "javascript:alert(1)", 1) is None
    assert audit.client_ip("2001:DB8:0:0:0:0:0:1", None, 0) == "2001:db8::1"
    assert audit.client_ip("::ffff:1.2.3.4", None, 0) == "::ffff:1.2.3.4"


def test_the_user_agent_is_cleaned_and_cut():
    origin = audit.origin_of(
        "1.2.3.4", None, "Mozilla/5.0\r\nX-Injected: yes\x00" + "a" * 400, "req-1"
    )
    assert (
        "\r" not in origin.user_agent
        and "\n" not in origin.user_agent
        and "\x00" not in origin.user_agent
    )
    assert len(origin.user_agent) == 255
    assert origin.request_id == "req-1" and origin.ip == "1.2.3.4"
    assert audit.origin_of("1.2.3.4", None, None, None).user_agent is None


def test_accounts_are_grouped_by_a_hash_of_the_email():
    key = audit.subject_key("Someone@Example.com")
    assert key == audit.subject_key("  someone@example.com ") and len(key) == 64
    assert key != audit.subject_key("someone2@example.com")
    assert "someone" not in key


# --- writing -----------------------------------------------------------------------------------


def test_an_entry_records_who_what_to_what_and_from_where(db_session):
    user = make_user(db_session, "a@example.com", Role.ADMIN)
    actor = Actor("user", user.id, user.email, user.organization_id)
    origin = Origin("203.0.113.9", "agent", "req-42")

    audit.record(
        db_session,
        actor=actor,
        action="thing.done",
        target_type="thing",
        target_id=12345,
        details={"password": "no", "kept": "yes"},
        origin=origin,
        subject="abc",
    )
    db_session.commit()

    (row,) = db_session.scalars(select(AuditLog)).all()
    assert (row.actor_type, row.actor_id, row.actor_label) == ("user", user.id, "a@example.com")
    assert row.organization_id == user.organization_id
    assert (row.action, row.outcome, row.target_type, row.target_id) == (
        "thing.done",
        "success",
        "thing",
        "12345",
    )
    assert row.details == {"password": "[removed]", "kept": "yes"}
    assert (row.ip, row.user_agent, row.request_id, row.subject_key) == (
        "203.0.113.9",
        "agent",
        "req-42",
        "abc",
    )
    assert row.created_at is not None and abs(datetime.now(UTC) - row.created_at) < timedelta(
        seconds=30
    )


def test_overlong_fields_are_cut_to_fit_their_columns(db_session):
    audit.record(
        db_session,
        actor=Actor("user", 1, "x" * 400, None),
        action="a" * 100,
        target_id="t" * 300,
    )
    db_session.commit()
    (row,) = db_session.scalars(select(AuditLog)).all()
    assert len(row.actor_label) == 320 and len(row.action) == 60 and len(row.target_id) == 100


def test_an_event_can_belong_to_an_organization_other_than_the_actors(db_session):
    user = make_user(db_session, "a@example.com", Role.ADMIN)
    audit.record(
        db_session, actor=audit.SYSTEM, action="cli.thing", organization_id=user.organization_id
    )
    audit.record(db_session, actor=audit.ANONYMOUS, action="anonymous.thing")
    db_session.commit()
    rows = db_session.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    assert [(r.actor_type, r.organization_id) for r in rows] == [
        ("system", user.organization_id),
        ("anonymous", None),
    ]


# --- reading it --------------------------------------------------------------------------------


@pytest.fixture
def admin(client, db_session):
    return person(client, db_session, Role.ADMIN)


def put(db_session, organization_id, **fields):
    audit.record(db_session, actor=Actor("user", 1, "x@example.com", organization_id), **fields)
    db_session.commit()


def get(client, headers, **params):
    response = client.get("/api/v1/audit", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_log_is_newest_first_and_pages(client, db_session, admin):
    user, headers = admin
    for n in range(7):
        put(db_session, user.organization_id, action=f"thing.n{n}")

    first = get(client, headers, action="thing.", limit=3)
    assert first["total"] == 7 and first["limit"] == 3 and first["offset"] == 0
    assert [i["action"] for i in first["items"]] == ["thing.n6", "thing.n5", "thing.n4"]
    last = get(client, headers, action="thing.", limit=3, offset=6)
    assert [i["action"] for i in last["items"]] == ["thing.n0"]
    item = first["items"][0]
    assert set(item) == {
        "id",
        "created_at",
        "actor",
        "action",
        "outcome",
        "target",
        "details",
        "ip",
        "user_agent",
        "request_id",
    }
    assert set(item["actor"]) == {"type", "id", "label"} and set(item["target"]) == {"type", "id"}


def test_the_log_can_be_filtered(client, db_session, admin):
    user, headers = admin
    org = user.organization_id
    put(db_session, org, action="screening.request", target_type="screening", target_id="scr_1")
    put(db_session, org, action="screening.export", target_type="screening", target_id="scr_1")
    put(db_session, org, action="user.create", outcome="failure", target_type="user", target_id="7")
    audit.record(
        db_session,
        actor=Actor("api_key", 9, "Ingest", org),
        action="screening.request",
        target_type="screening",
        target_id="scr_2",
    )
    db_session.commit()

    def actions(**params):
        return sorted(
            i["action"] + ":" + str(i["target"]["id"])
            for i in get(client, headers, **params)["items"]
            if i["actor"]["id"] in (1, 9)
        )

    assert actions(action="screening.") == [
        "screening.export:scr_1",
        "screening.request:scr_1",
        "screening.request:scr_2",
    ]
    assert actions(action="screening.request") == [
        "screening.request:scr_1",
        "screening.request:scr_2",
    ]
    assert actions(outcome="failure") == ["user.create:7"]
    assert actions(actor_type="api_key") == ["screening.request:scr_2"]
    assert actions(actor_id=9) == ["screening.request:scr_2"]
    assert actions(target_type="user") == ["user.create:7"]
    assert actions(target_type="screening", target_id="scr_1") == [
        "screening.export:scr_1",
        "screening.request:scr_1",
    ]
    # a filter that names no action is not a wildcard
    assert actions(action="screening%") == [] and actions(action="screening_") == []


def test_the_log_can_be_cut_by_time_and_naive_times_are_utc(client, db_session, admin):
    user, headers = admin
    put(db_session, user.organization_id, action="then.thing")
    row = db_session.scalar(select(AuditLog).where(AuditLog.action == "then.thing"))
    row.created_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    db_session.commit()

    def count(**params):
        return len([i for i in get(client, headers, action="then.", **params)["items"]])

    assert (
        count(since="2026-01-01T12:00:00") == 1
    )  # a time with no zone is UTC; the start is included
    assert count(since="2026-01-01T12:00:01") == 0
    assert count(until="2026-01-01T12:00:00") == 0  # the end is not
    assert count(until="2026-01-01T12:00:01") == 1
    assert count(since="2026-01-01T13:00:00+01:00") == 1  # a zone is honoured


def test_only_the_organizations_own_events_are_visible(client, db_session, admin):
    user, headers = admin
    _, other = person(client, db_session, Role.ADMIN, organization="Other Org")
    put(db_session, user.organization_id, action="ours.thing")
    audit.record(db_session, actor=audit.ANONYMOUS, action="orphan.thing")  # belongs to nobody
    db_session.commit()

    assert [i["action"] for i in get(client, headers, action="ours.")["items"]] == ["ours.thing"]
    assert get(client, other, action="ours.")["items"] == []
    assert get(client, headers, action="orphan.")["items"] == []
    assert get(client, other, action="orphan.")["items"] == []


def test_the_log_can_only_be_read(app):
    methods = set()
    for route in app.router.routes:
        stack = [(route, "")]
        while stack:
            node, prefix = stack.pop()
            if isinstance(node, APIRoute) and (prefix + node.path).startswith("/api/v1/audit"):
                methods |= set(node.methods)
            elif hasattr(node, "original_router"):
                stack.extend(
                    (r, prefix + (node.include_context.prefix or ""))
                    for r in node.original_router.routes
                )
    assert methods - {"HEAD"} == {"GET"}


def test_bad_filters_are_refused(client, admin):
    _, headers = admin
    for params in (
        {"outcome": "maybe"},
        {"actor_type": "robot"},
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"since": "yesterday"},
    ):
        assert client.get("/api/v1/audit", params=params, headers=headers).status_code == 422, (
            params
        )
