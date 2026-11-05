"""What can be said about Bitcoin transactions, and how sure we are of it.

The chain proves exactly three things: which output each input spends, what each output pays, and
that an output is spent once and by whom. Everything else about "who paid whom" is inference, and
this module keeps the two apart:

* PROVEN, from the records alone: `link_spends` (which known transaction spends which output),
  `address_activity` (what one address put in and took out of a transaction), `order_transactions`.
* INFERRED, heuristics that are often right and sometimes badly wrong: `assess_change` (which output
  is the sender's own change), `detect_coinjoin` (several parties mixing coins), `common_inputs`
  (all inputs belong to one owner). Their results are named as inferences, carry the reasons that
  produced them, and say "unknown" when nothing separates the cases. A CoinJoin invalidates the
  other two, so they are withheld when one is suspected.

Nothing here makes a request or touches a database.
"""

from __future__ import annotations

import heapq
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from app.chains.bitcoin.models import BitcoinTransaction, SpendState, TxOutput
from app.chains.bitcoin.normalize import OP_RETURN, unresolved_inputs

# --- what is proven ----------------------------------------------------------------------------


def link_spends(
    transactions: Sequence[BitcoinTransaction], *, complete_for: str | None = None
) -> list[BitcoinTransaction]:
    """Fill in which outputs the given transactions spend from each other.

    An output whose spender is among `transactions` becomes SPENT by it. Any other output stays
    UNKNOWN: the spender may simply not be in this set, so "no spender here" never means unspent.

    The exception is `complete_for`: if `transactions` are the complete confirmed history of that
    address, then every spend of an output paying it is in the set (the spender must list the
    address among its inputs), so its outputs not spent here are UNSPENT. A spend still waiting in
    the mempool is not in a confirmed history; "unspent" means unspent as of the last block.
    """
    spenders: dict[tuple[str, int], tuple[str, int]] = {}
    for tx in transactions:
        for item in tx.inputs:
            if item.previous_txid is not None and item.previous_output_index is not None:
                spenders.setdefault(
                    (item.previous_txid, item.previous_output_index), (tx.txid, item.index)
                )

    linked = []
    for tx in transactions:
        outputs = []
        for output in tx.outputs:
            if output.spend_state is SpendState.UNKNOWN:
                spender = spenders.get((tx.txid, output.index))
                if spender is not None:
                    output = replace(
                        output,
                        spend_state=SpendState.SPENT,
                        spent_by_txid=spender[0],
                        spent_by_input_index=spender[1],
                    )
                elif complete_for is not None and output.destination_address == complete_for:
                    output = replace(output, spend_state=SpendState.UNSPENT)
            outputs.append(output)
        linked.append(replace(tx, outputs=tuple(outputs)))
    return linked


def order_transactions(transactions: Iterable[BitcoinTransaction]) -> list[BitcoinTransaction]:
    """Oldest first. Within one block, a transaction follows the ones it spends from.

    A block's transactions have no recorded order in what providers return, but a transaction can
    only spend an output that exists, so a spender always comes after what it spends. Otherwise the
    transaction id decides, so the same input always gives the same order. Unconfirmed last.
    """
    groups: dict[int | None, list[BitcoinTransaction]] = defaultdict(list)
    for tx in transactions:
        groups[tx.block_height].append(tx)
    ordered: list[BitcoinTransaction] = []
    for height in sorted(groups, key=lambda value: (value is None, value or 0)):
        ordered.extend(_parents_first(groups[height]))
    return ordered


def _parents_first(group: list[BitcoinTransaction]) -> list[BitcoinTransaction]:
    by_id = {tx.txid: tx for tx in group}
    waiting = dict.fromkeys(by_id, 0)
    children: dict[str, list[str]] = defaultdict(list)
    for tx in group:
        parents = {item.previous_txid for item in tx.inputs} & by_id.keys() - {tx.txid}
        for parent in parents:
            children[parent].append(tx.txid)
            waiting[tx.txid] += 1
    ready = [txid for txid, count in waiting.items() if count == 0]
    heapq.heapify(ready)
    result: list[BitcoinTransaction] = []
    while ready:
        txid = heapq.heappop(ready)
        result.append(by_id[txid])
        for child in children[txid]:
            waiting[child] -= 1
            if waiting[child] == 0:
                heapq.heappush(ready, child)
    return result


@dataclass(frozen=True, slots=True)
class AddressActivity:
    """What one address put into a transaction and took out of it. Proven, not inferred."""

    txid: str
    received_raw: str  # the outputs that pay the address
    sent_raw: str  # the inputs the address funded
    outputs_to_address: int
    inputs_from_address: int
    # False when the provider left out the output some input spends, so `sent_raw` may be short.
    complete: bool

    @property
    def net_raw(self) -> str:
        return str(int(self.received_raw) - int(self.sent_raw))

    @property
    def role(self) -> str:
        if self.inputs_from_address and self.outputs_to_address:
            return "both"
        return "sender" if self.inputs_from_address else "receiver"

    def to_dict(self) -> dict[str, Any]:
        return {
            "txid": self.txid,
            "received_raw": self.received_raw,
            "sent_raw": self.sent_raw,
            "net_raw": self.net_raw,
            "role": self.role,
            "outputs_to_address": self.outputs_to_address,
            "inputs_from_address": self.inputs_from_address,
            "complete": self.complete,
        }


def address_activity(tx: BitcoinTransaction, address: str) -> AddressActivity | None:
    """The address's part in a transaction, or None if it has no part."""
    paid = [output for output in tx.outputs if output.destination_address == address]
    funded = [item for item in tx.inputs if item.source_address == address]
    if not paid and not funded:
        return None
    return AddressActivity(
        txid=tx.txid,
        received_raw=str(sum(int(output.value_raw) for output in paid)),
        sent_raw=str(sum(int(item.value_raw or 0) for item in funded)),
        outputs_to_address=len(paid),
        inputs_from_address=len(funded),
        complete=unresolved_inputs(tx) == 0,
    )


# --- what is inferred --------------------------------------------------------------------------

# An output is "round" when its value is a whole multiple of 0.001 BTC.
ROUND_UNIT = 100_000
# Three equal outputs to three different addresses are the least that can be a mix; five or more
# from five or more funders is what mixing services produce.
COINJOIN_MIN_EQUAL = 3
COINJOIN_LIKELY_EQUAL = 5
COINJOIN_MIN_VALUE = 1_000  # satoshis; mixing denominations are far above the dust limit


class Likelihood(StrEnum):
    CHANGE = "likely_change"  # probably returns the sender's own money to the sender
    PAYMENT = "likely_payment"  # probably pays someone else
    UNKNOWN = "unknown"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class CoinJoinSignal:
    """Several parties appear to have mixed their coins in one transaction."""

    strength: str  # "possible" or "likely"
    equal_outputs: int  # how many outputs have exactly the same value
    equal_value_raw: str
    input_parties: int  # distinct addresses among the inputs

    def to_dict(self) -> dict[str, Any]:
        return {
            "strength": self.strength,
            "equal_outputs": self.equal_outputs,
            "equal_value_raw": self.equal_value_raw,
            "input_parties": self.input_parties,
            "inferred": True,
        }


@dataclass(frozen=True, slots=True)
class OutputAssessment:
    index: int
    likelihood: Likelihood
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "likelihood": self.likelihood.value,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class ChangeAssessment:
    """Which outputs look like the sender's change. Always an inference."""

    outputs: tuple[OutputAssessment, ...]
    confidence: Confidence
    notes: tuple[str, ...]  # why nothing could be said, when that is so

    def to_dict(self) -> dict[str, Any]:
        return {
            "inferred": True,
            "confidence": self.confidence.value,
            "outputs": [item.to_dict() for item in self.outputs],
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class CommonInputs:
    """The addresses that signed one transaction, which are often (not always) one owner."""

    addresses: tuple[str, ...]
    reliable: bool  # False when the transaction looks like a CoinJoin, where it is not so

    def to_dict(self) -> dict[str, Any]:
        return {"inferred": True, "addresses": list(self.addresses), "reliable": self.reliable}


def _input_addresses(tx: BitcoinTransaction) -> list[str]:
    """The distinct addresses behind the inputs, in order of first appearance."""
    seen: dict[str, None] = {}
    for item in tx.inputs:
        if item.source_address is not None:
            seen.setdefault(item.source_address)
    return list(seen)


def _value_outputs(tx: BitcoinTransaction) -> list[TxOutput]:
    """The outputs that carry value to someone: not OP_RETURN data, not empty."""
    return [o for o in tx.outputs if o.script_type != OP_RETURN and int(o.value_raw) > 0]


def detect_coinjoin(tx: BitcoinTransaction) -> CoinJoinSignal | None:
    """Several equal outputs to different addresses, paid for by at least as many funders.

    One wallet paying many people the same amount (a payout batch) has many equal outputs but one
    funder, so it is not flagged. An exchange paying several customers exactly the same amount from
    several of its addresses will be: this is a signal, not a verdict.
    """
    if tx.is_coinbase:
        return None
    # Equal outputs of dust size are what batch minting of inscriptions produces, not mixing.
    outputs = [o for o in _value_outputs(tx) if int(o.value_raw) >= COINJOIN_MIN_VALUE]
    values = Counter(o.value_raw for o in outputs)
    if not values:
        return None
    value, equal = values.most_common(1)[0]
    recipients = {o.destination_address for o in outputs if o.value_raw == value} - {None}
    parties = len(_input_addresses(tx))
    if equal < COINJOIN_MIN_EQUAL or len(recipients) < COINJOIN_MIN_EQUAL or parties < equal:
        return None
    likely = equal >= COINJOIN_LIKELY_EQUAL
    return CoinJoinSignal("likely" if likely else "possible", equal, value, parties)


def common_inputs(tx: BitcoinTransaction) -> CommonInputs | None:
    """The common-input-ownership heuristic: addresses that sign together are one wallet."""
    addresses = _input_addresses(tx)
    if tx.is_coinbase or len(addresses) < 2:
        return None
    return CommonInputs(tuple(addresses), reliable=detect_coinjoin(tx) is None)


def assess_change(tx: BitcoinTransaction) -> ChangeAssessment:
    """Which outputs look like change, from three signals that wallets leave behind.

    * An output paying an address that also funded the transaction returns money to its owner
      (address reuse). The strongest signal.
    * Wallets pay change in the same kind of script their inputs were; a payee often uses another.
      If exactly one output matches every input's script type, it leans change. (When many outputs
      match, as in a payout to many people, the type says nothing.)
    * People choose round amounts to pay and get the odd remainder back. Only a weak lean.

    Signals vote with weights; an output needs a clear margin to be called, otherwise it stays
    unknown. In a two-output transaction whose change was found by address reuse, the other output
    is called a likely payment. No signal is evidence about the others' owners, only about this
    transaction's shape.
    """
    if tx.is_coinbase:
        return ChangeAssessment((), Confidence.LOW, ("a coinbase pays a block reward, not change",))
    outputs = _value_outputs(tx)
    if len(outputs) < 2:
        carrying = "no output carries" if not outputs else "a single output carries"
        return ChangeAssessment(
            tuple(OutputAssessment(o.index, Likelihood.UNKNOWN, ()) for o in outputs),
            Confidence.LOW,
            (f"{carrying} value: there is no change to tell apart from a payment",),
        )
    if detect_coinjoin(tx) is not None:
        return ChangeAssessment(
            tuple(OutputAssessment(o.index, Likelihood.UNKNOWN, ()) for o in outputs),
            Confidence.LOW,
            ("this looks like a CoinJoin: the change heuristics do not apply",),
        )

    funders = set(_input_addresses(tx))
    input_types = {item.script_type for item in tx.inputs if item.script_type is not None}
    shared_type = next(iter(input_types)) if len(input_types) == 1 else None
    matching = [o for o in outputs if shared_type is not None and o.script_type == shared_type]
    round_ones = [o for o in outputs if int(o.value_raw) % ROUND_UNIT == 0]

    scores: dict[int, int] = {o.index: 0 for o in outputs}
    reasons: dict[int, list[str]] = {o.index: [] for o in outputs}
    strongest = Confidence.LOW

    for o in outputs:
        if o.destination_address is not None and o.destination_address in funders:
            scores[o.index] += 3
            reasons[o.index].append("pays an address that also funded the transaction")
            strongest = Confidence.HIGH
    if len(matching) == 1 and len(outputs) > 1:
        for o in outputs:
            if o in matching:
                scores[o.index] += 2
                reasons[o.index].append(f"same script type as every input ({shared_type})")
            else:
                scores[o.index] -= 2
                reasons[o.index].append(
                    f"a different script type from the inputs ({o.script_type})"
                )
        if strongest is not Confidence.HIGH:
            strongest = Confidence.MEDIUM
    if 0 < len(round_ones) < len(outputs):
        for o in outputs:
            if o in round_ones:
                scores[o.index] -= 1
                reasons[o.index].append("a round amount, as payments tend to be")
            else:
                scores[o.index] += 1
                reasons[o.index].append("an odd amount, as a remainder is")

    assessed = []
    for o in outputs:
        score = scores[o.index]
        likelihood = (
            Likelihood.CHANGE
            if score >= 2
            else Likelihood.PAYMENT
            if score <= -2
            else Likelihood.UNKNOWN
        )
        assessed.append(OutputAssessment(o.index, likelihood, tuple(reasons[o.index])))
    if len(assessed) == 2 and strongest is Confidence.HIGH:
        first, second = assessed
        for found, other in ((first, second), (second, first)):
            if found.likelihood is Likelihood.CHANGE and other.likelihood is Likelihood.UNKNOWN:
                reason = "the other output of a two-output transaction whose change was found"
                assessed[assessed.index(other)] = OutputAssessment(
                    other.index, Likelihood.PAYMENT, (*other.reasons, reason)
                )
                break
    notes: tuple[str, ...] = ()
    if all(a.likelihood is Likelihood.UNKNOWN for a in assessed):
        notes = ("no signal separates the outputs clearly",)
        strongest = Confidence.LOW
    return ChangeAssessment(tuple(assessed), strongest, notes)


@dataclass(frozen=True, slots=True)
class TransactionAnalysis:
    coinjoin: CoinJoinSignal | None
    common_inputs: CommonInputs | None
    change: ChangeAssessment

    def to_dict(self) -> dict[str, Any]:
        return {
            "inferred": True,
            "coinjoin": None if self.coinjoin is None else self.coinjoin.to_dict(),
            "common_inputs": None if self.common_inputs is None else self.common_inputs.to_dict(),
            "change": self.change.to_dict(),
        }


def analyze(tx: BitcoinTransaction) -> TransactionAnalysis:
    return TransactionAnalysis(detect_coinjoin(tx), common_inputs(tx), assess_change(tx))
