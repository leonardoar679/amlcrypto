"""The operator commands for providers and the job queue."""

from __future__ import annotations

import json

import httpx2
import pytest
from sqlalchemy import select

from app import cli
from app.core.config import Settings, get_settings
from app.database.models import FetchJob
from app.providers.service import ProviderService
from tests.conftest import TEST_SECRET
from tests.fake_etherscan import KEY as FAKE_ETHERSCAN_KEY
from tests.fake_etherscan import ME, OTHER, tx_row
from tests.helpers import load_fixture
from tests.rigs import ALCHEMY_KEY, Rig

KEY = "etherscan-key-9f8e7d6c"


@pytest.fixture
def cli_env(monkeypatch, database_url):
    """Point the commands at the test database, ignoring any real apps/api/.env."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def test_providers_lists_what_is_configured_without_revealing_keys(cli_env, capsys):
    cli_env.setenv("ETHERSCAN_API_KEY", KEY)

    assert cli.main(["providers"]) == 0

    out = capsys.readouterr().out
    rows = {line.split()[0]: line.split()[1:] for line in out.splitlines()[1:]}
    assert rows["etherscan"] == ["set", "3", "ethereum", "[bsc]"]
    assert rows["alchemy"] == ["MISSING", "5", "ethereum", "bsc", "solana", "[bitcoin]"]
    assert rows["esplora"] == ["none", "1", "bitcoin"]
    assert KEY not in out


def test_probing_an_unknown_provider_lists_the_known_ones(cli_env, capsys):
    assert cli.main(["probe", "nope"]) == 1
    err = capsys.readouterr().err
    assert "unknown provider" in err and "etherscan" in err and "esplora" in err


def test_enqueue_adds_a_job_and_refuses_a_duplicate(cli_env, capsys, committing_db):
    args = [
        "enqueue",
        "PROVIDER_PROBE",
        "--payload",
        '{"provider": "esplora"}',
        "--dedupe-key",
        "k",
    ]

    assert cli.main(args) == 0
    assert "Queued job" in capsys.readouterr().out
    assert cli.main(args) == 0
    assert "already waiting" in capsys.readouterr().out

    with committing_db.read() as session:
        (job,) = session.scalars(select(FetchJob)).all()
    assert (job.job_type, job.payload, job.status, job.max_attempts) == (
        "PROVIDER_PROBE",
        {"provider": "esplora"},
        "QUEUED",
        3,
    )


def test_enqueue_can_build_the_payload_from_key_value_pairs(cli_env, capsys, committing_db):
    args = ["enqueue", "PROVIDER_PROBE", "--payload", '{"a": 1}', "--set", "provider=esplora"]
    assert cli.main([*args, "--set", "note=x=y"]) == 0

    with committing_db.read() as session:
        (job,) = session.scalars(select(FetchJob)).all()
    assert job.payload == {"a": 1, "provider": "esplora", "note": "x=y"}


@pytest.mark.parametrize("pair", ["oops", "=value"])
def test_enqueue_rejects_a_malformed_key_value_pair(cli_env, capsys, pair):
    assert cli.main(["enqueue", "PROVIDER_PROBE", "--set", pair]) == 1
    assert "KEY=VALUE" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("payload", "complaint"),
    [("{not json", "not valid JSON"), ("[1, 2]", "must be a JSON object")],
)
def test_enqueue_rejects_a_payload_that_is_not_a_json_object(cli_env, capsys, payload, complaint):
    assert cli.main(["enqueue", "PROVIDER_PROBE", "--payload", payload]) == 1
    assert complaint in capsys.readouterr().err


# --- history and tx ----------------------------------------------------------------------------


@pytest.fixture
def providers(cli_env, monkeypatch, committing_db):
    """The CLI's provider service, pointed at the in-memory Etherscan and Alchemy.

    The CLI counts its requests in the real database, which is shared by every test on MySQL;
    `committing_db` removes those usage rows again afterwards.
    """
    rig = Rig()
    cli_env.setenv("ETHERSCAN_API_KEY", FAKE_ETHERSCAN_KEY)
    cli_env.setenv("ALCHEMY_API_KEY", ALCHEMY_KEY)
    real = ProviderService.from_settings.__func__

    def from_settings(cls, settings, **options):
        return real(cls, settings, transport=httpx2.MockTransport(rig.route), **options)

    monkeypatch.setattr(ProviderService, "from_settings", classmethod(from_settings))
    return rig


def test_history_prints_the_normalized_transfers_and_how_complete_they_are(providers, capsys):
    providers.etherscan.add("txlist", tx_row(5, OTHER, ME, value="1500000000000000000"))

    assert cli.main(["history", "ethereum", ME]) == 0

    out = capsys.readouterr().out
    assert "1 transfers (native 1)" in out and "1.5 ETH" in out
    assert f"Complete through block {providers.etherscan.head}." in out


def test_history_as_json_carries_every_transfer_and_the_summary(providers, capsys):
    providers.etherscan.add("txlist", *[tx_row(b, OTHER, ME, value="1") for b in (5, 6)])

    assert cli.main(["history", "ethereum", ME, "--json"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["complete"] is True and document["transfers"] == 2
    assert [t["block_number"] for t in document["transfers_list"]] == [5, 6]
    assert document["transfers_list"][0]["amount_raw"] == "1"


def test_a_read_that_stops_early_exits_with_status_two_so_scripts_can_tell(providers, capsys):
    providers.etherscan.add("txlist", *[tx_row(b, OTHER, ME, value="1") for b in range(1, 300)])

    assert cli.main(["history", "ethereum", ME, "--max-items", "5"]) == 2

    assert "INCOMPLETE" in capsys.readouterr().out


def test_history_refuses_what_cannot_work_before_spending_any_request(providers, capsys):
    assert cli.main(["history", "bitcoin", ME]) == 1
    assert "not a chain this platform reads" in capsys.readouterr().err
    assert cli.main(["history", "ethereum", "0x123"]) == 1
    assert "not a valid ethereum address" in capsys.readouterr().err
    assert providers.etherscan.requests == [] and providers.alchemy.requests == []


def test_history_without_any_key_says_which_to_set(cli_env, capsys):
    assert cli.main(["history", "ethereum", ME]) == 1
    assert "ETHERSCAN_API_KEY or ALCHEMY_API_KEY" in capsys.readouterr().err


def test_a_provider_failure_is_reported_as_an_error_line_not_a_traceback(providers, capsys):
    invalid = {"status": "0", "message": "NOTOK", "result": "Invalid API Key"}
    unauthorized = {"jsonrpc": "2.0", "id": 1, "error": {"code": 401, "message": "Unauthorized"}}
    providers.etherscan.queued = [invalid] * 10  # a rejected key is rejected on every call
    providers.alchemy.queued = [unauthorized] * 10

    assert cli.main(["history", "ethereum", ME]) == 1

    err = capsys.readouterr().err
    assert err.startswith("error:") and "rejected" in err and "Traceback" not in err


def test_tx_decodes_a_transaction_from_its_receipt(providers, capsys):
    data = load_fixture("evm/ethereum_erc20_single")
    tx_hash = data["tx"]["hash"]
    providers.etherscan.transactions[tx_hash] = data["tx"]
    providers.etherscan.receipts[tx_hash] = data["receipt"]

    assert cli.main(["tx", "ethereum", tx_hash]) == 0
    out = capsys.readouterr().out
    assert tx_hash in out and "Movements proven by the receipt:" in out

    assert cli.main(["tx", "ethereum", tx_hash, "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["transaction"]["status"] == "success"
    assert document["transfers"][0]["transfer_type"] == "erc20"


def test_tx_says_when_nothing_has_it_and_rejects_a_malformed_hash(providers, capsys):
    assert cli.main(["tx", "ethereum", "0x" + "ab" * 32]) == 1
    assert "Not found" in capsys.readouterr().err
    assert cli.main(["tx", "ethereum", "0x1234"]) == 1
    assert "not a transaction hash" in capsys.readouterr().err
