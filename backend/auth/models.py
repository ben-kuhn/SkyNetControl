import enum
from datetime import datetime, timezone

from sqlalchemy import String, Enum, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    NET_CONTROL = "net_control"
    VIEWER = "viewer"
    PENDING = "pending"
    DELETED = "deleted"


class User(Base):
    __tablename__ = "users"

    callsign: Mapped[str] = mapped_column(String(20), primary_key=True)
    oidc_subject: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False, default=UserRole.VIEWER)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pending_callsign: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class AdminRecoveryToken(Base):
    __tablename__ = "admin_recovery_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
