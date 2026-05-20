import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.auth.models import User, UserRole
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

    if user.role == UserRole.ADMIN:
        admin_count = db.query(User).filter(User.role == UserRole.ADMIN).count()
        if admin_count <= 1:
            raise ValueError("Cannot anonymize: sole admin")
        raise ValueError("Cannot anonymize an admin")

    anon_id = _generate_anon_id(db)

    # 1. Delete PATs first (FK to users.callsign)
    db.query(PersonalAccessToken).filter(
        PersonalAccessToken.user_callsign == callsign
    ).delete()

    # 2. Update audit log references
    db.query(AuditLog).filter(AuditLog.actor_callsign == callsign).update(
        {AuditLog.actor_callsign: anon_id}
    )
    db.query(AuditLog).filter(AuditLog.target_callsign == callsign).update(
        {AuditLog.target_callsign: anon_id}
    )

    # 3. Anonymize check-ins and their raw messages
    checkins = db.query(CheckIn).filter(CheckIn.callsign == callsign).all()
    raw_message_ids = [ci.raw_message_id for ci in checkins if ci.raw_message_id]

    db.query(CheckIn).filter(CheckIn.callsign == callsign).update({
        CheckIn.callsign: anon_id,
        CheckIn.name: "Deleted User",
        CheckIn.city: None,
        CheckIn.county: None,
        CheckIn.state: None,
        CheckIn.latitude: None,
        CheckIn.longitude: None,
        CheckIn.comments: None,
    })

    if raw_message_ids:
        db.query(RawMessage).filter(RawMessage.id.in_(raw_message_ids)).update({
            RawMessage.from_address: "anonymized",
            RawMessage.subject: "[redacted]",
            RawMessage.body: "[redacted]",
        })

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
        oidc_subject="deleted",
        name="Deleted User",
        role=UserRole.DELETED,
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
