import enum
from datetime import datetime

from sqlalchemy import (
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    Float,
    Enum,
    ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class MessageType(str, enum.Enum):
    FORM = "form"
    PLAIN_TEXT = "plain_text"
    UNKNOWN = "unknown"
    WINLINK_FORM = "winlink_form"


class ParseStatus(str, enum.Enum):
    AUTO = "auto"
    MANUAL_REVIEW = "manual_review"
    MANUALLY_ENTERED = "manually_entered"


class TimingStatus(str, enum.Enum):
    ON_TIME = "on_time"
    EARLY = "early"
    LATE = "late"


class RawMessage(Base):
    __tablename__ = "raw_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    from_address: Mapped[str] = mapped_column(String(255), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[MessageType] = mapped_column(Enum(MessageType), nullable=False)
    parsed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    checkin: Mapped["CheckIn | None"] = relationship(back_populates="raw_message")


class CheckIn(Base):
    __tablename__ = "check_ins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("net_sessions.id"), nullable=False)
    raw_message_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("raw_messages.id"), nullable=True)
    callsign: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    county: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mode: Mapped[str] = mapped_column(String(100), nullable=False)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    parse_status: Mapped[ParseStatus] = mapped_column(Enum(ParseStatus), nullable=False)
    timing_status: Mapped[TimingStatus] = mapped_column(Enum(TimingStatus), nullable=False)
    is_new_member: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    raw_message: Mapped["RawMessage | None"] = relationship(back_populates="checkin")


class Member(Base):
    __tablename__ = "members"

    callsign: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    first_check_in_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_check_in_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_check_ins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
