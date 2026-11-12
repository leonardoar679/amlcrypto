"""Wallet profiles, for anybody: no sign-in, read only, and only what has been published.

Like the incidents (public_incidents), this is part of the API that answers without knowing who
asks, so it is deliberately small: three reads, every field written out in app.wallets.public,
nothing from the shared chain data and nothing an organization owns. A draft, a profile in review
and an archived one are not found, in the same way as one that does not exist. Answers may be kept
by a cache for a minute; errors never are.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.errors import APIError
from app.chains.support import SUPPORTED, find_chain
from app.editorial import SLUG_PATTERN
from app.graph.service import GraphRequestError, resolve
from app.wallets import public
from app.wallets.model import NOTHING_FOUND_NOTICE, PUBLIC_NOTICE, WalletKind

router = APIRouter(prefix="/public", tags=["public"])

CACHE_CONTROL = "public, max-age=60"


@router.get("/wallets")
def list_wallets(
    response: Response,
    kind: Annotated[WalletKind | None, Query()] = None,
    chain: Annotated[str | None, Query(max_length=100, description="A chain slug.")] = None,
    q: Annotated[str | None, Query(max_length=100, description="Part of a name.")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Published wallet profiles, by name."""
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
        name=(q or "").strip() or None,
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


@router.get("/wallets/{slug}")
def get_wallet(
    response: Response,
    slug: Annotated[str, Path(min_length=3, max_length=80, pattern=SLUG_PATTERN)],
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """One published profile: who it is, what it rests on, and which addresses it names."""
    profile = public.get_published(session, slug)
    if profile is None:
        raise APIError(404, "wallet_not_found", "No such wallet profile.")
    response.headers["Cache-Control"] = CACHE_CONTROL
    return {"success": True, "wallet": public.detail(session, profile)}


@router.get("/addresses/{chain}/{address}/wallets")
def wallets_naming_an_address(
    response: Response,
    chain: Annotated[str, Path(max_length=100)],
    address: Annotated[str, Path(max_length=512)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """The published profiles that name an address, and what it is in each."""
    try:
        found, normalized = resolve(chain, address)
    except GraphRequestError as error:
        if error.chain_unknown:
            raise APIError(404, "unknown_chain", "That chain is not supported.") from error
        raise APIError(422, "invalid_address", str(error)) from error
    profiles = public.naming(session, found.chain_id, normalized, limit=limit)
    response.headers["Cache-Control"] = CACHE_CONTROL
    return {
        "success": True,
        "chain": found.slug,
        "address": normalized,
        "wallets": profiles,
        "notice": PUBLIC_NOTICE if profiles else NOTHING_FOUND_NOTICE,
    }
