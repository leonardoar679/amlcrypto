"""Plain-text views of a history and of one transaction, for the command line."""

from __future__ import annotations

from app.chains.bitcoin.models import BitcoinDetail, BitcoinTransaction, SpendState
from app.chains.bitcoin.utxo import Likelihood, analyze
from app.chains.models import TransactionDetail, Transfer
from app.chains.support import find_chain
from app.ingestion.fetcher import History
from app.ingestion.utxo import UtxoHistory


def format_amount(amount_raw: str, decimals: int | None) -> str:
    """1.5 for 1500000 at 6 decimals. Without decimals, the raw integer and a marker saying so."""
    if decimals is None:
        return f"{amount_raw} (raw)"
    amount = int(amount_raw)
    if decimals == 0:
        return str(amount)
    whole, fraction = divmod(amount, 10**decimals)
    return f"{whole}.{fraction:0{decimals}d}".rstrip("0").rstrip(".")


def printable(text: str) -> str:
    """Provider-controlled text with every non-ASCII character spelled out as a backslash escape.

    A scam token can name itself "USDT" using a Cyrillic letter that looks exactly like the Latin
    one. Showing the escape makes such a lookalike visible instead of letting it pass for the real
    thing, and it can never fail to print on a console that cannot encode the character.
    """
    return text.encode("ascii", "backslashreplace").decode("ascii")


def short(value: str | None, head: int = 8, tail: int = 6) -> str:
    if not value:
        return "-"
    return value if len(value) <= head + tail + 2 else f"{value[:head]}..{value[-tail:]}"


def direction(transfer: Transfer, address: str) -> str:
    sent, received = transfer.from_address == address, transfer.to_address == address
    return "self" if sent and received else "out" if sent else "in"


def _asset_label(transfer: Transfer) -> str:
    """What to call the asset: its symbol, else its contract, else (TRC-10) its id."""
    asset = transfer.asset
    if asset.symbol:
        return printable(asset.symbol)
    if asset.contract_address:
        return short(asset.contract_address)
    if asset.asset_id:
        return printable(f"#{asset.asset_id}")
    return "?"


def _block(transfer: Transfer) -> str:
    return "-" if transfer.block_number is None else str(transfer.block_number)


def _when(transfer: Transfer) -> str:
    return "-" if transfer.timestamp is None else transfer.timestamp.strftime("%Y-%m-%d %H:%M")


def _line(transfer: Transfer, address: str) -> str:
    asset = _asset_label(transfer)
    other = transfer.to_address if transfer.from_address == address else transfer.from_address
    return (
        f"{_block(transfer):>10}  {_when(transfer):<16}  {transfer.transfer_type.value:<8}"
        f"  {direction(transfer, address):<4}  "
        f"{format_amount(transfer.amount_raw, transfer.asset.decimals):>28} {asset:<10}"
        f"  {short(other):<15}  {short(transfer.transaction_hash, 10, 6)}"
    )


def _native(amount_raw: str, chain_id: str) -> str:
    """An amount of the chain's own coin, in whole units when the chain is known."""
    chain = find_chain(chain_id)
    if chain is None:
        return f"{amount_raw} (raw, smallest unit)"
    return f"{format_amount(amount_raw, chain.native.decimals)} {chain.native.symbol}"


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
    lines.append(f"  value         {_native(tx.value_raw, tx.chain_id)}")
    if tx.fee_raw is not None:
        lines.append(f"  fee           {_native(tx.fee_raw, tx.chain_id)}")
    if tx.method_id:
        lines.append(f"  method        {tx.method_id}")
    lines.append(f"  source        {tx.provider} ({tx.data_quality.value})")

    lines.append("")
    if detail.transfers:
        lines.append("Movements proven by the receipt:")
        for transfer in detail.transfers:
            asset = _asset_label(transfer)
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


# --- Bitcoin -----------------------------------------------------------------------------------


def _btc(amount_raw: str | None) -> str:
    return "-" if amount_raw is None else f"{format_amount(amount_raw, 8)} BTC"


def _signed_btc(net_raw: str) -> str:
    net = int(net_raw)
    return f"{'+' if net > 0 else '-' if net < 0 else ' '}{format_amount(str(abs(net)), 8)}"


def _tags(tx: BitcoinTransaction) -> str:
    analysis = analyze(tx)
    tags = []
    if tx.is_coinbase:
        tags.append("coinbase")
    if analysis.coinjoin is not None:
        tags.append(f"coinjoin? ({analysis.coinjoin.strength})")
    return ", ".join(tags)


def render_utxo_history(history: UtxoHistory) -> str:
    heights = [tx.block_height for tx in history.transactions if tx.block_height is not None]
    span = f" in blocks {min(heights)}..{max(heights)}" if heights else ""
    lines = [
        f"{history.chain_id}  {history.address}",
        f"{len(history.transactions)} transactions{span}; this address received "
        f"{_btc(history.received_raw)} and sent {_btc(history.sent_raw)}; "
        f"chain height {history.head_block or 'unknown'}",
        "",
    ]
    if history.transactions:
        lines.append(
            f"{'block':>10}  {'time (UTC)':<16}  {'role':<8}  {'net (BTC)':>16}  {'in/out':>7}  "
            f"{'transaction':<17}  notes"
        )
        for tx in history.transactions:
            mine = history.activity.get(tx.txid)
            when = "-" if tx.timestamp is None else tx.timestamp.strftime("%Y-%m-%d %H:%M")
            block = "-" if tx.block_height is None else str(tx.block_height)
            role, net = (mine.role, _signed_btc(mine.net_raw)) if mine else ("-", "-")
            counts = f"{len(tx.inputs)}/{len(tx.outputs)}"
            lines.append(
                f"{block:>10}  {when:<16}  {role:<8}  {net:>16}  {counts:>7}  "
                f"{short(tx.txid, 10, 6):<17}  {_tags(tx)}".rstrip()
            )
        lines.append("")

    if history.complete:
        through = history.synced_through_block
        lines.append(f"Complete through block {through}." if through is not None else "Complete.")
        if history.provider_summary is not None:
            lines.append(
                f"Balance now: {_btc(history.provider_summary.balance_raw)} "
                f"(confirmed), checked against the provider's own totals."
            )
    else:
        lines.append("INCOMPLETE: this is not the whole history.")
    lines += [f"  - {note}" for note in history.coverage]
    if history.resume:
        lines.append(f"Resume with: {history.resume}")
    return "\n".join(lines)


def _spend_text(output) -> str:
    if output.spend_state is SpendState.SPENT:
        return f"spent by {short(output.spent_by_txid, 10, 6)}:{output.spent_by_input_index}"
    return {
        SpendState.UNSPENT: "unspent",
        SpendState.UNSPENDABLE: "unspendable (data, not coins)",
        SpendState.UNKNOWN: "spend state unknown",
    }[output.spend_state]


def render_bitcoin_transaction(detail: BitcoinDetail) -> str:
    tx = detail.transaction
    where = (
        "unconfirmed (in the mempool)"
        if tx.block_height is None
        else f"{tx.confirmation_status.value} (block {tx.block_height}"
        + (f", {tx.confirmations} confirmations" if tx.confirmations is not None else "")
        + ")"
    )
    total_in = sum(int(i.value_raw) for i in tx.inputs if i.value_raw is not None)
    total_out = sum(int(o.value_raw) for o in tx.outputs)
    lines = [
        f"{tx.chain_id}  {tx.txid}",
        f"  status        {where}",
        f"  time          {tx.timestamp:%Y-%m-%d %H:%M:%S} UTC"
        if tx.timestamp
        else "  time          -",
        f"  size          {tx.vsize} vB ({tx.weight} weight units)",
    ]
    if tx.fee_raw is not None:
        rate = int(tx.fee_raw) / tx.vsize if tx.vsize else 0
        lines.append(f"  fee           {_btc(tx.fee_raw)} ({rate:.1f} sat/vB)")
    lines.append(f"  source        {tx.provider} ({tx.data_quality.value})")

    lines += ["", f"Inputs ({len(tx.inputs)}), {_btc(str(total_in))}:"]
    for item in tx.inputs:
        if item.is_coinbase:
            lines.append(f"  {item.index:>3}  coinbase: newly created coins")
            continue
        who = item.source_address or f"(no address form: {printable(item.script_type or '?')})"
        lines.append(
            f"  {item.index:>3}  {who}  {_btc(item.value_raw)}  "
            f"spends {short(item.previous_txid, 10, 6)}:{item.previous_output_index}"
        )
    lines += ["", f"Outputs ({len(tx.outputs)}), {_btc(str(total_out))}:"]
    for output in tx.outputs:
        who = output.destination_address or f"(no address form: {printable(output.script_type)})"
        lines.append(f"  {output.index:>3}  {who}  {_btc(output.value_raw)}  {_spend_text(output)}")

    analysis = analyze(tx)
    inferred: list[str] = []
    if analysis.coinjoin is not None:
        c = analysis.coinjoin
        inferred.append(
            f"CoinJoin ({c.strength}): {c.equal_outputs} outputs of exactly "
            f"{_btc(c.equal_value_raw)} and {c.input_parties} different input addresses"
        )
    if analysis.common_inputs is not None:
        c = analysis.common_inputs
        inferred.append(
            f"{len(c.addresses)} input addresses signed together"
            + (
                ": usually one owner (common-input-ownership)"
                if c.reliable
                else ": NOT a sign of one owner here, this looks like a CoinJoin"
            )
        )
    for item in analysis.change.outputs:
        if item.likelihood is not Likelihood.UNKNOWN:
            label = "change" if item.likelihood is Likelihood.CHANGE else "a payment"
            inferred.append(f"output {item.index} is probably {label}: {'; '.join(item.reasons)}")
    inferred += list(analysis.change.notes)
    if inferred:
        confidence = analysis.change.confidence.value
        lines += ["", f"Inferred, not proven by the chain (change confidence: {confidence}):"]
        lines += [f"  - {text}" for text in inferred]
    if detail.coverage:
        lines.append("")
        lines += [f"  - {note}" for note in detail.coverage]
    return "\n".join(lines)
