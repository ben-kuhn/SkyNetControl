import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.modules.schedule.models import NetSession, SessionStatus

logger = logging.getLogger(__name__)


def find_active_session(db: Session, now: datetime) -> NetSession | None:
    """Find a SCHEDULED session whose window (start - grace through end + grace) contains `now`."""
    sessions = (
        db.query(NetSession)
        .filter(NetSession.status == SessionStatus.SCHEDULED)
        .all()
    )

    for session in sessions:
        session_start = datetime.combine(session.start_date, datetime.min.time(), tzinfo=timezone.utc)
        grace = timedelta(hours=session.grace_period_hours)

        window_open = session_start - grace

        if session.end_date is not None:
            session_end = datetime.combine(session.end_date, datetime.max.time(), tzinfo=timezone.utc)
            window_close = session_end + grace
        else:
            window_close = None

        if now >= window_open and (window_close is None or now <= window_close):
            return session

    return None
