"""The EVM adapter: one object that answers for an EVM chain, whichever sources are configured.

Routing, fallback and cursors are shared with the other chains (app.chains.adapter). What is EVM
here is how a hash looks and how a receipt is read.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.chains.adapter import AccountAdapter, AccountSource
from app.chains.evm.chains import EvmChain
from app.chains.evm.normalize import (
    MalformedRow,
    hash_of,
    transaction_from_rpc,
    transfers_from_receipt,
)
from app.chains.models import TransactionDetail, TransferType
from app.providers.alchemy import AlchemySource
from app.providers.base import ProviderError
from app.providers.budget import RequestBudget
from app.providers.etherscan import EtherscanSource
from app.providers.service import ProviderService


class EvmAdapter(AccountAdapter):
    token_kind = TransferType.ERC20
    chain: EvmChain

    def _normalize_hash(self, tx_hash: str) -> str:
        try:
            return hash_of(tx_hash)
        except MalformedRow as error:
            raise ValueError("not a transaction hash") from error

    async def _detail(
        self, source: str, tx: dict[str, Any], receipt: dict[str, Any], when: datetime | None
    ) -> TransactionDetail:
        try:
            head = await self.get_block_height()
        except ProviderError:
            head = None  # confirmations stay unknown rather than failing the lookup
        transaction = transaction_from_rpc(tx, receipt, self.chain, when, source, head)
        transfers, unreadable = transfers_from_receipt(tx, receipt, self.chain, when, source, head)
        coverage = ["internal coin movements are not part of a receipt; see the address history"]
        if unreadable:
            coverage.append(f"{unreadable} Transfer events in a non-ERC-20 shape were not read")
        if any(
            t.asset.decimals is None for t in transfers if t.transfer_type is TransferType.ERC20
        ):
            coverage.append("token symbols and decimals are not in a receipt")
        return TransactionDetail(transaction, tuple(transfers), tuple(coverage))


def build_evm_adapter(
    chain: EvmChain,
    providers: ProviderService,
    *,
    etherscan_paid_plan: bool = False,
    budget: RequestBudget | None = None,
) -> EvmAdapter:
    """The sources that are configured and can serve this chain, the richest first."""
    client = providers.client
    sources: list[AccountSource] = []

    etherscan = providers.spec("etherscan")
    serves = chain.chain_id in etherscan.chains or (
        etherscan_paid_plan and chain.chain_id in etherscan.paid_chains
    )
    if client.is_configured("etherscan") and serves:
        sources.append(EtherscanSource(client, budget=budget))

    if client.is_configured("alchemy") and chain.chain_id in providers.spec("alchemy").chains:
        sources.append(AlchemySource(client, budget=budget))

    return EvmAdapter(chain, sources)
