import enum
from datetime import date, time

from sqlalchemy import (
    Integer,
    String,
    Date,
    Time,
    Boolean,
    Enum,
    ForeignKey,
    Float,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class SessionType(str, enum.Enum):
    REGULAR_CHECKIN = "regular_checkin"
    ACTIVITY = "activity"
    REAL_EVENT = "real_event"


class SessionStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class NetSeason(Base):
    __tablename__ = "net_seasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    net_id: Mapped[int] = mapped_column(Integer, ForeignKey("nets.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time: Mapped[time | None] = mapped_column(Time, nullable=True)
    is_week_long: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    activity_cadence: Mapped[int] = mapped_column(Integer, nullable=False, default=2)

    # No automatic cascade — the delete-season route preserves completed
    # sessions by detaching them (season_id = NULL) and explicitly deletes
    # the rest. `all, delete-orphan` would force all-or-nothing.
    sessions: Mapped[list["NetSession"]] = relationship(back_populates="season")


class NetSession(Base):
    __tablename__ = "net_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # net_id is the source of truth for cross-net isolation. For sessions with a
    # season, it must match the season's net_id; for orphans (REAL_EVENT) it's
    # the only link to a net. No DB-level CHECK enforces the season match —
    # routes only ever set net_id from ctx.net.id, so the invariant holds.
    net_id: Mapped[int] = mapped_column(Integer, ForeignKey("nets.id"), nullable=False, index=True)
    season_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("net_seasons.id"), nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    grace_period_hours: Mapped[float] = mapped_column(Float, nullable=False, default=24.0)
    session_type: Mapped[SessionType] = mapped_column(Enum(SessionType), nullable=False)
    status: Mapped[SessionStatus] = mapped_column(Enum(SessionStatus), nullable=False, default=SessionStatus.SCHEDULED)
    activity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    net_control_callsign: Mapped[str | None] = mapped_column(String(20), nullable=True)

    season: Mapped["NetSeason | None"] = relationship(back_populates="sessions")


@event.listens_for(NetSession, "before_insert")
def _backfill_net_id_from_season(_mapper, connection, target: "NetSession") -> None:
    """If a caller forgot to set net_id but provided a season, derive it from the season.

    Production routes always set net_id explicitly from ctx.net.id; this is a safety net
    so a missed call site (or a test fixture) doesn't silently insert a NULL — SQLAlchemy
    would raise a confusing IntegrityError instead of a clear cross-net leak."""
    if target.net_id is not None:
        return
    if target.season_id is None:
        return
    row = connection.execute(
        NetSeason.__table__.select().with_only_columns(NetSeason.net_id).where(NetSeason.id == target.season_id)
    ).first()
    if row is not None:
        target.net_id = row[0]
