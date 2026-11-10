"""The operator commands for providers and the job queue."""

from __future__ import annotations

import json
from pathlib import Path

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
from tests.fake_helius import KEY as HELIUS_KEY
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
    cli_env.setenv("HELIUS_API_KEY", HELIUS_KEY)
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
    assert cli.main(["history", "dogecoin", ME]) == 1
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


# --- Bitcoin ------------------------------------------------------------------------------------


def _bitcoin_flow(rig: Rig):
    from tests.fake_esplora import p2pkh, pay, spend, tx, txid

    me = p2pkh(1)
    rig.esplora.add(
        tx(
            1,
            [spend(txid(900), 0, p2pkh(2), 60_000)],
            [pay(me, 50_000), pay(p2pkh(2), 9_000)],
            height=101,
        ),
        tx(
            2, [spend(txid(1), 0, me, 50_000)], [pay(p2pkh(3), 30_000), pay(me, 19_000)], height=102
        ),
        tx(3, [spend(txid(901), 0, p2pkh(4), 8_000)], [pay(me, 7_000)], height=103),
    )
    return me


def test_history_for_bitcoin_shows_each_transaction_with_the_addresss_part_in_it(providers, capsys):
    me = _bitcoin_flow(providers)

    assert cli.main(["history", "bitcoin", me]) == 0

    out = capsys.readouterr().out
    assert "3 transactions in blocks 101..103" in out
    assert "received 0.00076 BTC and sent 0.0005 BTC" in out
    assert "receiver" in out and "both" in out and "-0.00031" in out
    assert "Balance now: 0.00026 BTC (confirmed), checked against the provider's own totals." in out
    assert "Complete through block" in out


def test_history_for_bitcoin_as_json_carries_every_transaction_with_inputs_outputs_and_activity(
    providers, capsys
):
    me = _bitcoin_flow(providers)

    assert cli.main(["history", "bitcoin", me, "--json"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["complete"] is True and document["transactions"] == 3
    assert [t["block_height"] for t in document["transactions_list"]] == [101, 102, 103]
    second = document["transactions_list"][1]
    assert second["activity"]["net_raw"] == "-31000" and second["activity"]["role"] == "both"
    assert second["inputs"][0]["source_address"] == me and len(second["outputs"]) == 2
    assert second["outputs"][0]["spend_state"] == "unknown"
    assert document["transactions_list"][0]["outputs"][0]["spend_state"] == "spent"


def test_a_bitcoin_read_that_stops_early_exits_with_status_two(providers, capsys):
    from tests.fake_esplora import p2pkh, pay, spend, tx, txid

    me = p2pkh(1)
    providers.esplora.add(
        *[
            tx(
                n,
                [spend(txid(5000 + n), 0, p2pkh(9000 + n), 10_000)],
                [pay(me, 9_000)],
                height=100 + n,
            )
            for n in range(1, 61)
        ]
    )

    assert cli.main(["history", "bitcoin", me, "--max-items", "5"]) == 2

    out = capsys.readouterr().out
    assert "INCOMPLETE" in out and "Resume with: {'transactions': 'esplora|" in out


def test_tx_for_bitcoin_shows_inputs_outputs_which_are_spent_and_what_is_only_inferred(
    providers, capsys
):
    me = _bitcoin_flow(providers)
    from tests.fake_esplora import txid

    assert cli.main(["tx", "bitcoin", txid(2)]) == 0

    out = capsys.readouterr().out
    assert f"bip122:000000000019d6689c085ae165831e93  {txid(2)}" in out
    assert "Inputs (1), 0.0005 BTC:" in out and "Outputs (2), 0.00049 BTC:" in out
    assert me in out and "spends 0000000000..000001:0" in out
    assert "fee           0.00001 BTC" in out and "confirmed (block 102" in out
    assert "Inferred, not proven by the chain" in out


def test_tx_for_bitcoin_as_json(providers, capsys):
    _bitcoin_flow(providers)
    from tests.fake_esplora import txid

    assert cli.main(["tx", "bitcoin", txid(1), "--json"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["transaction"]["txid"] == txid(1) and document["coverage"] == []
    assert [o["spend_state"] for o in document["transaction"]["outputs"]] == ["spent", "unspent"]


def test_tx_for_bitcoin_that_does_not_exist_or_is_not_an_id(providers, capsys):
    assert cli.main(["tx", "bitcoin", "ab" * 32]) == 1
    assert "Not found" in capsys.readouterr().err
    assert cli.main(["tx", "bitcoin", "1234"]) == 1
    assert "not a transaction id" in capsys.readouterr().err


def test_a_bad_bitcoin_address_is_refused_before_any_request(providers, capsys):
    assert cli.main(["history", "bitcoin", "1NotAnAddressAtAll"]) == 1
    assert "not a valid bitcoin address" in capsys.readouterr().err
    assert providers.esplora.requests == []


# --- Solana -------------------------------------------------------------------------------------


def _solana_flow(rig: Rig):
    from tests.fake_helius import HEAD, address, transfer_item

    wallet = address(1)
    rig.helius.add(
        transfer_item(1, address(2), wallet, 2_000_000, slot=HEAD - 300),
        transfer_item(2, wallet, address(3), 500_000, slot=HEAD - 100),
    )
    return wallet


def test_history_for_solana_shows_each_transaction_with_what_the_wallet_did_in_it(
    providers, capsys
):
    wallet = _solana_flow(providers)

    assert cli.main(["history", "solana", wallet]) == 0

    out = capsys.readouterr().out
    assert out.startswith(f"solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp  {wallet}")
    assert "2 transactions in slots" in out and "2 transfers involve this wallet" in out
    assert "receiver" in out and "sender" in out and "+0.002" in out and "-0.000505" in out
    assert "Wallet holds 0.001495 SOL and 0 tokens, checked against the chain" in out
    assert "Complete through slot" in out


def test_history_for_solana_as_json_carries_every_transaction_with_its_transfers(providers, capsys):
    wallet = _solana_flow(providers)

    assert cli.main(["history", "solana", wallet, "--json"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["complete"] is True and document["transactions"] == 2
    second = document["transactions_list"][1]
    assert second["activity"]["role"] == "sender" and second["fee_payer"] == wallet
    assert second["transfers"][0]["amount_raw"] == "500000" and second["version"] == "0"
    assert document["figures"]["sol_raw"] == "1495000"


def test_a_solana_read_that_stops_early_exits_with_status_two(providers, capsys):
    from tests.fake_helius import HEAD, address, transfer_item

    wallet = address(1)
    providers.helius.add(
        *[
            transfer_item(n, address(1000 + n), wallet, 5_000, slot=HEAD - 10_000 + n)
            for n in range(1, 251)
        ]
    )

    assert cli.main(["history", "solana", wallet, "--max-items", "5"]) == 2

    out = capsys.readouterr().out
    assert "INCOMPLETE" in out and "Resume with: {'transactions': 'helius|" in out


def test_tx_for_solana_shows_the_fee_the_transfers_and_the_largest_balance_changes(
    providers, capsys
):
    from tests.fake_helius import signature

    wallet = _solana_flow(providers)

    assert cli.main(["tx", "solana", signature(2)]) == 0

    out = capsys.readouterr().out
    assert f"solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp  {signature(2)}" in out
    assert "finalized, version 0" in out and f"paid by {wallet}" in out
    assert "fee           0.000005 SOL" in out
    assert "Transfers (1):" in out and "0.0005 SOL" in out
    assert "Largest SOL balance changes (fee included):" in out and "-0.000505 SOL" in out


def test_tx_for_solana_as_json(providers, capsys):
    from tests.fake_helius import signature

    _solana_flow(providers)

    assert cli.main(["tx", "solana", signature(1), "--json"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["transaction"]["signature"] == signature(1) and document["coverage"] == []
    assert document["transaction"]["transfers"][0]["operation"] == "transfer"


def test_tx_for_solana_that_does_not_exist_or_is_not_a_signature(providers, capsys):
    from tests.fake_helius import signature

    assert cli.main(["tx", "solana", signature(99)]) == 1
    assert "Not found" in capsys.readouterr().err
    assert cli.main(["tx", "solana", "1234"]) == 1
    assert "not a transaction signature" in capsys.readouterr().err


def test_a_bad_solana_address_is_refused_before_any_request(providers, capsys):
    assert cli.main(["history", "solana", "not-an-address"]) == 1
    assert "not a valid solana address" in capsys.readouterr().err
    assert providers.helius.requests == []


def test_solana_without_a_helius_key_says_which_to_set(cli_env, capsys):
    from tests.fake_helius import address

    assert cli.main(["history", "solana", address(1)]) == 1
    assert "HELIUS_API_KEY" in capsys.readouterr().err


# --- stored histories --------------------------------------------------------------------------


def requests_made(rig) -> int:
    return len(rig.etherscan.requests) + len(rig.alchemy.requests)


def test_history_stores_what_it_reads_and_the_second_call_asks_nothing(providers, capsys):
    providers.etherscan.add("txlist", tx_row(5, OTHER, ME, value="1500000000000000000"))

    assert cli.main(["history", "ethereum", ME]) == 0
    first = capsys.readouterr().out
    asked = requests_made(providers)
    assert cli.main(["history", "ethereum", ME]) == 0
    second = capsys.readouterr().out

    assert requests_made(providers) == asked
    assert "stored data: never read; read: full;" in first
    assert "stored data: served from storage: read" in second
    assert "no provider request made" in second and "1.5 ETH" in second


def test_history_as_json_says_what_the_sync_did(providers, capsys):
    providers.etherscan.add("txlist", tx_row(5, OTHER, ME, value="1"))

    assert cli.main(["history", "ethereum", ME, "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert cli.main(["history", "ethereum", ME, "--json"]) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["sync"]["mode"] == "full" and first["sync"]["requests_used"] > 0
    assert second["sync"]["mode"] == "cached" and second["sync"]["requests_used"] == 0
    assert second["transfers_list"] == first["transfers_list"]


def test_refresh_updates_now_and_rebuild_reads_everything_again(providers, capsys):
    providers.etherscan.add("txlist", tx_row(5, OTHER, ME, value="1"))
    cli.main(["history", "ethereum", ME])
    capsys.readouterr()

    assert cli.main(["history", "ethereum", ME, "--refresh"]) == 0
    assert "stored data: refresh requested; read: incremental;" in capsys.readouterr().out
    assert cli.main(["history", "ethereum", ME, "--rebuild"]) == 0
    assert "stored data: rebuild requested; read: full;" in capsys.readouterr().out


def test_no_store_reads_from_the_providers_and_neither_uses_nor_changes_storage(providers, capsys):
    providers.etherscan.add("txlist", tx_row(5, OTHER, ME, value="1"))

    assert cli.main(["history", "ethereum", ME, "--no-store"]) == 0
    first = capsys.readouterr().out
    asked = requests_made(providers)
    assert cli.main(["history", "ethereum", ME, "--no-store"]) == 0
    capsys.readouterr()

    assert "stored data" not in first
    assert requests_made(providers) > asked  # nothing was kept, so the second one read again
    assert cli.main(["history", "ethereum", ME]) == 0
    assert "never read" in capsys.readouterr().out


def test_a_block_range_is_read_as_asked_and_not_stored(providers, capsys):
    providers.etherscan.add("txlist", *[tx_row(b, OTHER, ME, value="1") for b in (5, 6, 7)])

    assert cli.main(["history", "ethereum", ME, "--from-block", "6", "--to-block", "6"]) == 0
    assert "stored data" not in capsys.readouterr().out
    assert cli.main(["history", "ethereum", ME]) == 0
    assert "never read" in capsys.readouterr().out


@pytest.mark.parametrize(
    "flags",
    [["--rebuild", "--no-store"], ["--refresh", "--no-store"], ["--refresh", "--from-block", "1"]],
)
def test_flags_that_contradict_each_other_are_refused_before_any_request(providers, capsys, flags):
    assert cli.main(["history", "ethereum", ME, *flags]) == 1

    assert "error:" in capsys.readouterr().err
    assert requests_made(providers) == 0


# --- stored transactions -----------------------------------------------------------------------


def test_a_looked_up_solana_transaction_is_stored_and_the_second_lookup_asks_nothing(
    providers, capsys
):
    from tests.fake_helius import signature

    _solana_flow(providers)

    assert cli.main(["tx", "solana", signature(2), "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    asked = len(providers.helius.requests)
    assert cli.main(["tx", "solana", signature(2), "--json"]) == 0
    second = json.loads(capsys.readouterr().out)

    assert len(providers.helius.requests) == asked
    assert second == first  # exactly the answer a new lookup would give
    assert cli.main(["tx", "solana", signature(2)]) == 0
    assert "stored data: served from storage" in capsys.readouterr().out


def test_a_solana_transaction_read_in_a_history_answers_a_later_lookup_from_storage(
    providers, capsys
):
    from tests.fake_helius import signature

    wallet = _solana_flow(providers)
    assert cli.main(["history", "solana", wallet]) == 0
    capsys.readouterr()
    asked = len(providers.helius.requests)

    assert cli.main(["tx", "solana", signature(1)]) == 0

    assert len(providers.helius.requests) == asked
    assert "stored data: served from storage" in capsys.readouterr().out


def test_a_looked_up_ethereum_transaction_is_stored_decoded_and_always_asked_again(
    providers, capsys, committing_db
):
    from sqlalchemy import func

    from app.database.models import StoredTransaction, StoredTransfer

    data = load_fixture("evm/ethereum_erc20_single")
    tx_hash = data["tx"]["hash"]
    providers.etherscan.transactions[tx_hash] = data["tx"]
    providers.etherscan.receipts[tx_hash] = data["receipt"]

    assert cli.main(["tx", "ethereum", tx_hash]) == 0
    capsys.readouterr()
    asked = requests_made(providers)

    with committing_db.read() as session:
        assert session.scalar(select(func.count()).select_from(StoredTransaction)) == 1
        stored = list(session.scalars(select(StoredTransfer)))
    assert stored and {t.data_quality for t in stored} == {"decoded"}
    # A receipt's notes say what it leaves out, so it is not answered from storage.
    assert cli.main(["tx", "ethereum", tx_hash]) == 0
    assert requests_made(providers) > asked
    assert "served from storage" not in capsys.readouterr().out


def test_a_looked_up_bitcoin_transaction_is_stored_with_its_inputs_and_outputs(
    providers, capsys, committing_db
):
    from sqlalchemy import func

    from app.database.models import StoredInput, StoredOutput
    from tests.fake_esplora import txid

    _bitcoin_flow(providers)

    assert cli.main(["tx", "bitcoin", txid(2)]) == 0
    capsys.readouterr()

    with committing_db.read() as session:
        assert session.scalar(select(func.count()).select_from(StoredInput)) == 1
        outputs = list(session.scalars(select(StoredOutput)))
    assert len(outputs) == 2
    assert {o.spend_state for o in outputs} <= {None, "spent", "unspendable"}  # never "unspent"


def test_an_unconfirmed_bitcoin_transaction_is_shown_but_not_stored(
    providers, capsys, committing_db
):
    from sqlalchemy import func

    from app.database.models import StoredTransaction
    from tests.fake_esplora import p2pkh, pay, spend, tx, txid

    providers.esplora.add(
        tx(5, [spend(txid(905), 0, p2pkh(2), 9_000)], [pay(p2pkh(3), 8_000)], height=None)
    )

    assert cli.main(["tx", "bitcoin", txid(5)]) == 0

    capsys.readouterr()
    with committing_db.read() as session:
        # It may never be mined, and nothing would ever remove it.
        assert session.scalar(select(func.count()).select_from(StoredTransaction)) == 0


# --- address intelligence ------------------------------------------------------------------------

SDN_SAMPLE = Path(__file__).parent / "fixtures" / "intel" / "sdn_sample.xml"
LAZARUS = "0x098B716B8Aaf21512996dC57EB0615e2383E2f96"


def test_labels_refresh_reads_a_local_copy_and_labels_lookup_explains_what_it_found(
    cli_env, capsys, committing_db
):
    assert cli.main(["labels", "refresh", "--file", str(SDN_SAMPLE)]) == 0
    assert "published 2026-11-05" in capsys.readouterr().out

    assert cli.main(["labels", "lookup", "ethereum", LAZARUS]) == 2  # a label in force: exit 2
    out = capsys.readouterr().out
    assert "LAZARUS GROUP" in out and "DPRK3" in out and "[official]" in out
    assert "Checked against:" in out and "OFAC" in out and "(fresh)" in out


def test_an_address_with_no_label_exits_zero_and_says_it_is_not_proof(
    cli_env, capsys, committing_db
):
    cli.main(["labels", "refresh", "--file", str(SDN_SAMPLE)])
    capsys.readouterr()

    assert cli.main(["labels", "lookup", "ethereum", "0x" + "ab" * 20]) == 0

    assert "not proof" in capsys.readouterr().out


def test_labels_lookup_as_json(cli_env, capsys, committing_db):
    cli.main(["labels", "refresh", "--file", str(SDN_SAMPLE)])
    capsys.readouterr()

    assert cli.main(["labels", "lookup", "bsc", LAZARUS, "--json"]) == 2

    body = json.loads(capsys.readouterr().out)
    assert body["known"] and body["labels"][0]["source"]["slug"] == "ofac_sdn"


def test_the_second_refresh_of_the_same_list_changes_nothing(cli_env, capsys, committing_db):
    cli.main(["labels", "refresh", "--file", str(SDN_SAMPLE)])
    capsys.readouterr()

    assert cli.main(["labels", "refresh", "--file", str(SDN_SAMPLE), "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["outcome"] == "unchanged"


def test_labels_sources_shows_how_current_each_is_without_any_request(
    cli_env, capsys, committing_db
):
    assert cli.main(["labels", "sources"]) == 0
    assert "never fetched" in capsys.readouterr().out

    cli.main(["labels", "refresh", "--file", str(SDN_SAMPLE)])
    capsys.readouterr()
    assert cli.main(["labels", "sources"]) == 0
    assert "published 2026-11-05" in capsys.readouterr().out


ELLIPTIC_SAMPLE = Path(__file__).parent / "fixtures" / "intel" / "elliptic_sample.csv"
EIP55_EXAMPLE = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"


def test_a_source_nobody_has_imported_is_listed_as_not_imported_and_then_as_any_other(
    cli_env, capsys, committing_db
):
    assert cli.main(["labels", "sources"]) == 0
    before = capsys.readouterr().out
    assert "elliptic_binance" in before and "not imported yet" in before
    assert cli.main(["labels", "sources", "--json"]) == 0
    assert [s["slug"] for s in json.loads(capsys.readouterr().out)] == ["ofac_sdn"]

    args = ["labels", "refresh", "--source", "elliptic_binance", "--file", str(ELLIPTIC_SAMPLE)]
    assert cli.main(args) == 0
    assert "no publication date" in capsys.readouterr().out

    assert cli.main(["labels", "sources"]) == 0
    after = capsys.readouterr().out
    assert "not imported yet" not in after and "Elliptic: Binance hack blocklist" in after
    assert cli.main(["labels", "lookup", "bsc", EIP55_EXAMPLE]) == 2  # a label in force
    out = capsys.readouterr().out
    assert "STOLEN_FUNDS (60% confidence)" in out and "'Binance hack' (incident)" in out


def test_an_import_of_a_new_source_that_fails_says_so_and_leaves_no_source_behind(
    cli_env, capsys, committing_db, tmp_path
):
    junk = tmp_path / "junk.csv"
    junk.write_text("<html>maintenance</html>", encoding="utf-8")

    assert cli.main(["labels", "refresh", "--source", "elliptic_binance", "--file", str(junk)]) == 1

    assert "could not bring elliptic_binance up to date" in capsys.readouterr().err
    assert cli.main(["labels", "sources", "--json"]) == 0
    assert [s["slug"] for s in json.loads(capsys.readouterr().out)] == ["ofac_sdn"]


def test_labels_commands_refuse_what_cannot_work(cli_env, capsys, committing_db, tmp_path):
    junk = tmp_path / "junk.xml"
    junk.write_text("<html/>", encoding="utf-8")

    assert cli.main(["labels", "refresh", "--file", str(junk)]) == 1
    assert "could not bring ofac_sdn up to date" in capsys.readouterr().err
    assert cli.main(["labels", "refresh", "--source", "nope"]) == 1
    assert cli.main(["labels", "lookup", "bitcoin", LAZARUS]) == 1
    assert "not a valid bitcoin address" in capsys.readouterr().err
    assert cli.main(["labels", "lookup", "dogecoin", LAZARUS]) == 1


def test_labels_lookup_adds_watchmans_opinion_when_one_is_configured(
    cli_env, capsys, committing_db, monkeypatch
):
    from app.intel.watchman import WatchmanClient

    cli.main(["labels", "refresh", "--file", str(SDN_SAMPLE)])
    capsys.readouterr()

    def down(request):
        raise httpx2.ConnectError("no route")

    monkeypatch.setattr(
        cli,
        "watchman_client",
        lambda settings: WatchmanClient(
            "http://watchman:8084", transport=httpx2.MockTransport(down)
        ),
    )

    assert cli.main(["labels", "lookup", "ethereum", LAZARUS]) == 2

    assert "Watchman second opinion unavailable" in capsys.readouterr().out


# --- graph -------------------------------------------------------------------------------------


def _stored_graph(committing_db):
    from tests.graph_helpers import addr
    from tests.graph_store import move, seed

    friend = addr(0xF1)
    with committing_db.write() as session:
        seed(session, ME, [move(friend, ME, str(2 * 10**18)), move(ME, friend, str(10**18))])
    return friend


def test_graph_shows_the_stored_neighbourhood_and_asks_the_providers_nothing(
    cli_env, capsys, committing_db
):
    friend = _stored_graph(committing_db)

    assert cli.main(["graph", "ethereum", ME]) == 0

    out = capsys.readouterr().out
    assert "complete within that scope" in out and "0 provider requests" in out
    assert friend in out and "2 ETH" in out and "1 ETH" in out
    assert "Addresses:" in out and "Links" in out


def test_graph_as_json_is_the_graph_the_api_returns(cli_env, capsys, committing_db):
    friend = _stored_graph(committing_db)

    assert cli.main(["graph", "ethereum", ME, "--json", "--direction", "in"]) == 0

    body = json.loads(capsys.readouterr().out)
    assert body["complete"] is True and body["scope"]["direction"] == "in"
    assert [n["address"].lower() for n in body["nodes"]] == [ME, friend]
    assert len(body["edges"]) == 1


def test_graph_of_an_address_that_was_never_read_exits_2_and_says_why(
    cli_env, capsys, committing_db
):
    assert cli.main(["graph", "ethereum", ME]) == 2

    out = capsys.readouterr().out
    assert "INCOMPLETE" in out and "no history is stored" in out


def test_graph_update_reads_the_histories_first_and_a_repeat_costs_nothing(providers, capsys):
    providers.etherscan.add("txlist", tx_row(5, OTHER, ME, value="1500000000000000000"))
    args = ["graph", "ethereum", ME, "--update", "--days", "3650", "--json"]

    assert cli.main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert cli.main(args) == 0
    again = json.loads(capsys.readouterr().out)

    assert first["requests_used"] > 0 and again["requests_used"] == 0
    assert first["edges"][0]["amount"] == "1.5" and again["edges"] == first["edges"]


def test_graph_refuses_what_cannot_work_before_spending_any_request(providers, capsys):
    assert cli.main(["graph", "dogecoin", ME]) == 1
    assert "not a chain this platform reads" in capsys.readouterr().err
    assert cli.main(["graph", "ethereum", "0x123", "--update"]) == 1
    assert "not a valid ethereum address" in capsys.readouterr().err
    assert cli.main(["graph", "ethereum", ME, "--refresh"]) == 1
    assert "add --update" in capsys.readouterr().err
    assert providers.etherscan.requests == [] and providers.alchemy.requests == []


def test_graph_update_without_any_key_says_which_to_set(cli_env, capsys):
    assert cli.main(["graph", "ethereum", ME, "--update"]) == 1
    assert "ETHERSCAN_API_KEY or ALCHEMY_API_KEY" in capsys.readouterr().err


def test_graph_refuses_a_bridge_registry_it_cannot_read(cli_env, capsys, tmp_path):
    broken = tmp_path / "bridges.json"
    broken.write_text("{")
    cli_env.setenv("BRIDGE_REGISTRY_FILE", str(broken))

    assert cli.main(["graph", "ethereum", ME]) == 1
    assert "cannot read the bridge registry" in capsys.readouterr().err
