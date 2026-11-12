"""The data providers the platform can use, and what is known about each.

`chains` lists the CAIP-2 chains a provider has an endpoint for, `paid_chains` those that need a
paid plan. Plan coverage was checked against each provider's documentation on 2026-11-04 only where
the notes say so; rate limits are deliberately conservative starting points (PROVIDER_RATE_LIMITS
overrides them) because most free tiers publish no figure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from pydantic import SecretStr

from app.chains.registry import CHAINS
from app.core.config import Settings
from app.providers.base import AuthStyle, Probe, ProviderSpec

_CHAIN_ID = {spec.slug: spec.id for spec in CHAINS}
ETHEREUM = _CHAIN_ID["ethereum"]
BSC = _CHAIN_ID["bsc"]
TRON = _CHAIN_ID["tron"]
BITCOIN = _CHAIN_ID["bitcoin"]
SOLANA = _CHAIN_ID["solana"]

_JSON_RPC_BLOCK_NUMBER = {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}


def _hex_result(data: Any) -> bool:
    return isinstance(data, dict) and str(data.get("result", "")).startswith("0x")


def _has_block_id(data: Any) -> bool:
    return isinstance(data, dict) and bool(data.get("blockID"))


def _integer_result(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("result"), int)


def _positive_integer(data: Any) -> bool:
    return isinstance(data, int) and not isinstance(data, bool) and data > 0


ETHERSCAN = ProviderSpec(
    name="etherscan",
    display_name="Etherscan API V2",
    base_url="https://api.etherscan.io/v2/api",
    auth=AuthStyle.QUERY,
    auth_name="apikey",
    key_setting="etherscan_api_key",
    chains=(ETHEREUM,),
    paid_chains=(BSC,),
    default_rps=3.0,
    probe=Probe(
        "GET",
        params={"chainid": "1", "module": "proxy", "action": "eth_blockNumber"},
        check=_hex_result,
        describe="the latest Ethereum block number",
    ),
    notes=(
        "One key serves every chain; the chain is chosen with the chainid parameter. The free "
        "plan (3 calls/s, 100,000/day) covers Ethereum but not BNB Smart Chain, Base, OP or "
        "Avalanche, which need Lite or above. Calls stop at the daily cap unless paid overage "
        "is on."
    ),
)

ALCHEMY = ProviderSpec(
    name="alchemy",
    display_name="Alchemy",
    base_url="https://eth-mainnet.g.alchemy.com/v2/{key}",
    auth=AuthStyle.PATH,
    key_setting="alchemy_api_key",
    chains=(ETHEREUM, BSC, SOLANA),
    paid_chains=(BITCOIN,),
    chain_base_urls={
        ETHEREUM: "https://eth-mainnet.g.alchemy.com/v2/{key}",
        BSC: "https://bnb-mainnet.g.alchemy.com/v2/{key}",
        SOLANA: "https://solana-mainnet.g.alchemy.com/v2/{key}",
        BITCOIN: "https://bitcoin-mainnet.g.alchemy.com/v2/{key}",
    },
    default_rps=5.0,
    probe=Probe(
        "POST",
        json=_JSON_RPC_BLOCK_NUMBER,
        chain=ETHEREUM,
        check=_hex_result,
        describe="the latest Ethereum block number",
    ),
    notes=(
        "The key is part of the URL path. Bitcoin's REST address API "
        "(/api/v2/address/{address}, up to 1000 transactions a page) is enabled by purchasing a "
        "separate UTXO add-on: on the development key (checked 2026-11-12) the network answers "
        "JSON-RPC but that API answers 401 'UTXO requests are not allowed', so it is listed as "
        "paid here. BNB Smart Chain works on that key once switched on in the dashboard's Networks "
        "tab (checked 2026-11-12); Solana was not checked."
    ),
)

TRONGRID = ProviderSpec(
    name="trongrid",
    display_name="TronGrid",
    base_url="https://api.trongrid.io",
    auth=AuthStyle.HEADER,
    auth_name="TRON-PRO-API-KEY",
    key_setting="trongrid_api_key",
    chains=(TRON,),
    default_rps=5.0,
    probe=Probe(
        "POST",
        "/wallet/getnowblock",
        json={},
        check=_has_block_id,
        describe="the latest TRON block",
    ),
    notes=(
        "The documentation requires an API key (header TRON-PRO-API-KEY). Requests without one "
        "currently succeed at a low shared limit, but nothing should rely on that. Free-tier "
        "limits are unpublished: set PROVIDER_RATE_LIMITS once you know yours."
    ),
)

HELIUS = ProviderSpec(
    name="helius",
    display_name="Helius",
    base_url="https://mainnet.helius-rpc.com",
    auth=AuthStyle.QUERY,
    auth_name="api-key",
    key_setting="helius_api_key",
    chains=(SOLANA,),
    probe=Probe(
        "POST",
        json={"jsonrpc": "2.0", "id": 1, "method": "getSlot"},
        check=_integer_result,
        describe="the current Solana slot",
    ),
    notes=(
        "Solana RPC today; Parsed Events (transaction history) is added with the Solana connector. "
        "The free plan includes 1,000,000 credits a month. Its request-rate limits were not "
        "checked, so the default is cautious."
    ),
)

ESPLORA = ProviderSpec(
    name="esplora",
    display_name="Esplora (Bitcoin)",
    base_url="https://blockstream.info/api",
    auth=AuthStyle.NONE,
    chains=(BITCOIN,),
    default_rps=1.0,
    max_concurrency=2,
    probe=Probe(
        "GET",
        "/blocks/tip/height",
        check=_positive_integer,
        describe="the latest Bitcoin block height",
    ),
    notes=(
        "Public and keyless with no published limits: for development and as a fallback only. "
        "Set ESPLORA_BASE_URL to use mempool.space or your own Esplora instance."
    ),
)

PROVIDERS: Mapping[str, ProviderSpec] = {
    spec.name: spec for spec in (ETHERSCAN, ALCHEMY, TRONGRID, HELIUS, ESPLORA)
}


def build_provider_specs(settings: Settings) -> dict[str, ProviderSpec]:
    """The specs with deployment overrides applied."""
    specs = dict(PROVIDERS)
    if settings.esplora_base_url:
        specs["esplora"] = replace(specs["esplora"], base_url=settings.esplora_base_url)
    unknown = sorted(set(settings.provider_rate_limits) - set(specs))
    if unknown:
        raise ValueError(
            f"PROVIDER_RATE_LIMITS names unknown providers: {', '.join(unknown)} "
            f"(known: {', '.join(sorted(specs))})"
        )
    return specs


def provider_keys(
    settings: Settings, specs: Mapping[str, ProviderSpec]
) -> dict[str, SecretStr | None]:
    return {
        name: getattr(settings, spec.key_setting)
        for name, spec in specs.items()
        if spec.key_setting
    }
