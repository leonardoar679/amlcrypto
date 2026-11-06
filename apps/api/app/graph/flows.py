"""From stored records to the movements the graph is made of.

Account chains record transfers, which are already movements from one address to another. Bitcoin
records transactions that destroy some outputs and create others, and does not say which input paid
which output, so a Bitcoin movement is a LINK the chain proves (this address was an input and that
one an output of the same transaction) carrying an allocated amount. The graph applies no ownership
heuristic to Bitcoin: change detection, CoinJoin detection and common-input clustering are
inferences, and the graph shows only what is proven.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from app.chains.bitcoin.models import BitcoinTransaction
from app.chains.models import Transfer
from app.graph.model import AssetRef, Basis, Flow

# Why a movement was left out, as the keys of `Flows.ignored`.
ZERO_VALUE = "zero_value"  # a zero-value token event: address poisoning leaves these behind
ROUNDED_TO_ZERO = "rounded_to_zero"  # a Bitcoin share smaller than one satoshi
SELF = "self_transfer"
NO_COUNTERPARTY = "no_counterparty"  # a mint, a burn, a contract creation
OUTSIDE_WINDOW = "outside_window"
COINBASE = "coinbase"  # newly mined coins have no sending address
NO_ADDRESS = "no_address"  # a Bitcoin output with no address form (OP_RETURN, a bare script)


@dataclass(slots=True)
class Flows:
    flows: list[Flow] = field(default_factory=list)
    ignored: Counter[str] = field(default_factory=Counter)


def asset_ref(transfer: Transfer, native_symbol: str | None = None) -> AssetRef:
    asset = transfer.asset
    key = asset.asset_id or asset.contract_address or "native"
    symbol = asset.symbol or (native_symbol if key == "native" else None)
    return AssetRef(key=key, symbol=symbol, decimals=asset.decimals)


def account_flows(
    transfers: Iterable[Transfer],
    address: str,
    *,
    since: datetime | None,
    native_symbol: str | None = None,
) -> Flows:
    """The movements an address took part in. A movement outside the window, from nowhere or to
    nowhere, to itself, or of nothing is not a flow of value between two addresses."""
    result = Flows()
    for transfer in transfers:
        source, target = transfer.from_address, transfer.to_address
        if address not in (source, target):
            continue
        if source is None or target is None:
            result.ignored[NO_COUNTERPARTY] += 1
            continue
        if source == target:
            result.ignored[SELF] += 1
            continue
        amount = int(transfer.amount_raw)
        if amount == 0:
            result.ignored[ZERO_VALUE] += 1
            continue
        if since is not None and transfer.timestamp is not None and transfer.timestamp < since:
            result.ignored[OUTSIDE_WINDOW] += 1
            continue
        result.flows.append(
            Flow(
                chain_id=transfer.chain_id,
                source=source,
                target=target,
                asset=asset_ref(transfer, native_symbol),
                amount=amount,
                timestamp=transfer.timestamp,
                transaction=transfer.transaction_hash,
                event=transfer.event_key,
            )
        )
    return result


def bitcoin_flows(
    transactions: Iterable[BitcoinTransaction], address: str, *, since: datetime | None
) -> Flows:
    """The links between `address` and the other addresses of each transaction it is in.

    When a transaction has one paying address, every output was paid by it and the amount is the
    output's value (`exact`). When it has several, the output's value is shared among them in
    proportion to what each contributed (`allocated`), rounded down to a satoshi, which makes the
    amount the same whichever end of the link it is worked out from. The chain records no more
    than that.
    """
    result = Flows()
    bitcoin = AssetRef(key="native", symbol="BTC", decimals=8)
    for tx in transactions:
        if tx.is_coinbase:
            result.ignored[COINBASE] += 1
            continue
        if since is not None and tx.timestamp is not None and tx.timestamp < since:
            result.ignored[OUTSIDE_WINDOW] += 1
            continue
        paid: Counter[str] = Counter()
        total_in = 0
        every_input_has_an_address = True
        for item in tx.inputs:
            value = int(item.value_raw or 0)
            total_in += value
            if item.source_address is None:
                every_input_has_an_address = False
            else:
                paid[item.source_address] += value
        received: Counter[str] = Counter()
        for out in tx.outputs:
            if out.destination_address is None:
                result.ignored[NO_ADDRESS] += 1
            else:
                received[out.destination_address] += int(out.value_raw)
        if total_in == 0:
            continue
        basis = Basis.EXACT if len(paid) == 1 and every_input_has_an_address else Basis.ALLOCATED

        links: list[tuple[str, str]] = []
        if address in paid:
            links += [(address, other) for other in sorted(received) if other != address]
        if address in received:
            links += [(other, address) for other in sorted(paid) if other != address]
        for source, target in links:
            amount = received[target] * paid[source] // total_in
            if amount == 0:
                result.ignored[ROUNDED_TO_ZERO] += 1
                continue
            result.flows.append(
                Flow(
                    chain_id=tx.chain_id,
                    source=source,
                    target=target,
                    asset=bitcoin,
                    amount=amount,
                    timestamp=tx.timestamp,
                    transaction=tx.txid,
                    event=f"{source}>{target}",
                    basis=basis,
                )
            )
    return result
