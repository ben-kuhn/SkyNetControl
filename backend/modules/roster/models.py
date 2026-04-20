import enum
from datetime import datetime, timezone

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


class RosterStatus(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    SENT = "sent"
    SKIPPED = "skipped"


class RosterTemplate(Base):
    __tablename__ = "roster_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    subject_template: Mapped[str] = mapped_column(Text, nullable=False)
    header_template: Mapped[str] = mapped_column(Text, nullable=False)
    welcome_template: Mapped[str] = mapped_column(Text, nullable=False)
    comments_template: Mapped[str] = mapped_column(Text, nullable=False)
    footer_template: Mapped[str] = mapped_column(Text, nullable=False)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    logs: Mapped[list["RosterLog"]] = relationship(back_populates="template")


class RosterLog(Base):
    __tablename__ = "roster_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("net_sessions.id"), unique=True, nullable=False
    )
    template_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("roster_templates.id"), nullable=True
    )
    status: Mapped[RosterStatus] = mapped_column(
        Enum(RosterStatus), nullable=False, default=RosterStatus.DRAFT
    )
    content_subject: Mapped[str] = mapped_column(Text, nullable=False)
    content_header: Mapped[str] = mapped_column(Text, nullable=False)
    content_welcome: Mapped[str] = mapped_column(Text, nullable=False)
    content_comments: Mapped[str] = mapped_column(Text, nullable=False)
    content_footer: Mapped[str] = mapped_column(Text, nullable=False)
    map_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    drafted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[str | None] = mapped_column(String(20), nullable=True)

    session: Mapped["NetSession"] = relationship()
    template: Mapped["RosterTemplate | None"] = relationship(back_populates="logs")
