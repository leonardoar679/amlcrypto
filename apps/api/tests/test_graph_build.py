"""The bounded expansion: what is expanded, what is capped, and what the graph says it left out."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.chains.models import StreamStatus
from app.database.repositories.acquisition import Coverage
from app.graph.bridges import BridgeRegistry
from app.graph.builder import build_graph
from app.graph.model import Basis, Direction, GraphLimits, NodeCoverage
from app.graph.reader import judge
from app.ingestion.families import NEVER
from app.intel.model import Category, Freshness
from tests.graph_helpers import (
    NOW,
    ROOT,
    USDT,
    FakeLabels,
    FakeReader,
    addr,
    at,
    flow,
    label,
)
from tests.helpers import run
from tests.rigs import ETHEREUM

A, B, C, D = addr(10), addr(11), addr(12), addr(13)


def build(flows, *, labels=None, limits=None, reader=None, root=ROOT, **options):
    reader = reader or FakeReader(flows)
    graph = run(
        build_graph(
            reader,
            labels or FakeLabels(),
            ETHEREUM,
            root,
            limits or GraphLimits(),
            now=NOW,
            **options,
        )
    )
    return graph, reader


def ids(graph):
    return [node.address for node in graph.nodes]


def edge_of(graph, source, target, asset="native"):
    wanted = (f"ethereum:{source}", f"ethereum:{target}")
    return next(e for e in graph.edges if (e.source, e.target) == wanted and e.asset.key == asset)


# --- scope -------------------------------------------------------------------------------------


def test_a_one_hop_graph_has_the_root_its_counterparties_and_their_links_to_the_root():
    graph, reader = build([flow(A, ROOT), flow(ROOT, B)])

    assert ids(graph)[0] == ROOT and sorted(ids(graph)[1:]) == [A, B]
    assert [n.depth for n in graph.nodes] == [0, 1, 1]
    assert {(e.source, e.target) for e in graph.edges} == {
        (f"ethereum:{A}", f"ethereum:{ROOT}"),
        (f"ethereum:{ROOT}", f"ethereum:{B}"),
    }
    assert reader.loaded == [ROOT]  # the counterparties are shown, not expanded
    assert graph.complete and graph.root == f"ethereum:{ROOT}"


def test_a_movement_between_two_unexpanded_addresses_is_unknown_and_not_shown():
    graph, _ = build([flow(A, ROOT), flow(B, ROOT), flow(A, B)])

    assert all(ROOT in (e.source[-42:], e.target[-42:]) for e in graph.edges)
    assert len(graph.edges) == 2


def test_a_two_hop_graph_expands_the_counterparties_and_adds_what_they_touch():
    graph, reader = build(
        [flow(A, ROOT), flow(C, A), flow(A, B), flow(ROOT, B)], limits=GraphLimits(depth=2)
    )

    assert ids(graph)[0] == ROOT and sorted(ids(graph)) == [ROOT, A, B, C]
    assert reader.loaded[0] == ROOT and sorted(reader.loaded) == [ROOT, A, B]
    assert {n.address: n.depth for n in graph.nodes} == {ROOT: 0, A: 1, B: 1, C: 2}
    assert {n.address: n.expanded for n in graph.nodes} == {ROOT: True, A: True, B: True, C: False}
    # A to B is in A's history, and B is in the graph: it is shown.
    assert edge_of(graph, A, B)


def test_a_movement_between_two_expanded_addresses_is_one_edge_not_two():
    graph, _ = build([flow(A, ROOT), flow(ROOT, B), flow(A, B, 5)], limits=GraphLimits(depth=2))

    (edge,) = [e for e in graph.edges if e.source.endswith(A) and e.target.endswith(B)]
    assert edge.count == 1 and edge.total == 5


def test_the_root_alone_is_a_complete_graph_when_it_has_no_activity():
    graph, _ = build([])

    assert ids(graph) == [ROOT] and graph.edges == [] and graph.complete


# --- edges -------------------------------------------------------------------------------------


def test_an_edge_adds_up_one_asset_between_two_addresses_and_keeps_the_proof():
    moves = [flow(A, ROOT, n, at(n), tx=f"0x{n:064x}") for n in range(1, 8)]
    graph, _ = build(moves)

    (edge,) = graph.edges
    assert edge.count == 7 and edge.total == sum(range(1, 8))
    assert edge.first == at(1) and edge.last == at(7)
    # The five most recent movements, newest first.
    assert [e.transaction for e in edge.evidence] == [f"0x{n:064x}" for n in (7, 6, 5, 4, 3)]


def test_different_assets_between_the_same_two_addresses_are_different_edges():
    graph, _ = build([flow(A, ROOT, 5), flow(A, ROOT, 7, asset=USDT, event="erc20:x")])

    assert sorted(e.asset.key for e in graph.edges) == sorted(["native", USDT.key])


def test_edges_are_shaped_for_cytoscape_with_the_amount_in_whole_units():
    graph, _ = build([flow(A, ROOT, 5 * 10**17, at(3), tx="0xabc")])

    body = graph.to_dict()
    (edge,) = body["edges"]
    assert edge["source"] == f"ethereum:{A}" and edge["target"] == f"ethereum:{ROOT}"
    assert (
        edge["asset"] == "ETH" and edge["amount"] == "0.5" and edge["amount_raw"] == str(5 * 10**17)
    )
    assert (
        edge["transaction_hash"] == "0xabc"
        and edge["timestamp"].endswith("Z")
        or "+" in edge["timestamp"]
    )
    node = next(n for n in body["nodes"] if n["address"] != body["nodes"][0]["address"])
    assert node["id"] == f"ethereum:{node['address'].lower()}" and node["risk_level"] is None


def test_edge_ids_are_assigned_in_order_nearest_the_root_first():
    graph, _ = build([flow(A, ROOT), flow(C, A), flow(A, B)], limits=GraphLimits(depth=2))

    first = graph.edges[0]
    assert ROOT in (first.source[-42:], first.target[-42:])
    assert [e.id for e in graph.edges] == [f"e{n}" for n in range(1, len(graph.edges) + 1)]


# --- direction ---------------------------------------------------------------------------------


def test_following_money_that_arrived_ignores_where_the_root_sent_it():
    graph, _ = build([flow(A, ROOT), flow(ROOT, B)], limits=GraphLimits(direction=Direction.IN))

    assert ids(graph) == [ROOT, A]
    assert [(e.source[-42:], e.target[-42:]) for e in graph.edges] == [(A, ROOT)]


def test_following_money_that_left_ignores_where_it_came_from():
    graph, _ = build([flow(A, ROOT), flow(ROOT, B)], limits=GraphLimits(direction=Direction.OUT))

    assert ids(graph) == [ROOT, B]


# --- limits and the gaps they leave ------------------------------------------------------------


def test_only_the_most_active_counterparties_are_followed_and_the_rest_are_counted():
    moves = [flow(addr(100 + i), ROOT) for i in range(5)]
    moves += [flow(addr(200), ROOT) for _ in range(3)]  # the busiest
    graph, _ = build(moves, limits=GraphLimits(per_node=2))

    assert ids(graph)[1] == addr(200)
    assert len(graph.nodes) == 3
    (gap,) = graph.gaps
    assert gap.code == "counterparties_not_followed" and gap.count == 4
    assert gap.node == f"ethereum:{ROOT}" and not graph.complete
    assert graph.nodes[0].counterparties == 6 and graph.nodes[0].counterparties_shown == 2


def test_a_known_risky_counterparty_is_always_followed_however_quiet():
    quiet_risky = addr(999)
    moves = [flow(addr(100 + i), ROOT) for i in range(30)] + [flow(quiet_risky, ROOT)]
    graph, _ = build(
        moves,
        labels=FakeLabels(label(quiet_risky, Category.MIXER)),
        limits=GraphLimits(per_node=3),
    )

    assert ids(graph)[1] == quiet_risky


def test_a_labelled_service_ranks_above_an_unlabelled_address_but_below_a_risky_one():
    exchange, risky = addr(300), addr(301)
    moves = [flow(addr(100 + i), ROOT) for i in range(5)] + [
        flow(exchange, ROOT),
        flow(risky, ROOT),
    ]
    graph, _ = build(
        moves,
        labels=FakeLabels(label(exchange, Category.EXCHANGE), label(risky, Category.SCAM)),
        limits=GraphLimits(per_node=2),
    )

    assert ids(graph)[1:] == [risky, exchange]


def test_equally_ranked_counterparties_come_in_a_fixed_order():
    moves = [flow(addr(i), ROOT, when=at(1)) for i in (5, 3, 4)]
    graph, _ = build(moves)
    again, _ = build(list(reversed(moves)))

    assert ids(graph) == ids(again) == [ROOT, addr(3), addr(4), addr(5)]


def test_the_node_limit_stops_the_graph_and_says_how_many_were_held_back():
    moves = [flow(addr(100 + i), ROOT) for i in range(10)]
    graph, _ = build(moves, limits=GraphLimits(max_nodes=4, per_node=25))

    assert len(graph.nodes) == 4
    codes = {gap.code: gap for gap in graph.gaps}
    assert codes["node_limit"].count == 7  # 10 followed - 3 admitted
    assert not graph.complete


def test_the_edge_limit_keeps_the_edges_nearest_the_root():
    moves = [flow(A, ROOT), flow(B, ROOT), flow(C, A), flow(D, A)]
    graph, _ = build(moves, limits=GraphLimits(depth=2, max_edges=2))

    assert len(graph.edges) == 2
    assert all(ROOT in (e.source[-42:], e.target[-42:]) for e in graph.edges)
    assert next(g for g in graph.gaps if g.code == "edge_limit").count == 2


def test_nothing_cut_means_complete():
    graph, _ = build([flow(A, ROOT)], limits=GraphLimits(per_node=1, max_nodes=2, max_edges=1))

    assert graph.complete and graph.gaps == []


# --- what the stored histories cover -----------------------------------------------------------


def not_read():
    return {ROOT: judge({}, f"ethereum:{ROOT}")}


def test_an_address_with_no_stored_history_makes_the_graph_incomplete_and_says_so():
    graph, _ = build([], reader=FakeReader([], states=not_read()))

    (gap,) = graph.gaps
    assert gap.code == "history_not_read" and gap.node == f"ethereum:{ROOT}"
    assert graph.nodes[0].coverage.state == "not_read" and not graph.complete


def row(stream, status, **extra):
    return Coverage(stream=stream, status=status, synced_at=at(0), **extra)


def test_a_read_that_failed_before_getting_anything_is_a_history_never_read():
    failed = Coverage(stream="account", status=StreamStatus.PARTIAL.value, synced_at=NEVER)

    coverage, gaps = judge({"account": failed}, "n")

    assert coverage.state == "not_read" and [g.code for g in gaps] == ["history_not_read"]


def test_a_stream_that_never_read_does_not_make_the_others_look_a_lifetime_old():
    failed = Coverage(stream="erc20", status=StreamStatus.PARTIAL.value, synced_at=NEVER)

    coverage, gaps = judge({"account": row("account", StreamStatus.COMPLETE), "erc20": failed}, "n")

    assert coverage.state == "partial" and coverage.synced_at == at(0)
    assert [g.code for g in gaps] == ["history_partial"]


def test_a_partly_read_history_is_a_gap():
    state = judge(
        {
            "account": row("account", StreamStatus.COMPLETE),
            "erc20": row("erc20", StreamStatus.PARTIAL),
        },
        "n",
    )

    assert state[0].state == "partial" and [g.code for g in state[1]] == ["history_partial"]
    assert state[0].streams == {"account": "complete", "erc20": "partial"}


def test_a_stream_no_source_provides_is_a_gap_but_the_read_is_not_partial():
    state = judge(
        {
            "account": row("account", StreamStatus.COMPLETE),
            "internal": row("internal", StreamStatus.UNAVAILABLE),
        },
        "n",
    )

    assert state[0].state == "complete" and [g.code for g in state[1]] == ["stream_unavailable"]


def test_a_history_that_disagrees_with_the_chain_is_a_gap():
    state = judge({"account": row("account", StreamStatus.COMPLETE, verified="mismatch")}, "n")

    assert [g.code for g in state[1]] == ["history_mismatch"]
    assert state[0].verified == "mismatch"


def test_a_fully_read_history_has_no_gap_and_keeps_the_notes_once():
    state = judge(
        {
            "account": row("account", StreamStatus.COMPLETE, notes=("note one",), verified="ok"),
            "erc20": row("erc20", StreamStatus.COMPLETE, notes=("note one", "note two")),
        },
        "n",
    )

    assert state[1] == [] and state[0].state == "complete" and state[0].verified == "ok"
    assert state[0].notes == ("note one", "note two")


def test_a_gap_in_a_counterpartys_history_shows_up_when_it_is_expanded():
    reader = FakeReader(
        [flow(A, ROOT)], states={A: (NodeCoverage("partial"), [judge({}, "x")[1][0]])}
    )
    graph, _ = build([], reader=reader, limits=GraphLimits(depth=2))

    assert reader.loaded == [ROOT, A]
    assert [g.code for g in graph.gaps] == ["history_not_read"]


# --- labels, warnings and bridges --------------------------------------------------------------


def test_nodes_carry_their_labels_and_a_stale_label_source_is_a_warning():
    graph, _ = build(
        [flow(A, ROOT)],
        labels=FakeLabels(
            label(A, Category.SANCTIONS, entity="Lazarus"), freshness=Freshness.STALE
        ),
    )

    body = graph.to_dict()
    node = next(n for n in body["nodes"] if n["address"].lower() == A)
    assert node["label"] == "Lazarus"
    assert node["labels"][0]["category"] == "SANCTIONS" and node["labels"][0]["confidence"] == 100
    assert any("ofac_sdn is stale" in w for w in graph.warnings)


def test_an_address_in_the_bridge_registry_is_marked_as_that_bridge():
    registry = BridgeRegistry.from_dict(
        {
            "bridges": [
                {
                    "name": "Example Bridge",
                    "contracts": [{"chain": "ethereum", "address": A, "role": "deposit"}],
                }
            ]
        }
    )
    graph, _ = build([flow(ROOT, A)], registry=registry)

    assert [n.bridge for n in graph.nodes] == [None, "Example Bridge"]


def test_requests_spent_reading_are_reported():
    reader = FakeReader([flow(A, ROOT)])
    reader.requests_used = 7

    graph, _ = build([], reader=reader)

    assert graph.requests_used == 7


def test_the_node_statistics_come_from_its_own_history_in_the_window():
    graph, _ = build(
        [flow(A, ROOT, when=at(1)), flow(B, ROOT, when=at(5)), flow(ROOT, C, when=at(3))]
    )

    stats = graph.nodes[0].stats
    assert (stats.transfers_in, stats.transfers_out) == (2, 1)
    assert (stats.counterparties_in, stats.counterparties_out) == (2, 1)
    assert (stats.first_seen, stats.last_seen) == (at(1), at(5))
    assert graph.nodes[1].stats is None  # a leaf was not read


@pytest.mark.parametrize("depth,expanded", [(1, 1), (2, 3)])
def test_only_addresses_nearer_than_the_depth_are_read(depth, expanded):
    reader = FakeReader([flow(A, ROOT), flow(ROOT, B)])

    build([], reader=reader, limits=GraphLimits(depth=depth))

    assert len(reader.loaded) == expanded


# --- ranking, ordering and scope, pinned down ---------------------------------------------------


def test_between_equally_busy_counterparties_the_more_recent_comes_first():
    older, newer = addr(21), addr(22)

    graph, _ = build([flow(older, ROOT, when=at(1)), flow(newer, ROOT, when=at(9))])

    assert ids(graph)[1:] == [newer, older]


def test_a_busier_counterparty_comes_before_a_more_recent_one():
    busy, recent = addr(21), addr(22)

    graph, _ = build(
        [flow(busy, ROOT, when=at(1)), flow(busy, ROOT, when=at(2)), flow(recent, ROOT, when=at(9))]
    )

    assert ids(graph)[1:] == [busy, recent]


def test_counterparties_left_out_of_one_node_but_reached_through_another_are_not_a_gap():
    # ROOT follows only A (B ranks below it), but A's busiest counterparty is B, so B is in the
    # graph after all; and A follows only B, but ROOT is already there.
    moves = [flow(A, ROOT) for _ in range(3)] + [flow(B, ROOT)] + [flow(A, B) for _ in range(5)]

    graph, _ = build(moves, limits=GraphLimits(depth=2, per_node=1))

    assert sorted(ids(graph)) == sorted([ROOT, A, B]) and graph.gaps == []


def test_a_label_source_that_was_never_fetched_is_a_warning_too():
    graph, _ = build([flow(A, ROOT)], labels=FakeLabels(freshness=Freshness.NEVER))

    assert any("is never" in warning for warning in graph.warnings)


def test_the_graph_states_the_window_it_covers():
    graph, _ = build([], limits=GraphLimits(window_days=30))

    assert graph.since == NOW - timedelta(days=30)
    assert graph.to_dict()["scope"]["since"] == (NOW - timedelta(days=30)).isoformat().replace(
        "+00:00", "Z"
    )


def test_when_edges_are_cut_the_busier_ones_between_the_same_depths_are_kept():
    quiet, busy = addr(31), addr(32)

    graph, _ = build(
        [flow(quiet, ROOT)] + [flow(busy, ROOT) for _ in range(4)], limits=GraphLimits(max_edges=1)
    )

    (edge,) = graph.edges
    assert edge.source.endswith(busy) and edge.count == 4


def test_when_edges_are_cut_those_touching_the_root_are_kept_before_those_between_others():
    # A and B are followed from the root; C only through A. Of the links between them the root's
    # own come first, even to C (depth 2) before a link between A and B (both depth 1).
    moves = (
        [flow(A, ROOT) for _ in range(5)]
        + [flow(B, ROOT) for _ in range(4)]
        + [flow(C, ROOT)]
        + [flow(C, A) for _ in range(3)]
        + [flow(A, B)]
    )

    graph, _ = build(moves, limits=GraphLimits(depth=2, per_node=2, max_edges=3))

    kept = {(e.source[-42:], e.target[-42:]) for e in graph.edges}
    assert (C, ROOT) in kept and (A, B) not in kept


def test_an_edge_is_exact_only_if_every_movement_on_it_is():
    allocated = Basis.ALLOCATED
    mixed, _ = build([flow(A, ROOT, 1), flow(A, ROOT, 2, basis=allocated)])
    exact, _ = build([flow(A, ROOT, 1), flow(A, ROOT, 2)])

    assert mixed.edges[0].basis is allocated and exact.edges[0].basis is Basis.EXACT


def test_a_history_that_was_cut_off_by_the_providers_window_is_partial_too():
    state = judge({"account": row("account", StreamStatus.TRUNCATED)}, "n")

    assert state[0].state == "partial" and [g.code for g in state[1]] == ["history_partial"]
