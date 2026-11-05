"""Which chains the platform reads, the settings each needs and the adapter each gets."""

from __future__ import annotations

import pytest

from app.chains.evm.adapter import EvmAdapter
from app.chains.support import SUPPORTED, build_adapter, find_chain, keys_needed
from app.chains.tron.adapter import TronAdapter
from app.chains.tron.chains import TRON_CHAIN
from tests.rigs import Rig


@pytest.mark.parametrize(
    ("name", "chain_id"),
    [
        ("ethereum", "eip155:1"),
        ("Ethereum", "eip155:1"),
        ("eip155:1", "eip155:1"),
        ("bsc", "eip155:56"),
        (" bsc ", "eip155:56"),
        ("eip155:56", "eip155:56"),
        ("tron", TRON_CHAIN.chain_id),
        ("TRON", TRON_CHAIN.chain_id),
        (TRON_CHAIN.chain_id, TRON_CHAIN.chain_id),
    ],
)
def test_chains_are_found_by_slug_or_caip2_id(name, chain_id):
    assert find_chain(name).chain_id == chain_id


@pytest.mark.parametrize("name", ["bitcoin", "solana", "eip155:137", "dogecoin", "", "EIP155:1"])
def test_chains_the_platform_cannot_read_are_not_found(name):
    assert find_chain(name) is None


def test_every_supported_slug_is_a_distinct_readable_chain():
    chains = [find_chain(slug) for slug in SUPPORTED]
    assert all(chains) and len({chain.chain_id for chain in chains}) == len(SUPPORTED)
    assert [chain.slug for chain in chains] == list(SUPPORTED)


def test_the_message_names_the_settings_that_would_make_a_chain_readable():
    assert keys_needed(find_chain("tron")) == "TRONGRID_API_KEY"
    assert keys_needed(find_chain("ethereum")) == "ETHERSCAN_API_KEY or ALCHEMY_API_KEY"


def test_for_bnb_an_etherscan_key_alone_is_not_offered_as_the_fix():
    message = keys_needed(find_chain("bsc"))
    assert message.startswith("ALCHEMY_API_KEY") and "ETHERSCAN_PAID_PLAN=true" in message


def test_the_adapter_is_the_one_for_the_chains_family():
    rig = Rig()
    tron = build_adapter(find_chain("tron"), rig.providers)
    ethereum = build_adapter(find_chain("ethereum"), rig.providers)
    assert isinstance(tron, TronAdapter) and tron.source_names == ["trongrid"]
    assert isinstance(ethereum, EvmAdapter) and ethereum.source_names == ["etherscan", "alchemy"]


def test_the_paid_plan_setting_reaches_the_evm_adapter():
    rig = Rig()
    bnb = find_chain("bsc")
    assert build_adapter(bnb, rig.providers).source_names == ["alchemy"]
    assert build_adapter(bnb, rig.providers, etherscan_paid_plan=True).source_names == [
        "etherscan",
        "alchemy",
    ]
