from __future__ import annotations

import pytest
from sqlalchemy import select

from app.chains.base import AddressError, ChainFamily
from app.chains.registry import CHAINS, detect_chains, get_chain
from app.database.models import Chain, ChainAsset

SAMPLES = {
    "evm": "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    "bitcoin_p2pkh": "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
    "bitcoin_segwit": "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",
    "bitcoin_taproot": "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0",
    "tron": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
    "solana": "So11111111111111111111111111111111111111112",
}
EXPECTED_SLUGS = {
    "evm": {"ethereum", "bsc"},
    "bitcoin_p2pkh": {"bitcoin"},
    "bitcoin_segwit": {"bitcoin"},
    "bitcoin_taproot": {"bitcoin"},
    "tron": {"tron"},
    "solana": {"solana"},
}


def test_registry_identifiers_are_unique():
    ids = [spec.id for spec in CHAINS]
    slugs = [spec.slug for spec in CHAINS]
    aliases = [alias for spec in CHAINS for alias in spec.aliases]
    assert len(set(ids)) == len(ids)
    assert len(set(slugs)) == len(slugs)
    assert not set(aliases) & set(ids)


def test_chain_lookup_by_slug_id_and_alias():
    assert get_chain("ethereum").id == "eip155:1"
    assert get_chain("  ETHEREUM ").id == "eip155:1"
    assert get_chain("eip155:56").slug == "bsc"
    assert get_chain("tron:728126428").slug == "tron"
    assert get_chain("tron:0x2b6653dc").slug == "tron"  # deprecated hex alias
    assert get_chain("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp").slug == "solana"
    assert get_chain("dogecoin") is None
    # CAIP-2 references are case-sensitive; only slugs are case-insensitive.
    assert get_chain("solana:5EYKT4USFV8P8NJDTREPY1VZQKQZKVDP") is None


@pytest.mark.parametrize("name", SAMPLES)
def test_each_sample_address_is_valid_on_exactly_the_expected_chains(name):
    detection = detect_chains(SAMPLES[name])
    assert {c.chain.slug for c in detection.candidates} == EXPECTED_SLUGS[name]
    assert detection.reason is None


def test_no_address_is_valid_on_two_different_chain_families():
    for name, address in SAMPLES.items():
        families = {c.chain.family for c in detect_chains(address).candidates}
        assert len(families) == 1, name


def test_evm_chains_share_one_normalized_form():
    forms = {
        (c.check.normalized, c.check.display) for c in detect_chains(SAMPLES["evm"]).candidates
    }
    assert forms == {(SAMPLES["evm"].lower(), SAMPLES["evm"])}


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        # Shape says "Bitcoin" / "TRON" / "EVM", so the specific mistake is reported.
        ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb", AddressError.INVALID_CHECKSUM),
        ("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdr", AddressError.INVALID_CHECKSUM),
        ("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6u", AddressError.INVALID_CHECKSUM),
        ("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAe", AddressError.INVALID_LENGTH),
        ("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAEd", AddressError.INVALID_CHECKSUM),
        # No chain's shape matches, so say that rather than blaming one chain's rules.
        ("hello world", AddressError.UNRECOGNIZED_FORMAT),
        ("not-an-address", AddressError.UNRECOGNIZED_FORMAT),
        ("", AddressError.EMPTY),
        ("   ", AddressError.EMPTY),
    ],
)
def test_failures_explain_themselves_using_the_shape_of_the_input(address, reason):
    detection = detect_chains(address)
    assert detection.candidates == ()
    assert detection.reason is reason


def test_detection_can_be_limited_to_some_chains():
    only_bsc = [spec for spec in CHAINS if spec.slug == "bsc"]
    assert [c.chain.slug for c in detect_chains(SAMPLES["evm"], only_bsc).candidates] == ["bsc"]


def test_chain_families_cover_every_supported_family():
    assert {spec.family for spec in CHAINS} == set(ChainFamily)


# --- the seeded database tables must equal the in-code registry -------------------------------


def test_seeded_chains_table_matches_the_registry(db_session):
    rows = {row.id: row for row in db_session.scalars(select(Chain))}
    assert set(rows) == {spec.id for spec in CHAINS}
    for spec in CHAINS:
        row = rows[spec.id]
        assert (row.slug, row.name, row.family) == (spec.slug, spec.name, spec.family.value)
        assert (row.native_symbol, row.native_decimals) == (
            spec.native.symbol,
            spec.native.decimals,
        )
        assert row.enabled is True


def test_seeded_native_assets_match_the_registry(db_session):
    assets = {a.chain_id: a for a in db_session.scalars(select(ChainAsset))}
    assert set(assets) == {spec.id for spec in CHAINS}
    for spec in CHAINS:
        asset = assets[spec.id]
        assert asset.is_native is True
        assert (asset.asset_key, asset.standard) == ("native", "native")
        assert (asset.symbol, asset.name, asset.decimals) == (
            spec.native.symbol,
            spec.native.name,
            spec.native.decimals,
        )
