from typing import Callable

from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.auth.models import User, UserRole
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
    db: Session = Depends(get_db_session),
    app_settings: Settings = Depends(get_settings),
) -> User:
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
