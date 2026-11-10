from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    address_data,
    addresses,
    alerts,
    api_keys,
    audit,
    auth,
    cases,
    chains,
    editor_incidents,
    graph,
    health,
    intel,
    jobs,
    monitoring,
    providers,
    public_incidents,
    screening,
    users,
    version,
    webhooks,
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
api_router.include_router(monitoring.router)
api_router.include_router(alerts.router)
api_router.include_router(cases.router)
api_router.include_router(webhooks.router)
api_router.include_router(users.router)
api_router.include_router(api_keys.router)
api_router.include_router(audit.router)
api_router.include_router(editor_incidents.router)
api_router.include_router(public_incidents.router)
