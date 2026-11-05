"""The operator commands for providers and the job queue."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app import cli
from app.core.config import Settings, get_settings
from app.database.models import FetchJob
from tests.conftest import TEST_SECRET

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
