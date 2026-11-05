"""What a sync decides to read, from the coverage it holds. These are plain functions of the stored
state and the clock, so they are checked directly, including at the edges."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.database.repositories.acquisition import (
    COMPLETE,
    PARTIAL,
    TRUNCATED,
    UNAVAILABLE,
    Coverage,
)
from app.ingestion.families import PassPlan, family_for
from tests.rigs import BITCOIN, ETHEREUM, SOLANA, Rig

NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
MAX_AGE = 300.0


def row(stream, *, age=10.0, status=COMPLETE, through=1_000, cursor=None, **changes) -> Coverage:
    return Coverage(
        stream=stream,
        status=status,
        synced_at=NOW - timedelta(seconds=age),
        synced_through_block=through,
        resume_cursor=cursor,
        head_block=through,
        **changes,
    )


def account_rows(**ages) -> dict[str, Coverage]:
    return {name: row(name, **ages) for name in ("account", "internal", "erc20")}


def plan(family, coverage, *, refresh=False, rebuild=False):
    return family.plan(coverage, NOW, max_age=MAX_AGE, refresh=refresh, rebuild=rebuild)


@pytest.fixture
def account():
    return family_for(Rig().adapter(ETHEREUM))


# --- account chains ----------------------------------------------------------------------------


def test_an_address_never_read_is_read_in_full(account):
    assert plan(account, {}) == ([PassPlan("full")], "never read")


def test_a_rebuild_of_an_account_chain_address_reads_in_full(account):
    assert plan(account, account_rows(), rebuild=True) == (
        [PassPlan("full")],
        "rebuild requested",
    )


def test_a_history_younger_than_the_window_needs_no_pass(account):
    plans, reason = plan(account, account_rows(age=MAX_AGE - 0.001))

    assert plans == [] and reason.startswith("fresh")


def test_a_history_exactly_as_old_as_the_window_is_updated(account):
    plans, reason = plan(account, account_rows(age=MAX_AGE))

    assert [p.kind for p in plans] == ["incremental"] and reason.startswith("older than")


def test_a_history_is_only_as_fresh_as_its_stalest_stream(account):
    rows = account_rows()
    rows["internal"] = row("internal", age=MAX_AGE + 1)

    plans, _ = plan(account, rows)

    assert [p.kind for p in plans] == ["incremental"]


def test_a_refresh_updates_a_fresh_history_and_says_why(account):
    plans, reason = plan(account, account_rows(age=1), refresh=True)

    assert [p.kind for p in plans] == ["incremental"] and reason == "refresh requested"


def test_a_stream_with_no_recorded_coverage_is_read_even_when_the_rest_is_fresh(account):
    rows = account_rows(age=1)
    del rows["internal"]

    plans, reason = plan(account, rows)

    assert plans[0] == PassPlan("resume", None, {"internal": ""})
    assert plans[1].resume == {"account": "", "erc20": ""}
    assert reason == "an earlier read was stopped before it finished"


def test_an_update_starts_before_the_stream_that_was_read_the_least_far(account):
    rows = account_rows()
    rows["account"] = row("account", through=5_000)
    rows["internal"] = row("internal", through=4_000)
    rows["erc20"] = row("erc20", through=6_000)

    plans, _ = plan(account, rows, refresh=True)

    (update,) = plans
    assert update.from_block == ETHEREUM.incremental_start(4_000)
    assert update.resume == {"account": "", "internal": "", "erc20": ""}


def test_a_stopped_stream_carries_on_from_its_cursor_and_the_others_are_updated(account):
    rows = account_rows(age=MAX_AGE + 1)
    rows["internal"] = row("internal", status=PARTIAL, through=None, cursor="etherscan|700:0")
    rows["erc20"] = row("erc20", status=PARTIAL, through=None, cursor="")

    resume, update = plan(account, rows)[0]

    assert resume == PassPlan("resume", None, {"internal": "etherscan|700:0", "erc20": ""})
    assert update.kind == "incremental" and update.resume == {"account": ""}


def test_a_stream_nobody_provides_is_asked_again_with_the_others(account):
    rows = account_rows(age=MAX_AGE + 1)
    rows["internal"] = row("internal", status=UNAVAILABLE)

    (update,) = plan(account, rows)[0]

    assert set(update.resume) == {"account", "internal", "erc20"}


def test_a_stream_whose_window_ran_out_is_updated_not_read_again_from_the_start(account):
    rows = account_rows(age=MAX_AGE + 1)
    rows["account"] = row("account", status=TRUNCATED, through=900)

    (update,) = plan(account, rows)[0]

    assert update.kind == "incremental" and update.from_block == ETHEREUM.incremental_start(900)


# --- chains read as whole transactions ---------------------------------------------------------


@pytest.fixture(params=["bitcoin", "solana"])
def family(request):
    rig = Rig()
    adapter = rig.bitcoin_adapter() if request.param == "bitcoin" else rig.solana_adapter()
    return family_for(adapter), (BITCOIN if request.param == "bitcoin" else SOLANA)


def only(item):
    return {"transactions": item}


def test_a_wallet_never_read_is_read_in_full(family):
    assert plan(family[0], {}) == ([PassPlan("full")], "never read")


def test_a_rebuild_reads_in_full_whatever_is_stored(family):
    plans, reason = plan(family[0], only(row("transactions")), rebuild=True)

    assert plans == [PassPlan("full")] and reason == "rebuild requested"


def test_the_window_ends_exactly_at_its_length(family):
    fresh, _ = plan(family[0], only(row("transactions", age=MAX_AGE - 0.001)))
    stale, _ = plan(family[0], only(row("transactions", age=MAX_AGE)))

    assert fresh == [] and [p.kind for p in stale] == ["incremental"]


def test_a_refresh_updates_a_fresh_history(family):
    plans, reason = plan(family[0], only(row("transactions", age=1)), refresh=True)

    assert [p.kind for p in plans] == ["incremental"] and reason == "refresh requested"


def test_a_complete_history_is_updated_from_a_little_before_where_it_ended(family):
    fam, chain = family

    (update,), _ = plan(fam, only(row("transactions", age=MAX_AGE, through=7_000)))

    assert update == PassPlan("incremental", chain.incremental_start(7_000))


def test_a_history_whose_older_part_was_never_read_is_updated_and_then_carried_on(family):
    fam, chain = family
    stored = row("transactions", age=MAX_AGE, status=PARTIAL, through=7_000, cursor="x|9")

    (update, resume), reason = plan(fam, only(stored))

    assert update == PassPlan("incremental", chain.incremental_start(7_000))
    assert resume == PassPlan("resume", None, {"transactions": "x|9"})
    assert reason == "the older part of the history was never read"


def test_a_half_read_history_with_no_frontier_is_carried_on_not_updated(family):
    stored = row("transactions", age=MAX_AGE, status=PARTIAL, through=None, cursor="x|9")

    plans, _ = plan(family[0], only(stored))

    assert plans == [PassPlan("resume", None, {"transactions": "x|9"})]


def test_a_stream_with_neither_frontier_nor_cursor_is_read_in_full(family):
    stored = replace(row("transactions", age=MAX_AGE), synced_through_block=None)

    plans, _ = plan(family[0], only(stored))

    assert plans == [PassPlan("full")]


# --- putting a history back together from what is stored ---------------------------------------


def test_a_stored_history_is_only_as_far_along_as_its_least_read_stream(committing_db):
    family = family_for(Rig().adapter(ETHEREUM))
    rows = account_rows()
    rows["account"] = row("account", through=5_000)
    rows["internal"] = row("internal", through=4_000)
    rows["erc20"] = row("erc20", through=6_000)

    with committing_db.read() as session:
        history = family.load(session, "0x" + "a1" * 20, rows)

    assert history.complete and history.synced_through_block == 4_000


def test_a_stream_that_was_stopped_has_no_frontier_and_says_where_to_carry_on(committing_db):
    family = family_for(Rig().adapter(ETHEREUM))
    rows = account_rows()
    rows["internal"] = row("internal", status=PARTIAL, through=777, cursor="etherscan|9:2")

    with committing_db.read() as session:
        history = family.load(session, "0x" + "a1" * 20, rows)

    state = history.streams["internal"]
    assert (state.synced_through, state.cursor) == (None, "etherscan|9:2")
    assert not history.complete and history.synced_through_block is None
    assert history.resume == {"internal": "etherscan|9:2"}


def test_what_a_solana_wallets_stored_transactions_cannot_show_is_said_afresh(committing_db):
    from app.chains.solana.models import Unattributed
    from app.chains.solana.normalize import solana_transaction
    from app.database.repositories.solana import save_solana_transactions
    from tests.helpers import load_fixture
    from tests.test_solana_normalize import WALLET

    item = solana_transaction(load_fixture("solana/hidden_payout_v1")["item"], SOLANA)
    odd = replace(item, unattributed=(Unattributed("sol", WALLET, None, None, "-250"),))
    family = family_for(Rig().solana_adapter())
    with committing_db.write() as session:
        save_solana_transactions(session, [odd], address=WALLET)
        session.commit()

    with committing_db.read() as session:
        history = family.load(session, WALLET, only(row("transactions")))

    assert any("changed this wallet's balance with no instruction" in n for n in history.coverage)
