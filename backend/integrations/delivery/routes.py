from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.auth.dependencies import NetContext, get_db_session, require_net_role
from backend.integrations.delivery.service import get_delivery_status, retry_failed
from backend.modules.nets.models import NetRole

delivery_router = APIRouter()


@delivery_router.get("/{content_type}/{content_id}")
def list_delivery_attempts(
    content_type: str,
    content_id: int,
    db: Session = Depends(get_db_session),
    _ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
):
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
    _ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
):
    success = retry_failed(db, content_type, content_id)
    return {"retried": success}
