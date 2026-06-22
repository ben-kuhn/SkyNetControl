import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class NetRole(str, enum.Enum):
    NET_CONTROL = "net_control"
    VIEWER = "viewer"


class Net(Base):
    __tablename__ = "nets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    memberships: Mapped[list["NetMembership"]] = relationship(
        back_populates="net", cascade="all, delete-orphan"
    )


class NetMembership(Base):
    __tablename__ = "net_memberships"

    user_callsign: Mapped[str] = mapped_column(
        String(20), ForeignKey("users.callsign"), primary_key=True
    )
    net_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("nets.id"), primary_key=True
    )
    role: Mapped[NetRole] = mapped_column(Enum(NetRole), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    net: Mapped["Net"] = relationship(back_populates="memberships")


class NetConfig(Base):
    __tablename__ = "net_config"

    net_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("nets.id"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
