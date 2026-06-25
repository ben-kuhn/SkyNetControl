from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class User(Base):
    __tablename__ = "users"

    callsign: Mapped[str] = mapped_column(String(20), primary_key=True)
    oidc_subject: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # role column removed in multi-net cutover migration (Task 3).
    # is_pending and is_deleted replace role='pending' and role='deleted'.
    # is_admin already existed (added in Task 2).
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pending_callsign: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # JWT invalidation: incremented on logout, role change, and account
    # delete; the JWT carries the value at issue time and auth dependencies
    # reject any token whose `tv` claim doesn't match the current row.
    # `server_default="0"` matches the alembic migration so existing rows
    # populate cleanly on upgrade.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )
    is_pending: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )
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
