import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.config_mgmt.service import get_config_value
from backend.modules.checkins.mailbox_reader import read_mailbox
from backend.modules.checkins.service import scan_and_import_messages
from backend.modules.schedule.models import NetSession, SessionStatus

logger = logging.getLogger(__name__)


def find_active_session(db: Session, now: datetime) -> NetSession | None:
    """Find a SCHEDULED session whose window (start - grace through end + grace) contains `now`."""
    sessions = db.query(NetSession).filter(NetSession.status == SessionStatus.SCHEDULED).all()

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


class ScannerState:
    """Mutable state for the background scanner."""

    def __init__(self):
        self.running: bool = False
        self.last_scan_time: datetime | None = None
        self.last_scan_count: int | None = None
        self.active_session_id: int | None = None
        self.interval_minutes: int = 5


scanner_state = ScannerState()


def run_scan(db: Session, now: datetime) -> int | None:
    """Run a single scan cycle. Returns count of imported check-ins, or None if skipped."""
    net_address = get_config_value(db, "net_address", "")
    mailbox_path = get_config_value(db, "pat_mailbox_path", "")

    if not net_address or not mailbox_path:
        logger.info("Scanner skipped: net_address or pat_mailbox_path not configured")
        return None

    session = find_active_session(db, now)
    if session is None:
        logger.debug("Scanner skipped: no active session window")
        return None

    inbox_path = os.path.join(mailbox_path, "in")

    messages = read_mailbox(inbox_path, net_address)
    checkins = scan_and_import_messages(db, messages, session)

    scanner_state.last_scan_time = now
    scanner_state.last_scan_count = len(checkins)
    scanner_state.active_session_id = session.id

    logger.info("Scanner completed: %d new check-ins imported", len(checkins))
    return len(checkins)


async def scanner_loop(session_factory, get_interval_minutes):
    """Background loop that runs scans on a schedule."""
    import asyncio

    scanner_state.running = True
    logger.info("Scanner started")

    try:
        while scanner_state.running:
            interval = get_interval_minutes()
            scanner_state.interval_minutes = interval
            try:
                with session_factory() as db:
                    now = datetime.now(tz=timezone.utc)
                    run_scan(db, now)
            except Exception:
                logger.exception("Scanner error during scan cycle")

            await asyncio.sleep(interval * 60)
    finally:
        scanner_state.running = False
        logger.info("Scanner stopped")
