"""A platform editor and the incidents they write, for the tests of both sides of the incidents."""

from __future__ import annotations

import copy

from sqlalchemy import update

from app.access.permissions import Role
from app.database.models import User
from tests.access_helpers import person

EDITOR_ROUTES = "/api/v1/editor/incidents"
PUBLIC_ROUTES = "/api/v1/public/incidents"
EVM = "0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed"
EVM_CHECKSUMMED = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"

DOCUMENT = {
    "title": "A theft",
    "summary": "Funds were reported taken from a wallet.",
    "kind": "theft",
    "occurred_on": "2026-01-15",
    "attribution_note": "Attributed by an analytics firm; no authority has confirmed it.",
    "internal_note": "Waiting for a second source.",
    "evidence": [
        {
            "ref": "e1",
            "publisher": "An analytics firm",
            "title": "What was taken",
            "url": "https://example.org/report",
            "kind": "analytics",
            "retrieved_on": "2026-01-16",
            "published_on": "2026-01-15",
        }
    ],
    "amounts": [{"evidence_ref": "e1", "amount_usd_low": 5000000, "amount_usd_high": 6000000}],
    "addresses": [
        {
            "chain": "ethereum",
            "address": EVM,
            "role": "ATTACKER_FUNDS_RECEIVER",
            "attribution_status": "REPORTED_BY_ANALYST",
            "evidence_ref": "e1",
        }
    ],
    "updates": [{"occurred_at": "2026-01-15T10:00:00Z", "kind": "event", "body": "Noticed."}],
}


def body(**changes):
    """The content of an incident, as the editor sends it, with some parts changed."""
    made = copy.deepcopy(DOCUMENT)
    made.update(changes)
    return made


def make_editor(client, db_session, email: str = "editor@example.com") -> dict[str, str]:
    """The headers of a platform editor (an administrator who has been made one)."""
    _, headers = person(client, db_session, Role.ADMIN, email=email)
    db_session.execute(update(User).where(User.email == email).values(is_platform_editor=True))
    db_session.commit()
    db_session.rollback()
    return headers


def create(client, editor: dict[str, str], slug: str = "a-theft", **changes) -> dict:
    response = client.post(EDITOR_ROUTES, json={"slug": slug, **body(**changes)}, headers=editor)
    assert response.status_code == 201, response.text
    return response.json()["incident"]


def move(client, editor: dict[str, str], number: int, *states: str) -> dict:
    for state in states:
        response = client.post(
            f"{EDITOR_ROUTES}/{number}/transition", json={"to": state}, headers=editor
        )
        assert response.status_code == 200, response.text
    return response.json()["incident"]


def publish(client, editor: dict[str, str], number: int) -> dict:
    return move(client, editor, number, "in_review", "published")
