from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.auth.dependencies import NetContext, get_db_session, require_net_role
from backend.integrations.delivery.service import get_delivery_status, retry_failed
from backend.modules.nets.models import NetRole

delivery_router = APIRouter()


def _verify_content_belongs_to_net(
    db: Session, content_type: str, content_id: int, net_id: int
) -> None:
    """Resolve a delivery log target (`roster`/`reminder`) to its owning net and
    enforce isolation. 404 conflates "not found" with "cross-net" so net
    existence isn't probeable from outside the net.
    """
    from backend.modules.checkins.service import get_net_id_for_session
    from backend.modules.reminders.models import ReminderLog
    from backend.modules.roster.models import RosterLog
    from backend.modules.schedule.models import NetSession

    if content_type == "roster":
        log = db.get(RosterLog, content_id)
    elif content_type == "reminder":
        log = db.get(ReminderLog, content_id)
    else:
        raise HTTPException(status_code=404, detail="Unknown content type")

    if log is None:
        raise HTTPException(status_code=404, detail="Not found")
    sess = db.get(NetSession, log.session_id)
    if sess is None or get_net_id_for_session(db, sess) != net_id:
        raise HTTPException(status_code=404, detail="Not found")


@delivery_router.get("/{content_type}/{content_id}")
def list_delivery_attempts(
    content_type: str,
    content_id: int,
    db: Session = Depends(get_db_session),
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
):
    _verify_content_belongs_to_net(db, content_type, content_id, ctx.net.id)
    logs = get_delivery_status(db, content_type, content_id)
    return [
        {
            "id": log.id,
            "backend": log.backend,
            "status": log.status.value,
            "error_message": log.error_message,
            "sent_at": log.sent_at.isoformat() if log.sent_at else None,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]


@delivery_router.post("/{content_type}/{content_id}/retry")
def retry_delivery(
    content_type: str,
    content_id: int,
    db: Session = Depends(get_db_session),
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
):
    _verify_content_belongs_to_net(db, content_type, content_id, ctx.net.id)
    success = retry_failed(db, content_type, content_id, ctx.net.id)
    return {"retried": success}
