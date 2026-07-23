"""Outbound event Winlink messages and message-status transitions.

Outbound send reuses the existing delivery pipeline (dispatch_delivery) with the
'event_message' content type — no new backend. Messages go out under the event
callsign (derived from event config / creator callsign via event_from_callsign).
Outbound event messages are always delivered via the winlink backend only,
addressed directly to the composed recipient (to_address)."""
from sqlalchemy.orm import Session

from backend.integrations.delivery.service import dispatch_delivery
from backend.modules.events.event_config_service import event_from_callsign
from backend.modules.events.messages import next_msg_seq
from backend.modules.events.models import (
    EventMessage,
    EventStatus,
    MessageDirection,
    MessageStatus,
)
from backend.modules.events.service import EventNotActiveError, locked_event

MAX_ADDRESS_LEN = 255


def validate_to_address(raw: str) -> str:
    """Permissive: accept callsign / Winlink / internet-email forms. Strip CR/LF
    and other control chars (header-injection backstop), trim, and bound length.
    Winlink's CMS does the actual routing — we sanitize, not gatekeep."""
    cleaned = "".join(ch for ch in (raw or "") if ch.isprintable()).strip()
    if not cleaned:
        raise ValueError("Recipient address is required")
    if len(cleaned) > MAX_ADDRESS_LEN:
        raise ValueError("Recipient address is too long")
    return cleaned


def send_event_message(
    db: Session,
    event_id: int,
    *,
    actor: str,
    to_address: str,
    subject: str,
    body: str,
    reply_to_id: int | None = None,
) -> EventMessage:
    to_address = validate_to_address(to_address)
    event = locked_event(db, event_id)
    if event is None or event.status != EventStatus.ACTIVE:
        raise EventNotActiveError("Event is not active")

    seq = next_msg_seq(event)
    message = EventMessage(
        event_id=event_id,
        msg_seq=seq,
        direction=MessageDirection.OUTBOUND,
        from_callsign=event_from_callsign(db, event),
        to_address=to_address,
        subject=subject,
        body=body,
        status=MessageStatus.READ,
        actor=actor,
        reply_to_id=reply_to_id,
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    # Private Winlink reply: always goes to the composed recipient via winlink
    # backend only, regardless of the event's delivery.backends configuration.
    # A failure is non-fatal: the row persists and the operator can retry via
    # the delivery routes.
    dispatch_delivery(
        db, "event_message", message.id, subject, body,
        event_id=event.id,
        backends=["winlink"],
        config_overrides={"target_address": to_address},
    )
    db.refresh(message)
    return message


def _build_context(db, event, datetime_stamp: str):
    from backend.modules.forms.builder import BuildContext

    callsign = event_from_callsign(db, event)
    return BuildContext(callsign=callsign, datetime_stamp=datetime_stamp, grid="")


def compose_form_preview(db, event_id, *, template_path, variables, datetime_stamp) -> dict:
    from backend.modules.forms.builder import FormBuildError, build_form_message
    from backend.modules.events.models import Event

    event = db.get(Event, event_id)
    ctx = _build_context(db, event, datetime_stamp)
    try:
        composed = build_form_message(template_path, variables, ctx)
    except FormBuildError as exc:
        raise ValueError(str(exc))
    if not composed.to.strip():
        raise ValueError("form has no destination address")
    return {
        "to": composed.to, "subject": composed.subject, "body": composed.body,
        "attachment_filename": composed.attachment.filename,
    }


def send_event_form_message(db, event_id, *, actor, template_path, variables, datetime_stamp, reply_to_id=None):
    from backend.modules.forms.builder import FormBuildError, build_form_message
    from backend.modules.events.models import (
        EventMessage, EventMessageForm, EventStatus, MessageDirection, MessageStatus,
    )
    from backend.modules.events.messages import next_msg_seq
    from backend.modules.events.service import EventNotActiveError, locked_event
    from backend.integrations.delivery.service import dispatch_delivery

    event = locked_event(db, event_id)
    if event is None or event.status != EventStatus.ACTIVE:
        raise EventNotActiveError("Event is not active")
    ctx = _build_context(db, event, datetime_stamp)
    try:
        composed = build_form_message(template_path, variables, ctx)
    except FormBuildError as exc:
        raise ValueError(str(exc))
    if not composed.to.strip():
        raise ValueError("form has no destination address")

    seq = next_msg_seq(event)
    message = EventMessage(
        event_id=event_id, msg_seq=seq, direction=MessageDirection.OUTBOUND,
        from_callsign=ctx.callsign, to_address=composed.to,
        subject=composed.subject, body=composed.body,
        status=MessageStatus.READ, actor=actor, reply_to_id=reply_to_id,
    )
    db.add(message)
    db.flush()
    db.add(EventMessageForm(
        event_message_id=message.id, template_path=template_path,
        display_form=composed.display_form, reply_template=composed.reply_template or None,
        variables=composed.variables, datetime_stamp=datetime_stamp,
    ))
    db.commit()
    db.refresh(message)

    dispatch_delivery(
        db, "event_message", message.id, composed.subject, composed.body,
        event_id=event.id,
        backends=["winlink"],
        config_overrides={"target_address": composed.to, "attachments": [composed.attachment]},
    )
    db.refresh(message)
    return message


def resolve_reply_form(db, event_id, message_id) -> dict:
    """From an inbound form message → the reply template's input form (if any)
    and prefill from the sender's variables."""
    import xml.etree.ElementTree as ET
    from backend.modules.checkins.message_parser import extract_form_variables, find_form_xml
    from backend.modules.checkins.models import RawMessage, RawMessageAttachment
    from backend.modules.events.models import EventMessage
    from backend.modules.forms.catalog import _input_form_for  # reuse the resolver
    from backend.modules.forms.library import find_template, forms_library_dir

    msg = db.query(EventMessage).filter_by(id=message_id, event_id=event_id).one_or_none()
    if msg is None or msg.raw_message_id is None:
        raise ValueError("message not found")
    atts = db.query(RawMessageAttachment).filter_by(raw_message_id=msg.raw_message_id).all()
    raw = db.get(RawMessage, msg.raw_message_id)
    xml_text = find_form_xml(
        [{"filename": a.filename, "content_type": a.content_type, "data": a.data} for a in atts],
        raw.body if raw else "",
    )
    if not xml_text:
        raise ValueError("no form to reply to")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        raise ValueError("no form to reply to")
    rt = root.find(".//form_parameters/reply_template")
    reply_name = (rt.text or "").strip() if rt is not None else ""
    prefill = extract_form_variables(root)

    reply_template_path = ""
    input_form_path = None
    if reply_name:
        tpath = find_template(reply_name if reply_name.endswith(".txt") else reply_name + ".txt")
        if tpath is not None:
            base = forms_library_dir()
            reply_template_path = str(tpath.relative_to(base))
            inp = _input_form_for(tpath)
            if inp is not None:
                input_form_path = str(inp.relative_to(base))
    return {"reply_template_path": reply_template_path, "input_form_path": input_form_path, "prefill": prefill}


def set_message_status(
    db: Session, event_id: int, message_id: int, status: MessageStatus
) -> EventMessage | None:
    message = (
        db.query(EventMessage)
        .filter(EventMessage.id == message_id, EventMessage.event_id == event_id)
        .one_or_none()
    )
    if message is None:
        return None
    message.status = status
    db.commit()
    db.refresh(message)
    return message
