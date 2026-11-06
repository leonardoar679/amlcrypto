"""A graph as text for the command line."""

from __future__ import annotations

from app.graph.model import Graph
from app.ingestion.render import format_amount, printable, short

MAX_NODES_SHOWN = 40
MAX_EDGES_SHOWN = 40
MAX_EXPOSURES_SHOWN = 20


def _when(moment) -> str:
    return "-" if moment is None else moment.strftime("%Y-%m-%d")


def render_graph(graph: Graph) -> str:
    scope = graph.limits
    lines = [
        f"{graph.root}",
        f"  depth {scope.depth}, {scope.direction.value}, since {_when(graph.since)} "
        f"({scope.window_days} days); {len(graph.nodes)} addresses, {len(graph.edges)} links, "
        f"{graph.requests_used} provider requests",
    ]
    if graph.complete:
        lines.append("  complete within that scope")
    else:
        lines.append(f"  INCOMPLETE: {len(graph.gaps)} thing(s) this graph does not show")
        lines += [f"    - {gap.message}" for gap in graph.gaps]
    lines += [f"  note: {warning}" for warning in graph.warnings]
    if graph.ignored:
        left_out = ", ".join(f"{count} {name}" for name, count in sorted(graph.ignored.items()))
        lines.append(f"  left out of the links: {left_out}")

    if graph.exposures:
        lines.append("")
        lines.append("Exposure (routes value could have taken; not proof that it did):")
        for path in graph.exposures[:MAX_EXPOSURES_SHOWN]:
            categories = "/".join(path.categories)
            arrow = "paid the root" if path.direction == "incoming" else "was paid by the root"
            lines.append(
                f"  {categories} ({path.confidence}%) {short(path.target, 12, 8)} {arrow}, "
                f"{path.length} hop(s), {path.transfers} transfer(s)"
                + ("" if path.ordering_proven else ", ORDER NOT PROVEN")
                + (", passes a bridge" if path.contains_bridge else "")
                + ("" if path.all_exact else ", amounts allocated")
            )
            for hop in path.hops:
                lines.append(
                    f"      {short(hop.source, 12, 8)} -> {short(hop.target, 12, 8)}  "
                    f"{format_amount(hop.chosen.amount_raw, hop.asset.decimals)} "
                    f"{printable(hop.asset.symbol or hop.asset.key)}  {_when(hop.chosen.timestamp)}"
                )
        if len(graph.exposures) > MAX_EXPOSURES_SHOWN:
            lines.append(f"  ... {len(graph.exposures) - MAX_EXPOSURES_SHOWN} more")

    if graph.patterns:
        lines.append("")
        lines.append("Patterns (indicators, not conclusions):")
        for pattern in graph.patterns:
            word = "paid by" if pattern.kind == "fan_in" else "paying"
            lines.append(
                f"  {short(pattern.node, 12, 8)} {word} {pattern.counterparties} different "
                f"addresses within {pattern.window_seconds // 3600} h "
                f"({pattern.transfers} transfers, {_when(pattern.start)})"
            )

    lines.append("")
    lines.append("Addresses:")
    for node in graph.nodes[:MAX_NODES_SHOWN]:
        labels = node.intel.labels if node.intel else ()
        tag = "; ".join(
            f"{label.category.value} {label.confidence}% {label.entity_name or ''}".strip()
            for label in labels
        )
        state = node.coverage.state if node.coverage else "not expanded"
        lines.append(
            f"  {node.depth}  {node.display}  [{state}]" + (f"  {printable(tag)}" if tag else "")
        )
    if len(graph.nodes) > MAX_NODES_SHOWN:
        lines.append(f"  ... {len(graph.nodes) - MAX_NODES_SHOWN} more")

    lines.append("")
    lines.append("Links (an address paying another; nearest the root first):")
    for edge in graph.edges[:MAX_EDGES_SHOWN]:
        amount = format_amount(str(edge.total), edge.asset.decimals)
        lines.append(
            f"  {short(edge.source, 12, 8)} -> {short(edge.target, 12, 8)}  {amount} "
            f"{printable(edge.asset.symbol or edge.asset.key)}  x{edge.count}  "
            f"{_when(edge.first)}..{_when(edge.last)}"
            + ("" if edge.basis.value == "exact" else "  (allocated)")
        )
    if len(graph.edges) > MAX_EDGES_SHOWN:
        lines.append(f"  ... {len(graph.edges) - MAX_EDGES_SHOWN} more")
    return "\n".join(lines)
