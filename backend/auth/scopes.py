ADMIN_SCOPES = {
    "users:read",
    "users:write",
    "config:read",
    "config:write",
    "nets:read",
    "nets:write",
    "nets:members:write",
}
PER_NET_SCOPES = {
    "schedule:read",
    "schedule:write",
    "checkins:read",
    "checkins:write",
    "roster:read",
    "map:read",
}
SCOPE_NAMES: set[str] = ADMIN_SCOPES | PER_NET_SCOPES

# Keep SCOPES dict for backward-compat with PAT route UI (description strings).
# Keys are scope names; values have a "description" field.
SCOPES: dict[str, dict] = {
    "schedule:read": {"description": "View sessions"},
    "schedule:write": {"description": "Create/edit/delete sessions"},
    "checkins:read": {"description": "View check-in data"},
    "checkins:write": {"description": "Submit/manage check-ins"},
    "roster:read": {"description": "View roster data"},
    "map:read": {"description": "View map/GeoJSON data"},
    "users:read": {"description": "List users"},
    "users:write": {"description": "Manage users/roles"},
    "config:read": {"description": "View app configuration"},
    "config:write": {"description": "Modify app configuration"},
    "nets:read": {"description": "List nets"},
    "nets:write": {"description": "Create/edit nets"},
    "nets:members:write": {"description": "Manage net memberships"},
}


def validate_pat_scopes(scopes: list[str], is_admin: bool, net_id: int | None) -> None:
    """Validate scopes for a PAT.

    ``net_id`` is optional: if provided, the token is scoped to a specific net
    and ``require_net_role`` will enforce cross-net isolation.  When ``None``
    the token is valid for any net the user is a member of (or all nets if the
    user is an admin).
    """
    if not scopes:
        raise ValueError("Token must have at least one scope")
    for s in scopes:
        if s not in SCOPE_NAMES:
            raise ValueError(f"Unknown scope: {s}")
        if s in ADMIN_SCOPES and not is_admin:
            raise ValueError(f"Only admins can issue scope: {s}")
