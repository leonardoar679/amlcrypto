"""The provider's doorbell: who may ring it, what it understands, and what ringing does."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import pytest
from sqlalchemy import select

from app.access.permissions import Role
from app.api.errors import APIError
from app.api.v1.webhooks import _read_body as read_body
from app.core.config import Settings
from app.database.models import AuditLog, FetchJob
from app.monitoring import service, webhooks
from app.monitoring.webhooks import BadEvent, Event, parse_alchemy, verify_alchemy, wake
from tests.access_helpers import make_user
from tests.conftest import TEST_SECRET
from tests.monitoring_helpers import ADDRESS, OTHER_ADDRESS, watch

KEY = "whsec_0123456789abcdef"
URL = "/api/v1/webhooks/alchemy"


def sign(body: bytes, key: str = KEY) -> str:
    return hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


def event(*pairs, network="ETH_MAINNET", kind="ADDRESS_ACTIVITY") -> dict:
    return {
        "webhookId": "wh_1",
        "type": kind,
        "event": {
            "network": network,
            "activity": [{"fromAddress": a, "toAddress": b, "asset": "ETH"} for a, b in pairs],
        },
    }


def body_of(payload) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


# --- the signature ------------------------------------------------------------------------------


def test_the_signature_is_the_hmac_sha256_of_the_raw_body_in_hex():
    body = b'{"a": 1}'
    expected = hmac.new(b"key", body, hashlib.sha256).hexdigest()
    assert webhooks.alchemy_signature(body, "key") == expected
    assert len(expected) == 64


def test_the_right_signature_is_accepted_in_any_case_and_with_stray_spaces():
    body = body_of(event())
    good = sign(body)
    assert verify_alchemy(body, good, KEY)
    assert verify_alchemy(body, good.upper(), KEY)
    assert verify_alchemy(body, f"  {good}\n", KEY)


@pytest.mark.parametrize(
    "signature", [None, "", "   ", "0" * 64, "zz", sign(b"other"), "é" * 64, 12345, b"bytes"]
)
def test_anything_but_the_right_signature_is_refused(signature):
    assert verify_alchemy(body_of(event()), signature, KEY) is False


def test_the_signature_belongs_to_this_body_and_this_key():
    body = body_of(event())
    assert not verify_alchemy(body + b" ", sign(body), KEY)
    assert not verify_alchemy(body, sign(body), "another key")
    assert not verify_alchemy(body, sign(body, "another key"), KEY)
    assert not verify_alchemy(b"", sign(body), KEY)


# --- reading an event ---------------------------------------------------------------------------


def test_an_event_names_its_chain_and_every_address_once_in_storage_form():
    mixed = "0x" + "AB" * 20
    parsed = parse_alchemy(
        event((mixed, OTHER_ADDRESS), (ADDRESS, OTHER_ADDRESS), (OTHER_ADDRESS, mixed))
    )
    assert parsed == Event("eip155:1", (ADDRESS, OTHER_ADDRESS))


def test_the_chain_of_an_event_is_the_network_it_names():
    assert parse_alchemy(event((ADDRESS, ADDRESS), network="BNB_MAINNET")).chain_id == "eip155:56"
    assert parse_alchemy(event((ADDRESS, ADDRESS), network="ETH_MAINNET")).chain_id == "eip155:1"
    for other in ("MATIC_MAINNET", "SOLANA_MAINNET", "", None, 1):
        assert parse_alchemy(event((ADDRESS, ADDRESS), network=other)).chain_id is None


def test_what_is_not_an_address_is_not_taken_for_one():
    odd = [
        {"fromAddress": "0x123", "toAddress": None},
        {"fromAddress": 5, "toAddress": ["0x" + "ab" * 20]},
        {"fromAddress": "0x" + "zz" * 20, "toAddress": "0x" + "ab" * 21},
        {"fromAddress": "0x" + "ab" * 20 + "\n", "toAddress": ADDRESS + " "},
        "text",
        None,
        {"toAddress": OTHER_ADDRESS},
    ]
    payload = event()
    payload["event"]["activity"] = odd
    assert parse_alchemy(payload).addresses == (OTHER_ADDRESS,)


def test_an_event_names_a_bounded_number_of_addresses():
    count = webhooks.MAX_ADDRESSES + 50
    pairs = [("0x" + f"{n:040x}", "0x" + f"{n + 10**6:040x}") for n in range(count)]
    parsed = parse_alchemy(event(*pairs))
    assert len(parsed.addresses) == webhooks.MAX_ADDRESSES
    assert parsed.addresses[0] == "0x" + f"{0:040x}"


def test_the_bound_holds_when_the_last_item_to_fit_brings_two_addresses():
    singles = [("0x" + f"{n:040x}",) * 2 for n in range(webhooks.MAX_ADDRESSES - 1)]
    crossing = ("0x" + f"{10**6:040x}", "0x" + f"{10**6 + 1:040x}")  # takes it from 499 to 501
    parsed = parse_alchemy(event(*singles, crossing, ("0x" + f"{10**7:040x}",) * 2))
    assert len(parsed.addresses) == webhooks.MAX_ADDRESSES
    assert parsed.addresses[-1] == crossing[0]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "text",
        {},
        {"type": "GRAPHQL", "event": {"activity": []}},
        {"type": "ADDRESS_ACTIVITY"},
        {"type": "ADDRESS_ACTIVITY", "event": []},
        {"type": "ADDRESS_ACTIVITY", "event": {}},
        {"type": "ADDRESS_ACTIVITY", "event": {"activity": {}}},
    ],
)
def test_what_is_not_an_address_activity_event_is_refused(payload):
    with pytest.raises(BadEvent):
        parse_alchemy(payload)


# --- what ringing does --------------------------------------------------------------------------


@pytest.fixture
def team(db_session, settings):
    ana = make_user(db_session, "ana@example.com", Role.ANALYST, "Acme")
    sam = make_user(db_session, "sam@example.com", Role.ANALYST, "Other Org")
    return ana, sam


def queued(session):
    return list(session.scalars(select(FetchJob).order_by(FetchJob.id)))


def test_every_organization_watching_an_address_is_woken(db_session, settings, team):
    ana, sam = team
    mine = watch(db_session, settings, ana)
    theirs = watch(db_session, settings, sam)
    watch(db_session, settings, ana, address=OTHER_ADDRESS)  # not named in the event

    count = wake(db_session, Event("eip155:1", (ADDRESS,)))

    jobs = queued(db_session)
    assert count == 2
    assert sorted(j.payload["monitor_id"] for j in jobs) == sorted([mine.id, theirs.id])
    assert {j.payload["reason"] for j in jobs} == {"webhook"}
    assert {j.organization_id for j in jobs} == {ana.organization_id, sam.organization_id}


def test_only_active_monitors_on_the_chain_of_the_event_are_woken(db_session, settings, team):
    ana, _ = team
    paused = watch(db_session, settings, ana)
    paused.status = "paused"
    watch(db_session, settings, ana, chain="bsc")
    db_session.commit()

    assert wake(db_session, Event("eip155:1", (ADDRESS,))) == 0
    assert wake(db_session, Event("eip155:56", (ADDRESS,))) == 1
    assert wake(db_session, Event("eip155:56", (OTHER_ADDRESS,))) == 0


def test_an_event_about_a_chain_nobody_reads_or_no_address_wakes_nobody(db_session, settings, team):
    watch(db_session, settings, team[0])
    assert wake(db_session, Event(None, (ADDRESS,))) == 0
    assert wake(db_session, Event("eip155:1", ())) == 0
    assert queued(db_session) == []


def test_ringing_twice_does_not_queue_a_second_check(db_session, settings, team):
    watch(db_session, settings, team[0])
    assert wake(db_session, Event("eip155:1", (ADDRESS,))) == 1
    assert wake(db_session, Event("eip155:1", (ADDRESS,))) == 0
    assert len(queued(db_session)) == 1


def test_a_check_already_waiting_for_its_turn_is_not_doubled_by_the_bell(
    db_session, settings, team
):
    row = watch(db_session, settings, team[0])
    service.queue_check(db_session, row, reason=service.SCHEDULED)
    assert wake(db_session, Event("eip155:1", (ADDRESS,))) == 0
    assert len(queued(db_session)) == 1


# --- over HTTP ----------------------------------------------------------------------------------


@pytest.fixture
def settings(database_url, tmp_path):
    return Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        secret_key=TEST_SECRET,
        data_dir=tmp_path,
        alchemy_webhook_signing_key=KEY,
        webhook_max_body_bytes=2048,
    )


def post(client, payload=None, *, body=None, signature="sign", **headers):
    raw = body if body is not None else body_of(payload)
    sent = {"content-type": "application/json", **headers}
    if signature == "sign":
        sent["x-alchemy-signature"] = sign(raw)
    elif signature is not None:
        sent["x-alchemy-signature"] = signature
    return client.post(URL, content=raw, headers=sent)


def jobs_in(db_session):
    db_session.rollback()
    return queued(db_session)


def test_a_signed_event_wakes_the_monitors_it_names(client, db_session, settings, team):
    row = watch(db_session, settings, team[0])
    db_session.rollback()

    response = post(client, event((ADDRESS, OTHER_ADDRESS)))

    assert response.status_code == 202 and response.json() == {"success": True, "queued": 1}
    (job,) = jobs_in(db_session)
    assert job.payload == {"monitor_id": row.id, "reason": "webhook"}


def test_an_event_about_an_address_nobody_watches_is_accepted_and_does_nothing(
    client, db_session, settings, team
):
    response = post(client, event((ADDRESS, OTHER_ADDRESS)))
    assert response.status_code == 202 and response.json()["queued"] == 0
    assert jobs_in(db_session) == []


def test_the_signature_is_checked_before_anything_else_is_read(client, db_session, settings, team):
    watch(db_session, settings, team[0])
    db_session.rollback()
    payload = event((ADDRESS, ADDRESS))

    for signature in (None, "", "0" * 64, sign(b"something else"), sign(body_of(payload), "no")):
        response = post(client, payload, signature=signature)
        assert response.status_code == 401, signature
        assert response.json()["error"]["code"] == "invalid_signature"

    # a signature over different bytes (here: the same JSON with spaces) is a different body
    spaced = json.dumps(payload).encode()
    assert post(client, body=spaced, signature=sign(body_of(payload))).status_code == 401
    assert jobs_in(db_session) == []


def test_a_signature_that_is_not_ascii_is_refused_like_any_other(client, settings):
    response = client.post(
        URL,
        content=body_of(event()),
        headers=[(b"x-alchemy-signature", "é".encode("latin-1") * 64)],  # raw bytes, not ASCII
    )
    assert response.status_code == 401


def test_a_signed_body_that_is_not_an_event_is_a_422(client, settings):
    for raw in (b"not json", b"[]", b"{}", b'{"type":"MINED_TRANSACTION"}', b"\xff\xfe", b"["):
        response = post(client, body=raw)
        assert response.status_code == 422, raw
        assert response.json()["error"]["code"] == "invalid_event"


def test_a_body_nested_deeper_than_json_parsing_can_follow_is_refused_not_a_crash(
    client, app, settings
):
    app.state.settings = settings.model_copy(update={"webhook_max_body_bytes": 1_000_000})
    deep = b"[" * 200_000

    response = post(client, body=deep, signature=sign(deep))

    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_event"


def test_a_signed_body_that_arrives_in_pieces_is_put_back_together(
    client, db_session, settings, team
):
    row = watch(db_session, settings, team[0])
    db_session.rollback()
    body = body_of(event((ADDRESS, OTHER_ADDRESS)))
    pieces = [body[:20], body[20:55], body[55:]]  # no Content-Length: it is read as it comes

    response = client.post(URL, content=iter(pieces), headers={"x-alchemy-signature": sign(body)})

    assert response.status_code == 202 and response.json()["queued"] == 1
    (job,) = jobs_in(db_session)
    assert job.payload["monitor_id"] == row.id


def _request_of(*pieces, announced=None):
    """A stand-in for a request: the headers it announces and the pieces its body arrives in."""

    class Request:
        headers = {} if announced is None else {"content-length": str(announced)}
        read = 0

        async def stream(self):
            for piece in pieces:
                Request.read += 1
                yield piece

    return Request()


def test_a_body_announced_as_too_large_is_refused_before_any_of_it_is_read():
    request = _request_of(b"x" * 10, announced=5000)

    with pytest.raises(APIError) as refused:
        asyncio.run(read_body(request, 4096))

    assert refused.value.status_code == 413 and type(request).read == 0


def test_a_body_is_read_whole_however_many_pieces_it_comes_in():
    pieces = [b"abc", b"def", b"", b"ghi"]
    assert asyncio.run(read_body(_request_of(*pieces, announced=9), 9)) == b"abcdefghi"
    assert asyncio.run(read_body(_request_of(*pieces), 9)) == b"abcdefghi"  # nothing announced


def test_a_body_that_outgrows_the_limit_on_the_way_is_refused():
    with pytest.raises(APIError) as refused:
        asyncio.run(read_body(_request_of(b"x" * 3000, b"y" * 3000), 4096))
    assert refused.value.status_code == 413
    # exactly at the limit is fine
    assert len(asyncio.run(read_body(_request_of(b"x" * 2048, b"y" * 2048), 4096))) == 4096


def test_an_event_about_another_network_is_accepted_and_does_nothing(
    client, db_session, settings, team
):
    watch(db_session, settings, team[0])
    db_session.rollback()
    response = post(client, event((ADDRESS, ADDRESS), network="MATIC_MAINNET"))
    assert response.status_code == 202 and response.json()["queued"] == 0
    assert jobs_in(db_session) == []


def test_a_body_over_the_limit_is_refused_without_being_read(client, settings):
    too_big = b" " * 2049
    announced = client.post(URL, content=too_big, headers={"x-alchemy-signature": sign(too_big)})
    assert announced.status_code == 413
    assert announced.json()["error"]["code"] == "payload_too_large"

    chunks = [b" " * 1500, b" " * 1500]  # no Content-Length: it is only found out on the way
    streamed = client.post(URL, content=iter(chunks), headers={"x-alchemy-signature": "0" * 64})
    assert streamed.status_code == 413

    exactly = b" " * 2048
    at_the_limit = client.post(URL, content=exactly, headers={"x-alchemy-signature": sign(exactly)})
    assert at_the_limit.status_code == 422  # signed, read, and not an event


def test_without_a_signing_key_the_endpoint_does_not_exist(client, app, settings):
    app.state.settings = settings.model_copy(update={"alchemy_webhook_signing_key": None})
    body = body_of(event())

    response = client.post(URL, content=body, headers={"x-alchemy-signature": sign(body)})

    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"


def test_a_person_signed_in_is_not_a_provider(client, db_session, settings, team):
    from tests.access_helpers import sign_in

    db_session.rollback()
    headers = sign_in(client, "ana@example.com")
    body = body_of(event((ADDRESS, ADDRESS)))

    response = client.post(URL, content=body, headers=headers)

    assert response.status_code == 401  # who you are does not matter, only the signature


def test_the_signing_key_is_kept_out_of_the_logs_once_it_is_used(client, settings):
    from app.core.redaction import secrets

    assert KEY not in secrets
    post(client, event((ADDRESS, ADDRESS)))
    assert KEY in secrets


def test_the_doorbell_leaves_no_audit_trail_and_never_echoes_the_key(
    client, db_session, settings, team
):
    watch(db_session, settings, team[0])
    db_session.rollback()
    before = len(db_session.scalars(select(AuditLog)).all())
    db_session.rollback()  # a write session that has read holds the write lock

    ok = post(client, event((ADDRESS, ADDRESS)))
    bad = post(client, event((ADDRESS, ADDRESS)), signature="0" * 64)

    db_session.rollback()
    assert len(db_session.scalars(select(AuditLog)).all()) == before  # nothing anyone can fill up
    assert KEY not in ok.text and KEY not in bad.text
