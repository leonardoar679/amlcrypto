"""Building blocks for graph tests: flows, labels and a reader that needs no database."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from app.graph.model import AssetRef, Basis, Flow, Gap, NodeCoverage
from app.graph.reader import NodeData
from app.intel.model import (
    AddressIntel,
    Category,
    Freshness,
    LabelMatch,
    SourceKind,
    SourceStatus,
)

CHAIN_ID = "eip155:1"
NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
ETH = AssetRef("native", "ETH", 18)
USDT = AssetRef("0xdac17f958d2ee523a2206206994597c13d831ec7", "USDT", 6)

ROOT = "0x" + "0" * 39 + "1"


def addr(number: int) -> str:
    """A valid-looking EVM address in storage form."""
    return "0x" + f"{number:040x}"


def at(hours: float = 0, *, seconds: float = 0) -> datetime:
    """A moment `hours` after the start of the test day."""
    return NOW - timedelta(days=10) + timedelta(hours=hours, seconds=seconds)


_counter = {"n": 0}


def flow(
    source: str,
    target: str,
    amount: int = 10**18,
    when: datetime | None = None,
    *,
    asset: AssetRef = ETH,
    tx: str | None = None,
    event: str = "native",
    basis: Basis = Basis.EXACT,
    timed: bool = True,
) -> Flow:
    _counter["n"] += 1
    return Flow(
        chain_id=CHAIN_ID,
        source=source,
        target=target,
        asset=asset,
        amount=amount,
        timestamp=(when or at(_counter["n"])) if timed else None,
        transaction=tx or f"0x{_counter['n']:064x}",
        event=event,
        basis=basis,
    )


class FakeReader:
    """Serves every flow that has the address at one end, as the database would."""

    def __init__(
        self,
        flows: Sequence[Flow],
        *,
        states: dict[str, tuple[NodeCoverage, list[Gap]]] | None = None,
    ) -> None:
        self.flows = list(flows)
        self.states = states or {}
        self.loaded: list[str] = []
        self.requests_used = 0

    async def load(self, address: str) -> NodeData:
        self.loaded.append(address)
        mine = [f for f in self.flows if address in (f.source, f.target)]
        coverage, gaps = self.states.get(address, (NodeCoverage("complete"), []))
        return NodeData(mine, coverage, list(gaps))


def source_status(freshness: Freshness = Freshness.FRESH) -> SourceStatus:
    return SourceStatus(
        slug="ofac_sdn",
        name="OFAC SDN",
        kind=SourceKind.OFFICIAL,
        url=None,
        publish_date=date(2026, 11, 5),
        last_checked_at=NOW,
        refresh_seconds=86_400,
        status="ok",
        last_error=None,
        age_seconds=60.0,
        freshness=freshness,
    )


def label(
    address: str,
    category: Category = Category.SANCTIONS,
    *,
    confidence: int = 100,
    entity: str | None = "Bad Actor",
) -> LabelMatch:
    return LabelMatch(
        chain_id=CHAIN_ID,
        address=address,
        category=category,
        confidence=confidence,
        source=source_status(),
        source_ref="1234",
        entity_name=entity,
        entity_type="entity",
        programs=(),
        listed_as="ETH",
        source_url=None,
        first_observed=NOW,
        last_verified=NOW,
        active=True,
        removed_at=None,
    )


class FakeLabels:
    def __init__(self, *labelled: LabelMatch, freshness: Freshness = Freshness.FRESH) -> None:
        self.by_address: dict[str, list[LabelMatch]] = {}
        for item in labelled:
            self.by_address.setdefault(item.address, []).append(item)
        self.freshness = freshness
        self.asked: list[list[str]] = []

    async def of(self, addresses: Sequence[str]) -> dict[str, AddressIntel]:
        self.asked.append(list(addresses))
        sources = (source_status(self.freshness),)
        return {
            a: AddressIntel(
                chain_id=CHAIN_ID,
                address=a,
                labels=tuple(self.by_address.get(a, ())),
                former=(),
                sources=sources,
            )
            for a in addresses
        }
