"""Signing in: what is audited, and how an account that keeps failing is locked for a while."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.access import audit
from app.access.permissions import Role
from app.core.config import Settings
from app.database.models import AuditLog, User
from tests.access_helpers import PASSWORD, make_user, sign_in
from tests.conftest import TEST_SECRET

MAX = 3
WINDOW = 600


@pytest.fixture
def settings(database_url, tmp_path):
    return Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        secret_key=TEST_SECRET,
        data_dir=tmp_path,
        login_max_failures=MAX,
        login_failure_window_seconds=WINDOW,
    )


def attempt(client, email="ada@example.com", password=PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def rows(db_session, **where):
    db_session.rollback()  # see what the API committed
    query = select(AuditLog).where(AuditLog.action == "auth.login").order_by(AuditLog.id)
    return [
        row
        for row in db_session.scalars(query).all()
        if all(getattr(row, name) == value for name, value in where.items())
    ]


@pytest.fixture
def ada(db_session):
    return make_user(db_session, "ada@example.com", Role.ANALYST)


# --- what is audited ---------------------------------------------------------------------------


def test_a_sign_in_is_audited_with_who_and_from_where(client, db_session, ada):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "Ada@Example.com", "password": PASSWORD},
        headers={"User-Agent": "test-agent/1.0", "X-Request-ID": "req-abcdef123"},
    )
    assert response.status_code == 200

    (row,) = rows(db_session)
    assert (row.outcome, row.actor_type, row.actor_id, row.actor_label) == (
        "success",
        "user",
        ada.id,
        "ada@example.com",
    )
    assert row.organization_id == ada.organization_id
    assert row.subject_key == audit.subject_key("ada@example.com")
    assert row.user_agent == "test-agent/1.0" and row.request_id == "req-abcdef123"
    assert row.ip is None  # the test client's "address" is the word testclient, not an address
    assert PASSWORD not in str(row.details) and row.details == {}


def test_the_address_of_the_connection_is_recorded_and_a_forged_header_is_not_believed(
    app, db_session, ada
):
    from fastapi.testclient import TestClient

    with TestClient(app, client=("203.0.113.9", 4321)) as client:
        ok = client.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": PASSWORD},
            headers={"X-Forwarded-For": "6.6.6.6"},
        )
    assert ok.status_code == 200
    (row,) = rows(db_session)
    assert row.ip == "203.0.113.9"  # no trusted proxy is configured, so the header is only a claim


def test_a_wrong_password_for_a_known_account_belongs_to_that_accounts_organization(
    client, db_session, ada
):
    assert attempt(client, password="not the password").status_code == 401

    (row,) = rows(db_session)
    assert (row.outcome, row.actor_type, row.actor_id) == ("failure", "user", ada.id)
    assert row.organization_id == ada.organization_id and row.details == {
        "reason": "wrong_password"
    }


def test_a_sign_in_as_nobody_is_audited_without_storing_what_was_typed(client, db_session):
    secret_typed_as_email = "my-actual-password-123"
    assert (
        attempt(client, email=secret_typed_as_email + "@x.io", password="whatever1234").status_code
        == 401
    )

    (row,) = rows(db_session)
    assert (row.outcome, row.actor_type, row.organization_id) == ("failure", "anonymous", None)
    assert row.details == {"reason": "unknown_account"}
    everything = " ".join(str(getattr(row, c.name)) for c in AuditLog.__table__.columns)
    assert secret_typed_as_email not in everything and "whatever1234" not in everything
    assert row.subject_key == audit.subject_key(secret_typed_as_email + "@x.io")


def test_a_switched_off_account_is_refused_and_audited_as_such(client, db_session, ada):
    db_session.execute(update(User).values(is_active=False))
    db_session.commit()

    assert attempt(client).status_code == 401
    (row,) = rows(db_session)
    assert row.details == {"reason": "inactive_account"} and row.actor_id == ada.id


def test_an_unknown_account_costs_the_same_time_as_a_wrong_password(
    client, db_session, ada, monkeypatch
):
    from app.api.v1 import auth

    burned = []
    monkeypatch.setattr(auth, "burn_password_check", lambda password: burned.append(password))

    attempt(client, email="nobody@example.com", password="some password 1")
    attempt(client, password="wrong password!")  # a known account: the real check is the cost

    assert burned == ["some password 1"]


def test_every_refusal_looks_the_same_to_the_caller(client, db_session, ada):
    unknown = attempt(client, email="nobody@example.com")
    wrong = attempt(client, password="wrong password!")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["code"] == wrong.json()["error"]["code"] == "invalid_credentials"
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]


# --- the lockout -------------------------------------------------------------------------------


def fail(client, times, **kwargs):
    for _ in range(times):
        assert attempt(client, password="wrong password!", **kwargs).status_code == 401


def test_an_account_that_keeps_failing_is_locked_even_for_the_right_password(
    client, db_session, ada
):
    fail(client, MAX)

    locked = attempt(client)  # the right password, now refused
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "too_many_attempts"
    retry = int(locked.headers["Retry-After"])
    assert (
        WINDOW - 5 <= retry <= WINDOW + 1
    )  # about the whole window: the last failure was just now


def test_one_failure_fewer_than_the_limit_does_not_lock(client, db_session, ada):
    fail(client, MAX - 1)
    assert attempt(client).status_code == 200


def test_the_lock_is_for_one_account_only(client, db_session, ada):
    make_user(db_session, "bob@example.com", Role.VIEWER)
    fail(client, MAX)
    assert attempt(client).status_code == 429
    assert attempt(client, email="bob@example.com").status_code == 200


def test_an_unknown_account_locks_the_same_way_so_the_lock_tells_nothing(client, db_session):
    fail(client, MAX, email="nobody@example.com")
    locked = attempt(client, email="nobody@example.com")
    assert locked.status_code == 429 and locked.json()["error"]["code"] == "too_many_attempts"


def test_the_email_is_matched_whatever_its_case_or_spacing(client, db_session, ada):
    fail(client, 1, email="ADA@example.com")
    fail(client, 1, email=" ada@example.com ")
    fail(client, 1, email="Ada@Example.Com")
    assert attempt(client, email="ada@EXAMPLE.com").status_code == 429


def test_trying_again_while_locked_does_not_extend_the_lock(client, db_session, ada):
    fail(client, MAX)
    first = int(attempt(client).headers["Retry-After"])
    for _ in range(5):
        assert attempt(client).status_code == 429
    again = int(attempt(client).headers["Retry-After"])
    assert again <= first  # it can only have got shorter

    denied = rows(db_session, outcome="denied")
    assert len(denied) == 7 and all(r.details == {"reason": "locked"} for r in denied)
    assert len(rows(db_session, outcome="failure")) == MAX  # the refusals are not failures


def test_the_lock_lifts_when_the_failures_age_out_of_the_window(client, db_session, ada):
    fail(client, MAX)
    assert attempt(client).status_code == 429

    # the oldest of the failures that make the lock leaves the window
    old = datetime.now(UTC) - timedelta(seconds=WINDOW + 5)
    oldest = rows(db_session, outcome="failure")[0]
    oldest.created_at = old
    db_session.commit()

    assert attempt(client).status_code == 200


def test_failures_older_than_the_window_do_not_count_at_all(client, db_session, ada):
    fail(client, MAX - 1)
    for row in rows(db_session, outcome="failure"):
        row.created_at = datetime.now(UTC) - timedelta(seconds=WINDOW + 5)
    db_session.commit()
    fail(client, MAX - 1)  # only these count now
    assert attempt(client).status_code == 200


def test_the_wait_is_how_long_until_the_failure_that_tipped_it_expires(client, db_session, ada):
    fail(client, MAX)
    failures = rows(db_session, outcome="failure")
    # make the three failures 100, 200 and 300 seconds old: the lock lifts when the OLDEST of the
    # newest MAX leaves the window, in WINDOW - 300 seconds
    for age, row in zip((100, 200, 300), failures[::-1], strict=True):
        row.created_at = datetime.now(UTC) - timedelta(seconds=age)
    db_session.commit()

    retry = int(attempt(client).headers["Retry-After"])
    assert WINDOW - 300 - 1 <= retry <= WINDOW - 300 + 2


def test_a_successful_sign_in_before_the_limit_is_still_possible_afterwards(
    client, db_session, ada
):
    fail(client, MAX - 1)
    assert attempt(client).status_code == 200
    # the failures stay in the window: one more is the limit, since the log is the record
    assert attempt(client, password="wrong password!").status_code == 401
    assert attempt(client).status_code == 429


# --- changing your own password ----------------------------------------------------------------


NEW = "a brand new passphrase"


def change(client, headers, current=PASSWORD, new=NEW):
    return client.post(
        "/api/v1/auth/password",
        json={"current_password": current, "new_password": new},
        headers=headers,
    )


def test_a_person_changes_their_own_password(client, db_session, ada):
    headers = sign_in(client, "ada@example.com")

    assert change(client, headers).status_code == 204

    assert attempt(client, password=PASSWORD).status_code == 401  # the old one is gone
    assert attempt(client, password=NEW).status_code == 200
    (row,) = [
        r
        for r in db_session.scalars(
            select(AuditLog).where(AuditLog.action == "auth.password_change")
        )
    ]
    assert row.outcome == "success" and row.target_type == "user" and row.target_id == str(ada.id)
    assert PASSWORD not in str(row.details) and NEW not in str(row.details)


def test_the_current_password_is_needed_and_a_wrong_one_counts_as_a_failed_sign_in(
    client, db_session, ada
):
    headers = sign_in(client, "ada@example.com")

    refused = change(client, headers, current="not my password")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "wrong_password"
    assert attempt(client, password=NEW).status_code == 401  # nothing changed

    failures = [
        r for r in rows(db_session, outcome="failure") if r.details == {"reason": "password_change"}
    ]
    assert len(failures) == 1 and failures[0].actor_id is not None
    db_session.rollback()  # a session that has read holds SQLite's write lock; let go of it

    # a token alone cannot be used to guess the password for ever
    for _ in range(MAX - 1):
        change(client, headers, current="still not")
    assert change(client, headers, current=PASSWORD).status_code == 429


def test_a_weak_new_password_is_refused(client, db_session, ada):
    headers = sign_in(client, "ada@example.com")
    weak = change(client, headers, new="short")
    assert weak.status_code == 422 and weak.json()["error"]["code"] == "weak_password"
    assert attempt(client, password=PASSWORD).status_code == 200


# --- the rule itself, with the clock in our hands -----------------------------------------------


def _failures(db_session, subject, ages, *, action="auth.login", outcome="failure"):
    """Entries that happened `ages` seconds before NOW."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    for age in ages:
        row = audit.record(
            db_session, actor=audit.ANONYMOUS, action=action, outcome=outcome, subject=subject
        )
        row.created_at = now - timedelta(seconds=age)
    db_session.commit()
    return now


def _wait(db_session, settings, subject, now):
    from app.access.lockout import seconds_locked

    return seconds_locked(db_session, settings, subject, now=now)


def test_only_failed_sign_ins_of_that_account_count(db_session, settings):
    mine, other = audit.subject_key("me@example.com"), audit.subject_key("you@example.com")
    now = _failures(db_session, mine, [10, 20, 30], action="something.else")  # not sign-ins
    _failures(db_session, mine, [10, 20, 30], outcome="denied")  # not failures
    _failures(db_session, other, [10, 20, 30])  # someone else's
    assert _wait(db_session, settings, mine, now) is None
    assert _wait(db_session, settings, other, now) is not None


def test_the_wait_follows_the_failure_that_tipped_it_even_when_there_are_more(db_session, settings):
    subject = audit.subject_key("me@example.com")
    # five failures, the limit is three: the lock lifts when the third newest (300 s old) leaves
    now = _failures(db_session, subject, [100, 200, 300, 400, 500])
    assert _wait(db_session, settings, subject, now) == WINDOW - 300 + 1


def test_a_failure_leaves_the_window_at_the_instant_the_window_ends(db_session, settings):
    subject = audit.subject_key("me@example.com")
    now = _failures(db_session, subject, [WINDOW, 200, 100])  # the oldest is exactly one window old
    assert _wait(db_session, settings, subject, now) is None  # so it no longer counts
    assert _wait(db_session, settings, subject, now - timedelta(microseconds=1)) is not None


def test_the_wait_is_never_less_than_a_second(db_session, settings):
    subject = audit.subject_key("me@example.com")
    now = _failures(db_session, subject, [WINDOW - 0.4, 200, 100])  # lifts in 0.4 s
    assert _wait(db_session, settings, subject, now) == 1
