from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session, require_not_pending
from backend.auth.models import User, UserRole
from backend.auth.pat_service import create_token, list_tokens, revoke_token

pat_router = APIRouter(tags=["tokens"])


def _require_cookie_auth(request: Request, user: User = Depends(require_not_pending)) -> User:
    token_scopes = getattr(request.state, "token_scopes", None)
    if token_scopes is not None:
        raise HTTPException(
            status_code=403,
            detail="Token management requires browser authentication",
        )
    return user


class TokenCreateRequest(BaseModel):
    name: str
    scopes: list[str]
    expires_at: datetime | None = None


@pat_router.post("", status_code=201)
async def create_token_route(
    body: TokenCreateRequest,
    user: User = Depends(_require_cookie_auth),
    db: Session = Depends(get_db_session),
):
    try:
        result = create_token(
            db=db,
            user_callsign=user.callsign,
            user_role=user.role,
            name=body.name,
            scopes=body.scopes,
            expires_at=body.expires_at,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@pat_router.get("")
async def list_tokens_route(
    user: User = Depends(_require_cookie_auth),
    db: Session = Depends(get_db_session),
):
    return list_tokens(db, user.callsign)


@pat_router.delete("/{token_id}", status_code=204)
async def revoke_token_route(
    token_id: int,
    user: User = Depends(_require_cookie_auth),
    db: Session = Depends(get_db_session),
):
    try:
        revoke_token(
            db=db,
            token_id=token_id,
            user_callsign=user.callsign,
            is_admin=user.role == UserRole.ADMIN,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Token not found")
