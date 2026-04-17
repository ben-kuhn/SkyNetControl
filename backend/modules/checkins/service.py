from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

from backend.modules.checkins.models import (
    CheckIn,
    Member,
    MessageType,
    ParseStatus,
    RawMessage,
    TimingStatus,
)
from backend.modules.checkins.message_parser import parse_message
from backend.modules.schedule.models import NetSession, SessionStatus


def classify_timing(
    net_session: NetSession, received_at: datetime
) -> TimingStatus:
    """Classify a message's timing relative to the session window + grace period."""
    session_start = datetime.combine(
        net_session.start_date, datetime.min.time(), tzinfo=timezone.utc
    )
    session_end = datetime.combine(
        net_session.end_date, datetime.max.time(), tzinfo=timezone.utc
    )

    grace = timedelta(hours=net_session.grace_period_hours)

    # Ensure received_at is timezone-aware (SQLite may strip tzinfo on round-trip)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)

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


def process_raw_message(
    db: Session, raw: RawMessage, net_session: NetSession
) -> CheckIn:
    """Parse a RawMessage and create a CheckIn record."""
    msg_type, fields = parse_message(raw.body)
    raw.message_type = msg_type
    raw.parsed = True

    callsign = fields.get("callsign", "").upper()
    confidence = fields.get("confidence", "low")

    if confidence == "high" or confidence == "medium":
        parse_status = ParseStatus.AUTO
    else:
        parse_status = ParseStatus.MANUAL_REVIEW

    if not callsign and "@" in raw.from_address:
        callsign = raw.from_address.split("@")[0].upper()
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
        comments=fields.get("comments"),
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
    existing_ids = set()
    for msg in raw_messages:
        existing = (
            db.query(RawMessage)
            .filter(RawMessage.message_id == msg["message_id"])
            .first()
        )
        if existing:
            existing_ids.add(msg["message_id"])

    new_messages = [m for m in raw_messages if m["message_id"] not in existing_ids]

    if not new_messages:
        return []

    new_messages.sort(key=lambda m: m["received_at"])

    parsed_checkins: dict[str, CheckIn] = {}
    for msg_dict in new_messages:
        msg_type, fields = parse_message(msg_dict["body"])
        raw = RawMessage(
            message_id=msg_dict["message_id"],
            from_address=msg_dict["from_address"],
            received_at=msg_dict["received_at"],
            subject=msg_dict["subject"],
            body=msg_dict["body"],
            message_type=msg_type,
            parsed=True,
        )
        db.add(raw)
        db.flush()

        checkin = process_raw_message(db, raw, net_session)
        if checkin.callsign:
            if checkin.callsign in parsed_checkins:
                old = parsed_checkins[checkin.callsign]
                db.delete(old)
            parsed_checkins[checkin.callsign] = checkin

    db.commit()
    return list(parsed_checkins.values())


def get_checkins_for_session(
    db: Session, session_id: int
) -> list[CheckIn]:
    return (
        db.query(CheckIn)
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
