from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class PatSessionStatus(str, enum.Enum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    SYNCING = "syncing"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class PatConnectionSession(Base):
    __tablename__ = "pat_connection_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    net_id: Mapped[int | None] = mapped_column(ForeignKey("nets.id", ondelete="CASCADE"), nullable=True)
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    connect_url: Mapped[str] = mapped_column(String(512), nullable=False)
    method_label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[PatSessionStatus] = mapped_column(
        SAEnum(PatSessionStatus, values_callable=lambda e: [m.value for m in e],
               native_enum=False, length=20),
        nullable=False,
    )
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    actor: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
