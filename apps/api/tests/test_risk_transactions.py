"""Screening one transaction: its parties against what the label sources say."""

from __future__ import annotations

from dataclasses import replace

from app.chains.bitcoin.models import BitcoinDetail
from app.chains.models import (
    Asset,
    DataQuality,
    Transaction,
    TransactionDetail,
    Transfer,
    TransferType,
    TxStatus,
)
from app.chains.solana.chains import SOLANA_CHAIN
from app.chains.solana.models import SolanaDetail
from app.chains.solana.normalize import solana_transaction
from app.intel.model import Category
from app.risk.model import Coverage, Level, Outcome, RuleStatus
from app.risk.rules.registry import effective_rules
from app.risk.scoring import ScoringSettings
from app.risk.transactions import RECEIVER, SENDER, Party, facts_of, screen_transaction
from tests.graph_helpers import CHAIN_ID, NOW, FakeLabels, addr, label
from tests.helpers import load_fixture, run
from tests.test_graph_flows import bitcoin

ME, YOU, BAD, MIXER = addr(1), addr(2), addr(900), addr(901)
TX = "0x" + "d" * 64


def evm_detail(sender=ME, receiver=YOU, *, transfers=(), notes=(), status=TxStatus.SUCCESS):
    tx = Transaction(
        chain_id=CHAIN_ID,
        transaction_hash=TX,
        block_number=100,
        timestamp=NOW,
        from_address=sender,
        to_address=receiver,
        created_contract=None,
        value_raw="5",
        status=status,
        provider="etherscan",
        data_quality=DataQuality.INDEXED,
    )
    return TransactionDetail(tx, tuple(transfers), tuple(notes))


def transfer(sender, to):
    return Transfer(
        chain_id=CHAIN_ID,
        transaction_hash=TX,
        event_key=f"erc20:{sender}:{to}",
        transfer_type=TransferType.ERC20,
        block_number=100,
        timestamp=NOW,
        from_address=sender,
        to_address=to,
        asset=Asset("USDT", "0xtoken", 6),
        amount_raw="9",
        provider="etherscan",
        data_quality=DataQuality.INDEXED,
    )


def screen(detail, *labelled, overrides=None, facts=None):
    facts = facts or facts_of(detail)
    addresses = [p.address for p in facts.parties]
    intel = run(FakeLabels(*labelled).of(addresses))
    return screen_transaction(
        facts,
        intel,
        chain="ethereum",
        chain_id=CHAIN_ID,
        rules=effective_rules(overrides),
        settings=ScoringSettings(),
        now=NOW,
    )


def counted(report):
    return {item.finding.rule_id: item.finding for item in report.findings}


# --- who is a party ------------------------------------------------------------------------------


def test_the_parties_of_an_account_chain_transaction_are_its_senders_and_receivers():
    facts = facts_of(evm_detail(transfers=[transfer(ME, BAD), transfer(BAD, MIXER)]))

    assert set(facts.parties) == {
        Party(ME, SENDER),
        Party(YOU, RECEIVER),
        Party(BAD, RECEIVER),
        Party(BAD, SENDER),
        Party(MIXER, RECEIVER),
    }
    assert facts.transaction == TX and facts.transfers == 2


def test_a_contract_creation_has_the_new_contract_as_its_receiver():
    detail = evm_detail(receiver=None)
    detail = replace(detail, transaction=replace(detail.transaction, created_contract=BAD))

    assert Party(BAD, RECEIVER) in facts_of(detail).parties


def test_the_parties_of_a_bitcoin_transaction_are_its_input_and_output_addresses():
    tx = bitcoin([("bc1qa", 5000), ("bc1qb", 5000)], [("bc1qc", 9000), (None, 0)], txid="t" * 64)

    facts = facts_of(BitcoinDetail(tx, ("outspends unknown",)))

    assert facts.parties == (
        Party("bc1qa", SENDER),
        Party("bc1qb", SENDER),
        Party("bc1qc", RECEIVER),
    )
    assert facts.notes == ("outspends unknown",) and facts.transaction == "t" * 64


def test_the_parties_of_a_solana_transaction_are_the_wallets_in_its_transfers():
    tx = solana_transaction(load_fixture("solana/ata_create_and_transfer")["item"], SOLANA_CHAIN)

    facts = facts_of(SolanaDetail(tx))

    assert facts.parties and facts.transfers == len(tx.transfers)
    assert {p.address for p in facts.parties} >= {
        t.from_address for t in tx.transfers if t.from_address
    }


# --- what is found -------------------------------------------------------------------------------


def test_a_sanctioned_receiver_is_a_critical_direct_finding():
    report = screen(evm_detail(receiver=BAD), label(BAD, Category.SANCTIONS, entity="LAZARUS"))

    finding = counted(report)["AML-001"]
    assert finding.hops == 1 and finding.direction == "outgoing" and finding.override
    assert finding.transactions == (TX,) and finding.target == f"ethereum:{BAD}"
    assert finding.evidence[0]["role"] == RECEIVER and "LAZARUS" in finding.description
    assert report.score.value == 100 and report.score.level is Level.CRITICAL
    assert report.subject_type == "transaction" and report.subject == TX


def test_a_sanctioned_sender_is_found_too_and_counted_once():
    report = screen(evm_detail(sender=BAD), label(BAD, Category.SANCTIONS))

    assert set(counted(report)) == {"AML-001"}  # AML-007 saw the same party and was a duplicate
    assert [i.finding.rule_id for i in report.suppressed] == ["AML-007"]
    assert report.suppressed[0].suppressed_by == "AML-001"


def test_a_mixer_party_is_a_mixer_finding():
    report = screen(evm_detail(receiver=MIXER), label(MIXER, Category.MIXER))

    assert set(counted(report)) == {"AML-003"}
    assert report.score.value == 35


def test_a_risky_sender_is_incoming_risk_and_a_risky_receiver_outgoing_risk():
    sender = screen(evm_detail(sender=BAD), label(BAD, Category.SCAM))
    receiver = screen(evm_detail(receiver=BAD), label(BAD, Category.SCAM))

    assert set(counted(sender)) == {"AML-007"} and set(counted(receiver)) == {"AML-008"}
    assert counted(sender)["AML-007"].direction == "incoming"
    assert counted(receiver)["AML-008"].direction == "outgoing"


def test_an_exchange_is_not_a_finding():
    report = screen(evm_detail(receiver=BAD), label(BAD, Category.EXCHANGE))

    assert report.findings == [] and report.outcome is Outcome.NO_FINDINGS
    assert report.score.value == 0


def test_a_weak_sanctions_claim_is_a_finding_without_the_override():
    report = screen(evm_detail(receiver=BAD), label(BAD, Category.SANCTIONS, confidence=40))

    assert not counted(report)["AML-001"].override and 0 < report.score.value < 100


# --- what is said about the rest -----------------------------------------------------------------


def test_the_rules_that_need_a_history_say_they_were_not_judged():
    report = screen(evm_detail())

    judged = {r.rule_id for r in report.rules if r.status is not RuleStatus.NOT_EVALUATED}
    assert judged == {"AML-001", "AML-003", "AML-007", "AML-008"}
    reason = next(r.reason for r in report.rules if r.rule_id == "AML-004")
    assert "not on one transaction" in reason
    assert report.to_dict()["analysis"]["rules_not_evaluated"] == 11


def test_a_rule_switched_off_is_not_run():
    report = screen(
        evm_detail(receiver=BAD),
        label(BAD, Category.SANCTIONS),
        overrides={"AML-001": (False, None)},
    )

    assert set(counted(report)) == {"AML-008"}
    assert next(r for r in report.rules if r.rule_id == "AML-001").reason.startswith("switched off")


def test_what_the_answer_leaves_out_makes_the_coverage_partial_and_is_said():
    report = screen(evm_detail(notes=("internal transfers are not covered for this chain",)))

    assert report.coverage is Coverage.PARTIAL
    assert report.gaps[0]["message"].startswith("internal transfers")
    assert any("does not include" in w for w in report.warnings)


def test_a_failed_transaction_is_screened_with_a_warning_that_it_moved_nothing():
    report = screen(evm_detail(receiver=BAD, status=TxStatus.FAILED), label(BAD, Category.SCAM))

    assert any("moved no value" in w for w in report.warnings)


def test_a_transaction_with_no_parties_cannot_be_judged_and_has_no_score():
    detail = evm_detail()
    facts = replace(facts_of(detail), parties=())

    report = screen(detail, facts=facts)

    assert report.coverage is Coverage.INSUFFICIENT and report.outcome is Outcome.INSUFFICIENT_DATA
    assert report.to_dict()["risk_score"] is None


def test_the_report_can_be_stored():
    import json

    body = screen(evm_detail(receiver=BAD), label(BAD, Category.SANCTIONS)).to_dict()

    json.dumps(body)
    assert body["subject"]["type"] == "transaction" and body["findings"][0]["rule_id"] == "AML-001"


# --- pinned down by mutation checks -------------------------------------------------------------


def test_a_party_that_appears_twice_in_a_transaction_is_one_party_and_one_finding():
    detail = evm_detail(sender=ME, receiver=BAD, transfers=[transfer(ME, BAD), transfer(ME, BAD)])

    facts = facts_of(detail)
    report = screen(detail, label(BAD, Category.SCAM))

    assert len(facts.parties) == len(set(facts.parties)) == 2
    assert len(report.findings) == 1 and report.suppressed == []


def test_a_claim_of_exactly_the_override_confidence_overrides_and_one_below_does_not():
    at_the_line = screen(evm_detail(receiver=BAD), label(BAD, Category.SANCTIONS, confidence=90))
    below = screen(evm_detail(receiver=BAD), label(BAD, Category.SANCTIONS, confidence=89))

    assert at_the_line.score.override == "AML-001" and below.score.override is None


def test_the_broad_rules_score_a_party_by_what_it_is_when_the_specific_rules_are_off():
    sanctioned = screen(
        evm_detail(sender=BAD),
        label(BAD, Category.SANCTIONS),
        overrides={"AML-001": (False, None)},
    )
    mixer = screen(
        evm_detail(sender=MIXER),
        label(MIXER, Category.MIXER),
        overrides={"AML-003": (False, None)},
    )

    assert counted(sanctioned)["AML-007"].factor == "indirect_sanctions"
    assert counted(mixer)["AML-007"].factor == "mixer"
