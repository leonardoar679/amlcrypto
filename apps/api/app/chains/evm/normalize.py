"""Turning what Etherscan, Alchemy and a node's receipts say into our records.

Pure functions: no network, no clock. Provider data is untrusted, so every value is checked
before it becomes a record, and a row that cannot be what it claims raises MalformedRow instead
of becoming a plausible-looking wrong record.

How a movement is identified (`Transfer.event_key`)
---------------------------------------------------
Etherscan's token endpoint does not say which log in the transaction a transfer came from, so the
position cannot be the identity. A key built from what the movement *is* works whichever provider
or query reports it, and whichever address the query was for:

    native    "native"                              (a transaction sends its value once)
    internal  "internal:<from>:<to>:<amount>"
    erc20     "erc20:<contract>:<from>:<to>:<amount>"

Two identical movements inside one transaction (the same token, parties and amount) are told apart
by a suffix, "#1", "#2", in the order the provider lists them (assign_occurrences). That order is
the order of the transaction's logs or trace, so it agrees between providers. It can only go wrong
when a page boundary falls between two identical movements of one transaction, which is rare.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.chains.evm.chains import EvmChain
from app.chains.models import (
    Asset,
    Confirmation,
    DataQuality,
    Transaction,
    Transfer,
    TransferType,
    TxStatus,
)
from app.chains.values import (
    MalformedRow,
    assign_occurrences,
    clean_text,
    raw_amount,
    to_int,
)
from app.chains.values import optional_int as _optional_int

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
NATIVE_KEY = "native"

_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LOG_ID = re.compile(r":log:(\d+)$")
_TRACE_ID = re.compile(r":internal:(\d+)$")


# --- scalars -----------------------------------------------------------------------------------


def address_of(value: Any, *, optional: bool = False) -> str | None:
    """A lowercase 0x address. Empty values are None when `optional`, otherwise an error."""
    if value is None or value == "":
        if optional:
            return None
        raise MalformedRow("missing address")
    text = str(value).strip()
    if not _ADDRESS.match(text):
        raise MalformedRow(f"not an address: {text[:50]!r}")
    return text.lower()


def hash_of(value: Any) -> str:
    text = str(value or "").strip()
    if not _HASH.match(text):
        raise MalformedRow(f"not a transaction hash: {text[:70]!r}")
    return text.lower()


def moment(seconds: Any) -> datetime:
    try:
        return datetime.fromtimestamp(to_int(seconds), UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise MalformedRow("timestamp out of range") from error


def confirmation_of(confirmations: int | None, chain: EvmChain) -> Confirmation:
    if confirmations is None:
        return Confirmation.UNKNOWN
    return (
        Confirmation.CONFIRMED
        if confirmations >= chain.confirmation_depth
        else (Confirmation.UNCONFIRMED)
    )


# --- identity ----------------------------------------------------------------------------------


def internal_key(from_address: str | None, to_address: str | None, amount_raw: str) -> str:
    return f"internal:{from_address}:{to_address}:{amount_raw}"


def erc20_key(contract: str, from_address: str | None, to_address: str | None, amount: str) -> str:
    return f"erc20:{contract}:{from_address}:{to_address}:{amount}"


# --- Etherscan ---------------------------------------------------------------------------------


def _etherscan_failed(row: dict[str, Any]) -> bool:
    return row.get("isError") == "1" or row.get("txreceipt_status") == "0"


def etherscan_transaction(
    row: dict[str, Any], chain: EvmChain, provider: str = "etherscan"
) -> Transaction:
    """A row of the `txlist` action."""
    confirmations = _optional_int(row.get("confirmations"))
    gas_used, gas_price = _optional_int(row.get("gasUsed")), _optional_int(row.get("gasPrice"))
    method_id = row.get("methodId") or None
    return Transaction(
        chain_id=chain.chain_id,
        transaction_hash=hash_of(row.get("hash")),
        block_number=to_int(row.get("blockNumber")),
        timestamp=moment(row.get("timeStamp")),
        transaction_index=_optional_int(row.get("transactionIndex")),
        from_address=address_of(row.get("from")),
        to_address=address_of(row.get("to"), optional=True),
        created_contract=address_of(row.get("contractAddress"), optional=True),
        value_raw=raw_amount(row.get("value")),
        status=TxStatus.FAILED if _etherscan_failed(row) else TxStatus.SUCCESS,
        fee_raw=None if gas_used is None or gas_price is None else str(gas_used * gas_price),
        method_id=None if method_id == "0x" else method_id,
        function_name=clean_text(row.get("functionName")),
        is_contract_call=row.get("input", "0x") not in ("", "0x"),
        confirmations=confirmations,
        confirmation_status=confirmation_of(confirmations, chain),
        provider=provider,
        data_quality=DataQuality.INDEXED,
    )


def etherscan_native_transfer(
    row: dict[str, Any], chain: EvmChain, provider: str = "etherscan"
) -> Transfer | None:
    """The coin a transaction sent, from a `txlist` row. None when nothing was moved."""
    amount = raw_amount(row.get("value"))
    if amount == "0" or _etherscan_failed(row):
        return None
    confirmations = _optional_int(row.get("confirmations"))
    return Transfer(
        chain_id=chain.chain_id,
        transaction_hash=hash_of(row.get("hash")),
        event_key=NATIVE_KEY,
        transfer_type=TransferType.NATIVE,
        block_number=to_int(row.get("blockNumber")),
        timestamp=moment(row.get("timeStamp")),
        transaction_index=_optional_int(row.get("transactionIndex")),
        from_address=address_of(row.get("from")),
        # A creation has no `to`; the value went to the contract it created.
        to_address=address_of(row.get("to"), optional=True)
        or address_of(row.get("contractAddress"), optional=True),
        asset=chain.native,
        amount_raw=amount,
        confirmations=confirmations,
        confirmation_status=confirmation_of(confirmations, chain),
        provider=provider,
        data_quality=DataQuality.INDEXED,
    )


def etherscan_internal_transfer(
    row: dict[str, Any], chain: EvmChain, provider: str = "etherscan"
) -> Transfer | None:
    """A row of the `txlistinternal` action. None for calls that moved nothing."""
    if row.get("isError") == "1":
        return None  # the call reverted
    amount = raw_amount(row.get("value"))
    if amount == "0":
        return None
    sender = address_of(row.get("from"))
    recipient = address_of(row.get("to"), optional=True) or address_of(
        row.get("contractAddress"), optional=True
    )
    return Transfer(
        chain_id=chain.chain_id,
        transaction_hash=hash_of(row.get("hash")),
        event_key=internal_key(sender, recipient, amount),
        transfer_type=TransferType.INTERNAL,
        block_number=to_int(row.get("blockNumber")),
        timestamp=moment(row.get("timeStamp")),
        from_address=sender,
        to_address=recipient,
        asset=chain.native,
        amount_raw=amount,
        trace_id=clean_text(row.get("traceId")),
        provider=provider,
        data_quality=DataQuality.INDEXED,
    )


def etherscan_token_transfer(
    row: dict[str, Any], chain: EvmChain, provider: str = "etherscan"
) -> Transfer:
    """A row of the `tokentx` action."""
    contract = address_of(row.get("contractAddress"))
    sender = address_of(row.get("from"))
    recipient = address_of(row.get("to"), optional=True)
    amount = raw_amount(row.get("value"))
    confirmations = _optional_int(row.get("confirmations"))
    return Transfer(
        chain_id=chain.chain_id,
        transaction_hash=hash_of(row.get("hash")),
        event_key=erc20_key(contract, sender, recipient, amount),
        transfer_type=TransferType.ERC20,
        block_number=to_int(row.get("blockNumber")),
        timestamp=moment(row.get("timeStamp")),
        transaction_index=_optional_int(row.get("transactionIndex")),
        from_address=sender,
        to_address=recipient,
        asset=Asset(
            symbol=clean_text(row.get("tokenSymbol")),
            name=clean_text(row.get("tokenName")),
            contract_address=contract,
            decimals=_optional_int(row.get("tokenDecimal")),
        ),
        amount_raw=amount,
        confirmations=confirmations,
        confirmation_status=confirmation_of(confirmations, chain),
        provider=provider,
        data_quality=DataQuality.INDEXED,
    )


# --- Alchemy -----------------------------------------------------------------------------------


def alchemy_transfer(
    row: dict[str, Any],
    chain: EvmChain,
    timestamp: datetime | None,
    provider: str = "alchemy",
) -> Transfer | None:
    """A transfer of `alchemy_getAssetTransfers`. None for categories we do not model (NFTs)."""
    category = row.get("category")
    raw = row.get("rawContract") or {}
    unique_id = str(row.get("uniqueId") or "")
    sender = address_of(row.get("from"))
    recipient = address_of(row.get("to"), optional=True)
    common = {
        "chain_id": chain.chain_id,
        "transaction_hash": hash_of(row.get("hash")),
        "block_number": to_int(row.get("blockNum")),
        "timestamp": timestamp,
        "from_address": sender,
        "to_address": recipient,
        "provider": provider,
        "data_quality": DataQuality.INDEXED,
    }

    if category in ("external", "internal"):
        amount = raw_amount(raw.get("value"))
        if category == "external":
            return Transfer(
                event_key=NATIVE_KEY,
                transfer_type=TransferType.NATIVE,
                asset=chain.native,
                amount_raw=amount,
                **common,
            )
        trace = _TRACE_ID.search(unique_id)
        return Transfer(
            event_key=internal_key(sender, recipient, amount),
            transfer_type=TransferType.INTERNAL,
            asset=chain.native,
            amount_raw=amount,
            trace_id=trace.group(1) if trace else None,
            **common,
        )

    if category in ("erc20", "token"):
        contract = address_of(raw.get("address"))
        amount = raw_amount(raw.get("value"))
        log = _LOG_ID.search(unique_id)
        return Transfer(
            event_key=erc20_key(contract, sender, recipient, amount),
            transfer_type=TransferType.ERC20,
            asset=Asset(
                symbol=clean_text(row.get("asset")),
                contract_address=contract,
                decimals=_optional_int(raw.get("decimal")),
            ),
            amount_raw=amount,
            log_index=int(log.group(1)) if log else None,
            **common,
        )

    return None


# --- a node's own transaction and receipt ------------------------------------------------------


def transaction_from_rpc(
    tx: dict[str, Any],
    receipt: dict[str, Any],
    chain: EvmChain,
    timestamp: datetime | None,
    provider: str,
    head_block: int | None = None,
) -> Transaction:
    block_number = to_int(receipt.get("blockNumber"))
    gas_used = to_int(receipt.get("gasUsed"))
    price = receipt.get("effectiveGasPrice") or tx.get("gasPrice")
    data = str(tx.get("input") or "0x")
    confirmations = None if head_block is None else max(0, head_block - block_number + 1)
    return Transaction(
        chain_id=chain.chain_id,
        transaction_hash=hash_of(tx.get("hash")),
        block_number=block_number,
        timestamp=timestamp,
        transaction_index=_optional_int(receipt.get("transactionIndex")),
        from_address=address_of(tx.get("from")),
        to_address=address_of(tx.get("to"), optional=True),
        created_contract=address_of(receipt.get("contractAddress"), optional=True),
        value_raw=raw_amount(tx.get("value")),
        status=TxStatus.SUCCESS if to_int(receipt.get("status")) == 1 else TxStatus.FAILED,
        fee_raw=None if price is None else str(gas_used * to_int(price)),
        method_id=data[:10] if len(data) >= 10 else None,
        is_contract_call=data not in ("", "0x"),
        confirmations=confirmations,
        confirmation_status=confirmation_of(confirmations, chain),
        provider=provider,
        data_quality=DataQuality.DECODED,
    )


def transfers_from_receipt(
    tx: dict[str, Any],
    receipt: dict[str, Any],
    chain: EvmChain,
    timestamp: datetime | None,
    provider: str,
    head_block: int | None = None,
) -> tuple[list[Transfer], int]:
    """The coin and the ERC-20 movements a receipt proves. Also: how many logs could not be read.

    Internal coin movements are not in a receipt (they need a trace), so they are not here.
    """
    if to_int(receipt.get("status")) != 1:
        return [], 0  # a reverted transaction moved nothing

    block_number = to_int(receipt.get("blockNumber"))
    confirmations = None if head_block is None else max(0, head_block - block_number + 1)
    common = {
        "chain_id": chain.chain_id,
        "transaction_hash": hash_of(receipt.get("transactionHash") or tx.get("hash")),
        "block_number": block_number,
        "timestamp": timestamp,
        "transaction_index": _optional_int(receipt.get("transactionIndex")),
        "confirmations": confirmations,
        "confirmation_status": confirmation_of(confirmations, chain),
        "provider": provider,
        "data_quality": DataQuality.DECODED,
    }

    transfers: list[Transfer] = []
    sent = raw_amount(tx.get("value"))
    if sent != "0":
        transfers.append(
            Transfer(
                event_key=NATIVE_KEY,
                transfer_type=TransferType.NATIVE,
                from_address=address_of(tx.get("from")),
                to_address=address_of(tx.get("to"), optional=True)
                or address_of(receipt.get("contractAddress"), optional=True),
                asset=chain.native,
                amount_raw=sent,
                **common,
            )
        )

    unreadable = 0
    token_transfers: list[Transfer] = []
    for log in receipt.get("logs") or []:
        topics = log.get("topics") or []
        if log.get("removed") or not topics or str(topics[0]).lower() != TRANSFER_TOPIC:
            continue
        if len(topics) == 4:
            continue  # ERC-721: the token id is indexed. Not modelled yet.
        data = str(log.get("data") or "0x")
        if len(topics) != 3 or not _is_one_word(data):
            unreadable += 1  # a Transfer event in a shape ERC-20 does not use
            continue
        contract = address_of(log.get("address"))
        sender, recipient = _topic_address(topics[1]), _topic_address(topics[2])
        amount = str(int(data, 16))
        token_transfers.append(
            Transfer(
                event_key=erc20_key(contract, sender, recipient, amount),
                transfer_type=TransferType.ERC20,
                from_address=sender,
                to_address=recipient,
                asset=Asset(symbol=None, contract_address=contract, decimals=None),
                amount_raw=amount,
                log_index=to_int(log.get("logIndex")),
                **common,
            )
        )
    token_transfers.sort(key=lambda transfer: transfer.log_index or 0)
    return transfers + assign_occurrences(token_transfers), unreadable


def _is_one_word(data: str) -> bool:
    return len(data) == 66 and data.startswith("0x") and _is_hex(data[2:])


def _is_hex(text: str) -> bool:
    try:
        int(text, 16)
    except ValueError:
        return False
    return True


def _topic_address(topic: str) -> str:
    text = str(topic).lower()
    if not (len(text) == 66 and text.startswith("0x") and _is_hex(text[2:])):
        raise MalformedRow("an indexed address topic is not 32 bytes")
    return "0x" + text[-40:]
