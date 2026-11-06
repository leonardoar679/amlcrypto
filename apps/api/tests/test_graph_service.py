"""The graph over real stored histories: from storage alone, and after bringing it up to date."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.chains.support import build_adapter
from app.database.connection import Sessions
from app.graph.builder import StoredLabels, build_graph
from app.graph.model import GraphLimits
from app.graph.reader import AcquiringReader
from app.graph.service import (
    GraphRequestError,
    graph_after_update,
    graph_from_storage,
    load_registry,
    resolve,
)
from app.ingestion.sync import SyncPolicy
from app.providers.base import ProviderUnavailable
from app.providers.budget import RequestBudget
from tests.fake_etherscan import ME, OTHER, internal_row, token_row, tx_row
from tests.helpers import run
from tests.rigs import Rig
from tests.test_sync_account import Clock, seeded, sync

# The fake Etherscan's blocks are timestamped November 2023.
NOW = datetime(2023, 12, 1, tzinfo=UTC)


def sessions(db) -> Sessions:
    return Sessions(db.read, db.write)


def stored(db, rig, address=ME, **limits):
    chain, normalized = resolve("ethereum", address)
    return run(
        graph_from_storage(
            sessions(db), rig.settings, chain, normalized, GraphLimits(**limits), now=NOW
        )
    )


def updated(db, rig, address=ME, **limits):
    chain, normalized = resolve("ethereum", address)
    return run(
        graph_after_update(
            sessions(db),
            rig.settings,
            rig.providers,
            chain,
            normalized,
            GraphLimits(**limits),
            now=NOW,
        )
    )


def requests(rig) -> int:
    return len(rig.etherscan.requests)


def edge(graph, source, target, asset="native"):
    wanted = (f"ethereum:{source}", f"ethereum:{target}")
    return next(e for e in graph.edges if (e.source, e.target) == wanted and e.asset.key == asset)


# --- from storage ------------------------------------------------------------------------------


def test_a_graph_from_storage_shows_what_a_sync_stored_and_asks_the_providers_nothing(
    committing_db,
):
    rig = seeded()
    sync(rig, committing_db, Clock())
    asked = requests(rig)

    graph = stored(committing_db, rig)

    assert requests(rig) == asked and graph.requests_used == 0
    assert [n.address for n in graph.nodes] == [ME, OTHER]
    # OTHER paid ME 7 (a transaction) and 3 (an internal call); ME paid OTHER 5.
    received = edge(graph, OTHER, ME)
    assert (received.count, received.total) == (2, 10)
    assert (edge(graph, ME, OTHER).count, edge(graph, ME, OTHER).total) == (1, 5)
    assert graph.complete, graph.gaps


def test_a_zero_value_token_event_is_not_a_link_and_is_counted_as_ignored(committing_db):
    rig = seeded()
    rig.etherscan.add("tokentx", token_row(14, OTHER, ME, value="0"))
    sync(rig, committing_db, Clock())

    graph = stored(committing_db, rig)

    assert graph.ignored.get("zero_value") == 1
    assert sum(e.count for e in graph.edges) == 5  # 2 + 1 native/internal, 1 + 1 token


def test_tokens_are_their_own_edges_with_the_token_as_the_asset(committing_db):
    rig = seeded()
    sync(rig, committing_db, Clock())

    graph = stored(committing_db, rig)

    token_edges = [e for e in graph.edges if e.asset.key != "native"]
    assert sorted((e.source[-40:], e.total) for e in token_edges) == [
        (ME[-40:], 1),
        (OTHER[-40:], 9),
    ]


def test_an_address_never_read_is_shown_alone_and_the_graph_says_it_is_incomplete(
    committing_db,
):
    graph = stored(committing_db, seeded())

    assert [n.address for n in graph.nodes] == [ME]
    (gap,) = graph.gaps
    assert gap.code == "history_not_read" and not graph.complete
    assert graph.nodes[0].coverage.state == "not_read"


def test_a_counterparty_that_was_never_read_is_a_gap_at_depth_two(committing_db):
    rig = seeded()
    sync(rig, committing_db, Clock())

    graph = stored(committing_db, rig, depth=2)

    assert [g.code for g in graph.gaps] == ["history_not_read"]
    assert graph.gaps[0].node == f"ethereum:{OTHER}"


def test_the_window_is_applied_to_the_stored_history(committing_db):
    rig = seeded()
    sync(rig, committing_db, Clock())

    chain, normalized = resolve("ethereum", ME)
    later = datetime(2026, 11, 5, tzinfo=UTC)  # three years on: nothing is within a year
    graph = run(
        graph_from_storage(
            sessions(committing_db), rig.settings, chain, normalized, GraphLimits(), now=later
        )
    )

    assert graph.edges == [] and graph.ignored.get("outside_window", 0) > 0
    assert graph.complete  # an empty window is not a gap: the question was about the last year


# --- after an update ---------------------------------------------------------------------------


def test_expanding_a_counterparty_reads_it_and_a_repeat_costs_nothing(committing_db):
    rig = seeded()

    first = updated(committing_db, rig, depth=2)
    spent = requests(rig)

    assert first.requests_used > 0 and spent > 0
    assert [n.expanded for n in first.nodes] == [True, True]
    assert first.complete, first.gaps

    again = updated(committing_db, rig, depth=2)

    assert requests(rig) == spent and again.requests_used == 0  # fresh data is reused
    assert [(e.source, e.target, e.total) for e in again.edges] == [
        (e.source, e.target, e.total) for e in first.edges
    ]


def test_a_graph_after_an_update_equals_the_graph_from_storage_afterwards(committing_db):
    rig = seeded()

    after = updated(committing_db, rig, depth=2)
    later = stored(committing_db, rig, depth=2)

    assert after.to_dict()["edges"] == later.to_dict()["edges"]
    assert [n.address for n in after.nodes] == [n.address for n in later.nodes]


def with_budget(db, rig, budget, *, refresh=False, **limits):
    chain, normalized = resolve("ethereum", ME)
    return run(
        graph_after_update(
            sessions(db),
            rig.settings,
            rig.providers,
            chain,
            normalized,
            GraphLimits(request_budget=budget, **limits),
            refresh=refresh,
            now=NOW,
        )
    )


def test_a_spent_budget_leaves_a_partial_history_that_the_graph_reports(committing_db):
    rig = seeded()

    graph = with_budget(committing_db, rig, 1, depth=2)

    assert graph.requests_used == 1
    assert [g.code for g in graph.gaps] == ["history_partial"] and not graph.complete
    assert graph.nodes[0].coverage.state == "partial"


def test_a_refresh_carries_on_from_where_the_budget_stopped(committing_db):
    rig = seeded()
    with_budget(committing_db, rig, 1)

    graph = with_budget(committing_db, rig, 100, refresh=True)

    assert graph.complete, graph.gaps
    assert {e.total for e in graph.edges if e.asset.key == "native"} == {5, 10}


def test_without_a_refresh_a_recent_partial_history_is_served_as_it_is(committing_db):
    rig = seeded()
    with_budget(committing_db, rig, 1)
    asked = requests(rig)

    graph = with_budget(committing_db, rig, 100)

    assert requests(rig) == asked and not graph.complete


def test_a_provider_that_fails_leaves_a_gap_and_the_stored_history_is_used(committing_db):
    rig = seeded()
    sync(rig, committing_db, Clock())  # ME is stored; OTHER is not
    chain, normalized = resolve("ethereum", ME)

    budget = RequestBudget(100)
    adapter = build_adapter(chain, rig.providers, budget=budget)

    async def down(*args, **kwargs):
        raise ProviderUnavailable("etherscan", "HTTP 503")

    adapter.get_account_page = down
    reader = AcquiringReader(
        sessions(committing_db),
        chain,
        adapter,
        budget,
        SyncPolicy(),
        None,
        since=datetime(2023, 1, 1, tzinfo=UTC),
        slug="ethereum",
    )
    graph = run(
        build_graph(
            reader,
            StoredLabels(sessions(committing_db), chain.chain_id, NOW),
            chain,
            normalized,
            GraphLimits(depth=2),
            now=NOW,
        )
    )

    codes = [g.code for g in graph.gaps]
    assert "acquisition_failed" in codes and not graph.complete
    assert graph.edges  # ME's stored history still shows
    assert "HTTP 503" not in " ".join(g.message for g in graph.gaps)  # no provider text leaks


# --- the request ------------------------------------------------------------------------------


def test_a_chain_and_address_are_resolved_to_their_storage_form():
    chain, normalized = resolve("eip155:1", "0x" + "A1" * 20)

    assert chain.slug == "ethereum" and normalized == "0x" + "a1" * 20


def test_an_unknown_chain_and_a_bad_address_are_told_apart():
    with pytest.raises(GraphRequestError) as unknown:
        resolve("dogecoin", ME)
    with pytest.raises(GraphRequestError) as bad:
        resolve("ethereum", "0x123")

    assert unknown.value.chain_unknown and not bad.value.chain_unknown


def test_updating_needs_a_provider_for_the_chain():
    rig = Rig(etherscan_key=None, alchemy_key=None)
    chain, normalized = resolve("ethereum", ME)

    with pytest.raises(GraphRequestError, match="ETHERSCAN_API_KEY"):
        run(
            graph_after_update(
                None, rig.settings, rig.providers, chain, normalized, GraphLimits(), now=NOW
            )
        )


def test_no_registry_file_means_no_bridges(tmp_path):
    settings = Rig().settings

    assert not load_registry(settings)
    assert not load_registry(settings.model_copy(update={"bridge_registry_file": None}))
    path = tmp_path / "b.json"
    path.write_text('{"bridges": []}')
    assert not load_registry(settings.model_copy(update={"bridge_registry_file": str(path)}))


def test_history_rows_used_by_the_fixtures_are_what_the_tests_assume():
    # The expectations above rest on seeded(): guard them against a change to the fixture.
    rig = seeded()
    assert len(rig.etherscan.rows["txlist"]) == 3 and len(rig.etherscan.rows["tokentx"]) == 2
    assert internal_row and token_row and tx_row  # imported for the reader of this file


# --- pinned down by mutation checks -------------------------------------------------------------


def test_a_spent_budget_means_the_address_is_not_updated_and_the_graph_says_so(committing_db):
    from app.graph.builder import StoredLabels, build_graph

    chain, normalized = resolve("ethereum", ME)
    budget = RequestBudget(5)
    budget.used = 5
    adapter = build_adapter(chain, seeded().providers, budget=budget)

    async def never(*args, **kwargs):
        raise AssertionError("a spent budget must not ask a provider")

    adapter.get_account_page = never
    reader = AcquiringReader(
        sessions(committing_db), chain, adapter, budget, SyncPolicy(), None,
        since=datetime(2023, 1, 1, tzinfo=UTC), slug="ethereum",
    )  # fmt: skip

    graph = run(
        build_graph(
            reader,
            StoredLabels(sessions(committing_db), chain.chain_id, NOW),
            chain,
            normalized,
            GraphLimits(),
            now=NOW,
        )
    )

    assert "request_budget" in {g.code for g in graph.gaps}
    assert reader.errors == {}


def test_a_failure_on_a_counterparty_is_a_gap_while_the_graph_is_still_returned(committing_db):
    rig = seeded()
    sync(rig, committing_db, Clock())  # ME is stored and fresh; OTHER is not
    rig.etherscan.queued = [ProviderUnavailable("etherscan", "HTTP 503")] * 40
    rig.alchemy.queued = [ProviderUnavailable("alchemy", "HTTP 503")] * 40

    graph = updated(committing_db, rig, depth=2)

    assert "acquisition_failed" in {g.code for g in graph.gaps}
    assert graph.edges and not graph.complete


def test_the_window_is_counted_from_now_in_whole_days(committing_db):
    from app.chains.models import StreamStatus
    from tests.graph_store import move, seed

    just_inside, just_outside = "0x" + "c1" * 20, "0x" + "c2" * 20
    with committing_db.write() as session:
        seed(
            session,
            ME,
            [move(just_inside, ME, days_ago=364.5), move(just_outside, ME, days_ago=365.5)],
            status=StreamStatus.COMPLETE,
        )
    chain, normalized = resolve("ethereum", ME)

    graph = run(
        graph_from_storage(
            sessions(committing_db), Rig().settings, chain, normalized, GraphLimits()
        )
    )

    assert [n.address for n in graph.nodes] == [ME, just_inside]
