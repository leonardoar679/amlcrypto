"""Who may do what.

Roles and the permissions they carry are fixed here, in code, so what a role means is reviewed like
any other change and tested exhaustively (tests/test_access_control.py walks every route against
every role). A role is a person's job: a viewer reads, an analyst works with screenings, monitoring
and cases, an admin also manages people, keys and the audit log.

Every route under /api/v1 names the permission it needs with `require(...)` (app.api.deps), or is
listed as public or as open to any signed-in user in tests/test_access_control.py. A route added
without one fails that test.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class Permission(StrEnum):
    SCREENING_READ = "screening:read"  # reports, the history of screenings, jobs
    SCREENING_RUN = "screening:run"  # ask for a screening
    ADDRESS_READ = "address:read"  # what is stored about an address, its labels, its graph
    GRAPH_UPDATE = "graph:update"  # read the histories a graph is missing (provider requests)
    PROVIDERS_READ = "providers:read"  # which providers are configured and what they were used for
    PROVIDERS_PROBE = "providers:probe"  # spend a provider request on a health check
    MONITORING_READ = "monitoring:read"  # which addresses are watched, and how they are doing
    MONITORING_WRITE = "monitoring:write"  # watch an address, change how, stop, ask for a check now
    ALERTS_READ = "alerts:read"
    ALERTS_MANAGE = "alerts:manage"  # acknowledge, assign, close and reopen alerts
    CASES_READ = "cases:read"
    CASES_WRITE = "cases:write"  # open, work and document cases
    USERS_MANAGE = "users:manage"
    API_KEYS_MANAGE = "apikeys:manage"
    AUDIT_READ = "audit:read"
    INCIDENTS_CURATE = "incidents:curate"  # write the incidents everybody can read (editors only)


_VIEWER = frozenset(
    {
        Permission.SCREENING_READ,
        Permission.ADDRESS_READ,
        Permission.MONITORING_READ,
        Permission.ALERTS_READ,
        Permission.CASES_READ,
    }
)
_ANALYST = _VIEWER | {
    Permission.SCREENING_RUN,
    Permission.GRAPH_UPDATE,
    Permission.PROVIDERS_READ,
    Permission.MONITORING_WRITE,
    Permission.ALERTS_MANAGE,
    Permission.CASES_WRITE,
}
_ADMIN = _ANALYST | {
    Permission.PROVIDERS_PROBE,
    Permission.USERS_MANAGE,
    Permission.API_KEYS_MANAGE,
    Permission.AUDIT_READ,
}

ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = {
    Role.VIEWER: _VIEWER,
    Role.ANALYST: frozenset(_ANALYST),
    Role.ADMIN: frozenset(_ADMIN),
}

# An API key is a credential for a program. Whatever its role, it can never manage people or keys:
# a leaked key must not be able to mint more keys or promote an account.
KEY_NEVER: frozenset[Permission] = frozenset({Permission.USERS_MANAGE, Permission.API_KEYS_MANAGE})


# What no role holds: a person is given it (users.is_platform_editor, set from the command line),
# because any administrator can make a person of any role and so could make an editor. It is added
# to a signed-in person in app.api.deps and never to an API key.
EDITOR_ONLY: frozenset[Permission] = frozenset({Permission.INCIDENTS_CURATE})


class UnknownRole(ValueError):
    pass


def parse_role(value: str) -> Role:
    try:
        return Role(value)
    except ValueError:
        raise UnknownRole(f"{value!r} is not a role (admin, analyst or viewer)") from None


def permissions_for(role: Role | str, *, api_key: bool = False) -> frozenset[Permission]:
    """What a role may do; for an API key, minus what a key may never do."""
    granted = ROLE_PERMISSIONS[parse_role(role) if isinstance(role, str) else role]
    return granted - KEY_NEVER if api_key else granted


def can_grant(creator: Role, wanted: Role) -> bool:
    """A key (or an account) may not be given more than the person giving it has."""
    return ROLE_PERMISSIONS[wanted] <= ROLE_PERMISSIONS[creator]
