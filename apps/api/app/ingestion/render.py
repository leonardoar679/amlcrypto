"""Plain-text views of a history and of one transaction, for the command line."""

from __future__ import annotations

from app.chains.models import TransactionDetail, Transfer
from app.ingestion.fetcher import History


def format_amount(amount_raw: str, decimals: int | None) -> str:
    """1.5 for 1500000 at 6 decimals. Without decimals, the raw integer and a marker saying so."""
    if decimals is None:
        return f"{amount_raw} (raw)"
    amount = int(amount_raw)
    if decimals == 0:
        return str(amount)
    whole, fraction = divmod(amount, 10**decimals)
    return f"{whole}.{fraction:0{decimals}d}".rstrip("0").rstrip(".")


def short(value: str | None, head: int = 8, tail: int = 6) -> str:
    if not value:
        return "-"
    return value if len(value) <= head + tail + 1 else f"{value[:head]}…{value[-tail:]}"


def direction(transfer: Transfer, address: str) -> str:
    sent, received = transfer.from_address == address, transfer.to_address == address
    return "self" if sent and received else "out" if sent else "in"


def _when(transfer: Transfer) -> str:
    return "-" if transfer.timestamp is None else transfer.timestamp.strftime("%Y-%m-%d %H:%M")


def _line(transfer: Transfer, address: str) -> str:
    asset = transfer.asset.symbol or short(transfer.asset.contract_address) or "?"
    other = transfer.to_address if transfer.from_address == address else transfer.from_address
    return (
        f"{transfer.block_number:>10}  {_when(transfer):<16}  {transfer.transfer_type.value:<8}"
        f"  {direction(transfer, address):<4}  "
        f"{format_amount(transfer.amount_raw, transfer.asset.decimals):>28} {asset:<10}"
        f"  {short(other):<15}  {short(transfer.transaction_hash, 10, 6)}"
    )


def render_history(history: History) -> str:
    counts: dict[str, int] = {}
    for transfer in history.transfers:
        counts[transfer.transfer_type.value] = counts.get(transfer.transfer_type.value, 0) + 1
    by_type = ", ".join(f"{name} {count}" for name, count in counts.items()) or "none"

    lines = [
        f"{history.chain_id}  {history.address}",
        f"{len(history.transfers)} transfers ({by_type}) in {len(history.transactions)} "
        f"transactions; chain height {history.head_block or 'unknown'}",
        "",
    ]
    if history.transfers:
        lines.append(
            f"{'block':>10}  {'time (UTC)':<16}  {'type':<8}  {'':<4}  {'amount':>28} "
            f"{'asset':<10}  {'counterparty':<15}  transaction"
        )
        lines += [_line(transfer, history.address) for transfer in history.transfers]
        lines.append("")

    if history.complete:
        through = history.synced_through_block
        lines.append(f"Complete through block {through}." if through is not None else "Complete.")
    else:
        lines.append("INCOMPLETE: this is not the whole history.")
    if history.unavailable:
        lines.append(f"Not available from any configured source: {', '.join(history.unavailable)}.")
    lines += [f"  - {note}" for note in history.coverage]
    if history.resume:
        lines.append(f"Resume with: {history.resume}")
    return "\n".join(lines)


def render_transaction(detail: TransactionDetail) -> str:
    tx = detail.transaction
    lines = [
        f"{tx.chain_id}  {tx.transaction_hash}",
        f"  status        {tx.status.value}   (block {tx.block_number}, "
        f"{tx.confirmation_status.value}"
        + (f", {tx.confirmations} confirmations" if tx.confirmations is not None else "")
        + ")",
        f"  time          {tx.timestamp:%Y-%m-%d %H:%M:%S} UTC"
        if tx.timestamp
        else "  time          -",
        f"  from          {tx.from_address}",
        f"  to            {tx.to_address or '(contract creation)'}",
    ]
    if tx.created_contract:
        lines.append(f"  created       {tx.created_contract}")
    lines.append(f"  value         {tx.value_raw} (raw, smallest unit)")
    if tx.fee_raw is not None:
        lines.append(f"  fee           {tx.fee_raw} (raw, smallest unit)")
    if tx.method_id:
        lines.append(f"  method        {tx.method_id}")
    lines.append(f"  source        {tx.provider} ({tx.data_quality.value})")

    lines.append("")
    if detail.transfers:
        lines.append("Movements proven by the receipt:")
        for transfer in detail.transfers:
            asset = transfer.asset.symbol or short(transfer.asset.contract_address)
            lines.append(
                f"  {transfer.transfer_type.value:<7} {short(transfer.from_address, 10, 6)} -> "
                f"{short(transfer.to_address, 10, 6)}  "
                f"{format_amount(transfer.amount_raw, transfer.asset.decimals)} {asset}"
                + (f"  (log {transfer.log_index})" if transfer.log_index is not None else "")
            )
    else:
        lines.append("No value movements in the receipt.")
    lines += [f"  - {note}" for note in detail.coverage]
    return "\n".join(lines)
