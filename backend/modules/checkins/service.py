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
# Register geocoder cache tables early — `_compute_checkin_fields` uses
# them for the lat/lon <-> city/state fallbacks, and test fixtures
# create tables via Base.metadata.create_all which only sees models that
# have been imported by the time it runs.
from backend.integrations.geocoder.models import (  # noqa: F401
    GeocodeCache,
    ReverseGeocodeCache,
)
from backend.modules.checkins.message_parser import parse_message
from backend.config_mgmt.service import get_checkin_modes
from backend.modules.schedule.models import NetSession, NetSeason, SessionStatus

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


def get_net_id_for_session(db: Session, net_session: NetSession) -> int | None:
    """Resolve the net_id for a session.

    Reads ``net_session.net_id`` directly. The legacy season-join fallback was
    removed once every session row gained an explicit ``net_id`` column.
    """
    return net_session.net_id


def is_new_member(db: Session, net_id: int, callsign: str) -> bool:
    """Check if this callsign has never checked in before (within the net)."""
    return db.get(Member, (net_id, callsign)) is None


def _compute_checkin_fields(db: Session, raw: RawMessage, net_session: NetSession) -> dict:
    """Run the parser + sender-callsign resolution against a RawMessage and
    return the field values for the corresponding CheckIn. Mutates ``raw``
    to record the detected message_type and the parsed flag, but does not
    touch the CheckIn table — callers compose the new/updated row.

    Net-scoped state (``is_new_member``) is computed by the caller, not here,
    so this helper stays usable from reparse paths that don't need it.
    """
    from backend.modules.checkins.message_parser import CALLSIGN_RE
    from backend.modules.checkins.mode_normalize import normalize_mode

    configured_modes = get_checkin_modes(db)
    modes_set = {m.lower() for m in configured_modes}
    msg_type, fields = parse_message(raw.body, known_modes=modes_set)
    raw.message_type = msg_type
    raw.parsed = True

    # Collapse mode variations ("VARA-HF", "HF VARA", "VHF VARA", etc.)
    # onto canonical names so the check-in table groups cleanly.
    fields["mode"] = normalize_mode(fields.get("mode") or "")

    body_callsign = fields.get("callsign", "").upper()
    confidence = fields.get("confidence", "low")
    parse_status = (
        ParseStatus.AUTO if confidence in ("high", "medium") else ParseStatus.MANUAL_REVIEW
    )

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

    # Location fallback chain when the form didn't carry precise coords:
    #   1. Maidenhead grid square (free, ~5 km accuracy for 6-char).
    #   2. City + state via Nominatim (cached in geocode_cache).
    # Both are best-effort — failure leaves lat/lon null and the row
    # simply doesn't render on the map. We never overwrite explicit
    # coordinates, since those are more accurate than either fallback.
    latitude = fields.get("latitude")
    longitude = fields.get("longitude")
    if latitude is None or longitude is None:
        from backend.utils.location import maidenhead_to_latlon
        coords = maidenhead_to_latlon(fields.get("grid") or "")
        if coords is not None:
            latitude, longitude = coords
    if latitude is None or longitude is None:
        city = fields.get("city")
        state = fields.get("state")
        if city and state:
            from backend.integrations.geocoder.service import geocode_city
            coords = geocode_city(db, city, state)
            if coords is not None:
                latitude, longitude = coords

    # Reverse direction: parser couldn't pull a city from comments / the
    # location fallback, but we do have coordinates. Resolve the closest
    # populated place via Overpass so the table view shows something
    # meaningful instead of an empty City column.
    city = fields.get("city")
    state = fields.get("state")
    if city is None and latitude is not None and longitude is not None:
        from backend.integrations.geocoder.service import reverse_geocode_closest_city
        result = reverse_geocode_closest_city(db, latitude, longitude)
        if result is not None:
            rg_city, rg_state = result
            fields["city"] = rg_city
            if state is None and rg_state:
                fields["state"] = rg_state

    return {
        "callsign": callsign,
        "name": fields.get("name", ""),
        "city": fields.get("city"),
        "county": fields.get("county"),
        "state": fields.get("state"),
        "mode": fields.get("mode", ""),
        "comments": comments,
        "latitude": latitude,
        "longitude": longitude,
        "parse_status": parse_status,
        "timing_status": classify_timing(net_session, raw.received_at),
    }


def process_raw_message(db: Session, raw: RawMessage, net_session: NetSession, net_id: int | None) -> CheckIn:
    """Parse a RawMessage and create a CheckIn record."""
    f = _compute_checkin_fields(db, raw, net_session)
    new_member = (
        is_new_member(db, net_id, f["callsign"]) if (f["callsign"] and net_id is not None) else False
    )
    checkin = CheckIn(
        session_id=net_session.id,
        raw_message_id=raw.id,
        is_new_member=new_member,
        **f,
    )
    db.add(checkin)
    db.commit()
    db.refresh(checkin)
    return checkin


def reparse_checkin(db: Session, checkin_id: int) -> CheckIn | None:
    """Re-run the parser against this CheckIn's stored RawMessage and update
    its fields in place. Returns None if the CheckIn doesn't exist or has no
    RawMessage attached (manually-entered rows). ``is_new_member`` is
    preserved — it's a historical fact, not a parser output.
    """
    checkin = db.get(CheckIn, checkin_id)
    if checkin is None or checkin.raw_message_id is None:
        return None
    raw = db.get(RawMessage, checkin.raw_message_id)
    if raw is None:
        return None
    net_session = db.get(NetSession, checkin.session_id)
    if net_session is None:
        return None

    f = _compute_checkin_fields(db, raw, net_session)
    for k, v in f.items():
        setattr(checkin, k, v)
    db.commit()
    db.refresh(checkin)
    return checkin


def reparse_session(db: Session, net_session: NetSession) -> dict:
    """Re-run the parser for every check-in in the session, and reclaim any
    orphan RawMessages whose ``received_at`` falls in the session window.

    Returns ``{"updated": N, "imported": M}``. Useful after deploying a
    parser fix or after a check-in was accidentally deleted.
    """
    updated = 0
    existing = (
        db.query(CheckIn)
        .filter(CheckIn.session_id == net_session.id)
        .filter(CheckIn.raw_message_id.isnot(None))
        .all()
    )
    for checkin in existing:
        raw = db.get(RawMessage, checkin.raw_message_id)
        if raw is None:
            continue
        f = _compute_checkin_fields(db, raw, net_session)
        for k, v in f.items():
            setattr(checkin, k, v)
        updated += 1

    # Orphan reclaim: any RawMessage in the session's time window that has
    # no CheckIn referencing it. Uses the same window as classify_timing so
    # an orphan that would have been counted as on-time/early/late before
    # deletion comes back into the same session.
    session_start = datetime.combine(net_session.start_date, datetime.min.time(), tzinfo=timezone.utc)
    grace = timedelta(hours=net_session.grace_period_hours)
    window_start = session_start - grace
    if net_session.end_date is None:
        window_end = datetime.now(timezone.utc)
    else:
        window_end = (
            datetime.combine(net_session.end_date, datetime.max.time(), tzinfo=timezone.utc) + grace
        )

    referenced_ids = db.query(CheckIn.raw_message_id).filter(CheckIn.raw_message_id.isnot(None))
    orphans = (
        db.query(RawMessage)
        .filter(RawMessage.received_at >= window_start)
        .filter(RawMessage.received_at <= window_end)
        .filter(~RawMessage.id.in_(referenced_ids))
        .all()
    )
    net_id = get_net_id_for_session(db, net_session)
    imported = 0
    for raw in orphans:
        process_raw_message(db, raw, net_session, net_id=net_id)
        imported += 1

    db.commit()
    return {"updated": updated, "imported": imported}


def scan_and_import_messages(
    db: Session,
    raw_messages: list[dict],
    net_session: NetSession,
    net_id: int | None = None,
) -> list[CheckIn]:
    """Import raw message dicts, deduplicate by callsign (keep latest), skip existing."""
    # Resolve net_id if not provided
    if net_id is None:
        net_id = get_net_id_for_session(db, net_session)

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

    if net_id is None:
        # REAL_EVENT session with no season — net_id unresolvable.
        # Checkins are still imported; is_new_member detection is skipped.
        logger.warning(
            "scan_and_import_messages: net_id unknown for session %d "
            "(REAL_EVENT without season); new-member detection skipped",
            net_session.id,
        )

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

        checkin = process_raw_message(db, raw, net_session, net_id=net_id)
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
    net_id: int | None = None,
    city: str | None = None,
    county: str | None = None,
    state: str | None = None,
    comments: str | None = None,
) -> CheckIn:
    new_member = is_new_member(db, net_id, callsign.upper()) if net_id is not None else False
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
    net_id: int | None = None,
) -> CheckIn | None:
    checkin = db.get(CheckIn, checkin_id)
    if checkin is None:
        return None

    # Cross-net isolation: verify this checkin belongs to the requested net
    if net_id is not None:
        session_obj = db.get(NetSession, checkin.session_id)
        if session_obj is not None:
            session_net_id = get_net_id_for_session(db, session_obj)
            if session_net_id != net_id:
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


def delete_checkin(db: Session, checkin_id: int, net_id: int | None = None) -> bool:
    """Remove a check-in row outright. Returns True on success, False if
    no such row. Used by operators to scrub a misparsed/spam entry — the
    underlying RawMessage is kept so a re-scan or manual re-import is
    possible without re-importing the mailbox.

    If net_id is provided, also returns False (not 404-leaking) if the
    checkin belongs to a different net.
    """
    checkin = db.get(CheckIn, checkin_id)
    if checkin is None:
        return False

    # Cross-net isolation
    if net_id is not None:
        session_obj = db.get(NetSession, checkin.session_id)
        if session_obj is not None:
            session_net_id = get_net_id_for_session(db, session_obj)
            if session_net_id != net_id:
                return False

    db.delete(checkin)
    db.commit()
    return True


def approve_session_checkins(db: Session, session_id: int, net_id: int | None = None) -> None:
    """Approve all check-ins for a session: update Member records, mark session completed."""
    checkins = get_checkins_for_session(db, session_id)
    now = datetime.now(timezone.utc)

    # Resolve net_id if not provided
    if net_id is None:
        net_session = db.get(NetSession, session_id)
        if net_session is not None:
            net_id = get_net_id_for_session(db, net_session)

    for checkin in checkins:
        if not checkin.callsign:
            continue

        if net_id is not None:
            member = db.get(Member, (net_id, checkin.callsign))
            if member is None:
                member = Member(
                    net_id=net_id,
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
        else:
            # Fallback: no net_id known, skip member upsert
            logger.warning("approve_session_checkins: no net_id for session %d, skipping member upsert", session_id)

    net_session = db.get(NetSession, session_id)
    if net_session is not None:
        net_session.status = SessionStatus.COMPLETED

    db.commit()


def get_checkins_by_callsign(db: Session, callsign: str, net_id: int | None = None) -> list[tuple[CheckIn, date]]:
    """All check-ins for a callsign with their session date, newest first.

    When net_id is provided, only returns check-ins for sessions belonging to that net.
    """
    normalized = callsign.upper()
    query = (
        db.query(CheckIn, NetSession.start_date)
        .join(NetSession, CheckIn.session_id == NetSession.id)
        .options(selectinload(CheckIn.raw_message))
        .filter(CheckIn.callsign == normalized)
    )
    if net_id is not None:
        query = query.join(NetSeason, NetSession.season_id == NetSeason.id).filter(NetSeason.net_id == net_id)
    return (
        query.order_by(NetSession.start_date.desc(), CheckIn.id.desc())
        .all()
    )
