import enum
from datetime import datetime, timezone

from sqlalchemy import String, Enum, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    NET_CONTROL = "net_control"
    VIEWER = "viewer"


class User(Base):
    __tablename__ = "users"

    callsign: Mapped[str] = mapped_column(String(20), primary_key=True)
    oidc_subject: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False, default=UserRole.VIEWER)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
