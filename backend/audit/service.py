import json

from sqlalchemy.orm import Session

from backend.audit.models import AuditLog


def log_action(
    db: Session,
    actor: str,
    action: str,
    target: str | None = None,
    details: dict | None = None,
) -> None:
    entry = AuditLog(
        actor_callsign=actor,
        action=action,
        target_callsign=target,
        details=json.dumps(details) if details is not None else None,
    )
    db.add(entry)
    db.commit()
