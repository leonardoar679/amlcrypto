"""Exposure paths: routes value could have taken between the root and a risk-labelled address."""

from __future__ import annotations

from app.graph.bridges import BridgeRegistry
from app.graph.builder import build_graph
from app.graph.model import Basis, GraphLimits
from app.intel.model import Category
from tests.graph_helpers import NOW, ROOT, USDT, FakeLabels, FakeReader, addr, at, flow, label
from tests.helpers import run
from tests.rigs import ETHEREUM

RISKY = addr(900)
A, B, C = addr(10), addr(11), addr(12)


def exposures(flows, *, labels=None, depth=3, root=ROOT, registry=None):
    graph = run(
        build_graph(
            FakeReader(flows),
            labels or FakeLabels(label(RISKY, Category.SANCTIONS)),
            ETHEREUM,
            root,
            GraphLimits(depth=depth),
            now=NOW,
            registry=registry,
        )
    )
    return graph.exposures


def short(path):
    return [(hop.source[-42:], hop.target[-42:]) for hop in path.hops]


def test_a_risky_address_that_paid_the_root_is_a_direct_incoming_exposure():
    (path,) = exposures([flow(RISKY, ROOT, 7, at(2))], depth=1)

    assert path.direction == "incoming" and path.length == 1
    assert path.target == f"ethereum:{RISKY}" and short(path) == [(RISKY, ROOT)]
    assert path.categories == ("SANCTIONS",) and path.confidence == 100
    assert path.ordering_proven and path.all_exact and path.intermediaries == ()


def test_a_risky_address_the_root_paid_is_a_direct_outgoing_exposure():
    (path,) = exposures([flow(ROOT, RISKY, 7, at(2))], depth=1)

    assert path.direction == "outgoing" and short(path) == [(ROOT, RISKY)]


def test_a_two_hop_path_names_the_address_in_between():
    (path,) = exposures([flow(RISKY, A, 5, at(1)), flow(A, ROOT, 4, at(3))], depth=2)

    assert path.direction == "incoming" and path.length == 2
    assert short(path) == [(RISKY, A), (A, ROOT)]
    assert path.intermediaries == (f"ethereum:{A}",)
    assert path.gaps_seconds == (2 * 3600,)
    assert (path.first_at, path.last_at) == (at(1), at(3))
    assert path.transfers == 2


def test_value_cannot_leave_an_address_before_it_arrived():
    paths = exposures([flow(RISKY, A, 5, at(5)), flow(A, ROOT, 4, at(1))], depth=2)

    assert paths == []  # the money left A hours before the risky address paid it


def test_the_same_second_counts_as_after():
    (path,) = exposures([flow(RISKY, A, 5, at(1)), flow(A, ROOT, 4, at(1))], depth=2)

    assert path.length == 2 and path.gaps_seconds == (0,)


def test_the_path_uses_the_earliest_movement_that_fits_each_hop():
    moves = [
        flow(RISKY, A, 5, at(2), tx="0xfirst"),
        flow(RISKY, A, 5, at(8), tx="0xlater"),
        flow(A, ROOT, 4, at(1), tx="0xtoo_early"),
        flow(A, ROOT, 4, at(4), tx="0xfits"),
        flow(A, ROOT, 4, at(6), tx="0xafter"),
    ]

    (path,) = exposures(moves, depth=2)

    assert [hop.chosen.transaction for hop in path.hops] == ["0xfirst", "0xfits"]
    assert path.hops[0].edge_transfers == 2 and path.hops[0].edge_total == 10


def test_a_later_arrival_by_a_shorter_route_does_not_hide_an_earlier_one_by_a_longer_route():
    # RISKY -> A happens late (hour 100) but RISKY -> B -> A is early (hours 5 and 6), and only
    # the early arrival at A lets A's payment to the root (hour 50) follow.
    moves = [
        flow(RISKY, A, 5, at(100)),
        flow(RISKY, B, 5, at(5)),
        flow(B, A, 5, at(6)),
        flow(A, ROOT, 4, at(50)),
    ]

    (path,) = exposures(moves, depth=3)

    assert short(path) == [(RISKY, B), (B, A), (A, ROOT)]
    assert path.intermediaries == (f"ethereum:{B}", f"ethereum:{A}")


def test_the_shortest_path_is_the_one_reported():
    moves = [
        flow(RISKY, ROOT, 1, at(9)),
        flow(RISKY, A, 5, at(1)),
        flow(A, ROOT, 4, at(2)),
    ]

    (path,) = exposures(moves, depth=2)

    assert path.length == 1


def test_a_risk_label_on_the_root_itself_is_not_an_exposure_of_the_root():
    paths = exposures([flow(A, ROOT)], labels=FakeLabels(label(ROOT, Category.SANCTIONS)), depth=1)

    assert paths == []


def test_only_risk_categories_make_an_exposure_an_exchange_does_not():
    paths = exposures([flow(A, ROOT)], labels=FakeLabels(label(A, Category.EXCHANGE)), depth=1)

    assert paths == []


def test_a_risky_address_that_is_both_paid_by_and_paying_the_root_gives_a_path_each_way():
    paths = exposures([flow(RISKY, ROOT, 1, at(1)), flow(ROOT, RISKY, 1, at(2))], depth=1)

    assert sorted(p.direction for p in paths) == ["incoming", "outgoing"]


def test_the_most_value_a_path_could_carry_is_its_smallest_hop_when_one_asset_runs_through():
    (path,) = exposures([flow(RISKY, A, 50, at(1)), flow(A, ROOT, 30, at(2))], depth=2)

    assert path.same_asset and path.upper_bound_raw == "30"


def test_no_bound_is_given_when_the_asset_changes_on_the_way():
    moves = [flow(RISKY, A, 50, at(1)), flow(A, ROOT, 30, at(2), asset=USDT, event="erc20:x")]

    (path,) = exposures(moves, depth=2)

    assert not path.same_asset and path.upper_bound_raw is None


def test_a_movement_with_no_known_time_gives_a_path_whose_order_is_not_proven():
    (path,) = exposures([flow(RISKY, A, 5, timed=False), flow(A, ROOT, 4, at(3))], depth=2)

    assert path.length == 2 and not path.ordering_proven
    assert [hop.ordering_proven for hop in path.hops] == [False, True]


def test_a_timed_movement_is_preferred_to_an_untimed_one():
    moves = [flow(RISKY, ROOT, 5, timed=False), flow(RISKY, ROOT, 6, at(2))]

    (path,) = exposures(moves, depth=1)

    assert path.ordering_proven and path.hops[0].chosen.amount_raw == "6"


def test_a_bitcoin_style_allocated_link_makes_the_path_not_exact():
    moves = [flow(RISKY, A, 5, at(1), basis=Basis.ALLOCATED), flow(A, ROOT, 4, at(2))]

    (path,) = exposures(moves, depth=2)

    assert not path.all_exact


def test_a_path_through_a_labelled_bridge_says_so():
    labels = FakeLabels(label(RISKY, Category.SANCTIONS), label(A, Category.BRIDGE, entity="B"))

    (path,) = exposures([flow(RISKY, A, 5, at(1)), flow(A, ROOT, 4, at(2))], labels=labels, depth=2)

    assert path.contains_bridge


def test_a_path_through_a_registered_bridge_contract_says_so():
    registry = BridgeRegistry.from_dict(
        {
            "bridges": [
                {"name": "X", "contracts": [{"chain": "ethereum", "address": A, "role": "both"}]}
            ]
        }
    )

    (path,) = exposures(
        [flow(RISKY, A, 5, at(1)), flow(A, ROOT, 4, at(2))], depth=2, registry=registry
    )

    assert path.contains_bridge


def test_an_ordinary_intermediary_is_not_a_bridge():
    (path,) = exposures([flow(RISKY, A, 5, at(1)), flow(A, ROOT, 4, at(2))], depth=2)

    assert not path.contains_bridge


def test_every_risky_address_in_the_graph_gets_its_own_path_nearest_first():
    other = addr(901)
    labels = FakeLabels(
        label(RISKY, Category.SANCTIONS), label(other, Category.MIXER, confidence=80)
    )
    moves = [flow(RISKY, A, 5, at(1)), flow(A, ROOT, 4, at(2)), flow(other, ROOT, 3, at(3))]

    paths = exposures(moves, labels=labels, depth=2)

    assert [(p.target, p.length) for p in paths] == [
        (f"ethereum:{other}", 1),
        (f"ethereum:{RISKY}", 2),
    ]
    assert paths[0].categories == ("MIXER",) and paths[0].confidence == 80


def test_paths_are_part_of_the_graph_and_of_its_output():
    graph = run(
        build_graph(
            FakeReader([flow(RISKY, ROOT, 7, at(2))]),
            FakeLabels(label(RISKY, Category.SANCTIONS)),
            ETHEREUM,
            ROOT,
            GraphLimits(),
            now=NOW,
        )
    )

    body = graph.to_dict()["exposures"][0]
    assert body["direction"] == "incoming" and body["hops"] == 1
    assert body["path"][0]["transfer"]["amount_raw"] == "7" and body["label_confidence"] == 100
    assert body["risk_categories"] == ["SANCTIONS"] and body["ordering_proven"] is True


# --- pinned down by mutation checks -------------------------------------------------------------


def test_a_path_can_be_longer_than_the_depth_of_the_graph():
    # RISKY and ROOT are two apart in the graph, but the money took three hops to get there.
    moves = [flow(RISKY, A, 5, at(1)), flow(A, B, 5, at(2)), flow(B, ROOT, 4, at(3))]
    moves.append(flow(ROOT, A, 1, at(9)))  # what makes A a neighbour of the root

    (path,) = exposures(moves, depth=2)

    assert path.length == 3 and path.intermediaries == (f"ethereum:{A}", f"ethereum:{B}")


def test_the_path_counts_every_transfer_of_every_edge_on_it():
    moves = [flow(RISKY, A, 5, at(2)), flow(RISKY, A, 5, at(8))]
    moves += [flow(A, ROOT, 4, at(4)), flow(A, ROOT, 4, at(6)), flow(A, ROOT, 4, at(7))]

    (path,) = exposures(moves, depth=2)

    assert path.transfers == 5


def test_among_paths_of_the_same_length_the_surest_label_comes_first():
    sure, unsure = addr(902), addr(901)  # the surer one is NOT first in address order
    labels = FakeLabels(
        label(sure, Category.SANCTIONS, confidence=100), label(unsure, Category.SCAM, confidence=30)
    )
    moves = [flow(unsure, ROOT, 1, at(1)), flow(sure, ROOT, 1, at(2))]

    paths = exposures(moves, labels=labels, depth=1)

    assert [p.confidence for p in paths] == [100, 30]


def test_when_two_routes_arrive_together_the_first_in_address_order_is_reported():
    moves = [
        flow(RISKY, B, 5, at(1)),
        flow(RISKY, A, 5, at(1)),
        flow(A, ROOT, 4, at(3)),
        flow(B, ROOT, 4, at(3)),
    ]

    (path,) = exposures(moves, depth=2)

    assert path.intermediaries == (f"ethereum:{A}",)


def test_a_hop_without_a_known_time_leaves_the_gap_to_the_next_unknown():
    (path,) = exposures([flow(RISKY, A, 5, timed=False), flow(A, ROOT, 4, at(3))], depth=2)

    assert path.gaps_seconds == (None,)
    assert (path.first_at, path.last_at) == (at(3), at(3))
