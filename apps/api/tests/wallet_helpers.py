"""The editor's wallet profiles, for the tests of both sides of them."""

from __future__ import annotations

import copy

from tests.incident_helpers import EVM, make_editor  # noqa: F401  (the same editor writes both)

EDITOR_ROUTES = "/api/v1/editor/wallets"
PUBLIC_ROUTES = "/api/v1/public/wallets"
OTHER = "0x" + "cd" * 20
TRON = "TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy"

DOCUMENT = {
    "name": "Example Exchange",
    "kind": "exchange",
    "summary": "A centralized exchange.",
    "website": "https://example.org",
    "attribution_note": "Addresses as the exchange publishes them.",
    "internal_note": "Re-check the cold wallet next quarter.",
    "evidence": [
        {
            "ref": "e1",
            "publisher": "The exchange itself",
            "title": "Our wallet addresses",
            "url": "https://example.org/proof-of-reserves",
            "kind": "official",
            "retrieved_on": "2026-01-16",
            "published_on": "2026-01-15",
        }
    ],
    "addresses": [
        {
            "chain": "ethereum",
            "address": EVM,
            "label": "Hot wallet",
            "attribution_status": "OFFICIALLY_CONFIRMED",
            "evidence_ref": "e1",
        }
    ],
}


def body(**changes):
    """The content of a profile, as the editor sends it, with some parts changed."""
    made = copy.deepcopy(DOCUMENT)
    made.update(changes)
    return made


def create(client, editor: dict[str, str], slug: str = "example-exchange", **changes) -> dict:
    response = client.post(EDITOR_ROUTES, json={"slug": slug, **body(**changes)}, headers=editor)
    assert response.status_code == 201, response.text
    return response.json()["wallet"]


def move(client, editor: dict[str, str], number: int, *states: str) -> dict:
    for state in states:
        response = client.post(
            f"{EDITOR_ROUTES}/{number}/transition", json={"to": state}, headers=editor
        )
        assert response.status_code == 200, response.text
    return response.json()["wallet"]


def publish(client, editor: dict[str, str], number: int) -> dict:
    return move(client, editor, number, "in_review", "published")
