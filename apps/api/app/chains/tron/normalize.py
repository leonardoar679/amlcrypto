"""Turning TronGrid's rows and a transaction's details into our records.

Pure functions, no network. TRON has four kinds of value movement, and each is identified in a way
that is stable whichever feed reported it (`Transfer.event_key`):

    native    "native"                                   the TRX a transaction sends (one per tx)
    trc10     "trc10:<asset id>:<from>:<to>:<amount>"    a token that is not a contract
    trc20     "trc20:<contract>:<from>:<to>:<amount>"    a token event; "#1", "#2" tell apart
                                                         identical repeats in one transaction
    internal  "internal:<internal transaction id>"       TRON gives each one a unique id, and the
                                                         same id appears in the address feed and
                                                         in a transaction's details

Amounts are in sun for TRX (1 TRX = 10**6 sun) and in the token's smallest unit otherwise. TronGrid
sends addresses as hex ("41...") in some places and Base58 in others; both are accepted and the
Base58 form is stored. Times are milliseconds. TRC-20 and internal rows carry no block number, only
a time, so `block_number` is None for them.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.chains.encoding import b58check_encode
from app.chains.models import (
    Asset,
    Confirmation,
    DataQuality,
    Transaction,
    Transfer,
    TransferType,
    TxStatus,
)
from app.chains.tron.address import TronAddressValidator
from app.chains.tron.chains import TronChain
from app.chains.values import (
    MalformedRow,
    assign_occurrences,
    clean_text,
    optional_int,
    raw_amount,
    to_int,
)

NATIVE_KEY = "native"
TRANSFER_TOPIC = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

_TXID = re.compile(r"^[0-9a-fA-F]{64}$")
_HEX = re.compile(r"^[0-9a-fA-F]+$")
_VALIDATOR = TronAddressValidator()


# --- scalars -----------------------------------------------------------------------------------


def hex_to_base58(text: str) -> str:
    """ "41a614...": the 21-byte hex form of an address, as Base58Check."""
    if len(text) != 42 or not text.lower().startswith("41") or not _HEX.match(text):
        raise MalformedRow(f"not a TRON hex address: {text[:50]!r}")
    return b58check_encode(bytes.fromhex(text))


def tron_address(value: Any) -> str:
    """A Base58 TRON address from either the hex or the Base58 form, checked."""
    text = str(value or "").strip()
    if not text:
        raise MalformedRow("missing address")
    if len(text) == 42 and _HEX.match(text):
        return hex_to_base58(text)
    check = _VALIDATOR.check(text)
    if not check.valid or check.normalized is None:
        raise MalformedRow(f"not a TRON address: {text[:50]!r}")
    return check.normalized


def txid_of(value: Any) -> str:
    text = str(value or "").strip().lower().removeprefix("0x")
    if not _TXID.match(text):
        raise MalformedRow(f"not a transaction id: {text[:70]!r}")
    return text


def moment_ms(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(to_int(value) / 1000, UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise MalformedRow("timestamp out of range") from error


def confirmation_of(confirmations: int | None, chain: TronChain) -> Confirmation:
    if confirmations is None:
        return Confirmation.UNKNOWN
    if confirmations >= chain.confirmation_depth:
        return Confirmation.CONFIRMED
    return Confirmation.UNCONFIRMED


def asset_id_of(asset_name: Any) -> str:
    """A TRC-10 token id. Some endpoints send it as hex-encoded ASCII ("31303035313933"), the
    address feed sends the id itself ("1005193"); both are accepted.

    Hex is only assumed when it decodes to digits: ids are numeric, and a plain id such as
    "1005190" has an odd length so it can never be mistaken for hex.
    """
    text = str(asset_name or "").strip()
    if len(text) % 2 == 0 and _HEX.match(text):
        try:
            decoded = bytes.fromhex(text).decode("ascii")
        except (ValueError, UnicodeDecodeError):
            decoded = ""
        if decoded.isdigit():
            return decoded
    cleaned = clean_text(text)
    if not cleaned:
        raise MalformedRow("empty TRC-10 asset name")
    return cleaned


def trc20_key(contract: str, sender: str, recipient: str, amount: str) -> str:
    return f"trc20:{contract}:{sender}:{recipient}:{amount}"


def trc10_key(asset_id: str, sender: str, recipient: str, amount: str) -> str:
    return f"trc10:{asset_id}:{sender}:{recipient}:{amount}"


# --- the contract inside a transaction ---------------------------------------------------------


def _contract(raw_data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    try:
        contract = raw_data["contract"][0]
        return str(contract["type"]), contract["parameter"]["value"]
    except (KeyError, IndexError, TypeError) as error:
        raise MalformedRow("a transaction without a contract") from error


def _contract_result(tx: dict[str, Any]) -> str | None:
    ret = tx.get("ret")
    if isinstance(ret, list) and ret and isinstance(ret[0], dict):
        return ret[0].get("contractRet")
    return None


def _status(tx: dict[str, Any]) -> TxStatus:
    return TxStatus.SUCCESS if _contract_result(tx) == "SUCCESS" else TxStatus.FAILED


def _contract_movements(
    kind: str, value: dict[str, Any], common: dict[str, Any], chain: TronChain
) -> list[Transfer]:
    """What a transaction's own contract moves: TRX, a TRC-10 token, or the TRX of a call."""
    if kind == "TransferContract":
        amount = raw_amount(value.get("amount"))
        if amount == "0":
            return []
        return [
            Transfer(
                event_key=NATIVE_KEY,
                transfer_type=TransferType.NATIVE,
                from_address=tron_address(value.get("owner_address")),
                to_address=tron_address(value.get("to_address")),
                asset=chain.native,
                amount_raw=amount,
                **common,
            )
        ]
    if kind == "TransferAssetContract":
        amount = raw_amount(value.get("amount"))
        if amount == "0":
            return []
        asset_id = asset_id_of(value.get("asset_name"))
        sender, recipient = (
            tron_address(value.get("owner_address")),
            tron_address(value.get("to_address")),
        )
        return [
            Transfer(
                event_key=trc10_key(asset_id, sender, recipient, amount),
                transfer_type=TransferType.TRC10,
                from_address=sender,
                to_address=recipient,
                asset=Asset(symbol=None, contract_address=None, decimals=None, asset_id=asset_id),
                amount_raw=amount,
                **common,
            )
        ]
    if kind == "TriggerSmartContract":
        amount = raw_amount(value.get("call_value", 0))
        if amount == "0":
            return []
        return [
            Transfer(
                event_key=NATIVE_KEY,
                transfer_type=TransferType.NATIVE,
                from_address=tron_address(value.get("owner_address")),
                to_address=tron_address(value.get("contract_address")),
                asset=chain.native,
                amount_raw=amount,
                **common,
            )
        ]
    return []  # staking, delegation, account creation ...: not a movement between accounts


# --- the address feeds -------------------------------------------------------------------------


def tron_transaction(
    row: dict[str, Any], chain: TronChain, provider: str = "trongrid"
) -> Transaction:
    """A row of /v1/accounts/{address}/transactions."""
    kind, value = _contract(row.get("raw_data") or {})
    to_address: str | None = None
    value_raw = "0"
    method_id: str | None = None
    if kind in ("TransferContract", "TransferAssetContract"):
        to_address = tron_address(value.get("to_address"))
        if kind == "TransferContract":
            value_raw = raw_amount(value.get("amount"))
    elif kind == "TriggerSmartContract":
        to_address = tron_address(value.get("contract_address"))
        value_raw = raw_amount(value.get("call_value", 0))
        data = str(value.get("data") or "")
        method_id = f"0x{data[:8]}" if len(data) >= 8 else None
    ret = row.get("ret")
    fee = ret[0].get("fee") if isinstance(ret, list) and ret and isinstance(ret[0], dict) else None
    return Transaction(
        chain_id=chain.chain_id,
        transaction_hash=txid_of(row.get("txID")),
        block_number=to_int(row.get("blockNumber")),
        timestamp=moment_ms(row.get("block_timestamp")),
        from_address=tron_address(value.get("owner_address")),
        to_address=to_address,
        created_contract=None,
        value_raw=value_raw,
        status=_status(row),
        fee_raw=None if fee is None else raw_amount(fee),
        method_id=method_id,
        is_contract_call=kind in ("TriggerSmartContract", "CreateSmartContract"),
        confirmation_status=Confirmation.CONFIRMED,  # the feed is read with only_confirmed
        provider=provider,
        data_quality=DataQuality.INDEXED,
    )


def tron_native_transfers(
    row: dict[str, Any], chain: TronChain, provider: str = "trongrid"
) -> list[Transfer]:
    """What the contract of a feed row moved. Nothing, if the transaction failed."""
    if _status(row) is not TxStatus.SUCCESS:
        return []
    kind, value = _contract(row.get("raw_data") or {})
    common = {
        "chain_id": chain.chain_id,
        "transaction_hash": txid_of(row.get("txID")),
        "block_number": to_int(row.get("blockNumber")),
        "timestamp": moment_ms(row.get("block_timestamp")),
        "confirmation_status": Confirmation.CONFIRMED,
        "provider": provider,
        "data_quality": DataQuality.INDEXED,
    }
    return _contract_movements(kind, value, common, chain)


def tron_internal_transfer(
    row: dict[str, Any], chain: TronChain, provider: str = "trongrid"
) -> Transfer | None:
    """A row of /v1/accounts/{address}/internal-transactions. None unless it moved TRX."""
    data = row.get("data") or {}
    if data.get("rejected"):
        return None
    call_value = data.get("call_value") or {}
    amount = raw_amount(call_value.get("_", 0))  # "_" is TRX; other keys are TRC-10 token ids
    if amount == "0":
        return None
    internal_id = txid_of(row.get("internal_tx_id"))
    return Transfer(
        chain_id=chain.chain_id,
        transaction_hash=txid_of(row.get("tx_id")),
        event_key=f"internal:{internal_id}",
        transfer_type=TransferType.INTERNAL,
        block_number=None,
        timestamp=moment_ms(row.get("block_timestamp")),
        from_address=tron_address(row.get("from_address")),
        to_address=tron_address(row.get("to_address")),
        asset=chain.native,
        amount_raw=amount,
        confirmation_status=Confirmation.CONFIRMED,
        provider=provider,
        data_quality=DataQuality.INDEXED,
    )


def tron_trc20_transfer(
    row: dict[str, Any], chain: TronChain, provider: str = "trongrid"
) -> Transfer | None:
    """A row of /v1/accounts/{address}/transactions/trc20."""
    if row.get("type") != "Transfer":
        return None
    info = row.get("token_info") or {}
    contract = tron_address(info.get("address"))
    sender, recipient = tron_address(row.get("from")), tron_address(row.get("to"))
    amount = raw_amount(row.get("value"))
    return Transfer(
        chain_id=chain.chain_id,
        transaction_hash=txid_of(row.get("transaction_id")),
        event_key=trc20_key(contract, sender, recipient, amount),
        transfer_type=TransferType.TRC20,
        block_number=None,
        timestamp=moment_ms(row.get("block_timestamp")),
        from_address=sender,
        to_address=recipient,
        asset=Asset(
            symbol=clean_text(info.get("symbol")),
            name=clean_text(info.get("name")),
            contract_address=contract,
            decimals=optional_int(info.get("decimals")),
        ),
        amount_raw=amount,
        confirmation_status=Confirmation.CONFIRMED,
        provider=provider,
        data_quality=DataQuality.INDEXED,
    )


# --- one transaction's details -----------------------------------------------------------------


def tron_transaction_from_details(
    tx: dict[str, Any],
    info: dict[str, Any],
    chain: TronChain,
    provider: str = "trongrid",
    head_block: int | None = None,
) -> Transaction:
    """From /wallet/gettransactionbyid (visible) and /wallet/gettransactioninfobyid."""
    kind, value = _contract(tx.get("raw_data") or {})
    block = to_int(info.get("blockNumber"))
    confirmations = None if head_block is None else max(0, head_block - block + 1)
    to_address: str | None = None
    value_raw = "0"
    method_id: str | None = None
    if kind in ("TransferContract", "TransferAssetContract"):
        to_address = tron_address(value.get("to_address"))
        if kind == "TransferContract":
            value_raw = raw_amount(value.get("amount"))
    elif kind == "TriggerSmartContract":
        to_address = tron_address(value.get("contract_address"))
        value_raw = raw_amount(value.get("call_value", 0))
        data = str(value.get("data") or "")
        method_id = f"0x{data[:8]}" if len(data) >= 8 else None
    return Transaction(
        chain_id=chain.chain_id,
        transaction_hash=txid_of(info.get("id") or tx.get("txID")),
        block_number=block,
        timestamp=moment_ms(info.get("blockTimeStamp")),
        from_address=tron_address(value.get("owner_address")),
        to_address=to_address,
        created_contract=None,
        value_raw=value_raw,
        status=_status(tx),
        fee_raw=raw_amount(info.get("fee", 0)),
        method_id=method_id,
        is_contract_call=kind in ("TriggerSmartContract", "CreateSmartContract"),
        confirmation_status=confirmation_of(confirmations, chain),
        confirmations=confirmations,
        provider=provider,
        data_quality=DataQuality.DECODED,
    )


def tron_transfers_from_details(
    tx: dict[str, Any],
    info: dict[str, Any],
    chain: TronChain,
    provider: str = "trongrid",
    head_block: int | None = None,
) -> tuple[list[Transfer], int]:
    """Every movement the details prove, and how many events could not be read.

    Unlike an Ethereum receipt, TRON's details list the internal TRX movements too.
    """
    if _status(tx) is not TxStatus.SUCCESS:
        return [], 0
    block = to_int(info.get("blockNumber"))
    confirmations = None if head_block is None else max(0, head_block - block + 1)
    common = {
        "chain_id": chain.chain_id,
        "transaction_hash": txid_of(info.get("id") or tx.get("txID")),
        "block_number": block,
        "timestamp": moment_ms(info.get("blockTimeStamp")),
        "confirmation_status": confirmation_of(confirmations, chain),
        "confirmations": confirmations,
        "provider": provider,
        "data_quality": DataQuality.DECODED,
    }
    kind, value = _contract(tx.get("raw_data") or {})
    transfers = _contract_movements(kind, value, common, chain)

    unreadable = 0
    tokens: list[Transfer] = []
    for position, log in enumerate(info.get("log") or []):
        topics = [str(topic).lower().removeprefix("0x") for topic in log.get("topics") or []]
        if not topics or topics[0] != TRANSFER_TOPIC:
            continue
        data = str(log.get("data") or "").removeprefix("0x")
        if len(topics) != 3 or len(data) != 64 or not _HEX.match(data):
            if len(topics) != 4:  # four topics is an NFT transfer: not modelled, not unreadable
                unreadable += 1
            continue
        contract = hex_to_base58("41" + str(log.get("address") or ""))
        sender, recipient = _topic_address(topics[1]), _topic_address(topics[2])
        amount = str(int(data, 16))
        tokens.append(
            Transfer(
                event_key=trc20_key(contract, sender, recipient, amount),
                transfer_type=TransferType.TRC20,
                from_address=sender,
                to_address=recipient,
                asset=Asset(symbol=None, contract_address=contract, decimals=None),
                amount_raw=amount,
                log_index=position,
                **common,
            )
        )
    transfers += assign_occurrences(tokens)

    for internal in info.get("internal_transactions") or []:
        if internal.get("rejected"):
            continue
        entries = internal.get("callValueInfo") or []
        trx = sum(
            to_int(entry["callValue"])
            for entry in entries
            if entry.get("callValue") and not entry.get("tokenId")
        )
        if trx <= 0:
            continue
        transfers.append(
            Transfer(
                event_key=f"internal:{txid_of(internal.get('hash'))}",
                transfer_type=TransferType.INTERNAL,
                from_address=tron_address(internal.get("caller_address")),
                to_address=tron_address(internal.get("transferTo_address")),
                asset=chain.native,
                amount_raw=str(trx),
                **common,
            )
        )
    return transfers, unreadable


def _topic_address(topic: str) -> str:
    if len(topic) != 64 or not _HEX.match(topic):
        raise MalformedRow("an indexed address topic is not 32 bytes")
    return hex_to_base58("41" + topic[-40:])
