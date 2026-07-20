import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventType(str, enum.Enum):
    PUBLIC_SERVICE = "public_service"
    EMERGENCY = "emergency"


class EventStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    CLOSED = "closed"


class ParticipantStatus(str, enum.Enum):
    CHECKED_IN = "checked_in"
    AT_POST = "at_post"
    EN_ROUTE = "en_route"
    OUT_OF_SERVICE = "out_of_service"
    CHECKED_OUT = "checked_out"


class EventLogType(str, enum.Enum):
    SYSTEM = "system"
    NOTE = "note"
    PARTICIPANT_NOTE = "participant_note"


class MessageDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageStatus(str, enum.Enum):
    UNREAD = "unread"
    READ = "read"
    DISMISSED = "dismissed"


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    net_id: Mapped[int] = mapped_column(Integer, ForeignKey("nets.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[EventType] = mapped_column(Enum(EventType), nullable=False)
    status: Mapped[EventStatus] = mapped_column(Enum(EventStatus), nullable=False, default=EventStatus.DRAFT)
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    # Last-assigned event_log.seq for this event. Incremented under the event
    # row lock (SELECT ... FOR UPDATE on PostgreSQL; SQLite serializes writes)
    # so concurrent operators can't mint the same seq.
    log_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Last-assigned event_messages.msg_seq for this event — the Messages-panel
    # polling cursor, incremented under the event row lock like log_seq.
    msg_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Per-event APRS map settings (sub-project 2). Positions themselves are
    # in-memory only; these persisted flags configure the live client.
    aprs_other_stations: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    aprs_range_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    aprs_range_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    aprs_range_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    aprs_beacon_posts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    posts: Mapped[list["EventPost"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    participants: Mapped[list["EventParticipant"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    log_entries: Mapped[list["EventLogEntry"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    messages: Mapped[list["EventMessage"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class EventPost(Base):
    __tablename__ = "event_posts"
    __table_args__ = (UniqueConstraint("event_id", "name", name="uq_event_posts_event_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("events.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    event: Mapped["Event"] = relationship(back_populates="posts")


class EventParticipant(Base):
    __tablename__ = "event_participants"
    __table_args__ = (UniqueConstraint("event_id", "callsign", name="uq_event_participants_event_callsign"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("events.id"), nullable=False)
    # Plain string, uppercased by the service — events have no membership
    # concept and any callsign (mutual aid, walk-ups) can participate.
    callsign: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    post_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("event_posts.id"), nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_status: Mapped[ParticipantStatus] = mapped_column(
        Enum(ParticipantStatus), nullable=False, default=ParticipantStatus.CHECKED_IN
    )
    # Latest transition times; full history lives in event_log.
    checked_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    checked_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    event: Mapped["Event"] = relationship(back_populates="participants")
    post: Mapped["EventPost | None"] = relationship()


class EventLogEntry(Base):
    __tablename__ = "event_log"
    __table_args__ = (UniqueConstraint("event_id", "seq", name="uq_event_log_event_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("events.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_type: Mapped[EventLogType] = mapped_column(Enum(EventLogType), nullable=False)
    # The participant this entry concerns (nullable — event-wide entries).
    callsign: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Who caused/wrote the entry. Always the authenticated operator.
    actor: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured record of the status a SYSTEM transition set — stint/hours
    # computation reads this instead of parsing message text.
    new_status: Mapped[ParticipantStatus | None] = mapped_column(Enum(ParticipantStatus), nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    event: Mapped["Event"] = relationship(back_populates="log_entries")


class EventMessage(Base):
    __tablename__ = "event_messages"
    __table_args__ = (
        UniqueConstraint("event_id", "raw_message_id", name="uq_event_messages_event_raw"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("events.id"), nullable=False)
    msg_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    direction: Mapped[MessageDirection] = mapped_column(Enum(MessageDirection), nullable=False)
    # Set for inbound (links the shared, deduped RawMessage); null for outbound.
    raw_message_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("raw_messages.id"), nullable=True)
    # Linked when the from-callsign matches a checked-in participant.
    participant_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("event_participants.id"), nullable=True
    )
    from_callsign: Mapped[str] = mapped_column(String(64), nullable=False)
    to_address: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus), nullable=False, default=MessageStatus.UNREAD
    )
    # Outbound reply → the inbound message it answers.
    reply_to_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("event_messages.id"), nullable=True)
    # Operator who sent an outbound message; null for inbound.
    actor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    event: Mapped["Event"] = relationship(back_populates="messages")
    form_record: Mapped["EventMessageForm | None"] = relationship(
        cascade="all, delete-orphan"
    )


class EventMessageForm(Base):
    __tablename__ = "event_message_forms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("event_messages.id", ondelete="CASCADE"), nullable=False
    )
    template_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    display_form: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    reply_template: Mapped[str | None] = mapped_column(String(255), nullable=True)
    variables: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    datetime_stamp: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
