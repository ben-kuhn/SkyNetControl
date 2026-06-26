import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.config_mgmt.service import get_config_value
from backend.modules.checkins.mailbox_reader import read_mailbox
from backend.modules.checkins.service import scan_and_import_messages
from backend.modules.schedule.models import NetSession, SessionStatus

logger = logging.getLogger(__name__)


def find_active_session(db: Session, now: datetime, net_id: int | None = None) -> NetSession | None:
    """Find a SCHEDULED session whose window (start - grace through end + grace) contains `now`.

    When net_id is provided, only considers sessions belonging to that net.
    """
    from backend.modules.schedule.models import NetSeason

    query = db.query(NetSession).filter(NetSession.status == SessionStatus.SCHEDULED)
    if net_id is not None:
        query = (
            query.join(NetSeason, NetSession.season_id == NetSeason.id)
            .filter(NetSeason.net_id == net_id)
        )
    sessions = query.all()

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
    """Run a single scan cycle against the default net (global app_config).

    Returns count of imported check-ins, or None if skipped.
    This is the legacy single-net path used by the scanner trigger route and tests.
    For multi-net scanning, use scan_all_enabled().
    """
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


def scan_one(db: Session, net_id: int, mailbox: str, now: datetime) -> int:
    """Scan a single net's mailbox. Returns count of imported check-ins."""
    from backend.modules.nets.config_service import get_net_config

    net_address = get_net_config(db, net_id, "net_address", "")
    if not net_address:
        # Fall back to global config for backward compat during migration
        net_address = get_config_value(db, "net_address", "")

    if not net_address:
        logger.info("Scanner skipped for net_id=%d: net_address not configured", net_id)
        return 0

    session = find_active_session(db, now, net_id=net_id)
    if session is None:
        logger.debug("Scanner skipped for net_id=%d: no active session window", net_id)
        return 0

    inbox_path = os.path.join(mailbox, "in")
    messages = read_mailbox(inbox_path, net_address)
    checkins = scan_and_import_messages(db, messages, session, net_id=net_id)

    count = len(checkins)
    scanner_state.last_scan_time = now
    scanner_state.last_scan_count = count
    scanner_state.active_session_id = session.id

    logger.info("Scanner completed for net_id=%d: %d new check-ins imported", net_id, count)
    return count


def scan_all_enabled(db: Session, now: datetime) -> int | None:
    """Scan all nets that have scanner.enabled=true in their net_config.

    Returns total count of imported check-ins across all enabled nets, or
    ``None`` when no nets have ``scanner.enabled=true`` (so the caller can
    distinguish "nothing configured" from "configured, zero new messages").
    """
    from backend.modules.nets.models import Net
    from backend.modules.nets.config_service import get_net_config

    nets = db.query(Net).all()
    total: int | None = None
    for net in nets:
        if get_net_config(db, net.id, "scanner.enabled", "false") != "true":
            continue
        mailbox = get_net_config(db, net.id, "pat_mailbox_path")
        if not mailbox:
            continue
        if total is None:
            total = 0
        total += scan_one(db, net.id, mailbox, now)
    return total


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
                    # Try per-net scanning first; fall back to global config
                    # for installations that haven't migrated to net_config yet.
                    # TODO(Task 14/15): remove the global fallback once all
                    # installations have per-net scanner config.
                    result = scan_all_enabled(db, now)
                    if result is None:
                        # No nets have per-net scanner.enabled; try global config
                        run_scan(db, now)
            except Exception:
                logger.exception("Scanner error during scan cycle")

            await asyncio.sleep(interval * 60)
    finally:
        scanner_state.running = False
        logger.info("Scanner stopped")
