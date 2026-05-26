from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session
from backend.auth.models import User
from backend.modules.notifications.models import Notification
from backend.modules.notifications.service import (
    list_for_user,
    mark_all_read,
    mark_read,
)

notifications_router = APIRouter()


def _to_response(n: Notification) -> dict:
    return {
        "id": n.id,
        "kind": n.kind.value,
        "session_id": n.session_id,
        "message": n.message,
        "link_url": n.link_url,
        "created_at": n.created_at.isoformat(),
        "read_at": n.read_at.isoformat() if n.read_at else None,
    }


@notifications_router.get("/")
async def list_notifications_route(
    all: int = Query(default=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    rows = list_for_user(db, user.callsign, include_read=bool(all))
    return [_to_response(n) for n in rows]


@notifications_router.post("/{notification_id}/read")
async def mark_read_route(
    notification_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    n = mark_read(db, notification_id, user.callsign)
    if n is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return _to_response(n)


@notifications_router.post("/read-all")
async def mark_all_read_route(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    count = mark_all_read(db, user.callsign)
    return {"count": count}
