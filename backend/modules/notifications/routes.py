from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.auth.dependencies import NetContext, get_db_session, require_net_role
from backend.modules.nets.models import NetRole
from backend.modules.notifications.models import Notification
from backend.modules.notifications.service import (
    list_for_user,
    mark_all_read,
    mark_read,
)

# TODO(Task 13): replace DEFAULT_NET_SLUG with CurrentNetContext once available.
notifications_router = APIRouter(prefix="/api/nets/{net_slug}/notifications", tags=["notifications"])


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
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    rows = list_for_user(db, ctx.user.callsign, net_id=ctx.net.id, include_read=bool(all))
    return [_to_response(n) for n in rows]


@notifications_router.post("/read-all")
async def mark_all_read_route(
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    count = mark_all_read(db, ctx.user.callsign, net_id=ctx.net.id)
    return {"count": count}


@notifications_router.post("/{notification_id}/read")
async def mark_read_route(
    notification_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    n = mark_read(db, notification_id, ctx.user.callsign, net_id=ctx.net.id)
    if n is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return _to_response(n)
