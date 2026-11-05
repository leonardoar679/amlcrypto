"""Storing transfers and account-model transactions: identity, merging, loading, reorganisations."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.chains.evm.chains import get_evm_chain
from app.chains.evm.normalize import transaction_from_rpc, transfers_from_receipt
from app.chains.models import (
    Asset,
    Confirmation,
    DataQuality,
    Transaction,
    Transfer,
    TransferType,
    TxStatus,
)
from app.database.repositories.rows import SaveCounts
from app.database.repositories.transfers import (
    load_transaction_detail,
    load_transactions,
    load_transfers,
    remove_unseen,
    save_transactions,
    save_transfers,
)
from tests.helpers import load_fixture

ETH = "eip155:1"
ME = "0x" + "a1" * 20
OTHER = "0x" + "b2" * 20
THIRD = "0x" + "c3" * 20
T0 = datetime(2026, 11, 5, 12, 0, 0, 123_456, tzinfo=UTC)
FIXTURES = [
    f"{chain}_{kind}"
    for chain in ("ethereum", "bsc")
    for kind in ("native", "erc20_single", "erc20_multi", "failed", "nft", "creation")
]


def transfer(n: int = 1, **changes) -> Transfer:
    base = Transfer(
        chain_id=ETH,
        transaction_hash="0x" + f"{n:064x}",
        event_key="native",
        transfer_type=TransferType.NATIVE,
        block_number=100 + n,
        timestamp=T0,
        from_address=OTHER,
        to_address=ME,
        asset=Asset(symbol="ETH", contract_address=None, decimals=18, name="Ether"),
        amount_raw="1000",
        provider="etherscan",
        data_quality=DataQuality.INDEXED,
    )
    return replace(base, **changes)


def transaction(n: int = 1, **changes) -> Transaction:
    base = Transaction(
        chain_id=ETH,
        transaction_hash="0x" + f"{n:064x}",
        block_number=100 + n,
        timestamp=T0,
        from_address=OTHER,
        to_address=ME,
        created_contract=None,
        value_raw="1000",
        status=TxStatus.SUCCESS,
        provider="etherscan",
        data_quality=DataQuality.INDEXED,
    )
    return replace(base, **changes)


def save(db, *, transfers=(), transactions=()):
    with db.write() as session:
        counts = (
            save_transfers(session, transfers),
            save_transactions(session, transactions),
        )
        session.commit()
    return counts


def stored(db, address=ME, chain=ETH):
    with db.read() as session:
        return load_transfers(session, chain, address), load_transactions(session, chain, address)


# --- fidelity ----------------------------------------------------------------------------------


def test_every_field_of_a_transfer_comes_back_as_it_went_in(committing_db):
    everything = Transfer(
        chain_id="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
        transaction_hash="5" * 88,
        event_key="3.1#2",
        transfer_type=TransferType.SPL,
        block_number=412_345_678,
        timestamp=T0,
        from_address="W" * 44,
        to_address="X" * 44,
        asset=Asset(
            symbol="USDC", contract_address="M" * 44, decimals=6, name="USD Coin", asset_id="7"
        ),
        amount_raw=str(2**256 - 1),
        provider="helius",
        data_quality=DataQuality.DECODED,
        confirmation_status=Confirmation.CONFIRMED,
        confirmations=77,
        transaction_index=311,
        log_index=12,
        trace_id="3.1",
        source_account="S" * 44,
        destination_account="D" * 44,
        program_id="P" * 43,
        operation="transfer",
    )
    bare = transfer(2, from_address=None, to_address=ME, timestamp=None, block_number=None)
    bare = replace(bare, asset=Asset(symbol=None, contract_address=None, decimals=None))
    chain_fk = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
    save(committing_db, transfers=[everything, bare])

    assert stored(committing_db, "W" * 44, chain_fk)[0] == [everything]
    assert stored(committing_db, ME)[0] == [bare]


def test_a_transaction_comes_back_as_it_went_in(committing_db):
    full = transaction(
        1,
        created_contract="0x" + "d4" * 20,
        fee_raw="21000000000000",
        method_id="0xa9059cbb",
        function_name="transfer(address _to, uint256 _value)",
        is_contract_call=True,
        confirmation_status=Confirmation.CONFIRMED,
        confirmations=40,
        transaction_index=7,
        status=TxStatus.FAILED,
    )
    creation = transaction(2, to_address=None, created_contract=THIRD, from_address=ME)
    save(committing_db, transactions=[full, creation])

    assert sorted(stored(committing_db, ME)[1], key=lambda t: t.block_number) == [full, creation]
    assert stored(committing_db, THIRD)[1] == [creation]  # found by the contract it created


@pytest.mark.parametrize("name", FIXTURES)
def test_real_transactions_survive_a_round_trip(committing_db, name):
    data = load_fixture(f"evm/{name}")
    chain = get_evm_chain(data["chain"])
    when = datetime.fromtimestamp(int(data["block_timestamp"], 16), UTC)
    tx, receipt = data["tx"], data["receipt"]
    head = int(receipt["blockNumber"], 16) + 100
    record = transaction_from_rpc(tx, receipt, chain, when, "node", head_block=head)
    moves, _ = transfers_from_receipt(tx, receipt, chain, when, "node", head)

    save(committing_db, transfers=moves, transactions=[record])

    with committing_db.read() as session:
        loaded = load_transaction_detail(session, record.chain_id, record.transaction_hash)
    assert loaded is not None
    assert loaded[0] == record
    assert sorted(loaded[1], key=lambda t: t.event_key) == sorted(moves, key=lambda t: t.event_key)


def test_a_provider_cannot_make_a_write_fail_with_a_very_long_name(committing_db):
    hostile = transfer(
        1,
        asset=Asset(symbol="S" * 500, contract_address=None, decimals=1, name="N" * 5000),
        trace_id="t" * 900,
        operation="o" * 100,
    )
    save(committing_db, transfers=[hostile])

    (loaded,) = stored(committing_db)[0]
    assert len(loaded.asset.symbol) == 64 and len(loaded.asset.name) == 128
    assert len(loaded.trace_id) == 128 and len(loaded.operation) == 32


# --- identity and merging ------------------------------------------------------------------------


def test_the_same_movement_read_twice_is_stored_once(committing_db):
    first, second = (
        save(committing_db, transfers=[transfer(1), transfer(2)])[0],
        save(committing_db, transfers=[transfer(1), transfer(2)])[0],
    )

    assert first == SaveCounts(new=2) and second == SaveCounts(unchanged=2)
    assert len(stored(committing_db)[0]) == 2


def test_a_movement_is_identified_by_chain_transaction_and_event(committing_db):
    token = transfer(1, event_key="erc20:0xtoken:a:b:5", transfer_type=TransferType.ERC20)
    bsc = transfer(1, chain_id="eip155:56")
    save(committing_db, transfers=[transfer(1), token, bsc])

    assert len(stored(committing_db)[0]) == 2  # the native and the token event of one transaction
    assert len(stored(committing_db, ME, "eip155:56")[0]) == 1


def test_a_batch_that_repeats_an_identity_counts_it_once(committing_db):
    counts = save(committing_db, transfers=[transfer(1), transfer(1), transfer(1)])[0]

    assert counts == SaveCounts(new=1)


def test_a_read_that_knows_more_completes_the_stored_record(committing_db):
    save(committing_db, transfers=[transfer(1, timestamp=None, confirmations=None)])

    counts = save(committing_db, transfers=[transfer(1, confirmations=30, provider="alchemy")])[0]

    assert counts == SaveCounts(updated=1)
    (loaded,) = stored(committing_db)[0]
    assert loaded.timestamp == T0 and loaded.confirmations == 30 and loaded.provider == "alchemy"


def test_a_read_that_knows_less_does_not_erase_what_was_known(committing_db):
    save(committing_db, transfers=[transfer(1)])

    counts = save(
        committing_db,
        transfers=[transfer(1, timestamp=None, confirmations=None)],
    )[0]

    (loaded,) = stored(committing_db)[0]
    assert loaded.timestamp == T0
    assert counts == SaveCounts(unchanged=1)  # it taught nothing, so nothing was written


def test_a_record_decoded_from_the_chain_is_not_replaced_by_an_indexers_summary(committing_db):
    decoded = transfer(1, data_quality=DataQuality.DECODED, provider="node", amount_raw="1000")
    save(
        committing_db,
        transfers=[decoded],
        transactions=[transaction(1, data_quality=DataQuality.DECODED, provider="node")],
    )

    counts = save(
        committing_db,
        transfers=[transfer(1, amount_raw="999", provider="etherscan")],
        transactions=[transaction(1, provider="etherscan", value_raw="999")],
    )

    assert counts == (SaveCounts(unchanged=1), SaveCounts(unchanged=1))
    (loaded,) = stored(committing_db)[0]
    assert loaded == decoded
    assert stored(committing_db)[1][0].provider == "node"


def test_a_decoded_record_replaces_an_indexed_one(committing_db):
    save(committing_db, transfers=[transfer(1)])

    counts = save(
        committing_db, transfers=[transfer(1, data_quality=DataQuality.DECODED, provider="node")]
    )[0]

    assert counts == SaveCounts(updated=1)
    assert stored(committing_db)[0][0].data_quality is DataQuality.DECODED


# --- loading -----------------------------------------------------------------------------------


def test_an_address_history_is_every_movement_it_is_a_party_to(committing_db):
    save(
        committing_db,
        transfers=[
            transfer(1, from_address=OTHER, to_address=ME),
            transfer(2, from_address=ME, to_address=OTHER),
            transfer(3, from_address=OTHER, to_address=THIRD),
            transfer(4, from_address=ME, to_address=ME),
        ],
    )

    mine = stored(committing_db, ME)[0]
    assert sorted(t.block_number for t in mine) == [101, 102, 104]  # a self-transfer is one row
    assert sorted(t.block_number for t in stored(committing_db, THIRD)[0]) == [103]


def test_addresses_are_compared_exactly_not_ignoring_case(committing_db):
    base58 = "TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE"
    save(committing_db, transfers=[transfer(1, from_address=base58)])

    assert len(stored(committing_db, base58)[0]) == 1
    assert stored(committing_db, base58.lower())[0] == []


def test_a_transaction_that_is_not_stored_is_not_found(committing_db):
    with committing_db.read() as session:
        assert load_transaction_detail(session, ETH, "0x" + "f" * 64) is None


# --- reorganisations ---------------------------------------------------------------------------


def seen(*items: Transfer) -> set[tuple[str, str]]:
    return {(item.transaction_hash, item.event_key) for item in items}


def remove(db, **kwargs):
    arguments = {
        "from_block": 103,
        "transfer_types": {TransferType.NATIVE},
        "seen_transfers": set(),
        "transactions_read": True,
        "seen_transactions": set(),
    }
    arguments.update(kwargs)
    with db.write() as session:
        count = remove_unseen(session, ETH, ME, **arguments)
        session.commit()
    return count


def test_what_a_complete_reread_no_longer_shows_is_removed_from_the_stretch_it_covered(
    committing_db,
):
    keep, dropped, first, old = transfer(4), transfer(5), transfer(3), transfer(1)
    save(
        committing_db,
        transfers=[keep, dropped, first, old],
        transactions=[transaction(4), transaction(5), transaction(3), transaction(1)],
    )

    removed = remove(
        committing_db,
        seen_transfers=seen(keep),
        seen_transactions={keep.transaction_hash},
    )

    # Two transfers and two transactions: block 105, and block 103, the first block read again.
    assert removed == 4
    hashes = sorted(t.transaction_hash for t in stored(committing_db)[0])
    assert hashes == sorted([keep.transaction_hash, old.transaction_hash])  # `old` is before 103
    assert sorted(t.block_number for t in stored(committing_db)[1]) == [101, 104]


def test_only_the_kinds_that_were_read_can_be_declared_gone(committing_db):
    token = transfer(5, event_key="erc20:x:a:b:1", transfer_type=TransferType.ERC20)
    save(committing_db, transfers=[token, transfer(5)])

    removed = remove(committing_db, transactions_read=False)

    assert removed == 1  # the native one; the token stream was not read, so it proves nothing
    assert [t.transfer_type for t in stored(committing_db)[0]] == [TransferType.ERC20]


def test_nothing_is_removed_from_another_address_or_for_transactions_not_read(committing_db):
    elsewhere = transfer(5, from_address=THIRD, to_address=OTHER)
    save(committing_db, transfers=[elsewhere], transactions=[transaction(5)])

    removed = remove(committing_db, transactions_read=False)

    assert removed == 0
    assert len(stored(committing_db, OTHER)[0]) == 1 and len(stored(committing_db)[1]) == 1
