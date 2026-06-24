import logging
import os
from collections.abc import Iterable
from datetime import date, datetime, timezone, timedelta

from sqlalchemy.orm import Session, selectinload

from backend.modules.checkins.models import (
    CheckIn,
    Member,
    MessageType,
    ParseStatus,
    RawMessage,
    TimingStatus,
)
from backend.modules.checkins.message_parser import parse_message
from backend.config_mgmt.service import get_checkin_modes
from backend.modules.schedule.models import NetSession, SessionStatus

logger = logging.getLogger(__name__)


def purge_source_files(paths: Iterable[str]) -> None:
    """Best-effort delete of PAT mailbox files. Missing files are silent;
    other OS errors log a warning but never raise.
    """
    for path in paths:
        if not path:
            continue
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Failed to delete mailbox file %s: %s", path, exc)


def purge_session_source_files(db: Session, session_id: int) -> int:
    """Delete the on-disk source files for all RawMessages attached to a
    session via its CheckIns. Returns the number of paths attempted.
    """
    rows = (
        db.query(RawMessage.source_path)
        .join(CheckIn, CheckIn.raw_message_id == RawMessage.id)
        .filter(CheckIn.session_id == session_id)
        .filter(RawMessage.source_path.isnot(None))
        .all()
    )
    paths = [row[0] for row in rows]
    purge_source_files(paths)
    return len(paths)


def _upsert_source_paths(db: Session, message_dicts: list[dict]) -> None:
    """For each already-imported message dict that has a 'path', backfill
    RawMessage.source_path when currently NULL. No-op otherwise.
    """
    by_id = {m["message_id"]: m.get("path") for m in message_dicts if m.get("path")}
    if not by_id:
        return
    rows = db.query(RawMessage).filter(RawMessage.message_id.in_(by_id.keys())).all()
    for row in rows:
        if row.source_path is None:
            row.source_path = by_id[row.message_id]


def classify_timing(net_session: NetSession, received_at: datetime) -> TimingStatus:
    """Classify a message's timing relative to the session window + grace period."""
    session_start = datetime.combine(net_session.start_date, datetime.min.time(), tzinfo=timezone.utc)

    # Ensure received_at is timezone-aware (SQLite may strip tzinfo on round-trip)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)

    grace = timedelta(hours=net_session.grace_period_hours)

    # Open-ended sessions (real events) — no end date, so anything after start is on time
    if net_session.end_date is None:
        if received_at >= session_start:
            return TimingStatus.ON_TIME
        elif session_start - grace <= received_at < session_start:
            return TimingStatus.EARLY
        return TimingStatus.EARLY

    session_end = datetime.combine(net_session.end_date, datetime.max.time(), tzinfo=timezone.utc)

    if session_start <= received_at <= session_end:
        return TimingStatus.ON_TIME
    elif session_start - grace <= received_at < session_start:
        return TimingStatus.EARLY
    elif session_end < received_at <= session_end + grace:
        return TimingStatus.LATE
    else:
        if received_at < session_start:
            return TimingStatus.EARLY
        return TimingStatus.LATE


def is_new_member(db: Session, callsign: str) -> bool:
    """Check if this callsign has never checked in before."""
    return db.get(Member, callsign) is None


def process_raw_message(db: Session, raw: RawMessage, net_session: NetSession) -> CheckIn:
    """Parse a RawMessage and create a CheckIn record."""
    configured_modes = get_checkin_modes(db)
    modes_set = {m.lower() for m in configured_modes}
    msg_type, fields = parse_message(raw.body, known_modes=modes_set)
    raw.message_type = msg_type
    raw.parsed = True

    body_callsign = fields.get("callsign", "").upper()
    confidence = fields.get("confidence", "low")

    if confidence == "high" or confidence == "medium":
        parse_status = ParseStatus.AUTO
    else:
        parse_status = ParseStatus.MANUAL_REVIEW

    # Sender (From: header) is authoritative — it's the Winlink account that
    # actually transmitted, and the user wins when the body disagrees
    # (backlog item 5). The body's callsign drops to a comment in that case
    # so a "Ben sent on behalf of Alice" situation is still visible.
    #
    # An @-style sender ("W0ABC@winlink.org") gives us the local-part directly.
    # PAT-delivered B2F mail uses a bare-callsign `From:` ("W9GM"), so when
    # there's no @, accept the value only if it looks like a callsign — that
    # keeps junk values like "malformed-no-at-sign" falling through to the
    # body-callsign path.
    from backend.modules.checkins.message_parser import CALLSIGN_RE
    raw_from = raw.from_address.strip()
    if "@" in raw_from:
        sender_callsign = raw_from.split("@", 1)[0].upper()
    elif CALLSIGN_RE.fullmatch(raw_from.upper()):
        sender_callsign = raw_from.upper()
    else:
        sender_callsign = ""
    comments = fields.get("comments")
    if sender_callsign:
        callsign = sender_callsign
        if body_callsign and body_callsign != sender_callsign:
            note = f"Body callsign mismatch: {body_callsign}"
            comments = f"{comments}\n{note}" if comments else note
            parse_status = ParseStatus.MANUAL_REVIEW
    else:
        callsign = body_callsign
        if callsign:
            # Fall back to body when the sender envelope is unparseable —
            # better than dropping the message entirely; flag for review.
            parse_status = ParseStatus.MANUAL_REVIEW

    timing = classify_timing(net_session, raw.received_at)
    new_member = is_new_member(db, callsign) if callsign else False

    checkin = CheckIn(
        session_id=net_session.id,
        raw_message_id=raw.id,
        callsign=callsign,
        name=fields.get("name", ""),
        city=fields.get("city"),
        county=fields.get("county"),
        state=fields.get("state"),
        mode=fields.get("mode", ""),
        comments=comments,
        latitude=fields.get("latitude"),
        longitude=fields.get("longitude"),
        parse_status=parse_status,
        timing_status=timing,
        is_new_member=new_member,
    )
    db.add(checkin)
    db.commit()
    db.refresh(checkin)
    return checkin


def scan_and_import_messages(
    db: Session,
    raw_messages: list[dict],
    net_session: NetSession,
) -> list[CheckIn]:
    """Import raw message dicts, deduplicate by callsign (keep latest), skip existing."""
    all_msg_ids = [msg["message_id"] for msg in raw_messages]
    existing_ids = set(
        row[0] for row in db.query(RawMessage.message_id).filter(RawMessage.message_id.in_(all_msg_ids)).all()
    )

    new_messages = [m for m in raw_messages if m["message_id"] not in existing_ids]
    already_imported = [m for m in raw_messages if m["message_id"] in existing_ids]

    if not new_messages:
        # All messages already in DB. Backfill source_path for any rows
        # that were imported before this field existed.
        _upsert_source_paths(db, already_imported)
        db.commit()
        return []

    new_messages.sort(key=lambda m: m["received_at"])

    parsed_checkins: dict[str, CheckIn] = {}
    for msg_dict in new_messages:
        raw = RawMessage(
            message_id=msg_dict["message_id"],
            from_address=msg_dict["from_address"],
            received_at=msg_dict["received_at"],
            subject=msg_dict["subject"],
            body=msg_dict["body"],
            message_type=MessageType.UNKNOWN,
            parsed=False,
            source_path=msg_dict.get("path"),
        )
        db.add(raw)
        db.flush()

        checkin = process_raw_message(db, raw, net_session)
        if checkin.callsign:
            if checkin.callsign in parsed_checkins:
                old = parsed_checkins[checkin.callsign]
                db.delete(old)
            parsed_checkins[checkin.callsign] = checkin

    _upsert_source_paths(db, already_imported)
    db.commit()  # picks up both the new RawMessage source_paths (already flushed)
                 # and any upserts
    result = list(parsed_checkins.values())

    if result:
        from backend.modules.notifications.models import NotificationKind
        from backend.modules.notifications.service import (
            _format_session_date,
            create_notification,
            resolve_session_recipient,
        )

        recipient = resolve_session_recipient(db, net_session)
        if recipient is not None:
            n = len(result)
            create_notification(
                db,
                recipient_callsign=recipient,
                kind=NotificationKind.CHECKINS_READY,
                message=f"{n} check-in(s) imported for {_format_session_date(net_session)}",
                link_url=f"/checkins?session={net_session.id}",
                session_id=net_session.id,
            )

    return result


def get_checkins_for_session(db: Session, session_id: int) -> list[CheckIn]:
    return (
        db.query(CheckIn)
        .options(selectinload(CheckIn.raw_message))
        .filter(CheckIn.session_id == session_id)
        .order_by(CheckIn.callsign)
        .all()
    )


def create_manual_checkin(
    db: Session,
    session_id: int,
    callsign: str,
    name: str,
    mode: str,
    city: str | None = None,
    county: str | None = None,
    state: str | None = None,
    comments: str | None = None,
) -> CheckIn:
    new_member = is_new_member(db, callsign.upper())
    checkin = CheckIn(
        session_id=session_id,
        callsign=callsign.upper(),
        name=name,
        mode=mode,
        city=city,
        county=county,
        state=state,
        comments=comments,
        parse_status=ParseStatus.MANUALLY_ENTERED,
        timing_status=TimingStatus.ON_TIME,
        is_new_member=new_member,
    )
    db.add(checkin)
    db.commit()
    db.refresh(checkin)
    return checkin


def update_checkin(
    db: Session,
    checkin_id: int,
    name: str | None = None,
    callsign: str | None = None,
    city: str | None = None,
    county: str | None = None,
    state: str | None = None,
    mode: str | None = None,
    comments: str | None = None,
    parse_status: ParseStatus | None = None,
) -> CheckIn | None:
    checkin = db.get(CheckIn, checkin_id)
    if checkin is None:
        return None

    if name is not None:
        checkin.name = name
    if callsign is not None:
        checkin.callsign = callsign.upper()
    if city is not None:
        checkin.city = city
    if county is not None:
        checkin.county = county
    if state is not None:
        checkin.state = state
    if mode is not None:
        checkin.mode = mode
    if comments is not None:
        checkin.comments = comments
    if parse_status is not None:
        checkin.parse_status = parse_status

    db.commit()
    db.refresh(checkin)
    return checkin


def delete_checkin(db: Session, checkin_id: int) -> bool:
    """Remove a check-in row outright. Returns True on success, False if
    no such row. Used by operators to scrub a misparsed/spam entry — the
    underlying RawMessage is kept so a re-scan or manual re-import is
    possible without re-importing the mailbox.
    """
    checkin = db.get(CheckIn, checkin_id)
    if checkin is None:
        return False
    db.delete(checkin)
    db.commit()
    return True


def approve_session_checkins(db: Session, session_id: int) -> None:
    """Approve all check-ins for a session: update Member records, mark session completed."""
    checkins = get_checkins_for_session(db, session_id)
    now = datetime.now(timezone.utc)

    for checkin in checkins:
        if not checkin.callsign:
            continue

        member = db.get(Member, checkin.callsign)
        if member is None:
            member = Member(
                callsign=checkin.callsign,
                name=checkin.name,
                first_check_in_date=now,
                last_check_in_date=now,
                total_check_ins=1,
            )
            db.add(member)
        else:
            member.last_check_in_date = now
            member.total_check_ins += 1
            if checkin.name:
                member.name = checkin.name

    net_session = db.get(NetSession, session_id)
    if net_session is not None:
        net_session.status = SessionStatus.COMPLETED

    db.commit()


def get_checkins_by_callsign(db: Session, callsign: str) -> list[tuple[CheckIn, date]]:
    """All check-ins for a callsign with their session date, newest first."""
    normalized = callsign.upper()
    return (
        db.query(CheckIn, NetSession.start_date)
        .join(NetSession, CheckIn.session_id == NetSession.id)
        .options(selectinload(CheckIn.raw_message))
        .filter(CheckIn.callsign == normalized)
        .order_by(NetSession.start_date.desc(), CheckIn.id.desc())
        .all()
    )
