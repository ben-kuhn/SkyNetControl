"""Inbound Winlink routing into active events, and the msg_seq cursor helper.

route_event_messages runs in the scanner cycle right after the check-in pass.
Both consume the same net-matched, deduped RawMessage rows, so one inbound
message can produce both a CheckIn and an EventMessage (intended dual-ingest)."""
import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.modules.checkins.models import RawMessage
from backend.modules.events.models import (
    Event,
    EventLogType,
    EventMessage,
    EventParticipant,
    EventStatus,
    MessageDirection,
    MessageStatus,
)
from backend.modules.events.service import add_log_entry

logger = logging.getLogger(__name__)


def next_msg_seq(event: Event) -> int:
    event.msg_seq += 1
    return event.msg_seq


def _base_callsign(address: str) -> str:
    """KE0XYZ-7@winlink.org -> KE0XYZ."""
    local = address.split("@")[0]
    return local.split("-")[0].strip().upper()


def route_event_messages(db: Session, net_id: int, raw_messages: list[dict]) -> int:
    """Create inbound EventMessages for every active event of net_id. Returns the
    total number of new messages created across all active events."""
    events = (
        db.query(Event)
        .filter(Event.net_id == net_id, Event.status == EventStatus.ACTIVE)
        .all()
    )
    if not events:
        return 0

    # Resolve RawMessage rows by message_id (the check-in pass already upserted
    # them). A message with no RawMessage row is skipped.
    msg_ids = [m["message_id"] for m in raw_messages]
    raw_by_id = {
        r.message_id: r
        for r in db.query(RawMessage).filter(RawMessage.message_id.in_(msg_ids)).all()
    }

    created = 0
    for event in events:
        participants = {
            p.callsign.upper(): p
            for p in db.query(EventParticipant).filter(EventParticipant.event_id == event.id).all()
        }
        for m in raw_messages:
            raw = raw_by_id.get(m["message_id"])
            if raw is None:
                continue
            # Dedup: skip if this raw message is already routed to this event.
            exists = (
                db.query(EventMessage)
                .filter(EventMessage.event_id == event.id, EventMessage.raw_message_id == raw.id)
                .first()
            )
            if exists is not None:
                continue

            base = _base_callsign(m["from_address"])
            participant = participants.get(base)
            seq = next_msg_seq(event)
            message = EventMessage(
                event_id=event.id,
                msg_seq=seq,
                direction=MessageDirection.INBOUND,
                raw_message_id=raw.id,
                participant_id=participant.id if participant else None,
                from_callsign=base,
                to_address=m.get("to_address") or "",
                subject=m.get("subject") or "",
                body=m.get("body") or "",
                status=MessageStatus.UNREAD,
                received_at=raw.received_at,
            )
            db.add(message)
            add_log_entry(
                db, event, entry_type=EventLogType.SYSTEM,
                message=f"\U0001F4E9 Winlink from {base}: {m.get('subject') or '(no subject)'}",
                actor=base, callsign=base,
            )
            try:
                db.commit()
                created += 1
            except IntegrityError:
                # Concurrent scan raced us to the same (event, raw). Roll back
                # this message; the seq we consumed is harmless (monotonic gap).
                db.rollback()
                continue
    return created
