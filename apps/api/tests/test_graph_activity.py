"""A stored history as figures and as a page of movements."""

from __future__ import annotations

from app.graph.activity import page_of, summarize
from app.graph.model import Direction
from tests.graph_helpers import ROOT, addr, at, flow

A, B, C = addr(10), addr(11), addr(12)


def test_the_figures_count_movements_and_different_counterparties_each_way():
    flows = [
        flow(A, ROOT, when=at(1)),
        flow(A, ROOT, when=at(2)),
        flow(B, ROOT, when=at(3)),
        flow(ROOT, C, when=at(8)),
        flow(ROOT, C, when=at(9)),
        flow(ROOT, B, when=at(9)),
    ]

    stats = summarize(flows, ROOT)

    assert (stats.transfers_in, stats.transfers_out) == (3, 3)
    assert (stats.counterparties_in, stats.counterparties_out) == (2, 2)
    assert (stats.first_seen, stats.last_seen) == (at(1), at(9))


def test_an_address_with_no_movements_has_empty_figures():
    stats = summarize([], ROOT)

    assert (stats.transfers_in, stats.transfers_out, stats.first_seen, stats.last_seen) == (
        0,
        0,
        None,
        None,
    )


def test_a_page_is_newest_first_with_undated_movements_last():
    flows = [
        flow(A, ROOT, 1, at(1), tx="0xold"),
        flow(B, ROOT, 2, at(5), tx="0xnew"),
        flow(C, ROOT, 3, timed=False, tx="0xundated"),
    ]

    rows, total = page_of(flows, ROOT, direction=Direction.BOTH, limit=10, offset=0)

    assert total == 3
    assert [r["transaction_hash"] for r in rows] == ["0xnew", "0xold", "0xundated"]
    assert rows[2]["timestamp"] is None


def test_each_row_says_which_way_the_money_went_and_who_the_other_party_was():
    flows = [flow(A, ROOT, when=at(2)), flow(ROOT, B, when=at(1))]

    rows, _ = page_of(flows, ROOT, direction=Direction.BOTH, limit=10, offset=0)

    assert [(r["direction"], r["counterparty"]) for r in rows] == [("in", A), ("out", B)]
    assert all(r["flow"] in flows for r in rows)


def test_a_direction_keeps_only_that_way_and_the_total_counts_only_those():
    flows = [flow(A, ROOT, when=at(3)), flow(B, ROOT, when=at(2)), flow(ROOT, C, when=at(1))]

    incoming, total_in = page_of(flows, ROOT, direction=Direction.IN, limit=10, offset=0)
    outgoing, total_out = page_of(flows, ROOT, direction=Direction.OUT, limit=10, offset=0)

    assert total_in == 2 and {r["direction"] for r in incoming} == {"in"}
    assert total_out == 1 and outgoing[0]["counterparty"] == C


def test_paging_slices_the_sorted_movements():
    flows = [flow(addr(100 + n), ROOT, when=at(n)) for n in range(1, 6)]

    rows, total = page_of(flows, ROOT, direction=Direction.BOTH, limit=2, offset=1)

    assert total == 5 and [r["counterparty"] for r in rows] == [addr(104), addr(103)]
    assert page_of(flows, ROOT, direction=Direction.BOTH, limit=2, offset=9)[0] == []


def test_a_movement_of_other_addresses_is_not_on_the_page():
    rows, total = page_of([flow(A, B)], ROOT, direction=Direction.BOTH, limit=10, offset=0)

    assert rows == [] and total == 0
