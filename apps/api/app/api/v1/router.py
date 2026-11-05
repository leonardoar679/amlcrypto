from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import addresses, auth, chains, health, version

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(version.router)
api_router.include_router(auth.router)
api_router.include_router(chains.router)
api_router.include_router(addresses.router)
