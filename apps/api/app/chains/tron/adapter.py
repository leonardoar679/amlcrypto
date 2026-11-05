"""The TRON adapter: the same face as the EVM one, over TronGrid."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.chains.adapter import AccountAdapter, AccountSource
from app.chains.models import TransactionDetail, TransferType
from app.chains.tron.chains import TronChain
from app.chains.tron.normalize import (
    tron_transaction_from_details,
    tron_transfers_from_details,
    txid_of,
)
from app.chains.values import MalformedRow
from app.providers.base import ProviderError
from app.providers.budget import RequestBudget
from app.providers.service import ProviderService
from app.providers.trongrid import TronGridSource


class TronAdapter(AccountAdapter):
    """TRX and TRC-10 transfers come with the transactions (the NATIVE stream: a TRC-10 one says
    so in its type), contracts' TRX movements are INTERNAL, and TRC-20 events are the tokens."""

    token_kind = TransferType.TRC20
    chain: TronChain

    async def get_heights(self) -> tuple[int, int]:
        # History is read with only_confirmed: TRON shows a block once it is solidified, about
        # twenty blocks behind the head. A read that starts now is complete through that block.
        head = await self.get_block_height()
        return head, max(0, head - self.chain.confirmation_depth)

    def _normalize_hash(self, tx_hash: str) -> str:
        try:
            return txid_of(tx_hash)
        except MalformedRow as error:
            raise ValueError("not a transaction id") from error

    async def _detail(
        self, source: str, tx: dict[str, Any], info: dict[str, Any], when: datetime | None
    ) -> TransactionDetail:
        try:
            head = await self.get_block_height()
        except ProviderError:
            head = None  # confirmations stay unknown rather than failing the lookup
        transaction = tron_transaction_from_details(tx, info, self.chain, source, head)
        transfers, unreadable = tron_transfers_from_details(tx, info, self.chain, source, head)
        coverage: list[str] = []
        if unreadable:
            coverage.append(f"{unreadable} Transfer events in a non-TRC-20 shape were not read")
        if any(
            t.asset.decimals is None for t in transfers if t.transfer_type is TransferType.TRC20
        ):
            coverage.append("token symbols and decimals are not in a transaction's details")
        if any(t.transfer_type is TransferType.TRC10 for t in transfers):
            coverage.append("TRC-10 token names and decimals are not in a transaction's details")
        return TransactionDetail(transaction, tuple(transfers), tuple(coverage))


def build_tron_adapter(
    chain: TronChain, providers: ProviderService, *, budget: RequestBudget | None = None
) -> TronAdapter:
    """TronGrid, if it is configured."""
    sources: list[AccountSource] = []
    if providers.client.is_configured("trongrid"):
        sources.append(TronGridSource(providers.client, budget=budget))
    return TronAdapter(chain, sources)
