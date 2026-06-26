from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.auth.models import User
from backend.modules.notifications.models import Notification, NotificationKind
from backend.modules.schedule.models import NetSeason, NetSession


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


def _get_net_id_for_session(db: Session, net_session: NetSession) -> int | None:
    """Resolve the net_id for a session by joining through its season."""
    if net_session.season_id is None:
        return None
    season = db.get(NetSeason, net_session.season_id)
    return season.net_id if season else None


def list_for_user(
    db: Session,
    callsign: str,
    net_id: int,
    include_read: bool = False,
) -> list[Notification]:
    """List notifications for *callsign* scoped to *net_id*.

    Notifications without a session_id have no net affiliation and are excluded.
    """
    query = (
        db.query(Notification)
        .join(NetSession, Notification.session_id == NetSession.id)
        .join(NetSeason, NetSession.season_id == NetSeason.id)
        .filter(
            Notification.recipient_callsign == callsign,
            NetSeason.net_id == net_id,
        )
    )
    if not include_read:
        query = query.filter(Notification.read_at.is_(None))
    return query.order_by(Notification.created_at.desc()).all()


def mark_read(
    db: Session,
    notification_id: int,
    callsign: str,
    net_id: int,
) -> Notification | None:
    """Mark a notification read.  Returns None if the notification doesn't exist,
    doesn't belong to *callsign*, or doesn't belong to *net_id*."""
    n = db.get(Notification, notification_id)
    if n is None or n.recipient_callsign != callsign:
        return None
    if n.session_id is None:
        return None
    net_session = db.get(NetSession, n.session_id)
    if net_session is None or _get_net_id_for_session(db, net_session) != net_id:
        return None
    if n.read_at is None:
        n.read_at = datetime.now(tz=timezone.utc)
        db.commit()
        db.refresh(n)
    return n


def mark_all_read(db: Session, callsign: str, net_id: int) -> int:
    """Mark all unread notifications for *callsign* within *net_id* as read."""
    # Fetch IDs that belong to this net via session join, then bulk-update by id list.
    ids = [
        row[0]
        for row in (
            db.query(Notification.id)
            .join(NetSession, Notification.session_id == NetSession.id)
            .join(NetSeason, NetSession.season_id == NetSeason.id)
            .filter(
                Notification.recipient_callsign == callsign,
                Notification.read_at.is_(None),
                NetSeason.net_id == net_id,
            )
            .all()
        )
    ]
    if not ids:
        return 0
    now = datetime.now(tz=timezone.utc)
    result = (
        db.query(Notification)
        .filter(Notification.id.in_(ids))
        .update({Notification.read_at: now}, synchronize_session="fetch")
    )
    db.commit()
    return result


def resolve_session_recipient(db: Session, net_session: NetSession) -> str | None:
    """Return the session's net_control_callsign, or fall back to the lowest-id admin, or None."""
    if net_session.net_control_callsign:
        return net_session.net_control_callsign

    admin = db.query(User).filter(User.is_admin.is_(True)).order_by(User.callsign).first()
    return admin.callsign if admin else None


def _format_session_date(net_session: NetSession) -> str:
    """Short, friendly date — 'May 28' when in the current year, else 'May 28, 2026'."""
    d = net_session.start_date
    today = datetime.now(tz=timezone.utc).date()
    if d.year == today.year:
        return d.strftime("%b %-d")
    return d.strftime("%b %-d, %Y")
