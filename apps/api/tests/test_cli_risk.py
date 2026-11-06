"""The screen and risk commands."""

from __future__ import annotations

import json

import httpx2
import pytest

from app import cli
from app.core.config import Settings, get_settings
from app.database.repositories import risk as store
from app.providers.service import ProviderService
from app.services.users import create_user
from tests.conftest import TEST_SECRET
from tests.fake_etherscan import KEY as FAKE_ETHERSCAN_KEY
from tests.fake_etherscan import ME, OTHER, tx_row
from tests.graph_helpers import addr
from tests.graph_store import label_address, move, seed
from tests.helpers import load_fixture
from tests.rigs import ALCHEMY_KEY, Rig

BAD = addr(0xB1)


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


@pytest.fixture
def providers(cli_env, monkeypatch, committing_db):
    """The CLI's provider service, pointed at the in-memory providers."""
    rig = Rig()
    cli_env.setenv("ETHERSCAN_API_KEY", FAKE_ETHERSCAN_KEY)
    cli_env.setenv("ALCHEMY_API_KEY", ALCHEMY_KEY)
    real = ProviderService.from_settings.__func__

    def from_settings(cls, settings, **options):
        return real(cls, settings, transport=httpx2.MockTransport(rig.route), **options)

    monkeypatch.setattr(ProviderService, "from_settings", classmethod(from_settings))
    return rig


def run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- risk rules ----------------------------------------------------------------------------------


def test_the_rules_are_listed(cli_env, capsys, committing_db):
    code, out, _ = run(capsys, "risk", "rules")

    assert code == 0
    assert out.count("AML-0") == 15 and "Direct sanctions exposure" in out
    assert "OFF" not in out and "changed from the defaults" not in out


def test_the_rules_as_json_carry_every_field(cli_env, capsys, committing_db):
    code, out, _ = run(capsys, "risk", "rules", "--json")

    rules = json.loads(out)
    assert code == 0 and len(rules) == 15
    assert {"rule_id", "enabled", "version", "config", "defaults", "supported_chains"} <= set(
        rules[0]
    )


def test_a_rule_can_be_switched_off_and_have_a_setting_changed_and_then_reset(
    cli_env, capsys, committing_db
):
    code, out, _ = run(
        capsys, "risk", "rule", "set", "AML-004", "--disable", "--set", "window_seconds=600"
    )
    assert code == 0 and "AML-004" in out and "OFF" in out

    _, listing, _ = run(capsys, "risk", "rules")
    assert "AML-004  OFF" in listing
    assert 'changed from the defaults: {"window_seconds": 600}' in listing

    # A later change keeps the earlier ones.
    run(capsys, "risk", "rule", "set", "AML-004", "--enable", "--set", "min_ratio=0.5")
    rules = {r["rule_id"]: r for r in json.loads(run(capsys, "risk", "rules", "--json")[1])}
    assert rules["AML-004"]["enabled"] is True
    assert rules["AML-004"]["config"]["window_seconds"] == 600
    assert rules["AML-004"]["config"]["min_ratio"] == 0.5

    code, out, _ = run(capsys, "risk", "rule", "reset", "AML-004")
    assert code == 0 and "back to the defaults" in out
    assert run(capsys, "risk", "rule", "reset", "AML-004")[1].strip().endswith("had no override")
    assert "changed from" not in run(capsys, "risk", "rules")[1]


def test_a_setting_may_be_a_json_value(cli_env, capsys, committing_db):
    code, _, _ = run(
        capsys, "risk", "rule", "set", "AML-015", "--set", 'thresholds={"native": [1000]}'
    )

    rules = {r["rule_id"]: r for r in json.loads(run(capsys, "risk", "rules", "--json")[1])}
    assert code == 0 and rules["AML-015"]["config"]["thresholds"] == {"native": [1000]}


@pytest.mark.parametrize(
    "argv,complaint",
    [
        (["risk", "rule", "set", "AML-999", "--disable"], "unknown rule"),
        (["risk", "rule", "set", "AML-004", "--set", "nope=1"], "has no setting 'nope'"),
        (["risk", "rule", "set", "AML-004", "--set", "window_seconds=soon"], "must be like"),
        (["risk", "rule", "set", "AML-004", "--set", "window_seconds"], "KEY=VALUE"),
        (["risk", "rule", "reset", "AML-999"], "unknown rule"),
    ],
)
def test_a_change_that_makes_no_sense_is_refused_and_nothing_is_kept(
    cli_env, capsys, committing_db, argv, complaint
):
    code, _, err = run(capsys, *argv)

    assert code == 1 and complaint in err
    assert "changed from" not in run(capsys, "risk", "rules")[1]


def test_enabling_and_disabling_together_is_refused(cli_env, capsys, committing_db):
    with pytest.raises(SystemExit):
        cli.main(["risk", "rule", "set", "AML-004", "--enable", "--disable"])


# --- screen address ------------------------------------------------------------------------------


def stored_history(committing_db, *, flagged=True):
    with committing_db.write() as session:
        seed(session, ME, [move(BAD, ME, str(10**18))])
        if flagged:
            label_address(session, BAD, "SANCTIONS")


def test_screening_a_flagged_address_from_storage_prints_the_report_and_exits_2(
    cli_env, capsys, committing_db
):
    stored_history(committing_db)

    code, out, _ = run(capsys, "screen", "address", "ethereum", ME, "--no-update")

    assert code == 2
    assert "risk score 100/100  CRITICAL" in out and "policy override: AML-001" in out
    assert "Findings (1):" in out and "AML-001 CRITICAL" in out
    assert "from ofac_sdn" in out and "transactions: 0x" in out
    assert "not counted twice: AML-007 (same event as AML-001)" in out
    assert "Rules that could not be judged" in out and "AML-015" in out


def test_screening_an_address_nobody_flags_exits_0_when_the_data_is_complete(
    cli_env, capsys, committing_db
):
    stored_history(committing_db, flagged=False)

    code, out, _ = run(capsys, "screen", "address", "ethereum", ME, "--no-update")

    assert code == 0 and "risk score 0/100  LOW" in out and "No findings." in out
    assert "data coverage: COMPLETE" in out


def test_with_no_stored_history_there_is_no_score_and_it_is_not_a_clearance(
    cli_env, capsys, committing_db
):
    code, out, _ = run(capsys, "screen", "address", "ethereum", ME, "--no-update")

    assert code == 3
    assert "risk score: none: too little data was held to judge" in out
    assert "INSUFFICIENT" in out and "This is not a clearance" in out


def test_the_report_as_json(cli_env, capsys, committing_db):
    stored_history(committing_db)

    code, out, _ = run(capsys, "screen", "address", "ethereum", ME, "--no-update", "--json")

    body = json.loads(out)
    assert code == 2 and body["risk_score"] == 100 and body["outcome"] == "findings"
    assert body["findings"][0]["rule_id"] == "AML-001"
    assert len(body["rules"]) == 15 and body["subject"]["id"] == ME


def test_an_operators_rule_changes_apply_to_the_screening(cli_env, capsys, committing_db):
    stored_history(committing_db)
    run(capsys, "risk", "rule", "set", "AML-001", "--disable")
    run(capsys, "risk", "rule", "set", "AML-007", "--disable")

    code, out, _ = run(capsys, "screen", "address", "ethereum", ME, "--no-update", "--json")

    body = json.loads(out)
    assert code == 0 and body["findings"] == [] and body["risk_score"] == 0
    reasons = {r["rule_id"]: r["reason"] for r in body["rules"]}
    assert reasons["AML-001"].startswith("switched off")


def test_screening_with_an_update_reads_the_providers_first(providers, capsys):
    providers.etherscan.add("txlist", tx_row(5, OTHER, ME, value="1500000000000000000"))

    code, out, _ = run(capsys, "screen", "address", "ethereum", ME, "--days", "3650", "--json")

    body = json.loads(out)
    assert code == 0 and body["data_coverage"] == "COMPLETE"
    assert body["analysis"]["transactions_checked"] == 1
    assert body["analysis"]["graph"]["requests_used"] > 0
    assert providers.etherscan.requests


def test_a_screening_can_be_kept_for_an_organization_and_the_api_can_then_show_it(
    cli_env, capsys, committing_db
):
    stored_history(committing_db)
    with committing_db.write() as session:
        user = create_user(
            session, email="a@example.com", password="pw-pw-pw-pw-1", organization_name="Acme"
        )
        organization = user.organization_id

    code, out, _ = run(
        capsys, "screen", "address", "ethereum", ME, "--no-update", "--store-in", "acme"
    )

    assert code == 2 and "kept as scr_" in out and "for organization acme" in out
    with committing_db.read() as session:
        (row,) = store.completed_assessments(session, organization, "eip155:1", ME)
        assert row.risk_score == 100 and row.report["findings"][0]["rule_id"] == "AML-001"
        assert row.options["update"] is False


def test_an_organization_that_does_not_exist_is_refused_before_anything_is_read(providers, capsys):
    code, _, err = run(
        capsys, "screen", "address", "ethereum", ME, "--store-in", "nobody", "--days", "3650"
    )

    assert code == 1 and "no organization 'nobody'" in err
    assert providers.etherscan.requests == [] and providers.alchemy.requests == []


def test_screening_refuses_what_cannot_work_before_spending_a_request(providers, capsys):
    assert run(capsys, "screen", "address", "dogecoin", ME)[0] == 1
    code, _, err = run(capsys, "screen", "address", "ethereum", "0x123")
    assert code == 1 and "not a valid ethereum address" in err
    code, _, err = run(capsys, "screen", "address", "ethereum", ME, "--no-update", "--refresh")
    assert code == 1 and "drop --no-update" in err
    assert providers.etherscan.requests == []


def test_screening_with_an_update_and_no_key_says_which_to_set(cli_env, capsys, committing_db):
    code, _, err = run(capsys, "screen", "address", "ethereum", ME)

    assert code == 1 and "ETHERSCAN_API_KEY or ALCHEMY_API_KEY" in err


# --- screen transaction --------------------------------------------------------------------------


def transaction_rig(providers):
    data = load_fixture("evm/ethereum_erc20_single")
    tx_hash = data["tx"]["hash"]
    providers.etherscan.transactions[tx_hash] = data["tx"]
    providers.etherscan.receipts[tx_hash] = data["receipt"]
    return tx_hash, data


def test_a_transaction_with_a_flagged_party_exits_2(providers, capsys, committing_db):
    tx_hash, data = transaction_rig(providers)
    with committing_db.write() as session:
        label_address(session, data["tx"]["to"].lower(), "SCAM")

    code, out, _ = run(capsys, "screen", "transaction", "ethereum", tx_hash)

    assert code == 2 and "AML-008" in out and tx_hash in out
    assert "judged on an address's history" in out  # the rules it could not apply


def test_a_transaction_with_no_flagged_party_is_not_a_clearance_because_a_receipt_is_partial(
    providers, capsys
):
    tx_hash, _ = transaction_rig(providers)

    code, out, _ = run(capsys, "screen", "transaction", "ethereum", tx_hash, "--json")

    body = json.loads(out)
    assert code == 3  # nothing found, but a receipt leaves out internal coin movements
    assert body["outcome"] == "no_findings" and body["data_coverage"] == "PARTIAL"
    assert body["subject"]["type"] == "transaction"
    assert any("internal coin movements" in gap["message"] for gap in body["data_gaps"])


def test_a_transaction_nobody_has_or_that_is_malformed_is_an_error(providers, capsys):
    code, _, err = run(capsys, "screen", "transaction", "ethereum", "0x" + "ab" * 32)
    assert code == 1 and "no configured source has this transaction" in err
    code, _, err = run(capsys, "screen", "transaction", "ethereum", "0x1234")
    assert code == 1 and "not a transaction" in err
    assert run(capsys, "screen", "transaction", "dogecoin", "0x1")[0] == 1
