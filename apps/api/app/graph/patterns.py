"""Fan-in and fan-out: many counterparties paying one address, or one address paying many, in a
short time (blueprint rules AML-009 and AML-010 are built on this).

The detector finds the busiest window, not every window, so one burst is one pattern. It looks only
at movements with a known time, and says how many it could not place. What it finds is an
indicator: exchanges, payment processors, payroll and airdrops all look like this.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime

from app.graph.model import Evidence, FanPattern, Flow

SAMPLE = 10
EVIDENCE = 5


def detect_fans(
    node: str,
    address: str,
    flows: Sequence[Flow],
    *,
    threshold: int,
    window_seconds: int,
) -> list[FanPattern]:
    """The fan-in and fan-out of `address`, when each reaches `threshold` different counterparties
    inside some window of `window_seconds`. `node` names the address in the graph."""
    found = []
    for kind, mine in (("fan_in", "target"), ("fan_out", "source")):
        pattern = _busiest(
            kind,
            node,
            [flow for flow in flows if getattr(flow, mine) == address and flow.timestamp],
            "source" if kind == "fan_in" else "target",
            threshold=threshold,
            window_seconds=window_seconds,
        )
        if pattern is not None:
            found.append(pattern)
    return found


def _busiest(
    kind: str,
    node: str,
    flows: list[Flow],
    other: str,
    *,
    threshold: int,
    window_seconds: int,
) -> FanPattern | None:
    if len({getattr(flow, other) for flow in flows}) < threshold:
        return None
    flows.sort(key=lambda f: (_seconds(f.timestamp), f.transaction, f.event))
    inside: Counter[str] = Counter()
    left = 0
    best: tuple[int, int, int] | None = None  # distinct counterparties, then window start, end
    for right, flow in enumerate(flows):
        inside[getattr(flow, other)] += 1
        while _seconds(flow.timestamp) - _seconds(flows[left].timestamp) > window_seconds:
            party = getattr(flows[left], other)
            inside[party] -= 1
            if not inside[party]:
                del inside[party]
            left += 1
        if len(inside) >= threshold and (best is None or len(inside) > best[0]):
            best = (len(inside), left, right)
    if best is None:
        return None

    distinct, start, end = best
    window = flows[start : end + 1]
    parties = Counter(getattr(flow, other) for flow in window)
    return FanPattern(
        kind=kind,
        node=node,
        window_seconds=window_seconds,
        start=window[0].timestamp,
        end=window[-1].timestamp,
        counterparties=distinct,
        transfers=len(window),
        assets=tuple(sorted({flow.asset.symbol or flow.asset.key for flow in window})),
        sample=tuple(p for p, _ in sorted(parties.items(), key=lambda i: (-i[1], i[0]))[:SAMPLE]),
        evidence=tuple(
            Evidence(f.transaction, f.event, str(f.amount), f.timestamp) for f in window[:EVIDENCE]
        ),
    )


def _seconds(moment: datetime | None) -> int:
    assert moment is not None
    return int(moment.timestamp())
