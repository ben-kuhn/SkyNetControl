from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.auth.models import User, UserRole
from backend.modules.notifications.models import Notification, NotificationKind
from backend.modules.schedule.models import NetSession


def create_notification(
    db: Session,
    recipient_callsign: str,
    kind: NotificationKind,
    message: str,
    link_url: str | None = None,
    session_id: int | None = None,
    dedupe: bool = True,
) -> Notification:
    """Insert a notification. With dedupe=True, return an existing unread row with the same
    (recipient, kind, session_id) instead of creating a duplicate."""
    if dedupe:
        existing = (
            db.query(Notification)
            .filter(
                Notification.recipient_callsign == recipient_callsign,
                Notification.kind == kind,
                Notification.session_id == session_id,
                Notification.read_at.is_(None),
            )
            .first()
        )
        if existing is not None:
            return existing

    n = Notification(
        recipient_callsign=recipient_callsign,
        kind=kind,
        message=message,
        link_url=link_url,
        session_id=session_id,
        created_at=datetime.now(tz=timezone.utc),
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def list_for_user(
    db: Session, callsign: str, include_read: bool = False,
) -> list[Notification]:
    query = db.query(Notification).filter(Notification.recipient_callsign == callsign)
    if not include_read:
        query = query.filter(Notification.read_at.is_(None))
    return query.order_by(Notification.created_at.desc()).all()


def mark_read(
    db: Session, notification_id: int, callsign: str,
) -> Notification | None:
    n = db.get(Notification, notification_id)
    if n is None or n.recipient_callsign != callsign:
        return None
    if n.read_at is None:
        n.read_at = datetime.now(tz=timezone.utc)
        db.commit()
        db.refresh(n)
    return n


def mark_all_read(db: Session, callsign: str) -> int:
    now = datetime.now(tz=timezone.utc)
    result = (
        db.query(Notification)
        .filter(
            Notification.recipient_callsign == callsign,
            Notification.read_at.is_(None),
        )
        .update({Notification.read_at: now})
    )
    db.commit()
    return result


def resolve_session_recipient(db: Session, net_session: NetSession) -> str | None:
    """Return the session's net_control_callsign, or fall back to the lowest-id admin, or None."""
    if net_session.net_control_callsign:
        return net_session.net_control_callsign

    admin = (
        db.query(User)
        .filter(User.role == UserRole.ADMIN)
        .order_by(User.callsign)
        .first()
    )
    return admin.callsign if admin else None


def _format_session_date(net_session: NetSession) -> str:
    """Short, friendly date — 'May 28' when in the current year, else 'May 28, 2026'."""
    d = net_session.start_date
    today = datetime.now(tz=timezone.utc).date()
    if d.year == today.year:
        return d.strftime("%b %-d")
    return d.strftime("%b %-d, %Y")
