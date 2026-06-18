from typing import Callable, Union

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from backend.auth.models import User, UserRole
from backend.auth.pat_service import authenticate_token
from backend.auth.recovery import RecoveryPrincipal, decode_recovery_token
from backend.auth.service import decode_access_token
from backend.config import Settings


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
    # Try Bearer token first
    if authorization and authorization.startswith("Bearer skynet_"):
        raw_token = authorization[len("Bearer ") :]
        auth_result = authenticate_token(db, raw_token)
        if auth_result is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user = db.get(User, auth_result["user_callsign"])
        if user is None or user.role in (UserRole.PENDING, UserRole.DELETED):
            raise HTTPException(status_code=401, detail="User not found or pending")

        request.state.token_scopes = auth_result["scopes"]
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

    if user.role == UserRole.DELETED:
        raise HTTPException(status_code=401, detail="Account has been deleted")

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


def require_role(*roles: UserRole) -> Callable:
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return dependency


def require_not_pending(user: User = Depends(get_current_user)) -> User:
    if user.role == UserRole.PENDING:
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
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return user


def require_scope(*scopes: str) -> Callable:
    def dependency(request: Request, user: User = Depends(get_current_user)) -> User:
        from backend.auth.scopes import SCOPES, _ROLE_RANK

        user_rank = _ROLE_RANK[user.role]
        token_scopes = getattr(request.state, "token_scopes", None)
        for scope in scopes:
            # Cookie auth: no token-scopes set, but the user must still meet
            # the scope's min_role. Previously this branch returned the user
            # unconditionally — a future endpoint guarded by
            # require_scope("users:write") would have admitted any logged-in
            # viewer via the cookie path. The intent of require_scope is
            # "this endpoint needs scope X, and X's min_role"; cookies
            # bypass the per-scope grant check (since cookies aren't issued
            # with scopes) but never the role floor.
            scope_min_rank = _ROLE_RANK[SCOPES[scope]["min_role"]]
            if user_rank < scope_min_rank:
                raise HTTPException(
                    status_code=403,
                    detail=f"Your role does not permit scope: {scope}",
                )
            if token_scopes is not None and scope not in token_scopes:
                raise HTTPException(
                    status_code=403,
                    detail=f"Token missing required scope: {scope}",
                )
        return user

    return dependency
