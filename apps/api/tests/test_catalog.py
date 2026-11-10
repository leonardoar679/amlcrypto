"""Every route has a working request in tests/catalog.py, and the catalogue names no dead route.

The tenant-isolation tests and the hostile-input sweep are built from the catalogue, so a route
that is not in it would escape both.
"""

from __future__ import annotations

from tests.catalog import CATALOG
from tests.test_access_control import api_routes


def test_every_route_has_a_working_request_and_every_request_a_route(app):
    routes = {(method, path) for method, path, _ in api_routes(app)}
    assert len(routes) >= 30, f"only {len(routes)} routes found"
    assert routes - set(CATALOG) == set(), "routes with no request in tests/catalog.py"
    assert set(CATALOG) - routes == set(), "requests in tests/catalog.py for routes that are gone"
