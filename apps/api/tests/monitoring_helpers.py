"""Monitored addresses and alerts, made directly in the database for the tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.chains.support import find_chain
from app.core.config import Settings
from app.database.models import Alert, MonitoredAddress
from app.monitoring import service
from app.monitoring.detect import AlertDraft

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
ETHEREUM_ID = "eip155:1"
ADDRESS = "0x" + "ab" * 20
OTHER_ADDRESS = "0x" + "cd" * 20


def watch(
    session,
    settings: Settings,
    user,
    *,
    address: str = ADDRESS,
    chain: str = "ethereum",
    name: str | None = None,
    interval: int | None = None,
    depth: int | None = None,
    min_severity: str | None = None,
    now: datetime = NOW,
) -> MonitoredAddress:
    """Start monitoring an address for the user's organization (what the API does, minus the
    first queued check) and commit."""
    row = service.register(
        session,
        settings=settings,
        organization_id=user.organization_id,
        created_by=user.id,
        chain=find_chain(chain),
        address=address,
        name=name,
        check_interval_seconds=interval,
        depth=depth,
        min_severity=min_severity,
        now=now,
    )
    session.commit()
    return row


def alert_for(
    session, monitor: MonitoredAddress, *, key: str | None = None, **fields: Any
) -> Alert:
    """An alert on a monitor, with whatever the test wants to differ from the usual."""
    values: dict[str, Any] = {
        "organization_id": monitor.organization_id,
        "monitored_address_id": monitor.id,
        "chain_id": monitor.chain_id,
        "address": monitor.address,
        "kind": "new_finding",
        "severity": "HIGH",
        "status": "open",
        "title": "1 new finding on the address",
        "summary": "1 new finding.\nAML-001: Funds from a sanctioned address",
        "evidence": {"reasons": [{"code": "new_finding", "detail": "1 new finding"}]},
        **fields,
    }
    values.setdefault("dedupe_key", key or f"{monitor.id}:{values['kind']}:{values['severity']}")
    alert = Alert(**values)
    session.add(alert)
    session.commit()
    return alert


def draft(kind: str = "new_finding", severity: str = "HIGH") -> AlertDraft:
    from app.monitoring.detect import Snapshot

    return AlertDraft(
        kind=kind,
        severity=severity,
        title="A title",
        summary="A summary",
        reasons=(),
        findings=(),
        previous=None,
        current=Snapshot(None, None, frozenset()),
    )
