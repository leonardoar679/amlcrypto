"""The Solana adapter over the in-memory Helius: sources, cursors, heights, balances, details."""

from __future__ import annotations

import pytest

from app.chains.cursors import InvalidCursor
from app.chains.models import Confirmation, DataQuality, TransferType, TxStatus
from app.providers.base import AllProvidersFailed
from tests.fake_helius import HEAD, address, signature, transfer_item
from tests.helpers import load_fixture, run
from tests.rigs import Rig

WALLET = address(1)


def fill(rig: Rig, count: int) -> None:
    rig.helius.add(
        *[
            transfer_item(n, address(1000 + n), WALLET, 10_000 + n, slot=HEAD - 10_000 + n)
            for n in range(1, count + 1)
        ]
    )


# --- sources, height and balances ---------------------------------------------------------------


def test_solana_is_read_through_helius_and_only_when_it_has_a_key():
    assert Rig().solana_adapter().source_names == ["helius"]
    assert Rig(helius_key=None).solana_adapter().source_names == []


def test_a_read_started_now_is_complete_through_the_newest_finalized_slot():
    assert run(Rig().solana_adapter().get_heights()) == (HEAD, HEAD)


def test_what_the_chain_says_a_wallet_holds_comes_through():
    rig = Rig()
    fill(rig, 3)
    figures = run(rig.solana_adapter().get_wallet_figures(WALLET))
    assert figures.sol_raw == str(10_001 + 10_002 + 10_003) and figures.provider == "helius"


# --- cursors -----------------------------------------------------------------------------------


def test_a_page_cursor_names_its_source_and_leads_to_the_next_page():
    rig = Rig()
    fill(rig, 130)
    adapter = rig.solana_adapter()

    async def scenario():
        first = await adapter.get_address_page(WALLET)
        second = await adapter.get_address_page(WALLET, cursor=first.next_cursor)
        return first, second

    first, second = run(scenario())

    assert first.next_cursor.startswith("helius|") and second.next_cursor is None
    assert [t.signature for t in first.items + second.items] == [
        signature(n) for n in range(130, 0, -1)
    ]


def test_a_cursor_from_somewhere_else_is_refused_before_any_request():
    rig = Rig()
    adapter = rig.solana_adapter()
    for cursor in ("etherscan|5:0", "esplora|" + "ab" * 32, "nonsense", "|", "helius|not-a-token"):
        with pytest.raises(InvalidCursor):
            run(adapter.get_address_page(WALLET, cursor=cursor))
    assert rig.helius.requests == []


# --- one transaction ---------------------------------------------------------------------------


def stock(rig: Rig, name: str) -> str:
    raw = load_fixture(f"solana/{name}")["item"]
    rig.helius.add(raw)
    return raw["transaction"]["signatures"][0]


def test_a_real_swap_is_decoded_by_us_and_marked_final():
    rig = Rig()
    sig = stock(rig, "swap_temp_wsol_v1")

    detail = run(rig.solana_adapter().get_transaction(sig))

    transaction = detail.transaction
    assert transaction.signature == sig and transaction.provider == "helius"
    assert transaction.data_quality is DataQuality.DECODED
    assert transaction.confirmation_status is Confirmation.CONFIRMED
    assert transaction.status is TxStatus.SUCCESS and transaction.version == "1"
    assert {t.transfer_type for t in transaction.transfers} == {
        TransferType.SPL,
        TransferType.INTERNAL,
        TransferType.NATIVE,
    }


def test_what_cannot_be_explained_is_said_in_the_coverage_notes():
    rig = Rig()
    detail = run(rig.solana_adapter().get_transaction(stock(rig, "hidden_payout_v1")))
    assert detail.coverage == (
        "8 balance changes are explained by no instruction (a program moved them directly); "
        "they are listed as unattributed",
    )
    plain = run(rig.solana_adapter().get_transaction(stock(rig, "system_transfer_legacy")))
    assert plain.coverage == ()


def test_the_signature_may_be_typed_with_spaces_around_it():
    rig = Rig()
    sig = stock(rig, "system_transfer_v0")
    assert run(rig.solana_adapter().get_transaction(f"  {sig}\n")).transaction.signature == sig


def test_an_unknown_or_unfinalized_transaction_is_none_and_a_bad_signature_is_an_error():
    rig = Rig()
    adapter = rig.solana_adapter()
    assert run(adapter.get_transaction(signature(99))) is None
    sent = len(rig.helius.requests)
    for bad in ("", "1234", "0OIl" * 22, address(1), "ab" * 32):
        with pytest.raises(ValueError, match="not a transaction signature"):
            run(adapter.get_transaction(bad))
    assert len(rig.helius.requests) == sent  # a bad signature costs nothing

    sig = stock(rig, "system_transfer_v0")
    rig.helius.unfinalized = {sig}
    assert run(adapter.get_transaction(sig)) is None


def test_not_found_is_not_claimed_when_the_source_could_not_answer():
    rig = Rig()
    rig.helius.queued = [
        (401, {"jsonrpc": "2.0", "error": {"code": -32001, "message": "Unauthorized"}, "id": 1})
    ]
    with pytest.raises(AllProvidersFailed) as info:
        run(rig.solana_adapter().get_transaction(signature(5)))
    assert [name for name, _ in info.value.failures] == ["helius"]


def test_unreadable_instructions_and_unknown_owners_are_said_in_a_transactions_notes():
    from tests.fake_helius import TOKEN, instruction, item, token_entry

    rig = Rig()
    accounts = [WALLET, address(61), address(62), address(100), TOKEN]
    paid = {WALLET: (10**9, 10**9 - 5000)}
    unread = instruction(
        TOKEN,
        "transferCheckedWithFee",
        {"source": accounts[1], "destination": accounts[2], "mint": accounts[3]},
    )
    rig.helius.add(item(1, accounts, [unread], slot=HEAD - 40, lamports=paid))
    detail = run(rig.solana_adapter().get_transaction(signature(1)))
    assert detail.coverage == ("1 instructions move value in a shape this reader does not know",)

    move = instruction(
        TOKEN,
        "transferChecked",
        {
            "source": accounts[1],
            "destination": accounts[2],
            "mint": accounts[3],
            "authority": WALLET,
            "tokenAmount": {"amount": "5", "decimals": 6},
        },
    )
    rig.helius.add(
        item(
            2,
            accounts,
            [move],
            slot=HEAD - 30,
            lamports=paid,
            pre_tokens=[
                token_entry(accounts, accounts[1], accounts[3], WALLET, 5),
                token_entry(accounts, accounts[2], accounts[3], None, 0),
            ],
            post_tokens=[
                token_entry(accounts, accounts[1], accounts[3], WALLET, 0),
                token_entry(accounts, accounts[2], accounts[3], None, 5),
            ],
        )
    )
    detail = run(rig.solana_adapter().get_transaction(signature(2)))
    assert detail.coverage == (
        "1 token accounts have no owner in this transaction's balances; "
        "the account itself stands in for its owner",
    )
