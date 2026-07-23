from datetime import datetime
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session
from backend.auth.models import User
from backend.integrations.aprs import manager as aprs_manager
from backend.integrations.weather.service import get_event_alerts
from backend.modules.checkins.mailbox_reader import read_mailbox
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
    ParticipantStatus,
)
from backend.modules.events.service import (
    EventError,
    InvalidPostError,
    InvalidStatusTransitionError,
    activate_event as activate_event_service,
    add_note,
    add_operator,
    check_in,
    close_event as close_event_service,
    compute_report,
    create_event as create_event_service,
    create_post,
    delete_post,
    list_operators,
    remove_operator,
    reopen_event as reopen_event_service,
    rotate_public_token,
    set_log_pinned,
    set_visibility,
    transfer_owner,
    update_event as update_event_service,
    update_participant,
    update_post,
)

events_router = APIRouter(prefix="/api/events", tags=["events"])


# --- Auth helpers ---


def _require_approved_user(user: User = Depends(get_current_user)) -> User:
    if user.is_pending or user.is_deleted:
        raise HTTPException(status_code=403, detail="Account not approved")
    return user


def _require_owner(ctx: EventContext) -> None:
    if not (ctx.user and (ctx.user.is_admin or ctx.event.created_by == ctx.user.callsign)):
        raise HTTPException(status_code=403, detail="Only the owner can do this")


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


class MessageCompose(BaseModel):
    to_address: str
    subject: str = ""
    body: str = ""
    reply_to_id: int | None = None


class MessageStatusUpdate(BaseModel):
    status: MessageStatus


class FormPreview(BaseModel):
    template_path: str
    variables: dict = Field(default_factory=dict)
    datetime_stamp: str = ""


class FormSend(BaseModel):
    template_path: str
    variables: dict = Field(default_factory=dict)
    datetime_stamp: str = ""
    reply_to_id: int | None = None


class OperatorBody(BaseModel):
    callsign: str


class VisibilityBody(BaseModel):
    visibility: str


class TransferBody(BaseModel):
    callsign: str


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
    from backend.integrations.winlink.models import PatConnectionSession
    db.query(PatConnectionSession).filter(PatConnectionSession.event_id == ctx.event.id).update({"event_id": None})
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


# --- Post routes ---


@events_router.post("/{event_id}/posts", status_code=201)
async def create_post_route(
    body: PostCreate,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    try:
        post = create_post(db, ctx.event.id, name=body.name, description=body.description, lat=body.lat, lon=body.lon)
    except EventError as err:
        _raise_for(err)
    aprs_manager.nudge(ctx.event.id)
    return _post_to_response(post)


@events_router.patch("/{event_id}/posts/{post_id}")
async def update_post_route(
    post_id: int,
    body: PostUpdate,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    data = body.model_dump(exclude_unset=True)
    try:
        post = update_post(db, ctx.event.id, post_id, **data)
    except EventError as err:
        _raise_for(err)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    aprs_manager.nudge(ctx.event.id)
    return _post_to_response(post)


@events_router.delete("/{event_id}/posts/{post_id}", status_code=204)
async def delete_post_route(
    post_id: int,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    try:
        deleted = delete_post(db, ctx.event.id, post_id)
    except EventError as err:
        _raise_for(err)
    if not deleted:
        raise HTTPException(status_code=404, detail="Post not found")
    aprs_manager.nudge(ctx.event.id)


# --- Participant routes ---


@events_router.post("/{event_id}/participants", status_code=201)
async def check_in_route(
    body: ParticipantCheckIn,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    try:
        participant = check_in(
            db, ctx.event.id,
            callsign=body.callsign,
            actor=ctx.user.callsign,
            name=body.name,
            post_id=body.post_id,
            location=body.location,
        )
    except EventError as err:
        _raise_for(err)
    aprs_manager.nudge(ctx.event.id)
    return _participant_to_response(participant)


@events_router.patch("/{event_id}/participants/{participant_id}")
async def update_participant_route(
    participant_id: int,
    body: ParticipantUpdate,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    data = body.model_dump(exclude_unset=True)
    try:
        participant = update_participant(db, ctx.event.id, participant_id, actor=ctx.user.callsign, **data)
    except EventError as err:
        _raise_for(err)
    if participant is None:
        raise HTTPException(status_code=404, detail="Participant not found")
    aprs_manager.nudge(ctx.event.id)
    return _participant_to_response(participant)


# --- Log routes ---


@events_router.post("/{event_id}/log", status_code=201)
async def add_note_route(
    body: NoteCreate,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    try:
        entry = add_note(
            db, ctx.event.id,
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
    entry_id: int,
    body: LogPinUpdate,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    try:
        entry = set_log_pinned(db, ctx.event.id, entry_id, body.pinned)
    except EventError as err:
        _raise_for(err)
    if entry is None:
        raise HTTPException(status_code=404, detail="Log entry not found")
    return _log_to_response(entry)


# --- Updates (polling cursor) + report ---


@events_router.get("/{event_id}/updates")
async def updates_route(
    since: int = Query(default=0, ge=0),
    ctx: EventContext = Depends(require_event_role(EventRole.READ)),
    db: Session = Depends(get_db_session),
):
    snapshot = _snapshot(db, ctx.event, ctx)
    log = (
        db.query(EventLogEntry)
        .filter(EventLogEntry.event_id == ctx.event.id, EventLogEntry.seq > since)
        .order_by(EventLogEntry.seq)
        .all()
    )
    snapshot["log"] = [_log_to_response(e) for e in log]
    snapshot["latest_seq"] = ctx.event.log_seq
    return snapshot


@events_router.get("/{event_id}/report")
async def report_route(
    ctx: EventContext = Depends(require_event_role(EventRole.READ)),
    db: Session = Depends(get_db_session),
):
    return {"participants": compute_report(db, ctx.event)}


# --- APRS positions ---


@events_router.get("/{event_id}/positions")
async def positions_route(
    since: int = Query(default=0, ge=0),
    ctx: EventContext = Depends(require_event_role(EventRole.READ)),
    db: Session = Depends(get_db_session),
):
    state = aprs_manager.get_state(ctx.event.id)
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
    ctx: EventContext = Depends(require_event_role(EventRole.READ)),
    db: Session = Depends(get_db_session),
):
    return get_event_alerts(db, ctx.event.id)


# --- Message routes ---


@events_router.get("/{event_id}/messages")
async def list_messages_route(
    since: int = Query(default=0, ge=0),
    include_dismissed: bool = Query(default=False),
    ctx: EventContext = Depends(require_event_role(EventRole.READ)),
    db: Session = Depends(get_db_session),
):
    from backend.integrations.winlink.pat_config import pat_transport_enabled_for_event

    q = db.query(EventMessage).filter(
        EventMessage.event_id == ctx.event.id,
        EventMessage.msg_seq > since,
    )
    if not include_dismissed:
        q = q.filter(EventMessage.status != MessageStatus.DISMISSED)
    messages = q.order_by(EventMessage.msg_seq).all()

    net_address = get_event_config(db, ctx.event.id, "net_address", "") or ""
    mailbox_path = get_event_config(db, ctx.event.id, "pat_mailbox_path", "") or ""
    pat_enabled = pat_transport_enabled_for_event(db, ctx.event.id)
    messaging_configured = bool(net_address and (mailbox_path or pat_enabled))

    return {
        "latest_msg_seq": ctx.event.msg_seq,
        "messaging_configured": messaging_configured,
        "messages": [_message_to_response(m, _message_extras(db, m)) for m in messages],
    }


@events_router.post("/{event_id}/messages", status_code=201)
async def compose_message_route(
    body: MessageCompose,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    from backend.modules.events.message_service import send_event_message
    from backend.modules.events.service import EventNotActiveError

    try:
        message = send_event_message(
            db, ctx.event.id,
            actor=ctx.user.callsign,
            to_address=body.to_address,
            subject=body.subject,
            body=body.body,
            reply_to_id=body.reply_to_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except EventNotActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    extras = _message_extras(db, message)
    return {
        "message": _message_to_response(message, extras),
        "delivered": extras.get("delivery_status") is not None,
    }


@events_router.patch("/{event_id}/messages/{message_id}")
async def patch_message_route(
    message_id: int,
    body: MessageStatusUpdate,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    from backend.modules.events.message_service import set_message_status

    if ctx.event.status != EventStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Event is not active")
    message = set_message_status(db, ctx.event.id, message_id, body.status)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return _message_to_response(message)


@events_router.post("/{event_id}/messages/{message_id}/retry")
async def retry_message_route(
    message_id: int,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    from backend.integrations.delivery.service import retry_failed
    from backend.modules.events.message_service import _build_context
    from backend.modules.events.models import EventMessage

    msg = db.query(EventMessage).filter(
        EventMessage.id == message_id, EventMessage.event_id == ctx.event.id
    ).one_or_none()
    if msg is None:
        raise HTTPException(status_code=404, detail="Message not found")

    config_overrides: dict = {"target_address": msg.to_address}
    subject = msg.subject
    body_text = msg.body

    rec = msg.form_record  # EventMessageForm; None for plain messages
    if rec is not None:
        from backend.modules.forms.builder import FormBuildError, build_form_message
        from backend.modules.events.models import Event

        event = db.get(Event, ctx.event.id)
        try:
            build_ctx = _build_context(db, event, rec.datetime_stamp)
            composed = build_form_message(rec.template_path, rec.variables, build_ctx)
        except (FormBuildError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Cannot rebuild form attachment for retry: {exc}",
            )
        config_overrides["attachments"] = [composed.attachment]
        subject = composed.subject
        body_text = composed.body

    success = retry_failed(
        db, "event_message", message_id,
        event_id=ctx.event.id,
        backends=["winlink"],
        config_overrides=config_overrides,
        subject_override=subject,
        body_override=body_text,
    )
    return {"retried": success}


@events_router.get("/{event_id}/messages/{message_id}/attachments/{attachment_id}")
async def download_attachment_route(
    message_id: int,
    attachment_id: int,
    ctx: EventContext = Depends(require_event_role(EventRole.READ)),
    db: Session = Depends(get_db_session),
):
    from backend.modules.checkins.models import RawMessageAttachment
    from backend.modules.events.models import EventMessage

    msg = db.query(EventMessage).filter(
        EventMessage.id == message_id, EventMessage.event_id == ctx.event.id
    ).one_or_none()
    if msg is None or msg.raw_message_id is None:
        raise HTTPException(status_code=404, detail="Message not found")

    att = db.query(RawMessageAttachment).filter(
        RawMessageAttachment.id == attachment_id,
        RawMessageAttachment.raw_message_id == msg.raw_message_id,
    ).one_or_none()
    if att is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    safe_name = att.filename.replace("\r", "").replace("\n", "").replace('"', "").replace("\\", "")
    safe_name = safe_name.encode("ascii", errors="replace").decode("ascii").replace("?", "_")
    if not safe_name:
        safe_name = "attachment"
    return Response(
        content=att.data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


# --- Rescan route ---


@events_router.post("/{event_id}/rescan")
async def rescan_route(
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    from backend.integrations.winlink.pat_config import (
        build_pat_client, pat_transport_enabled_for_event, resolve_pat_config_for_event,
    )
    from backend.integrations.winlink.pat_inbound import fetch_inbound_messages
    from backend.integrations.winlink.pat_client import PatUnavailable
    from backend.modules.events.messages import route_event_messages

    if ctx.event.status != EventStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Event is not active")

    net_address = get_event_config(db, ctx.event.id, "net_address", "") or ""
    mailbox_path = get_event_config(db, ctx.event.id, "pat_mailbox_path", "") or ""

    if pat_transport_enabled_for_event(db, ctx.event.id):
        cfg = resolve_pat_config_for_event(db, ctx.event.id)
        client = build_pat_client(cfg)
        try:
            raw_messages = fetch_inbound_messages(client)
            if net_address:
                from backend.modules.checkins.mailbox_reader import _to_matches_net
                raw_messages = [m for m in raw_messages if _to_matches_net(net_address, m.get("to_address", ""))]
        except PatUnavailable as exc:
            raise HTTPException(status_code=502, detail=f"PAT unreachable: {exc}")
    elif mailbox_path and net_address:
        import os
        inbox_path = os.path.join(mailbox_path, "in")
        raw_messages = read_mailbox(inbox_path, net_address)
    else:
        raise HTTPException(status_code=409, detail="Messaging not configured for this event")

    # Persist raw message rows so route_event_messages can resolve them by message_id.
    from backend.integrations.scanner.service import _persist_raw_messages
    _persist_raw_messages(db, raw_messages)

    new_messages = route_event_messages(db, None, raw_messages)
    return {"new_messages": new_messages}


# --- Form routes ---


@events_router.post("/{event_id}/forms/preview")
async def form_preview_route(
    body: FormPreview,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    from backend.modules.events.message_service import compose_form_preview

    try:
        result = compose_form_preview(
            db, ctx.event.id,
            template_path=body.template_path,
            variables=body.variables,
            datetime_stamp=body.datetime_stamp,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result


@events_router.post("/{event_id}/form-messages", status_code=201)
async def form_send_route(
    body: FormSend,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    from backend.modules.events.message_service import send_event_form_message
    from backend.modules.events.service import EventNotActiveError

    try:
        message = send_event_form_message(
            db, ctx.event.id,
            actor=ctx.user.callsign,
            template_path=body.template_path,
            variables=body.variables,
            datetime_stamp=body.datetime_stamp,
            reply_to_id=body.reply_to_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except EventNotActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    extras = _message_extras(db, message)
    return {
        "message": _message_to_response(message, extras),
        "delivered": extras.get("delivery_status") is not None,
    }


@events_router.get("/{event_id}/messages/{message_id}/reply-form")
async def reply_form_route(
    message_id: int,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    from backend.modules.events.message_service import resolve_reply_form

    try:
        result = resolve_reply_form(db, ctx.event.id, message_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return result


# --- Owner-only routes ---


@events_router.post("/{event_id}/operators", status_code=201)
async def add_operator_route(
    body: OperatorBody,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    _require_owner(ctx)
    add_operator(db, ctx.event, body.callsign, added_by=ctx.user.callsign)
    return {"operators": list_operators(db, ctx.event)}


@events_router.delete("/{event_id}/operators/{callsign}", status_code=204)
async def remove_operator_route(
    callsign: str,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    _require_owner(ctx)
    remove_operator(db, ctx.event, callsign)


@events_router.patch("/{event_id}/visibility")
async def set_visibility_route(
    body: VisibilityBody,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    _require_owner(ctx)
    try:
        set_visibility(db, ctx.event, body.visibility)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"visibility": ctx.event.visibility}


@events_router.post("/{event_id}/token/rotate")
async def rotate_token_route(
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    _require_owner(ctx)
    return {"public_token": rotate_public_token(db, ctx.event)}


@events_router.post("/{event_id}/transfer")
async def transfer_route(
    body: TransferBody,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    _require_owner(ctx)
    transfer_owner(db, ctx.event, body.callsign)
    return {"owner": ctx.event.created_by}
