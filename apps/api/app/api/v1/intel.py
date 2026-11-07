"""Address intelligence over HTTP: what is known about an address, and how current that is."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import Principal, address_read, get_session
from app.api.errors import APIError
from app.intel.lookup import UnknownAddress, lookup, source_statuses
from app.intel.service import with_second_opinion

router = APIRouter(prefix="/intel", tags=["intel"])


class LookupRequest(BaseModel):
    chain: str = Field(max_length=100, description="Chain slug or CAIP-2 id.")
    address: str = Field(max_length=512)


@router.post("/lookup")
async def lookup_address(
    body: LookupRequest,
    request: Request,
    _principal: Principal = Depends(address_read),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Every claim about the address (sanctions listings so far), each with its source, confidence
    and the age of that source, and the same for claims since withdrawn. "Nothing found" comes with
    the sources that were checked, because it is never proof that an address is clean. When a
    Watchman is configured its opinion is added; its being down only shows in that field."""
    try:
        found = await run_in_threadpool(lookup, session, body.chain, body.address)
    except UnknownAddress as error:
        if error.chain_unknown:
            raise APIError(404, "unknown_chain", "That chain is not supported.") from error
        raise APIError(422, "invalid_address", str(error)) from error
    found = await with_second_opinion(found, request.app.state.watchman)
    return found.to_dict()


@router.get("/sources")
def label_sources(
    _principal: Principal = Depends(address_read),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """Each source of labels, when its data was published and last confirmed current, and whether
    that counts as fresh. Makes no network request."""
    return [status.to_dict() for status in source_statuses(session)]
