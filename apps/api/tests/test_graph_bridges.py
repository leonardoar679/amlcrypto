"""The bridge registry and the conservative pairing of a deposit with a withdrawal."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app.graph.bridges import (
    BridgeEvent,
    BridgeRegistry,
    BridgeRegistryError,
    correlate,
    events_from_graph,
)
from app.graph.builder import build_graph
from app.graph.model import AssetRef, GraphLimits
from tests.graph_helpers import NOW, ROOT, FakeLabels, FakeReader, addr, at, flow
from tests.helpers import run
from tests.rigs import ETHEREUM

ETH_BRIDGE = addr(0xB1)
BSC_BRIDGE = addr(0xB2)
DEPOSITOR, RECIPIENT = addr(1), addr(2)
BSC_ETH = AssetRef("0x2170ed0880ac9a755fd29b2688956bd959f933f8", "ETH", 18)


def registry(**overrides) -> BridgeRegistry:
    bridge = {
        "name": "Example Bridge",
        "contracts": [
            {"chain": "ethereum", "address": ETH_BRIDGE, "role": "deposit"},
            {"chain": "bsc", "address": BSC_BRIDGE, "role": "withdrawal"},
        ],
        "tokens": [{"ethereum": "native", "bsc": BSC_ETH.key}],
        **overrides,
    }
    return BridgeRegistry.from_dict({"bridges": [bridge]})


def deposit(amount=10**18, hours=0.0, party=DEPOSITOR, tx="0xdep") -> BridgeEvent:
    return BridgeEvent(
        "Example Bridge", "ethereum", "deposit", party, AssetRef("native", "ETH", 18),
        amount, at(hours), tx,
    )  # fmt: skip


def withdrawal(amount=10**18 - 10**15, hours=0.5, party=RECIPIENT, tx="0xwd") -> BridgeEvent:
    return BridgeEvent("Example Bridge", "bsc", "withdrawal", party, BSC_ETH, amount, at(hours), tx)


# --- the registry ------------------------------------------------------------------------------


def test_without_a_registry_nothing_is_a_bridge():
    assert not BridgeRegistry() and BridgeRegistry().contract("ethereum", ETH_BRIDGE) is None


def test_a_contract_is_looked_up_by_chain_and_normalized_address():
    found = registry().contract("ethereum", ETH_BRIDGE)

    assert found is not None and found[0].name == "Example Bridge" and found[1] == "deposit"
    assert registry().contract("bsc", ETH_BRIDGE) is None


def test_addresses_are_validated_and_stored_lowercase():
    checksummed = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"

    loaded = registry(contracts=[{"chain": "ethereum", "address": checksummed, "role": "both"}])

    assert loaded.contract("ethereum", checksummed.lower()) is not None


def test_a_file_is_loaded(tmp_path):
    path = tmp_path / "bridges.json"
    path.write_text(json.dumps(contract_of()))

    assert BridgeRegistry.from_file(path).contract("ethereum", ETH_BRIDGE) is not None


def one(**fields):
    """A registry of one bridge with a valid contract, changed by `fields`."""
    contract = {"chain": "ethereum", "address": ETH_BRIDGE, "role": "both"}
    return {"bridges": [{"name": "B", "contracts": [contract], **fields}]}


def contract_of(**fields):
    contract = {"chain": "ethereum", "address": ETH_BRIDGE, "role": "deposit", **fields}
    return {"bridges": [{"name": "B", "contracts": [contract]}]}


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"bridges": "x"},
        {"bridges": [], "extra": 1},
        {"bridges": [{"contracts": []}]},
        {"bridges": [{"name": "B"}]},
        {"bridges": [{"name": "B", "colour": "red", "contracts": []}]},
        {"bridges": [{"name": "B", "contracts": "x"}]},
        {"bridges": [{"name": "B", "contracts": ["x"]}]},
        contract_of(address="nope"),
        contract_of(chain="narnia"),
        contract_of(role="thief"),
        one(max_delay_seconds=-1),
        one(amount_tolerance_bps=True),
        one(tokens=["x"]),
        one(tokens=[{"narnia": "x"}]),
    ],
)
def test_a_registry_that_makes_no_sense_is_refused_whole(data):
    with pytest.raises(BridgeRegistryError):
        BridgeRegistry.from_dict(data)


def test_one_contract_cannot_belong_to_two_bridges():
    one = {
        "name": "One",
        "contracts": [{"chain": "ethereum", "address": ETH_BRIDGE, "role": "both"}],
    }
    two = {**one, "name": "Two"}

    with pytest.raises(BridgeRegistryError, match="two bridges"):
        BridgeRegistry.from_dict({"bridges": [one, two]})


def test_an_unreadable_file_is_a_clear_error(tmp_path):
    with pytest.raises(BridgeRegistryError, match="cannot read"):
        BridgeRegistry.from_file(tmp_path / "missing.json")
    (tmp_path / "bad.json").write_text("{")
    with pytest.raises(BridgeRegistryError, match="cannot read"):
        BridgeRegistry.from_file(tmp_path / "bad.json")


# --- pairing -----------------------------------------------------------------------------------


def test_a_deposit_and_a_withdrawal_that_fit_are_paired_as_an_inference():
    reg = registry()

    result = correlate(reg, [deposit(), withdrawal()])

    (link,) = result.links
    assert link.inferred and link.method == "amount_and_time"
    assert link.delay_seconds == 1800 and link.fee_bps == 10  # 0.1 % fee
    assert (link.deposit.party, link.withdrawal.party) == (addr(1), addr(2))
    assert "not an attribution" in link.to_dict()["note"]
    assert result.ambiguous == ()


def test_the_withdrawal_must_come_after_the_deposit_and_within_the_delay():
    reg = registry(max_delay_seconds=600)

    assert correlate(reg, [deposit(hours=1), withdrawal(hours=0.5)]).links == ()  # before
    assert correlate(reg, [deposit(hours=0), withdrawal(hours=0.1)]).links != ()  # 6 minutes
    assert correlate(reg, [deposit(hours=0), withdrawal(hours=0.2)]).links == ()  # 12 minutes


def test_the_withdrawal_may_not_exceed_the_deposit_or_fall_short_by_more_than_the_tolerance():
    reg = registry(amount_tolerance_bps=50)

    assert correlate(reg, [deposit(), withdrawal(10**18 + 1)]).links == ()
    assert correlate(reg, [deposit(), withdrawal(10**18 * 996 // 1000)]).links != ()
    assert correlate(reg, [deposit(), withdrawal(10**18 * 994 // 1000)]).links == ()


def test_the_amounts_are_compared_in_whole_units_not_raw_units():
    # 100 USDT is 100_000_000 on a 6-decimal chain and 10**20 on an 18-decimal one.
    usdt_a, usdt_b = AssetRef("0xa", "USDT", 6), AssetRef("0xb", "USDT", 18)
    reg = registry(tokens=[{"ethereum": "0xa", "bsc": "0xb"}])
    dep = BridgeEvent(
        "Example Bridge", "ethereum", "deposit", addr(1), usdt_a, 100_000_000, at(0), "0xd"
    )
    wd = BridgeEvent("Example Bridge", "bsc", "withdrawal", addr(2), usdt_b, 10**20, at(0.1), "0xw")

    assert len(correlate(reg, [dep, wd]).links) == 1


def test_assets_the_registry_does_not_say_are_the_same_are_never_paired():
    other = BridgeEvent(
        "Example Bridge", "bsc", "withdrawal", addr(2), AssetRef("0xother", "ETH", 18),
        10**18, at(0.5), "0xw",
    )  # fmt: skip

    assert correlate(registry(), [deposit(), other]).links == ()


def test_an_asset_with_unknown_decimals_cannot_be_compared():
    unknown = BridgeEvent(
        "Example Bridge", "bsc", "withdrawal", addr(2), AssetRef(BSC_ETH.key, "ETH", None),
        10**18, at(0.5), "0xw",
    )  # fmt: skip

    assert correlate(registry(), [deposit(), unknown]).links == ()


def test_two_withdrawals_that_fit_one_deposit_are_ambiguous_and_make_no_link():
    result = correlate(
        registry(), [deposit(), withdrawal(tx="0xw1"), withdrawal(tx="0xw2", party=addr(3))]
    )

    assert result.links == ()
    ((dep, candidates),) = result.ambiguous
    assert dep.transaction == "0xdep" and {c.transaction for c in candidates} == {"0xw1", "0xw2"}


def test_two_deposits_that_fit_one_withdrawal_are_ambiguous_too():
    result = correlate(
        registry(), [deposit(tx="0xd1"), deposit(tx="0xd2", party=addr(3)), withdrawal()]
    )

    assert result.links == () and len(result.ambiguous) == 2


def test_two_identical_deposits_are_two_deposits_and_so_ambiguous():
    result = correlate(registry(), [deposit(), deposit(), withdrawal()])

    assert result.links == ()


def test_the_same_chain_is_not_a_crossing():
    same = BridgeEvent(
        "Example Bridge", "ethereum", "withdrawal", addr(2), AssetRef("native", "ETH", 18),
        10**18, at(0.5), "0xw",
    )  # fmt: skip

    assert correlate(registry(), [deposit(), same]).links == ()


# --- from a graph ------------------------------------------------------------------------------


def test_deposits_and_withdrawals_are_found_in_a_graphs_movements():
    reg = registry(
        contracts=[
            {"chain": "ethereum", "address": ETH_BRIDGE, "role": "deposit"},
            {"chain": "bsc", "address": BSC_BRIDGE, "role": "withdrawal"},
        ]
    )
    graph = run(
        build_graph(
            FakeReader([flow(ROOT, ETH_BRIDGE, 10**18, at(1), tx="0xdep")]),
            FakeLabels(),
            ETHEREUM,
            ROOT,
            GraphLimits(),
            now=NOW,
        )
    )

    (event,) = events_from_graph(reg, graph, "ethereum")

    assert (event.role, event.party, event.amount, event.transaction) == (
        "deposit",
        ROOT,
        10**18,
        "0xdep",
    )
    assert event.timestamp == at(1) and event.bridge == "Example Bridge"
    assert events_from_graph(BridgeRegistry(), graph, "ethereum") == []
    assert timedelta(0) == event.timestamp - at(1)


# --- pinned down by mutation checks -------------------------------------------------------------


def test_the_delay_may_be_nothing_and_may_be_exactly_the_most_the_bridge_allows():
    reg = registry(max_delay_seconds=600)

    assert correlate(reg, [deposit(hours=0), withdrawal(hours=0)]).links != ()
    assert correlate(reg, [deposit(hours=0), withdrawal(hours=600 / 3600)]).links != ()
    assert correlate(reg, [deposit(hours=0), withdrawal(hours=601 / 3600)]).links == ()
    assert correlate(reg, [deposit(hours=1), withdrawal(hours=1 - 1 / 3600)]).links == ()


def test_a_fee_of_exactly_the_tolerance_is_allowed():
    reg = registry(amount_tolerance_bps=50)

    assert len(correlate(reg, [deposit(), withdrawal(10**18 * 995 // 1000)]).links) == 1


def test_the_same_asset_is_recognised_whatever_its_letter_case():
    shouting = BridgeEvent(
        "Example Bridge", "bsc", "withdrawal", RECIPIENT,
        AssetRef(BSC_ETH.key.upper().replace("0X", "0x"), "ETH", 18), 10**18, at(0.5), "0xw",
    )  # fmt: skip

    assert len(correlate(registry(), [deposit(), shouting]).links) == 1


def test_an_asset_with_unknown_decimals_is_not_compared_as_if_it_had_none():
    # Read as whole units this would be exactly the deposit; with unknown decimals it is unknowable.
    unknown = BridgeEvent(
        "Example Bridge", "bsc", "withdrawal", RECIPIENT, AssetRef(BSC_ETH.key, "ETH", None),
        1, at(0.5), "0xw",
    )  # fmt: skip
    one_unit = BridgeEvent(
        "Example Bridge", "ethereum", "deposit", DEPOSITOR, AssetRef("native", "ETH", None),
        1, at(0), "0xd",
    )  # fmt: skip

    assert correlate(registry(), [one_unit, unknown]).links == ()


@pytest.mark.parametrize(
    "data",
    [
        one(max_delay_seconds=30 * 86_400 + 1),
        one(amount_tolerance_bps=10_001),
    ],
)
def test_the_delay_and_the_tolerance_have_ceilings(data):
    with pytest.raises(BridgeRegistryError):
        BridgeRegistry.from_dict(data)


def test_a_deposit_contract_is_not_a_source_of_withdrawals_nor_the_reverse():
    reg = registry()  # ETH_BRIDGE only takes deposits; BSC_BRIDGE only pays out
    graph = run(
        build_graph(
            FakeReader(
                [
                    flow(ROOT, ETH_BRIDGE, 10**18, at(1)),  # a deposit
                    flow(ETH_BRIDGE, ROOT, 10**18, at(2)),  # the deposit contract paying out
                ]
            ),
            FakeLabels(),
            ETHEREUM,
            ROOT,
            GraphLimits(),
            now=NOW,
        )
    )

    events = events_from_graph(reg, graph, "ethereum")

    assert [(e.role, e.party) for e in events] == [("deposit", ROOT)]
    again = events_from_graph(registry(), graph, "bsc")  # nothing on this chain is registered
    assert again == []


def test_a_withdrawal_contract_receiving_money_is_not_a_deposit():
    reg = registry()
    graph = run(
        build_graph(
            FakeReader(
                [
                    flow(ROOT, BSC_BRIDGE, 10**18, at(1)),  # into a contract that only pays out
                    flow(BSC_BRIDGE, ROOT, 10**18, at(2)),  # and out of it: a withdrawal
                ]
            ),
            FakeLabels(),
            ETHEREUM,
            ROOT,
            GraphLimits(),
            now=NOW,
        )
    )

    events = events_from_graph(reg, graph, "bsc")

    assert [(e.role, e.party) for e in events] == [("withdrawal", ROOT)]


def test_the_deposited_asset_is_recognised_whatever_its_letter_case():
    shouting = BridgeEvent(
        "Example Bridge", "ethereum", "deposit", DEPOSITOR, AssetRef("Native", "ETH", 18),
        10**18, at(0), "0xd",
    )  # fmt: skip

    assert len(correlate(registry(), [shouting, withdrawal()]).links) == 1
