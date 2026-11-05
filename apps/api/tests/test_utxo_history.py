"""Reading a whole Bitcoin history: order, spend links, the check against the node's own figures,
limits, resuming and honest gaps."""

from __future__ import annotations

import json
import re

import pytest

from app.chains.bitcoin.models import SpendState
from app.ingestion.utxo import fetch_utxo_history
from app.providers.base import AllProvidersFailed
from app.providers.budget import RequestBudget
from tests.fake_esplora import HEAD, FakeEsplora, p2pkh, pay, spend, tx, txid
from tests.helpers import load_fixture, run
from tests.rigs import Rig

ME, A, B, C = (p2pkh(n) for n in (1, 2, 3, 4))


def flow(rig: Rig) -> None:
    """ME is paid, spends part of it (getting change), and is paid again."""
    rig.esplora.add(
        tx(1, [spend(txid(900), 0, A, 60_000)], [pay(ME, 50_000), pay(A, 9_000)], height=101),
        tx(2, [spend(txid(1), 0, ME, 50_000)], [pay(B, 30_000), pay(ME, 19_000)], height=102),
        tx(3, [spend(txid(901), 0, C, 8_000)], [pay(ME, 7_000)], height=103),
    )


def incoming(rig: Rig, count: int) -> None:
    rig.esplora.add(
        *[
            tx(
                n,
                [spend(txid(5000 + n), 0, p2pkh(9000 + n), 10_000)],
                [pay(ME, 9_000)],
                height=100 + n,
            )
            for n in range(1, count + 1)
        ]
    )


def read(rig: Rig, **options):
    options.setdefault("max_items", 5000)
    return run(fetch_utxo_history(rig.bitcoin_adapter(), ME, **options))


def state_of(history, n, index):
    (item,) = [t for t in history.transactions if t.txid == txid(n)]
    return item.outputs[index].spend_state


def asked(rig: Rig, path: str) -> int:
    return sum(1 for p, _ in rig.esplora.calls if p == path)


# --- a whole read ------------------------------------------------------------------------------


def test_a_whole_history_is_oldest_first_checked_against_the_node_and_complete():
    rig = Rig()
    flow(rig)

    history = read(rig)

    assert [t.txid for t in history.transactions] == [txid(1), txid(2), txid(3)]
    assert history.complete and history.coverage == []
    assert history.head_block == history.synced_through_block == HEAD
    assert (history.received_raw, history.sent_raw) == ("76000", "50000")
    assert history.provider_summary.balance_raw == "26000"  # the node says the same
    assert {k: v.role for k, v in history.activity.items()} == {
        txid(1): "receiver",
        txid(2): "both",
        txid(3): "receiver",
    }
    assert history.activity[txid(2)].net_raw == "-31000"  # 30,000 paid away and a 1,000 fee


def test_a_whole_read_links_the_spends_and_proves_what_is_still_unspent():
    rig = Rig()
    flow(rig)

    history = read(rig)

    assert state_of(history, 1, 0) is SpendState.SPENT  # spent by transaction 2
    assert state_of(history, 1, 1) is SpendState.UNKNOWN  # A's change: nothing here says
    assert state_of(history, 2, 0) is SpendState.UNKNOWN  # B's
    assert state_of(history, 2, 1) is SpendState.UNSPENT  # ME's change
    assert state_of(history, 3, 0) is SpendState.UNSPENT
    (spent,) = [o for o in history.transactions[0].outputs if o.spend_state is SpendState.SPENT]
    assert (spent.spent_by_txid, spent.spent_by_input_index) == (txid(2), 0)


def test_the_unspent_outputs_of_the_address_add_up_to_the_nodes_balance():
    rig = Rig()
    flow(rig)
    history = read(rig)
    unspent = [
        int(o.value_raw)
        for t in history.transactions
        for o in t.outputs
        if o.destination_address == ME and o.spend_state is SpendState.UNSPENT
    ]
    assert sum(unspent) == int(history.provider_summary.balance_raw)


def test_a_longer_history_is_read_across_pages_and_still_agrees_with_the_node():
    rig = Rig()
    incoming(rig, 60)

    history = read(rig)

    assert len(history.transactions) == 60 and history.complete
    assert [t.block_height for t in history.transactions] == list(range(101, 161))
    assert asked(rig, f"/address/{ME}/txs/chain") == 1  # the first page; the rest continue after it
    assert history.provider_summary.tx_count == 60


def test_confirmations_are_worked_out_from_the_height_the_read_began_at():
    rig = Rig()
    flow(rig)
    history = read(rig)
    newest = history.transactions[-1]
    assert newest.confirmations == HEAD - newest.block_height + 1
    assert newest.confirmation_status.value == "confirmed"


def test_an_address_with_no_history_is_an_empty_complete_one():
    history = read(Rig())
    assert history.transactions == [] and history.complete and history.coverage == []
    assert history.provider_summary.tx_count == 0


def test_a_real_transaction_in_a_history_gives_the_amounts_the_chain_recorded():
    rig = Rig()
    raw = load_fixture("bitcoin/self_change")["tx"]
    rig.esplora.add(raw)
    reused = next(
        o["scriptpubkey_address"]
        for o in raw["vout"]
        if o["scriptpubkey_address"] == raw["vin"][0]["prevout"]["scriptpubkey_address"]
    )

    history = run(fetch_utxo_history(rig.bitcoin_adapter(), reused, max_items=100))

    mine = history.activity[raw["txid"]]
    assert mine.sent_raw == str(raw["vin"][0]["prevout"]["value"])
    assert mine.received_raw == str(
        next(o["value"] for o in raw["vout"] if o["scriptpubkey_address"] == reused)
    )
    assert history.complete and history.coverage == []


# --- when the read does not agree with the node ---------------------------------------------------


def test_a_transaction_the_node_counts_but_does_not_list_makes_the_read_incomplete_and_loud():
    rig = Rig()
    flow(rig)
    rig.esplora.unlisted = {txid(2)}

    history = read(rig)

    assert len(history.transactions) == 2
    assert not history.complete and history.synced_through_block is None
    assert history.resume == {}  # nothing to carry on from: the listing itself is short
    (note,) = [n for n in history.coverage if n.startswith("MISMATCH")]
    assert "counts 3 transactions" in note and "this read holds 2" in note


@pytest.mark.parametrize("figure", ["tx_count", "funded_txo_sum", "spent_txo_sum"])
def test_each_of_the_nodes_figures_is_checked_on_its_own(figure):
    rig = Rig()
    flow(rig)
    rig.esplora.skew = {figure: 1}  # the node disagrees in this one respect only

    history = read(rig)

    assert not history.complete and history.synced_through_block is None
    assert any(note.startswith("MISMATCH") for note in history.coverage)


def test_an_amount_missing_from_the_read_is_caught_even_when_the_count_agrees():
    rig = Rig()
    flow(rig)
    rig.esplora.unresolved = {(txid(2), 0)}  # served without the output it spends

    history = read(rig)

    assert len(history.transactions) == 3 and not history.complete
    assert history.activity[txid(2)].complete is False
    assert any(n.startswith("MISMATCH") and "spent 50000" in n for n in history.coverage)
    assert any("did not give" in n for n in history.coverage)  # and the page said why


class ArrivingBlock(FakeEsplora):
    """A transaction is confirmed right after the node's figures are first read."""

    def __init__(self, arriving):
        super().__init__()
        self.arriving, self.served_figures = arriving, False

    def route(self, path):
        response = super().route(path)
        if re.fullmatch(r"/address/[^/]+", path) and not self.served_figures:
            self.served_figures = True
            self.add(self.arriving)
        return response


def test_a_block_arriving_between_the_figures_and_the_first_page_is_not_a_gap():
    rig = Rig()
    flow(rig)
    rig.esplora = ArrivingBlock(
        tx(4, [spend(txid(902), 0, A, 3_000)], [pay(ME, 2_000)], height=104)
    )
    flow(rig)

    history = read(rig)

    assert len(history.transactions) == 4 and history.complete
    assert history.coverage == []
    assert history.provider_summary.tx_count == 4  # the figures were asked for again
    assert asked(rig, f"/address/{ME}") == 2


def test_figures_that_cannot_be_read_are_said_so_and_the_read_goes_on_unchecked():
    rig = Rig()
    flow(rig)
    rig.esplora.broken_summary = True

    history = read(rig)

    assert len(history.transactions) == 3 and history.complete
    assert history.provider_summary is None
    assert len(history.coverage) == 1 and "not checked against them" in history.coverage[0]


def test_unconfirmed_transactions_are_noted_and_left_out():
    rig = Rig()
    flow(rig)
    rig.esplora.add(tx(9, [spend(txid(2), 1, ME, 19_000)], [pay(B, 18_000)], height=None))

    history = read(rig)

    assert len(history.transactions) == 3 and history.complete
    assert history.coverage == [
        "1 unconfirmed transactions involve this address and are not included; "
        "an output shown unspent may be about to be spent"
    ]


def test_without_a_known_height_the_read_says_so_and_still_reads():
    rig = Rig()
    flow(rig)
    rig.esplora.broken_tip = True

    history = read(rig)

    assert len(history.transactions) == 3 and history.head_block is None
    assert history.synced_through_block is None
    assert history.coverage[0].startswith("the chain's current height is unknown")
    assert history.transactions[-1].confirmations is None


def test_a_provider_failure_while_reading_is_raised_not_hidden():
    rig = Rig()
    flow(rig)
    rig.esplora.queued = [(403, "Forbidden")] * 3
    with pytest.raises(AllProvidersFailed):
        read(rig)


# --- block ranges ------------------------------------------------------------------------------


def test_a_range_read_is_neither_checked_against_the_node_nor_used_to_prove_anything_unspent():
    rig = Rig()
    flow(rig)

    history = read(rig, from_block=102, to_block=103)

    assert [t.txid for t in history.transactions] == [txid(2), txid(3)]
    assert history.complete and history.synced_through_block == 103
    assert history.provider_summary is None and history.coverage == []
    assert asked(rig, f"/address/{ME}") == 0
    assert state_of(history, 3, 0) is SpendState.UNKNOWN  # a later spend may be outside the range


def test_with_no_end_block_a_range_read_is_synced_through_the_tip():
    rig = Rig()
    flow(rig)
    assert read(rig, from_block=102).synced_through_block == HEAD


# --- limits and resuming ------------------------------------------------------------------------


def test_a_limit_stops_the_read_with_the_newest_and_resuming_finishes_it_without_overlap():
    rig = Rig()
    incoming(rig, 60)

    first = read(rig, max_items=30)

    assert not first.complete and first.synced_through_block is None
    assert len(first.transactions) == 50  # two whole pages; the limit is checked between pages
    assert first.resume["transactions"].startswith("esplora|")
    assert any("the newest ones" in note and "resume" in note for note in first.coverage)
    assert min(t.block_height for t in first.transactions) == 111

    rig.esplora.requests.clear()
    second = read(rig, resume=first.resume)

    assert second.complete and len(second.transactions) == 10
    assert {t.txid for t in first.transactions}.isdisjoint({t.txid for t in second.transactions})
    assert len({t.txid for t in first.transactions + second.transactions}) == 60
    assert asked(rig, f"/address/{ME}") == 0  # a resumed read is not checked against the node


def test_the_limit_is_reached_exactly_when_that_many_transactions_are_in_hand():
    rig = Rig()
    incoming(rig, 60)

    history = read(rig, max_items=25)  # exactly the first page

    assert len(history.transactions) == 25 and not history.complete
    assert history.resume["transactions"].startswith("esplora|")


def test_running_out_of_request_budget_ends_the_read_politely_and_resuming_finishes_it():
    rig = Rig()
    incoming(rig, 60)

    # The height, the figures and the first page use up the three requests.
    starved = run(
        fetch_utxo_history(rig.bitcoin_adapter(budget=RequestBudget(limit=3)), ME, max_items=5000)
    )

    assert not starved.complete and len(starved.transactions) == 25
    assert starved.resume["transactions"] and "request budget" in " ".join(starved.coverage)

    rest = read(rig, resume=starved.resume)
    assert rest.complete and len(rest.transactions) == 35


def test_a_read_refused_before_its_first_page_resumes_from_the_start():
    rig = Rig()
    incoming(rig, 3)

    starved = run(
        fetch_utxo_history(rig.bitcoin_adapter(budget=RequestBudget(limit=2)), ME, max_items=5000)
    )

    assert starved.transactions == [] and starved.resume == {"transactions": ""}
    rest = read(rig, resume=starved.resume)
    assert rest.complete and len(rest.transactions) == 3


# --- order and presentation --------------------------------------------------------------------


def test_within_one_block_a_spender_follows_what_it_spends():
    rig = Rig()
    # Both in block 101. By id alone the spender (1) would come before what it spends (9).
    rig.esplora.add(
        tx(9, [spend(txid(900), 0, A, 9_000)], [pay(ME, 8_000)], height=101),
        tx(1, [spend(txid(9), 0, ME, 8_000)], [pay(B, 7_000)], height=101),
    )

    history = read(rig)

    assert [t.txid for t in history.transactions] == [txid(9), txid(1)]
    assert history.complete


def test_the_summary_is_compact_and_json_ready():
    rig = Rig()
    flow(rig)
    history = read(rig)

    summary = history.summary(latest=2)

    json.dumps(summary)
    assert summary["transactions"] == 3 and summary["complete"] is True
    assert (summary["first_block"], summary["last_block"]) == (101, 103)
    assert (summary["received_raw"], summary["sent_raw"]) == ("76000", "50000")
    assert summary["providers"] == ["esplora"] and summary["provider_summary"]["tx_count"] == 3
    assert [item["txid"] for item in summary["latest"]] == [txid(2), txid(3)]
    assert summary["latest"][0]["activity"]["role"] == "both"
    assert history.summary(latest=0)["latest"] == []


def test_the_full_listing_carries_each_transaction_with_the_addresss_part_in_it():
    rig = Rig()
    flow(rig)
    history = read(rig)
    full = history.detailed(history.transactions[1])
    json.dumps(full)
    assert full["txid"] == txid(2) and full["activity"]["net_raw"] == "-31000"
    assert full["inputs"][0]["source_address"] == ME and len(full["outputs"]) == 2
