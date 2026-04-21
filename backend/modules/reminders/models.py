import enum
from datetime import datetime

from sqlalchemy import (
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base
from backend.modules.schedule.models import NetSession  # noqa: F401


class TemplateType(str, enum.Enum):
    REGULAR_CHECKIN = "regular_checkin"
    ACTIVITY = "activity"


class ReminderStatus(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    SENT = "sent"
    SKIPPED = "skipped"


class ReminderTemplate(Base):
    __tablename__ = "reminder_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    template_type: Mapped[TemplateType] = mapped_column(Enum(TemplateType), nullable=False)
    subject_template: Mapped[str] = mapped_column(Text, nullable=False)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    logs: Mapped[list["ReminderLog"]] = relationship(back_populates="template")


class ReminderLog(Base):
    __tablename__ = "reminder_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("net_sessions.id"), unique=True, nullable=False)
    template_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("reminder_templates.id"), nullable=True)
    status: Mapped[ReminderStatus] = mapped_column(Enum(ReminderStatus), nullable=False, default=ReminderStatus.DRAFT)
    content_subject: Mapped[str] = mapped_column(Text, nullable=False)
    content_body: Mapped[str] = mapped_column(Text, nullable=False)
    drafted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(20), nullable=True)

    session: Mapped["NetSession"] = relationship()
    template: Mapped["ReminderTemplate | None"] = relationship(back_populates="logs")
