# Privacy Compliance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add GDPR/CCPA compliance features: user data export, account anonymization ("right to be forgotten"), and a privacy policy page.

**Architecture:** A new `backend/privacy/` sub-package with two services (export and anonymization) and API routes. The anonymization service replaces all PII with `ANON-XXXX` placeholders across all tables. The export service collects all user data into a JSON download. Frontend adds a privacy policy page and user self-service actions.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, React/TypeScript

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `backend/privacy/__init__.py` | Package marker |
| `backend/privacy/service.py` | `export_user_data()` and `anonymize_user()` functions |
| `backend/privacy/routes.py` | Privacy API endpoints (export + anonymize) |
| `frontend/src/api/privacy.ts` | API client functions for privacy endpoints |
| `frontend/src/pages/PrivacyPolicyPage.tsx` | Static privacy policy content |
| `tests/test_privacy_service.py` | Service unit tests |
| `tests/test_privacy_routes.py` | Route/integration tests |

### Modified Files

| File | Change |
|------|--------|
| `backend/auth/models.py` | Add `DELETED = "deleted"` to `UserRole` enum |
| `backend/auth/dependencies.py` | Block `DELETED` users from authenticating |
| `backend/app.py` | Register `privacy_router` at `/api/privacy` |
| `frontend/src/types/index.ts` | Add `"deleted"` to `UserRole` type |
| `frontend/src/App.tsx` | Add `/privacy` route |
| `frontend/src/pages/UsersPage.tsx` | Add export/anonymize actions per user row |
| `frontend/src/pages/ProfilePage.tsx` | Add "Download My Data" and "Delete My Account" buttons |

---

### Task 1: Add DELETED to UserRole enum

**Files:**
- Modify: `backend/auth/models.py:10-14`
- Modify: `backend/auth/dependencies.py:38-39`
- Test: `tests/test_privacy_service.py` (new)

- [x] **Step 1: Write test for DELETED role existence**

Create `tests/test_privacy_service.py`:

```python
from backend.auth.models import UserRole


def test_deleted_role_exists():
    assert UserRole.DELETED == "deleted"
    assert "deleted" in [r.value for r in UserRole]
```

- [x] **Step 2: Run test to verify it fails**

Run: `nix-shell --run "python -m pytest tests/test_privacy_service.py::test_deleted_role_exists -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL with AttributeError

- [x] **Step 3: Add DELETED to UserRole enum**

In `backend/auth/models.py`, add after `PENDING = "pending"`:

```python
    DELETED = "deleted"
```

- [x] **Step 4: Block DELETED users from authenticating**

In `backend/auth/dependencies.py`, in `get_current_user`, after the line `if user is None or user.role == UserRole.PENDING:` (line 39), change to also check DELETED. And for cookie auth, after `if user is None:` (line 57), add a DELETED check:

Change line 39 from:
```python
        if user is None or user.role == UserRole.PENDING:
```
to:
```python
        if user is None or user.role in (UserRole.PENDING, UserRole.DELETED):
```

After `if user is None:` (line 57), add:
```python
    if user.role == UserRole.DELETED:
        raise HTTPException(status_code=401, detail="Account has been deleted")
```

- [x] **Step 5: Write test for DELETED user auth blocking**

Append to `tests/test_privacy_service.py`:

```python
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.auth.dependencies import get_current_user
from backend.config import Settings


@pytest.fixture
def privacy_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
    )


@pytest.fixture
def privacy_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin",
            role=UserRole.ADMIN,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer",
            role=UserRole.VIEWER,
            email="viewer@example.com",
        )
        deleted = User(
            callsign="ANON-AAAA",
            oidc_subject="deleted",
            name="Deleted User",
            role=UserRole.DELETED,
        )
        session.add_all([admin, viewer, deleted])
        session.commit()
    return factory


@pytest.fixture
def auth_app(privacy_settings, privacy_db):
    app = FastAPI()
    app.state.session_factory = privacy_db
    app.state.settings = privacy_settings

    @app.get("/me")
    async def me(user: User = Depends(get_current_user)):
        return {"callsign": user.callsign}

    return app


@pytest.fixture
async def auth_client(auth_app):
    transport = ASGITransport(app=auth_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_deleted_user_cannot_authenticate(auth_client, privacy_settings):
    token = create_access_token("ANON-AAAA", "deleted", privacy_settings)
    response = await auth_client.get("/me", cookies={"access_token": token})
    assert response.status_code == 401
```

- [x] **Step 6: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_privacy_service.py -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: 2 PASS

- [x] **Step 7: Commit**

```bash
git add backend/auth/models.py backend/auth/dependencies.py tests/test_privacy_service.py
git commit -m "feat: add DELETED role to UserRole enum

Block deleted users from authenticating via cookie or PAT."
```

---

### Task 2: Anonymization service

**Files:**
- Create: `backend/privacy/__init__.py`
- Create: `backend/privacy/service.py`
- Modify: `tests/test_privacy_service.py`

- [x] **Step 1: Create package marker**

Create empty `backend/privacy/__init__.py`.

- [x] **Step 2: Write anonymization tests**

Append to `tests/test_privacy_service.py`. These tests need additional model imports and richer fixtures:

```python
from backend.modules.checkins.models import RawMessage, CheckIn, Member, MessageType, ParseStatus, TimingStatus
from backend.audit.models import AuditLog
from backend.auth.pat_models import PersonalAccessToken
from backend.privacy.service import anonymize_user

import hashlib
from datetime import datetime, timezone


@pytest.fixture
def rich_db():
    """DB with user data across all tables for anonymization testing."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        # Users
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin",
            role=UserRole.ADMIN,
        )
        target = User(
            callsign="KD0TST",
            oidc_subject="auth0|target",
            name="Test User",
            role=UserRole.VIEWER,
            email="test@example.com",
            pending_callsign="KD0NEW",
        )
        session.add_all([admin, target])
        session.flush()

        # Check-ins (session_id uses a fake value since we don't need net_sessions)
        # We need to import and create a NetSession for the FK
        from backend.modules.schedule.models import NetSession
        net_session = NetSession(
            id=1,
            start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            grace_period_hours=1,
            session_type="regular",
            status="closed",
        )
        session.add(net_session)
        session.flush()

        raw_msg = RawMessage(
            message_id="msg-001",
            from_address="kd0tst@winlink.org",
            received_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
            subject="Check-in from KD0TST",
            body="Name: Test User\nCallsign: KD0TST",
            message_type=MessageType.FORM,
            parsed=True,
        )
        session.add(raw_msg)
        session.flush()

        checkin = CheckIn(
            session_id=1,
            raw_message_id=raw_msg.id,
            callsign="KD0TST",
            name="Test User",
            city="Denver",
            county="Denver",
            state="CO",
            mode="Winlink",
            comments="Good signal",
            latitude=39.7392,
            longitude=-104.9903,
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
        )
        session.add(checkin)

        member = Member(
            callsign="KD0TST",
            name="Test User",
            first_check_in_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_check_in_date=datetime(2026, 5, 20, tzinfo=timezone.utc),
            total_check_ins=10,
        )
        session.add(member)

        audit_entry = AuditLog(
            actor_callsign="W0NE",
            action="user.role_changed",
            target_callsign="KD0TST",
            details='{"from": "pending", "to": "viewer"}',
        )
        session.add(audit_entry)

        pat = PersonalAccessToken(
            user_callsign="KD0TST",
            name="Test Token",
            token_hash=hashlib.sha256(b"test").hexdigest(),
            token_prefix="skynet_t",
            scopes="schedule:read",
        )
        session.add(pat)

        session.commit()
    return factory


def test_anonymize_user_replaces_user_fields(rich_db):
    with rich_db() as db:
        result = anonymize_user(db, "KD0TST", actor_callsign="W0NE")

    anon_id = result["anonymous_id"]
    assert anon_id.startswith("ANON-")
    assert len(anon_id) == 9  # ANON- + 4 hex chars

    with rich_db() as db:
        # Original callsign should be gone
        assert db.get(User, "KD0TST") is None
        # New anonymous user should exist
        anon_user = db.get(User, anon_id)
        assert anon_user is not None
        assert anon_user.name == "Deleted User"
        assert anon_user.email is None
        assert anon_user.oidc_subject == "deleted"
        assert anon_user.pending_callsign is None
        assert anon_user.role == UserRole.DELETED


def test_anonymize_user_replaces_checkin_fields(rich_db):
    with rich_db() as db:
        result = anonymize_user(db, "KD0TST", actor_callsign="W0NE")
    anon_id = result["anonymous_id"]

    with rich_db() as db:
        checkins = db.query(CheckIn).filter(CheckIn.callsign == anon_id).all()
        assert len(checkins) == 1
        ci = checkins[0]
        assert ci.name == "Deleted User"
        assert ci.city is None
        assert ci.county is None
        assert ci.state is None
        assert ci.latitude is None
        assert ci.longitude is None
        assert ci.comments is None


def test_anonymize_user_redacts_raw_messages(rich_db):
    with rich_db() as db:
        anonymize_user(db, "KD0TST", actor_callsign="W0NE")

    with rich_db() as db:
        msgs = db.query(RawMessage).all()
        assert len(msgs) == 1
        assert msgs[0].from_address == "anonymized"
        assert msgs[0].subject == "[redacted]"
        assert msgs[0].body == "[redacted]"


def test_anonymize_user_replaces_member_record(rich_db):
    with rich_db() as db:
        result = anonymize_user(db, "KD0TST", actor_callsign="W0NE")
    anon_id = result["anonymous_id"]

    with rich_db() as db:
        assert db.get(Member, "KD0TST") is None
        anon_member = db.get(Member, anon_id)
        assert anon_member is not None
        assert anon_member.name == "Deleted User"


def test_anonymize_user_updates_audit_log(rich_db):
    with rich_db() as db:
        result = anonymize_user(db, "KD0TST", actor_callsign="W0NE")
    anon_id = result["anonymous_id"]

    with rich_db() as db:
        entries = db.query(AuditLog).order_by(AuditLog.id).all()
        # Original entry target_callsign should be anonymized
        assert entries[0].target_callsign == anon_id
        # New anonymization audit entry should exist
        anon_entry = [e for e in entries if e.action == "user.anonymized"]
        assert len(anon_entry) == 1
        assert anon_entry[0].actor_callsign == "W0NE"
        assert anon_entry[0].target_callsign == anon_id


def test_anonymize_user_deletes_tokens(rich_db):
    with rich_db() as db:
        anonymize_user(db, "KD0TST", actor_callsign="W0NE")

    with rich_db() as db:
        tokens = db.query(PersonalAccessToken).all()
        assert len(tokens) == 0


def test_anonymize_admin_by_admin_blocked(rich_db):
    with rich_db() as db:
        with pytest.raises(ValueError, match="Cannot anonymize an admin"):
            anonymize_user(db, "W0NE", actor_callsign="W0NE")


def test_anonymize_sole_admin_self_blocked(rich_db):
    """Sole admin cannot anonymize themselves."""
    with rich_db() as db:
        with pytest.raises(ValueError, match="sole admin"):
            anonymize_user(db, "W0NE", actor_callsign="W0NE")


def test_anonymize_nonexistent_user(rich_db):
    with rich_db() as db:
        with pytest.raises(ValueError, match="User not found"):
            anonymize_user(db, "NOPE", actor_callsign="W0NE")
```

- [x] **Step 3: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_privacy_service.py -k anonymize -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL with ImportError (anonymize_user not found)

- [x] **Step 4: Implement anonymize_user**

Create `backend/privacy/service.py`:

```python
import secrets

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
    """Anonymize a user's account and all associated PII.

    Returns dict with 'anonymous_id' key.
    Raises ValueError for invalid requests.
    """
    user = db.get(User, callsign)
    if user is None:
        raise ValueError("User not found")

    if user.role == UserRole.ADMIN:
        # Check if this is the sole admin
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
```

- [x] **Step 5: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_privacy_service.py -k anonymize -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All PASS

- [x] **Step 6: Commit**

```bash
git add backend/privacy/__init__.py backend/privacy/service.py tests/test_privacy_service.py
git commit -m "feat: add anonymization service

Replaces all PII with ANON-XXXX placeholders across users,
check_ins, raw_messages, members, and audit_log tables.
Deletes PATs. Creates audit entry."
```

---

### Task 3: Data export service

**Files:**
- Modify: `backend/privacy/service.py`
- Modify: `tests/test_privacy_service.py`

- [x] **Step 1: Write export tests**

Append to `tests/test_privacy_service.py`:

```python
from backend.privacy.service import export_user_data


def test_export_user_data_structure(rich_db):
    with rich_db() as db:
        data = export_user_data(db, "KD0TST")

    assert "exported_at" in data
    assert data["user"]["callsign"] == "KD0TST"
    assert data["user"]["name"] == "Test User"
    assert data["user"]["email"] == "test@example.com"
    assert data["user"]["role"] == "viewer"
    assert "created_at" in data["user"]


def test_export_includes_checkins(rich_db):
    with rich_db() as db:
        data = export_user_data(db, "KD0TST")

    assert len(data["check_ins"]) == 1
    ci = data["check_ins"][0]
    assert ci["callsign"] == "KD0TST"
    assert ci["city"] == "Denver"
    assert ci["latitude"] == 39.7392


def test_export_includes_raw_messages(rich_db):
    with rich_db() as db:
        data = export_user_data(db, "KD0TST")

    assert len(data["raw_messages"]) == 1
    msg = data["raw_messages"][0]
    assert msg["from_address"] == "kd0tst@winlink.org"
    assert "body" in msg


def test_export_includes_member_record(rich_db):
    with rich_db() as db:
        data = export_user_data(db, "KD0TST")

    assert data["member_record"]["callsign"] == "KD0TST"
    assert data["member_record"]["total_check_ins"] == 10


def test_export_includes_audit_log(rich_db):
    with rich_db() as db:
        data = export_user_data(db, "KD0TST")

    assert len(data["audit_log"]) == 1
    assert data["audit_log"][0]["target_callsign"] == "KD0TST"


def test_export_includes_tokens_without_secrets(rich_db):
    with rich_db() as db:
        data = export_user_data(db, "KD0TST")

    assert len(data["tokens"]) == 1
    tok = data["tokens"][0]
    assert tok["name"] == "Test Token"
    assert tok["scopes"] == "schedule:read"
    assert "token_hash" not in tok
    assert "token_prefix" not in tok


def test_export_nonexistent_user(rich_db):
    with rich_db() as db:
        with pytest.raises(ValueError, match="User not found"):
            export_user_data(db, "NOPE")
```

- [x] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_privacy_service.py -k export -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL with ImportError

- [x] **Step 3: Implement export_user_data**

Add to `backend/privacy/service.py`:

```python
from datetime import datetime, timezone


def export_user_data(db: Session, callsign: str) -> dict:
    """Export all data associated with a user's callsign as a dict."""
    user = db.get(User, callsign)
    if user is None:
        raise ValueError("User not found")

    # User profile
    user_data = {
        "callsign": user.callsign,
        "name": user.name,
        "email": user.email,
        "role": user.role.value,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }

    # Check-ins
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

    # Raw messages linked to check-ins
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

    # Member record
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

    # Audit log (user as actor or target)
    audit_entries = (
        db.query(AuditLog)
        .filter(
            (AuditLog.actor_callsign == callsign)
            | (AuditLog.target_callsign == callsign)
        )
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

    # Tokens (excluding secrets)
    tokens = (
        db.query(PersonalAccessToken)
        .filter(PersonalAccessToken.user_callsign == callsign)
        .all()
    )
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
```

- [x] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_privacy_service.py -k export -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All PASS

- [x] **Step 5: Commit**

```bash
git add backend/privacy/service.py tests/test_privacy_service.py
git commit -m "feat: add data export service

Collects all user data (profile, check-ins, messages, member
record, audit log, tokens) into a JSON-serializable dict.
Excludes secrets (token hashes, oidc_subject)."
```

---

### Task 4: Privacy API routes

**Files:**
- Create: `backend/privacy/routes.py`
- Create: `tests/test_privacy_routes.py`

- [x] **Step 1: Write route tests**

Create `tests/test_privacy_routes.py`:

```python
import hashlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.auth.pat_models import PersonalAccessToken
from backend.modules.checkins.models import (
    CheckIn, Member, RawMessage, MessageType, ParseStatus, TimingStatus,
)
from backend.modules.schedule.models import NetSession
from backend.audit.models import AuditLog
from backend.privacy.routes import privacy_router
from backend.config import Settings
from datetime import datetime, timezone


@pytest.fixture
def route_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
    )


@pytest.fixture
def route_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin User",
            role=UserRole.ADMIN,
            email="admin@example.com",
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Test User",
            role=UserRole.VIEWER,
            email="viewer@example.com",
        )
        session.add_all([admin, viewer])
        session.flush()

        ns = NetSession(
            id=1,
            start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            grace_period_hours=1,
            session_type="regular",
            status="closed",
        )
        session.add(ns)
        session.flush()

        ci = CheckIn(
            session_id=1,
            callsign="KD0TST",
            name="Test User",
            city="Denver",
            mode="Winlink",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
        )
        session.add(ci)
        session.commit()
    return factory


@pytest.fixture
def route_app(route_settings, route_db):
    app = FastAPI()
    app.state.session_factory = route_db
    app.state.settings = route_settings
    app.include_router(privacy_router, prefix="/api/privacy")
    return app


@pytest.fixture
async def route_client(route_app):
    transport = ASGITransport(app=route_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- Export route tests ---


@pytest.mark.asyncio
async def test_self_export(route_client, route_settings):
    token = create_access_token("KD0TST", "viewer", route_settings)
    response = await route_client.get(
        "/api/privacy/export", cookies={"access_token": token}
    )
    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "")
    data = response.json()
    assert data["user"]["callsign"] == "KD0TST"


@pytest.mark.asyncio
async def test_admin_export_other_user(route_client, route_settings):
    token = create_access_token("W0NE", "admin", route_settings)
    response = await route_client.get(
        "/api/privacy/export/KD0TST", cookies={"access_token": token}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["user"]["callsign"] == "KD0TST"


@pytest.mark.asyncio
async def test_viewer_cannot_export_other_user(route_client, route_settings):
    token = create_access_token("KD0TST", "viewer", route_settings)
    response = await route_client.get(
        "/api/privacy/export/W0NE", cookies={"access_token": token}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_export(route_client):
    response = await route_client.get("/api/privacy/export")
    assert response.status_code == 401


# --- Anonymize route tests ---


@pytest.mark.asyncio
async def test_self_anonymize(route_client, route_settings):
    token = create_access_token("KD0TST", "viewer", route_settings)
    response = await route_client.post(
        "/api/privacy/anonymize",
        json={"confirm": True},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["anonymized"] is True
    assert data["anonymous_id"].startswith("ANON-")
    # Cookie should be cleared
    set_cookie = response.headers.get("set-cookie", "")
    assert "access_token" in set_cookie


@pytest.mark.asyncio
async def test_self_anonymize_requires_confirm(route_client, route_settings):
    token = create_access_token("KD0TST", "viewer", route_settings)
    response = await route_client.post(
        "/api/privacy/anonymize",
        json={"confirm": False},
        cookies={"access_token": token},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_self_anonymize_without_body(route_client, route_settings):
    token = create_access_token("KD0TST", "viewer", route_settings)
    response = await route_client.post(
        "/api/privacy/anonymize",
        json={},
        cookies={"access_token": token},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_admin_anonymize_other_user(route_client, route_settings):
    token = create_access_token("W0NE", "admin", route_settings)
    response = await route_client.post(
        "/api/privacy/anonymize/KD0TST",
        json={"confirm": True},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["anonymized"] is True


@pytest.mark.asyncio
async def test_viewer_cannot_anonymize_other(route_client, route_settings):
    token = create_access_token("KD0TST", "viewer", route_settings)
    response = await route_client.post(
        "/api/privacy/anonymize/W0NE",
        json={"confirm": True},
        cookies={"access_token": token},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_anonymize(route_client):
    response = await route_client.post(
        "/api/privacy/anonymize", json={"confirm": True}
    )
    assert response.status_code == 401
```

- [x] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_privacy_routes.py -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL with ImportError

- [x] **Step 3: Implement privacy routes**

Create `backend/privacy/routes.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session, require_role
from backend.auth.models import User, UserRole
from backend.privacy.service import anonymize_user, export_user_data

privacy_router = APIRouter()


class AnonymizeRequest(BaseModel):
    confirm: bool = False


@privacy_router.get("/export")
def export_own_data(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    data = export_user_data(db, user.callsign)
    return Response(
        content=__import__("json").dumps(data, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="skynetcontrol-export-{user.callsign}.json"'
        },
    )


@privacy_router.get("/export/{callsign}")
def export_user_data_admin(
    callsign: str,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    try:
        data = export_user_data(db, callsign)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return Response(
        content=__import__("json").dumps(data, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="skynetcontrol-export-{callsign}.json"'
        },
    )


@privacy_router.post("/anonymize")
def anonymize_own_account(
    body: AnonymizeRequest,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Confirmation required")

    try:
        result = anonymize_user(db, user.callsign, actor_callsign=user.callsign)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Clear auth cookie
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")

    return {
        "anonymized": True,
        "anonymous_id": result["anonymous_id"],
        "message": "Account anonymized. All personal data has been replaced.",
    }


@privacy_router.post("/anonymize/{callsign}")
def anonymize_user_admin(
    callsign: str,
    body: AnonymizeRequest,
    user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Confirmation required")

    try:
        result = anonymize_user(db, callsign, actor_callsign=user.callsign)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "anonymized": True,
        "anonymous_id": result["anonymous_id"],
        "message": "Account anonymized. All personal data has been replaced.",
    }
```

- [x] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_privacy_routes.py -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All PASS

- [x] **Step 5: Commit**

```bash
git add backend/privacy/routes.py tests/test_privacy_routes.py
git commit -m "feat: add privacy API routes

GET /api/privacy/export - self-service data export
GET /api/privacy/export/{callsign} - admin export
POST /api/privacy/anonymize - self-service anonymization
POST /api/privacy/anonymize/{callsign} - admin anonymization"
```

---

### Task 5: Wire privacy routes into the app

**Files:**
- Modify: `backend/app.py:21-22,91`
- Test: Run existing test suite

- [x] **Step 1: Add privacy router import and registration**

In `backend/app.py`, add import after line 22:

```python
from backend.privacy.routes import privacy_router
```

Add router registration after line 91 (`app.include_router(scanner_router, prefix="/api/scanner")`):

```python
    app.include_router(privacy_router, prefix="/api/privacy")
```

- [x] **Step 2: Run full test suite to verify nothing breaks**

Run: `nix-shell --run "python -m pytest tests/ -v --tb=short" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All PASS

- [x] **Step 3: Commit**

```bash
git add backend/app.py
git commit -m "feat: register privacy routes at /api/privacy"
```

---

### Task 6: Frontend types and API client

**Files:**
- Modify: `frontend/src/types/index.ts:1`
- Create: `frontend/src/api/privacy.ts`

- [x] **Step 1: Add "deleted" to UserRole type**

In `frontend/src/types/index.ts`, change line 1 from:

```typescript
export type UserRole = "pending" | "viewer" | "net_control" | "admin";
```

to:

```typescript
export type UserRole = "pending" | "viewer" | "net_control" | "admin" | "deleted";
```

- [x] **Step 2: Create privacy API client**

Create `frontend/src/api/privacy.ts`:

```typescript
import { apiFetch } from "./client";

export async function exportMyData(): Promise<void> {
  const response = await fetch("/api/privacy/export");
  if (!response.ok) {
    throw new Error("Export failed");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `skynetcontrol-export.json`;
  a.click();
  URL.revokeObjectURL(url);
}

export async function exportUserData(callsign: string): Promise<void> {
  const response = await fetch(
    `/api/privacy/export/${encodeURIComponent(callsign)}`
  );
  if (!response.ok) {
    throw new Error("Export failed");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `skynetcontrol-export-${callsign}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

export async function anonymizeMyAccount(): Promise<{
  anonymized: boolean;
  anonymous_id: string;
  message: string;
}> {
  return apiFetch("/privacy/anonymize", {
    method: "POST",
    body: JSON.stringify({ confirm: true }),
  });
}

export async function anonymizeUser(callsign: string): Promise<{
  anonymized: boolean;
  anonymous_id: string;
  message: string;
}> {
  return apiFetch(`/privacy/anonymize/${encodeURIComponent(callsign)}`, {
    method: "POST",
    body: JSON.stringify({ confirm: true }),
  });
}
```

- [x] **Step 3: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/api/privacy.ts
git commit -m "feat: add privacy API client and deleted role type"
```

---

### Task 7: Privacy Policy page

**Files:**
- Create: `frontend/src/pages/PrivacyPolicyPage.tsx`
- Modify: `frontend/src/App.tsx`

- [x] **Step 1: Create PrivacyPolicyPage component**

Create `frontend/src/pages/PrivacyPolicyPage.tsx`:

```tsx
export function PrivacyPolicyPage() {
  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-2xl font-bold text-text-primary mb-6">
        Privacy Policy
      </h1>

      <div className="bg-bg-surface border border-border rounded-lg p-6 space-y-6 text-sm text-text-secondary leading-relaxed">
        <section>
          <h2 className="text-lg font-semibold text-text-primary mb-2">
            What Data We Collect
          </h2>
          <p>
            This application collects the following data to operate the amateur
            radio net:
          </p>
          <ul className="list-disc pl-5 mt-2 space-y-1">
            <li>
              <strong>Callsign</strong> &mdash; your FCC-assigned amateur radio
              callsign, used as your account identifier
            </li>
            <li>
              <strong>Name</strong> &mdash; your name as provided during
              registration
            </li>
            <li>
              <strong>Email</strong> &mdash; optional, used for account recovery
            </li>
            <li>
              <strong>Location</strong> &mdash; city, county, state, and
              coordinates submitted with check-ins
            </li>
            <li>
              <strong>Check-in messages</strong> &mdash; messages received via
              Winlink or entered manually
            </li>
          </ul>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-text-primary mb-2">
            Why We Collect It
          </h2>
          <p>Data is collected for:</p>
          <ul className="list-disc pl-5 mt-2 space-y-1">
            <li>Net operations &mdash; tracking check-ins and participation</li>
            <li>
              Roster generation &mdash; producing net rosters for distribution
            </li>
            <li>
              Check-in tracking &mdash; maintaining member activity history
            </li>
          </ul>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-text-primary mb-2">
            How Data Is Stored
          </h2>
          <p>
            All data is stored in a local database on the net operator's server.
            This is a self-hosted application &mdash; your data stays on the
            server operated by your net control.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-text-primary mb-2">
            External Sharing
          </h2>
          <p>
            When delivery backends are configured, net content (reminders and
            rosters) may be shared via:
          </p>
          <ul className="list-disc pl-5 mt-2 space-y-1">
            <li>groups.io &mdash; posted to the configured group</li>
            <li>Email &mdash; sent to the configured address via SMTP</li>
            <li>Winlink &mdash; sent via PAT radio email</li>
          </ul>
          <p className="mt-2">
            Individual user data is not shared externally unless it appears in a
            roster or check-in summary.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-text-primary mb-2">
            Cookies
          </h2>
          <p>
            This application uses two strictly-necessary HTTPOnly cookies for
            authentication (<code>access_token</code> and{" "}
            <code>refresh_token</code>). No tracking, analytics, or third-party
            cookies are used.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-text-primary mb-2">
            Your Rights
          </h2>
          <p>You have the right to:</p>
          <ul className="list-disc pl-5 mt-2 space-y-1">
            <li>
              <strong>Export your data</strong> &mdash; download all data
              associated with your account as a JSON file
            </li>
            <li>
              <strong>Delete your account</strong> &mdash; anonymize your
              account, replacing all personal data with opaque placeholders
            </li>
          </ul>
          <p className="mt-2">
            Both actions are available from your{" "}
            <a href="/profile" className="text-accent hover:underline">
              Profile page
            </a>
            . Net administrators can also perform these actions on your behalf.
          </p>
        </section>
      </div>
    </div>
  );
}
```

- [x] **Step 2: Add /privacy route to App.tsx**

In `frontend/src/App.tsx`, add import after line 14:

```typescript
import { PrivacyPolicyPage } from "./pages/PrivacyPolicyPage";
```

Inside the `<Route element={<ProtectedRoute><AppShell /></ProtectedRoute>}>` block, add a new route after the `/config` route (line 64):

```tsx
        <Route path="/privacy" element={<PrivacyPolicyPage />} />
```

- [x] **Step 3: Add privacy policy link to sidebar footer**

In `frontend/src/layouts/Sidebar.tsx`, add a link to the privacy policy in the footer section. After the logout button (line 73), add:

```tsx
        <NavLink
          to="/privacy"
          className="text-xs text-text-muted hover:text-text-secondary transition-colors px-2"
        >
          Privacy Policy
        </NavLink>
```

Also add the same link to `frontend/src/layouts/MobileMenu.tsx` in its footer area.

- [x] **Step 4: Commit**

```bash
git add frontend/src/pages/PrivacyPolicyPage.tsx frontend/src/App.tsx frontend/src/layouts/Sidebar.tsx frontend/src/layouts/MobileMenu.tsx
git commit -m "feat: add privacy policy page at /privacy

Link added to sidebar and mobile menu footers."
```

---

### Task 8: User self-service privacy actions on Profile page

**Files:**
- Modify: `frontend/src/pages/ProfilePage.tsx`

- [x] **Step 1: Read the full ProfilePage to understand current structure**

Read `frontend/src/pages/ProfilePage.tsx` to understand the full component layout before modifying.

- [x] **Step 2: Add privacy actions to ProfilePage**

Add imports at the top of the file:

```typescript
import { exportMyData, anonymizeMyAccount } from "../api/privacy";
```

Add state and handlers inside the `ProfilePage` component, after the existing state declarations:

```typescript
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [deleting, setDeleting] = useState(false);

  const handleExportData = async () => {
    try {
      await exportMyData();
      addToast("Data export downloaded", "success");
    } catch {
      addToast("Failed to export data", "error");
    }
  };

  const handleDeleteAccount = async () => {
    if (deleteConfirm !== "DELETE") return;
    setDeleting(true);
    try {
      await anonymizeMyAccount();
      window.location.href = "/login";
    } catch {
      addToast("Failed to delete account", "error");
      setDeleting(false);
    }
  };
```

Add a "Privacy & Data" section at the bottom of the page's JSX, before the closing `</div>`:

```tsx
      {/* Privacy & Data Section */}
      <div className="mt-8">
        <h2 className="text-lg font-semibold text-text-primary mb-3">
          Privacy & Data
        </h2>
        <div className="bg-bg-surface border border-border rounded-lg p-4 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-text-primary font-medium">
                Download My Data
              </p>
              <p className="text-xs text-text-muted">
                Export all your data as a JSON file
              </p>
            </div>
            <button
              onClick={handleExportData}
              className="text-xs px-3 py-1.5 rounded bg-accent/10 text-accent border border-accent/25 hover:bg-accent/20"
            >
              Download
            </button>
          </div>
          <div className="border-t border-border" />
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-text-primary font-medium">
                Delete My Account
              </p>
              <p className="text-xs text-text-muted">
                Permanently anonymize your account and all personal data
              </p>
            </div>
            <button
              onClick={() => setShowDeleteDialog(true)}
              className="text-xs px-3 py-1.5 rounded bg-danger/10 text-danger border border-danger/25 hover:bg-danger/20"
            >
              Delete Account
            </button>
          </div>
        </div>
      </div>

      {/* Delete confirmation dialog */}
      {showDeleteDialog && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-bg-surface border border-border rounded-lg p-6 max-w-md mx-4">
            <h3 className="text-lg font-bold text-danger mb-2">
              Delete Account
            </h3>
            <p className="text-sm text-text-secondary mb-3">
              This action is <strong>irreversible</strong>. Your account will be
              anonymized and all personal data replaced with placeholders. You
              will be logged out immediately.
            </p>
            <p className="text-sm text-text-secondary mb-3">
              Type <strong>DELETE</strong> to confirm:
            </p>
            <input
              type="text"
              value={deleteConfirm}
              onChange={(e) => setDeleteConfirm(e.target.value)}
              className="w-full bg-bg-elevated border border-border rounded-md px-3 py-1.5 text-sm text-text-primary font-mono mb-4"
              placeholder="Type DELETE"
            />
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => {
                  setShowDeleteDialog(false);
                  setDeleteConfirm("");
                }}
                className="text-xs px-3 py-1.5 rounded bg-bg-elevated text-text-muted border border-border hover:bg-bg-base"
              >
                Cancel
              </button>
              <button
                onClick={handleDeleteAccount}
                disabled={deleteConfirm !== "DELETE" || deleting}
                className="text-xs px-3 py-1.5 rounded bg-danger text-white border border-danger hover:bg-danger/90 disabled:opacity-50"
              >
                {deleting ? "Deleting..." : "Confirm Delete"}
              </button>
            </div>
          </div>
        </div>
      )}
```

- [x] **Step 3: Commit**

```bash
git add frontend/src/pages/ProfilePage.tsx
git commit -m "feat: add privacy actions to profile page

Download My Data button and Delete My Account with confirmation dialog."
```

---

### Task 9: Admin privacy controls on Users page

**Files:**
- Modify: `frontend/src/pages/UsersPage.tsx`

- [x] **Step 1: Add privacy imports and handlers**

In `frontend/src/pages/UsersPage.tsx`, add import:

```typescript
import { exportUserData, anonymizeUser } from "../api/privacy";
```

Add state inside the `UsersPage` component:

```typescript
  const [anonymizeTarget, setAnonymizeTarget] = useState<string | null>(null);
  const [anonymizeConfirm, setAnonymizeConfirm] = useState("");
  const [anonymizing, setAnonymizing] = useState(false);
```

Add handlers:

```typescript
  const handleExportUser = async (callsign: string) => {
    try {
      await exportUserData(callsign);
      addToast(`Data exported for ${callsign}`, "success");
    } catch {
      addToast("Failed to export data", "error");
    }
  };

  const handleAnonymizeUser = async () => {
    if (!anonymizeTarget || anonymizeConfirm !== "DELETE") return;
    setAnonymizing(true);
    try {
      await anonymizeUser(anonymizeTarget);
      addToast(`${anonymizeTarget} has been anonymized`, "success");
      setAnonymizeTarget(null);
      setAnonymizeConfirm("");
      loadData();
    } catch {
      addToast("Failed to anonymize user", "error");
    } finally {
      setAnonymizing(false);
    }
  };
```

- [x] **Step 2: Filter out DELETED users and add action buttons**

Update the `filteredUsers` filter to exclude deleted users by changing:

```typescript
  const filteredUsers = users.filter((u) => {
    const matchesSearch = u.callsign.toLowerCase().includes(search.toLowerCase());
    const matchesRole = roleFilter === "all" || u.role === roleFilter;
    return matchesSearch && matchesRole;
  });
```

to:

```typescript
  const filteredUsers = users.filter((u) => {
    if (u.role === "deleted") return false;
    const matchesSearch = u.callsign.toLowerCase().includes(search.toLowerCase());
    const matchesRole = roleFilter === "all" || u.role === roleFilter;
    return matchesSearch && matchesRole;
  });
```

In the table body, replace the Actions `<td>` (the block starting at line 218 that currently shows Approve/Reject or "—") with:

```tsx
                <td className="px-4 py-3">
                  <div className="flex gap-1">
                    {u.pending_callsign && (
                      <>
                        <button
                          onClick={() => handleApprove(u.callsign)}
                          className="text-xs px-2 py-1 rounded bg-success/10 text-success border border-success/25 hover:bg-success/20"
                        >
                          Approve
                        </button>
                        <button
                          onClick={() => handleReject(u.callsign)}
                          className="text-xs px-2 py-1 rounded bg-danger/10 text-danger border border-danger/25 hover:bg-danger/20"
                        >
                          Reject
                        </button>
                      </>
                    )}
                    {u.callsign !== currentUser.callsign && (
                      <>
                        <button
                          onClick={() => handleExportUser(u.callsign)}
                          title="Export data"
                          className="text-xs px-2 py-1 rounded bg-bg-elevated text-text-muted border border-border hover:bg-bg-base"
                        >
                          Export
                        </button>
                        <button
                          onClick={() => setAnonymizeTarget(u.callsign)}
                          title="Anonymize user"
                          className="text-xs px-2 py-1 rounded bg-danger/10 text-danger border border-danger/25 hover:bg-danger/20"
                        >
                          Anonymize
                        </button>
                      </>
                    )}
                    {!u.pending_callsign && u.callsign === currentUser.callsign && (
                      <span className="text-text-muted">&mdash;</span>
                    )}
                  </div>
                </td>
```

- [x] **Step 3: Add anonymize confirmation dialog**

Add before the closing `</div>` of the component's return JSX (before the audit log section's closing):

```tsx
      {/* Anonymize confirmation dialog */}
      {anonymizeTarget && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-bg-surface border border-border rounded-lg p-6 max-w-md mx-4">
            <h3 className="text-lg font-bold text-danger mb-2">
              Anonymize {anonymizeTarget}
            </h3>
            <p className="text-sm text-text-secondary mb-3">
              This action is <strong>irreversible</strong>. All personal data for{" "}
              <span className="font-mono text-accent">{anonymizeTarget}</span>{" "}
              will be replaced with anonymous placeholders.
            </p>
            <p className="text-sm text-text-secondary mb-3">
              Type <strong>DELETE</strong> to confirm:
            </p>
            <input
              type="text"
              value={anonymizeConfirm}
              onChange={(e) => setAnonymizeConfirm(e.target.value)}
              className="w-full bg-bg-elevated border border-border rounded-md px-3 py-1.5 text-sm text-text-primary font-mono mb-4"
              placeholder="Type DELETE"
            />
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => {
                  setAnonymizeTarget(null);
                  setAnonymizeConfirm("");
                }}
                className="text-xs px-3 py-1.5 rounded bg-bg-elevated text-text-muted border border-border hover:bg-bg-base"
              >
                Cancel
              </button>
              <button
                onClick={handleAnonymizeUser}
                disabled={anonymizeConfirm !== "DELETE" || anonymizing}
                className="text-xs px-3 py-1.5 rounded bg-danger text-white border border-danger hover:bg-danger/90 disabled:opacity-50"
              >
                {anonymizing ? "Anonymizing..." : "Confirm Anonymize"}
              </button>
            </div>
          </div>
        </div>
      )}
```

- [x] **Step 4: Add roleBadgeClass entry for deleted**

In the `roleBadgeClass` object, add:

```typescript
  deleted: "bg-bg-elevated text-text-muted border-border",
```

And add to `ROLE_OPTIONS` (though deleted users are filtered out, needed for type completeness):

```typescript
  { value: "deleted", label: "deleted" },
```

- [x] **Step 5: Commit**

```bash
git add frontend/src/pages/UsersPage.tsx
git commit -m "feat: add admin export/anonymize controls to users page

Export and Anonymize buttons per user row with confirmation dialog.
DELETED users filtered from the list."
```

---

### Task 10: Full test verification

**Files:**
- All test files

- [x] **Step 1: Run the complete test suite**

Run: `nix-shell --run "python -m pytest tests/ -v --tb=short" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All tests pass, including all new privacy tests.

- [x] **Step 2: Verify frontend builds**

Run: `cd frontend && nix-shell --run "npm run build" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: Build succeeds with no TypeScript errors.

- [x] **Step 3: Fix any failures found**

If any tests or build steps fail, fix them before proceeding.

- [x] **Step 4: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: resolve test/build issues from privacy compliance"
```
