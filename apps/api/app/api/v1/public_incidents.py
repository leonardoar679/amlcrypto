"""Incidents, for anybody: no sign-in, read only, and only what has been published.

This is the one part of the API that answers without knowing who asks, so it is deliberately
small: three reads, every field written out in app.incidents.public, nothing from the shared chain
data and nothing an organization owns. A draft, an incident in review and an archived one are not
found, in the same way as one that does not exist. Answers may be kept by a cache for a minute;
errors never are.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.errors import APIError
from app.chains.support import SUPPORTED, find_chain
from app.graph.service import GraphRequestError, resolve
from app.incidents import public
from app.incidents.model import NOTHING_FOUND_NOTICE, PUBLIC_NOTICE, SLUG_PATTERN, IncidentKind

router = APIRouter(prefix="/public", tags=["public"])

CACHE_CONTROL = "public, max-age=60"


@router.get("/incidents")
def list_incidents(
    response: Response,
    kind: Annotated[IncidentKind | None, Query()] = None,
    chain: Annotated[str | None, Query(max_length=100, description="A chain slug.")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Published incidents, the most recent first."""
    chain_id = None
    if chain is not None:
        found = find_chain(chain)
        if found is None:
            raise APIError(422, "unknown_chain", f"Chains are: {', '.join(SUPPORTED)}.")
        chain_id = found.chain_id
    items, total = public.list_published(
        session,
        kind=None if kind is None else kind.value,
        chain_id=chain_id,
        limit=limit,
        offset=offset,
    )
    response.headers["Cache-Control"] = CACHE_CONTROL
    return {
        "success": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
        "notice": PUBLIC_NOTICE,
    }


@router.get("/incidents/{slug}")
def get_incident(
    response: Response,
    slug: Annotated[str, Path(min_length=3, max_length=80, pattern=SLUG_PATTERN)],
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """One published incident: the story, what it rests on, and who reports what."""
    incident = public.get_published(session, slug)
    if incident is None:
        raise APIError(404, "incident_not_found", "No such incident.")
    response.headers["Cache-Control"] = CACHE_CONTROL
    return {"success": True, "incident": public.detail(session, incident)}


@router.get("/addresses/{chain}/{address}/incidents")
def incidents_naming_an_address(
    response: Response,
    chain: Annotated[str, Path(max_length=100)],
    address: Annotated[str, Path(max_length=512)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """The published incidents that name an address, and the part it plays in each."""
    try:
        found, normalized = resolve(chain, address)
    except GraphRequestError as error:
        if error.chain_unknown:
            raise APIError(404, "unknown_chain", "That chain is not supported.") from error
        raise APIError(422, "invalid_address", str(error)) from error
    incidents = public.naming(session, found.chain_id, normalized, limit=limit)
    response.headers["Cache-Control"] = CACHE_CONTROL
    return {
        "success": True,
        "chain": found.slug,
        "address": normalized,
        "incidents": incidents,
        "notice": PUBLIC_NOTICE if incidents else NOTHING_FOUND_NOTICE,
    }
