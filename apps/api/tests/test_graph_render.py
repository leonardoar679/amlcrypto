"""The limits a request may set, the app's handling of a bad registry, and the text rendering."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.graph.bridges import BridgeRegistryError
from app.graph.builder import build_graph
from app.graph.model import Direction, GraphLimits
from app.graph.render import MAX_EDGES_SHOWN, render_graph
from app.intel.model import Category
from app.main import create_app
from tests.graph_helpers import NOW, ROOT, FakeLabels, FakeReader, addr, at, flow, label
from tests.helpers import run
from tests.rigs import ETHEREUM, SECRET

RISKY = addr(900)


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, environment="test", secret_key=SECRET, **overrides)


# --- the limits ---------------------------------------------------------------------------------


def test_the_configured_limits_are_the_defaults_of_a_request():
    limits = GraphLimits.from_settings(settings())

    assert limits == GraphLimits()  # blueprint section 3.1: 1 hop, 500 nodes, 2,000 edges, 25 ...
    assert (limits.depth, limits.window_days, limits.history_items) == (1, 365, 500)


def test_a_request_can_tighten_a_limit():
    limits = GraphLimits.from_settings(
        settings(), depth=2, max_nodes=50, max_edges=100, per_node=5, window_days=30
    )

    assert (limits.depth, limits.max_nodes, limits.max_edges) == (2, 50, 100)
    assert (limits.per_node, limits.window_days) == (5, 30)


def test_a_request_can_never_raise_a_limit_above_the_configured_one():
    limits = GraphLimits.from_settings(
        settings(),
        depth=6,
        max_nodes=10**6,
        max_edges=10**6,
        per_node=10**6,
        window_days=10**6,
        history_items=10**9,
    )

    assert limits.depth == 3  # GRAPH_MAX_DEPTH
    assert (limits.max_nodes, limits.max_edges, limits.per_node) == (500, 2000, 25)
    assert limits.window_days == 3650  # GRAPH_MAX_WINDOW_DAYS
    assert limits.history_items == 5000  # HISTORY_MAX_ITEMS


def test_a_deployment_can_set_its_own_ceilings():
    limits = GraphLimits.from_settings(
        settings(graph_max_nodes=100, graph_max_depth=2, graph_window_days=90),
        depth=5,
        max_nodes=300,
    )

    assert (limits.depth, limits.max_nodes, limits.window_days) == (2, 100, 90)


def test_a_default_depth_above_the_ceiling_is_brought_down_to_it():
    limits = GraphLimits.from_settings(settings(graph_default_depth=5, graph_max_depth=2))

    assert limits.depth == 2


def test_the_direction_is_taken_from_text_or_the_enum():
    assert GraphLimits.from_settings(settings(), direction="out").direction is Direction.OUT
    assert GraphLimits.from_settings(settings(), direction=Direction.IN).direction is Direction.IN
    with pytest.raises(ValueError):
        GraphLimits.from_settings(settings(), direction="sideways")


def test_a_blank_registry_file_means_none():
    assert settings(bridge_registry_file="").bridge_registry_file is None


def test_an_app_does_not_start_with_a_bridge_registry_it_cannot_read(tmp_path):
    broken = tmp_path / "bridges.json"
    broken.write_text("[]")

    with pytest.raises(BridgeRegistryError):
        create_app(settings(bridge_registry_file=str(broken), database_url="sqlite:///:memory:"))


# --- the text -----------------------------------------------------------------------------------


def render(flows, *, labels=None, limits=None, reader=None):
    graph = run(
        build_graph(
            reader or FakeReader(flows),
            labels or FakeLabels(),
            ETHEREUM,
            ROOT,
            limits or GraphLimits(),
            now=NOW,
        )
    )
    return render_graph(graph), graph


def test_a_complete_graph_says_so_and_names_its_scope():
    text, _ = render([flow(addr(5), ROOT, 5 * 10**17, at(2))])

    assert "complete within that scope" in text and "depth 1, both" in text
    assert "365 days" in text and "0.5 ETH" in text and "Addresses:" in text


def test_an_incomplete_graph_lists_each_thing_it_leaves_out():
    flows = [flow(addr(100 + i), ROOT) for i in range(5)]

    text, graph = render(flows, limits=GraphLimits(per_node=2))

    assert "INCOMPLETE" in text and graph.gaps[0].message in text


def test_exposures_are_listed_with_their_hops_and_caveats():
    text, _ = render(
        [flow(RISKY, ROOT, 10**18, at(3))], labels=FakeLabels(label(RISKY, Category.SANCTIONS))
    )

    assert "Exposure" in text and "not proof" in text
    assert "SANCTIONS (100%)" in text and "paid the root" in text and "1 hop(s)" in text


def test_a_path_whose_order_is_unproven_says_so():
    text, _ = render(
        [flow(RISKY, ROOT, 10**18, timed=False)], labels=FakeLabels(label(RISKY, Category.SCAM))
    )

    assert "ORDER NOT PROVEN" in text


def test_patterns_are_listed_as_indicators():
    flows = [flow(addr(100 + i), ROOT, 1, at(i * 0.01)) for i in range(12)]

    text, _ = render(flows, limits=GraphLimits(per_node=25))

    assert "Patterns (indicators, not conclusions)" in text
    assert "paid by 12 different addresses within 24 h (12 transfers" in text


def test_a_long_graph_is_cut_off_in_the_text_and_says_how_much_more():
    flows = [flow(addr(1000 + i), ROOT) for i in range(60)]

    text, _ = render(flows, limits=GraphLimits(per_node=60))

    assert "... 21 more" in text  # 61 addresses, 40 shown
    assert text.count(" -> ") <= MAX_EDGES_SHOWN + 1


def test_token_names_are_printed_safely():
    from app.graph.model import AssetRef

    odd = AssetRef("0xtoken", "USDТ", 6)  # a Cyrillic letter that looks like a Latin one

    text, _ = render([flow(addr(5), ROOT, 5, at(1), asset=odd, event="erc20:x")])

    assert "USD\\u0422" in text


# --- pinned down by mutation checks -------------------------------------------------------------


def test_a_limit_of_nothing_or_less_is_one_not_zero():
    limits = GraphLimits.from_settings(settings(), depth=0, max_nodes=-5, per_node=0)

    assert (limits.depth, limits.max_nodes, limits.per_node) == (1, 1, 1)


def test_the_age_of_a_history_is_noted_only_past_a_day():
    from datetime import timedelta

    from app.graph.model import NodeCoverage
    from app.graph.reader import stale_note

    read = NodeCoverage("complete", synced_at=NOW - timedelta(hours=23))
    old = NodeCoverage("complete", synced_at=NOW - timedelta(days=3))

    assert stale_note(read, "n", NOW) is None
    assert stale_note(NodeCoverage("not_read"), "n", NOW) is None
    assert "last read 3 days ago" in stale_note(old, "n", NOW)
