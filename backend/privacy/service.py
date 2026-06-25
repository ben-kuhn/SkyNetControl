import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.auth.models import User
from backend.auth.pat_models import PersonalAccessToken
from backend.audit.models import AuditLog
from backend.audit.service import log_action
from backend.modules.checkins.models import CheckIn, Member, RawMessage


def _generate_anon_id(db: Session) -> str:
    """Generate a unique ANON-XXXX identifier."""
    for _ in range(100):
        anon_id = "ANON-" + secrets.token_hex(2).upper()
        if db.get(User, anon_id) is None:
            return anon_id
    raise RuntimeError("Failed to generate unique anonymous ID")


def anonymize_user(
    db: Session,
    callsign: str,
    actor_callsign: str,
) -> dict:
    """Anonymize a user's account and all associated PII."""
    user = db.get(User, callsign)
    if user is None:
        raise ValueError("User not found")

    if user.is_admin:
        admin_count = db.query(User).filter(User.is_admin.is_(True)).count()
        if admin_count <= 1:
            raise ValueError("Cannot anonymize: sole admin")
        raise ValueError("Cannot anonymize an admin")

    anon_id = _generate_anon_id(db)

    # 1. Delete PATs first (FK to users.callsign)
    db.query(PersonalAccessToken).filter(PersonalAccessToken.user_callsign == callsign).delete()

    # 2. Update audit log references
    db.query(AuditLog).filter(AuditLog.actor_callsign == callsign).update({AuditLog.actor_callsign: anon_id})
    db.query(AuditLog).filter(AuditLog.target_callsign == callsign).update({AuditLog.target_callsign: anon_id})

    # 3. Anonymize check-ins and their raw messages
    checkins = db.query(CheckIn).filter(CheckIn.callsign == callsign).all()
    raw_message_ids = [ci.raw_message_id for ci in checkins if ci.raw_message_id]

    db.query(CheckIn).filter(CheckIn.callsign == callsign).update(
        {
            CheckIn.callsign: anon_id,
            CheckIn.name: "Deleted User",
            CheckIn.city: None,
            CheckIn.county: None,
            CheckIn.state: None,
            CheckIn.latitude: None,
            CheckIn.longitude: None,
            CheckIn.comments: None,
        }
    )

    if raw_message_ids:
        db.query(RawMessage).filter(RawMessage.id.in_(raw_message_ids)).update(
            {
                RawMessage.from_address: "anonymized",
                RawMessage.subject: "[redacted]",
                RawMessage.body: "[redacted]",
            }
        )

    # 4. Anonymize member record (callsign is PK, so delete + re-insert)
    member = db.get(Member, callsign)
    if member:
        new_member = Member(
            callsign=anon_id,
            name="Deleted User",
            first_check_in_date=member.first_check_in_date,
            last_check_in_date=member.last_check_in_date,
            total_check_ins=member.total_check_ins,
        )
        db.delete(member)
        db.flush()
        db.add(new_member)

    # 5. Anonymize user record (callsign is PK, so delete + re-insert)
    created_at = user.created_at
    db.delete(user)
    db.flush()

    anon_user = User(
        callsign=anon_id,
        # oidc_subject is unique=True; use the unique anon_id so multiple
        # anonymizations don't collide on a shared "deleted" sentinel.
        oidc_subject=f"deleted:{anon_id}",
        name="Deleted User",
        is_deleted=True,
        email=None,
        pending_callsign=None,
        created_at=created_at,
    )
    db.add(anon_user)

    # 6. Log the anonymization action
    log_action(
        db,
        actor=actor_callsign,
        action="user.anonymized",
        target=anon_id,
    )

    return {"anonymous_id": anon_id}


def export_user_data(db: Session, callsign: str) -> dict:
    """Export all data associated with a user's callsign as a dict."""
    user = db.get(User, callsign)
    if user is None:
        raise ValueError("User not found")

    user_data = {
        "callsign": user.callsign,
        "name": user.name,
        "email": user.email,
        "is_admin": user.is_admin,
        "is_pending": user.is_pending,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }

    checkins = db.query(CheckIn).filter(CheckIn.callsign == callsign).all()
    checkins_data = [
        {
            "session_id": ci.session_id,
            "callsign": ci.callsign,
            "name": ci.name,
            "city": ci.city,
            "county": ci.county,
            "state": ci.state,
            "latitude": ci.latitude,
            "longitude": ci.longitude,
            "comments": ci.comments,
            "timing_status": ci.timing_status.value,
        }
        for ci in checkins
    ]

    raw_message_ids = [ci.raw_message_id for ci in checkins if ci.raw_message_id]
    raw_messages = []
    if raw_message_ids:
        msgs = db.query(RawMessage).filter(RawMessage.id.in_(raw_message_ids)).all()
        raw_messages = [
            {
                "message_id": m.message_id,
                "from_address": m.from_address,
                "subject": m.subject,
                "body": m.body,
                "received_at": m.received_at.isoformat() if m.received_at else None,
            }
            for m in msgs
        ]

    member = db.get(Member, callsign)
    member_data = None
    if member:
        member_data = {
            "callsign": member.callsign,
            "name": member.name,
            "first_check_in_date": member.first_check_in_date.isoformat() if member.first_check_in_date else None,
            "last_check_in_date": member.last_check_in_date.isoformat() if member.last_check_in_date else None,
            "total_check_ins": member.total_check_ins,
        }

    audit_entries = (
        db.query(AuditLog)
        .filter((AuditLog.actor_callsign == callsign) | (AuditLog.target_callsign == callsign))
        .order_by(AuditLog.created_at.desc())
        .all()
    )
    audit_data = [
        {
            "action": e.action,
            "actor_callsign": e.actor_callsign,
            "target_callsign": e.target_callsign,
            "details": e.details,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in audit_entries
    ]

    tokens = db.query(PersonalAccessToken).filter(PersonalAccessToken.user_callsign == callsign).all()
    tokens_data = [
        {
            "name": t.name,
            "scopes": t.scopes,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
            "expires_at": t.expires_at.isoformat() if t.expires_at else None,
        }
        for t in tokens
    ]

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user": user_data,
        "check_ins": checkins_data,
        "raw_messages": raw_messages,
        "member_record": member_data,
        "audit_log": audit_data,
        "tokens": tokens_data,
    }
