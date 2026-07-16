"""Outbound event Winlink messages and message-status transitions.

Outbound send reuses the existing delivery pipeline (dispatch_delivery) with the
'event_message' content type — no new backend. Messages go out under the net
callsign (derived from net_address by _build_config in the delivery service)."""
from sqlalchemy.orm import Session

from backend.integrations.delivery.service import dispatch_delivery
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


def _net_callsign(db: Session, net_id: int) -> str:
    from backend.modules.nets.config_service import get_net_config

    net_address = get_net_config(db, net_id, "net_address", "") or ""
    return net_address.split("@")[0].upper() if net_address else ""


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
        from_callsign=_net_callsign(db, event.net_id),
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

    # Send through the existing delivery pipeline. A failure is non-fatal: the
    # row persists and the operator can retry via the delivery routes.
    dispatch_delivery(db, "event_message", message.id, subject, body, event.net_id)
    db.refresh(message)
    return message


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
