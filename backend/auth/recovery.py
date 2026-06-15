"""Admin recovery token management.

Provides:
- Token minting/verification for single-use break-glass admin tokens.
- Recovery JWT cookie encode/decode using the same JWT secret as user sessions
  but with a distinct ``type: "recovery"`` claim so the two cannot be confused.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.auth.models import AdminRecoveryToken
from backend.config import Settings

_TOKEN_TTL = timedelta(minutes=10)
_COOKIE_TTL_MINUTES = 30


def _now() -> datetime:
    """Return current UTC time. Tests can monkeypatch this."""
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    """Return *dt* as an aware UTC datetime.

    SQLite stores timezone-aware datetimes as naive UTC values; this helper
    makes comparisons safe regardless of dialect.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def mint_token(db: Session, ttl: timedelta = _TOKEN_TTL) -> tuple[str, datetime]:
    """Generate a fresh single-use admin-recovery token.

    Returns (plaintext, expires_at). The plaintext is shown once and never
    stored; only its sha256 hash is persisted.
    """
    plaintext = secrets.token_urlsafe(32)
    expires_at = _now() + ttl
    db.add(AdminRecoveryToken(token_hash=_hash(plaintext), expires_at=expires_at))
    db.commit()
    return plaintext, expires_at


def verify_token(db: Session, plaintext: str) -> AdminRecoveryToken | None:
    """Return the matching token row iff present, unused, and unexpired.

    Does NOT mark the token used — call mark_used() separately after
    deciding to accept the token.
    """
    row = (
        db.query(AdminRecoveryToken)
        .filter(AdminRecoveryToken.token_hash == _hash(plaintext))
        .one_or_none()
    )
    if row is None:
        return None
    if row.used_at is not None:
        return None
    if _now() >= _as_utc(row.expires_at):
        return None
    return row


def mark_used(db: Session, row: AdminRecoveryToken) -> None:
    """Mark the token row as used. Idempotent (no-op if already marked)."""
    if row.used_at is None:
        row.used_at = _now()
        db.commit()


def claim_token(db: Session, plaintext: str) -> AdminRecoveryToken | None:
    """Atomically verify + mark-used in one round-trip.

    Returns the matching row iff the claim succeeded. Returns None if the
    token was unknown, already used, or expired.

    Uses a single UPDATE statement with the unused-and-unexpired check in
    the WHERE clause, so concurrent claims of the same token can't both
    win — one will update the row and the other will see rowcount=0.
    Robust across SQLite (process-level write serialization) and
    PostgreSQL (row-level locking via the UPDATE).
    """
    token_hash = _hash(plaintext)
    now = _now()
    # SQLite stores DateTime(timezone=True) as naive UTC strings; use naive
    # for the WHERE comparison so it matches whatever the DB has.
    now_naive = now.replace(tzinfo=None)
    result = db.execute(
        update(AdminRecoveryToken)
        .where(AdminRecoveryToken.token_hash == token_hash)
        .where(AdminRecoveryToken.used_at.is_(None))
        .where(AdminRecoveryToken.expires_at > now_naive)
        .values(used_at=now)
    )
    db.commit()
    if result.rowcount != 1:
        return None
    return (
        db.query(AdminRecoveryToken)
        .filter(AdminRecoveryToken.token_hash == token_hash)
        .one()
    )


def list_outstanding(db: Session) -> list[AdminRecoveryToken]:
    """Return all unused, unexpired tokens, ordered by expiry ascending."""
    # Use naive UTC for the DB comparison so SQLite's string comparison works
    # (SQLite stores DateTime(timezone=True) as naive UTC strings).
    now_naive = _now().replace(tzinfo=None)
    return (
        db.query(AdminRecoveryToken)
        .filter(AdminRecoveryToken.used_at.is_(None))
        .filter(AdminRecoveryToken.expires_at > now_naive)
        .order_by(AdminRecoveryToken.expires_at)
        .all()
    )


def revoke_by_prefix(db: Session, prefix: str) -> int:
    """Mark all unused tokens whose token_hash starts with ``prefix`` as used.

    Returns the number of tokens revoked.
    """
    rows = (
        db.query(AdminRecoveryToken)
        .filter(AdminRecoveryToken.used_at.is_(None))
        .filter(AdminRecoveryToken.token_hash.like(f"{prefix}%"))
        .all()
    )
    now = _now()
    for row in rows:
        row.used_at = now
    db.commit()
    return len(rows)


# ─── recovery cookie JWT ────────────────────────────────────────────────────


@dataclass(frozen=True)
class RecoveryPrincipal:
    """Returned by require_admin_or_recovery when the recovery cookie is valid."""

    hash_prefix: str

    @property
    def callsign(self) -> str:
        # Mirrors User.callsign so audit-log call sites don't need to branch.
        return f"recovery:{self.hash_prefix}"


def make_recovery_token(hash_prefix: str, settings: Settings) -> str:
    """Encode a recovery cookie JWT."""
    payload = {
        "type": "recovery",
        "hash_prefix": hash_prefix,
        "exp": _now() + timedelta(minutes=_COOKIE_TTL_MINUTES),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_recovery_token(token: str, settings: Settings) -> RecoveryPrincipal | None:
    """Decode and validate a recovery cookie JWT.

    Returns None for any failure: expired, wrong type, malformed, or missing
    claims. Never raises.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    if payload.get("type") != "recovery":
        return None
    prefix = payload.get("hash_prefix")
    if not isinstance(prefix, str) or not prefix:
        return None
    return RecoveryPrincipal(hash_prefix=prefix)


def cookie_ttl_seconds() -> int:
    return _COOKIE_TTL_MINUTES * 60
