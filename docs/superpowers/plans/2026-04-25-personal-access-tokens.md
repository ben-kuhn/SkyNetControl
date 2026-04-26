# Personal Access Tokens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Personal Access Tokens (PATs) so external tools can authenticate with the API using scoped, revocable Bearer tokens.

**Architecture:** Opaque tokens (`skynet_` + 32 hex bytes), stored as SHA-256 hashes. The existing `get_current_user` dependency is extended to accept Bearer tokens alongside cookie JWTs. A new `require_scope()` dependency enforces per-token scope restrictions. The frontend ProfilePage replaces its PAT placeholder with a token management UI.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, React/TypeScript (existing frontend)

**Spec:** `docs/superpowers/specs/2026-04-24-personal-access-tokens-design.md`

---

## File Structure

### Backend (new files)

| File | Responsibility |
|------|---------------|
| `backend/auth/scopes.py` | Scope constants, scope-to-minimum-role mapping |
| `backend/auth/pat_models.py` | `PersonalAccessToken` SQLAlchemy model |
| `backend/auth/pat_service.py` | Token create/list/revoke/authenticate logic |
| `backend/auth/pat_routes.py` | API endpoints: POST/GET/DELETE `/api/auth/tokens` |
| `alembic/versions/<hash>_add_personal_access_tokens.py` | Migration for `personal_access_tokens` table |

### Backend (modified files)

| File | Change |
|------|--------|
| `backend/auth/dependencies.py` | Extend `get_current_user` for Bearer tokens, add `require_scope()` |
| `backend/modules/schedule/routes.py` | Add `require_scope("schedule:read")` to `list_sessions_route` |
| `backend/app.py` | Register `pat_router` |
| `alembic/env.py` | Import `backend.auth.pat_models` |

### Frontend (new files)

| File | Responsibility |
|------|---------------|
| `frontend/src/api/tokens.ts` | `createToken`, `listTokens`, `revokeToken` API functions |

### Frontend (modified files)

| File | Change |
|------|--------|
| `frontend/src/types/index.ts` | Add `Token`, `TokenCreate`, `TokenWithSecret` types + `SCOPES` constant |
| `frontend/src/pages/ProfilePage.tsx` | Replace PAT placeholder with full token manager |

### Test files (new)

| File | Covers |
|------|--------|
| `tests/test_pat_scopes.py` | Scope constants and validation |
| `tests/test_pat_service.py` | Token create/list/revoke/authenticate service layer |
| `tests/test_pat_routes.py` | API endpoint integration tests |
| `tests/test_pat_auth.py` | Extended `get_current_user` + `require_scope` dependency tests |

---

## Task 1: Scope Constants

**Files:**
- Create: `backend/auth/scopes.py`
- Test: `tests/test_pat_scopes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pat_scopes.py
import pytest

from backend.auth.scopes import SCOPES, SCOPE_NAMES, validate_scopes_for_role
from backend.auth.models import UserRole


def test_scopes_dict_has_expected_entries():
    assert "schedule:read" in SCOPES
    assert "schedule:write" in SCOPES
    assert "checkins:read" in SCOPES
    assert "checkins:write" in SCOPES
    assert "roster:read" in SCOPES
    assert "map:read" in SCOPES
    assert "users:read" in SCOPES
    assert "users:write" in SCOPES
    assert "config:read" in SCOPES
    assert "config:write" in SCOPES
    assert len(SCOPES) == 10


def test_scope_names_matches_scopes_keys():
    assert SCOPE_NAMES == set(SCOPES.keys())


def test_validate_scopes_viewer_can_read_schedule():
    validate_scopes_for_role(["schedule:read"], UserRole.VIEWER)


def test_validate_scopes_viewer_cannot_write_schedule():
    with pytest.raises(ValueError, match="schedule:write"):
        validate_scopes_for_role(["schedule:write"], UserRole.VIEWER)


def test_validate_scopes_net_control_can_write_schedule():
    validate_scopes_for_role(["schedule:write"], UserRole.NET_CONTROL)


def test_validate_scopes_admin_can_use_all():
    validate_scopes_for_role(list(SCOPES.keys()), UserRole.ADMIN)


def test_validate_scopes_rejects_unknown_scope():
    with pytest.raises(ValueError, match="invalid:scope"):
        validate_scopes_for_role(["invalid:scope"], UserRole.ADMIN)


def test_validate_scopes_rejects_empty():
    with pytest.raises(ValueError, match="at least one"):
        validate_scopes_for_role([], UserRole.ADMIN)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix-shell --run "python -m pytest tests/test_pat_scopes.py -v"`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.auth.scopes'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/auth/scopes.py
from backend.auth.models import UserRole

# Scope name → minimum role required to create a token with this scope.
# Role hierarchy: ADMIN > NET_CONTROL > VIEWER > PENDING
_ROLE_RANK = {
    UserRole.PENDING: 0,
    UserRole.VIEWER: 1,
    UserRole.NET_CONTROL: 2,
    UserRole.ADMIN: 3,
}

SCOPES: dict[str, dict] = {
    "schedule:read":  {"description": "View sessions",           "min_role": UserRole.VIEWER},
    "schedule:write": {"description": "Create/edit/delete sessions", "min_role": UserRole.NET_CONTROL},
    "checkins:read":  {"description": "View check-in data",      "min_role": UserRole.VIEWER},
    "checkins:write": {"description": "Submit/manage check-ins", "min_role": UserRole.NET_CONTROL},
    "roster:read":    {"description": "View roster data",        "min_role": UserRole.NET_CONTROL},
    "map:read":       {"description": "View map/GeoJSON data",   "min_role": UserRole.VIEWER},
    "users:read":     {"description": "List users",              "min_role": UserRole.ADMIN},
    "users:write":    {"description": "Manage users/roles",      "min_role": UserRole.ADMIN},
    "config:read":    {"description": "View app configuration",  "min_role": UserRole.ADMIN},
    "config:write":   {"description": "Modify app configuration","min_role": UserRole.ADMIN},
}

SCOPE_NAMES: set[str] = set(SCOPES.keys())


def validate_scopes_for_role(scopes: list[str], role: UserRole) -> None:
    if not scopes:
        raise ValueError("Token must have at least one scope")

    user_rank = _ROLE_RANK[role]
    for scope in scopes:
        if scope not in SCOPES:
            raise ValueError(f"Unknown scope: {scope}")
        required_rank = _ROLE_RANK[SCOPES[scope]["min_role"]]
        if user_rank < required_rank:
            raise ValueError(
                f"Your role cannot use scope: {scope}"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix-shell --run "python -m pytest tests/test_pat_scopes.py -v"`
Expected: PASS — all 8 tests

- [ ] **Step 5: Commit**

```bash
git add backend/auth/scopes.py tests/test_pat_scopes.py
git commit -m "feat: add PAT scope constants and validation

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 2: PersonalAccessToken Model + Migration

**Files:**
- Create: `backend/auth/pat_models.py`
- Create: `alembic/versions/<auto>_add_personal_access_tokens.py`
- Modify: `alembic/env.py` (add model import)
- Test: `tests/test_pat_service.py` (model creation test)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pat_service.py
import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.pat_models import PersonalAccessToken


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite:///",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session_factory(db_engine):
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture
def seeded_db(db_session_factory):
    with db_session_factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin User",
            role=UserRole.ADMIN,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer User",
            role=UserRole.VIEWER,
        )
        session.add_all([admin, viewer])
        session.commit()
    return db_session_factory


def test_personal_access_token_model_creation(seeded_db):
    with seeded_db() as session:
        token = PersonalAccessToken(
            user_callsign="W0NE",
            name="Test token",
            token_hash="a" * 64,
            token_prefix="skynet_a3",
            scopes="schedule:read,checkins:read",
        )
        session.add(token)
        session.commit()
        session.refresh(token)

        assert token.id is not None
        assert token.user_callsign == "W0NE"
        assert token.name == "Test token"
        assert token.token_hash == "a" * 64
        assert token.token_prefix == "skynet_a3"
        assert token.scopes == "schedule:read,checkins:read"
        assert token.created_at is not None
        assert token.expires_at is None
        assert token.last_used_at is None
        assert token.revoked_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix-shell --run "python -m pytest tests/test_pat_service.py::test_personal_access_token_model_creation -v"`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.auth.pat_models'`

- [ ] **Step 3: Write the model**

```python
# backend/auth/pat_models.py
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, Text, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class PersonalAccessToken(Base):
    __tablename__ = "personal_access_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_callsign: Mapped[str] = mapped_column(
        String(20), ForeignKey("users.callsign"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(8), nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `nix-shell --run "python -m pytest tests/test_pat_service.py::test_personal_access_token_model_creation -v"`
Expected: PASS

- [ ] **Step 5: Add model import to alembic/env.py**

Add this line after the existing model imports in `alembic/env.py`:

```python
import backend.auth.pat_models  # noqa: F401
```

- [ ] **Step 6: Generate Alembic migration**

Run: `nix-shell --run "alembic revision --autogenerate -m 'add personal access tokens'"`

Verify the generated migration creates a `personal_access_tokens` table with the correct columns and foreign key.

- [ ] **Step 7: Run the migration to verify**

Run: `nix-shell --run "alembic upgrade head"`
Expected: Migration applies successfully.

- [ ] **Step 8: Commit**

```bash
git add backend/auth/pat_models.py alembic/env.py alembic/versions/*personal_access_tokens*.py tests/test_pat_service.py
git commit -m "feat: add PersonalAccessToken model and migration

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 3: PAT Service Layer

**Files:**
- Create: `backend/auth/pat_service.py`
- Modify: `tests/test_pat_service.py` (add service tests)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pat_service.py` (keep existing fixtures and model test):

```python
from backend.auth.pat_service import (
    create_token,
    list_tokens,
    revoke_token,
    authenticate_token,
)


def test_create_token_returns_raw_token(seeded_db):
    with seeded_db() as session:
        result = create_token(
            db=session,
            user_callsign="W0NE",
            user_role=UserRole.ADMIN,
            name="My token",
            scopes=["schedule:read"],
            expires_at=None,
        )
        assert result["token"].startswith("skynet_")
        assert len(result["token"]) == 71  # "skynet_" (7) + 64 hex chars
        assert result["name"] == "My token"
        assert result["token_prefix"] == result["token"][:8]
        assert result["scopes"] == ["schedule:read"]
        assert result["id"] is not None


def test_create_token_stores_hash_not_raw(seeded_db):
    with seeded_db() as session:
        result = create_token(
            db=session,
            user_callsign="W0NE",
            user_role=UserRole.ADMIN,
            name="Hash test",
            scopes=["schedule:read"],
            expires_at=None,
        )
        raw = result["token"]
        pat = session.query(PersonalAccessToken).filter_by(id=result["id"]).one()
        assert pat.token_hash != raw
        assert len(pat.token_hash) == 64  # SHA-256 hex


def test_create_token_rejects_invalid_scope_for_role(seeded_db):
    with seeded_db() as session:
        with pytest.raises(ValueError, match="schedule:write"):
            create_token(
                db=session,
                user_callsign="KD0TST",
                user_role=UserRole.VIEWER,
                name="Bad scope",
                scopes=["schedule:write"],
                expires_at=None,
            )


def test_create_token_enforces_max_10(seeded_db):
    with seeded_db() as session:
        for i in range(10):
            create_token(
                db=session,
                user_callsign="W0NE",
                user_role=UserRole.ADMIN,
                name=f"Token {i}",
                scopes=["schedule:read"],
                expires_at=None,
            )
        with pytest.raises(ValueError, match="maximum"):
            create_token(
                db=session,
                user_callsign="W0NE",
                user_role=UserRole.ADMIN,
                name="Token 11",
                scopes=["schedule:read"],
                expires_at=None,
            )


def test_create_token_rejects_past_expiry(seeded_db):
    with seeded_db() as session:
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="future"):
            create_token(
                db=session,
                user_callsign="W0NE",
                user_role=UserRole.ADMIN,
                name="Expired",
                scopes=["schedule:read"],
                expires_at=past,
            )


def test_create_token_rejects_empty_name(seeded_db):
    with seeded_db() as session:
        with pytest.raises(ValueError, match="name"):
            create_token(
                db=session,
                user_callsign="W0NE",
                user_role=UserRole.ADMIN,
                name="",
                scopes=["schedule:read"],
                expires_at=None,
            )


def test_create_token_rejects_long_name(seeded_db):
    with seeded_db() as session:
        with pytest.raises(ValueError, match="name"):
            create_token(
                db=session,
                user_callsign="W0NE",
                user_role=UserRole.ADMIN,
                name="x" * 101,
                scopes=["schedule:read"],
                expires_at=None,
            )


def test_list_tokens_returns_only_own(seeded_db):
    with seeded_db() as session:
        create_token(session, "W0NE", UserRole.ADMIN, "Admin token", ["schedule:read"], None)
        create_token(session, "KD0TST", UserRole.VIEWER, "Viewer token", ["schedule:read"], None)
        tokens = list_tokens(session, "W0NE")
        assert len(tokens) == 1
        assert tokens[0]["name"] == "Admin token"
        assert "token" not in tokens[0]  # no raw token


def test_list_tokens_excludes_revoked(seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", UserRole.ADMIN, "Revoked", ["schedule:read"], None)
        revoke_token(session, result["id"], "W0NE", is_admin=True)
        tokens = list_tokens(session, "W0NE")
        assert len(tokens) == 0


def test_revoke_token_sets_revoked_at(seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", UserRole.ADMIN, "To revoke", ["schedule:read"], None)
        revoke_token(session, result["id"], "W0NE", is_admin=False)
        pat = session.query(PersonalAccessToken).filter_by(id=result["id"]).one()
        assert pat.revoked_at is not None


def test_revoke_token_admin_can_revoke_others(seeded_db):
    with seeded_db() as session:
        result = create_token(session, "KD0TST", UserRole.VIEWER, "Viewer token", ["schedule:read"], None)
        revoke_token(session, result["id"], "W0NE", is_admin=True)
        pat = session.query(PersonalAccessToken).filter_by(id=result["id"]).one()
        assert pat.revoked_at is not None


def test_revoke_token_non_owner_non_admin_fails(seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", UserRole.ADMIN, "Admin token", ["schedule:read"], None)
        with pytest.raises(ValueError, match="not found"):
            revoke_token(session, result["id"], "KD0TST", is_admin=False)


def test_authenticate_valid_token(seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", UserRole.ADMIN, "Auth test", ["schedule:read"], None)
        raw = result["token"]
        auth = authenticate_token(session, raw)
        assert auth is not None
        assert auth["user_callsign"] == "W0NE"
        assert auth["scopes"] == ["schedule:read"]


def test_authenticate_revoked_token_fails(seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", UserRole.ADMIN, "Revoked", ["schedule:read"], None)
        raw = result["token"]
        revoke_token(session, result["id"], "W0NE", is_admin=False)
        auth = authenticate_token(session, raw)
        assert auth is None


def test_authenticate_expired_token_fails(seeded_db):
    with seeded_db() as session:
        # Create a token with future expiry, then manually set it to the past
        future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        result = create_token(session, "W0NE", UserRole.ADMIN, "Expired", ["schedule:read"], future)
        raw = result["token"]
        pat = session.query(PersonalAccessToken).filter_by(id=result["id"]).one()
        pat.expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        session.commit()
        auth = authenticate_token(session, raw)
        assert auth is None


def test_authenticate_invalid_token_fails(seeded_db):
    with seeded_db() as session:
        auth = authenticate_token(session, "skynet_" + "f" * 64)
        assert auth is None


def test_authenticate_updates_last_used_at(seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", UserRole.ADMIN, "Usage test", ["schedule:read"], None)
        raw = result["token"]
        pat = session.query(PersonalAccessToken).filter_by(id=result["id"]).one()
        assert pat.last_used_at is None
        authenticate_token(session, raw)
        session.refresh(pat)
        assert pat.last_used_at is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_pat_service.py -v -k 'not model_creation'"`
Expected: FAIL — `ImportError: cannot import name 'create_token' from 'backend.auth.pat_service'`

- [ ] **Step 3: Write the service implementation**

```python
# backend/auth/pat_service.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_pat_service.py -v"`
Expected: PASS — all tests

- [ ] **Step 5: Commit**

```bash
git add backend/auth/pat_service.py tests/test_pat_service.py
git commit -m "feat: add PAT service layer (create/list/revoke/authenticate)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Extended Auth Dependencies

**Files:**
- Modify: `backend/auth/dependencies.py`
- Test: `tests/test_pat_auth.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pat_auth.py
import pytest
from fastapi import FastAPI, Depends, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.auth.dependencies import get_current_user, require_scope
from backend.auth.pat_service import create_token
from backend.config import Settings


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
    )


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite:///",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session_factory(db_engine):
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture
def seeded_db(db_session_factory):
    with db_session_factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin User",
            role=UserRole.ADMIN,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer User",
            role=UserRole.VIEWER,
        )
        pending = User(
            callsign="PENDING-abc",
            oidc_subject="auth0|pending",
            name="Pending User",
            role=UserRole.PENDING,
        )
        session.add_all([admin, viewer, pending])
        session.commit()
    return db_session_factory


@pytest.fixture
def test_app(test_settings, seeded_db):
    app = FastAPI()
    app.state.session_factory = seeded_db
    app.state.settings = test_settings

    @app.get("/api/test/me")
    async def me(user: User = Depends(get_current_user)):
        return {"callsign": user.callsign, "role": user.role.value}

    @app.get("/api/test/scoped")
    async def scoped(user: User = Depends(require_scope("schedule:read"))):
        return {"callsign": user.callsign}

    @app.get("/api/test/multi-scope")
    async def multi_scope(user: User = Depends(require_scope("schedule:read", "checkins:read"))):
        return {"callsign": user.callsign}

    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_bearer_pat_authenticates(test_client, seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", UserRole.ADMIN, "Test", ["schedule:read"], None)
        raw = result["token"]
    response = await test_client.get(
        "/api/test/me",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200
    assert response.json()["callsign"] == "W0NE"


@pytest.mark.asyncio
async def test_bearer_invalid_token_returns_401(test_client):
    response = await test_client.get(
        "/api/test/me",
        headers={"Authorization": "Bearer skynet_" + "f" * 64},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bearer_revoked_token_returns_401(test_client, seeded_db):
    from backend.auth.pat_service import revoke_token
    with seeded_db() as session:
        result = create_token(session, "W0NE", UserRole.ADMIN, "Revoke me", ["schedule:read"], None)
        raw = result["token"]
        revoke_token(session, result["id"], "W0NE", is_admin=False)
    response = await test_client.get(
        "/api/test/me",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bearer_pending_user_returns_401(test_client, seeded_db):
    with seeded_db() as session:
        # Manually create token for pending user (bypassing role check)
        from backend.auth.pat_models import PersonalAccessToken
        import hashlib, secrets
        raw = "skynet_" + secrets.token_hex(32)
        pat = PersonalAccessToken(
            user_callsign="PENDING-abc",
            name="Pending token",
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            token_prefix=raw[:8],
            scopes="schedule:read",
        )
        session.add(pat)
        session.commit()
    response = await test_client.get(
        "/api/test/me",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_cookie_auth_still_works(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get("/api/test/me", cookies={"access_token": token})
    assert response.status_code == 200
    assert response.json()["callsign"] == "W0NE"


@pytest.mark.asyncio
async def test_require_scope_passes_with_correct_scope(test_client, seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", UserRole.ADMIN, "Scoped", ["schedule:read"], None)
        raw = result["token"]
    response = await test_client.get(
        "/api/test/scoped",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_require_scope_fails_with_missing_scope(test_client, seeded_db):
    with seeded_db() as session:
        result = create_token(session, "W0NE", UserRole.ADMIN, "Wrong scope", ["checkins:read"], None)
        raw = result["token"]
    response = await test_client.get(
        "/api/test/scoped",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 403
    assert "schedule:read" in response.json()["detail"]


@pytest.mark.asyncio
async def test_require_scope_cookie_auth_bypasses(test_client, test_settings):
    token = create_access_token("W0NE", "admin", test_settings)
    response = await test_client.get(
        "/api/test/scoped",
        cookies={"access_token": token},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_require_scope_multi_scope_all_needed(test_client, seeded_db):
    with seeded_db() as session:
        result = create_token(
            session, "W0NE", UserRole.ADMIN, "Multi",
            ["schedule:read", "checkins:read"], None
        )
        raw = result["token"]
    response = await test_client.get(
        "/api/test/multi-scope",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_require_scope_multi_scope_partial_fails(test_client, seeded_db):
    with seeded_db() as session:
        result = create_token(
            session, "W0NE", UserRole.ADMIN, "Partial",
            ["schedule:read"], None
        )
        raw = result["token"]
    response = await test_client.get(
        "/api/test/multi-scope",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_role_intersection_downgraded_admin(test_client, seeded_db):
    """If admin is downgraded to viewer, tokens with admin-only scopes stop working."""
    with seeded_db() as session:
        result = create_token(session, "W0NE", UserRole.ADMIN, "Admin scope", ["users:read"], None)
        raw = result["token"]
        # Downgrade the user
        user = session.get(User, "W0NE")
        user.role = UserRole.VIEWER
        session.commit()
    # The token has users:read scope, but user is now viewer — auth still works
    # (role intersection happens at scope enforcement, not auth)
    response = await test_client.get(
        "/api/test/me",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_pat_auth.py -v"`
Expected: FAIL — `ImportError: cannot import name 'require_scope' from 'backend.auth.dependencies'`

- [ ] **Step 3: Update dependencies.py**

Replace the full contents of `backend/auth/dependencies.py`:

```python
from typing import Callable

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from backend.auth.models import User, UserRole
from backend.auth.service import decode_access_token
from backend.config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db_session(request: Request):
    session_factory = request.app.state.session_factory
    with session_factory() as session:
        yield session


def get_current_user(
    request: Request,
    access_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db_session),
    app_settings: Settings = Depends(get_settings),
) -> User:
    # Try Bearer token first
    if authorization and authorization.startswith("Bearer skynet_"):
        raw_token = authorization[len("Bearer "):]
        from backend.auth.pat_service import authenticate_token

        auth_result = authenticate_token(db, raw_token)
        if auth_result is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user = db.get(User, auth_result["user_callsign"])
        if user is None or user.role == UserRole.PENDING:
            raise HTTPException(status_code=401, detail="User not found or pending")

        request.state.token_scopes = auth_result["scopes"]
        return user

    # Fall back to cookie JWT
    if not access_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_access_token(access_token, settings=app_settings)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    callsign = payload.get("sub")
    if not callsign:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user = db.get(User, callsign)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    request.state.token_scopes = None  # cookie auth = full access
    return user


def require_role(*roles: UserRole) -> Callable:
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return dependency


def require_not_pending(user: User = Depends(get_current_user)) -> User:
    if user.role == UserRole.PENDING:
        raise HTTPException(status_code=403, detail="Account pending approval")
    return user


def require_scope(*scopes: str) -> Callable:
    def dependency(request: Request, user: User = Depends(get_current_user)) -> User:
        token_scopes = getattr(request.state, "token_scopes", None)
        if token_scopes is None:
            return user  # cookie auth = full access
        for scope in scopes:
            if scope not in token_scopes:
                raise HTTPException(
                    status_code=403,
                    detail=f"Token missing required scope: {scope}",
                )
        return user

    return dependency
```

- [ ] **Step 4: Run new tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_pat_auth.py -v"`
Expected: PASS — all tests

- [ ] **Step 5: Run existing auth dependency tests to verify no regressions**

Run: `nix-shell --run "python -m pytest tests/test_auth_dependencies.py -v"`
Expected: PASS — all existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add backend/auth/dependencies.py tests/test_pat_auth.py
git commit -m "feat: extend get_current_user for Bearer PAT auth, add require_scope

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 5: PAT API Routes

**Files:**
- Create: `backend/auth/pat_routes.py`
- Modify: `backend/app.py` (register router)
- Test: `tests/test_pat_routes.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pat_routes.py
import pytest
from datetime import datetime, timezone, timedelta
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.auth.pat_service import create_token
from backend.config import Settings
from backend.app import create_app


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
    )


@pytest.fixture
def app(test_settings):
    application = create_app(settings=test_settings)
    Base.metadata.create_all(application.state.engine)
    return application


@pytest.fixture
def seed_users(app):
    with app.state.session_factory() as session:
        admin = User(
            callsign="W0NE",
            oidc_subject="auth0|admin",
            name="Admin User",
            role=UserRole.ADMIN,
        )
        viewer = User(
            callsign="KD0TST",
            oidc_subject="auth0|viewer",
            name="Viewer User",
            role=UserRole.VIEWER,
        )
        pending = User(
            callsign="PENDING-abc",
            oidc_subject="auth0|pending",
            name="Pending User",
            role=UserRole.PENDING,
        )
        session.add_all([admin, viewer, pending])
        session.commit()


@pytest.fixture
async def client(app, seed_users):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _admin_cookie(test_settings):
    return {"access_token": create_access_token("W0NE", "admin", test_settings)}


def _viewer_cookie(test_settings):
    return {"access_token": create_access_token("KD0TST", "viewer", test_settings)}


def _pending_cookie(test_settings):
    return {"access_token": create_access_token("PENDING-abc", "pending", test_settings)}


@pytest.mark.asyncio
async def test_create_token_success(client, test_settings):
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "My token", "scopes": ["schedule:read"]},
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "My token"
    assert data["token"].startswith("skynet_")
    assert data["scopes"] == ["schedule:read"]
    assert data["token_prefix"] == data["token"][:8]


@pytest.mark.asyncio
async def test_create_token_with_expiry(client, test_settings):
    future = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "Expiring", "scopes": ["schedule:read"], "expires_at": future},
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 201
    assert response.json()["expires_at"] is not None


@pytest.mark.asyncio
async def test_create_token_invalid_scope_for_viewer(client, test_settings):
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "Bad scope", "scopes": ["users:write"]},
        cookies=_viewer_cookie(test_settings),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_token_pending_user_blocked(client, test_settings):
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "No tokens", "scopes": ["schedule:read"]},
        cookies=_pending_cookie(test_settings),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_token_empty_name(client, test_settings):
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "", "scopes": ["schedule:read"]},
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_token_empty_scopes(client, test_settings):
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "No scopes", "scopes": []},
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_token_past_expiry(client, test_settings):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "Past", "scopes": ["schedule:read"], "expires_at": past},
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_token_cannot_use_pat(client, test_settings, app):
    # Create a token first
    with app.state.session_factory() as session:
        result = create_token(session, "W0NE", UserRole.ADMIN, "Boot", ["schedule:read"], None)
        raw = result["token"]
    # Try to create another token using PAT auth
    response = await client.post(
        "/api/auth/tokens",
        json={"name": "Via PAT", "scopes": ["schedule:read"]},
        headers={"Authorization": f"Bearer {raw}"},
    )
    # Should fail — token management requires cookie auth
    # The endpoint uses require_not_pending which uses get_current_user
    # PAT will authenticate but the endpoint should reject PAT auth
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_tokens(client, test_settings):
    # Create a token
    await client.post(
        "/api/auth/tokens",
        json={"name": "Listed", "scopes": ["schedule:read"]},
        cookies=_admin_cookie(test_settings),
    )
    response = await client.get(
        "/api/auth/tokens",
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Listed"
    assert "token" not in data[0]  # no raw token in list


@pytest.mark.asyncio
async def test_revoke_token(client, test_settings):
    create_resp = await client.post(
        "/api/auth/tokens",
        json={"name": "To revoke", "scopes": ["schedule:read"]},
        cookies=_admin_cookie(test_settings),
    )
    token_id = create_resp.json()["id"]
    response = await client.delete(
        f"/api/auth/tokens/{token_id}",
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 204
    # Verify it's gone from list
    list_resp = await client.get(
        "/api/auth/tokens",
        cookies=_admin_cookie(test_settings),
    )
    assert len(list_resp.json()) == 0


@pytest.mark.asyncio
async def test_revoke_token_not_found(client, test_settings):
    response = await client.delete(
        "/api/auth/tokens/99999",
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_can_revoke_others_token(client, test_settings):
    # Viewer creates a token
    create_resp = await client.post(
        "/api/auth/tokens",
        json={"name": "Viewer token", "scopes": ["schedule:read"]},
        cookies=_viewer_cookie(test_settings),
    )
    token_id = create_resp.json()["id"]
    # Admin revokes it
    response = await client.delete(
        f"/api/auth/tokens/{token_id}",
        cookies=_admin_cookie(test_settings),
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_non_owner_non_admin_cannot_revoke(client, test_settings):
    # Admin creates a token
    create_resp = await client.post(
        "/api/auth/tokens",
        json={"name": "Admin token", "scopes": ["schedule:read"]},
        cookies=_admin_cookie(test_settings),
    )
    token_id = create_resp.json()["id"]
    # Viewer tries to revoke it
    response = await client.delete(
        f"/api/auth/tokens/{token_id}",
        cookies=_viewer_cookie(test_settings),
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_pat_routes.py -v"`
Expected: FAIL — route not found (404 on `/api/auth/tokens`)

- [ ] **Step 3: Write the routes**

```python
# backend/auth/pat_routes.py
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session, require_not_pending
from backend.auth.models import User, UserRole
from backend.auth.pat_service import create_token, list_tokens, revoke_token

pat_router = APIRouter(tags=["tokens"])


def _require_cookie_auth(request: Request, user: User = Depends(require_not_pending)) -> User:
    token_scopes = getattr(request.state, "token_scopes", None)
    if token_scopes is not None:
        raise HTTPException(
            status_code=403,
            detail="Token management requires browser authentication",
        )
    return user


class TokenCreateRequest(BaseModel):
    name: str
    scopes: list[str]
    expires_at: datetime | None = None


@pat_router.post("", status_code=201)
async def create_token_route(
    body: TokenCreateRequest,
    user: User = Depends(_require_cookie_auth),
    db: Session = Depends(get_db_session),
):
    try:
        result = create_token(
            db=db,
            user_callsign=user.callsign,
            user_role=user.role,
            name=body.name,
            scopes=body.scopes,
            expires_at=body.expires_at,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@pat_router.get("")
async def list_tokens_route(
    user: User = Depends(_require_cookie_auth),
    db: Session = Depends(get_db_session),
):
    return list_tokens(db, user.callsign)


@pat_router.delete("/{token_id}", status_code=204)
async def revoke_token_route(
    token_id: int,
    user: User = Depends(_require_cookie_auth),
    db: Session = Depends(get_db_session),
):
    try:
        revoke_token(
            db=db,
            token_id=token_id,
            user_callsign=user.callsign,
            is_admin=user.role == UserRole.ADMIN,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Token not found")
```

- [ ] **Step 4: Register the router in app.py**

Add import at top of `backend/app.py`:

```python
from backend.auth.pat_routes import pat_router
```

Add after the existing `auth_router` registration:

```python
app.include_router(pat_router, prefix="/api/auth/tokens")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_pat_routes.py -v"`
Expected: PASS — all tests

- [ ] **Step 6: Run the full test suite to check for regressions**

Run: `nix-shell --run "python -m pytest -v"`
Expected: All existing tests pass alongside new ones.

- [ ] **Step 7: Commit**

```bash
git add backend/auth/pat_routes.py backend/app.py tests/test_pat_routes.py
git commit -m "feat: add PAT API routes (create/list/revoke)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 6: Scope Wiring on Schedule Endpoints

**Files:**
- Modify: `backend/modules/schedule/routes.py`

- [ ] **Step 1: Write the failing test**

Add to the bottom of `tests/test_pat_auth.py`:

```python
@pytest.mark.asyncio
async def test_schedule_endpoint_requires_scope():
    """PAT with wrong scope gets 403 on /api/schedule/sessions."""
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)
    from backend.app import create_app
    app = create_app(settings=settings)
    Base.metadata.create_all(app.state.engine)

    with app.state.session_factory() as session:
        admin = User(
            callsign="W0NE", oidc_subject="auth0|admin",
            name="Admin User", role=UserRole.ADMIN,
        )
        session.add(admin)
        session.commit()
        result = create_token(session, "W0NE", UserRole.ADMIN, "Wrong scope", ["checkins:read"], None)
        raw_wrong = result["token"]
        result2 = create_token(session, "W0NE", UserRole.ADMIN, "Right scope", ["schedule:read"], None)
        raw_right = result2["token"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # Wrong scope → 403
        resp = await c.get(
            "/api/schedule/sessions",
            headers={"Authorization": f"Bearer {raw_wrong}"},
        )
        assert resp.status_code == 403

        # Right scope → 200
        resp = await c.get(
            "/api/schedule/sessions",
            headers={"Authorization": f"Bearer {raw_right}"},
        )
        assert resp.status_code == 200
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `nix-shell --run "python -m pytest tests/test_pat_auth.py::test_schedule_endpoint_requires_scope -v"`
Expected: FAIL — the wrong-scope token still gets 200 (no scope check on the route)

- [ ] **Step 3: Add require_scope to schedule list endpoint**

In `backend/modules/schedule/routes.py`, add `require_scope` to the import:

```python
from backend.auth.dependencies import get_current_user, get_db_session, require_role, require_scope
```

Change the `list_sessions_route` (around line 212) to use `require_scope`:

```python
@schedule_router.get("/sessions")
async def list_sessions_route(
    season_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    user: User = Depends(require_scope("schedule:read")),
    db: Session = Depends(get_db_session),
):
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `nix-shell --run "python -m pytest tests/test_pat_auth.py::test_schedule_endpoint_requires_scope -v"`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `nix-shell --run "python -m pytest -v"`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/modules/schedule/routes.py tests/test_pat_auth.py
git commit -m "feat: wire require_scope to schedule list endpoint

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 7: Frontend Types + API Client

**Files:**
- Create: `frontend/src/api/tokens.ts`
- Modify: `frontend/src/types/index.ts`

- [ ] **Step 1: Add types to index.ts**

Add to the end of `frontend/src/types/index.ts`:

```typescript
export interface Token {
  id: number;
  name: string;
  token_prefix: string;
  scopes: string[];
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
  is_expired: boolean;
  is_revoked: boolean;
}

export interface TokenCreate {
  name: string;
  scopes: string[];
  expires_at?: string;
}

export interface TokenWithSecret extends Token {
  token: string;
}

export const SCOPES: Record<string, { description: string; minRole: UserRole }> = {
  "schedule:read":  { description: "View sessions",             minRole: "viewer" },
  "schedule:write": { description: "Create/edit/delete sessions", minRole: "net_control" },
  "checkins:read":  { description: "View check-in data",        minRole: "viewer" },
  "checkins:write": { description: "Submit/manage check-ins",   minRole: "net_control" },
  "roster:read":    { description: "View roster data",          minRole: "net_control" },
  "map:read":       { description: "View map/GeoJSON data",     minRole: "viewer" },
  "users:read":     { description: "List users",                minRole: "admin" },
  "users:write":    { description: "Manage users/roles",        minRole: "admin" },
  "config:read":    { description: "View app configuration",    minRole: "admin" },
  "config:write":   { description: "Modify app configuration",  minRole: "admin" },
};
```

- [ ] **Step 2: Create API client**

```typescript
// frontend/src/api/tokens.ts
import type { Token, TokenCreate, TokenWithSecret } from "../types";
import { apiFetch } from "./client";

export async function createToken(data: TokenCreate): Promise<TokenWithSecret> {
  return apiFetch<TokenWithSecret>("/auth/tokens", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function listTokens(): Promise<Token[]> {
  return apiFetch<Token[]>("/auth/tokens");
}

export async function revokeToken(id: number): Promise<void> {
  await apiFetch<void>(`/auth/tokens/${id}`, { method: "DELETE" });
}
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit"`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/api/tokens.ts
git commit -m "feat: add PAT types and API client functions

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 8: Frontend Token Manager on ProfilePage

**Files:**
- Modify: `frontend/src/pages/ProfilePage.tsx`

- [ ] **Step 1: Replace ProfilePage with token manager**

Replace the full contents of `frontend/src/pages/ProfilePage.tsx`:

```tsx
import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../hooks/useAuth";
import { useToast } from "../context/ToastContext";
import { updateCallsign } from "../api/auth";
import { createToken, listTokens, revokeToken } from "../api/tokens";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { Modal } from "../components/Modal";
import { Spinner } from "../components/Spinner";
import { ApiError, SCOPES } from "../types";
import type { Token, UserRole } from "../types";

const CALLSIGN_PATTERN = /^[A-Z]{1,2}\d[A-Z]{1,4}$/;

const ROLE_RANK: Record<UserRole, number> = {
  pending: 0,
  viewer: 1,
  net_control: 2,
  admin: 3,
};

function canUseScope(userRole: UserRole, minRole: UserRole): boolean {
  return ROLE_RANK[userRole] >= ROLE_RANK[minRole];
}

export function ProfilePage() {
  const { user, refreshUser } = useAuth();
  const { addToast } = useToast();

  // Callsign change state
  const [newCallsign, setNewCallsign] = useState("");
  const [callsignError, setCallsignError] = useState<string | null>(null);
  const [callsignLoading, setCallsignLoading] = useState(false);

  // Token state
  const [tokens, setTokens] = useState<Token[]>([]);
  const [tokensLoading, setTokensLoading] = useState(true);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [tokenName, setTokenName] = useState("");
  const [selectedScopes, setSelectedScopes] = useState<string[]>([]);
  const [tokenExpiry, setTokenExpiry] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [createLoading, setCreateLoading] = useState(false);
  const [revealedToken, setRevealedToken] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<Token | null>(null);
  const [revokeLoading, setRevokeLoading] = useState(false);

  const loadTokens = useCallback(async () => {
    try {
      const data = await listTokens();
      setTokens(data);
    } catch {
      addToast("Failed to load tokens", "error");
    } finally {
      setTokensLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    loadTokens();
  }, [loadTokens]);

  if (!user) return null;

  const handleCallsignChange = async (e: React.FormEvent) => {
    e.preventDefault();
    setCallsignError(null);
    const upper = newCallsign.toUpperCase();
    if (!CALLSIGN_PATTERN.test(upper)) {
      setCallsignError("Invalid callsign format (e.g., W0NE, KD0ABC)");
      return;
    }
    setCallsignLoading(true);
    try {
      await updateCallsign(upper);
      await refreshUser();
      setNewCallsign("");
      addToast("Callsign change request submitted", "success");
    } catch (err) {
      setCallsignError(err instanceof ApiError ? err.detail : "Request failed");
    } finally {
      setCallsignLoading(false);
    }
  };

  const handleCreateToken = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreateError(null);
    if (!tokenName.trim()) {
      setCreateError("Name is required");
      return;
    }
    if (selectedScopes.length === 0) {
      setCreateError("Select at least one scope");
      return;
    }
    setCreateLoading(true);
    try {
      const result = await createToken({
        name: tokenName.trim(),
        scopes: selectedScopes,
        expires_at: tokenExpiry || undefined,
      });
      setRevealedToken(result.token);
      setTokenName("");
      setSelectedScopes([]);
      setTokenExpiry("");
      setShowCreateForm(false);
      await loadTokens();
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.detail : "Failed to create token");
    } finally {
      setCreateLoading(false);
    }
  };

  const handleRevoke = async () => {
    if (!revokeTarget) return;
    setRevokeLoading(true);
    try {
      await revokeToken(revokeTarget.id);
      addToast("Token revoked", "success");
      setRevokeTarget(null);
      await loadTokens();
    } catch {
      addToast("Failed to revoke token", "error");
    } finally {
      setRevokeLoading(false);
    }
  };

  const toggleScope = (scope: string) => {
    setSelectedScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope],
    );
  };

  const copyToken = async () => {
    if (revealedToken) {
      await navigator.clipboard.writeText(revealedToken);
      addToast("Token copied to clipboard", "success");
    }
  };

  const roleBadgeClass =
    user.role === "admin"
      ? "bg-accent/10 text-accent border-accent/25"
      : user.role === "net_control"
        ? "bg-success/10 text-success border-success/25"
        : "bg-bg-elevated text-text-muted border-border";

  return (
    <div className="max-w-lg">
      <h1 className="text-xl font-bold text-text-primary mb-6">Profile</h1>

      {/* User info */}
      <div className="bg-bg-surface border border-border rounded-lg p-6 mb-6">
        <div className="font-mono text-2xl text-accent mb-1">{user.callsign}</div>
        <div className="text-text-secondary">{user.name}</div>
        {user.email && <div className="text-text-muted text-sm mt-1">{user.email}</div>}
        <span className={`inline-block mt-2 text-xs px-2 py-0.5 rounded border ${roleBadgeClass}`}>
          {user.role.replace(/_/g, " ")}
        </span>
      </div>

      {/* Callsign change */}
      <div className="bg-bg-surface border border-border rounded-lg p-6 mb-6">
        <h2 className="text-lg font-semibold text-text-primary mb-4">Change Callsign</h2>
        {user.pending_callsign ? (
          <div className="flex items-center gap-2 text-sm">
            <div className="h-2 w-2 rounded-full bg-warning animate-pulse" />
            <span className="text-text-muted">Pending approval:</span>
            <span className="font-mono text-warning">{user.pending_callsign}</span>
          </div>
        ) : (
          <form onSubmit={handleCallsignChange} className="flex gap-3">
            <div className="flex-1">
              <Input
                value={newCallsign}
                onChange={(e) => setNewCallsign(e.target.value.toUpperCase())}
                placeholder="W0NEW"
                error={callsignError || undefined}
                mono
              />
            </div>
            <Button type="submit" loading={callsignLoading} className="self-start">
              Request Change
            </Button>
          </form>
        )}
      </div>

      {/* Personal Access Tokens */}
      <div className="bg-bg-surface border border-border rounded-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-text-primary">Personal Access Tokens</h2>
          {!showCreateForm && !revealedToken && (
            <Button size="sm" onClick={() => setShowCreateForm(true)}>
              Create Token
            </Button>
          )}
        </div>

        {/* Token reveal */}
        {revealedToken && (
          <div className="mb-4 p-4 bg-warning/10 border border-warning/25 rounded-lg">
            <p className="text-sm text-warning font-semibold mb-2">
              Copy this token now. It will not be shown again.
            </p>
            <div className="flex items-center gap-2 mb-3">
              <code className="flex-1 font-mono text-xs bg-bg-base p-2 rounded border border-border break-all text-text-primary">
                {revealedToken}
              </code>
              <Button size="sm" onClick={copyToken}>
                Copy
              </Button>
            </div>
            <Button size="sm" variant="secondary" onClick={() => setRevealedToken(null)}>
              Done
            </Button>
          </div>
        )}

        {/* Create form */}
        {showCreateForm && (
          <form onSubmit={handleCreateToken} className="mb-4 p-4 bg-bg-base rounded-lg border border-border">
            <Input
              label="Token name"
              value={tokenName}
              onChange={(e) => setTokenName(e.target.value)}
              placeholder="OpenClaw integration"
              error={createError || undefined}
              autoFocus
            />
            <div className="mt-3">
              <label className="block text-sm text-text-secondary mb-2">Scopes</label>
              <div className="space-y-1">
                {Object.entries(SCOPES).map(([scope, { description, minRole }]) => {
                  const allowed = canUseScope(user.role as UserRole, minRole);
                  return (
                    <label
                      key={scope}
                      className={`flex items-center gap-2 text-sm ${allowed ? "text-text-secondary" : "text-text-muted opacity-50"}`}
                    >
                      <input
                        type="checkbox"
                        checked={selectedScopes.includes(scope)}
                        onChange={() => toggleScope(scope)}
                        disabled={!allowed}
                        className="accent-accent"
                      />
                      <span className="font-mono text-xs">{scope}</span>
                      <span className="text-text-muted">— {description}</span>
                    </label>
                  );
                })}
              </div>
            </div>
            <div className="mt-3">
              <Input
                label="Expiry (optional)"
                type="datetime-local"
                value={tokenExpiry}
                onChange={(e) => setTokenExpiry(e.target.value)}
              />
            </div>
            <div className="flex gap-2 mt-4">
              <Button type="submit" loading={createLoading}>
                Create
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  setShowCreateForm(false);
                  setCreateError(null);
                }}
              >
                Cancel
              </Button>
            </div>
          </form>
        )}

        {/* Token list */}
        {tokensLoading ? (
          <div className="flex justify-center py-4">
            <Spinner />
          </div>
        ) : tokens.length === 0 && !revealedToken ? (
          <p className="text-text-muted text-sm">No tokens created yet.</p>
        ) : (
          <div className="space-y-3">
            {tokens.map((t) => (
              <div
                key={t.id}
                className="flex items-start justify-between p-3 bg-bg-base rounded-lg border border-border"
              >
                <div className="min-w-0">
                  <div className="text-sm font-medium text-text-primary">{t.name}</div>
                  <div className="font-mono text-xs text-text-muted mt-0.5">{t.token_prefix}...</div>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {t.scopes.map((s) => (
                      <span
                        key={s}
                        className="text-xs px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/25"
                      >
                        {s}
                      </span>
                    ))}
                  </div>
                  <div className="text-xs text-text-muted mt-1">
                    Created {new Date(t.created_at).toLocaleDateString()}
                    {t.last_used_at && ` · Last used ${new Date(t.last_used_at).toLocaleDateString()}`}
                    {t.expires_at && (
                      <span className={t.is_expired ? "text-danger" : ""}>
                        {" · "}
                        {t.is_expired ? "Expired" : `Expires ${new Date(t.expires_at).toLocaleDateString()}`}
                      </span>
                    )}
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => setRevokeTarget(t)}
                >
                  Revoke
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Revoke confirmation modal */}
      <Modal
        open={revokeTarget !== null}
        onClose={() => setRevokeTarget(null)}
        title="Revoke Token"
      >
        <p className="text-text-secondary text-sm mb-4">
          Revoke token &ldquo;{revokeTarget?.name}&rdquo;? Any integrations using this token will stop working immediately.
        </p>
        <div className="flex gap-2 justify-end">
          <Button variant="secondary" onClick={() => setRevokeTarget(null)}>
            Cancel
          </Button>
          <Button variant="danger" loading={revokeLoading} onClick={handleRevoke}>
            Revoke
          </Button>
        </div>
      </Modal>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit"`
Expected: No errors.

- [ ] **Step 3: Verify the app builds**

Run: `nix-shell --run "cd frontend && npx vite build"`
Expected: Build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/ProfilePage.tsx
git commit -m "feat: replace PAT placeholder with full token manager UI

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 9: Full Test Suite Verification

- [ ] **Step 1: Run the complete backend test suite**

Run: `nix-shell --run "python -m pytest -v"`
Expected: All tests pass (existing + new PAT tests).

- [ ] **Step 2: Run the frontend build**

Run: `nix-shell --run "cd frontend && npx vite build"`
Expected: Build succeeds with no errors.

- [ ] **Step 3: Verify TypeScript**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit"`
Expected: No type errors.

- [ ] **Step 4: Final commit (if any fixes needed)**

If any adjustments were required, commit them:

```bash
git add -A
git commit -m "fix: address test/build issues in PAT implementation

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```
