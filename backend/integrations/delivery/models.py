import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class DeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"


class DeliveryLog(Base):
    __tablename__ = "delivery_logs"
    __table_args__ = (UniqueConstraint("content_type", "content_id", "backend", name="uq_delivery_content_backend"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_type: Mapped[str] = mapped_column(String(20), nullable=False)
    content_id: Mapped[int] = mapped_column(Integer, nullable=False)
    backend: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[DeliveryStatus] = mapped_column(Enum(DeliveryStatus), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pat_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("pat_connection_sessions.id", ondelete="SET NULL"), nullable=True
    )
    pat_mid: Mapped[str | None] = mapped_column(String(64), nullable=True)
