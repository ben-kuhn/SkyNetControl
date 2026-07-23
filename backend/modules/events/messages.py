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


def route_event_messages(db: Session, net_id: int | None, raw_messages: list[dict]) -> int:
    """Create inbound EventMessages for every active event. Returns the total
    number of new messages created across all active events.

    ``net_id`` is accepted for caller compatibility (scanner passes it) but is
    no longer used to filter events — events are net-independent since EP1.
    Messages are routed to every ACTIVE event whose event-config ``net_address``
    matches the ``to_address`` of the incoming message, or to all active events
    when no per-event net_address is configured (fallback: accept all)."""
    from backend.modules.events.event_config_service import get_event_config

    events = (
        db.query(Event)
        .filter(Event.status == EventStatus.ACTIVE)
        .all()
    )

    def _to_base(addr: str) -> str:
        """W0NE-7@winlink.org -> W0NE; bare callsigns W0NE-7 -> W0NE."""
        if not addr:
            return ""
        local = addr.split("@")[0].upper()
        return local.split("-")[0]

    # Resolve RawMessage rows by message_id (the check-in pass already upserted
    # them). A message with no RawMessage row is skipped.
    msg_ids = [m["message_id"] for m in raw_messages]
    raw_by_id = {
        r.message_id: r
        for r in db.query(RawMessage).filter(RawMessage.message_id.in_(msg_ids)).all()
    }

    # Pre-resolve each event's accept-callsign once (avoid repeated DB calls).
    # None means "catch-all" (no net_address configured → accepts every message).
    event_accept_base: dict[int, str | None] = {}
    for event in events:
        net_address = get_event_config(db, event.id, "net_address", "") or ""
        event_accept_base[event.id] = _to_base(net_address) if net_address else None

    if not events:
        return 0

    created = 0
    for event in events:
        accept_base = event_accept_base[event.id]  # None = catch-all
        participants = {
            p.callsign.upper(): p
            for p in db.query(EventParticipant).filter(EventParticipant.event_id == event.id).all()
        }
        for m in raw_messages:
            # Per-message address check: only deliver if this message's to_address
            # matches the event's net_address (or the event is a catch-all).
            msg_to_base = _to_base(m.get("to_address", ""))
            if accept_base is not None and msg_to_base != accept_base:
                continue

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
