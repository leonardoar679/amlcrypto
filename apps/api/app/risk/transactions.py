"""Screening one transaction: who is on each side of it, and what the sources say about them.

A transaction has no history, so the rules that judge behaviour over time and the routes through
other addresses do not apply; what remains is the direct question the blueprint's acceptance asks
of a transaction: is a party to it sanctioned, a mixer, or flagged as risky? To judge an address's
wider exposure, screen the address.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.chains.bitcoin.models import BitcoinDetail
from app.chains.models import TransactionDetail, TxStatus
from app.chains.solana.models import SolanaDetail
from app.intel.model import RISK_CATEGORIES, AddressIntel, Category, LabelMatch
from app.risk.model import (
    Coverage,
    Finding,
    Outcome,
    Report,
    RuleReport,
    RuleStatus,
)
from app.risk.rules.base import label_dict
from app.risk.rules.registry import BY_ID, EffectiveRule
from app.risk.scoring import ScoringSettings, score_findings

SENDER, RECEIVER = "sender", "receiver"
# The rules that can be judged from one transaction; the others need a history or a graph.
JUDGED = ("AML-001", "AML-003", "AML-007", "AML-008")


@dataclass(frozen=True, slots=True)
class Party:
    address: str
    role: str  # SENDER or RECEIVER


@dataclass(frozen=True, slots=True)
class TransactionFacts:
    """What a transaction is, for the report."""

    transaction: str
    timestamp: datetime | None
    status: str
    parties: tuple[Party, ...]
    transfers: int
    notes: tuple[str, ...]  # what the answer does not include, in words


def facts_of(detail: TransactionDetail | BitcoinDetail | SolanaDetail) -> TransactionFacts:
    if isinstance(detail, BitcoinDetail):
        tx = detail.transaction
        parties = [Party(i.source_address, SENDER) for i in tx.inputs if i.source_address]
        parties += [
            Party(o.destination_address, RECEIVER) for o in tx.outputs if o.destination_address
        ]
        return TransactionFacts(
            tx.txid,
            tx.timestamp,
            "success",
            _unique(parties),
            len(tx.outputs),
            tuple(detail.coverage),
        )
    if isinstance(detail, SolanaDetail):
        tx = detail.transaction
        parties = []
        for transfer in tx.transfers:
            if transfer.from_address:
                parties.append(Party(transfer.from_address, SENDER))
            if transfer.to_address:
                parties.append(Party(transfer.to_address, RECEIVER))
        return TransactionFacts(
            tx.signature,
            tx.timestamp,
            tx.status.value,
            _unique(parties),
            len(tx.transfers),
            tuple(detail.coverage),
        )
    tx = detail.transaction
    parties = [Party(tx.from_address, SENDER)]
    receiver = tx.to_address or tx.created_contract
    if receiver:
        parties.append(Party(receiver, RECEIVER))
    for transfer in detail.transfers:
        if transfer.from_address:
            parties.append(Party(transfer.from_address, SENDER))
        if transfer.to_address:
            parties.append(Party(transfer.to_address, RECEIVER))
    return TransactionFacts(
        tx.transaction_hash,
        tx.timestamp,
        tx.status.value,
        _unique(parties),
        len(detail.transfers),
        tuple(detail.coverage),
    )


def _unique(parties: Sequence[Party]) -> tuple[Party, ...]:
    return tuple(dict.fromkeys(parties))


def _labels(intel: AddressIntel | None, *categories: Category) -> list[LabelMatch]:
    return [label for label in (intel.labels if intel else ()) if label.category in categories]


def _finding(
    rule_id: str,
    party: Party,
    labels: list[LabelMatch],
    facts: TransactionFacts,
    cfg: Mapping[str, Any],
    *,
    chain: str,
    factor: str | None = None,
) -> Finding:
    rule = BY_ID[rule_id]
    strongest = max(labels, key=lambda label: label.confidence)
    incoming = party.role == SENDER
    kinds = "/".join(sorted({label.category.value for label in labels}))
    who = strongest.entity_name or party.address
    sanctions = rule_id == "AML-001"
    return rule.finding(
        description=(
            f"A {party.role} of this transaction is {who} ({kinds}, {strongest.source.slug})"
        ),
        key=("party", party.address, party.role),
        confidence=strongest.confidence / 100,
        severity=rule.severity,
        factor=factor or rule.factor,
        hops=1,
        direction="incoming" if incoming else "outgoing",
        target=f"{chain}:{party.address}",
        transactions=(facts.transaction,),
        transactions_total=1,
        evidence=(
            {
                "type": "transaction_party",
                "role": party.role,
                "address": party.address,
                "labels": [label_dict(label) for label in labels],
            },
        ),
        sources=tuple(sorted({label.source.slug for label in labels})),
        last_verified=max(label.last_verified for label in labels),
        latest_at=facts.timestamp,
        override=sanctions and strongest.confidence >= int(cfg["override_min_confidence"]),
    )


def screen_transaction(
    facts: TransactionFacts,
    intel: Mapping[str, AddressIntel],
    *,
    chain: str,
    chain_id: str,
    rules: Sequence[EffectiveRule],
    settings: ScoringSettings,
    now: datetime,
) -> Report:
    by_id = {e.rule.id: e for e in rules}
    findings: list[Finding] = []
    reports = []
    for effective in rules:
        rule = effective.rule
        if rule.id not in JUDGED:
            reports.append(
                RuleReport(
                    rule.id,
                    rule.name,
                    rule.version,
                    RuleStatus.NOT_EVALUATED,
                    "judged on an address's history or its routes, not on one transaction",
                    effective.config,
                )
            )
            continue
        if not effective.enabled:
            reports.append(
                RuleReport(
                    rule.id, rule.name, rule.version, RuleStatus.NOT_EVALUATED,
                    "switched off in the rule configuration", effective.config,
                )
            )  # fmt: skip
            continue
        found: list[Finding] = []
        for party in facts.parties:
            known = intel.get(party.address)
            if rule.id == "AML-001":
                labels = _labels(known, Category.SANCTIONS)
            elif rule.id == "AML-003":
                labels = _labels(known, Category.MIXER)
            elif (rule.id == "AML-007") == (party.role == SENDER):
                labels = [label for label in _labels(known, *RISK_CATEGORIES)]
            else:
                labels = []
            if labels:
                factor = None
                if rule.id in ("AML-007", "AML-008"):
                    kinds = {label.category for label in labels}
                    factor = (
                        "indirect_sanctions"
                        if Category.SANCTIONS in kinds
                        else "mixer"
                        if Category.MIXER in kinds
                        else "scam_ransomware"
                    )
                found.append(
                    _finding(
                        rule.id,
                        party,
                        labels,
                        facts,
                        by_id[rule.id].config,
                        chain=chain,
                        factor=factor,
                    )  # fmt: skip
                )
        findings += found
        reports.append(
            RuleReport(
                rule.id,
                rule.name,
                rule.version,
                RuleStatus.TRIGGERED if found else RuleStatus.CLEAR,
                None,
                effective.config,
            )
        )

    sources = next((i.sources for i in intel.values()), ())
    if not facts.parties:
        coverage = Coverage.INSUFFICIENT
    elif facts.notes:
        coverage = Coverage.PARTIAL
    else:
        coverage = Coverage.COMPLETE
    score = score_findings(
        findings,
        priorities={e.rule.id: e.rule.priority for e in rules},
        coverage=coverage,
        sources_fresh=all(s.freshness.value == "fresh" for s in sources),
        now=now,
        settings=settings,
    )
    counted = [item for item in score.scored if item.suppressed_by is None]
    if counted:
        outcome = Outcome.FINDINGS
    elif coverage is Coverage.INSUFFICIENT:
        outcome = Outcome.INSUFFICIENT_DATA
    else:
        outcome = Outcome.NO_FINDINGS

    warnings = [f"the answer does not include: {note}" for note in facts.notes]
    if facts.status == TxStatus.FAILED.value:
        warnings.append("the transaction failed, so it moved no value (only its parties are shown)")
    return Report(
        subject_type="transaction",
        chain_id=chain_id,
        chain=chain,
        subject=facts.transaction,
        generated_at=now,
        coverage=coverage,
        outcome=outcome,
        score=None if outcome is Outcome.INSUFFICIENT_DATA else score,
        rules=reports,
        analysis={
            "parties_checked": len(facts.parties),
            "transfers": facts.transfers,
            "transaction_status": facts.status,
            "transaction_time": facts.timestamp.isoformat() if facts.timestamp else None,
            "rules_triggered": sum(1 for r in reports if r.status is RuleStatus.TRIGGERED),
            "rules_not_evaluated": sum(1 for r in reports if r.status is RuleStatus.NOT_EVALUATED),
        },
        gaps=[{"code": "transaction_coverage", "message": note} for note in facts.notes],
        warnings=warnings,
        sources=[source.to_dict() for source in sources],
    )
