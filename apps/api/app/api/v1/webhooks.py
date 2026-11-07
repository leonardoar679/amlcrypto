"""Events a data provider pushes to the platform.

These routes are open to the internet by necessity (the provider is not a user), so they take
nothing on trust: no event is read before its signature is checked, the body is capped, and what the
event says only decides which monitored addresses are checked now (see app.monitoring.webhooks). The
check reads the chain itself.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_settings_dep
from app.api.errors import APIError
from app.core.config import Settings
from app.core.redaction import secrets
from app.monitoring import webhooks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

ALCHEMY_SIGNATURE_HEADER = "x-alchemy-signature"


async def _read_body(request: Request, limit: int) -> bytes:
    """The raw body, refusing one larger than `limit` without reading all of it."""
    too_large = APIError(413, "payload_too_large", "The event is too large.")
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise too_large
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise too_large
        chunks.append(chunk)
    return b"".join(chunks)


def _wake(request: Request, event: webhooks.Event) -> int:
    with request.app.state.write_session_factory() as session:
        return webhooks.wake(session, event)


@router.post("/alchemy", status_code=202)
async def alchemy_address_activity(
    request: Request, settings: Settings = Depends(get_settings_dep)
) -> dict[str, Any]:
    """Alchemy's Address Activity webhook: an address the platform watches had activity. Answers 404
    until ALCHEMY_WEBHOOK_SIGNING_KEY is set, 401 for a body whose signature is wrong."""
    signing_key = settings.alchemy_webhook_signing_key
    if signing_key is None:
        raise APIError(404, "not_found", "Not found.")
    key = signing_key.get_secret_value()
    secrets.add(key)

    body = await _read_body(request, settings.webhook_max_body_bytes)
    if not webhooks.verify_alchemy(body, request.headers.get(ALCHEMY_SIGNATURE_HEADER), key):
        logger.warning(
            "a webhook event with a wrong signature was refused",
            extra={"client": request.client.host if request.client else None},
        )
        raise APIError(401, "invalid_signature", "Invalid signature.")

    try:
        event = webhooks.parse_alchemy(json.loads(body))
    except (ValueError, RecursionError):  # not JSON, or not an address activity event
        raise APIError(422, "invalid_event", "Not an address activity event.") from None

    queued = await run_in_threadpool(_wake, request, event)
    logger.info(
        "webhook event received",
        extra={"chain_id": event.chain_id, "addresses": len(event.addresses), "queued": queued},
    )
    return {"success": True, "queued": queued}
