import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session, require_role
from backend.auth.models import UserRole
from backend.audit.models import AuditLog

audit_router = APIRouter(tags=["audit"])


@audit_router.get("/")
async def list_audit_log(
    limit: int = Query(default=50, ge=1, le=200),
    user=Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    entries = (
        db.query(AuditLog)
        .order_by(AuditLog.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": e.id,
            "actor_callsign": e.actor_callsign,
            "action": e.action,
            "target_callsign": e.target_callsign,
            "details": json.loads(e.details) if e.details else None,
            "created_at": e.created_at.isoformat(),
        }
        for e in entries
    ]
