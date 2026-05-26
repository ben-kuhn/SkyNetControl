from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class NotificationKind(str, enum.Enum):
    REMINDER_DRAFT = "reminder_draft"
    CHECKINS_READY = "checkins_ready"
    ROSTER_DRAFT = "roster_draft"
    DELIVERY_FAILURE = "delivery_failure"


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipient_callsign: Mapped[str] = mapped_column(
        String(20), ForeignKey("users.callsign"), nullable=False
    )
    kind: Mapped[NotificationKind] = mapped_column(Enum(NotificationKind), nullable=False)
    session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("net_sessions.id"), nullable=True
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    link_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_notifications_recipient_read", "recipient_callsign", "read_at"),
    )
