from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth.dependencies import NetContext, get_db_session, require_net_role
from backend.modules.events.models import (
    Event,
    EventLogEntry,
    EventParticipant,
    EventPost,
    EventType,
    ParticipantStatus,
)
from backend.modules.events.service import (
    EventError,
    InvalidPostError,
    InvalidStatusTransitionError,
    activate_event as activate_event_service,
    add_note as add_note_service,
    check_in as check_in_service,
    close_event as close_event_service,
    compute_report as compute_report_service,
    create_event as create_event_service,
    create_post as create_post_service,
    delete_post as delete_post_service,
    reopen_event as reopen_event_service,
    set_log_pinned as set_log_pinned_service,
    update_event as update_event_service,
    update_participant as update_participant_service,
    update_post as update_post_service,
)
from backend.modules.nets.models import NetRole

events_router = APIRouter(prefix="/api/nets/{net_slug}/events", tags=["events"])


# --- Pydantic schemas ---


class EventCreate(BaseModel):
    name: str = Field(min_length=1)
    event_type: EventType
    description: str | None = None
    scheduled_start: datetime | None = None
    activate: bool = False


class EventUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    scheduled_start: datetime | None = None
    aprs_other_stations: bool | None = None
    aprs_range_lat: float | None = None
    aprs_range_lon: float | None = None
    aprs_range_km: float | None = None
    aprs_beacon_posts: bool | None = None


class PostCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    lat: float | None = None
    lon: float | None = None


class PostUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    lat: float | None = None
    lon: float | None = None


# --- Helpers ---


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _event_to_response(event: Event) -> dict:
    return {
        "id": event.id,
        "net_id": event.net_id,
        "name": event.name,
        "description": event.description,
        "event_type": event.event_type.value,
        "status": event.status.value,
        "scheduled_start": _iso(event.scheduled_start),
        "activated_at": _iso(event.activated_at),
        "closed_at": _iso(event.closed_at),
        "created_by": event.created_by,
        "created_at": _iso(event.created_at),
        "aprs_other_stations": event.aprs_other_stations,
        "aprs_range_lat": event.aprs_range_lat,
        "aprs_range_lon": event.aprs_range_lon,
        "aprs_range_km": event.aprs_range_km,
        "aprs_beacon_posts": event.aprs_beacon_posts,
    }


def _post_to_response(post: EventPost) -> dict:
    return {
        "id": post.id,
        "event_id": post.event_id,
        "name": post.name,
        "description": post.description,
        "lat": post.lat,
        "lon": post.lon,
    }


def _participant_to_response(p: EventParticipant) -> dict:
    return {
        "id": p.id,
        "event_id": p.event_id,
        "callsign": p.callsign,
        "name": p.name,
        "post_id": p.post_id,
        "location": p.location,
        "current_status": p.current_status.value,
        "checked_in_at": _iso(p.checked_in_at),
        "checked_out_at": _iso(p.checked_out_at),
    }


def _log_to_response(entry: EventLogEntry) -> dict:
    return {
        "id": entry.id,
        "seq": entry.seq,
        "entry_type": entry.entry_type.value,
        "callsign": entry.callsign,
        "actor": entry.actor,
        "message": entry.message,
        "new_status": entry.new_status.value if entry.new_status else None,
        "pinned": entry.pinned,
        "created_at": _iso(entry.created_at),
    }


def _get_event_or_404(db: Session, net_id: int, event_id: int) -> Event:
    event = db.get(Event, event_id)
    if event is None or event.net_id != net_id:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def _raise_for(err: EventError):
    if isinstance(err, (InvalidStatusTransitionError, InvalidPostError)):
        raise HTTPException(status_code=422, detail=str(err))
    raise HTTPException(status_code=409, detail=str(err))


def _snapshot(db: Session, event: Event) -> dict:
    posts = db.query(EventPost).filter(EventPost.event_id == event.id).order_by(EventPost.name).all()
    participants = (
        db.query(EventParticipant)
        .filter(EventParticipant.event_id == event.id)
        .order_by(EventParticipant.callsign)
        .all()
    )
    log = (
        db.query(EventLogEntry)
        .filter(EventLogEntry.event_id == event.id)
        .order_by(EventLogEntry.seq)
        .all()
    )
    return {
        "event": _event_to_response(event),
        "posts": [_post_to_response(p) for p in posts],
        "participants": [_participant_to_response(p) for p in participants],
        "log": [_log_to_response(e) for e in log],
        # pinned is the one mutable log field; ride the full-log state, not the delta
        "pinned_seqs": [e.seq for e in log if e.pinned],
    }


# --- Event lifecycle routes ---


@events_router.get("")
async def list_events_route(
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    events = db.query(Event).filter(Event.net_id == ctx.net.id).order_by(Event.created_at.desc()).all()
    return [_event_to_response(e) for e in events]


@events_router.post("", status_code=201)
async def create_event_route(
    body: EventCreate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    event = create_event_service(
        db,
        net_id=ctx.net.id,
        name=body.name,
        event_type=body.event_type,
        created_by=ctx.user.callsign,
        description=body.description,
        scheduled_start=body.scheduled_start,
        activate=body.activate,
    )
    return _event_to_response(event)


@events_router.get("/{event_id}")
async def get_event_route(
    event_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    event = _get_event_or_404(db, ctx.net.id, event_id)
    return _snapshot(db, event)


@events_router.patch("/{event_id}")
async def update_event_route(
    event_id: int,
    body: EventUpdate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    data = body.model_dump(exclude_unset=True)
    try:
        event = update_event_service(db, event_id, **data)
    except EventError as err:
        _raise_for(err)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return _event_to_response(event)


@events_router.post("/{event_id}/activate")
async def activate_event_route(
    event_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        event = activate_event_service(db, event_id, actor=ctx.user.callsign)
    except EventError as err:
        _raise_for(err)
    return _event_to_response(event)


@events_router.post("/{event_id}/close")
async def close_event_route(
    event_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        event = close_event_service(db, event_id, actor=ctx.user.callsign)
    except EventError as err:
        _raise_for(err)
    return _event_to_response(event)


@events_router.post("/{event_id}/reopen")
async def reopen_event_route(
    event_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        event = reopen_event_service(db, event_id, actor=ctx.user.callsign)
    except EventError as err:
        _raise_for(err)
    return _event_to_response(event)


# --- Post routes ---


@events_router.post("/{event_id}/posts", status_code=201)
async def create_post_route(
    event_id: int,
    body: PostCreate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        post = create_post_service(
            db, event_id, name=body.name, description=body.description, lat=body.lat, lon=body.lon
        )
    except EventError as err:
        _raise_for(err)
    return _post_to_response(post)


@events_router.patch("/{event_id}/posts/{post_id}")
async def update_post_route(
    event_id: int,
    post_id: int,
    body: PostUpdate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    data = body.model_dump(exclude_unset=True)
    try:
        post = update_post_service(db, event_id, post_id, **data)
    except EventError as err:
        _raise_for(err)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return _post_to_response(post)


@events_router.delete("/{event_id}/posts/{post_id}", status_code=204)
async def delete_post_route(
    event_id: int,
    post_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        deleted = delete_post_service(db, event_id, post_id)
    except EventError as err:
        _raise_for(err)
    if not deleted:
        raise HTTPException(status_code=404, detail="Post not found")


# --- Participant / log schemas ---


class ParticipantCheckIn(BaseModel):
    callsign: str
    name: str | None = None
    post_id: int | None = None
    location: str | None = None


class ParticipantUpdate(BaseModel):
    status: ParticipantStatus | None = None
    post_id: int | None = None
    location: str | None = None
    name: str | None = None


class NoteCreate(BaseModel):
    message: str
    callsign: str | None = None
    pinned: bool = False


class LogPinUpdate(BaseModel):
    pinned: bool


# --- Participant routes ---


@events_router.post("/{event_id}/participants", status_code=201)
async def check_in_route(
    event_id: int,
    body: ParticipantCheckIn,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        participant = check_in_service(
            db, event_id,
            callsign=body.callsign,
            actor=ctx.user.callsign,
            name=body.name,
            post_id=body.post_id,
            location=body.location,
        )
    except EventError as err:
        _raise_for(err)
    return _participant_to_response(participant)


@events_router.patch("/{event_id}/participants/{participant_id}")
async def update_participant_route(
    event_id: int,
    participant_id: int,
    body: ParticipantUpdate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    data = body.model_dump(exclude_unset=True)
    try:
        participant = update_participant_service(
            db, event_id, participant_id, actor=ctx.user.callsign, **data
        )
    except EventError as err:
        _raise_for(err)
    if participant is None:
        raise HTTPException(status_code=404, detail="Participant not found")
    return _participant_to_response(participant)


# --- Log routes ---


@events_router.post("/{event_id}/log", status_code=201)
async def add_note_route(
    event_id: int,
    body: NoteCreate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        entry = add_note_service(
            db, event_id,
            actor=ctx.user.callsign,
            message=body.message,
            callsign=body.callsign,
            pinned=body.pinned,
        )
    except EventError as err:
        _raise_for(err)
    return _log_to_response(entry)


@events_router.patch("/{event_id}/log/{entry_id}")
async def pin_log_route(
    event_id: int,
    entry_id: int,
    body: LogPinUpdate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        entry = set_log_pinned_service(db, event_id, entry_id, body.pinned)
    except EventError as err:
        _raise_for(err)
    if entry is None:
        raise HTTPException(status_code=404, detail="Log entry not found")
    return _log_to_response(entry)


# --- Updates (polling cursor) + report ---


@events_router.get("/{event_id}/updates")
async def updates_route(
    event_id: int,
    since: int = Query(default=0, ge=0),
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    event = _get_event_or_404(db, ctx.net.id, event_id)
    snapshot = _snapshot(db, event)
    log = (
        db.query(EventLogEntry)
        .filter(EventLogEntry.event_id == event.id, EventLogEntry.seq > since)
        .order_by(EventLogEntry.seq)
        .all()
    )
    snapshot["log"] = [_log_to_response(e) for e in log]
    snapshot["latest_seq"] = event.log_seq
    return snapshot


@events_router.get("/{event_id}/report")
async def report_route(
    event_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    event = _get_event_or_404(db, ctx.net.id, event_id)
    return {"participants": compute_report_service(db, event)}
