"""Turning Esplora's transactions and spend lists into our records.

Pure functions, no network. Esplora (the open-source indexer behind blockstream.info and
mempool.space) gives each input with the output it spends already looked up (`prevout`), so a
transaction arrives with both sides complete and addresses on both. An output with no address form
(an old pay-to-public-key output, a bare multisig, OP_RETURN data) comes without one, and so does
the input that spends it: that is stored as None, never guessed.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.chains.bitcoin.address import BitcoinAddressValidator
from app.chains.bitcoin.chains import BitcoinChain
from app.chains.bitcoin.models import BitcoinTransaction, SpendState, TxInput, TxOutput
from app.chains.models import Confirmation, DataQuality
from app.chains.values import MalformedRow, clean_text, raw_amount, to_int

OP_RETURN = "op_return"
_TXID = re.compile(r"^[0-9a-fA-F]{64}$")
_VALIDATOR = BitcoinAddressValidator()
MAX_IO = 100_000  # far above any valid transaction; bounds the work done on hostile input


def txid_of(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not _TXID.match(text):
        raise MalformedRow(f"not a transaction id: {text[:70]!r}")
    return text


def _address(value: Any) -> str | None:
    """A checked address, or None when the script has none (which is normal, not an error)."""
    if value in (None, ""):
        return None
    check = _VALIDATOR.check(str(value))
    if not check.valid or check.normalized is None:
        raise MalformedRow(f"not a Bitcoin address: {str(value)[:70]!r}")
    return check.normalized


def _script_type(value: Any) -> str:
    text = clean_text(value)
    if text is None:
        raise MalformedRow("an output without a script type")
    return text


def _input(index: int, item: Any) -> TxInput:
    if not isinstance(item, dict):
        raise MalformedRow("an input that is not an object")
    if item.get("is_coinbase") is True:
        return TxInput(index, None, None, None, None, None, is_coinbase=True)
    prevout = item.get("prevout")
    base = {
        "previous_txid": txid_of(item.get("txid")),
        "previous_output_index": to_int(item.get("vout")),
    }
    if prevout is None:
        # The provider could not look the spent output up: the value and the source are unknown.
        return TxInput(index, source_address=None, value_raw=None, script_type=None, **base)
    if not isinstance(prevout, dict):
        raise MalformedRow("a spent output that is not an object")
    return TxInput(
        index,
        source_address=_address(prevout.get("scriptpubkey_address")),
        value_raw=raw_amount(prevout.get("value")),
        script_type=_script_type(prevout.get("scriptpubkey_type")),
        **base,
    )


def _output(index: int, item: Any) -> TxOutput:
    if not isinstance(item, dict):
        raise MalformedRow("an output that is not an object")
    script_type = _script_type(item.get("scriptpubkey_type"))
    return TxOutput(
        index,
        destination_address=_address(item.get("scriptpubkey_address")),
        value_raw=raw_amount(item.get("value")),
        script_type=script_type,
        spend_state=SpendState.UNSPENDABLE if script_type == OP_RETURN else SpendState.UNKNOWN,
    )


def _moment(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(to_int(value), UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise MalformedRow("timestamp out of range") from error


def esplora_transaction(
    tx: Any, chain: BitcoinChain, provider: str = "esplora"
) -> BitcoinTransaction:
    """A transaction as Esplora returns it from /tx/:txid or an address or block listing.

    Confirmations need the chain's height, which this does not know: see `with_head`.
    """
    if not isinstance(tx, dict):
        raise MalformedRow("a transaction that is not an object")
    vin, vout = tx.get("vin"), tx.get("vout")
    if not isinstance(vin, list) or not isinstance(vout, list) or not vin or not vout:
        raise MalformedRow("a transaction without inputs or outputs")
    if len(vin) > MAX_IO or len(vout) > MAX_IO:
        raise MalformedRow("a transaction with an absurd number of inputs or outputs")

    inputs = tuple(_input(index, item) for index, item in enumerate(vin))
    if any(item.is_coinbase for item in inputs) and len(inputs) != 1:
        raise MalformedRow("a coinbase input next to other inputs")
    outputs = tuple(_output(index, item) for index, item in enumerate(vout))

    status = tx.get("status")
    if not isinstance(status, dict) or not isinstance(status.get("confirmed"), bool):
        raise MalformedRow("a transaction without a status")
    height = block_hash = timestamp = None
    if status["confirmed"]:
        height = to_int(status.get("block_height"))
        if height < 0:
            raise MalformedRow("negative block height")
        block_hash = txid_of(status.get("block_hash"))
        timestamp = _moment(status.get("block_time"))

    coinbase = inputs[0].is_coinbase
    return BitcoinTransaction(
        chain_id=chain.chain_id,
        txid=txid_of(tx.get("txid")),
        block_height=height,
        block_hash=block_hash,
        timestamp=timestamp,
        inputs=inputs,
        outputs=outputs,
        fee_raw=None if coinbase else raw_amount(tx.get("fee")),
        size=to_int(tx.get("size")),
        weight=to_int(tx.get("weight")),
        version=to_int(tx.get("version")),
        locktime=to_int(tx.get("locktime")),
        provider=provider,
        data_quality=DataQuality.INDEXED,
    )


def with_head(tx: BitcoinTransaction, head: int | None, chain: BitcoinChain) -> BitcoinTransaction:
    """The transaction with its depth worked out from the chain's height."""
    if tx.block_height is None:
        return replace(tx, confirmations=0, confirmation_status=Confirmation.UNCONFIRMED)
    if head is None:
        return tx
    confirmations = max(0, head - tx.block_height + 1)
    status = (
        Confirmation.CONFIRMED
        if confirmations >= chain.confirmation_depth
        else Confirmation.UNCONFIRMED
    )
    return replace(tx, confirmations=confirmations, confirmation_status=status)


def unresolved_inputs(tx: BitcoinTransaction) -> int:
    """How many inputs the provider gave without the output they spend."""
    return sum(1 for item in tx.inputs if not item.is_coinbase and item.value_raw is None)


def apply_outspends(tx: BitcoinTransaction, outspends: Any) -> BitcoinTransaction:
    """Say, for each output, whether it has been spent and by what (Esplora's /tx/:txid/outspends).

    The list holds one entry per output, in order. An output that can never be spent stays so.
    """
    if not isinstance(outspends, list) or len(outspends) != len(tx.outputs):
        raise MalformedRow("the spend list does not match the transaction's outputs")
    outputs = []
    for output, entry in zip(tx.outputs, outspends, strict=True):
        if output.spend_state is SpendState.UNSPENDABLE:
            outputs.append(output)
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("spent"), bool):
            raise MalformedRow("a spend entry without a verdict")
        if entry["spent"]:
            outputs.append(
                replace(
                    output,
                    spend_state=SpendState.SPENT,
                    spent_by_txid=txid_of(entry.get("txid")),
                    spent_by_input_index=to_int(entry.get("vin")),
                )
            )
        else:
            outputs.append(replace(output, spend_state=SpendState.UNSPENT))
    return replace(tx, outputs=tuple(outputs))
