"""A request that works for every route.

Each entry builds, from a source of records (tests.world.Fresh makes real ones, Ghost names ones
that do not exist), the request a person would send to that route. The tenant-isolation tests send
them from the wrong organization; the hostile-input sweep changes one value of each at a time. A
route with no entry fails tests/test_catalog.py, so a new endpoint cannot escape either.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tests.world import Call

V1 = "/api/v1"

Build = Callable[[Any], Call]
CATALOG: dict[tuple[str, str], Build] = {}


def add(
    method: str,
    template: str,
    *,
    path: Callable[[Any], str] | None = None,
    json: Callable[[Any], Any] | None = None,
    params: Callable[[Any], dict[str, Any]] | None = None,
) -> None:
    """Register the working request for `method template` (the template as the router has it,
    without the /api/v1 prefix)."""

    def build(source: Any) -> Call:
        return Call(
            method,
            V1 + (path(source) if path else template),
            json(source) if json else None,
            params(source) if params else None,
        )

    CATALOG[(method, V1 + template)] = build


def incident_document(source: Any) -> dict[str, Any]:
    """The content of an incident, as the editor sends it."""
    return {
        "title": "An example incident",
        "summary": "Funds were reported taken from a wallet.",
        "kind": "theft",
        "occurred_on": "2026-01-15",
        "evidence": [
            {
                "ref": "e1",
                "publisher": "An analytics firm",
                "title": "A report",
                "url": "https://example.org/report",
                "kind": "analytics",
                "retrieved_on": "2026-01-16",
            }
        ],
        "amounts": [{"evidence_ref": "e1", "amount_usd_low": 1000}],
        "addresses": [
            {
                "chain": "ethereum",
                "address": source.address(),
                "role": "ATTACKER_FUNDS_RECEIVER",
                "attribution_status": "REPORTED_BY_ANALYST",
                "evidence_ref": "e1",
            }
        ],
        "updates": [
            {"occurred_at": "2026-01-15T10:00:00Z", "kind": "event", "body": "It was noticed."}
        ],
    }


def link_path(source: Any) -> str:
    case = source.case()
    return f"/cases/{case}/links/{source.link(case)}"


# --- open to anyone -----------------------------------------------------------------------------
add("GET", "/health")
add("GET", "/version")
add("POST", "/auth/login", json=lambda s: s.credentials())

# --- about the caller ---------------------------------------------------------------------------
add("GET", "/auth/me")
add(
    "POST",
    "/auth/password",
    json=lambda s: {"current_password": "not the password", "new_password": "Another password 1!"},
)
add("GET", "/chains")
add("POST", "/address/validate", json=lambda s: {"address": s.address(), "chain": "ethereum"})

# --- chain data and what is known about it (the same for every organization) --------------------
add(
    "GET",
    "/address/{chain}/{address}",
    path=lambda s: f"/address/ethereum/{s.known_address()}",
)
add(
    "GET",
    "/address/{chain}/{address}/labels",
    path=lambda s: f"/address/ethereum/{s.address()}/labels",
)
add(
    "GET",
    "/address/{chain}/{address}/transactions",
    path=lambda s: f"/address/ethereum/{s.address()}/transactions",
)
add("GET", "/graph", params=lambda s: {"chain": "ethereum", "address": s.address()})
add("POST", "/intel/lookup", json=lambda s: {"chain": "ethereum", "address": s.address()})
add("GET", "/intel/sources")
add("GET", "/providers")
add("POST", "/providers/{name}/probe", path=lambda s: "/providers/nope/probe")
add("GET", "/risk/rules")

# --- screening (each organization's own) --------------------------------------------------------
add(
    "GET",
    "/address/{chain}/{address}/risk",
    path=lambda s: f"/address/ethereum/{s.known_address()}/risk",
)
add(
    "GET",
    "/address/{chain}/{address}/risk/history",
    path=lambda s: f"/address/ethereum/{s.known_address()}/risk/history",
)
add(
    "POST",
    "/screen/address",
    json=lambda s: {"chain": "ethereum", "address": s.address(), "options": {"update": False}},
)
add("POST", "/screen/transaction", json=lambda s: {"chain": "ethereum", "hash": "0x" + "ab" * 32})
add("GET", "/screen/{screening}", path=lambda s: f"/screen/{s.screening()}")
add(
    "GET",
    "/screen/{screening}/export",
    path=lambda s: f"/screen/{s.screening()}/export",
    params=lambda s: {"format": "json"},
)
add("GET", "/screenings")
add(
    "POST",
    "/graph/update",
    json=lambda s: {"chain": "ethereum", "address": s.address()},
)
add("GET", "/jobs/{job_id}", path=lambda s: f"/jobs/{s.job()}")

# --- monitoring and alerts ----------------------------------------------------------------------
add("GET", "/monitoring/addresses")
add(
    "POST",
    "/monitoring/addresses",
    json=lambda s: {"chain": "ethereum", "address": s.address(), "name": "Watched"},
)
add(
    "GET",
    "/monitoring/addresses/{monitor_id}",
    path=lambda s: f"/monitoring/addresses/{s.monitor()}",
)
add(
    "PATCH",
    "/monitoring/addresses/{monitor_id}",
    path=lambda s: f"/monitoring/addresses/{s.monitor()}",
    json=lambda s: {"name": "Renamed"},
)
add(
    "DELETE",
    "/monitoring/addresses/{monitor_id}",
    path=lambda s: f"/monitoring/addresses/{s.monitor()}",
)
add(
    "POST",
    "/monitoring/addresses/{monitor_id}/check",
    path=lambda s: f"/monitoring/addresses/{s.monitor()}/check",
)
add("GET", "/alerts")
add("GET", "/alerts/summary")
add("GET", "/alerts/{alert_id}", path=lambda s: f"/alerts/{s.alert()}")
add(
    "PATCH",
    "/alerts/{alert_id}",
    path=lambda s: f"/alerts/{s.alert()}",
    json=lambda s: {"status": "acknowledged", "note": "Looking"},
)

# --- cases --------------------------------------------------------------------------------------
add("GET", "/cases")
add("GET", "/cases/summary")
add("POST", "/cases", json=lambda s: {"title": "A new case", "priority": "high"})
add("GET", "/cases/{case_id}", path=lambda s: f"/cases/{s.case()}")
add(
    "PATCH",
    "/cases/{case_id}",
    path=lambda s: f"/cases/{s.case()}",
    json=lambda s: {"title": "A better title", "status": "in_progress"},
)
add("GET", "/cases/{case_id}/export", path=lambda s: f"/cases/{s.case()}/export")
add(
    "POST",
    "/cases/{case_id}/notes",
    path=lambda s: f"/cases/{s.case()}/notes",
    json=lambda s: {"body": "A note"},
)
add(
    "POST",
    "/cases/{case_id}/links",
    path=lambda s: f"/cases/{s.case()}/links",
    json=lambda s: {"kind": "address", "chain": "ethereum", "reference": s.address()},
)
add("DELETE", "/cases/{case_id}/links/{link_id}", path=link_path)

# --- people, keys, the audit log ----------------------------------------------------------------
add("GET", "/users")
add(
    "POST",
    "/users",
    json=lambda s: {
        "email": f"new-{s.address()[-6:]}@example.com",
        "password": "x" * 12,
        "role": "viewer",
    },
)
add(
    "PATCH",
    "/users/{user_id}",
    path=lambda s: f"/users/{s.user()}",
    json=lambda s: {"role": "analyst"},
)
add(
    "POST",
    "/users/{user_id}/password",
    path=lambda s: f"/users/{s.user()}/password",
    json=lambda s: {"new_password": "A fresh password 1!"},
)
add("GET", "/api-keys")
add("POST", "/api-keys", json=lambda s: {"name": "ci", "role": "viewer", "expires_in_days": 30})
add("DELETE", "/api-keys/{key_id}", path=lambda s: f"/api-keys/{s.key()}")
add("GET", "/audit")

# --- the platform editor's incidents (global; Acme's administrator is the editor) ----------------
add("GET", "/editor/incidents")
add(
    "POST",
    "/editor/incidents",
    json=lambda s: {"slug": f"example-{s.address()[-8:]}", **incident_document(s)},
)
add(
    "GET",
    "/editor/incidents/{incident_id}",
    path=lambda s: f"/editor/incidents/{s.incident()}",
)
add(
    "PUT",
    "/editor/incidents/{incident_id}/content",
    path=lambda s: f"/editor/incidents/{s.incident()}/content",
    json=incident_document,
)
add(
    "POST",
    "/editor/incidents/{incident_id}/transition",
    path=lambda s: f"/editor/incidents/{s.incident()}/transition",
    json=lambda s: {"to": "in_review"},
)
add(
    "POST",
    "/editor/incidents/{incident_id}/updates",
    path=lambda s: f"/editor/incidents/{s.incident()}/updates",
    json=lambda s: {"occurred_at": "2026-01-16T10:00:00Z", "kind": "statement", "body": "Said."},
)
add("DELETE", "/editor/incidents/{incident_id}", path=lambda s: f"/editor/incidents/{s.incident()}")

# --- incidents, for anybody (published only) ----------------------------------------------------
add("GET", "/public/incidents")
add("GET", "/public/incidents/{slug}", path=lambda s: f"/public/incidents/{s.published_slug()}")
add(
    "GET",
    "/public/addresses/{chain}/{address}/incidents",
    path=lambda s: f"/public/addresses/ethereum/{s.address()}/incidents",
)

# --- from the provider, not from a person -------------------------------------------------------
add(
    "POST",
    "/webhooks/alchemy",
    json=lambda s: {"webhookId": "wh_1", "id": "whevt_1", "type": "ADDRESS_ACTIVITY", "event": {}},
)
