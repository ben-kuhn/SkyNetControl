from __future__ import annotations

import asyncio
import logging
from datetime import date as date_type, datetime, time as time_type, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from backend.auth.email import send_email
from backend.auth.models import User
from backend.config import settings
from backend.modules.nets.config_service import get_net_config
from backend.modules.nets.models import Net
from backend.modules.notifications.service import resolve_session_recipient
from backend.modules.reminders.models import (
    NcoReminderKind,
    NcoReminderLog,
    ReminderLog,
    ReminderStatus,
)
from backend.modules.reminders.service import generate_due_drafts
from backend.modules.schedule.models import NetSession, SessionStatus

logger = logging.getLogger(__name__)

_DAY = timedelta(days=1)
_DEFAULT_MORNING = "07:00"
_DEFAULT_EVENING = "19:00"
_DEFAULT_INTERVAL_MINUTES = 5


def _today() -> date_type:
    """Today's local date. Extracted for test mocking."""
    return date_type.today()


def _parse_time(value: str | None, default: str) -> time_type:
    if not value:
        return _parse_time(default, default)
    try:
        hour, minute = value.split(":", 1)
        return time_type(int(hour), int(minute))
    except (TypeError, ValueError):
        logger.warning("Invalid time %r, using default %s", value, default)
        return _parse_time(default, default)


def _get_timezone(db: Session, net_id: int):
    tz_name = get_net_config(db, net_id, "reminders.nco_email_timezone", "") or ""
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning(
                "Unknown timezone %r for net %s, falling back to server local",
                tz_name,
                net_id,
            )
    return datetime.now().astimezone().tzinfo


def _is_week_long(session: NetSession) -> bool:
    if session.season is not None and session.season.is_week_long:
        return True
    if session.end_date is not None and session.start_date is not None:
        return (session.end_date - session.start_date).days > 1
    return False


def _format_date(d: date_type) -> str:
    return d.strftime("%A, %B %d, %Y")


def _resolve_recipient(db: Session, net: Net, session: NetSession) -> str | None:
    override = (get_net_config(db, net.id, "reminders.nco_email_to", "") or "").strip()
    if override:
        return override

    callsign = resolve_session_recipient(db, session)
    if callsign:
        user = db.query(User).filter(User.callsign == callsign).first()
        if user is not None and user.email:
            return user.email
    return None


def _draft_link(net: Net, session: NetSession, reminder: ReminderLog | None) -> str:
    base = (settings.app_base_url or "").rstrip("/")
    if reminder is not None and reminder.status in (ReminderStatus.DRAFT, ReminderStatus.APPROVED):
        return f"{base}/nets/{net.slug}/reminders?focus={reminder.id}"
    return f"{base}/nets/{net.slug}/reminders"


def _has_log(db: Session, session_id: int, kind: NcoReminderKind) -> NcoReminderLog | None:
    return (
        db.query(NcoReminderLog)
        .filter(NcoReminderLog.session_id == session_id, NcoReminderLog.kind == kind)
        .first()
    )


def _record_email(db: Session, session_id: int, kind: NcoReminderKind) -> None:
    db.add(
        NcoReminderLog(
            session_id=session_id,
            kind=kind,
            emailed_at=datetime.now(tz=timezone.utc),
        )
    )
    db.commit()


async def _send_nco_email(
    db: Session,
    net: Net,
    session: NetSession,
    reminder: ReminderLog | None,
    kind: NcoReminderKind,
) -> None:
    recipient = _resolve_recipient(db, net, session)
    if recipient is None:
        logger.info(
            "No NCO email address for net %s session %s, skipping %s reminder",
            net.slug,
            session.id,
            kind.value,
        )
        return

    link = _draft_link(net, session, reminder)
    when = _format_date(session.start_date)
    if kind == NcoReminderKind.MORNING:
        subject = f"[SkyNetControl] Reminder for {net.name} on {when} is ready"
        body = (
            f"The net reminder for {net.name} on {when} is ready for review.\n"
            f"Open it and send: {link}"
        )
    else:
        subject = f"[SkyNetControl] Reminder for {net.name} on {when} has not been sent"
        body = (
            f"The net reminder for {net.name} on {when} has not been sent yet.\n"
            f"Send it now: {link}"
        )

    await send_email(db, recipient, subject, body)
    _record_email(db, session.id, kind)
    logger.info(
        "Sent NCO %s reminder for net %s session %s to %s",
        kind.value,
        net.slug,
        session.id,
        recipient,
    )


async def _process_net(db: Session, net: Net, tomorrow: date_type) -> None:
    # Ensure the draft exists so the email can deep-link to it. Idempotent.
    generate_due_drafts(db, net_id=net.id)

    morning = _parse_time(
        get_net_config(db, net.id, "reminders.nco_email_morning_time", _DEFAULT_MORNING),
        _DEFAULT_MORNING,
    )
    evening = _parse_time(
        get_net_config(db, net.id, "reminders.nco_email_evening_time", _DEFAULT_EVENING),
        _DEFAULT_EVENING,
    )
    tz = _get_timezone(db, net.id)
    local_now = datetime.now(tz).time()

    sessions = (
        db.query(NetSession)
        .filter(NetSession.net_id == net.id, NetSession.status == SessionStatus.SCHEDULED)
        .all()
    )
    for session in sessions:
        if session.start_date != tomorrow or _is_week_long(session):
            continue

        reminder = db.query(ReminderLog).filter(ReminderLog.session_id == session.id).first()
        if reminder is not None and reminder.status in (ReminderStatus.SENT, ReminderStatus.SKIPPED):
            continue

        if _has_log(db, session.id, NcoReminderKind.MORNING) is None and morning <= local_now < evening:
            await _send_nco_email(db, net, session, reminder, NcoReminderKind.MORNING)
        elif _has_log(db, session.id, NcoReminderKind.EVENING) is None and local_now >= evening:
            await _send_nco_email(db, net, session, reminder, NcoReminderKind.EVENING)


async def run_nco_reminder_checks(db: Session) -> None:
    """Run one NCO-reminder check cycle for every net with the feature enabled."""
    tomorrow = _today() + _DAY
    for net in db.query(Net).all():
        if get_net_config(db, net.id, "reminders.nco_email_enabled", "false") != "true":
            continue
        try:
            await _process_net(db, net, tomorrow)
        except Exception:
            logger.exception("NCO reminder check failed for net %s", net.slug)


def _get_interval_minutes(db: Session) -> int:
    intervals: list[int] = []
    for net in db.query(Net).all():
        if get_net_config(db, net.id, "reminders.nco_email_enabled", "false") != "true":
            continue
        try:
            value = get_net_config(
                db,
                net.id,
                "reminders.nco_email_check_interval_minutes",
                str(_DEFAULT_INTERVAL_MINUTES),
            )
            intervals.append(int(value))
        except (TypeError, ValueError):
            pass
    return min(intervals) if intervals else _DEFAULT_INTERVAL_MINUTES


async def nco_reminder_loop(session_factory) -> None:
    """Background loop that emails net control about unsent reminder drafts."""
    logger.info("NCO reminder loop started")
    while True:
        interval = _DEFAULT_INTERVAL_MINUTES
        try:
            with session_factory() as db:
                interval = _get_interval_minutes(db)
                await run_nco_reminder_checks(db)
        except Exception:
            logger.exception("NCO reminder error during check cycle")
        await asyncio.sleep(interval * 60)