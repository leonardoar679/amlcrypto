from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    address_data,
    addresses,
    auth,
    chains,
    graph,
    health,
    intel,
    jobs,
    providers,
    screening,
    version,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(version.router)
api_router.include_router(auth.router)
api_router.include_router(chains.router)
api_router.include_router(addresses.router)
api_router.include_router(address_data.router)
api_router.include_router(providers.router)
api_router.include_router(intel.router)
api_router.include_router(graph.router)
api_router.include_router(screening.router)
api_router.include_router(jobs.router)
