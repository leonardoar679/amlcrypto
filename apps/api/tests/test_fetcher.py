"""Reading a whole history: merging streams, ordering, limits, budgets and honest gaps."""

from __future__ import annotations

import pytest

from app.chains.models import AccountPage, Page, TransferType
from app.ingestion.fetcher import fetch_history
from app.providers.base import (
    AllProvidersFailed,
    BudgetExceeded,
    ProviderAuthError,
    ProviderUnavailable,
)
from app.providers.budget import RequestBudget
from tests.fake_alchemy import asset_row
from tests.fake_etherscan import ME, OTHER, internal_row, token_row, tx_row
from tests.helpers import run
from tests.rigs import BSC, ETHEREUM, Rig


def seed(rig: Rig) -> dict:
    """A small history in all three streams, with a transaction at index 2 of block 10."""
    parent = tx_row(10, ME, OTHER, value="5", transactionIndex="2")
    rig.etherscan.add("txlist", parent, tx_row(12, OTHER, ME, value="7"), tx_row(13, ME, OTHER))
    rig.etherscan.add("txlistinternal", internal_row(10, OTHER, ME, value="3", hash=parent["hash"]))
    rig.etherscan.add(
        "tokentx",
        token_row(11, OTHER, ME, value="9"),
        token_row(10, ME, OTHER, value="1", transactionIndex="5"),
    )
    return parent


# --- a complete read ---------------------------------------------------------------------------


def test_all_three_streams_are_merged_into_one_chronological_history():
    rig = Rig()
    seed(rig)

    history = run(fetch_history(rig.adapter(), ME, page_size=2))

    assert [(t.block_number, t.transfer_type.value, t.amount_raw) for t in history.transfers] == [
        (10, "native", "5"),  # the transaction at index 2 ...
        (10, "internal", "3"),  # ... its own internal transfer straight after it ...
        (10, "erc20", "1"),  # ... then the one at index 5
        (11, "erc20", "9"),
        (12, "native", "7"),
    ]
    assert len(history.transactions) == 3  # including the call that moved no value
    assert history.complete and history.coverage == [] and history.unavailable == []
    assert history.synced_through_block == history.head_block == rig.etherscan.head


def test_a_requested_block_range_limits_the_read_and_is_what_counts_as_synced():
    rig = Rig()
    seed(rig)

    history = run(fetch_history(rig.adapter(), ME, from_block=11, to_block=12))

    assert [t.block_number for t in history.transfers] == [11, 12]
    assert history.synced_through_block == 12


def test_a_movement_listed_in_both_directions_is_kept_once():
    rig = Rig()
    rig.alchemy.add(asset_row("external", 5, ME, ME), asset_row("external", 6, ME, OTHER))

    history = run(fetch_history(rig.adapter(BSC), ME))

    assert [t.block_number for t in history.transfers] == [5, 6]


def test_a_kind_no_source_provides_is_a_reported_gap_not_an_incomplete_read():
    rig = Rig()
    rig.alchemy.add(asset_row("external", 5, ME, OTHER))

    history = run(fetch_history(rig.adapter(BSC), ME))  # BNB, and Alchemy has no internal there

    assert history.complete and history.synced_through_block == history.head_block
    assert history.unavailable == ["internal"]
    assert "no configured source provides internal transfers on bsc" in history.coverage


def test_only_the_requested_kinds_are_read():
    rig = Rig()
    seed(rig)

    history = run(fetch_history(rig.adapter(), ME, kinds=frozenset({TransferType.NATIVE})))

    assert {t.transfer_type for t in history.transfers} == {TransferType.NATIVE}
    assert {call["action"] for call in rig.etherscan.calls} == {"eth_blockNumber", "txlist"}


def test_an_address_with_no_history_is_an_empty_complete_one():
    history = run(fetch_history(Rig().adapter(), ME))
    assert history.transfers == [] and history.transactions == [] and history.complete


# --- limits and resuming -----------------------------------------------------------------------


def test_a_limit_on_items_stops_the_read_and_the_rest_can_be_resumed_without_overlap():
    rig = Rig()
    rig.etherscan.add("txlist", *[tx_row(b, OTHER, ME, value=str(b)) for b in range(1, 31)])
    rig.etherscan.add("tokentx", *[token_row(b, OTHER, ME, value=str(b)) for b in range(1, 31)])
    adapter = rig.adapter()

    first = run(fetch_history(adapter, ME, page_size=5, max_items=12))
    assert not first.complete and first.synced_through_block is None
    # The account stream was cut mid-way; the two streams after it had not started (empty cursor).
    assert set(first.resume) == {"account", "internal", "erc20"}
    assert first.resume["internal"] == "" and first.resume["erc20"] == ""
    assert any("resume" in note for note in first.coverage)

    second = run(fetch_history(adapter, ME, page_size=5, resume=first.resume))
    assert second.complete

    everything = {t.identity for t in first.transfers} | {t.identity for t in second.transfers}
    assert len(everything) == 60
    assert not {t.identity for t in first.transfers} & {t.identity for t in second.transfers}


def test_resuming_reads_only_the_streams_that_were_left():
    rig = Rig()
    seed(rig)
    rig.etherscan.requests.clear()

    history = run(fetch_history(rig.adapter(), ME, resume={"erc20": ""}))

    assert {t.transfer_type for t in history.transfers} == {TransferType.ERC20}
    assert {c["action"] for c in rig.etherscan.calls} == {"eth_blockNumber", "tokentx"}


def test_running_out_of_request_budget_ends_the_read_politely_and_resuming_finishes_it():
    rig = Rig()
    rig.etherscan.add("txlist", *[tx_row(b, OTHER, ME, value="1") for b in range(1, 31)])

    starved = run(
        fetch_history(rig.adapter(budget=RequestBudget(limit=3)), ME, page_size=5, max_items=1000)
    )
    assert not starved.complete and starved.synced_through_block is None
    assert starved.resume["account"] and "request budget" in " ".join(starved.coverage)
    assert 0 < len(starved.transfers) < 30

    rest = run(fetch_history(rig.adapter(), ME, page_size=5, resume=starved.resume))
    assert rest.complete
    assert len({t.identity for t in starved.transfers + rest.transfers}) == 30


# --- when the source misbehaves ----------------------------------------------------------------


class StubSource:
    chain_id = "eip155:1"
    token_kind = TransferType.ERC20

    def __init__(self, *, height=None, account=None, transfers=None):
        self._height, self._account, self._transfers = height, account, transfers

    async def get_heights(self):
        if isinstance(self._height, Exception):
            raise self._height
        return self._height, self._height

    async def get_account_page(self, address, **kwargs):
        return self._account

    async def get_transfers(self, address, kind, **kwargs):
        return self._transfers


def test_a_cut_stream_makes_the_read_incomplete_and_says_why():
    cut = AccountPage([], [], None, truncated=True, coverage=("etherscan result window ran out",))
    source = StubSource(height=100, account=cut, transfers=Page([], None))

    history = run(fetch_history(source, ME))

    assert not history.complete and history.synced_through_block is None
    assert history.coverage == ["etherscan result window ran out"]


def test_an_unknown_height_is_noted_and_the_history_is_still_read():
    source = StubSource(
        height=ProviderUnavailable("alchemy", "HTTP 503"),
        account=AccountPage([], [], None),
        transfers=Page([], None),
    )

    history = run(fetch_history(source, ME))

    assert history.head_block is None and history.synced_through_block is None
    assert history.coverage == ["the chain's current height is unknown (HTTP 503)"]
    assert history.complete  # every stream was read; only the sync point is unknown


def test_a_provider_failure_while_reading_is_raised_not_hidden():
    rig = Rig()
    rig.etherscan.queued = [{"status": "0", "message": "NOTOK", "result": "Invalid API Key"}] * 2
    rig.alchemy.queued = [
        {"jsonrpc": "2.0", "id": 1, "error": {"code": 401, "message": "Unauthorized"}}
    ] * 2
    with pytest.raises((AllProvidersFailed, ProviderAuthError)):
        run(fetch_history(rig.adapter(), ME))


def test_the_budget_error_type_is_what_the_fetcher_treats_as_a_polite_stop():
    class Exhausted(StubSource):
        async def get_account_page(self, address, **kwargs):
            raise BudgetExceeded("etherscan", "request budget of 1 is used up")

    history = run(fetch_history(Exhausted(height=1, transfers=Page([], None)), ME))
    assert not history.complete and history.resume == {"account": "", "internal": "", "erc20": ""}


# --- the summary -------------------------------------------------------------------------------


def test_the_summary_is_compact_and_json_ready():
    import json

    rig = Rig()
    seed(rig)
    history = run(fetch_history(rig.adapter(), ME))

    summary = history.summary(latest=2)

    json.dumps(summary)
    assert summary["transfers"] == 5 and summary["transactions"] == 3
    assert summary["transfers_by_type"] == {"native": 2, "internal": 1, "erc20": 2}
    assert (summary["first_block"], summary["last_block"]) == (10, 12)
    assert summary["providers"] == ["etherscan"] and summary["complete"] is True
    assert [t["block_number"] for t in summary["latest"]] == [11, 12]
    assert ETHEREUM.chain_id == summary["chain_id"]


# --- incremental updates -----------------------------------------------------------------------


def test_an_update_starts_a_reorg_margin_before_the_synced_block():
    assert ETHEREUM.incremental_start(1000) == 1000 - 64
    assert BSC.incremental_start(1000) == 1000 - 30
    assert ETHEREUM.incremental_start(10) == 0  # never below the first block


def test_an_incremental_read_returns_only_news_plus_a_harmless_overlap():
    rig = Rig()
    old = [tx_row(b, OTHER, ME, value=str(b)) for b in (10, 50, 95)]
    rig.etherscan.add("txlist", *old)
    first = run(fetch_history(rig.adapter(), ME, to_block=100))
    assert first.complete and first.synced_through_block == 100

    news = [tx_row(b, OTHER, ME, value=str(b)) for b in (101, 130)]
    rig.etherscan.add("txlist", *news)
    start = ETHEREUM.incremental_start(first.synced_through_block)  # block 36
    update = run(fetch_history(rig.adapter(), ME, from_block=start))

    assert [t.block_number for t in update.transfers] == [50, 95, 101, 130]  # 10 is before 36
    overlap = {t.identity for t in update.transfers} & {t.identity for t in first.transfers}
    assert {identity[1] for identity in overlap} == {old[1]["hash"], old[2]["hash"]}
    merged = {t.identity for t in first.transfers} | {t.identity for t in update.transfers}
    assert len(merged) == 5  # three old + two new, however often a block was read
