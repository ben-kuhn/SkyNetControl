from typing import TypedDict

from backend.auth.models import UserRole


class ScopeEntry(TypedDict):
    description: str
    min_role: UserRole


# Scope name → minimum role required to create a token with this scope.
# Role hierarchy: ADMIN > NET_CONTROL > VIEWER > PENDING
_ROLE_RANK: dict[UserRole, int] = {
    UserRole.PENDING: 0,
    UserRole.VIEWER: 1,
    UserRole.NET_CONTROL: 2,
    UserRole.ADMIN: 3,
}

SCOPES: dict[str, ScopeEntry] = {
    "schedule:read":  {"description": "View sessions",               "min_role": UserRole.VIEWER},
    "schedule:write": {"description": "Create/edit/delete sessions", "min_role": UserRole.NET_CONTROL},
    "checkins:read":  {"description": "View check-in data",          "min_role": UserRole.VIEWER},
    "checkins:write": {"description": "Submit/manage check-ins",     "min_role": UserRole.NET_CONTROL},
    "roster:read":    {"description": "View roster data",            "min_role": UserRole.NET_CONTROL},
    "map:read":       {"description": "View map/GeoJSON data",       "min_role": UserRole.VIEWER},
    "users:read":     {"description": "List users",                  "min_role": UserRole.ADMIN},
    "users:write":    {"description": "Manage users/roles",          "min_role": UserRole.ADMIN},
    "config:read":    {"description": "View app configuration",      "min_role": UserRole.ADMIN},
    "config:write":   {"description": "Modify app configuration",    "min_role": UserRole.ADMIN},
}

SCOPE_NAMES: set[str] = set(SCOPES.keys())


def validate_scopes_for_role(scopes: list[str], role: UserRole) -> None:
    if not scopes:
        raise ValueError("Token must have at least one scope")

    user_rank = _ROLE_RANK[role]
    for scope in scopes:
        if scope not in SCOPES:
            raise ValueError(f"Unknown scope: {scope}")
        required_rank = _ROLE_RANK[SCOPES[scope]["min_role"]]
        if user_rank < required_rank:
            raise ValueError(
                f"Your role cannot use scope: {scope}"
            )
