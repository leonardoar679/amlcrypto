from __future__ import annotations

import pytest
from sqlalchemy import update

from app.database.models import Chain
from app.services.users import create_user

PASSWORD = "correct horse battery staple"
ETH_ADDRESS = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"


@pytest.fixture
def auth(client, db_session) -> dict[str, str]:
    create_user(
        db_session, email="analyst@example.com", password=PASSWORD, organization_name="Acme"
    )
    token = client.post(
        "/api/v1/auth/login", json={"email": "analyst@example.com", "password": PASSWORD}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def validate(client, auth, address, chain=None):
    body = {"address": address} if chain is None else {"address": address, "chain": chain}
    return client.post("/api/v1/address/validate", json=body, headers=auth)


# --- authentication --------------------------------------------------------------------------


def test_both_endpoints_require_authentication(client):
    assert client.get("/api/v1/chains").status_code == 401
    assert client.post("/api/v1/address/validate", json={"address": ETH_ADDRESS}).status_code == 401


# --- GET /chains -----------------------------------------------------------------------------


def test_chains_are_listed_in_registry_order(client, auth):
    response = client.get("/api/v1/chains", headers=auth)
    assert response.status_code == 200
    chains = response.json()
    assert [c["slug"] for c in chains] == ["ethereum", "bsc", "tron", "bitcoin", "solana"]
    assert chains[1] == {
        "id": "eip155:56",
        "slug": "bsc",
        "name": "BNB Smart Chain",
        "family": "evm",
        "native_symbol": "BNB",
        "native_decimals": 18,
        "enabled": True,
    }


def test_a_disabled_chain_is_still_listed_but_flagged(client, auth, db_session):
    db_session.execute(update(Chain).where(Chain.slug == "bsc").values(enabled=False))
    db_session.commit()
    chains = {c["slug"]: c for c in client.get("/api/v1/chains", headers=auth).json()}
    assert chains["bsc"]["enabled"] is False
    assert chains["ethereum"]["enabled"] is True


# --- POST /address/validate, chain given ------------------------------------------------------


def test_valid_evm_address_on_a_named_chain(client, auth):
    body = validate(client, auth, ETH_ADDRESS.lower(), "ethereum").json()
    assert body["valid"] is True
    assert body["chain"] == "ethereum"
    assert body["chain_id"] == "eip155:1"
    assert body["normalized"] == ETH_ADDRESS.lower()
    assert body["display"] == ETH_ADDRESS
    assert body["reason"] is None


def test_invalid_address_reports_a_reason_and_message(client, auth):
    mistyped = ETH_ADDRESS.replace("a", "A", 1)
    body = validate(client, auth, mistyped, "ethereum").json()
    assert body["valid"] is False
    assert body["reason"] == "invalid_checksum"
    assert "checksum" in body["message"]
    assert body["normalized"] is None


def test_chain_can_be_named_by_caip2_id_or_the_tron_hex_alias(client, auth):
    tron = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    for key in ("tron", "tron:728126428", "tron:0x2b6653dc"):
        body = validate(client, auth, tron, key).json()
        assert (body["valid"], body["chain"]) == (True, "tron"), key


def test_an_address_for_the_wrong_chain_is_invalid_not_an_error(client, auth):
    body = validate(client, auth, ETH_ADDRESS, "bitcoin").json()
    assert body["valid"] is False
    assert body["chain"] == "bitcoin"


def test_unknown_chain_is_a_404_with_the_error_envelope(client, auth):
    response = validate(client, auth, ETH_ADDRESS, "dogecoin")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_chain"


def test_disabled_chain_is_rejected(client, auth, db_session):
    db_session.execute(update(Chain).where(Chain.slug == "tron").values(enabled=False))
    db_session.commit()
    response = validate(client, auth, "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "tron")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "chain_disabled"


# --- POST /address/validate, chain detected ---------------------------------------------------


def test_evm_address_without_a_chain_lists_every_evm_chain(client, auth):
    body = validate(client, auth, ETH_ADDRESS).json()
    assert body["valid"] is True
    assert body["chain"] is None  # ambiguous: the caller must choose
    assert [c["chain"] for c in body["candidates"]] == ["ethereum", "bsc"]
    assert body["normalized"] == ETH_ADDRESS.lower()
    assert body["display"] == ETH_ADDRESS


@pytest.mark.parametrize(
    ("address", "chain", "kind"),
    [
        ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "bitcoin", "p2pkh"),
        ("bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0", "bitcoin", "p2tr"),
        ("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "tron", None),
        ("So11111111111111111111111111111111111111112", "solana", None),
    ],
)
def test_non_evm_addresses_resolve_to_a_single_chain(client, auth, address, chain, kind):
    body = validate(client, auth, address).json()
    assert body["valid"] is True
    assert (body["chain"], body["kind"]) == (chain, kind)
    assert len(body["candidates"]) == 1


def test_detection_ignores_disabled_chains(client, auth, db_session):
    db_session.execute(update(Chain).where(Chain.slug == "bsc").values(enabled=False))
    db_session.commit()
    body = validate(client, auth, ETH_ADDRESS).json()
    assert body["chain"] == "ethereum"
    assert [c["chain"] for c in body["candidates"]] == ["ethereum"]


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb", "invalid_checksum"),
        ("not-an-address", "unrecognized_format"),
        ("", "empty"),
    ],
)
def test_undetectable_addresses_explain_why(client, auth, address, reason):
    body = validate(client, auth, address).json()
    assert body["valid"] is False
    assert body["reason"] == reason
    assert body["message"]
    assert body["candidates"] == []


def test_oversized_addresses_are_refused_at_the_door(client, auth):
    response = validate(client, auth, "1" * 600)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
