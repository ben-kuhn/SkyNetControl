from dataclasses import dataclass
from typing import Callable, Union

from fastapi import Cookie, Depends, Header, HTTPException, Path, Request
from sqlalchemy.orm import Session

from backend.auth.models import User
from backend.auth.pat_service import authenticate_token
from backend.auth.recovery import RecoveryPrincipal, decode_recovery_token
from backend.auth.service import decode_access_token
from backend.config import Settings
from backend.modules.nets.models import Net, NetMembership, NetRole


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db_session(request: Request):
    session_factory = request.app.state.session_factory
    with session_factory() as session:
        yield session


def get_current_user(
    request: Request,
    access_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db_session),
    app_settings: Settings = Depends(get_settings),
) -> User:
    request.state.token_scopes = None
    request.state.token_net_id = None
    # Try Bearer token first
    if authorization and authorization.startswith("Bearer skynet_"):
        raw_token = authorization[len("Bearer ") :]
        auth_result = authenticate_token(db, raw_token)
        if auth_result is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user = db.get(User, auth_result["user_callsign"])
        if user is None or user.is_pending or user.is_deleted:
            raise HTTPException(status_code=401, detail="User not found or pending")

        request.state.token_scopes = auth_result["scopes"]
        request.state.token_net_id = auth_result["net_id"]
        return user

    # Fall back to cookie JWT
    if not access_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_access_token(access_token, settings=app_settings)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    callsign = payload.get("sub")
    if not callsign:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user = db.get(User, callsign)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    if user.is_deleted:
        raise HTTPException(status_code=401, detail="Account has been deleted")

    # Invalidate tokens issued before logout / role-change / delete bumped
    # users.token_version. Tokens without a `tv` claim (legacy from before
    # this rolled out) compare as 0, which only matches users still at the
    # default token_version=0 — i.e. those who have never logged out, never
    # had a role change, never had a forced invalidation. Once their tv
    # bumps past 0, legacy tokens stop working.
    if payload.get("tv", 0) != user.token_version:
        raise HTTPException(status_code=401, detail="Token has been invalidated")

    return user


def get_optional_user(
    request: Request,
    access_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db_session),
    app_settings: Settings = Depends(get_settings),
) -> User | None:
    """Like get_current_user, but returns None instead of raising 401.

    Used by routes that have both authenticated and anonymous behavior.
    DELETED users are treated as anonymous.
    """
    try:
        return get_current_user(
            request=request,
            access_token=access_token,
            authorization=authorization,
            db=db,
            app_settings=app_settings,
        )
    except HTTPException:
        return None


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Gate: caller must be a global admin."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin required")
    return user


def require_net_member(user: User = Depends(get_current_user), db: Session = Depends(get_db_session)) -> User:
    """Gate: caller must be a global admin or have any net membership.

    Temporary bridge for routes that previously accepted ADMIN or NET_CONTROL
    roles; once the routes are moved under /api/nets/{slug}/ in Task 5 they
    will use require_net_role instead.
    """
    if user.is_admin:
        return user
    from backend.modules.nets.models import NetMembership

    has_membership = db.query(NetMembership).filter(NetMembership.user_callsign == user.callsign).first() is not None
    if not has_membership:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return user


def require_not_pending(user: User = Depends(get_current_user)) -> User:
    if user.is_pending:
        raise HTTPException(status_code=403, detail="Account pending approval")
    return user


def optional_user_with_scope(*scopes: str) -> Callable:
    """Like get_optional_user, but enforces PAT token scopes when a Bearer token is used.

    Cookie auth bypasses scope checks (matching require_scope behavior).
    Anonymous (no token at all) returns None.
    """

    def dependency(
        request: Request,
        access_token: str | None = Cookie(default=None),
        authorization: str | None = Header(default=None),
        db: Session = Depends(get_db_session),
        app_settings: Settings = Depends(get_settings),
    ) -> User | None:
        try:
            user = get_current_user(
                request=request,
                access_token=access_token,
                authorization=authorization,
                db=db,
                app_settings=app_settings,
            )
        except HTTPException:
            return None

        token_scopes = getattr(request.state, "token_scopes", None)
        if token_scopes is not None:
            for scope in scopes:
                if scope not in token_scopes:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Token missing required scope: {scope}",
                    )

        return user

    return dependency


# Union type for endpoints that admit either an admin or a recovery session.
# Both branches expose `.callsign` for audit-log call sites.
Principal = Union[User, RecoveryPrincipal]


def require_admin_or_recovery(
    request: Request,
    access_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db_session),
    app_settings: Settings = Depends(get_settings),
) -> Principal:
    """Accept either a normal admin user OR a valid recovery cookie.

    Recovery is tried first; if the cookie is present and valid, we return
    immediately (no user-JWT lookup required). Otherwise we fall through to
    the normal admin check.

    Audit-log calls in wrapped handlers should use ``principal.callsign`` —
    for an admin that's their callsign; for a recovery session it's
    ``recovery:<hash-prefix>``.
    """
    recovery_cookie = request.cookies.get("recovery_token")
    if recovery_cookie:
        principal = decode_recovery_token(recovery_cookie, app_settings)
        if principal is not None:
            return principal

    # Fall back to normal admin check by re-using the existing helpers.
    user = get_current_user(
        request=request,
        access_token=access_token,
        authorization=authorization,
        db=db,
        app_settings=app_settings,
    )
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return user


def require_scope(*scopes: str) -> Callable:
    def dependency(request: Request, user: User = Depends(get_current_user)) -> User:
        from backend.auth.scopes import ADMIN_SCOPES

        token_scopes = getattr(request.state, "token_scopes", None)
        for scope in scopes:
            # Admin-only scopes require is_admin regardless of auth path.
            if scope in ADMIN_SCOPES and not user.is_admin:
                raise HTTPException(
                    status_code=403,
                    detail=f"Your role does not permit scope: {scope}",
                )
            # PAT auth: verify the token actually carries this scope.
            if token_scopes is not None and scope not in token_scopes:
                raise HTTPException(
                    status_code=403,
                    detail=f"Token missing required scope: {scope}",
                )
        return user

    return dependency


# ---------------------------------------------------------------------------
# Net-scoped access
# ---------------------------------------------------------------------------

_NET_ROLE_RANK = {NetRole.VIEWER: 1, NetRole.NET_CONTROL: 2}


@dataclass
class NetContext:
    user: User
    net: Net
    role: NetRole | None  # None if access is via is_admin


def require_net_role(min_role: NetRole) -> Callable:
    """Factory: returns a FastAPI dependency that enforces minimum net-level access.

    The dependency reads ``net_slug`` from the path, resolves the Net, checks
    the caller's NetMembership (or grants full access if ``user.is_admin``),
    and returns a ``NetContext`` on success.  Raises 404 if the net does not
    exist and 403 if the caller's role is insufficient.
    """
    min_rank = _NET_ROLE_RANK[min_role]

    def dep(
        request: Request,
        net_slug: str = Path(..., alias="net_slug"),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db_session),
    ) -> NetContext:
        net = db.query(Net).filter(Net.slug == net_slug).one_or_none()
        if net is None:
            raise HTTPException(status_code=404, detail="Net not found")
        token_net_id = getattr(request.state, "token_net_id", None)
        if token_net_id is not None and token_net_id != net.id:
            raise HTTPException(status_code=403, detail="Token scoped to a different net")
        if user.is_admin:
            return NetContext(user=user, net=net, role=None)
        m = db.get(NetMembership, (user.callsign, net.id))
        if m is None or _NET_ROLE_RANK[m.role] < min_rank:
            raise HTTPException(status_code=403, detail="Insufficient permissions for net")
        return NetContext(user=user, net=net, role=m.role)

    return dep
