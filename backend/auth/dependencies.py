from typing import Callable

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from backend.auth.models import User, UserRole
from backend.auth.pat_service import authenticate_token
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
        raw_token = authorization[len("Bearer "):]
        auth_result = authenticate_token(db, raw_token)
        if auth_result is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user = db.get(User, auth_result["user_callsign"])
        if user is None or user.role == UserRole.PENDING:
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

    return user


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


def require_scope(*scopes: str) -> Callable:
    def dependency(request: Request, user: User = Depends(get_current_user)) -> User:
        token_scopes = getattr(request.state, "token_scopes", None)
        if token_scopes is None:
            return user  # cookie auth = full access
        for scope in scopes:
            if scope not in token_scopes:
                raise HTTPException(
                    status_code=403,
                    detail=f"Token missing required scope: {scope}",
                )
        return user

    return dependency
