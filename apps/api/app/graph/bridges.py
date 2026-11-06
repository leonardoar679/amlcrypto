"""Cross-chain bridges: a registry of bridge contracts and a conservative way to pair a deposit on
one chain with a withdrawal on another (blueprint section 6.5).

THIS PLATFORM SHIPS NO BRIDGE DATA. Contract addresses, token mappings and event formats must come
from the bridges' own documentation and be checked by whoever loads them; guessing them would
produce confident, wrong cross-chain links. Until a registry file is configured
(`BRIDGE_REGISTRY_FILE`) no address is a bridge and nothing is correlated.

A pairing is an INFERENCE and never an identity: whoever deposited and whoever withdrew may be
different people. The only method here is `amount_and_time`: the same token (by the registry's own
mapping), a withdrawal after the deposit within the bridge's delay, for the deposited amount less a
fee within a tolerance, and nothing else that fits. Where more than one deposit or withdrawal fits,
no link is made and the candidates are reported as ambiguous.

Registry file:

    {"bridges": [{
        "name": "Example Bridge",
        "max_delay_seconds": 3600,          # optional, default 3600
        "amount_tolerance_bps": 50,         # optional, default 50 (0.5 % in fees)
        "contracts": [
            {"chain": "ethereum", "address": "0x...", "role": "deposit"},
            {"chain": "bsc", "address": "0x...", "role": "withdrawal"}    # or "both"
        ],
        "tokens": [{"ethereum": "native", "bsc": "0x..."}]  # the same asset on each chain
    }]}
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

from app.chains.registry import ChainSpec, get_chain
from app.graph.model import AssetRef, Flow, Graph

DEPOSIT, WITHDRAWAL, BOTH = "deposit", "withdrawal", "both"
METHOD = "amount_and_time"


class BridgeRegistryError(ValueError):
    """The registry file is not usable; nothing from it is loaded."""


@dataclass(frozen=True, slots=True)
class Bridge:
    name: str
    max_delay_seconds: int
    amount_tolerance_bps: int
    contracts: dict[tuple[str, str], str]  # (chain slug, address) -> role
    tokens: tuple[dict[str, str], ...]  # each maps chain slug -> asset key


@dataclass(frozen=True, slots=True)
class BridgeEvent:
    bridge: str
    chain: str
    role: str  # "deposit" or "withdrawal"
    party: str  # who paid in, or who was paid out
    asset: AssetRef
    amount: int
    timestamp: datetime
    transaction: str


@dataclass(frozen=True, slots=True)
class BridgeLink:
    bridge: str
    deposit: BridgeEvent
    withdrawal: BridgeEvent
    delay_seconds: int
    fee_bps: int
    method: str = METHOD
    inferred: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "bridge": self.bridge,
            "method": self.method,
            "inferred": self.inferred,
            "deposit": _event(self.deposit),
            "withdrawal": _event(self.withdrawal),
            "delay_seconds": self.delay_seconds,
            "fee_bps": self.fee_bps,
            "note": "An inferred relationship, not an attribution: the depositor and the "
            "recipient may be different people.",
        }


def _event(event: BridgeEvent) -> dict[str, Any]:
    return {
        "chain": event.chain,
        "party": event.party,
        "asset": event.asset.symbol or event.asset.key,
        "amount_raw": str(event.amount),
        "timestamp": event.timestamp.isoformat(),
        "transaction_hash": event.transaction,
    }


@dataclass(frozen=True, slots=True)
class Correlation:
    links: tuple[BridgeLink, ...]
    ambiguous: tuple[tuple[BridgeEvent, tuple[BridgeEvent, ...]], ...]  # a deposit, what fits it


class BridgeRegistry:
    def __init__(self, bridges: Iterable[Bridge] = ()) -> None:
        self.bridges = tuple(bridges)
        self._by_contract: dict[tuple[str, str], tuple[Bridge, str]] = {}
        for bridge in self.bridges:
            for key, role in bridge.contracts.items():
                if key in self._by_contract:
                    raise BridgeRegistryError(f"{key[1]} on {key[0]} is in two bridges")
                self._by_contract[key] = (bridge, role)

    def __bool__(self) -> bool:
        return bool(self.bridges)

    def contract(self, chain_slug: str, address: str) -> tuple[Bridge, str] | None:
        return self._by_contract.get((chain_slug, address))

    @classmethod
    def from_file(cls, path: str | Path) -> BridgeRegistry:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise BridgeRegistryError(f"cannot read the bridge registry: {error}") from error
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Any) -> BridgeRegistry:
        if (
            not isinstance(data, dict)
            or set(data) != {"bridges"}
            or not isinstance(data["bridges"], list)
        ):
            raise BridgeRegistryError('the registry must be {"bridges": [...]}')
        return cls(_bridge(item) for item in data["bridges"])


def _bridge(item: Any) -> Bridge:
    allowed = {"name", "max_delay_seconds", "amount_tolerance_bps", "contracts", "tokens"}
    if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
        raise BridgeRegistryError("every bridge needs a name")
    name = item["name"]
    if set(item) - allowed:
        raise BridgeRegistryError(f"{name}: unknown field(s) {sorted(set(item) - allowed)}")
    delay = item.get("max_delay_seconds", 3600)
    tolerance = item.get("amount_tolerance_bps", 50)
    for field_name, value, ceiling in (
        ("max_delay_seconds", delay, 30 * 86_400),
        ("amount_tolerance_bps", tolerance, 10_000),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= ceiling:
            raise BridgeRegistryError(
                f"{name}: {field_name} must be a whole number up to {ceiling}"
            )

    listed, grouped = item.get("contracts") or [], item.get("tokens") or []
    if not isinstance(listed, list) or not isinstance(grouped, list):
        raise BridgeRegistryError(f"{name}: contracts and tokens must be lists")
    contracts: dict[tuple[str, str], str] = {}
    for contract in listed:
        if not isinstance(contract, dict):
            raise BridgeRegistryError(f"{name}: every contract must be an object")
        chain_slug, role = contract.get("chain"), contract.get("role")
        if role not in (DEPOSIT, WITHDRAWAL, BOTH) or not isinstance(chain_slug, str):
            raise BridgeRegistryError(f"{name}: a contract needs a chain and a role")
        spec = _spec(name, chain_slug)
        check = spec.validator.check(str(contract.get("address")))
        if not check.valid or check.normalized is None:
            raise BridgeRegistryError(f"{name}: {contract.get('address')!r} is not an address")
        contracts[(spec.slug, check.normalized)] = role
    if not contracts:
        raise BridgeRegistryError(f"{name}: a bridge needs contracts")

    if not all(isinstance(group, dict) for group in grouped):
        raise BridgeRegistryError(f"{name}: every token group must map chains to assets")
    tokens = tuple(
        {_spec(name, chain).slug: str(asset).lower() for chain, asset in group.items()}
        for group in grouped
    )
    return Bridge(name, delay, tolerance, contracts, tokens)


def _spec(bridge: str, chain: Any) -> ChainSpec:
    spec = get_chain(chain) if isinstance(chain, str) else None
    if spec is None:
        raise BridgeRegistryError(f"{bridge}: unknown chain {chain!r}")
    return spec


def events_from_graph(registry: BridgeRegistry, graph: Graph, chain_slug: str) -> list[BridgeEvent]:
    """The deposits into and withdrawals out of registered bridge contracts that a graph holds
    (movements with no known time cannot be paired and are left out)."""
    events: list[BridgeEvent] = []
    if not registry:
        return events
    for edge in graph.edges:
        for moment, flow in edge.timeline:
            into = registry.contract(chain_slug, flow.target)
            if into and into[1] in (DEPOSIT, BOTH):
                events.append(_make(into[0], chain_slug, DEPOSIT, flow.source, flow, moment))
            out_of = registry.contract(chain_slug, flow.source)
            if out_of and out_of[1] in (WITHDRAWAL, BOTH):
                events.append(_make(out_of[0], chain_slug, WITHDRAWAL, flow.target, flow, moment))
    return events


def _make(
    bridge: Bridge, chain: str, role: str, party: str, flow: Flow, moment: datetime
) -> BridgeEvent:
    return BridgeEvent(
        bridge.name, chain, role, party, flow.asset, flow.amount, moment, flow.transaction
    )


def correlate(registry: BridgeRegistry, events: Iterable[BridgeEvent]) -> Correlation:
    """Pair deposits with withdrawals on another chain, only where the pairing is unique."""
    events = list(events)
    links: list[BridgeLink] = []
    ambiguous: list[tuple[BridgeEvent, tuple[BridgeEvent, ...]]] = []
    for bridge in registry.bridges:
        deposits = [e for e in events if e.bridge == bridge.name and e.role == DEPOSIT]
        withdrawals = [e for e in events if e.bridge == bridge.name and e.role == WITHDRAWAL]
        # Positions, not events: two identical deposits are two deposits.
        fits = [
            [w for w, other in enumerate(withdrawals) if _fits(bridge, d, other)] for d in deposits
        ]
        for index, deposit in enumerate(deposits):
            if not fits[index]:
                continue
            taken_by = sum(1 for other in fits if fits[index][0] in other)
            if len(fits[index]) == 1 and taken_by == 1:
                withdrawal = withdrawals[fits[index][0]]
                links.append(
                    BridgeLink(
                        bridge.name,
                        deposit,
                        withdrawal,
                        int((withdrawal.timestamp - deposit.timestamp).total_seconds()),
                        _fee_bps(deposit, withdrawal),
                    )
                )
            else:
                ambiguous.append((deposit, tuple(withdrawals[w] for w in fits[index])))
    return Correlation(tuple(links), tuple(ambiguous))


def _value(event: BridgeEvent) -> Fraction | None:
    decimals = event.asset.decimals
    return None if decimals is None else Fraction(event.amount, 10**decimals)


def _fee_bps(deposit: BridgeEvent, withdrawal: BridgeEvent) -> int:
    paid, got = _value(deposit), _value(withdrawal)
    assert paid is not None and got is not None
    return int((paid - got) * 10_000 / paid)


def _same_token(bridge: Bridge, deposit: BridgeEvent, withdrawal: BridgeEvent) -> bool:
    for group in bridge.tokens:
        if (
            group.get(deposit.chain) == deposit.asset.key.lower()
            and group.get(withdrawal.chain) == withdrawal.asset.key.lower()
        ):
            return True
    return False


def _fits(bridge: Bridge, deposit: BridgeEvent, withdrawal: BridgeEvent) -> bool:
    if deposit.chain == withdrawal.chain or not _same_token(bridge, deposit, withdrawal):
        return False
    delay = (withdrawal.timestamp - deposit.timestamp).total_seconds()
    if not 0 <= delay <= bridge.max_delay_seconds:
        return False
    paid, got = _value(deposit), _value(withdrawal)
    if paid is None or got is None or paid <= 0:
        return False
    return got <= paid and (paid - got) * 10_000 <= paid * bridge.amount_tolerance_bps
