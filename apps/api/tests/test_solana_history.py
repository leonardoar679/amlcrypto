"""Reading a whole Solana wallet history: order, the check against the chain, limits, gaps."""

from __future__ import annotations

import json

import pytest

from app.chains.models import TransferType
from app.ingestion.solana import fetch_solana_history
from app.providers.base import AllProvidersFailed
from app.providers.budget import RequestBudget
from tests.fake_helius import (
    HEAD,
    SYSTEM,
    TOKEN,
    address,
    instruction,
    item,
    signature,
    token_entry,
    transfer_item,
)
from tests.helpers import load_fixture, run
from tests.rigs import Rig

W, A, B, C = address(1), address(2), address(3), address(4)
MINT, TOKEN_S, TOKEN_W = address(900), address(61), address(62)
FEE = 5000


def token_payment(n: int, *, slot: int, amount: int = 300) -> dict:
    """Wallet A sends `amount` of a token to W's token account; W is not even named in it."""
    accounts = [A, TOKEN_S, TOKEN_W, MINT, TOKEN]
    ix = instruction(
        TOKEN,
        "transferChecked",
        {
            "source": TOKEN_S,
            "destination": TOKEN_W,
            "mint": MINT,
            "authority": A,
            "tokenAmount": {"amount": str(amount), "decimals": 6},
        },
    )
    return item(
        n,
        accounts,
        [ix],
        slot=slot,
        index=n % 400,
        lamports={A: (10**9, 10**9 - FEE)},
        pre_tokens=[
            token_entry(accounts, TOKEN_S, MINT, A, 1000),
            token_entry(accounts, TOKEN_W, MINT, W, 0),
        ],
        post_tokens=[
            token_entry(accounts, TOKEN_S, MINT, A, 1000 - amount),
            token_entry(accounts, TOKEN_W, MINT, W, amount),
        ],
    )


def flow(rig: Rig) -> None:
    """W is paid SOL, is paid a token, and then pays SOL away (and the fee)."""
    rig.helius.add(
        transfer_item(1, A, W, 2_000_000, slot=HEAD - 300),
        token_payment(2, slot=HEAD - 200),
        transfer_item(3, W, B, 500_000, slot=HEAD - 100),
    )


def incoming(rig: Rig, count: int) -> None:
    rig.helius.add(
        *[
            transfer_item(n, address(1000 + n), W, 10_000 + n, slot=HEAD - 10_000 + n)
            for n in range(1, count + 1)
        ]
    )


def read(rig: Rig, **options):
    options.setdefault("max_items", 5000)
    return run(fetch_solana_history(rig.solana_adapter(), W, **options))


# --- a whole read ------------------------------------------------------------------------------


def test_a_whole_history_is_oldest_first_checked_against_the_chain_and_complete():
    rig = Rig()
    flow(rig)

    history = read(rig)

    assert [t.signature for t in history.transactions] == [signature(1), signature(2), signature(3)]
    assert history.complete and history.coverage == []
    assert history.head_block == history.synced_through_block == HEAD
    assert history.figures.sol_raw == str(2_000_000 - 500_000 - FEE)
    assert history.figures.tokens[TOKEN].balances == {MINT: "300"}


def test_what_the_wallet_did_in_each_transaction_comes_from_the_balances_not_the_instructions():
    rig = Rig()
    flow(rig)

    first, second, third = (read(rig).activity[signature(n)] for n in (1, 2, 3))

    assert (first.sol_delta_raw, first.role, first.signed) == ("2000000", "receiver", False)
    # The token transaction never names the wallet; its token account's balance changed.
    assert second.sol_delta_raw == "0" and second.token_deltas == ((MINT, 6, "300"),)
    assert second.role == "receiver" and second.fee_paid_raw == "0"
    assert (third.sol_delta_raw, third.fee_paid_raw, third.role, third.signed) == (
        str(-500_000 - FEE),
        str(FEE),
        "sender",
        True,
    )


def test_the_token_payment_that_never_mentions_the_wallet_is_still_in_its_history():
    rig = Rig()
    flow(rig)
    history = read(rig)
    assert signature(2) in {t.signature for t in history.transactions}
    token_move = [t for t in history.wallet_transfers() if t.transfer_type is TransferType.SPL]
    assert [(t.from_address, t.to_address, t.amount_raw) for t in token_move] == [(A, W, "300")]


def test_a_longer_history_is_read_across_pages_and_still_agrees_with_the_chain():
    rig = Rig()
    incoming(rig, 250)

    history = read(rig)

    assert len(history.transactions) == 250 and history.complete
    assert [t.slot for t in history.transactions] == sorted(t.slot for t in history.transactions)
    assert history.figures.sol_raw == str(sum(10_000 + n for n in range(1, 251)))
    assert rig.helius.methods().count("getTransactionsForAddress") == 3


def test_an_address_with_no_history_is_an_empty_complete_one():
    history = read(Rig())
    assert history.transactions == [] and history.complete and history.coverage == []
    assert history.figures.sol_raw == "0"


def test_a_failed_transaction_is_in_the_history_with_only_its_fee():
    rig = Rig()
    flow(rig)
    accounts = [W, B, SYSTEM]
    rig.helius.add(
        item(
            4,
            accounts,
            [instruction(SYSTEM, "transfer", {"source": W, "destination": B, "lamports": 1})],
            slot=HEAD - 50,
            err={"InstructionError": [0, "Custom"]},
            lamports={W: (10**9, 10**9 - FEE)},
        )
    )

    history = read(rig)

    assert history.complete  # the fee is part of the balance, and the check still agrees
    failed = history.activity[signature(4)]
    assert (failed.sol_delta_raw, failed.fee_paid_raw, failed.role) == (
        str(-FEE),
        str(FEE),
        "signer",
    )
    assert history.summary()["failed"] == 1


# --- when the history does not add up to the chain ------------------------------------------------


def test_a_transaction_that_changed_the_wallets_sol_but_was_not_listed_breaks_the_check():
    rig = Rig()
    flow(rig)
    rig.helius.unlisted = {signature(3)}

    history = read(rig)

    assert len(history.transactions) == 2
    assert not history.complete and history.synced_through_block is None
    (note,) = [n for n in history.coverage if n.startswith("MISMATCH")]
    assert f"adds up to {2_000_000} lamports" in note and f"the chain says {1_495_000}" in note


def test_a_missing_token_transaction_is_caught_even_though_the_sol_adds_up():
    rig = Rig()
    flow(rig)
    rig.helius.unlisted = {signature(2)}  # changed no SOL of the wallet's, only its token balance

    history = read(rig)

    assert not history.complete
    (note,) = [n for n in history.coverage if n.startswith("MISMATCH")]
    assert f"token {MINT}" in note and "adds up to 0 (raw), the chain says 300" in note


def test_each_figure_the_chain_gives_is_checked_on_its_own():
    rig = Rig()
    flow(rig)
    rig.helius.skew_sol = 1
    sol_only = read(rig)
    assert not sol_only.complete
    assert [n.startswith("MISMATCH") for n in sol_only.coverage] == [True]
    assert "lamports of SOL" in sol_only.coverage[0]

    rig = Rig()
    flow(rig)
    rig.helius.skew_tokens = {MINT: 1}
    token_only = read(rig)
    assert not token_only.complete
    assert [n for n in token_only.coverage if "MISMATCH" in n] and "token" in token_only.coverage[0]


def test_a_transaction_after_the_slot_the_balances_are_as_of_does_not_count_against_the_history():
    rig = Rig()
    flow(rig)
    rig.helius.add(
        transfer_item(4, A, W, 777, slot=HEAD - 5)
    )  # finalized after the balance snapshot
    rig.helius.snapshot_lag = 10

    history = read(rig)

    assert len(history.transactions) == 4  # it is part of the history
    assert history.complete and history.figures.sol_slot == HEAD - 10  # but not of the comparison


def test_balances_that_cannot_be_read_are_said_so_and_the_read_goes_on_unchecked():
    rig = Rig()
    flow(rig)
    rig.helius.broken = {"getBalance"}

    history = read(rig)

    assert len(history.transactions) == 3 and history.complete and history.figures is None
    assert len(history.coverage) == 1 and "not checked against them" in history.coverage[0]


def test_without_a_known_height_the_read_says_so_and_still_reads():
    rig = Rig()
    flow(rig)
    rig.helius.broken = {"getSlot"}

    history = read(rig)

    assert len(history.transactions) == 3 and history.head_block is None
    assert history.synced_through_block is None
    assert history.coverage[0].startswith("the chain's current height is unknown")


def test_a_provider_failure_while_reading_is_raised_not_hidden():
    rig = Rig()
    flow(rig)
    rig.helius.queued = [
        (401, {"jsonrpc": "2.0", "error": {"code": -32001, "message": "Unauthorized"}, "id": 1})
    ] * 3
    with pytest.raises(AllProvidersFailed):
        read(rig)


# --- slot ranges -------------------------------------------------------------------------------


def test_a_range_read_is_not_checked_against_the_chain_and_is_synced_to_its_end():
    rig = Rig()
    flow(rig)

    history = read(rig, from_block=HEAD - 250, to_block=HEAD - 150)

    assert [t.signature for t in history.transactions] == [signature(2)]
    assert history.complete and history.synced_through_block == HEAD - 150
    assert history.figures is None and history.coverage == []
    assert "getBalance" not in rig.helius.methods()


def test_with_no_end_slot_a_range_read_is_synced_through_the_newest_finalized_slot():
    rig = Rig()
    flow(rig)
    assert read(rig, from_block=HEAD - 250).synced_through_block == HEAD


# --- limits and resuming -----------------------------------------------------------------------


def test_a_limit_stops_the_read_with_the_newest_and_resuming_finishes_it_without_overlap():
    rig = Rig()
    incoming(rig, 250)

    first = read(rig, max_items=120)

    assert not first.complete and first.synced_through_block is None
    assert len(first.transactions) == 200  # two whole pages; the limit is checked between pages
    assert first.resume["transactions"].startswith("helius|")
    assert any("the newest ones" in note and "resume" in note for note in first.coverage)
    assert min(t.slot for t in first.transactions) == HEAD - 10_000 + 51

    rig.helius.requests.clear()
    second = read(rig, resume=first.resume)

    assert second.complete and len(second.transactions) == 50
    assert {t.signature for t in first.transactions}.isdisjoint(
        {t.signature for t in second.transactions}
    )
    assert "getBalance" not in rig.helius.methods()  # a resumed read is not checked


def test_the_limit_is_reached_exactly_when_that_many_transactions_are_in_hand():
    rig = Rig()
    incoming(rig, 250)
    history = read(rig, max_items=100)  # exactly the first page
    assert len(history.transactions) == 100 and not history.complete and history.resume


def test_running_out_of_request_budget_ends_the_read_politely_and_resuming_finishes_it():
    rig = Rig()
    incoming(rig, 250)

    # The height and the first page use up the two requests.
    starved = run(
        fetch_solana_history(rig.solana_adapter(budget=RequestBudget(limit=2)), W, max_items=5000)
    )

    assert not starved.complete and len(starved.transactions) == 100
    assert starved.resume["transactions"] and "request budget" in " ".join(starved.coverage)

    rest = read(rig, resume=starved.resume)
    assert rest.complete and len(rest.transactions) == 150


def test_a_read_refused_before_its_first_page_resumes_from_the_start():
    rig = Rig()
    incoming(rig, 3)

    starved = run(
        fetch_solana_history(rig.solana_adapter(budget=RequestBudget(limit=1)), W, max_items=5000)
    )

    assert starved.transactions == [] and starved.resume == {"transactions": ""}
    rest = read(rig, resume=starved.resume)
    assert rest.complete and len(rest.transactions) == 3


# --- what the instruction view cannot show -----------------------------------------------------


def test_a_hidden_movement_is_noted_only_when_it_touches_this_wallet():
    rig = Rig()
    accounts = [W, B, C, SYSTEM]
    rig.helius.add(transfer_item(9, A, W, 5_000_000, slot=HEAD - 60))  # W has something to lose
    # A program moves lamports from B to W by editing balances: W is affected.
    rig.helius.add(
        item(
            1,
            accounts,
            [],
            slot=HEAD - 30,
            lamports={W: (10**9, 10**9 - FEE + 900), B: (5_000_000, 4_999_100)},
        )
    )
    # Others' lamports move between B and C: nothing to do with W, which is only named.
    rig.helius.add(
        item(
            2,
            accounts,
            [],
            slot=HEAD - 20,
            lamports={
                W: (10**9 - FEE + 900, 10**9 - 2 * FEE + 900),
                B: (4_999_100, 4_998_100),
                C: (2_000_000, 2_001_000),
            },
        )
    )

    history = read(rig)

    assert len([n for n in history.coverage if "no instruction to explain it" in n]) == 1
    assert "1 transactions changed this wallet's balance" in history.coverage[-1]


def test_the_summary_is_compact_and_json_ready():
    rig = Rig()
    flow(rig)
    history = read(rig)

    summary = history.summary(latest=2)

    json.dumps(summary)
    assert summary["transactions"] == 3 and summary["complete"] is True and summary["failed"] == 0
    assert (summary["first_block"], summary["last_block"]) == (HEAD - 300, HEAD - 100)
    assert summary["wallet_transfers"] == 3 and summary["transfers_by_type"] == {
        "native": 2,
        "spl": 1,
    }
    assert summary["providers"] == ["helius"] and summary["figures"]["sol_raw"] == "1495000"
    assert [e["signature"] for e in summary["latest"]] == [signature(2), signature(3)]
    assert history.summary(latest=0)["latest"] == []


def test_the_full_listing_carries_each_transaction_with_the_wallets_part_in_it():
    rig = Rig()
    flow(rig)
    history = read(rig)
    full = history.detailed(history.transactions[2])
    json.dumps(full)
    assert full["signature"] == signature(3) and full["activity"]["role"] == "sender"
    assert full["transfers"][0]["amount_raw"] == "500000" and full["fee_payer"] == W


# --- real transactions -------------------------------------------------------------------------

WALLET = "4jErKA4q4sUbAUWpseN8s2anSbdsTAAjX2PnwFRzEwQ2"
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


def opening_credit(raws: list[dict], wallet: str) -> dict:
    """A transaction before all the others that gives the wallet what they spend.

    Real transactions cut from the middle of a history spend what an earlier one paid in; without
    this the totals would go below zero, which a real chain can never show.
    """
    from app.chains.solana.chains import SOLANA_CHAIN
    from app.chains.solana.normalize import solana_transaction

    sol, tokens = 0, {}
    for raw in raws:
        transaction = solana_transaction(raw, SOLANA_CHAIN)
        sol += next((int(c.delta_raw) for c in transaction.sol_changes if c.account == wallet), 0)
        for change in transaction.token_changes:
            if change.owner == wallet:
                key = (change.account, change.program_id, change.mint, change.decimals)
                tokens[key] = tokens.get(key, 0) + int(change.delta_raw)
    owed = {key: -net for key, net in tokens.items() if net < 0}
    funder = address(7000)
    accounts = [funder, wallet, *[key[0] for key in owed], SYSTEM]
    credit = max(0, -sol)
    return item(
        7000,
        accounts,
        [],
        slot=HEAD - 10**6,
        version=0,
        lamports={funder: (10**12, 10**12 - credit - FEE), wallet: (0, credit)},
        post_tokens=[
            token_entry(accounts, account, mint, wallet, amount, decimals, program)
            for (account, program, mint, decimals), amount in owed.items()
        ],
    )


def test_real_transactions_of_every_version_make_a_history_that_agrees_with_the_chain():
    rig = Rig()
    raws = [load_fixture(f"solana/{name}")["item"] for name in NAMES]
    rig.helius.head = max(raw["slot"] for raw in raws) + 100  # the real slots are newer than HEAD
    rig.helius.add(opening_credit(raws, WALLET), *raws)

    history = run(fetch_solana_history(rig.solana_adapter(), WALLET, max_items=100))

    assert len(history.transactions) == 10 and history.complete  # nine real, one opening credit
    assert {t.version for t in history.transactions} == {"legacy", "0", "1"}
    assert history.figures is not None
    # The wallet's net SOL over all of them is what the (fake) chain holds.
    assert sum(int(a.sol_delta_raw) for a in history.activity.values()) == int(
        history.figures.sol_raw
    )
    assert [n for n in history.coverage if "MISMATCH" in n] == []
    # The program that paid by editing lamports, and the opening credit, which creates token
    # balances out of nothing: no instruction explains either.
    assert any("2 transactions changed this wallet's balance" in n for n in history.coverage)
    assert history.summary()["failed"] == 1


def test_a_token_transaction_after_the_balances_slot_does_not_count_against_the_history():
    rig = Rig()
    flow(rig)
    rig.helius.add(token_payment(4, slot=HEAD - 5, amount=111))  # after the balance snapshot
    rig.helius.snapshot_lag = 10

    history = read(rig)

    assert len(history.transactions) == 4 and history.complete
    assert history.figures.tokens[TOKEN].balances == {MINT: "300"}  # without the 111


def test_within_one_slot_the_position_in_the_block_orders_the_transactions():
    rig = Rig()
    first = transfer_item(10, A, W, 1_000, slot=HEAD - 40)
    second = transfer_item(11, A, W, 2_000, slot=HEAD - 40)
    first["transactionIndex"], second["transactionIndex"] = 7, 3  # the later signature ran first
    rig.helius.add(first, second)

    history = read(rig)

    assert [t.transaction_index for t in history.transactions] == [3, 7]
    assert [t.signature for t in history.transactions] == [signature(11), signature(10)]


def test_only_the_transfers_that_involve_the_wallet_are_the_wallets():
    rig = Rig()
    rig.helius.add(transfer_item(9, A, W, 5_000_000, slot=HEAD - 60))
    accounts = [W, B, C, SYSTEM]
    ixs = [
        instruction(SYSTEM, "transfer", {"source": W, "destination": B, "lamports": 100}),
        instruction(SYSTEM, "transfer", {"source": B, "destination": C, "lamports": 50}),  # not W's
    ]
    rig.helius.add(
        item(
            1,
            accounts,
            ixs,
            slot=HEAD - 30,
            lamports={
                W: (10**9, 10**9 - 100 - FEE),
                B: (1_000_000, 1_000_050),
                C: (1_000_000, 1_000_050),
            },
        )
    )

    history = read(rig)

    swap = next(t for t in history.transactions if t.signature == signature(1))
    assert len(swap.transfers) == 2  # the transaction holds both
    assert [
        (t.from_address, t.to_address)
        for t in history.wallet_transfers()
        if t.transaction_hash == signature(1)
    ] == [(W, B)]
    assert history.summary()["wallet_transfers"] == 2  # the payment in, and W paying B


def test_instructions_it_cannot_read_and_unknown_token_owners_are_noted_for_the_history():
    rig = Rig()
    rig.helius.add(transfer_item(9, A, W, 5_000_000, slot=HEAD - 60))
    accounts = [W, TOKEN_S, TOKEN_W, MINT, TOKEN]
    unread = instruction(
        TOKEN, "transferCheckedWithFee", {"source": TOKEN_S, "destination": TOKEN_W, "mint": MINT}
    )
    rig.helius.add(item(1, accounts, [unread], slot=HEAD - 40, lamports={W: (10**9, 10**9 - FEE)}))
    ownerless = [W, TOKEN_S, TOKEN_W, MINT, TOKEN]
    rig.helius.add(  # the 5 tokens the wallet later moves were paid in earlier
        item(
            8,
            ownerless,
            [],
            slot=HEAD - 50,
            lamports={W: (10**9, 10**9 - FEE)},
            post_tokens=[token_entry(ownerless, TOKEN_S, MINT, W, 5)],
        )
    )
    move = instruction(
        TOKEN,
        "transferChecked",
        {
            "source": TOKEN_S,
            "destination": TOKEN_W,
            "mint": MINT,
            "authority": W,
            "tokenAmount": {"amount": "5", "decimals": 6},
        },
    )
    rig.helius.add(
        item(
            2,
            ownerless,
            [move],
            slot=HEAD - 30,
            lamports={W: (10**9, 10**9 - FEE)},
            pre_tokens=[
                token_entry(ownerless, TOKEN_S, MINT, W, 5),
                token_entry(ownerless, TOKEN_W, MINT, None, 0),
            ],
            post_tokens=[
                token_entry(ownerless, TOKEN_S, MINT, W, 0),
                token_entry(ownerless, TOKEN_W, MINT, None, 5),
            ],
        )
    )

    history = read(rig)

    assert "1 instructions move value in a shape this reader does not know" in history.coverage
    assert any(
        "1 transactions have token accounts whose owner they do not show" in n
        for n in history.coverage
    )
