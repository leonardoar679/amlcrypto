"""Solana transactions become our records, checked against real transactions and balance changes.

The files in tests/fixtures/solana are real transactions from a busy wallet (2026-11-05), in the
form `getTransactionsForAddress` returns them: a legacy, a version 0 and version 1 ones, a failed
one, a swap through a temporary wrapped-SOL account, a trade where a program paid SOL back by
editing lamports directly, and token transfers in both token programs.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest

from app.chains.models import Confirmation, DataQuality, TransferType, TxStatus
from app.chains.solana.chains import SOLANA_CHAIN, get_solana_chain
from app.chains.solana.normalize import (
    SYSTEM_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    WSOL_MINT,
    signature_of,
    solana_address,
    solana_transaction,
)
from app.chains.values import MalformedRow
from tests.fake_helius import (
    SYSTEM,
    TOKEN,
    WSOL,
    address,
    instruction,
    item,
    signature,
    token_entry,
)
from tests.helpers import load_fixture

CHAIN = SOLANA_CHAIN
NAMES = [
    "system_transfer_legacy",
    "system_transfer_v0",
    "token_transfer_checked",
    "ata_create_and_transfer",
    "close_account",
    "hidden_payout_v1",
    "failed",
    "swap_temp_wsol_v1",
    "token_transfer_unchecked_swap",
]
WALLET = "4jErKA4q4sUbAUWpseN8s2anSbdsTAAjX2PnwFRzEwQ2"


def real(name):
    return load_fixture(f"solana/{name}")["item"]


def decode(name):
    return solana_transaction(real(name), CHAIN)


def positions(transaction):
    return {t.trace_id: t for t in transaction.transfers}


# --- the chain and scalars ---------------------------------------------------------------------


def test_the_chain_lookup_knows_solana_only():
    assert get_solana_chain("solana") is CHAIN
    assert get_solana_chain("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp") is CHAIN
    assert get_solana_chain("ethereum") is None and get_solana_chain("nonsense") is None


def test_sol_is_counted_in_lamports_nine_decimals_down_and_an_update_allows_for_a_lagging_index():
    assert (CHAIN.native.symbol, CHAIN.native.decimals) == ("SOL", 9)
    assert CHAIN.incremental_start(1000) == 1000 - CHAIN.lag_margin
    assert CHAIN.lag_margin > 0 and CHAIN.incremental_start(0) == 0


def test_program_ids_are_the_well_known_ones():
    assert SYSTEM_PROGRAM == SYSTEM == "1" * 32
    assert TOKEN_PROGRAM == TOKEN and TOKEN_2022_PROGRAM.startswith("Tokenz")
    assert WSOL_MINT == WSOL


def test_a_signature_is_64_bytes_of_base58_and_an_address_32():
    assert signature_of(signature(1)) == signature(1)
    assert solana_address(address(1)) == address(1)
    for bad in ("", "short", "0OIl" * 20, address(1), None, 5):
        with pytest.raises(MalformedRow):
            signature_of(bad)
    for bad in ("", "short", "0OIl" * 9, signature(1), None):
        with pytest.raises(MalformedRow):
            solana_address(bad)


# --- real transactions: nothing is created or lost ----------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_in_every_real_transaction_lamports_and_tokens_are_conserved(name):
    transaction = decode(name)
    # The fee leaves the accounts of the transaction; everything else only moves between them.
    assert sum(int(c.delta_raw) for c in transaction.sol_changes) == -int(transaction.fee_raw)
    per_mint = {}
    for change in transaction.token_changes:
        per_mint[change.mint] = per_mint.get(change.mint, 0) + int(change.delta_raw)
    # Wrapped SOL is the exception: wrapping and unwrapping turn lamports into tokens and back.
    per_mint.pop(WSOL_MINT, None)
    assert all(total == 0 for total in per_mint.values())
    # What no instruction explains was moved between accounts: it nets to nothing too.
    assert sum(int(u.amount_raw) for u in transaction.unattributed if u.kind == "sol") == 0


@pytest.mark.parametrize("name", NAMES)
def test_every_real_transaction_is_final_decoded_by_us_and_read_in_full(name):
    transaction = decode(name)
    assert transaction.confirmation_status is Confirmation.CONFIRMED
    assert transaction.data_quality is DataQuality.DECODED and transaction.provider == "helius"
    assert transaction.unreadable == 0 and transaction.unresolved_owners == 0
    assert transaction.identity == (CHAIN.chain_id, transaction.signature)
    assert transaction.fee_payer == transaction.signers[0]


def test_the_version_comes_through_as_legacy_0_or_1():
    versions = {name: decode(name).version for name in NAMES}
    assert versions["system_transfer_legacy"] == "legacy"
    assert versions["ata_create_and_transfer"] == "legacy"
    assert versions["system_transfer_v0"] == "0" and versions["close_account"] == "0"
    assert versions["hidden_payout_v1"] == "1" and versions["swap_temp_wsol_v1"] == "1"


def test_the_slot_the_time_and_the_position_in_the_block_come_from_the_item():
    raw = real("system_transfer_v0")
    transaction = solana_transaction(raw, CHAIN)
    assert (
        transaction.slot == raw["slot"] and transaction.transaction_index == raw["transactionIndex"]
    )
    assert transaction.timestamp == datetime.fromtimestamp(raw["blockTime"], UTC)
    (transfer,) = transaction.transfers
    assert (transfer.block_number, transfer.timestamp) == (transaction.slot, transaction.timestamp)
    assert transfer.transaction_index == transaction.transaction_index


def test_a_top_level_system_transfer_is_native_coin_between_two_wallets():
    raw = real("system_transfer_legacy")
    transaction = solana_transaction(raw, CHAIN)
    (transfer,) = transaction.transfers
    message = raw["transaction"]["message"]
    info = next(ix["parsed"]["info"] for ix in message["instructions"] if ix["program"] == "system")
    assert transfer.transfer_type is TransferType.NATIVE and transfer.operation == "transfer"
    assert (transfer.from_address, transfer.to_address) == (info["source"], info["destination"])
    assert transfer.amount_raw == str(info["lamports"]) and transfer.asset == CHAIN.native
    assert transfer.event_key == transfer.trace_id == "0" and transfer.program_id == SYSTEM
    assert (
        transfer.source_account == info["source"]
        and transfer.destination_account == info["destination"]
    )
    assert transfer.identity == (CHAIN.chain_id, transaction.signature, "0")


def test_what_a_program_moves_is_internal_and_each_movement_has_an_exact_position():
    found = positions(decode("hidden_payout_v1"))
    assert set(found) == {"0.1", "0.4", "1"}
    assert found["0.1"].transfer_type is TransferType.SPL
    assert found["0.4"].transfer_type is TransferType.INTERNAL  # called by instruction 0
    assert found["1"].transfer_type is TransferType.NATIVE  # an instruction of its own


def test_a_token_transfer_is_between_the_wallets_that_own_the_token_accounts():
    raw = real("token_transfer_checked")
    transaction = solana_transaction(raw, CHAIN)
    (transfer,) = transaction.transfers
    info = next(
        ix["parsed"]["info"]
        for ix in raw["transaction"]["message"]["instructions"]
        if ix.get("parsed", {}).get("type") == "transferChecked"
    )
    owners = {
        raw["transaction"]["message"]["accountKeys"][e["accountIndex"]]["pubkey"]: e["owner"]
        for e in raw["meta"]["postTokenBalances"]
    }
    assert transfer.transfer_type is TransferType.SPL and transfer.program_id == TOKEN_2022_PROGRAM
    assert (
        transfer.source_account == info["source"]
        and transfer.destination_account == info["destination"]
    )
    assert (transfer.from_address, transfer.to_address) == (
        owners[info["source"]],
        owners[info["destination"]],
    )
    assert transfer.from_address != transfer.source_account  # a wallet, not a token account
    assert transfer.asset.contract_address == info["mint"] and transfer.asset.decimals == 6
    assert transfer.amount_raw == info["tokenAmount"]["amount"]


def test_an_unchecked_transfer_takes_its_mint_and_decimals_from_the_transactions_balances():
    raw = real("token_transfer_unchecked_swap")
    group = next(
        g
        for g in raw["meta"]["innerInstructions"]
        if any(
            ix.get("parsed", {}).get("type") == "transfer" and ix["program"] == "spl-token"
            for ix in g["instructions"]
        )
    )
    number = group["index"]
    inner_position = next(
        j
        for j, ix in enumerate(group["instructions"])
        if ix.get("parsed", {}).get("type") == "transfer" and ix["program"] == "spl-token"
    )
    ix = group["instructions"][inner_position]
    assert "mint" not in ix["parsed"]["info"]  # the instruction does not say which token it is

    transfer = positions(solana_transaction(raw, CHAIN))[f"{number}.{inner_position}"]
    keys = [k["pubkey"] for k in raw["transaction"]["message"]["accountKeys"]]
    balance = next(
        e
        for e in raw["meta"]["preTokenBalances"] + raw["meta"]["postTokenBalances"]
        if keys[e["accountIndex"]] == ix["parsed"]["info"]["source"]
    )
    assert transfer.asset.contract_address == balance["mint"]
    assert transfer.asset.decimals == balance["uiTokenAmount"]["decimals"]
    assert transfer.amount_raw == ix["parsed"]["info"]["amount"]


def test_a_temporary_wrapped_sol_account_belongs_to_the_wallet_that_initialised_it():
    transaction = decode("swap_temp_wsol_v1")
    found = positions(transaction)
    # The account is created and closed inside the transaction, so no balance names its owner:
    # its initialisation does.
    assert transaction.unresolved_owners == 0
    assert found["1.0"].operation == "create_account"
    assert found["1.0"].from_address == found["1.0"].to_address == WALLET
    wrapped = found["3.7"]
    assert wrapped.asset.contract_address == WSOL_MINT and wrapped.to_address == WALLET
    closing = found["3.8"]
    assert closing.operation == "close_account" and closing.transfer_type is TransferType.INTERNAL
    assert closing.to_address == WALLET


def test_closing_a_wrapped_sol_account_returns_the_rent_and_the_wrapped_amount_together():
    found = positions(decode("swap_temp_wsol_v1"))
    rent = int(found["1.0"].amount_raw)  # what created the account
    wrapped = int(found["3.7"].amount_raw)  # what was paid into it as wrapped SOL
    assert int(found["3.8"].amount_raw) == rent + wrapped  # token transfers move lamports too


def test_the_wallets_balance_in_a_swap_is_fully_explained_by_the_instructions():
    transaction = decode("swap_temp_wsol_v1")
    assert not [u for u in transaction.unattributed if WALLET in (u.account, u.owner)]


def test_sol_a_program_pays_by_editing_lamports_is_reported_with_the_account_that_lost_it():
    transaction = decode("hidden_payout_v1")
    sol = {u.account: int(u.amount_raw) for u in transaction.unattributed if u.kind == "sol"}
    assert sol[WALLET] == 115_013_228  # the wallet sold tokens and was paid, with no instruction
    losers = [account for account, amount in sol.items() if amount < 0]
    assert len(losers) == 2 and sum(sol.values()) == 0
    assert all(item.owner is None and item.mint is None for item in transaction.unattributed)


def test_a_failed_transaction_moved_nothing_but_its_fee():
    transaction = decode("failed")
    assert transaction.status is TxStatus.FAILED
    assert "InstructionError" in transaction.error
    assert transaction.transfers == () and transaction.unattributed == ()
    (change,) = transaction.sol_changes
    assert change.account == transaction.fee_payer == WALLET
    assert change.delta_raw == f"-{transaction.fee_raw}"


def test_closing_a_token_account_returns_its_rent_to_the_wallet_that_owned_it():
    found = positions(decode("close_account"))
    closing = found["2"]
    assert closing.operation == "close_account" and closing.transfer_type is TransferType.NATIVE
    assert closing.from_address == closing.to_address == WALLET
    assert closing.source_account != WALLET and closing.destination_account == WALLET
    assert closing.amount_raw == "1513840"


def test_the_token_accounts_balances_after_a_real_transfer_are_the_transfers_effect():
    transaction = decode("token_transfer_checked")
    (transfer,) = transaction.transfers
    deltas = {c.account: int(c.delta_raw) for c in transaction.token_changes}
    assert deltas[transfer.source_account] == -int(transfer.amount_raw)
    assert deltas[transfer.destination_account] == int(transfer.amount_raw)


def test_to_dict_is_plain_json():
    import json

    document = decode("hidden_payout_v1").to_dict()
    json.dumps(document)
    assert document["version"] == "1" and len(document["transfers"]) == 3
    assert document["transfers"][0]["trace_id"] == "0.1" and document["unattributed"]
    assert document["transfers"][0]["source_account"] and document["transfers"][0]["program_id"]


# --- every kind of instruction, on small synthetic transactions ---------------------------------

W, V, N = address(1), address(2), address(3)  # a wallet, another wallet, a new account
TA, TB, MINT = address(11), address(12), address(100)  # two token accounts, a mint
FEE = 5000


def system(kind, **info):
    return instruction(SYSTEM, kind, info)


def spl(kind, **info):
    return instruction(TOKEN, kind, info)


def pair(*specs):
    """Before and after token balances from (account, owner, before, after) specs."""
    return list(specs)


def simple_transfer(amount=300, **overrides):
    accounts = [W, TA, TB, MINT, TOKEN]
    kwargs = {
        "pre_tokens": [
            token_entry(accounts, TA, MINT, W, 1000),
            token_entry(accounts, TB, MINT, V, 0),
        ],
        "post_tokens": [
            token_entry(accounts, TA, MINT, W, 1000 - amount),
            token_entry(accounts, TB, MINT, V, amount),
        ],
        "lamports": {W: (10**9, 10**9 - FEE)},
    }
    kwargs.update(overrides)
    ix = spl(
        "transferChecked",
        source=TA,
        destination=TB,
        mint=MINT,
        authority=W,
        tokenAmount={"amount": str(amount), "decimals": 6, "uiAmount": 0, "uiAmountString": "0"},
    )
    return item(1, accounts, [ix], **kwargs), accounts


def test_a_checked_token_transfer_names_the_wallets_the_amount_and_the_decimals():
    raw, _ = simple_transfer()
    transaction = solana_transaction(raw, CHAIN)
    (transfer,) = transaction.transfers
    assert (transfer.from_address, transfer.to_address) == (W, V)
    assert (transfer.source_account, transfer.destination_account) == (TA, TB)
    assert transfer.amount_raw == "300" and transfer.asset.contract_address == MINT
    assert transfer.asset.decimals == 6 and transfer.program_id == TOKEN
    assert transfer.transfer_type is TransferType.SPL and transfer.operation == "transfer"
    assert transaction.unattributed == () and transaction.unresolved_owners == 0


def test_an_unchecked_transfer_without_the_accounts_in_the_balances_is_unreadable_not_guessed():
    accounts = [W, TA, TB, TOKEN]
    ix = spl("transfer", source=TA, destination=TB, authority=W, amount="300")
    transaction = solana_transaction(
        item(1, accounts, [ix], lamports={W: (10**9, 10**9 - FEE)}), CHAIN
    )
    assert transaction.transfers == () and transaction.unreadable == 1


def test_a_zero_amount_token_transfer_is_kept_because_it_can_be_an_address_poisoning_trace():
    raw, _ = simple_transfer(amount=0)
    (transfer,) = solana_transaction(raw, CHAIN).transfers
    assert transfer.amount_raw == "0" and transfer.to_address == V


def test_a_token_owner_the_transaction_does_not_show_is_replaced_by_the_account_and_counted():
    raw, accounts = simple_transfer()
    for side in ("preTokenBalances", "postTokenBalances"):
        for entry in raw["meta"][side]:
            if entry["accountIndex"] == accounts.index(TB):
                del entry["owner"]
    transaction = solana_transaction(raw, CHAIN)
    (transfer,) = transaction.transfers
    assert transfer.to_address == TB and transfer.from_address == W
    assert transaction.unresolved_owners == 1


def test_minting_has_no_sender_and_burning_has_no_receiver():
    accounts = [W, TA, MINT, TOKEN]
    minted = item(
        1,
        accounts,
        [spl("mintTo", mint=MINT, account=TA, mintAuthority=W, amount="500")],
        pre_tokens=[],
        post_tokens=[token_entry(accounts, TA, MINT, W, 500)],
        lamports={W: (10**9, 10**9 - FEE)},
    )
    (mint,) = solana_transaction(minted, CHAIN).transfers
    assert (mint.from_address, mint.to_address, mint.operation) == (None, W, "mint")
    assert mint.destination_account == TA and mint.source_account is None

    burned = item(
        2,
        accounts,
        [
            spl(
                "burnChecked",
                account=TA,
                mint=MINT,
                authority=W,
                tokenAmount={"amount": "300", "decimals": 6},
            )
        ],
        pre_tokens=[token_entry(accounts, TA, MINT, W, 500)],
        post_tokens=[token_entry(accounts, TA, MINT, W, 200)],
        lamports={W: (10**9, 10**9 - FEE)},
    )
    (burn,) = solana_transaction(burned, CHAIN).transfers
    assert (burn.from_address, burn.to_address, burn.operation) == (W, None, "burn")
    assert burn.amount_raw == "300"


@pytest.mark.parametrize(
    ("kind", "info", "operation"),
    [
        ("transfer", {"source": W, "destination": V, "lamports": 1000}, "transfer"),
        (
            "transferWithSeed",
            {"source": W, "destination": V, "lamports": 1000, "sourceSeed": "s"},
            "transfer",
        ),
        (
            "createAccount",
            {"source": W, "newAccount": V, "lamports": 1000, "space": 0, "owner": SYSTEM},
            "create_account",
        ),
        (
            "createAccountWithSeed",
            {"source": W, "newAccount": V, "lamports": 1000, "seed": "s"},
            "create_account",
        ),
    ],
)
def test_the_system_instructions_that_move_lamports_become_native_transfers(kind, info, operation):
    accounts = [W, V, SYSTEM]
    lamports = {W: (10**9, 10**9 - 1000 - FEE), V: (1_000_000, 1_001_000)}
    transaction = solana_transaction(
        item(1, accounts, [system(kind, **info)], lamports=lamports), CHAIN
    )
    (transfer,) = transaction.transfers
    assert (transfer.from_address, transfer.to_address, transfer.amount_raw) == (W, V, "1000")
    assert transfer.operation == operation and transfer.transfer_type is TransferType.NATIVE
    assert transaction.unattributed == ()


def test_draining_a_nonce_account_pays_its_destination():
    accounts = [W, N, SYSTEM]
    ix = system("withdrawFromNonce", nonceAccount=N, destination=W, lamports=700, nonceAuthority=W)
    lamports = {W: (10**9, 10**9 - FEE + 700), N: (5_000_000, 5_000_000 - 700)}
    transaction = solana_transaction(item(1, accounts, [ix], lamports=lamports), CHAIN)
    (transfer,) = transaction.transfers
    assert (transfer.from_address, transfer.to_address, transfer.operation) == (
        N,
        W,
        "withdraw_nonce",
    )
    assert transaction.unattributed == ()


def test_a_system_instruction_that_moves_no_lamports_is_not_a_transfer():
    accounts = [W, N, SYSTEM]
    ixs = [
        system("advanceNonce", nonceAccount=N, nonceAuthority=W),
        system("allocate", account=N, space=8),
    ]
    transaction = solana_transaction(
        item(1, accounts, ixs, lamports={W: (10**9, 10**9 - FEE)}), CHAIN
    )
    assert transaction.transfers == () and transaction.unattributed == ()


def test_a_call_made_by_another_program_is_internal_and_numbered_inside_its_caller():
    accounts = [W, V, N, SYSTEM]
    outer = {"program": "unknown", "programId": address(500), "accounts": [], "data": "x"}
    inner = {
        0: [
            system("transfer", source=W, destination=V, lamports=100),
            system("transfer", source=W, destination=N, lamports=200),
        ]
    }
    lamports = {W: (10**9, 10**9 - 300 - FEE), V: (1_000_000, 1_000_100), N: (1_000_000, 1_000_200)}
    found = positions(
        solana_transaction(item(1, accounts, [outer], inner=inner, lamports=lamports), CHAIN)
    )
    assert set(found) == {"0.0", "0.1"}
    assert {t.transfer_type for t in found.values()} == {TransferType.INTERNAL}
    assert [found["0.0"].amount_raw, found["0.1"].amount_raw] == ["100", "200"]


def test_instructions_without_a_parser_and_text_parsed_ones_are_left_alone():
    accounts = [W, SYSTEM]
    ixs = [
        {"programId": address(500), "accounts": [], "data": "abcd"},  # no parser for it
        {"program": "spl-memo", "programId": address(501), "parsed": "hello"},  # parsed to text
        {"program": "x", "programId": address(502), "parsed": {"type": "t", "info": "not a dict"}},
    ]
    transaction = solana_transaction(
        item(1, accounts, ixs, lamports={W: (10**9, 10**9 - FEE)}), CHAIN
    )
    assert transaction.transfers == () and transaction.unreadable == 0
    assert transaction.programs == (address(500), address(501), address(502))


def test_a_token_instruction_that_moves_value_in_a_shape_we_do_not_read_is_counted():
    accounts = [W, TA, TB, MINT, TOKEN]
    ix = spl("transferCheckedWithFee", source=TA, destination=TB, mint=MINT, authority=W)
    transaction = solana_transaction(
        item(1, accounts, [ix], lamports={W: (10**9, 10**9 - FEE)}), CHAIN
    )
    assert transaction.transfers == () and transaction.unreadable == 1


def test_a_failed_transaction_is_not_read_for_transfers_whatever_its_instructions_say():
    accounts = [W, V, SYSTEM]
    ix = system("transfer", source=W, destination=V, lamports=1000)
    raw = item(
        1,
        accounts,
        [ix],
        err={"InstructionError": [0, {"Custom": 1}]},
        lamports={W: (10**9, 10**9 - FEE)},
    )
    transaction = solana_transaction(raw, CHAIN)
    assert transaction.status is TxStatus.FAILED and transaction.transfers == ()
    assert transaction.error == '{"InstructionError": [0, {"Custom": 1}]}'
    assert transaction.unattributed == ()


def test_a_failed_transaction_that_still_changed_something_else_says_so():
    accounts = [W, V, SYSTEM]
    raw = item(
        1,
        accounts,
        [],
        err="AccountInUse",
        lamports={W: (10**9, 10**9 - FEE), V: (1_000_000, 1_000_050)},
    )
    (change,) = solana_transaction(raw, CHAIN).unattributed
    assert (change.kind, change.account, change.amount_raw) == ("sol", V, "50")


# --- replaying lamports ------------------------------------------------------------------------


def test_lamports_a_program_moves_without_an_instruction_are_reported_in_both_directions():
    pool = address(4)
    accounts = [W, pool, SYSTEM]
    lamports = {W: (10**9, 10**9 - FEE + 777), pool: (5 * 10**9, 5 * 10**9 - 777)}
    transaction = solana_transaction(item(1, accounts, [], lamports=lamports), CHAIN)
    found = {u.account: u.amount_raw for u in transaction.unattributed}
    assert found == {W: "777", pool: "-777"}


def test_the_fee_is_not_an_unexplained_change():
    transaction = solana_transaction(
        item(1, [W, SYSTEM], [], lamports={W: (10**9, 10**9 - FEE)}), CHAIN
    )
    assert transaction.unattributed == ()
    assert [c.account for c in transaction.sol_changes] == [W]


def test_closing_a_token_account_moves_what_the_replay_says_it_holds_at_that_moment():
    accounts = [W, TA, N, SYSTEM, TOKEN]
    # Instruction 0 funds the token account; instruction 1 closes it. Its balance before is 0.
    ixs = [
        system(
            "createAccount", source=W, newAccount=TA, lamports=2_039_280, space=165, owner=TOKEN
        ),
        spl("closeAccount", account=TA, destination=N, owner=W),
    ]
    lamports = {
        W: (10**9, 10**9 - 2_039_280 - FEE),
        TA: (0, 0),
        N: (1_000_000, 1_000_000 + 2_039_280),
    }
    pre = []
    transaction = solana_transaction(
        item(1, accounts, ixs, lamports=lamports, pre_tokens=pre), CHAIN
    )
    funding, closing = transaction.transfers
    assert funding.operation == "create_account" and closing.operation == "close_account"
    assert closing.amount_raw == "2039280" and closing.destination_account == N
    assert transaction.unattributed == ()


def test_wrapped_sol_moves_lamports_when_its_tokens_move():
    accounts = [W, TA, TB, WSOL, TOKEN]
    ix = spl(
        "transferChecked",
        source=TA,
        destination=TB,
        mint=WSOL,
        authority=W,
        tokenAmount={"amount": "400", "decimals": 9},
    )
    lamports = {W: (10**9, 10**9 - FEE), TA: (3_000_000, 2_999_600), TB: (2_039_280, 2_039_680)}
    raw = item(
        1,
        accounts,
        [ix],
        lamports=lamports,
        pre_tokens=[
            token_entry(accounts, TA, WSOL, W, 1000, 9),
            token_entry(accounts, TB, WSOL, V, 0, 9),
        ],
        post_tokens=[
            token_entry(accounts, TA, WSOL, W, 600, 9),
            token_entry(accounts, TB, WSOL, V, 400, 9),
        ],
    )
    transaction = solana_transaction(raw, CHAIN)
    assert (
        transaction.unattributed == ()
    )  # without the lamport replay both accounts would be flagged
    assert transaction.transfers[0].asset.contract_address == WSOL


def test_token_balances_that_changed_without_an_instruction_are_reported():
    raw, accounts = simple_transfer()
    raw["meta"]["postTokenBalances"][0]["uiTokenAmount"]["amount"] = (
        "650"  # 50 less than it should be
    )
    (change,) = solana_transaction(raw, CHAIN).unattributed
    assert (change.kind, change.account, change.owner, change.mint) == ("token", TA, W, MINT)
    assert change.amount_raw == "-50"


def test_wrapped_sol_token_balances_are_accounted_for_in_lamports_not_twice():
    accounts = [W, TA, SYSTEM, WSOL, TOKEN]
    raw = item(
        1,
        accounts,
        [system("transfer", source=W, destination=TA, lamports=500)],
        lamports={W: (10**9, 10**9 - 500 - FEE), TA: (2_039_280, 2_039_780)},
        pre_tokens=[token_entry(accounts, TA, WSOL, W, 0, 9)],
        post_tokens=[token_entry(accounts, TA, WSOL, W, 500, 9)],  # after a syncNative
    )
    assert solana_transaction(raw, CHAIN).unattributed == ()


def test_an_account_that_lives_only_inside_the_transaction_still_has_a_known_owner():
    accounts = [W, TA, MINT, SYSTEM, TOKEN]
    ixs = [
        system(
            "createAccount", source=W, newAccount=TA, lamports=2_039_280, space=165, owner=TOKEN
        ),
        spl("initializeAccount3", account=TA, mint=MINT, owner=W),
        spl("closeAccount", account=TA, destination=W, owner=W),
    ]
    lamports = {W: (10**9, 10**9 - FEE), TA: (0, 0)}
    funding, closing = solana_transaction(
        item(1, accounts, ixs, lamports=lamports), CHAIN
    ).transfers
    assert (
        funding.to_address == W and closing.from_address == W
    )  # the owner, not the temporary account
    assert (funding.destination_account, closing.source_account) == (TA, TA)


# --- refusing what is not a transaction ---------------------------------------------------------


def corrupt(change):
    raw, _ = simple_transfer()
    change(raw)
    return raw


@pytest.mark.parametrize(
    "raw",
    [
        corrupt(lambda r: r["transaction"].update(signatures=["short"])),
        corrupt(lambda r: r["transaction"].update(signatures=[])),
        corrupt(lambda r: r["transaction"]["message"].update(accountKeys=[address(1)])),
        corrupt(lambda r: r["transaction"]["message"].update(accountKeys=[])),
        corrupt(lambda r: r["transaction"]["message"]["accountKeys"][1].update(pubkey="nope")),
        corrupt(lambda r: r["meta"].update(preBalances=[1])),
        corrupt(lambda r: r["meta"].update(postBalances=r["meta"]["postBalances"] + [1])),
        corrupt(lambda r: r["meta"]["preBalances"].__setitem__(0, -1)),
        corrupt(lambda r: r["meta"].update(fee=None)),
        corrupt(lambda r: r["meta"].update(innerInstructions="x")),
        corrupt(lambda r: r["meta"]["preTokenBalances"][0].update(accountIndex=99)),
        corrupt(lambda r: r["meta"]["preTokenBalances"][0].pop("uiTokenAmount")),
        corrupt(lambda r: r["meta"]["preTokenBalances"][0]["uiTokenAmount"].update(amount="-5")),
        corrupt(lambda r: r["meta"]["preTokenBalances"].append("x")),
        corrupt(lambda r: r["transaction"]["message"]["instructions"].append("x")),
        corrupt(lambda r: r.update(slot=-1)),
        corrupt(lambda r: r.update(slot="soon")),
        corrupt(lambda r: r.update(blockTime=10**30)),
        corrupt(lambda r: r.update(version="weird")),
        corrupt(lambda r: r.update(version=True)),
        corrupt(lambda r: r.pop("meta")),
        corrupt(lambda r: r.pop("transaction")),
        "not an object",
    ],
)
def test_data_that_cannot_be_what_it_claims_is_refused(raw):
    with pytest.raises(MalformedRow):
        solana_transaction(raw, CHAIN)


def test_a_transaction_may_have_no_block_time_and_is_not_edited_by_reading():
    raw, _ = simple_transfer()
    raw["blockTime"] = None
    before = copy.deepcopy(raw)
    assert solana_transaction(raw, CHAIN).timestamp is None
    assert raw == before


def test_when_a_token_account_changes_owner_the_owner_after_the_transaction_is_the_one_named():
    accounts = [W, TA, TB, MINT, TOKEN]
    ix = spl(
        "transferChecked",
        source=TA,
        destination=TB,
        mint=MINT,
        authority=V,
        tokenAmount={"amount": "300", "decimals": 6},
    )
    raw = item(
        1,
        accounts,
        [ix],
        lamports={W: (10**9, 10**9 - FEE)},
        pre_tokens=[
            token_entry(accounts, TA, MINT, V, 1000),
            token_entry(accounts, TB, MINT, V, 0),
        ],
        post_tokens=[
            token_entry(accounts, TA, MINT, W, 700),
            token_entry(accounts, TB, MINT, V, 300),
        ],
    )
    (transfer,) = solana_transaction(raw, CHAIN).transfers
    assert (transfer.from_address, transfer.to_address) == (W, V)  # TA now belongs to W
