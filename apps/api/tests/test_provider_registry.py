"""The provider catalogue, its settings, and that the catalogue and the documentation agree."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from app.chains.registry import CHAINS, get_chain
from app.core.config import API_ROOT, Settings
from app.providers.base import AuthStyle
from app.providers.registry import PROVIDERS, build_provider_specs, provider_keys


def make(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# --- the catalogue -----------------------------------------------------------------------------


def test_provider_names_are_unique_and_keyed_by_name():
    assert list(PROVIDERS) == ["etherscan", "alchemy", "trongrid", "helius", "esplora"]
    assert all(name == spec.name for name, spec in PROVIDERS.items())


@pytest.mark.parametrize("spec", PROVIDERS.values(), ids=lambda spec: spec.name)
def test_every_spec_is_internally_consistent(spec):
    urls = [spec.base_url, *spec.chain_base_urls.values()]

    # Credentials: the key setting exists, and the way it is sent is fully described.
    if spec.auth is AuthStyle.NONE:
        assert spec.key_setting is None
    else:
        assert spec.key_setting in Settings.model_fields
    if spec.auth in (AuthStyle.QUERY, AuthStyle.HEADER):
        assert spec.auth_name
    assert all(("{key}" in url) == (spec.auth is AuthStyle.PATH) for url in urls)
    assert all(url.startswith("https://") for url in urls)

    # Chains are ones the platform knows, and are either free or paid, not both.
    for chain_id in (*spec.chains, *spec.paid_chains, *spec.chain_base_urls):
        assert get_chain(chain_id) is not None, chain_id
    assert not set(spec.chains) & set(spec.paid_chains)

    assert spec.default_rps > 0 and spec.max_concurrency >= 1
    assert spec.probe is not None and spec.notes
    if spec.chain_base_urls:  # one host per chain: the probe must say which
        assert spec.probe.chain in spec.chain_base_urls


def test_every_supported_chain_has_a_provider_that_needs_no_paid_plan():
    for chain in CHAINS:
        assert any(chain.id in spec.chains for spec in PROVIDERS.values()), chain.slug


def test_the_recorded_plan_facts_about_bnb_and_bitcoin():
    # BNB Smart Chain is not on Etherscan's free plan; Alchemy's Bitcoin address API is an add-on.
    assert get_chain("bsc").id in PROVIDERS["etherscan"].paid_chains
    assert get_chain("bitcoin").id in PROVIDERS["alchemy"].paid_chains
    assert PROVIDERS["esplora"].auth is AuthStyle.NONE


@pytest.mark.parametrize(
    ("provider", "healthy", "unhealthy"),
    [
        (
            "etherscan",
            {"jsonrpc": "2.0", "id": 83, "result": "0x14e6a8f"},
            {"status": "0", "message": "NOTOK", "result": "Invalid API Key"},
        ),
        (
            "alchemy",
            {"jsonrpc": "2.0", "id": 1, "result": "0x14e6a8f"},
            {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "bad"}},
        ),
        ("trongrid", {"blockID": "00000000053aa2ee"}, {"Error": "bad request"}),
        (
            "helius",
            {"jsonrpc": "2.0", "id": 1, "result": 412345678},
            {"jsonrpc": "2.0", "error": {"code": -32401, "message": "invalid api key"}},
        ),
        ("esplora", 974631, "Too many requests"),
    ],
)
def test_probe_checks_tell_a_healthy_answer_from_an_error_that_arrived_with_http_200(
    provider, healthy, unhealthy
):
    check = PROVIDERS[provider].probe.check
    assert check(healthy) is True
    assert check(unhealthy) is False
    assert check(None) is False


# --- deployment overrides ----------------------------------------------------------------------


def test_the_esplora_endpoint_can_be_pointed_elsewhere():
    assert build_provider_specs(make())["esplora"].base_url == PROVIDERS["esplora"].base_url
    specs = build_provider_specs(make(esplora_base_url="https://mempool.space/api"))
    assert specs["esplora"].base_url == "https://mempool.space/api"
    assert PROVIDERS["esplora"].base_url == "https://blockstream.info/api"  # the default is intact


def test_a_rate_limit_for_a_provider_that_does_not_exist_is_a_startup_error():
    with pytest.raises(ValueError, match="unknown providers: etherscn"):
        build_provider_specs(make(provider_rate_limits={"etherscn": 1}))


def test_provider_keys_come_from_settings_and_only_for_providers_that_take_one():
    specs = build_provider_specs(make())
    keys = provider_keys(make(etherscan_api_key="abcdefgh12345678"), specs)
    assert set(keys) == {"etherscan", "alchemy", "trongrid", "helius"}
    assert keys["etherscan"].get_secret_value() == "abcdefgh12345678"
    assert keys["alchemy"] is None


# --- settings ----------------------------------------------------------------------------------


def test_provider_keys_are_secret_and_a_blank_key_means_not_configured():
    settings = make(etherscan_api_key="", alchemy_api_key="   ", trongrid_api_key="abc12345")
    assert settings.etherscan_api_key is None and settings.alchemy_api_key is None
    assert settings.trongrid_api_key.get_secret_value() == "abc12345"
    assert "abc12345" not in repr(settings) and "abc12345" not in str(settings.model_dump())


def test_rate_limits_are_read_from_json_in_the_environment(monkeypatch):
    monkeypatch.setenv("PROVIDER_RATE_LIMITS", '{"etherscan": 2.5, "trongrid": 10}')
    assert make().provider_rate_limits == {"etherscan": 2.5, "trongrid": 10.0}


def test_rate_limits_must_be_positive():
    with pytest.raises(ValidationError, match="greater than zero"):
        make(provider_rate_limits={"etherscan": 0})


def test_a_job_must_time_out_before_its_lock_does():
    with pytest.raises(ValidationError, match="JOB_LOCK_TIMEOUT_SECONDS must be greater"):
        make(job_timeout_seconds=600, job_lock_timeout_seconds=600)
    assert make(job_timeout_seconds=100, job_lock_timeout_seconds=101)


def test_the_esplora_url_must_be_http_and_blank_means_the_default():
    with pytest.raises(ValidationError, match="http"):
        make(esplora_base_url="ftp://example.com")
    assert make(esplora_base_url="").esplora_base_url is None


# --- documentation -----------------------------------------------------------------------------


def test_env_example_documents_every_setting_except_two_deliberately_internal_ones():
    text = (API_ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text, flags=re.MULTILINE))
    internal = {"DATA_DIR", "JWT_ALGORITHM"}
    wanted = {name.upper() for name in Settings.model_fields} - internal
    assert wanted <= documented, f"missing from .env.example: {sorted(wanted - documented)}"
