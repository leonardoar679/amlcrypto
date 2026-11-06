"""Putting a history into the database the way a sync leaves it, without running a sync."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.chains.models import Asset, DataQuality, StreamStatus, Transfer, TransferType
from app.database.repositories.acquisition import Coverage, save_coverage
from app.database.repositories.transfers import save_transfers

ETHEREUM = "eip155:1"
_numbers = itertools.count(1)


def move(sender: str, to: str, amount: str = "1000", *, days_ago: float = 5.0) -> Transfer:
    """A native transfer that happened `days_ago` days before now."""
    number = next(_numbers)
    return Transfer(
        chain_id=ETHEREUM,
        transaction_hash=f"0x{number:064x}",
        event_key="native",
        transfer_type=TransferType.NATIVE,
        block_number=1_000 + number,
        timestamp=datetime.now(UTC) - timedelta(days=days_ago),
        from_address=sender,
        to_address=to,
        asset=Asset(symbol="ETH", contract_address=None, decimals=18, name="Ether"),
        amount_raw=amount,
        provider="etherscan",
        data_quality=DataQuality.INDEXED,
    )


def seed(
    session: Session,
    address: str,
    transfers: list[Transfer],
    *,
    status: StreamStatus = StreamStatus.COMPLETE,
    streams: tuple[str, ...] = ("account", "internal", "erc20"),
) -> None:
    """Store `transfers` and mark `address` as read, each stream with `status`."""
    save_transfers(session, transfers)
    save_coverage(
        session,
        ETHEREUM,
        address,
        [
            Coverage(
                stream=stream,
                status=status.value,
                synced_at=datetime.now(UTC),
                synced_through_block=2_000 if status is StreamStatus.COMPLETE else None,
                resume_cursor=None if status is StreamStatus.COMPLETE else "",
            )
            for stream in streams
        ],
    )
    session.commit()
