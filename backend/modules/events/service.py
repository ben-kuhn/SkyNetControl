from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.modules.events.models import (
    Event,
    EventLogEntry,
    EventLogType,
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
    net_id: int,
    name: str,
    event_type: EventType,
    created_by: str,
    description: str | None = None,
    scheduled_start: datetime | None = None,
    activate: bool = False,
) -> Event:
    event = Event(
        net_id=net_id,
        name=name,
        event_type=event_type,
        description=description,
        scheduled_start=scheduled_start,
        created_by=created_by,
    )
    db.add(event)
    db.flush()
    if activate:
        event.status = EventStatus.ACTIVE
        event.activated_at = _utcnow()
        add_log_entry(db, event, entry_type=EventLogType.SYSTEM, message="Event activated", actor=created_by)
    db.commit()
    db.refresh(event)
    return event


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
    post = db.query(EventPost).filter(EventPost.id == post_id, EventPost.event_id == event_id).one_or_none()
    if post is None:
        return False
    assigned = db.query(EventParticipant).filter(EventParticipant.post_id == post_id).first()
    if assigned is not None:
        raise PostAssignedError("Post has assigned participants")
    db.delete(post)
    db.commit()
    return True
