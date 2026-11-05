"""The TRON adapter over the in-memory TronGrid: sources, the settled height, cursors, details,
and whole histories read through the fetcher."""

from __future__ import annotations

import pytest

from app.chains.cursors import InvalidCursor
from app.chains.models import Confirmation, DataQuality, TransferType, TxStatus
from app.chains.tron.chains import TRON_CHAIN
from app.chains.tron.normalize import hex_to_base58
from app.ingestion.fetcher import fetch_history
from app.providers.base import AllProvidersFailed
from app.providers.budget import RequestBudget
from tests.fake_trongrid import ALICE, BOB, CAROL, HEAD, T0, internal_row, trc20_row, trx_row
from tests.helpers import load_fixture, run
from tests.rigs import Rig

FEEDS = {"transactions", "internal-transactions", "transactions/trc20"}


def feeds_read(rig: Rig) -> set[str]:
    """Which address feeds were asked for."""
    prefix = f"/v1/accounts/{ALICE}/"
    return {path.removeprefix(prefix) for path, _ in rig.trongrid.calls if path.startswith(prefix)}


# --- sources and heights -----------------------------------------------------------------------


def test_tron_is_read_through_trongrid_and_only_when_it_has_a_key():
    assert Rig().tron_adapter().source_names == ["trongrid"]
    assert Rig(trongrid_key=None).tron_adapter().source_names == []


def test_a_read_started_now_is_complete_through_the_solidified_block_not_the_head():
    adapter = Rig().tron_adapter()
    assert run(adapter.get_block_height()) == HEAD
    assert run(adapter.get_heights()) == (HEAD, HEAD - TRON_CHAIN.confirmation_depth)


def test_the_settled_height_never_goes_below_zero():
    rig = Rig()
    rig.trongrid.head = 7
    assert run(rig.tron_adapter().get_heights()) == (7, 0)


def test_a_rejected_key_fails_the_height_lookup_naming_the_source():
    rig = Rig()
    rig.trongrid.key = "a-different-key-entirely"
    with pytest.raises(AllProvidersFailed) as info:
        run(rig.tron_adapter().get_heights())
    assert [name for name, _ in info.value.failures] == ["trongrid"]


# --- cursors -----------------------------------------------------------------------------------


def test_a_page_cursor_names_its_source_and_leads_to_the_next_page():
    rig = Rig()
    rows = [trx_row(T0 + i * 3000, ALICE, BOB, amount=i + 1) for i in range(5)]
    rig.trongrid.add("transactions", *rows)
    adapter = rig.tron_adapter()

    async def scenario():
        first = await adapter.get_account_page(ALICE, limit=3)
        second = await adapter.get_account_page(ALICE, cursor=first.next_cursor, limit=3)
        return first, second

    first, second = run(scenario())

    assert first.next_cursor == "trongrid|fp3" and second.next_cursor is None
    amounts = [t.amount_raw for t in first.native_transfers + second.native_transfers]
    assert amounts == ["1", "2", "3", "4", "5"]


def test_a_cursor_from_somewhere_else_is_refused_before_any_request():
    rig = Rig()
    adapter = rig.tron_adapter()
    for cursor in ("etherscan|5:0", "nonsense", "|", "trongrid|has space"):
        with pytest.raises(InvalidCursor):
            run(adapter.get_account_page(ALICE, cursor=cursor))
    assert rig.trongrid.requests == []


# --- the kinds of transfer ---------------------------------------------------------------------


def test_each_kind_of_transfer_has_its_own_stream():
    rig = Rig()
    rig.trongrid.add("transactions", trx_row(T0, ALICE, BOB, amount=9))
    rig.trongrid.add("internal-transactions", internal_row(T0 + 1000, CAROL, ALICE, sun=4))
    rig.trongrid.add("transactions/trc20", trc20_row(T0 + 2000, BOB, ALICE, value=3))
    adapter = rig.tron_adapter()

    native = run(adapter.get_transfers(ALICE, TransferType.NATIVE))
    internal = run(adapter.get_transfers(ALICE, TransferType.INTERNAL))
    tokens = run(adapter.get_token_transfers(ALICE))
    activity = run(adapter.get_address_activity(ALICE))

    assert [t.amount_raw for t in native.items] == ["9"]
    assert [t.amount_raw for t in internal.items] == ["4"]
    assert [(t.transfer_type, t.amount_raw) for t in tokens.items] == [(TransferType.TRC20, "3")]
    assert len(activity.items) == 1 and activity.items[0].transaction_hash


@pytest.mark.parametrize("kind", [TransferType.ERC20, TransferType.TRC10])
def test_a_kind_tron_has_no_stream_for_is_refused_clearly_and_costs_nothing(kind):
    rig = Rig()
    with pytest.raises(ValueError, match=f"tron has no {kind.value} transfers"):
        run(rig.tron_adapter().get_transfers(ALICE, kind))
    assert rig.trongrid.requests == []


def test_trc10_transfers_come_with_the_native_stream_and_say_what_they_are():
    rig = Rig()
    row = load_fixture("tron/feed_trc10")["row"]
    owner = row["raw_data"]["contract"][0]["parameter"]["value"]["owner_address"]
    rig.trongrid.add("transactions", row)

    page = run(rig.tron_adapter().get_transfers(hex_to_base58(owner), TransferType.NATIVE))

    (transfer,) = page.items
    assert transfer.transfer_type is TransferType.TRC10 and transfer.asset.asset_id


# --- one transaction ---------------------------------------------------------------------------


def stock(rig, name, *, blocks_after=40):
    data = load_fixture(f"tron/{name}")
    txid = data["info"]["id"]
    rig.trongrid.transactions[txid] = data["tx"]
    rig.trongrid.infos[txid] = data["info"]
    rig.trongrid.head = data["info"]["blockNumber"] + blocks_after
    return txid, data


def test_a_real_usdt_transfer_is_decoded_with_confirmations_and_the_block_time():
    rig = Rig()
    txid, data = stock(rig, "detail_trc20")

    detail = run(rig.tron_adapter().get_transaction(txid))

    assert detail.transaction.status is TxStatus.SUCCESS
    assert detail.transaction.provider == "trongrid"
    assert detail.transaction.data_quality is DataQuality.DECODED
    assert detail.transaction.confirmations == 41
    assert detail.transaction.confirmation_status is Confirmation.CONFIRMED
    assert int(detail.transaction.timestamp.timestamp() * 1000) == data["info"]["blockTimeStamp"]
    (transfer,) = detail.transfers
    assert transfer.transfer_type is TransferType.TRC20 and transfer.log_index == 0
    assert any("symbols and decimals" in note for note in detail.coverage)


def test_a_transaction_a_few_blocks_old_is_not_yet_confirmed():
    rig = Rig()
    txid, _ = stock(rig, "detail_trx", blocks_after=5)

    detail = run(rig.tron_adapter().get_transaction(txid))

    assert detail.transaction.confirmations == 6
    assert detail.transaction.confirmation_status is Confirmation.UNCONFIRMED


def test_a_real_trx_transfer_has_one_movement_and_nothing_to_caveat():
    rig = Rig()
    txid, data = stock(rig, "detail_trx")

    detail = run(rig.tron_adapter().get_transaction(txid))

    (transfer,) = detail.transfers
    assert transfer.transfer_type is TransferType.NATIVE
    assert detail.transaction.fee_raw == str(data["info"]["fee"])
    assert detail.coverage == ()


def test_a_real_swap_lists_the_token_events_and_the_internal_trx_in_one_lookup():
    rig = Rig()
    txid, _ = stock(rig, "detail_internal")

    detail = run(rig.tron_adapter().get_transaction(txid))

    kinds = {t.transfer_type for t in detail.transfers}
    assert kinds == {TransferType.TRC20, TransferType.INTERNAL}
    assert len({t.identity for t in detail.transfers}) == len(detail.transfers)
    assert [path for path, _ in rig.trongrid.calls].count("/wallet/gettransactionbyid") == 1


def test_events_in_an_unexpected_shape_are_counted_in_the_coverage_notes():
    rig = Rig()
    txid, data = stock(rig, "detail_trc20")
    log = data["info"]["log"][0]
    rig.trongrid.infos[txid] = {**data["info"], "log": [{**log, "data": "00ff"}, log]}

    detail = run(rig.tron_adapter().get_transaction(txid))

    assert len(detail.transfers) == 1
    assert "1 Transfer events in a non-TRC-20 shape were not read" in detail.coverage


def test_a_trc10_transfer_says_its_token_name_and_decimals_are_not_in_the_details():
    rig = Rig()
    row = load_fixture("tron/feed_trc10")["row"]
    txid = row["txID"]
    rig.trongrid.transactions[txid] = {"txID": txid, "ret": row["ret"], "raw_data": row["raw_data"]}
    rig.trongrid.infos[txid] = {
        "id": txid,
        "blockNumber": row["blockNumber"],
        "blockTimeStamp": row["block_timestamp"],
    }

    detail = run(rig.tron_adapter().get_transaction(txid))

    assert [t.transfer_type for t in detail.transfers] == [TransferType.TRC10]
    assert detail.coverage == (
        "TRC-10 token names and decimals are not in a transaction's details",
    )


def test_a_failed_transaction_is_recorded_as_failed_and_moved_nothing():
    rig = Rig()
    row = load_fixture("tron/feed_failed")["row"]
    txid = row["txID"]
    rig.trongrid.transactions[txid] = {
        "txID": txid,
        "ret": row["ret"],
        "raw_data": row["raw_data"],
    }
    rig.trongrid.infos[txid] = {
        "id": txid,
        "blockNumber": row["blockNumber"],
        "blockTimeStamp": row["block_timestamp"],
    }

    detail = run(rig.tron_adapter().get_transaction(txid))

    assert detail.transaction.status is TxStatus.FAILED and detail.transfers == ()


def test_the_id_may_be_typed_with_a_0x_prefix_or_in_capitals():
    rig = Rig()
    txid, _ = stock(rig, "detail_trx")
    adapter = rig.tron_adapter()

    assert run(adapter.get_transaction("0x" + txid.upper())).transaction.transaction_hash == txid
    assert run(adapter.get_transaction(f"  {txid}  ")).transaction.transaction_hash == txid


def test_an_unknown_transaction_is_none_and_a_bad_id_is_an_error_that_costs_nothing():
    rig = Rig()
    adapter = rig.tron_adapter()
    assert run(adapter.get_transaction("ab" * 32)) is None
    requests_so_far = len(rig.trongrid.requests)
    for bad in ("", "1234", "zz" * 32, "ab" * 33):
        with pytest.raises(ValueError, match="not a transaction id"):
            run(adapter.get_transaction(bad))
    assert len(rig.trongrid.requests) == requests_so_far


def test_a_transaction_that_is_not_in_a_block_yet_is_not_reported_as_decoded():
    rig = Rig()
    txid = "cd" * 32
    rig.trongrid.transactions[txid] = {"txID": txid, "raw_data": {}}  # known, no receipt yet
    assert run(rig.tron_adapter().get_transaction(txid)) is None


def test_not_found_is_not_claimed_when_the_source_could_not_answer():
    rig = Rig()
    rig.trongrid.key = "a-different-key-entirely"
    with pytest.raises(AllProvidersFailed):
        run(rig.tron_adapter().get_transaction("ab" * 32))


def test_confirmations_stay_unknown_when_only_the_height_lookup_fails():
    rig = Rig()
    txid, _ = stock(rig, "detail_trc20")
    rig.trongrid.head = None  # getnowblock now answers with something unreadable

    detail = run(rig.tron_adapter().get_transaction(txid))

    assert detail.transaction.confirmations is None
    assert detail.transaction.confirmation_status is Confirmation.UNKNOWN
    assert len(detail.transfers) == 1  # the lookup itself still worked


# --- whole histories through the fetcher -------------------------------------------------------


def seed(rig: Rig) -> None:
    """A small history in all three feeds; times order it, since token rows have no block."""
    rig.trongrid.add(
        "transactions",
        trx_row(T0, ALICE, BOB, amount=5),
        trx_row(T0 + 30_000, BOB, ALICE, amount=7),
    )
    rig.trongrid.add("internal-transactions", internal_row(T0 + 3000, CAROL, ALICE, sun=3))
    rig.trongrid.add(
        "transactions/trc20",
        trc20_row(T0 + 6000, BOB, ALICE, value=9),
        trc20_row(T0 + 15_000, ALICE, BOB, value=1),
    )


def test_the_three_feeds_are_merged_into_one_chronological_history():
    rig = Rig()
    seed(rig)

    history = run(fetch_history(rig.tron_adapter(), ALICE, page_size=2))

    assert [(t.transfer_type.value, t.amount_raw) for t in history.transfers] == [
        ("native", "5"),
        ("internal", "3"),
        ("trc20", "9"),
        ("trc20", "1"),
        ("native", "7"),
    ]
    assert len(history.transactions) == 2
    assert history.complete and history.coverage == [] and history.unavailable == []
    assert feeds_read(rig) == FEEDS


def test_one_transactions_movements_stay_together_coin_then_internal_then_tokens():
    rig = Rig()
    txid = "ab" * 32
    # The token and internal rows say when, not which block; the coin row says both. Whatever
    # their block numbers, they happened together and must read in the order they happen.
    rig.trongrid.add("transactions/trc20", trc20_row(T0, BOB, ALICE, value=9, transaction_id=txid))
    rig.trongrid.add("internal-transactions", internal_row(T0, BOB, ALICE, sun=3, tx_id=txid))
    rig.trongrid.add("transactions", trx_row(T0, ALICE, BOB, amount=5, txID=txid))
    rig.trongrid.add("transactions", trx_row(T0 + 3000, BOB, ALICE, amount=1))
    # Another transaction of the same block: nothing says which came first, but they must not mix.
    other = "cd" * 32
    rig.trongrid.add("transactions/trc20", trc20_row(T0, BOB, ALICE, value=2, transaction_id=other))
    rig.trongrid.add("transactions", trx_row(T0, BOB, ALICE, amount=4, txID=other))

    history = run(fetch_history(rig.tron_adapter(), ALICE))

    assert [
        (t.transaction_hash[:2], t.transfer_type.value, t.amount_raw) for t in history.transfers
    ] == [
        ("ab", "native", "5"),
        ("ab", "internal", "3"),
        ("ab", "trc20", "9"),
        ("cd", "native", "4"),
        ("cd", "trc20", "2"),
        (history.transfers[-1].transaction_hash[:2], "native", "1"),  # the next block's
    ]


def test_the_sync_point_is_the_solidified_block_so_an_update_cannot_miss_the_tail():
    rig = Rig()
    seed(rig)

    history = run(fetch_history(rig.tron_adapter(), ALICE))

    assert history.head_block == HEAD
    assert history.synced_through_block == HEAD - TRON_CHAIN.confirmation_depth
    # An update starts a reorg margin before that point and so covers the newest, unsettled blocks.
    assert TRON_CHAIN.incremental_start(history.synced_through_block) == HEAD - 20 - 60
    assert TRON_CHAIN.incremental_start(10) == 0


def test_a_requested_block_range_is_read_by_time_and_is_what_counts_as_synced():
    rig = Rig()
    seed(rig)
    rig.trongrid.block_times.update({100: T0 + 4000, 200: T0 + 20_000})

    history = run(fetch_history(rig.tron_adapter(), ALICE, from_block=100, to_block=200))

    assert [(t.transfer_type.value, t.amount_raw) for t in history.transfers] == [
        ("trc20", "9"),
        ("trc20", "1"),
    ]
    assert history.complete and history.synced_through_block == 200


def test_only_the_requested_kinds_are_read():
    rig = Rig()
    seed(rig)

    history = run(fetch_history(rig.tron_adapter(), ALICE, kinds=frozenset({TransferType.NATIVE})))

    assert {t.transfer_type for t in history.transfers} == {TransferType.NATIVE}
    assert feeds_read(rig) == {"transactions"}


def test_an_address_with_no_history_is_an_empty_complete_one():
    history = run(fetch_history(Rig().tron_adapter(), ALICE))
    assert history.transfers == [] and history.transactions == [] and history.complete


def test_a_limit_stops_the_read_and_resuming_finishes_it_without_overlap():
    rig = Rig()
    rig.trongrid.add(
        "transactions",
        *[trx_row(T0 + i * 3000, BOB, ALICE, amount=i + 1) for i in range(30)],
    )
    rig.trongrid.add(
        "transactions/trc20",
        *[trc20_row(T0 + i * 3000 + 1, BOB, ALICE, value=i + 1) for i in range(30)],
    )
    adapter = rig.tron_adapter()

    first = run(fetch_history(adapter, ALICE, page_size=5, max_items=12))
    assert not first.complete and first.synced_through_block is None
    assert set(first.resume) == {"account", "internal", "trc20"}
    assert first.resume["account"].startswith("trongrid|fp")
    assert first.resume["internal"] == "" and first.resume["trc20"] == ""

    second = run(fetch_history(adapter, ALICE, page_size=5, resume=first.resume))
    assert second.complete

    one, two = {t.identity for t in first.transfers}, {t.identity for t in second.transfers}
    assert len(one | two) == 60 and not one & two


def test_running_out_of_request_budget_ends_the_read_politely_and_resuming_finishes_it():
    rig = Rig()
    rig.trongrid.add(
        "transactions", *[trx_row(T0 + i * 3000, BOB, ALICE, amount=1) for i in range(30)]
    )

    starved = run(
        fetch_history(
            rig.tron_adapter(budget=RequestBudget(limit=3)), ALICE, page_size=5, max_items=1000
        )
    )
    assert not starved.complete and starved.resume["account"]
    assert "request budget" in " ".join(starved.coverage)

    rest = run(fetch_history(rig.tron_adapter(), ALICE, page_size=5, resume=starved.resume))
    assert rest.complete
    assert len({t.identity for t in starved.transfers + rest.transfers}) == 30


def test_the_same_event_in_two_feeds_is_kept_once_and_unreadable_rows_are_reported():
    rig = Rig()
    row = trx_row(T0, ALICE, BOB, amount=2)
    rig.trongrid.add("transactions", row, {**row, "txID": "not-an-id"})

    history = run(fetch_history(rig.tron_adapter(), ALICE))

    assert len(history.transfers) == 1
    assert history.coverage == ["1 rows from trongrid could not be read and were skipped"]


def test_a_provider_failure_while_reading_is_raised_not_hidden():
    rig = Rig()
    seed(rig)
    rig.trongrid.queued = [(401, {"Error": "Invalid api key"})] * 4
    with pytest.raises(AllProvidersFailed):
        run(fetch_history(rig.tron_adapter(), ALICE))


def test_the_summary_describes_a_tron_read_without_block_numbers_it_does_not_have():
    import json

    rig = Rig()
    seed(rig)
    history = run(fetch_history(rig.tron_adapter(), ALICE))

    summary = history.summary(latest=2)

    json.dumps(summary)
    assert summary["transfers_by_type"] == {"native": 2, "internal": 1, "trc20": 2}
    assert summary["providers"] == ["trongrid"] and summary["complete"] is True
    assert summary["synced_through_block"] == HEAD - 20
    assert [t["amount_raw"] for t in summary["latest"]] == ["1", "7"]
    assert summary["chain_id"] == TRON_CHAIN.chain_id
