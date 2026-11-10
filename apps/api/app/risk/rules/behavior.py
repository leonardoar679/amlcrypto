"""Rules about how the subject itself moves value: they read its own movements in the window (and,
for the two that follow money onward, the graph around it).

Every one of these is an indicator with thresholds that are defaults to calibrate, not a regulatory
limit, and none of them can say why the pattern exists: an exchange, a payroll service and an
airdrop each look like several of them.
"""

from __future__ import annotations

import statistics
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.graph.model import Flow, GraphEdge
from app.graph.patterns import detect_fans
from app.intel.model import Category
from app.risk.model import RuleOutcome
from app.risk.rules.base import (
    Rule,
    RuleContext,
    flow_summary,
    has_category,
    newest,
    transactions_of,
)

DAY = 86_400
# What a source says an address is, when that is a service people use as it is meant to be used:
# nearly everything an exchange's customer sends or receives goes through the one exchange.
SERVICES = (Category.EXCHANGE, Category.DEFI, Category.BRIDGE, Category.GAMBLING)


def _no_history(ctx: RuleContext) -> RuleOutcome | None:
    if not ctx.root_read:
        return RuleOutcome.not_evaluated("the subject's own history is not held")
    return None


def _timed(flows: Sequence[Flow]) -> list[Flow]:
    return sorted(
        (flow for flow in flows if flow.timestamp is not None),
        key=lambda f: (f.timestamp, f.transaction, f.event),
    )


# --- AML-004: rapid fund movement ---------------------------------------------------------------


def rapid_movement(rule: Rule, ctx: RuleContext, cfg: Mapping[str, Any]) -> RuleOutcome:
    """What comes in leaves again soon. Outgoing movements are matched to incoming ones oldest
    first (first in, first out) for each asset, so one incoming amount is never counted as having
    left twice."""
    if (early := _no_history(ctx)) is not None:
        return early
    address = ctx.root.address
    window = int(cfg["window_seconds"])
    needed = round(float(cfg["min_ratio"]) * 10_000)
    findings = []
    by_asset: dict[str, list[Flow]] = {}
    for flow in _timed(ctx.flows):
        by_asset.setdefault(flow.asset.key, []).append(flow)
    for key in sorted(by_asset):
        events = sorted(
            by_asset[key],
            key=lambda f: (f.timestamp, f.target != address, f.transaction, f.event),
        )
        queue: deque[list[Any]] = deque()  # [amount still unmatched, when it came, the movement]
        total_in = fast = 0
        fast_out: dict[tuple[str, str, str, str], Flow] = {}
        pairs: list[dict[str, Any]] = []
        for flow in events:
            if flow.target == address:
                queue.append([flow.amount, flow.timestamp, flow])
                total_in += flow.amount
                continue
            left = flow.amount
            while left and queue:
                head = queue[0]
                used = min(left, head[0])
                delay = (flow.timestamp - head[1]).total_seconds()
                if delay <= window:
                    fast += used
                    fast_out[flow.identity] = flow
                    if len(pairs) < 5:
                        pairs.append(
                            {
                                "type": "rapid_onward",
                                "received": flow_summary(head[2]),
                                "sent": flow_summary(flow),
                                "delay_seconds": int(delay),
                            }
                        )
                head[0] -= used
                left -= used
                if head[0] == 0:
                    queue.popleft()
        if (
            total_in
            and fast * 10_000 >= total_in * needed
            and len(fast_out) >= int(cfg["min_transfers"])
        ):
            out = list(fast_out.values())
            txs, total = transactions_of(out)
            symbol = events[0].asset.symbol or key
            findings.append(
                rule.finding(
                    description=(
                        f"{fast * 100 // total_in}% of the {symbol} received left again within "
                        f"{window // 60} minutes ({len(out)} onward transfers)"
                    ),
                    key=("rapid", key),
                    confidence=float(cfg["confidence"]),
                    transactions=txs,
                    transactions_total=total,
                    evidence=tuple(pairs),
                    latest_at=newest(out),
                )
            )
    return RuleOutcome.triggered(findings) if findings else RuleOutcome.clear()


# --- AML-005: layering --------------------------------------------------------------------------


def layering(rule: Rule, ctx: RuleContext, cfg: Mapping[str, Any]) -> RuleOutcome:
    """Value that keeps moving: from the subject through at least `min_hops` addresses, each hop
    soon after the last, in the same asset and for about the same amount."""
    min_hops = int(cfg["min_hops"])
    if (early := _no_history(ctx)) is not None:
        return early
    if ctx.graph.limits.depth < min_hops:
        return RuleOutcome.not_evaluated(
            f"the graph is {ctx.graph.limits.depth} hop(s) deep and following {min_hops} hops "
            "needs a graph that deep"
        )
    graph = ctx.graph
    leaving: dict[str, list[GraphEdge]] = {}
    for edge in sorted(graph.edges, key=lambda e: (e.source, e.target, e.asset.key)):
        leaving.setdefault(edge.source, []).append(edge)
    max_delay = int(cfg["max_hop_delay_seconds"])
    keep_percent = int(cfg["min_ratio_percent"])
    budget = [int(cfg["max_steps"])]
    chains: list[list[tuple[GraphEdge, Flow]]] = []

    def extend(node: str, chain: list[tuple[GraphEdge, Flow]], seen: frozenset[str]) -> None:
        budget[0] -= 1
        previous = chain[-1][1]
        grew = False
        for edge in leaving.get(node, ()):
            if edge.target in seen or edge.asset.key != previous.asset.key:
                continue
            for moment, flow in edge.timeline:
                if moment < previous.timestamp:
                    continue
                if (moment - previous.timestamp).total_seconds() > max_delay:
                    break
                if (
                    flow.amount > previous.amount
                    or flow.amount * 100 < previous.amount * keep_percent
                ):
                    continue
                grew = True
                if budget[0] > 0:
                    extend(edge.target, [*chain, (edge, flow)], seen | {edge.target})
                break  # the earliest movement that fits leaves the most room for what follows
        if not grew and len(chain) >= min_hops:
            chains.append(chain)

    for edge in leaving.get(graph.root, ()):
        for _, flow in edge.timeline[: int(cfg["max_starts"])]:
            if budget[0] > 0:
                extend(edge.target, [(edge, flow)], frozenset({graph.root, edge.target}))
    if not chains:
        return RuleOutcome.clear()

    chains.sort(key=lambda c: (-len(c), c[0][1].timestamp, c[0][1].transaction))
    flows = [flow for chain in chains for _, flow in chain]
    txs, total = transactions_of(flows)
    return RuleOutcome.triggered(
        [
            rule.finding(
                description=(
                    f"{len(chains)} route(s) of {min_hops} or more hops where the value kept "
                    f"moving soon after it arrived (longest: {len(chains[0])} hops)"
                ),
                key=("layering", graph.root),
                confidence=float(cfg["confidence"]),
                hops=len(chains[0]),
                direction="outgoing",
                transactions=txs,
                transactions_total=total,
                evidence=tuple(
                    {"type": "layering_route", "hops": [flow_summary(flow) for _, flow in chain]}
                    for chain in chains[:3]
                ),
                latest_at=newest(flows),
            )
        ]
    )


# --- AML-006: peel chains -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Peel:
    source: str
    large: str
    small: str
    transaction: str
    moment: datetime | None
    flows: tuple[Flow, ...]


def peel_chain(rule: Rule, ctx: RuleContext, cfg: Mapping[str, Any]) -> RuleOutcome:
    """A transaction that pays a small amount to one address and the rest to another, repeated down
    a chain: each step's large part becomes the next step's sender."""
    steps_needed = int(cfg["min_steps"])
    if (early := _no_history(ctx)) is not None:
        return early
    if ctx.graph.limits.depth < steps_needed:
        return RuleOutcome.not_evaluated(
            f"the graph is {ctx.graph.limits.depth} hop(s) deep and following {steps_needed} "
            "steps needs a graph that deep"
        )
    percent = int(cfg["max_peel_percent"])
    steps: dict[str, list[_Peel]] = {}
    for address, flows in ctx.graph.histories.items():
        by_tx: dict[str, list[Flow]] = {}
        for flow in flows:
            if flow.source == address:
                by_tx.setdefault(flow.transaction, []).append(flow)
        for tx, sent in sorted(by_tx.items()):
            amounts = Counter[str]()
            for flow in sent:
                amounts[flow.target] += flow.amount
            if len(amounts) != 2:
                continue
            (large, big), (small, little) = sorted(amounts.items(), key=lambda i: (-i[1], i[0]))
            if little * 100 <= big * percent:
                moments = [f.timestamp for f in sent if f.timestamp]
                steps.setdefault(address, []).append(
                    _Peel(address, large, small, tx, max(moments) if moments else None, tuple(sent))
                )

    def following(step: _Peel) -> _Peel | None:
        later = [
            s
            for s in steps.get(step.large, ())
            if s.transaction != step.transaction
            and step.moment is not None
            and s.moment is not None
            and s.moment >= step.moment
        ]
        return min(later, key=lambda s: (s.moment, s.transaction), default=None)

    successors = {s.transaction: following(s) for group in steps.values() for s in group}
    taken = {nxt.transaction for nxt in successors.values() if nxt is not None}
    chains = []
    for group in steps.values():
        for start in group:
            if start.transaction in taken:
                continue  # it continues a longer chain that is listed from its own start
            chain, step = [start], start
            while (step := successors.get(step.transaction)) is not None and step not in chain:
                chain.append(step)
            if len(chain) >= steps_needed and any(
                ctx.root.address in (s.source, s.large) for s in chain
            ):
                chains.append(chain)
    if not chains:
        return RuleOutcome.clear()
    chains.sort(key=lambda c: (-len(c), c[0].transaction))
    flows = [flow for chain in chains for s in chain for flow in s.flows]
    txs, total = transactions_of(flows)
    return RuleOutcome.triggered(
        [
            rule.finding(
                description=(
                    f"{len(chains)} chain(s) of {steps_needed} or more transactions that each pay "
                    f"a small part to one address and the rest onward (longest: {len(chains[0])})"
                ),
                key=("peel", ctx.root.id),
                confidence=float(cfg["confidence"]),
                hops=len(chains[0]),
                transactions=txs,
                transactions_total=total,
                evidence=tuple(
                    {
                        "type": "peel_chain",
                        "steps": [
                            {
                                "transaction_hash": s.transaction,
                                "from": s.source,
                                "onward_to": s.large,
                                "peeled_to": s.small,
                            }
                            for s in chain
                        ],
                    }
                    for chain in chains[:3]
                ),
                latest_at=newest(flows),
            )
        ]
    )


# --- AML-009 / 010: fan-in and fan-out ----------------------------------------------------------


def _fan(kind: str):
    def evaluate(rule: Rule, ctx: RuleContext, cfg: Mapping[str, Any]) -> RuleOutcome:
        if (early := _no_history(ctx)) is not None:
            return early
        found = [
            pattern
            for pattern in detect_fans(
                ctx.root.id,
                ctx.root.address,
                ctx.flows,
                threshold=int(cfg["threshold"]),
                window_seconds=int(cfg["window_seconds"]),
            )
            if pattern.kind == kind
        ]
        if not found:
            return RuleOutcome.clear()
        pattern = found[0]
        word = "paid by" if kind == "fan_in" else "paying"
        return RuleOutcome.triggered(
            [
                rule.finding(
                    description=(
                        f"{word.capitalize()} {pattern.counterparties} different addresses within "
                        f"{pattern.window_seconds // 3600} hours ({pattern.transfers} transfers)"
                    ),
                    key=(kind, ctx.root.id),
                    confidence=float(cfg["confidence"]),
                    direction="incoming" if kind == "fan_in" else "outgoing",
                    transactions=tuple(e.transaction for e in pattern.evidence),
                    transactions_total=len(pattern.evidence),
                    evidence=(
                        {
                            "type": kind,
                            **{k: v for k, v in pattern.to_dict().items() if k != "node"},
                        },
                    ),
                    latest_at=pattern.end,
                )
            ]
        )

    return evaluate


fan_in = _fan("fan_in")
fan_out = _fan("fan_out")


# --- AML-011: velocity --------------------------------------------------------------------------


def velocity(rule: Rule, ctx: RuleContext, cfg: Mapping[str, Any]) -> RuleOutcome:
    """The busiest day against the subject's own usual day: a burst, not just a busy address."""
    if (early := _no_history(ctx)) is not None:
        return early
    per_day = Counter(flow.timestamp.date() for flow in ctx.flows if flow.timestamp)
    if len(per_day) < int(cfg["min_active_days"]):
        return RuleOutcome.not_evaluated(
            f"fewer than {cfg['min_active_days']} active days: no usual day to compare with"
        )
    day, busiest = max(per_day.items(), key=lambda item: (item[1], item[0]))
    usual = statistics.median(per_day.values())
    if busiest < int(cfg["min_per_day"]) or busiest < float(cfg["factor"]) * usual:
        return RuleOutcome.clear()
    that_day = [f for f in ctx.flows if f.timestamp and f.timestamp.date() == day]
    txs, total = transactions_of(that_day)
    return RuleOutcome.triggered(
        [
            rule.finding(
                description=(
                    f"{busiest} movements on {day.isoformat()}, against a usual {usual:g} on the "
                    f"days it is active"
                ),
                key=("velocity", ctx.root.id),
                confidence=float(cfg["confidence"]),
                transactions=txs,
                transactions_total=total,
                evidence=(
                    {
                        "type": "velocity",
                        "day": day.isoformat(),
                        "movements": busiest,
                        "usual_per_active_day": usual,
                        "active_days": len(per_day),
                    },
                ),
                latest_at=newest(that_day),
            )
        ]
    )


# --- AML-012: dormant wallet activation ---------------------------------------------------------


def dormant_activation(rule: Rule, ctx: RuleContext, cfg: Mapping[str, Any]) -> RuleOutcome:
    """A long silence between two movements, ended recently. Silence before the first movement the
    window shows cannot be seen, so only gaps between two observed movements count."""
    if (early := _no_history(ctx)) is not None:
        return early
    flows = _timed(ctx.flows)
    if len(flows) < 2:
        return RuleOutcome.not_evaluated("fewer than two movements: there is no silence to measure")
    quiet = timedelta(days=int(cfg["dormant_days"]))
    recent = timedelta(days=int(cfg["recent_days"]))
    ended = None
    for before, after in zip(flows, flows[1:], strict=False):
        if after.timestamp - before.timestamp >= quiet and ctx.now - after.timestamp <= recent:
            ended = (before, after)
    if ended is None:
        return RuleOutcome.clear()
    before, after = ended
    days = (after.timestamp - before.timestamp).days
    return RuleOutcome.triggered(
        [
            rule.finding(
                description=f"Active again after {days} days without a movement",
                key=("dormant", ctx.root.id),
                confidence=float(cfg["confidence"]),
                transactions=(after.transaction, before.transaction),
                transactions_total=2,
                evidence=(
                    {
                        "type": "dormant",
                        "silent_days": days,
                        "last_before": flow_summary(before),
                        "first_after": flow_summary(after),
                    },
                ),
                latest_at=after.timestamp,
            )
        ]
    )


# --- AML-014: concentration ---------------------------------------------------------------------


def concentration(rule: Rule, ctx: RuleContext, cfg: Mapping[str, Any]) -> RuleOutcome:
    """Almost all of the value in (or out) of one asset goes through one counterparty, unless a
    source says that counterparty is an exchange, a DeFi protocol, a bridge or a gambling service:
    that is what ordinary use of one looks like."""
    if (early := _no_history(ctx)) is not None:
        return early
    address = ctx.root.address
    percent = int(cfg["share_percent"])
    findings = []
    for direction, mine in (("incoming", "target"), ("outgoing", "source")):
        by_asset: dict[str, list[Flow]] = {}
        for flow in ctx.flows:
            if getattr(flow, mine) == address:
                by_asset.setdefault(flow.asset.key, []).append(flow)
        for key in sorted(by_asset):
            flows = by_asset[key]
            if len(flows) < int(cfg["min_transfers"]):
                continue
            totals = Counter[str]()
            for flow in flows:
                totals[flow.source if direction == "incoming" else flow.target] += flow.amount
            who, top = max(totals.items(), key=lambda item: (item[1], item[0]))
            whole = sum(totals.values())
            if whole == 0 or top * 100 < whole * percent:
                continue  # nothing of value moved (address poisoning sends zero), or no one party
            if has_category(ctx.node(f"{ctx.chain}:{who}"), *SERVICES):
                continue  # ordinary use of a service, not a pass-through
            ones = [f for f in flows if (f.source if direction == "incoming" else f.target) == who]
            txs, total = transactions_of(ones)
            symbol = flows[0].asset.symbol or key
            findings.append(
                rule.finding(
                    description=(
                        f"{top * 100 // whole}% of the {symbol} "
                        f"{'received came from' if direction == 'incoming' else 'sent went to'} "
                        f"one address ({len(ones)} of {len(flows)} transfers)"
                    ),
                    key=("concentration", direction, key),
                    confidence=float(cfg["confidence"]),
                    direction=direction,
                    target=f"{ctx.chain}:{who}",
                    transactions=txs,
                    transactions_total=total,
                    evidence=(
                        {
                            "type": "concentration",
                            "counterparty": who,
                            "asset": symbol,
                            "share_percent": top * 100 // whole,
                            "counterparty_total_raw": str(top),
                            "total_raw": str(whole),
                        },
                    ),
                    latest_at=newest(ones),
                )
            )
    return RuleOutcome.triggered(findings) if findings else RuleOutcome.clear()


# --- AML-015: structuring -----------------------------------------------------------------------


def structuring(rule: Rule, ctx: RuleContext, cfg: Mapping[str, Any]) -> RuleOutcome:
    """Several movements just under a reporting threshold within a few days. Thresholds are in an
    asset's own units and nothing ships with the platform: what counts as a reporting threshold
    depends on the jurisdiction and the asset's price, so without them the rule cannot judge."""
    thresholds: Mapping[str, Sequence[int]] = cfg["thresholds"]
    if not thresholds:
        return RuleOutcome.not_evaluated(
            "no reporting thresholds are configured: set `thresholds` per asset "
            "(asset key -> amounts in its smallest unit)"
        )
    if (early := _no_history(ctx)) is not None:
        return early
    margin = int(cfg["margin_percent"])
    within = int(cfg["within_days"]) * DAY
    findings = []
    for key in sorted(thresholds):
        flows = [f for f in _timed(ctx.flows) if f.asset.key == key]
        for limit in sorted(int(t) for t in thresholds[key]):
            low = limit * (100 - margin) // 100
            near = [f for f in flows if low <= f.amount < limit]
            best: list[Flow] = []
            for index, first in enumerate(near):
                group = [
                    f
                    for f in near[index:]
                    if (f.timestamp - first.timestamp).total_seconds() <= within
                ]
                if len(group) > len(best):
                    best = group
            if len(best) >= int(cfg["min_count"]):
                txs, total = transactions_of(best)
                findings.append(
                    rule.finding(
                        description=(
                            f"{len(best)} movements of {best[0].asset.symbol or key} between "
                            f"{low} and {limit} (raw units) within {cfg['within_days']} days"
                        ),
                        key=("structuring", key, str(limit)),
                        confidence=float(cfg["confidence"]),
                        transactions=txs,
                        transactions_total=total,
                        evidence=tuple({"type": "transfer", **flow_summary(f)} for f in best[:5]),
                        latest_at=newest(best),
                    )
                )
    return RuleOutcome.triggered(findings) if findings else RuleOutcome.clear()
