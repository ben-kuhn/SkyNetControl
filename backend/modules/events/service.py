import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.integrations.callbook.service import lookup_callsign
from backend.modules.events.models import (
    Event,
    EventLogEntry,
    EventLogType,
    EventOperator,
    EventParticipant,
    EventPost,
    EventStatus,
    EventType,
    ParticipantStatus,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- Exceptions ---------------------------------------------------------


class EventError(Exception):
    """Base for events service errors."""


class EventNotActiveError(EventError):
    """Write attempted against an event that isn't in the required status (→ 409)."""


class InvalidLifecycleError(EventError):
    """activate/close/reopen from the wrong current status (→ 409)."""


class DuplicatePostError(EventError):
    """Post name already exists on this event (→ 409)."""


class PostAssignedError(EventError):
    """Post still has assigned participants (→ 409)."""


class InvalidPostError(EventError):
    """post_id does not belong to this event (→ 422)."""


class DuplicateParticipantError(EventError):
    """Callsign already checked in to this event (→ 409)."""


class InvalidStatusTransitionError(EventError):
    """Illegal participant status transition (→ 422)."""


# --- Log / seq machinery -------------------------------------------------


def locked_event(db: Session, event_id: int) -> Event | None:
    """Fetch the event row with FOR UPDATE so concurrent writers serialize on
    it (log_seq assignment depends on this). No-op lock on SQLite, which
    serializes writes anyway."""
    return db.query(Event).filter(Event.id == event_id).with_for_update().one_or_none()


def add_log_entry(
    db: Session,
    event: Event,
    *,
    entry_type: EventLogType,
    message: str,
    actor: str,
    callsign: str | None = None,
    new_status: ParticipantStatus | None = None,
    pinned: bool = False,
) -> EventLogEntry:
    """Append a log entry with the next seq. Caller must hold the event row
    (via locked_event) and is responsible for committing."""
    event.log_seq += 1
    entry = EventLogEntry(
        event_id=event.id,
        seq=event.log_seq,
        entry_type=entry_type,
        message=message,
        actor=actor,
        callsign=callsign,
        new_status=new_status,
        pinned=pinned,
    )
    db.add(entry)
    return entry


# --- Event lifecycle ------------------------------------------------------


def create_event(
    db: Session,
    *,
    name: str,
    event_type: EventType,
    created_by: str,
    description: str | None = None,
    scheduled_start: datetime | None = None,
) -> Event:
    event = Event(
        name=name,
        event_type=event_type,
        status=EventStatus.DRAFT,
        description=description,
        scheduled_start=scheduled_start,
        created_by=created_by,
        public_token=secrets.token_urlsafe(16),
        visibility="private",
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


# --- Ownership / operator helpers ----------------------------------------


def add_operator(db: Session, event: Event, callsign: str, *, added_by: str) -> None:
    cs = callsign.strip().upper()
    exists = (
        db.query(EventOperator)
        .filter(EventOperator.event_id == event.id, EventOperator.callsign == cs)
        .first()
    )
    if exists is None and cs and cs != (event.created_by or "").upper():
        db.add(EventOperator(event_id=event.id, callsign=cs, added_by=added_by,
                             added_at=datetime.now(timezone.utc)))
        db.commit()


def remove_operator(db: Session, event: Event, callsign: str) -> None:
    (
        db.query(EventOperator)
        .filter(EventOperator.event_id == event.id, EventOperator.callsign == callsign.strip().upper())
        .delete()
    )
    db.commit()


def list_operators(db: Session, event: Event) -> list[str]:
    return [
        o.callsign
        for o in db.query(EventOperator)
        .filter(EventOperator.event_id == event.id)
        .order_by(EventOperator.callsign)
        .all()
    ]


def transfer_owner(db: Session, event: Event, new_owner: str) -> None:
    event.created_by = new_owner.strip().upper()
    db.commit()


def set_visibility(db: Session, event: Event, visibility: str) -> None:
    if visibility not in ("private", "public"):
        raise ValueError("visibility must be 'private' or 'public'")
    event.visibility = visibility
    db.commit()


def rotate_public_token(db: Session, event: Event) -> str:
    event.public_token = secrets.token_urlsafe(16)
    db.commit()
    return event.public_token


def activate_event(db: Session, event_id: int, *, actor: str) -> Event:
    event = locked_event(db, event_id)
    if event is None:
        raise InvalidLifecycleError("Event not found")
    if event.status != EventStatus.DRAFT:
        raise InvalidLifecycleError(f"Cannot activate an event in status {event.status.value}")
    event.status = EventStatus.ACTIVE
    event.activated_at = _utcnow()
    add_log_entry(db, event, entry_type=EventLogType.SYSTEM, message="Event activated", actor=actor)
    db.commit()
    db.refresh(event)
    return event


def close_event(db: Session, event_id: int, *, actor: str) -> Event:
    event = locked_event(db, event_id)
    if event is None or event.status != EventStatus.ACTIVE:
        raise InvalidLifecycleError("Only an active event can be closed")
    add_log_entry(db, event, entry_type=EventLogType.SYSTEM, message="Event closed", actor=actor)
    event.status = EventStatus.CLOSED
    event.closed_at = _utcnow()
    db.commit()
    db.refresh(event)
    return event


def reopen_event(db: Session, event_id: int, *, actor: str) -> Event:
    event = locked_event(db, event_id)
    if event is None or event.status != EventStatus.CLOSED:
        raise InvalidLifecycleError("Only a closed event can be reopened")
    event.status = EventStatus.ACTIVE
    event.closed_at = None
    add_log_entry(db, event, entry_type=EventLogType.SYSTEM, message="Event reopened", actor=actor)
    db.commit()
    db.refresh(event)
    return event


_UNSET = object()


def update_event(
    db: Session,
    event_id: int,
    *,
    name: str | None = None,
    description: object = _UNSET,
    scheduled_start: object = _UNSET,
    aprs_other_stations: object = _UNSET,
    aprs_range_lat: object = _UNSET,
    aprs_range_lon: object = _UNSET,
    aprs_range_km: object = _UNSET,
    aprs_beacon_posts: object = _UNSET,
) -> Event | None:
    event = locked_event(db, event_id)
    if event is None:
        return None
    if event.status == EventStatus.CLOSED:
        raise EventNotActiveError("Cannot edit a closed event")
    if name is not None:
        event.name = name
    if description is not _UNSET:
        event.description = description
    if scheduled_start is not _UNSET:
        event.scheduled_start = scheduled_start
    if aprs_other_stations is not _UNSET:
        event.aprs_other_stations = aprs_other_stations
    if aprs_range_lat is not _UNSET:
        event.aprs_range_lat = aprs_range_lat
    if aprs_range_lon is not _UNSET:
        event.aprs_range_lon = aprs_range_lon
    if aprs_range_km is not _UNSET:
        event.aprs_range_km = aprs_range_km
    if aprs_beacon_posts is not _UNSET:
        event.aprs_beacon_posts = aprs_beacon_posts
    db.commit()
    db.refresh(event)
    return event


# --- Posts ---------------------------------------------------------------


def _post_name_taken(db: Session, event_id: int, name: str, exclude_id: int | None = None) -> bool:
    query = db.query(EventPost).filter(EventPost.event_id == event_id, EventPost.name == name)
    if exclude_id is not None:
        query = query.filter(EventPost.id != exclude_id)
    return query.first() is not None


def create_post(
    db: Session,
    event_id: int,
    *,
    name: str,
    description: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> EventPost:
    event = locked_event(db, event_id)
    if event is None or event.status == EventStatus.CLOSED:
        raise EventNotActiveError("Cannot add posts to a closed event")
    if _post_name_taken(db, event_id, name):
        raise DuplicatePostError(f"Post '{name}' already exists")
    post = EventPost(event_id=event_id, name=name, description=description, lat=lat, lon=lon)
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def update_post(
    db: Session,
    event_id: int,
    post_id: int,
    *,
    name: str | None = None,
    description: object = _UNSET,
    lat: object = _UNSET,
    lon: object = _UNSET,
) -> EventPost | None:
    event = locked_event(db, event_id)
    if event is None or event.status == EventStatus.CLOSED:
        raise EventNotActiveError("Event is closed")
    post = db.query(EventPost).filter(EventPost.id == post_id, EventPost.event_id == event_id).one_or_none()
    if post is None:
        return None
    if name is not None:
        if _post_name_taken(db, event_id, name, exclude_id=post_id):
            raise DuplicatePostError(f"Post '{name}' already exists")
        post.name = name
    if description is not _UNSET:
        post.description = description
    if lat is not _UNSET:
        post.lat = lat
    if lon is not _UNSET:
        post.lon = lon
    db.commit()
    db.refresh(post)
    return post


def delete_post(db: Session, event_id: int, post_id: int) -> bool:
    event = locked_event(db, event_id)
    if event is None or event.status == EventStatus.CLOSED:
        raise EventNotActiveError("Event is closed")
    post = db.query(EventPost).filter(EventPost.id == post_id, EventPost.event_id == event_id).one_or_none()
    if post is None:
        return False
    assigned = db.query(EventParticipant).filter(EventParticipant.post_id == post_id).first()
    if assigned is not None:
        raise PostAssignedError("Post has assigned participants")
    db.delete(post)
    db.commit()
    return True


# --- Participants ---------------------------------------------------------


def _human_status(status: ParticipantStatus) -> str:
    return status.value.replace("_", " ")


def _callbook_name(db: Session, callsign: str) -> str | None:
    """Best-effort name prefill. Never raises; a down callbook must not block
    a check-in mid-event. NOTE: lookup_callsign commits internally when it
    refreshes its cache — call this BEFORE taking the event row lock."""
    try:
        result = lookup_callsign(db, callsign)
    except Exception:
        return None
    if result is None:
        return None
    return result.get("name")


def _resolve_post(db: Session, event_id: int, post_id: int) -> EventPost:
    post = db.query(EventPost).filter(EventPost.id == post_id, EventPost.event_id == event_id).one_or_none()
    if post is None:
        raise InvalidPostError("Post does not belong to this event")
    return post


def check_in(
    db: Session,
    event_id: int,
    *,
    callsign: str,
    actor: str,
    name: str | None = None,
    post_id: int | None = None,
    location: str | None = None,
) -> EventParticipant:
    callsign = callsign.strip().upper()
    if not callsign:
        raise DuplicateParticipantError("Callsign is required")

    # Callbook lookup does network I/O and commits its cache — do it before
    # taking the event row lock so the lock isn't held across a network call.
    if name is None:
        name = _callbook_name(db, callsign)

    event = locked_event(db, event_id)
    if event is None or event.status != EventStatus.ACTIVE:
        raise EventNotActiveError("Event is not active")

    post = _resolve_post(db, event_id, post_id) if post_id is not None else None

    existing = (
        db.query(EventParticipant)
        .filter(EventParticipant.event_id == event_id, EventParticipant.callsign == callsign)
        .one_or_none()
    )
    if existing is not None:
        if existing.current_status != ParticipantStatus.CHECKED_OUT:
            raise DuplicateParticipantError(f"{callsign} is already checked in")
        # Re-check-in: new stint on the same row.
        existing.current_status = ParticipantStatus.CHECKED_IN
        existing.checked_in_at = _utcnow()
        existing.checked_out_at = None
        if post is not None:
            existing.post_id = post.id
        if location is not None:
            existing.location = location
        if name is not None:
            existing.name = name
        participant = existing
    else:
        participant = EventParticipant(
            event_id=event_id,
            callsign=callsign,
            name=name,
            post_id=post.id if post is not None else None,
            location=location,
        )
        db.add(participant)
        db.flush()

    message = f"{callsign} checked in"
    if post is not None:
        message += f" at {post.name}"
    add_log_entry(
        db, event, entry_type=EventLogType.SYSTEM, message=message, actor=actor,
        callsign=callsign, new_status=ParticipantStatus.CHECKED_IN,
    )
    db.commit()
    db.refresh(participant)
    return participant


def update_participant(
    db: Session,
    event_id: int,
    participant_id: int,
    *,
    actor: str,
    status: object = _UNSET,
    post_id: object = _UNSET,
    location: object = _UNSET,
    name: object = _UNSET,
) -> EventParticipant | None:
    event = locked_event(db, event_id)
    if event is None:
        return None
    if event.status != EventStatus.ACTIVE:
        raise EventNotActiveError("Event is not active")
    participant = (
        db.query(EventParticipant)
        .filter(EventParticipant.id == participant_id, EventParticipant.event_id == event_id)
        .one_or_none()
    )
    if participant is None:
        return None
    callsign = participant.callsign

    if status is not _UNSET and status is None:
        raise InvalidStatusTransitionError("status cannot be null")
    if status is not _UNSET and status != participant.current_status:
        if (
            participant.current_status == ParticipantStatus.CHECKED_OUT
            and status != ParticipantStatus.CHECKED_IN
        ):
            raise InvalidStatusTransitionError(
                f"{callsign} is checked out — they must check in again first"
            )
        participant.current_status = status
        if status == ParticipantStatus.CHECKED_OUT:
            participant.checked_out_at = _utcnow()
            message = f"{callsign} checked out"
        elif status == ParticipantStatus.CHECKED_IN and participant.checked_out_at is not None:
            participant.checked_in_at = _utcnow()
            participant.checked_out_at = None
            message = f"{callsign} checked in"
        else:
            message = f"{callsign} status: {_human_status(status)}"
        add_log_entry(
            db, event, entry_type=EventLogType.SYSTEM, message=message, actor=actor,
            callsign=callsign, new_status=status,
        )

    if post_id is not _UNSET:
        if post_id is None:
            participant.post_id = None
            add_log_entry(
                db, event, entry_type=EventLogType.SYSTEM,
                message=f"{callsign} unassigned from post", actor=actor, callsign=callsign,
            )
        else:
            post = _resolve_post(db, event_id, post_id)
            participant.post_id = post.id
            add_log_entry(
                db, event, entry_type=EventLogType.SYSTEM,
                message=f"{callsign} assigned to {post.name}", actor=actor, callsign=callsign,
            )

    if location is not _UNSET:
        participant.location = location
        add_log_entry(
            db, event, entry_type=EventLogType.SYSTEM,
            message=f"{callsign} location: {location or '(cleared)'}", actor=actor, callsign=callsign,
        )

    if name is not _UNSET:
        participant.name = name  # correction, not an operational fact — no log entry

    db.commit()
    db.refresh(participant)
    return participant


# --- Notes ----------------------------------------------------------------


def add_note(
    db: Session,
    event_id: int,
    *,
    actor: str,
    message: str,
    callsign: str | None = None,
    pinned: bool = False,
) -> EventLogEntry:
    event = locked_event(db, event_id)
    if event is None or event.status != EventStatus.ACTIVE:
        raise EventNotActiveError("Event is not active")
    callsign = callsign.strip().upper() if callsign else None
    entry_type = EventLogType.PARTICIPANT_NOTE if callsign else EventLogType.NOTE
    entry = add_log_entry(
        db, event, entry_type=entry_type, message=message, actor=actor,
        callsign=callsign, pinned=pinned,
    )
    db.commit()
    db.refresh(entry)
    return entry


def set_log_pinned(db: Session, event_id: int, entry_id: int, pinned: bool) -> EventLogEntry | None:
    event = locked_event(db, event_id)
    if event is None or event.status == EventStatus.CLOSED:
        raise EventNotActiveError("Event is closed")
    entry = (
        db.query(EventLogEntry)
        .filter(EventLogEntry.id == entry_id, EventLogEntry.event_id == event_id)
        .one_or_none()
    )
    if entry is None:
        return None
    entry.pinned = pinned
    db.commit()
    db.refresh(entry)
    return entry


# --- Report ----------------------------------------------------------------


def compute_report(db: Session, event: Event) -> list[dict]:
    """Per-participant stints and total on-event seconds, derived from the
    structured new_status on SYSTEM log entries. An open stint (no checkout)
    counts until event.closed_at, or now for a still-active event; its "end"
    stays None in the payload."""
    participants = (
        db.query(EventParticipant)
        .filter(EventParticipant.event_id == event.id)
        .order_by(EventParticipant.callsign)
        .all()
    )
    entries = (
        db.query(EventLogEntry)
        .filter(
            EventLogEntry.event_id == event.id,
            EventLogEntry.new_status.in_([ParticipantStatus.CHECKED_IN, ParticipantStatus.CHECKED_OUT]),
        )
        .order_by(EventLogEntry.seq)
        .all()
    )
    by_callsign: dict[str, list[EventLogEntry]] = {}
    for entry in entries:
        by_callsign.setdefault(entry.callsign, []).append(entry)

    if event.closed_at is not None:
        cutoff = event.closed_at
    else:
        cutoff = _utcnow()
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)

    report = []
    for participant in participants:
        stints: list[dict] = []
        open_start: datetime | None = None
        for entry in by_callsign.get(participant.callsign, []):
            created = entry.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if entry.new_status == ParticipantStatus.CHECKED_IN and open_start is None:
                open_start = created
            elif entry.new_status == ParticipantStatus.CHECKED_OUT and open_start is not None:
                stints.append({"start": open_start, "end": created})
                open_start = None
        if open_start is not None:
            stints.append({"start": open_start, "end": None})

        total = sum(((s["end"] or cutoff) - s["start"]).total_seconds() for s in stints)
        report.append({
            "callsign": participant.callsign,
            "name": participant.name,
            "post": participant.post.name if participant.post else None,
            "location": participant.location,
            "stints": [
                {"start": s["start"].isoformat(), "end": s["end"].isoformat() if s["end"] else None}
                for s in stints
            ],
            "total_seconds": int(total),
        })
    return report
