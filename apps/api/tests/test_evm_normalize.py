"""Provider rows and node receipts become our records, checked against real transactions.

The twelve files in tests/fixtures/evm are real recent transactions from public nodes (a plain
transfer, a token transfer, a swap with many transfers, a failed transaction, an NFT transfer
and a contract creation, on both chains). The Etherscan rows below are the samples printed in
Etherscan's own documentation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.chains.evm.chains import EVM_CHAINS, get_evm_chain
from app.chains.evm.normalize import (
    MalformedRow,
    address_of,
    alchemy_transfer,
    assign_occurrences,
    clean_text,
    confirmation_of,
    erc20_key,
    etherscan_internal_transfer,
    etherscan_native_transfer,
    etherscan_token_transfer,
    etherscan_transaction,
    hash_of,
    raw_amount,
    to_int,
    transaction_from_rpc,
    transfers_from_receipt,
)
from app.chains.models import Confirmation, DataQuality, TransferType, TxStatus
from tests.helpers import load_fixture

ETHEREUM = EVM_CHAINS["eip155:1"]
BSC = EVM_CHAINS["eip155:56"]
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# --- samples from Etherscan's documentation ----------------------------------------------------

TXLIST_ROW = {
    "blockNumber": "23467053",
    "blockHash": "0xf5646226f819fbdd6b2f1cb99de6e5d17da3d876ec166e69f4e9736c8ebf7ab4",
    "timeStamp": "1759129619",
    "hash": "0xf9db905d77704596d3600816bc70201586cfeec13bcf576320e2f38d6ca851a0",
    "nonce": "8",
    "transactionIndex": "184",
    "from": "0x2449ecef5012f0a0e153b278ef4fcc9625bc4c78",
    "to": "0xc5102fe9359fd9a28f877a67e36b0f050d81a3cc",
    "value": "0",
    "gas": "73271",
    "gasPrice": "238744402",
    "input": "0x5c19a95c0000000000000000000000002449ecef5012f0a0e153b278ef4fcc9625bc4c78",
    "methodId": "0x5c19a95c",
    "functionName": "delegate(address to)",
    "contractAddress": "",
    "cumulativeGasUsed": "22498564",
    "txreceipt_status": "1",
    "gasUsed": "48847",
    "confirmations": "589",
    "isError": "0",
}
INTERNAL_ROW = {
    "blockNumber": "2535368",
    "timeStamp": "1477837690",
    "hash": "0x8a1a9989bda84f80143181a68bc137ecefa64d0d4ebde45dd94fc0cf49e70cb6",
    "from": "0x20d42f2e99a421147acf198d775395cac2e8b03d",
    "to": "",
    "value": "0",
    "contractAddress": "0x2c1ba59d6f58433fb1eaee7d20b26ed83bda51a3",
    "input": "",
    "type": "create",
    "gas": "254791",
    "gasUsed": "46750",
    "traceId": "0",
    "isError": "0",
    "errCode": "",
}
TOKENTX_ROW = {
    "blockNumber": "4730207",
    "timeStamp": "1513240363",
    "hash": "0xe8c208398bd5ae8e4c237658580db56a2a94dfa0ca382c99b776fa6e7d31d5b4",
    "nonce": "406",
    "blockHash": "0x022c5e6a3d2487a8ccf8946a2ffb74938bf8e5c8a3f6d91b41c56378a96b5c37",
    "from": "0x642ae78fafbb8032da552d619ad43f1d81e4dd7c",
    "contractAddress": "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2",
    "to": "0x4e83362442b8d1bec281594cea3050c8eb01311c",
    "value": "5901522149285533025181",
    "tokenName": "Maker",
    "tokenSymbol": "MKR",
    "tokenDecimal": "18",
    "statusRep": "1",
    "transactionIndex": "81",
    "gas": "940000",
    "gasPrice": "32010000000",
    "gasUsed": "77759",
    "cumulativeGasUsed": "2523379",
    "input": "deprecated",
    "methodId": "0xbe040fb0",
    "functionName": "redeem()",
    "confirmations": "18737452",
}


def with_changes(row, **changes):
    return {**row, **changes}


# --- scalars -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("42", 42), ("0x2a", 42), ("0X2A", 42), (42, 42), (" 7 ", 7), ("0x0", 0)],
)
def test_numbers_come_from_decimal_strings_hex_strings_or_ints(value, expected):
    assert to_int(value) == expected


@pytest.mark.parametrize("value", [None, True, "", "abc", "0x", "1.5", [], 1.5])
def test_things_that_are_not_numbers_are_refused(value):
    with pytest.raises(MalformedRow):
        to_int(value)


def test_amounts_are_integer_strings_and_never_negative():
    assert raw_amount("00042") == "42"
    assert raw_amount("0xde0b6b3a7640000") == str(10**18)
    assert raw_amount(str(2**256 - 1)) == str(2**256 - 1)  # a uint256 survives
    with pytest.raises(MalformedRow):
        raw_amount("-1")


def test_addresses_are_lowercased_and_checked():
    mixed = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
    assert address_of(mixed) == mixed.lower()
    assert address_of("", optional=True) is None
    assert address_of(None, optional=True) is None
    for bad in ("", None, "0x123", "5aaeb6053f3e94c9b9a09f33669435e7ef1beaed", "0xZZ" + "0" * 38):
        with pytest.raises(MalformedRow):
            address_of(bad)


def test_transaction_hashes_must_be_32_bytes():
    assert hash_of("0x" + "AB" * 32) == "0x" + "ab" * 32
    with pytest.raises(MalformedRow):
        hash_of("0x1234")


def test_token_names_cannot_smuggle_control_characters_or_unbounded_text():
    assert clean_text("Evil\nToken\x00\x1b[31m") == "EvilToken[31m"
    assert len(clean_text("x" * 5000)) == 64
    assert clean_text("   ") is None and clean_text(None) is None


# --- Etherscan rows ----------------------------------------------------------------------------


def test_a_txlist_row_becomes_a_transaction():
    tx = etherscan_transaction(TXLIST_ROW, ETHEREUM)

    assert tx.transaction_hash == TXLIST_ROW["hash"]
    assert (tx.block_number, tx.transaction_index) == (23467053, 184)
    assert tx.timestamp == datetime(2025, 9, 29, 7, 6, 59, tzinfo=UTC)  # 1759129619, per `date -u`
    assert tx.from_address == TXLIST_ROW["from"] and tx.to_address == TXLIST_ROW["to"]
    assert tx.value_raw == "0" and tx.status is TxStatus.SUCCESS
    assert tx.fee_raw == str(48847 * 238744402)
    assert (tx.method_id, tx.function_name) == ("0x5c19a95c", "delegate(address to)")
    assert tx.is_contract_call and tx.created_contract is None
    assert (tx.confirmations, tx.confirmation_status) == (589, Confirmation.CONFIRMED)
    assert (tx.provider, tx.data_quality) == ("etherscan", DataQuality.INDEXED)


def test_a_shallow_transaction_is_not_yet_confirmed():
    tx = etherscan_transaction(with_changes(TXLIST_ROW, confirmations="3"), ETHEREUM)
    assert tx.confirmation_status is Confirmation.UNCONFIRMED
    unknown = etherscan_transaction(with_changes(TXLIST_ROW, confirmations=""), ETHEREUM)
    assert (unknown.confirmations, unknown.confirmation_status) == (None, Confirmation.UNKNOWN)


def test_the_confirmation_depth_differs_per_chain():
    row = with_changes(TXLIST_ROW, confirmations="13")
    assert etherscan_transaction(row, ETHEREUM).confirmation_status is Confirmation.CONFIRMED
    assert etherscan_transaction(row, BSC).confirmation_status is Confirmation.UNCONFIRMED


def test_a_value_carrying_transaction_also_yields_a_native_transfer():
    row = with_changes(TXLIST_ROW, value="1500000000000000000", input="0x", methodId="0x")
    transfer = etherscan_native_transfer(row, ETHEREUM)

    assert transfer.transfer_type is TransferType.NATIVE and transfer.event_key == "native"
    assert transfer.amount_raw == "1500000000000000000"
    assert transfer.asset == ETHEREUM.native and transfer.asset.decimals == 18
    assert (transfer.from_address, transfer.to_address) == (row["from"], row["to"])
    assert transfer.identity == (ETHEREUM.chain_id, row["hash"], "native")
    assert etherscan_transaction(row, ETHEREUM).method_id is None  # "0x" is no method


def test_a_zero_value_or_failed_transaction_moved_nothing():
    assert etherscan_native_transfer(TXLIST_ROW, ETHEREUM) is None
    failed = with_changes(TXLIST_ROW, value="5", isError="1", txreceipt_status="0")
    assert etherscan_native_transfer(failed, ETHEREUM) is None
    assert etherscan_transaction(failed, ETHEREUM).status is TxStatus.FAILED


def test_value_sent_while_creating_a_contract_went_to_the_new_contract():
    creation = with_changes(
        TXLIST_ROW, to="", value="9", contractAddress="0x2c1ba59d6f58433fb1eaee7d20b26ed83bda51a3"
    )
    transfer = etherscan_native_transfer(creation, ETHEREUM)
    tx = etherscan_transaction(creation, ETHEREUM)
    assert transfer.to_address == "0x2c1ba59d6f58433fb1eaee7d20b26ed83bda51a3"
    assert tx.to_address is None and tx.created_contract == transfer.to_address


def test_an_internal_row_is_a_transfer_only_if_it_moved_value_and_did_not_revert():
    assert etherscan_internal_transfer(INTERNAL_ROW, ETHEREUM) is None  # value 0 (a create call)

    row = with_changes(INTERNAL_ROW, value="250", to="0x" + "ab" * 20, traceId="0_1", type="call")
    transfer = etherscan_internal_transfer(row, ETHEREUM)
    assert transfer.transfer_type is TransferType.INTERNAL
    assert transfer.amount_raw == "250" and transfer.trace_id == "0_1"
    assert transfer.event_key == f"internal:{row['from']}:{row['to']}:250"
    assert transfer.timestamp == datetime(2016, 10, 30, 14, 28, 10, tzinfo=UTC)  # 1477837690

    assert etherscan_internal_transfer(with_changes(row, isError="1"), ETHEREUM) is None


def test_a_token_row_becomes_an_erc20_transfer_with_its_asset():
    transfer = etherscan_token_transfer(TOKENTX_ROW, ETHEREUM)

    assert transfer.transfer_type is TransferType.ERC20
    assert transfer.amount_raw == "5901522149285533025181"
    assert (transfer.asset.symbol, transfer.asset.name, transfer.asset.decimals) == (
        "MKR",
        "Maker",
        18,
    )
    assert transfer.asset.contract_address == "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2"
    assert transfer.event_key == erc20_key(
        transfer.asset.contract_address,
        TOKENTX_ROW["from"],
        TOKENTX_ROW["to"],
        "5901522149285533025181",
    )
    assert transfer.log_index is None  # Etherscan's token endpoint does not say
    assert transfer.transaction_index == 81
    assert transfer.confirmation_status is Confirmation.CONFIRMED


def test_a_token_without_decimals_keeps_decimals_unknown_instead_of_guessing():
    transfer = etherscan_token_transfer(with_changes(TOKENTX_ROW, tokenDecimal=""), ETHEREUM)
    assert transfer.asset.decimals is None


@pytest.mark.parametrize(
    ("builder", "row", "change"),
    [
        (etherscan_transaction, TXLIST_ROW, {"hash": "0x12"}),
        (etherscan_transaction, TXLIST_ROW, {"from": ""}),
        (etherscan_transaction, TXLIST_ROW, {"timeStamp": "soon"}),
        (etherscan_native_transfer, with_changes(TXLIST_ROW, value="5"), {"value": "-5"}),
        (etherscan_internal_transfer, INTERNAL_ROW, {"value": "lots"}),
        (etherscan_token_transfer, TOKENTX_ROW, {"contractAddress": "not an address"}),
        (etherscan_token_transfer, TOKENTX_ROW, {"value": None}),
    ],
)
def test_a_row_that_cannot_be_what_it_claims_is_refused_not_guessed(builder, row, change):
    with pytest.raises(MalformedRow):
        builder(with_changes(row, **change), ETHEREUM)


# --- identical movements inside one transaction -----------------------------------------------


def test_identical_movements_of_one_transaction_are_told_apart_by_their_order():
    first = etherscan_token_transfer(TOKENTX_ROW, ETHEREUM)
    other = etherscan_token_transfer(with_changes(TOKENTX_ROW, value="1"), ETHEREUM)
    elsewhere = etherscan_token_transfer(with_changes(TOKENTX_ROW, hash="0x" + "cd" * 32), ETHEREUM)

    result = assign_occurrences([first, first, other, first, elsewhere])
    keys = [t.event_key for t in result]

    assert keys[0] == first.event_key
    assert keys[1] == first.event_key + "#1" and keys[3] == first.event_key + "#2"
    assert keys[2] == other.event_key and keys[4] == elsewhere.event_key  # others unaffected
    # `elsewhere` has the same content as `first` but belongs to another transaction, so it keeps
    # the bare key; identity (chain, transaction, key) is what must be unique.
    assert len({t.identity for t in result}) == len(result)


def test_the_same_movement_listed_by_two_providers_has_the_same_identity():
    etherscan = etherscan_token_transfer(TOKENTX_ROW, ETHEREUM)
    alchemy = alchemy_transfer(
        {
            "blockNum": hex(4730207),
            "uniqueId": TOKENTX_ROW["hash"] + ":log:81",
            "hash": TOKENTX_ROW["hash"],
            "from": TOKENTX_ROW["from"],
            "to": TOKENTX_ROW["to"],
            "value": 5901.52,
            "asset": "MKR",
            "category": "erc20",
            "rawContract": {
                "value": hex(5901522149285533025181),
                "address": TOKENTX_ROW["contractAddress"],
                "decimal": "0x12",
            },
        },
        ETHEREUM,
        timestamp=None,
    )
    assert alchemy.identity == etherscan.identity
    assert alchemy.log_index == 81 and alchemy.asset.decimals == 18


# --- Alchemy transfers -------------------------------------------------------------------------

ALCHEMY_ROW = {
    "blockNum": "0xc5043f",
    "uniqueId": "0x3847245c01829b043431067fb2bfa95f7b5bdc7e4246c843e7a573ab6f26f5ff:external",
    "hash": "0x3847245c01829b043431067fb2bfa95f7b5bdc7e4246c843e7a573ab6f26f5ff",
    "from": "0xef4396d9ff8107086d215a1c9f8866c54795d7c7",
    "to": "0x5c43b1ed97e52d009611d89b74fa829fe4ac56b1",
    "value": 0.1,
    "erc721TokenId": None,
    "erc1155Metadata": None,
    "asset": "ETH",
    "category": "external",
    "rawContract": {"value": "0x16345785d8a0000", "address": None, "decimal": "0x12"},
}


def test_an_external_alchemy_transfer_is_a_native_transfer_in_raw_units():
    when = datetime(2021, 5, 1, tzinfo=UTC)
    transfer = alchemy_transfer(ALCHEMY_ROW, ETHEREUM, timestamp=when)

    assert transfer.transfer_type is TransferType.NATIVE and transfer.event_key == "native"
    assert transfer.amount_raw == "100000000000000000"  # exact: the float 0.1 is never used
    assert transfer.block_number == 0xC5043F and transfer.timestamp == when
    assert transfer.asset == ETHEREUM.native
    assert (transfer.provider, transfer.data_quality) == ("alchemy", DataQuality.INDEXED)


def test_alchemy_internal_transfers_carry_their_trace_position():
    row = with_changes(ALCHEMY_ROW, category="internal", uniqueId="0xabc:internal:3")
    transfer = alchemy_transfer(row, ETHEREUM, timestamp=None)
    assert transfer.transfer_type is TransferType.INTERNAL and transfer.trace_id == "3"
    assert transfer.event_key == f"internal:{row['from']}:{row['to']}:100000000000000000"


def test_nfts_and_unknown_categories_are_not_modelled():
    for category in ("erc721", "erc1155", "specialnft", "something-new"):
        assert (
            alchemy_transfer(with_changes(ALCHEMY_ROW, category=category), ETHEREUM, None) is None
        )


def test_an_alchemy_transfer_without_its_raw_amount_is_refused():
    row = with_changes(ALCHEMY_ROW, rawContract={"value": None, "address": None})
    with pytest.raises(MalformedRow):
        alchemy_transfer(row, ETHEREUM, None)


# --- real transactions from public nodes -------------------------------------------------------

FIXTURE_NAMES = [
    f"{chain}_{kind}"
    for chain in ("ethereum", "bsc")
    for kind in ("native", "erc20_single", "erc20_multi", "failed", "nft", "creation")
]


def decode(name):
    data = load_fixture(f"evm/{name}")
    chain = get_evm_chain(data["chain"])
    when = datetime.fromtimestamp(int(data["block_timestamp"], 16), UTC)
    tx, receipt = data["tx"], data["receipt"]
    head = int(receipt["blockNumber"], 16) + 100
    transaction = transaction_from_rpc(tx, receipt, chain, when, "node", head_block=head)
    transfers, unreadable = transfers_from_receipt(tx, receipt, chain, when, "node", head)
    return data, transaction, transfers, unreadable


def token_logs(receipt):
    return [
        log
        for log in receipt["logs"]
        if log["topics"] and log["topics"][0] == TRANSFER_TOPIC and len(log["topics"]) == 3
    ]


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_real_transaction_decodes_into_json_ready_records(name):
    _, transaction, transfers, unreadable = decode(name)
    assert unreadable == 0
    json.dumps(transaction.to_dict())
    json.dumps([transfer.to_dict() for transfer in transfers])
    assert len({t.identity for t in transfers}) == len(transfers)  # no two share an identity
    assert all(t.data_quality is DataQuality.DECODED for t in transfers)


@pytest.mark.parametrize("chain", ["ethereum", "bsc"])
def test_a_real_plain_transfer_is_one_native_movement(chain):
    data, transaction, transfers, _ = decode(f"{chain}_native")
    tx, receipt = data["tx"], data["receipt"]

    (transfer,) = transfers
    assert transfer.transfer_type is TransferType.NATIVE
    assert transfer.amount_raw == str(int(tx["value"], 16))
    assert (transfer.from_address, transfer.to_address) == (tx["from"], tx["to"])
    assert transfer.block_number == int(receipt["blockNumber"], 16)
    assert transaction.status is TxStatus.SUCCESS and not transaction.is_contract_call
    assert transaction.fee_raw == str(
        int(receipt["gasUsed"], 16) * int(receipt["effectiveGasPrice"], 16)
    )
    assert (
        transaction.confirmations == 101
        and transaction.confirmation_status is Confirmation.CONFIRMED
    )


@pytest.mark.parametrize("chain", ["ethereum", "bsc"])
def test_a_real_token_transfer_is_decoded_with_its_true_log_index(chain):
    data, transaction, transfers, _ = decode(f"{chain}_erc20_single")
    (log,) = token_logs(data["receipt"])

    (transfer,) = [t for t in transfers if t.transfer_type is TransferType.ERC20]
    assert transfer.asset.contract_address == log["address"]
    assert transfer.amount_raw == str(int(log["data"], 16))
    assert transfer.from_address == "0x" + log["topics"][1][-40:]
    assert transfer.to_address == "0x" + log["topics"][2][-40:]
    assert transfer.log_index == int(log["logIndex"], 16)
    assert transfer.asset.decimals is None  # a receipt does not say; never guessed
    assert transaction.is_contract_call and transaction.method_id.startswith("0x")


@pytest.mark.parametrize("chain", ["ethereum", "bsc"])
def test_a_real_swap_yields_every_token_movement_in_log_order(chain):
    data, _, transfers, _ = decode(f"{chain}_erc20_multi")
    logs = token_logs(data["receipt"])

    tokens = [t for t in transfers if t.transfer_type is TransferType.ERC20]
    assert len(tokens) == len(logs) >= 3
    assert [t.log_index for t in tokens] == sorted(int(log["logIndex"], 16) for log in logs)
    assert [t.amount_raw for t in tokens] == [str(int(log["data"], 16)) for log in logs]


@pytest.mark.parametrize("chain", ["ethereum", "bsc"])
def test_a_real_failed_transaction_moved_nothing_but_is_still_recorded(chain):
    data, transaction, transfers, _ = decode(f"{chain}_failed")
    assert transfers == []
    assert transaction.status is TxStatus.FAILED
    assert int(data["tx"]["value"], 16) >= 0  # whatever it tried to send did not move


@pytest.mark.parametrize("chain", ["ethereum", "bsc"])
def test_a_real_nft_transfer_is_not_mistaken_for_a_token_transfer(chain):
    data, _, transfers, unreadable = decode(f"{chain}_nft")
    four_topic = [
        log
        for log in data["receipt"]["logs"]
        if log["topics"] and log["topics"][0] == TRANSFER_TOPIC and len(log["topics"]) == 4
    ]
    assert four_topic  # the fixture really contains an ERC-721 style event
    assert not [t for t in transfers if t.transfer_type is TransferType.ERC20]
    assert unreadable == 0


@pytest.mark.parametrize("chain", ["ethereum", "bsc"])
def test_a_real_contract_creation_has_no_recipient_but_a_created_contract(chain):
    data, transaction, _, _ = decode(f"{chain}_creation")
    assert data["tx"]["to"] is None
    assert transaction.to_address is None
    assert transaction.created_contract == data["receipt"]["contractAddress"]


def test_the_chain_lookup_accepts_slugs_and_caip2_ids_and_ignores_other_families():
    assert get_evm_chain("ethereum") is ETHEREUM and get_evm_chain("eip155:56") is BSC
    assert get_evm_chain("bitcoin") is None and get_evm_chain("nonsense") is None


@pytest.mark.parametrize(
    ("chain", "confirmations", "expected"),
    [
        (ETHEREUM, 12, Confirmation.CONFIRMED),  # exactly the depth counts
        (ETHEREUM, 11, Confirmation.UNCONFIRMED),  # one short does not
        (ETHEREUM, 0, Confirmation.UNCONFIRMED),
        (BSC, 15, Confirmation.CONFIRMED),
        (BSC, 14, Confirmation.UNCONFIRMED),
        (ETHEREUM, None, Confirmation.UNKNOWN),
    ],
)
def test_a_block_is_confirmed_at_exactly_the_chains_depth(chain, confirmations, expected):
    assert confirmation_of(confirmations, chain) is expected
