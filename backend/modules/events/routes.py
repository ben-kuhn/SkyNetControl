from datetime import datetime
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session
from backend.auth.models import User
from backend.integrations.aprs import manager as aprs_manager
from backend.modules.events.event_auth import EventContext, EventRole, require_event_role
from backend.modules.events.event_config_service import get_event_config
from backend.modules.events.models import (
    Event,
    EventLogEntry,
    EventMessage,
    EventOperator,
    EventParticipant,
    EventPost,
    EventStatus,
    EventType,
    MessageStatus,
)
from backend.modules.events.service import (
    EventError,
    InvalidPostError,
    InvalidStatusTransitionError,
    activate_event as activate_event_service,
    close_event as close_event_service,
    create_event as create_event_service,
    list_operators,
    reopen_event as reopen_event_service,
    update_event as update_event_service,
)

events_router = APIRouter(prefix="/api/events", tags=["events"])


# --- Auth helpers ---


def _require_approved_user(user: User = Depends(get_current_user)) -> User:
    if user.is_pending or user.is_deleted:
        raise HTTPException(status_code=403, detail="Account not approved")
    return user


# --- Pydantic schemas ---


class EventCreate(BaseModel):
    name: str = Field(min_length=1)
    event_type: EventType
    description: str | None = None
    scheduled_start: str | None = None


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


class MessageCompose(BaseModel):
    to_address: str
    subject: str = ""
    body: str = ""
    reply_to_id: int | None = None


class MessageStatusUpdate(BaseModel):
    status: MessageStatus


# --- Helpers (kept net-free; Tasks 10-12 add sub-resource routes that use these) ---


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _event_to_response(db: Session, event: Event, ctx: EventContext | None) -> dict:
    is_control = bool(ctx and ctx.is_control)
    out = {
        "id": event.id,
        "name": event.name,
        "description": event.description,
        "event_type": event.event_type.value,
        "status": event.status.value,
        "owner": event.created_by,
        "created_by": event.created_by,
        "visibility": event.visibility,
        "created_at": _iso(event.created_at),
        "scheduled_start": _iso(event.scheduled_start),
        "activated_at": _iso(event.activated_at),
        "closed_at": _iso(event.closed_at),
        "aprs_other_stations": event.aprs_other_stations,
        "aprs_range_lat": event.aprs_range_lat,
        "aprs_range_lon": event.aprs_range_lon,
        "aprs_range_km": event.aprs_range_km,
        "aprs_beacon_posts": event.aprs_beacon_posts,
        "weather_enabled": (get_event_config(db, event.id, "weather.enabled") == "true"),
        "is_control": is_control,
    }
    if is_control:
        out["operators"] = list_operators(db, event)
        out["public_token"] = event.public_token
    return out


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


def _message_extras(db: Session, m: EventMessage) -> dict:
    """Attachment summary + received-form metadata for a message, from its
    linked RawMessage. Empty/None for outbound or attachment-less messages.

    Avoids loading BLOB data for the summary — only fetches sizes via
    func.length() so polling never materialises multi-MB payloads. For the
    form block, only the data of RMS_Express_Form_*.xml attachments is read.

    For outbound messages, also includes delivery_status from the winlink
    DeliveryLog (None if no row exists).
    """
    import xml.etree.ElementTree as ET

    from sqlalchemy import func

    from backend.integrations.delivery.models import DeliveryLog
    from backend.modules.checkins.message_parser import find_form_xml
    from backend.modules.checkins.models import RawMessage, RawMessageAttachment
    from backend.modules.events.models import MessageDirection

    # Delivery status for outbound messages only.
    delivery_status = None
    if m.direction == MessageDirection.OUTBOUND:
        log = (
            db.query(DeliveryLog)
            .filter(
                DeliveryLog.content_type == "event_message",
                DeliveryLog.content_id == m.id,
                DeliveryLog.backend == "winlink",
            )
            .one_or_none()
        )
        if log is not None:
            delivery_status = log.status.value

    if m.raw_message_id is None:
        return {"attachments": [], "form": None, "delivery_status": delivery_status}

    # Summary: id, filename, content_type, size — no BLOB bytes fetched.
    rows = (
        db.query(
            RawMessageAttachment.id,
            RawMessageAttachment.filename,
            RawMessageAttachment.content_type,
            func.length(RawMessageAttachment.data).label("size"),
        )
        .filter(RawMessageAttachment.raw_message_id == m.raw_message_id)
        .all()
    )
    summary = [
        {"id": r.id, "filename": r.filename, "content_type": r.content_type, "size": r.size}
        for r in rows
    ]

    # Form block: only fetch data for RMS_Express_Form_*.xml attachments.
    raw = db.get(RawMessage, m.raw_message_id)
    form_atts = (
        db.query(RawMessageAttachment)
        .filter(
            RawMessageAttachment.raw_message_id == m.raw_message_id,
            RawMessageAttachment.filename.ilike("RMS_Express_Form_%.xml"),
        )
        .all()
    )
    att_dicts = [{"filename": a.filename, "content_type": a.content_type, "data": a.data} for a in form_atts]
    xml_text = find_form_xml(att_dicts, raw.body if raw else "")
    form = None
    if xml_text:
        try:
            root = ET.fromstring(xml_text)
            df = root.find(".//form_parameters/display_form")
            rt = root.find(".//form_parameters/reply_template")
            form = {
                "is_form": True,
                "display_form": (df.text or "").strip() if df is not None else "",
                "reply_template": (rt.text or "").strip() if rt is not None else "",
            }
        except ET.ParseError:
            form = None
    return {"attachments": summary, "form": form, "delivery_status": delivery_status}


def _message_to_response(m: EventMessage, extras: dict | None = None) -> dict:
    resp = {
        "id": m.id,
        "msg_seq": m.msg_seq,
        "direction": m.direction.value,
        "raw_message_id": m.raw_message_id,
        "participant_id": m.participant_id,
        "from_callsign": m.from_callsign,
        "to_address": m.to_address,
        "subject": m.subject,
        "body": m.body,
        "status": m.status.value,
        "reply_to_id": m.reply_to_id,
        "actor": m.actor,
        "received_at": _iso(m.received_at),
        "created_at": _iso(m.created_at),
    }
    resp["attachments"] = (extras or {}).get("attachments", [])
    resp["form"] = (extras or {}).get("form")
    resp["delivery_status"] = (extras or {}).get("delivery_status")
    return resp


def _raise_for(err: EventError) -> NoReturn:
    if isinstance(err, (InvalidStatusTransitionError, InvalidPostError)):
        raise HTTPException(status_code=422, detail=str(err))
    raise HTTPException(status_code=409, detail=str(err))


def _snapshot(db: Session, event: Event, ctx: EventContext | None) -> dict:
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
        "event": _event_to_response(db, event, ctx),
        "posts": [_post_to_response(p) for p in posts],
        "participants": [_participant_to_response(p) for p in participants],
        "log": [_log_to_response(e) for e in log],
        # pinned is the one mutable log field; ride the full-log state, not the delta
        "pinned_seqs": [e.seq for e in log if e.pinned],
    }


# --- CRUD routes ---


@events_router.post("", status_code=201)
async def create_event_route(
    body: EventCreate,
    user: User = Depends(_require_approved_user),
    db: Session = Depends(get_db_session),
):
    event = create_event_service(
        db,
        name=body.name,
        event_type=body.event_type,
        created_by=user.callsign,
        description=body.description,
        scheduled_start=body.scheduled_start,
    )
    return _event_to_response(db, event, EventContext(user=user, event=event, is_control=True))


@events_router.get("")
async def list_mine_route(
    user: User = Depends(_require_approved_user),
    db: Session = Depends(get_db_session),
):
    op_ids = [o.event_id for o in db.query(EventOperator).filter(EventOperator.callsign == user.callsign).all()]
    q = db.query(Event).filter((Event.created_by == user.callsign) | (Event.id.in_(op_ids)))
    events = q.order_by(Event.created_at.desc()).all()
    return [_event_to_response(db, e, EventContext(user=user, event=e, is_control=True)) for e in events]


@events_router.get("/public")
async def list_public_route(db: Session = Depends(get_db_session)):
    events = (
        db.query(Event)
        .filter(Event.visibility == "public", Event.status == EventStatus.ACTIVE)
        .order_by(Event.activated_at.desc())
        .all()
    )
    return [_event_to_response(db, e, None) for e in events]


@events_router.get("/{event_id}")
async def get_event_route(
    ctx: EventContext = Depends(require_event_role(EventRole.READ)),
    db: Session = Depends(get_db_session),
):
    return _snapshot(db, ctx.event, ctx)


@events_router.patch("/{event_id}")
async def update_event_route(
    request: Request,
    body: EventUpdate,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    data = body.model_dump(exclude_unset=True)
    try:
        event = update_event_service(db, ctx.event.id, **data)
    except EventError as err:
        _raise_for(err)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    aprs_manager.ensure_started(request.app.state.session_factory, ctx.event.id)
    aprs_manager.nudge(ctx.event.id)
    return _event_to_response(db, event, ctx)


@events_router.delete("/{event_id}", status_code=204)
async def delete_event_route(
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    if ctx.event.created_by != ctx.user.callsign and not ctx.user.is_admin:
        raise HTTPException(status_code=403, detail="Only the owner can delete")
    aprs_manager.stop(ctx.event.id)
    db.delete(ctx.event)
    db.commit()


# --- Lifecycle routes ---


@events_router.post("/{event_id}/activate")
async def activate_event_route(
    request: Request,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    try:
        event = activate_event_service(db, ctx.event.id, actor=ctx.user.callsign)
    except EventError as err:
        _raise_for(err)
    aprs_manager.ensure_started(request.app.state.session_factory, ctx.event.id)
    return _event_to_response(db, event, ctx)


@events_router.post("/{event_id}/close")
async def close_event_route(
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    try:
        event = close_event_service(db, ctx.event.id, actor=ctx.user.callsign)
    except EventError as err:
        _raise_for(err)
    aprs_manager.stop(ctx.event.id)
    return _event_to_response(db, event, ctx)


@events_router.post("/{event_id}/reopen")
async def reopen_event_route(
    request: Request,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    try:
        event = reopen_event_service(db, ctx.event.id, actor=ctx.user.callsign)
    except EventError as err:
        _raise_for(err)
    aprs_manager.ensure_started(request.app.state.session_factory, ctx.event.id)
    return _event_to_response(db, event, ctx)
