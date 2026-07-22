from datetime import datetime
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth.dependencies import NetContext, get_db_session, require_net_role
from backend.integrations.aprs import manager as aprs_manager
from backend.integrations.weather.service import get_event_alerts
from backend.modules.checkins.mailbox_reader import read_mailbox
from backend.modules.nets.config_service import get_net_config
from backend.modules.events.message_service import send_event_message, set_message_status
from backend.modules.events.messages import route_event_messages
from backend.modules.events.models import (
    Event,
    EventLogEntry,
    EventMessage,
    EventParticipant,
    EventPost,
    EventStatus,
    EventType,
    MessageStatus,
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


class MessageCompose(BaseModel):
    to_address: str
    subject: str = ""
    body: str = ""
    reply_to_id: int | None = None


class MessageStatusUpdate(BaseModel):
    status: MessageStatus


# --- Helpers ---


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _event_to_response(event: Event, *, weather_enabled: bool = False) -> dict:
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
        "weather_enabled": weather_enabled,
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


def _get_event_or_404(db: Session, net_id: int, event_id: int) -> Event:
    event = db.get(Event, event_id)
    if event is None or event.net_id != net_id:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def _raise_for(err: EventError) -> NoReturn:
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
    weather_enabled = get_net_config(db, event.net_id, "weather.enabled") == "true"
    return {
        "event": _event_to_response(event, weather_enabled=weather_enabled),
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
    weather_enabled = get_net_config(db, ctx.net.id, "weather.enabled") == "true"
    return [_event_to_response(e, weather_enabled=weather_enabled) for e in events]


@events_router.post("", status_code=201)
async def create_event_route(
    request: Request,
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
    if event.status == EventStatus.ACTIVE:
        aprs_manager.ensure_started(request.app.state.session_factory, event.id)
    weather_enabled = get_net_config(db, event.net_id, "weather.enabled") == "true"
    return _event_to_response(event, weather_enabled=weather_enabled)


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
    request: Request,
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
    aprs_manager.ensure_started(request.app.state.session_factory, event_id)
    aprs_manager.nudge(event_id)
    weather_enabled = get_net_config(db, event.net_id, "weather.enabled") == "true"
    return _event_to_response(event, weather_enabled=weather_enabled)


@events_router.post("/{event_id}/activate")
async def activate_event_route(
    request: Request,
    event_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        event = activate_event_service(db, event_id, actor=ctx.user.callsign)
    except EventError as err:
        _raise_for(err)
    aprs_manager.ensure_started(request.app.state.session_factory, event_id)
    weather_enabled = get_net_config(db, event.net_id, "weather.enabled") == "true"
    return _event_to_response(event, weather_enabled=weather_enabled)


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
    aprs_manager.stop(event_id)
    weather_enabled = get_net_config(db, event.net_id, "weather.enabled") == "true"
    return _event_to_response(event, weather_enabled=weather_enabled)


@events_router.post("/{event_id}/reopen")
async def reopen_event_route(
    request: Request,
    event_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        event = reopen_event_service(db, event_id, actor=ctx.user.callsign)
    except EventError as err:
        _raise_for(err)
    aprs_manager.ensure_started(request.app.state.session_factory, event_id)
    weather_enabled = get_net_config(db, event.net_id, "weather.enabled") == "true"
    return _event_to_response(event, weather_enabled=weather_enabled)


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
    aprs_manager.nudge(event_id)
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
    aprs_manager.nudge(event_id)
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
    aprs_manager.nudge(event_id)


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
    aprs_manager.nudge(event_id)
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
    aprs_manager.nudge(event_id)
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


# --- APRS positions (sub-project 2) ---


@events_router.get("/{event_id}/positions")
async def positions_route(
    event_id: int,
    since: int = Query(default=0, ge=0),
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    state = aprs_manager.get_state(event_id)
    if state is None:
        return {
            "stations": [], "latest_pos_seq": 0,
            "aprs_status": "disabled", "aprs_status_detail": "", "objects": [],
        }
    snapshot = state.store.snapshot(since)
    snapshot["aprs_status"] = state.status
    snapshot["aprs_status_detail"] = state.status_detail
    snapshot["objects"] = [
        {"post_id": post_id, "name": name} for post_id, name in sorted(state.objects_by_post.items())
    ]
    return snapshot


# --- Weather alerts ---


@events_router.get("/{event_id}/weather")
async def weather_route(
    event_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    return get_event_alerts(db, event_id)


# --- Message routes ---


@events_router.get("/{event_id}/messages")
async def list_messages_route(
    event_id: int,
    since: int = Query(default=0, ge=0),
    include_dismissed: bool = Query(default=False),
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    event = _get_event_or_404(db, ctx.net.id, event_id)
    query = db.query(EventMessage).filter(
        EventMessage.event_id == event.id, EventMessage.msg_seq > since
    )
    if not include_dismissed:
        query = query.filter(EventMessage.status != MessageStatus.DISMISSED)
    messages = query.order_by(EventMessage.msg_seq).all()
    from backend.integrations.winlink.pat_config import pat_transport_enabled as _pat_enabled

    pat_mailbox = get_net_config(db, event.net_id, "pat_mailbox_path", "") or ""
    net_addr = get_net_config(db, event.net_id, "net_address", "") or ""
    messaging_configured = bool(pat_mailbox and net_addr) or _pat_enabled(db, event.net_id)
    return {
        "messages": [_message_to_response(m, _message_extras(db, m)) for m in messages],
        "latest_msg_seq": event.msg_seq,
        "messaging_configured": messaging_configured,
    }


@events_router.post("/{event_id}/messages", status_code=201)
async def compose_message_route(
    event_id: int,
    body: MessageCompose,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        message = send_event_message(
            db, event_id, actor=ctx.user.callsign,
            to_address=body.to_address, subject=body.subject, body=body.body,
            reply_to_id=body.reply_to_id,
        )
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err))
    except EventError as err:
        _raise_for(err)
    from backend.integrations.delivery.models import DeliveryStatus
    from backend.integrations.delivery.service import get_delivery_status

    logs = get_delivery_status(db, "event_message", message.id)
    delivered = any(log.status == DeliveryStatus.SENT for log in logs)
    return {"message": _message_to_response(message, _message_extras(db, message)), "delivered": delivered}


@events_router.patch("/{event_id}/messages/{message_id}")
async def patch_message_route(
    event_id: int,
    message_id: int,
    body: MessageStatusUpdate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    event = _get_event_or_404(db, ctx.net.id, event_id)
    if event.status != EventStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Event is not active")
    message = set_message_status(db, event_id, message_id, body.status)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return _message_to_response(message, _message_extras(db, message))


@events_router.post("/{event_id}/rescan")
async def rescan_route(
    event_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    import os

    from backend.integrations.scanner.service import _persist_raw_messages
    from backend.integrations.winlink.pat_config import (
        pat_transport_enabled as _pat_enabled,
        resolve_pat_config,
        build_pat_client,
    )
    from backend.integrations.winlink.pat_client import PatUnavailable
    from backend.integrations.winlink.pat_inbound import fetch_inbound_messages

    event = _get_event_or_404(db, ctx.net.id, event_id)
    if event.status != EventStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Event is not active")

    net_address = get_net_config(db, ctx.net.id, "net_address", "") or ""
    if not net_address:
        return {"new_messages": 0}

    if _pat_enabled(db, ctx.net.id):
        # Remote-PAT path: fetch inbound via HTTP API and filter to this net.
        from backend.modules.checkins.mailbox_reader import _to_matches_net

        client = build_pat_client(resolve_pat_config(db, ctx.net.id))
        try:
            raw = fetch_inbound_messages(client)
        except PatUnavailable:
            return {"new_messages": 0}
        messages = [m for m in raw if _to_matches_net(net_address, m["to_address"])]
    else:
        mailbox = get_net_config(db, ctx.net.id, "pat_mailbox_path", "") or ""
        if not mailbox:
            return {"new_messages": 0}
        messages = read_mailbox(os.path.join(mailbox, "in"), net_address)

    _persist_raw_messages(db, messages)
    before = event.msg_seq
    route_event_messages(db, ctx.net.id, messages)
    db.refresh(event)
    return {"new_messages": event.msg_seq - before}


@events_router.get("/{event_id}/messages/{message_id}/attachments/{attachment_id}")
async def download_attachment_route(
    event_id: int,
    message_id: int,
    attachment_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    from backend.modules.checkins.models import RawMessageAttachment

    _get_event_or_404(db, ctx.net.id, event_id)
    message = (
        db.query(EventMessage)
        .filter(EventMessage.id == message_id, EventMessage.event_id == event_id)
        .one_or_none()
    )
    if message is None or message.raw_message_id is None:
        raise HTTPException(status_code=404, detail="Not found")
    att = (
        db.query(RawMessageAttachment)
        .filter(RawMessageAttachment.id == attachment_id,
                RawMessageAttachment.raw_message_id == message.raw_message_id)
        .one_or_none()
    )
    if att is None:
        raise HTTPException(status_code=404, detail="Not found")
    # Sanitize the download filename; never serve the claimed content-type
    # (a hostile form XML must download, never render).
    # Strip control chars and header-injection sequences, then ASCII-fold to
    # avoid latin-1 encoding errors at the HTTP header boundary.
    safe_name = att.filename.replace("\r", "").replace("\n", "").replace('"', "").replace("\\", "")
    safe_name = safe_name.encode("ascii", errors="replace").decode("ascii").replace("?", "_")
    if not safe_name:
        safe_name = "attachment"
    return Response(
        content=att.data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


# --- Form compose / send / reply-form routes ---


class FormComposeBody(BaseModel):
    template_path: str
    variables: dict = {}
    datetime_stamp: str
    reply_to_id: int | None = None


@events_router.post("/{event_id}/forms/preview")
async def form_preview_route(
    event_id: int, body: FormComposeBody,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    from backend.modules.events.message_service import compose_form_preview
    try:
        return compose_form_preview(db, event_id, template_path=body.template_path,
                                    variables=body.variables, datetime_stamp=body.datetime_stamp)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@events_router.post("/{event_id}/form-messages", status_code=201)
async def form_send_route(
    event_id: int, body: FormComposeBody,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    from backend.modules.events.message_service import send_event_form_message
    try:
        message = send_event_form_message(
            db, event_id, actor=ctx.user.callsign, template_path=body.template_path,
            variables=body.variables, datetime_stamp=body.datetime_stamp, reply_to_id=body.reply_to_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except EventError as err:
        _raise_for(err)
    from backend.integrations.delivery.service import get_delivery_status
    from backend.integrations.delivery.models import DeliveryStatus
    logs = get_delivery_status(db, "event_message", message.id)
    delivered = any(log.status == DeliveryStatus.SENT for log in logs)
    return {"message": _message_to_response(message, _message_extras(db, message)), "delivered": delivered}


@events_router.get("/{event_id}/messages/{message_id}/reply-form")
async def reply_form_route(
    event_id: int, message_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    from backend.modules.events.message_service import resolve_reply_form
    try:
        return resolve_reply_form(db, event_id, message_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
