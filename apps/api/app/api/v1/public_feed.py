"""The news feed and the hot addresses, for anybody: no sign-in, read only, and only what editors
have published.

Like the incidents and the wallet profiles, this answers without knowing who asks, so it is
deliberately small: two reads, every field written out in app.feed.public, nothing from the shared
chain data, nothing an organization owns, and nothing that counts who looked at what. Answers may be
kept by a cache for a minute; errors never are.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.errors import APIError
from app.chains.support import SUPPORTED, find_chain
from app.feed import public
from app.feed.model import (
    DEFAULT_WINDOW,
    FEED_NOTICE,
    HOT_NOTICE,
    MAX_FEED_OFFSET,
    MAX_HOT_OFFSET,
    WINDOWS,
    FeedKind,
)

router = APIRouter(prefix="/public", tags=["public"])

CACHE_CONTROL = "public, max-age=60"


@router.get("/feed")
def feed(
    response: Response,
    kind: Annotated[FeedKind | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=MAX_FEED_OFFSET)] = 0,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """What editors have published, the newest first: new incidents, what happened next to the ones
    already published (a correction is one), and new wallet profiles."""
    items, total = public.events(
        session, kind=None if kind is None else kind.value, limit=limit, offset=offset
    )
    response.headers["Cache-Control"] = CACHE_CONTROL
    return {
        "success": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
        "notice": FEED_NOTICE,
    }


@router.get("/hot")
def hot_addresses(
    response: Response,
    window: Annotated[
        int, Query(ge=min(WINDOWS), le=max(WINDOWS), description="Days: 7, 30 or 90.")
    ] = DEFAULT_WINDOW,
    chain: Annotated[str | None, Query(max_length=100, description="A chain slug.")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=MAX_HOT_OFFSET)] = 0,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """The addresses that sources report most firmly and most recently, from published incidents
    only. Not a finding about anybody, and not a measure of attention."""
    if window not in WINDOWS:
        raise APIError(422, "invalid_window", f"The window is {', '.join(map(str, WINDOWS))} days.")
    chain_id = None
    if chain is not None:
        found = find_chain(chain)
        if found is None:
            raise APIError(422, "unknown_chain", f"Chains are: {', '.join(SUPPORTED)}.")
        chain_id = found.chain_id
    items, total = public.hot(session, window=window, chain_id=chain_id, limit=limit, offset=offset)
    response.headers["Cache-Control"] = CACHE_CONTROL
    return {
        "success": True,
        "window_days": window,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
        "notice": HOT_NOTICE,
    }
