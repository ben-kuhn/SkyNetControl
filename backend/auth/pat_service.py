import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.auth.models import UserRole
from backend.auth.pat_models import PersonalAccessToken
from backend.auth.scopes import validate_scopes_for_role

MAX_ACTIVE_TOKENS = 10
LAST_USED_DEBOUNCE_SECONDS = 60


def _generate_raw_token() -> str:
    return "skynet_" + secrets.token_hex(32)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_token(
    db: Session,
    user_callsign: str,
    user_role: UserRole,
    name: str,
    scopes: list[str],
    expires_at: datetime | None,
) -> dict:
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError("Token name must be 1-100 characters")

    validate_scopes_for_role(scopes, user_role)

    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise ValueError("Expiry must be in the future")

    active_count = (
        db.query(PersonalAccessToken)
        .filter_by(user_callsign=user_callsign, revoked_at=None)
        .count()
    )
    if active_count >= MAX_ACTIVE_TOKENS:
        raise ValueError(f"You have reached the maximum of {MAX_ACTIVE_TOKENS} active tokens")

    raw = _generate_raw_token()
    token_hash = _hash_token(raw)

    pat = PersonalAccessToken(
        user_callsign=user_callsign,
        name=name,
        token_hash=token_hash,
        token_prefix=raw[:8],
        scopes=",".join(scopes),
        expires_at=expires_at,
    )
    db.add(pat)
    db.commit()
    db.refresh(pat)

    return {
        "id": pat.id,
        "name": pat.name,
        "token": raw,
        "token_prefix": pat.token_prefix,
        "scopes": scopes,
        "expires_at": pat.expires_at.isoformat() if pat.expires_at else None,
        "created_at": pat.created_at.isoformat(),
    }


def list_tokens(db: Session, user_callsign: str) -> list[dict]:
    tokens = (
        db.query(PersonalAccessToken)
        .filter_by(user_callsign=user_callsign, revoked_at=None)
        .order_by(PersonalAccessToken.created_at.desc())
        .all()
    )
    now = datetime.now(timezone.utc)
    return [
        {
            "id": t.id,
            "name": t.name,
            "token_prefix": t.token_prefix,
            "scopes": t.scopes.split(","),
            "expires_at": t.expires_at.isoformat() if t.expires_at else None,
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
            "created_at": t.created_at.isoformat(),
            "is_expired": t.expires_at is not None and t.expires_at <= now,
            "is_revoked": False,
        }
        for t in tokens
    ]


def revoke_token(db: Session, token_id: int, user_callsign: str, is_admin: bool) -> None:
    query = db.query(PersonalAccessToken).filter_by(id=token_id, revoked_at=None)
    if not is_admin:
        query = query.filter_by(user_callsign=user_callsign)
    pat = query.first()
    if pat is None:
        raise ValueError("Token not found")
    pat.revoked_at = datetime.now(timezone.utc)
    db.commit()


def authenticate_token(db: Session, raw_token: str) -> dict | None:
    token_hash = _hash_token(raw_token)
    pat = (
        db.query(PersonalAccessToken)
        .filter_by(token_hash=token_hash, revoked_at=None)
        .first()
    )
    if pat is None:
        return None

    now = datetime.now(timezone.utc)
    if pat.expires_at is not None and pat.expires_at <= now:
        return None

    # Debounced last_used_at update
    if (
        pat.last_used_at is None
        or (now - pat.last_used_at).total_seconds() > LAST_USED_DEBOUNCE_SECONDS
    ):
        pat.last_used_at = now
        db.commit()

    return {
        "user_callsign": pat.user_callsign,
        "scopes": pat.scopes.split(","),
    }
