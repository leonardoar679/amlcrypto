"""What a read reports about each stream, and what it keeps when a provider error ends it.

A history read is many requests. When one fails for good, the pages already read are good data that
cost real quota; the readers hand them to whoever stores histories, on the exception itself.
"""

from __future__ import annotations

import pytest

from app.chains.models import AccountPage, Page, StreamState, StreamStatus, TransferType
from app.ingestion.fetcher import assemble_history, fetch_history
from app.ingestion.partial import partial_of
from app.ingestion.solana import SolanaHistory, fetch_solana_history
from app.ingestion.utxo import UtxoHistory, fetch_utxo_history
from app.providers.base import ProviderUnavailable
from app.providers.budget import RequestBudget
from tests.fake_etherscan import ME, OTHER, internal_row, token_row, tx_row
from tests.fake_helius import HEAD as SOLANA_HEAD
from tests.fake_helius import address, transfer_item
from tests.helpers import run
from tests.rigs import Rig

STALL = ProviderUnavailable("etherscan", "HTTP 503")


# --- the state of each stream ------------------------------------------------------------------


def seeded(rows: int = 4) -> Rig:
    rig = Rig()
    rig.etherscan.add(
        "txlist", *[tx_row(block, OTHER, ME, value=str(block)) for block in range(5, 5 + rows)]
    )
    rig.etherscan.add("tokentx", token_row(6, OTHER, ME, value="42"))
    return rig


def test_a_read_to_the_end_says_every_stream_is_complete_through_the_settled_height():
    rig = seeded()

    history = run(fetch_history(rig.adapter(), ME, page_size=2))

    through = rig.etherscan.head
    assert history.streams == {
        "account": StreamState(StreamStatus.COMPLETE, synced_through=through),
        "internal": StreamState(StreamStatus.COMPLETE, synced_through=through),
        "erc20": StreamState(StreamStatus.COMPLETE, synced_through=through),
    }


def test_a_block_range_read_is_complete_through_its_last_block():
    rig = seeded()

    history = run(fetch_history(rig.adapter(), ME, from_block=5, to_block=7))

    assert {s.synced_through for s in history.streams.values()} == {7}


def test_a_limit_leaves_the_stream_it_stopped_in_and_those_after_it_to_resume():
    rig = seeded(rows=30)

    history = run(fetch_history(rig.adapter(), ME, page_size=5, max_items=7))

    account = history.streams["account"]
    assert account.status is StreamStatus.PARTIAL and account.cursor == history.resume["account"]
    assert history.streams["internal"] == StreamState(StreamStatus.PARTIAL, cursor="")
    assert history.streams["erc20"] == StreamState(StreamStatus.PARTIAL, cursor="")


def test_the_budget_stops_a_stream_at_the_page_it_could_not_ask_for():
    rig = seeded(rows=30)

    history = run(
        fetch_history(rig.adapter(budget=RequestBudget(3)), ME, page_size=5, max_items=1000)
    )

    assert not history.complete
    assert history.streams["account"].status is StreamStatus.PARTIAL
    assert history.streams["account"].cursor == history.resume["account"]


def test_a_stream_no_source_provides_is_unavailable_and_a_cut_one_is_truncated():
    class Source:
        chain_id = "eip155:1"
        token_kind = TransferType.ERC20

        async def get_heights(self):
            return 50, 48

        async def get_account_page(self, address, **kwargs):
            return AccountPage([], [], None, truncated=True)

        async def get_transfers(self, address, kind, **kwargs):
            return Page([], None, unavailable=kind is TransferType.INTERNAL)

    history = run(fetch_history(Source(), ME))

    assert history.streams["account"] == StreamState(StreamStatus.TRUNCATED, synced_through=48)
    assert history.streams["internal"].status is StreamStatus.UNAVAILABLE
    assert history.streams["erc20"] == StreamState(StreamStatus.COMPLETE, synced_through=48)


def test_a_resume_read_reports_only_the_streams_it_read():
    rig = seeded()

    history = run(fetch_history(rig.adapter(), ME, resume={"erc20": ""}))

    assert list(history.streams) == ["erc20"]


# --- what a failed read keeps ------------------------------------------------------------------


class FailsOnSecondAccountPage:
    chain_id = "eip155:1"
    token_kind = TransferType.ERC20

    def __init__(self, first: AccountPage):
        self.first, self.calls = first, 0

    async def get_heights(self):
        return 100, 100

    async def get_account_page(self, address, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return self.first
        raise STALL

    async def get_transfers(self, address, kind, **kwargs):
        return Page([], None)


def test_the_pages_read_before_an_error_travel_on_the_error():
    rig = seeded()
    first = run(rig.adapter().get_account_page(ME, limit=2))
    assert first.next_cursor is not None

    source = FailsOnSecondAccountPage(first)
    with pytest.raises(ProviderUnavailable) as raised:
        run(fetch_history(source, ME, page_size=2))

    partial = partial_of(raised.value)
    assert partial is not None and not partial.complete
    assert [t.block_number for t in partial.transfers] == [5, 6]
    assert len(partial.transactions) == 2
    assert partial.synced_through_block is None
    # The page that failed is the one to read again, with every stream after it.
    assert partial.resume == {"account": first.next_cursor, "internal": "", "erc20": ""}
    assert partial.streams["account"] == StreamState(StreamStatus.PARTIAL, cursor=first.next_cursor)
    assert partial.coverage[-1] == "stopped: HTTP 503"


def test_a_stream_already_finished_stays_finished_when_a_later_one_fails():
    class FailsInTokens(FailsOnSecondAccountPage):
        async def get_account_page(self, address, **kwargs):
            return AccountPage([], [], None)

        async def get_transfers(self, address, kind, **kwargs):
            if kind is TransferType.ERC20:
                raise STALL
            return Page([], None)

    with pytest.raises(ProviderUnavailable) as raised:
        run(fetch_history(FailsInTokens(AccountPage([], [], None)), ME))

    partial = partial_of(raised.value)
    assert partial.streams["account"].status is StreamStatus.COMPLETE
    assert partial.streams["internal"].status is StreamStatus.COMPLETE
    assert partial.streams["erc20"] == StreamState(StreamStatus.PARTIAL, cursor="")


def test_an_error_nobody_stores_a_history_for_is_the_same_error_as_ever():
    source = FailsOnSecondAccountPage(AccountPage([], [], "next"))

    with pytest.raises(ProviderUnavailable, match="HTTP 503"):
        run(fetch_history(source, ME))


def test_a_bitcoin_read_keeps_the_pages_before_the_failure():
    from tests.fake_esplora import p2pkh, pay, spend, tx, txid
    from tests.rigs import BITCOIN

    rig = Rig()
    wallet = p2pkh(1)
    for n in range(1, 31):  # two pages of 25
        rig.esplora.add(tx(n, [spend(txid(900 + n), 0, p2pkh(9), 9000)], [pay(wallet, 8000)]))
    adapter = rig.bitcoin_adapter()
    real = adapter.get_address_page
    calls = []

    async def second_page_fails(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise STALL
        return await real(*args, **kwargs)

    adapter.get_address_page = second_page_fails

    with pytest.raises(ProviderUnavailable) as raised:
        run(fetch_utxo_history(adapter, wallet))

    partial = partial_of(raised.value)
    assert isinstance(partial, UtxoHistory) and not partial.complete
    assert len(partial.transactions) == 25 and partial.chain_id == BITCOIN.chain_id
    assert partial.resume["transactions"] != ""
    assert partial.provider_summary is None


def test_a_solana_read_keeps_the_pages_before_the_failure():
    rig = Rig()
    wallet = address(5)
    rig.helius.add(
        *[
            transfer_item(n, address(100 + n), wallet, 1000 + n, slot=SOLANA_HEAD - 500 + n)
            for n in range(1, 151)
        ]
    )
    adapter = rig.solana_adapter()
    real = adapter.get_address_page
    calls = []

    async def second_page_fails(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise STALL
        return await real(*args, **kwargs)

    adapter.get_address_page = second_page_fails

    with pytest.raises(ProviderUnavailable) as raised:
        run(fetch_solana_history(adapter, wallet))

    partial = partial_of(raised.value)
    assert isinstance(partial, SolanaHistory) and not partial.complete
    assert len(partial.transactions) == 100 and partial.figures is None
    assert partial.resume["transactions"] != ""
    assert set(partial.activity) == {tx.signature for tx in partial.transactions}


# --- one way to put a history together ---------------------------------------------------------


def test_the_same_records_always_give_the_same_history_whatever_order_they_arrive_in():
    rig = seeded()
    history = run(fetch_history(rig.adapter(), ME))
    rig.etherscan.add("txlistinternal", internal_row(7, OTHER, ME, value="3"))

    shuffled_transfers = list(reversed(history.transfers))
    shuffled_transactions = list(reversed(history.transactions))
    transfers, transactions = assemble_history(shuffled_transfers, shuffled_transactions)

    assert transfers == history.transfers and transactions == history.transactions
