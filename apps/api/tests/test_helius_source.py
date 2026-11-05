"""The Helius source against an in-memory Helius: paging, cursors, ranges, errors, balances."""

from __future__ import annotations

import httpx2
import pytest

from app.chains.cursors import InvalidCursor
from app.chains.solana.chains import SOLANA_CHAIN
from app.providers.base import (
    BudgetExceeded,
    ProviderAuthError,
    ProviderBadResponse,
)
from app.providers.budget import RequestBudget
from app.providers.client import ProviderClient
from app.providers.helius import PAGE_SIZE, HeliusSource, helius_body_error
from app.providers.registry import PROVIDERS
from tests.fake_helius import (
    HEAD,
    KEY,
    SYSTEM,
    TOKEN,
    TOKEN_2022,
    FakeHelius,
    RpcError,
    address,
    instruction,
    item,
    signature,
    token_entry,
    transfer_item,
)
from tests.helpers import FakeClock, FullJitter, load_fixture, run

CHAIN = SOLANA_CHAIN
WALLET = address(1)


def make(fake: FakeHelius, *, budget=None):
    clock = FakeClock()
    client = ProviderClient(
        {"helius": PROVIDERS["helius"]},
        {"helius": KEY},
        transport=httpx2.MockTransport(fake),
        rate_limits={"helius": 1000.0},
        clock=clock,
        sleep=clock.sleep,
        rng=FullJitter(),
    )
    return HeliusSource(client, budget=budget), clock


def fill(fake: FakeHelius, count: int, wallet: str = WALLET) -> list[dict]:
    """`count` transactions paying `wallet`, one per slot, the oldest first (number 1)."""
    added = [
        transfer_item(n, address(1000 + n), wallet, 10_000 + n, slot=HEAD - 10_000 + n)
        for n in range(1, count + 1)
    ]
    fake.add(*added)
    return added


def read_all(source, wallet=WALLET, **options):
    """Every page until the cursor runs out: (transactions, number of pages)."""

    async def go():
        found, cursor, pages = [], None, 0
        while True:
            page = await source.address_page(CHAIN, wallet, cursor=cursor, **options)
            found += page.items
            pages += 1
            assert pages < 50
            if page.next_cursor is None:
                return found, pages
            cursor = page.next_cursor

    return run(go())


def options_of(fake, method="getTransactionsForAddress", index=0):
    calls = [params for name, params in fake.calls if name == method]
    return calls[index][1]


# --- the chain ---------------------------------------------------------------------------------


def test_the_height_is_the_newest_finalized_slot():
    fake = FakeHelius()
    source, _ = make(fake)
    assert run(source.block_height(CHAIN)) == HEAD
    assert fake.calls == [("getSlot", [{"commitment": "finalized"}])]


@pytest.mark.parametrize("answer", ["x", -1, 1.5, None, True])
def test_a_slot_that_is_not_a_slot_is_a_bad_response(answer):
    fake = FakeHelius()
    fake.queued = [{"jsonrpc": "2.0", "result": answer, "id": 1}]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="unexpected response shape"):
        run(source.block_height(CHAIN))


# --- paging ------------------------------------------------------------------------------------


def test_a_short_history_is_one_page_newest_first_and_decoded():
    fake = FakeHelius()
    fill(fake, 3)
    source, _ = make(fake)

    page = run(source.address_page(CHAIN, WALLET))

    assert [t.signature for t in page.items] == [signature(3), signature(2), signature(1)]
    assert page.next_cursor is None and page.coverage == () and not page.truncated
    assert all(t.provider == "helius" and t.transfers for t in page.items)


def test_the_request_asks_for_final_fully_parsed_data_including_version_1_transactions():
    fake = FakeHelius()
    fill(fake, 1)
    source, _ = make(fake)
    run(source.address_page(CHAIN, WALLET))

    wallet, options = fake.calls[0][1]
    assert wallet == WALLET
    assert options == {
        "transactionDetails": "full",
        "encoding": "jsonParsed",
        "maxSupportedTransactionVersion": 1,
        "limit": PAGE_SIZE,
        "sortOrder": "desc",
        "commitment": "finalized",
        "filters": {"tokenAccounts": "balanceChanged", "status": "any"},
    }


def test_a_version_1_transaction_is_read_because_the_request_allows_it():
    fake = FakeHelius()
    one = transfer_item(1, address(50), WALLET, 5_000, slot=HEAD - 10)
    one["version"] = 1
    fake.add(one)
    source, _ = make(fake)

    (found,) = run(source.address_page(CHAIN, WALLET)).items

    assert found.version == "1"
    # ... and the service really does refuse a request that does not allow it.
    with pytest.raises(RpcError) as refused:
        fake.rpc_getTransactionsForAddress(WALLET, {"maxSupportedTransactionVersion": 0})
    assert refused.value.code == -32015


def test_pages_follow_each_other_through_the_token_without_gaps_or_repeats():
    fake = FakeHelius()
    fill(fake, 250)
    source, _ = make(fake)

    found, pages = read_all(source)

    assert pages == 3
    assert [t.signature for t in found] == [signature(n) for n in range(250, 0, -1)]
    tokens = [params[1].get("paginationToken") for name, params in fake.calls]
    assert tokens[0] is None and all(tokens[1:])


def test_a_page_with_no_rows_ends_the_read_even_if_a_token_comes_with_it():
    fake = FakeHelius()
    fake.queued = [{"jsonrpc": "2.0", "result": {"data": [], "paginationToken": "5:1"}, "id": 1}]
    source, _ = make(fake)
    page = run(source.address_page(CHAIN, WALLET))
    assert page.items == [] and page.next_cursor is None


def test_a_cursor_that_is_not_a_token_is_refused_before_any_request():
    fake = FakeHelius()
    source, _ = make(fake)
    for bad in ("abc", "1:2:3", "../etc", "1:x", ":5", "5:", " 1:2", "1:2\n"):
        with pytest.raises(InvalidCursor):
            run(source.address_page(CHAIN, WALLET, cursor=bad))
    assert fake.requests == []


# --- slot ranges -------------------------------------------------------------------------------


def test_a_slot_range_is_sent_to_the_service_and_only_that_range_comes_back():
    fake = FakeHelius()
    fill(fake, 30)  # slots HEAD-9999 .. HEAD-9970
    source, _ = make(fake)
    low, high = HEAD - 9990, HEAD - 9980

    found, _ = read_all(source, from_block=low, to_block=high)

    assert sorted(t.slot for t in found) == list(range(low, high + 1))
    assert options_of(fake)["filters"]["slot"] == {"gte": low, "lte": high}


def test_only_the_ends_given_are_sent():
    fake = FakeHelius()
    fill(fake, 3)
    source, _ = make(fake)
    run(source.address_page(CHAIN, WALLET, from_block=5))
    run(source.address_page(CHAIN, WALLET, to_block=HEAD))
    assert options_of(fake, index=0)["filters"]["slot"] == {"gte": 5}
    assert options_of(fake, index=1)["filters"]["slot"] == {"lte": HEAD}


# --- when the service is not as promised --------------------------------------------------------


@pytest.mark.parametrize(
    "result", [None, "text", [], {"data": "oops"}, {"data": None}, {"paginationToken": None}]
)
def test_a_listing_that_is_not_a_list_of_transactions_is_a_bad_response(result):
    fake = FakeHelius()
    fake.queued = [{"jsonrpc": "2.0", "result": result, "id": 1}]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="unexpected response shape"):
        run(source.address_page(CHAIN, WALLET))
    assert len(fake.requests) == 1  # a malformed answer is not retried


def test_a_body_with_no_result_is_a_bad_response():
    fake = FakeHelius()
    fake.queued = [{"jsonrpc": "2.0", "id": 1}]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="unexpected response shape"):
        run(source.block_height(CHAIN))


def test_unreadable_transactions_are_skipped_and_the_page_says_so():
    fake = FakeHelius()
    (good,) = fill(fake, 1)
    broken = {**good, "meta": {**good["meta"], "preBalances": [1]}}
    fake.queued = [
        {"jsonrpc": "2.0", "result": {"data": [broken, good], "paginationToken": None}, "id": 1}
    ]
    source, _ = make(fake)

    page = run(source.address_page(CHAIN, WALLET))

    assert [t.signature for t in page.items] == [signature(1)]
    assert page.coverage == ("1 transactions from helius could not be read and were skipped",)


def test_the_unsupported_version_error_is_a_request_problem_not_an_outage():
    fake = FakeHelius()
    error = {
        "jsonrpc": "2.0",
        "error": {"code": -32015, "message": "Transaction version (2)..."},
        "id": 1,
    }
    fake.queued = [error]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="transaction version") as info:
        run(source.address_page(CHAIN, WALLET))
    assert not info.value.retryable and len(fake.requests) == 1
    assert helius_body_error("helius", {"result": 1}) is None


def test_a_rejected_key_is_reported_once_and_never_retried():
    fake = FakeHelius(key="some-other-key-entirely-9999")
    source, _ = make(fake)
    with pytest.raises(ProviderAuthError):
        run(source.block_height(CHAIN))
    assert len(fake.requests) == 1


def test_a_429_is_waited_out_and_the_read_goes_on():
    fake = FakeHelius()
    fill(fake, 2)
    fake.queued = [(429, "Too many requests")]
    source, clock = make(fake)

    page = run(source.address_page(CHAIN, WALLET))

    assert len(page.items) == 2 and len(fake.requests) == 2 and clock.sleeps


# --- one transaction ---------------------------------------------------------------------------


def test_a_transaction_comes_back_as_the_node_gave_it_and_is_asked_for_finalized_and_parsed():
    fake = FakeHelius()
    fill(fake, 2)
    source, _ = make(fake)

    raw = run(source.transaction(CHAIN, signature(2)))

    assert raw["transaction"]["signatures"] == [signature(2)]
    assert "transactionIndex" not in raw  # getTransaction does not say where in the block
    assert fake.calls == [
        (
            "getTransaction",
            [
                signature(2),
                {
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 1,
                    "commitment": "finalized",
                },
            ],
        )
    ]


def test_an_unknown_or_not_yet_finalized_transaction_is_none():
    fake = FakeHelius()
    fill(fake, 1)
    fake.unfinalized = {signature(1)}
    source, _ = make(fake)
    assert run(source.transaction(CHAIN, signature(1))) is None
    assert run(source.transaction(CHAIN, signature(99))) is None


def test_a_transaction_answer_that_is_not_an_object_is_a_bad_response():
    fake = FakeHelius()
    fake.queued = [{"jsonrpc": "2.0", "result": "text", "id": 1}]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="unexpected response shape"):
        run(source.transaction(CHAIN, signature(1)))


# --- what a wallet holds -----------------------------------------------------------------------


def holdings(fake: FakeHelius) -> str:
    """A wallet that was paid 5,000 lamports and given 700 of a token under each token program."""
    mint1, mint2 = address(900), address(901)
    accounts = [
        address(50),
        WALLET,
        address(60),
        address(61),
        mint1,
        mint2,
        SYSTEM,
        TOKEN,
        TOKEN_2022,
    ]
    fake.add(
        item(
            1,
            accounts,
            [
                instruction(
                    SYSTEM,
                    "transfer",
                    {"source": accounts[0], "destination": WALLET, "lamports": 5000},
                )
            ],
            slot=HEAD - 50,
            lamports={accounts[0]: (10**9, 10**9 - 5005), WALLET: (0, 5000)},
            pre_tokens=[],
            post_tokens=[
                token_entry(accounts, accounts[2], mint1, WALLET, 700, 6, TOKEN),
                token_entry(accounts, accounts[3], mint2, WALLET, 700, 6, TOKEN_2022),
            ],
        )
    )
    return mint1 + " " + mint2


def test_a_wallets_balances_come_with_the_slot_each_is_as_of_and_are_read_per_token_program():
    fake = FakeHelius()
    mint1, mint2 = holdings(fake).split()
    source, _ = make(fake)

    figures = run(source.wallet_figures(CHAIN, WALLET))

    assert (figures.sol_raw, figures.sol_slot) == ("5000", HEAD)
    assert figures.tokens[TOKEN].balances == {mint1: "700"} and figures.tokens[TOKEN].slot == HEAD
    assert figures.tokens[TOKEN_2022].balances == {mint2: "700"}
    assert [name for name, _ in fake.calls] == [
        "getBalance",
        "getTokenAccountsByOwner",
        "getTokenAccountsByOwner",
    ]
    assert figures.provider == "helius"


def test_balances_are_as_of_the_slot_the_service_says_not_the_slot_asked_for():
    fake = FakeHelius()
    holdings(fake)
    fake.snapshot_lag = 25
    source, _ = make(fake)
    figures = run(source.wallet_figures(CHAIN, WALLET))
    assert figures.sol_slot == HEAD - 25 and figures.tokens[TOKEN].slot == HEAD - 25


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"context": {}, "value": 1},
        {"context": {"slot": 1}},
        {"context": {"slot": 1}, "value": -5},
        5,
        None,
    ],
)
def test_a_balance_that_is_not_a_balance_is_a_bad_response(answer):
    fake = FakeHelius()
    fake.queued = [{"jsonrpc": "2.0", "result": answer, "id": 1}]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="unexpected response shape"):
        run(source.wallet_figures(CHAIN, WALLET))


def test_token_holdings_that_are_not_token_holdings_are_a_bad_response():
    fake = FakeHelius()
    bad = {
        "context": {"slot": 1},
        "value": [{"account": {"data": {"parsed": {"info": {"mint": "nope"}}}}}],
    }
    fake.queued = [
        {"jsonrpc": "2.0", "result": {"context": {"slot": 1}, "value": 0}, "id": 1},
        {"jsonrpc": "2.0", "result": bad, "id": 1},
    ]
    source, _ = make(fake)
    with pytest.raises(ProviderBadResponse, match="unexpected response shape"):
        run(source.wallet_figures(CHAIN, WALLET))


def test_the_request_budget_is_spent_by_every_call_and_stops_a_read():
    fake = FakeHelius()
    fill(fake, 3)
    source, _ = make(fake, budget=RequestBudget(1))
    run(source.block_height(CHAIN))
    with pytest.raises(BudgetExceeded):
        run(source.block_height(CHAIN))


def test_real_transactions_pass_through_the_listing_unchanged():
    fake = FakeHelius()
    raw = load_fixture("solana/hidden_payout_v1")["item"]
    fake.add(raw)
    source, _ = make(fake)

    (found,) = run(source.address_page(CHAIN, "4jErKA4q4sUbAUWpseN8s2anSbdsTAAjX2PnwFRzEwQ2")).items

    assert found.signature == raw["transaction"]["signatures"][0] and found.version == "1"
    assert len(found.transfers) == 3 and found.slot == raw["slot"]


def test_a_wallets_token_accounts_of_one_mint_are_added_up():
    fake = FakeHelius()
    mint = address(900)
    first, second = address(61), address(62)
    accounts = [address(50), WALLET, first, second, mint, TOKEN]
    fake.add(
        item(
            1,
            accounts,
            [],
            slot=HEAD - 50,
            post_tokens=[
                token_entry(accounts, first, mint, WALLET, 700, 6, TOKEN),
                token_entry(accounts, second, mint, WALLET, 300, 6, TOKEN),
            ],
        )
    )
    source, _ = make(fake)

    figures = run(source.wallet_figures(CHAIN, WALLET))

    assert figures.tokens[TOKEN].balances == {mint: "1000"}
