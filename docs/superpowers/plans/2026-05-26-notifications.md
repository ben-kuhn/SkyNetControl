# In-App Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a lightweight in-app notification system so net control gets a bell-badge nudge when drafts are ready, check-ins arrive from a scan, or a delivery send fails.

**Architecture:** New `backend/modules/notifications/` module with a `notifications` table, a dedupe-aware service, and three API endpoints. Hook calls are added to existing service flows in reminders/check-ins/roster. Frontend gets a bell + dropdown component mounted in Sidebar and MobileMenu footers, polling every 60 seconds.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, React/TypeScript.

---

## File Structure

**New files:**

| File | Responsibility |
|------|---------------|
| `backend/modules/notifications/__init__.py` | Package marker |
| `backend/modules/notifications/models.py` | `Notification` model, `NotificationKind` enum |
| `backend/modules/notifications/service.py` | `create_notification`, `list_for_user`, `mark_read`, `mark_all_read`, `resolve_session_recipient`, `_format_session_date` |
| `backend/modules/notifications/routes.py` | Three endpoints (`GET /`, `POST /{id}/read`, `POST /read-all`) |
| `alembic/versions/<rev>_add_notifications_table.py` | Migration |
| `tests/test_notifications_service.py` | Service tests |
| `tests/test_notifications_routes.py` | Route tests |
| `frontend/src/api/notifications.ts` | API client |
| `frontend/src/components/NotificationBell.tsx` | Bell + dropdown |

**Modified files:**

| File | Change |
|------|--------|
| `backend/app.py` | Register `notifications_router` |
| `alembic/env.py` | Import `backend.modules.notifications.models` for autogen |
| `backend/modules/reminders/service.py` | Call `create_notification` in `generate_due_drafts` and `mark_sent` (failure) |
| `backend/modules/reminders/routes.py` | Call `create_notification` in `generate_draft_route` after success |
| `backend/modules/checkins/service.py` | Call `create_notification` at end of `scan_and_import_messages` when any checkin imported |
| `backend/modules/roster/service.py` | Replace `notify_ncs` calls with `create_notification` in `generate_due_drafts`; add to `mark_sent` failure path; remove the `notify_ncs` stub |
| `backend/modules/roster/routes.py` | Call `create_notification` in `generate_draft_for_session_route` after success |
| `frontend/src/types/index.ts` | Add `Notification`, `NotificationKind` types |
| `frontend/src/layouts/Sidebar.tsx` | Mount `<NotificationBell />` |
| `frontend/src/layouts/MobileMenu.tsx` | Mount `<NotificationBell />` |

---

### Task 1: Backend — Notification model + migration

**Files:**
- Create: `backend/modules/notifications/__init__.py` (empty)
- Create: `backend/modules/notifications/models.py`
- Create: `alembic/versions/<auto>_add_notifications_table.py`
- Modify: `alembic/env.py`
- Test: `tests/test_notifications_service.py` (sanity-check the model loads)

- [ ] **Step 1: Create the empty package marker**

Create `backend/modules/notifications/__init__.py` with no content.

- [ ] **Step 2: Create the model**

Create `backend/modules/notifications/models.py`:

```python
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class NotificationKind(str, enum.Enum):
    REMINDER_DRAFT = "reminder_draft"
    CHECKINS_READY = "checkins_ready"
    ROSTER_DRAFT = "roster_draft"
    DELIVERY_FAILURE = "delivery_failure"


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipient_callsign: Mapped[str] = mapped_column(
        String(20), ForeignKey("users.callsign"), nullable=False
    )
    kind: Mapped[NotificationKind] = mapped_column(Enum(NotificationKind), nullable=False)
    session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("net_sessions.id"), nullable=True
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    link_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_notifications_recipient_read", "recipient_callsign", "read_at"),
    )
```

- [ ] **Step 3: Register the model with Alembic autogen**

In `alembic/env.py`, find the existing block of `from backend.modules.<module>.models import ...` (or similar pattern of imports for table autogeneration). Add a line:

```python
from backend.modules.notifications import models as notifications_models  # noqa: F401
```

If the file imports models a different way (e.g., importing each model directly), follow that pattern. The goal is to ensure `Notification` is registered on `Base.metadata` before autogenerate runs.

- [ ] **Step 4: Generate the Alembic migration**

Run: `nix-shell --run "cd /home/ku0hn/dev/SkyNetControl && alembic revision --autogenerate -m 'add notifications table'" /home/ku0hn/dev/SkyNetControl/shell.nix`

Inspect the generated file. The `upgrade` should create a `notifications` table with the columns matching the model. The `downgrade` should drop it. Confirm `op.create_index("ix_notifications_recipient_read", ...)` is present.

If autogenerate produces noise (drops/renames of unrelated tables), manually edit the file to keep only the notifications-related operations.

- [ ] **Step 5: Write a smoke test for the model**

Create `tests/test_notifications_service.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
from backend.modules.notifications.models import Notification, NotificationKind


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_notification_model_loads(db):
    """The model can be imported and the table is created."""
    from backend.auth.models import User, UserRole
    user = User(callsign="W0NE", oidc_subject="x", name="X", role=UserRole.ADMIN)
    db.add(user)
    db.flush()

    from datetime import datetime, timezone
    n = Notification(
        recipient_callsign="W0NE",
        kind=NotificationKind.REMINDER_DRAFT,
        message="Test",
        link_url="/reminders",
        created_at=datetime.now(tz=timezone.utc),
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    assert n.id is not None
    assert n.read_at is None
```

- [ ] **Step 6: Run the test**

Run: `nix-shell --run "python -m pytest tests/test_notifications_service.py -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: PASS.

- [ ] **Step 7: Run the full backend suite to confirm no regressions**

Run: `nix-shell --run "python -m pytest tests/ -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass.

- [ ] **Step 8: Commit**

```bash
git add backend/modules/notifications/__init__.py backend/modules/notifications/models.py alembic/env.py alembic/versions/*notifications* tests/test_notifications_service.py
git commit -m "feat: add Notification model and migration"
```

---

### Task 2: Backend — notifications service

**Files:**
- Create: `backend/modules/notifications/service.py`
- Test: `tests/test_notifications_service.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_notifications_service.py`:

```python
def _seed_user(db, callsign="W0NE", role=None):
    from backend.auth.models import User, UserRole
    user = User(
        callsign=callsign,
        oidc_subject=f"sub|{callsign}",
        name=callsign,
        role=role or UserRole.NET_CONTROL,
    )
    db.add(user)
    db.flush()
    return user


def _seed_session(db, ncs="W0NE"):
    from datetime import date, time
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
    season = NetSeason(
        name="S", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        day_of_week=3, time=time(18, 0),
    )
    db.add(season)
    db.flush()
    sess = NetSession(
        season_id=season.id, start_date=date(2026, 5, 28),
        session_type=SessionType.REGULAR_CHECKIN, status=SessionStatus.SCHEDULED,
        net_control_callsign=ncs,
    )
    db.add(sess)
    db.commit()
    return sess


def test_create_notification_inserts_row(db):
    from backend.modules.notifications.service import create_notification
    from backend.modules.notifications.models import NotificationKind
    _seed_user(db)
    sess = _seed_session(db)

    n = create_notification(
        db, "W0NE", NotificationKind.REMINDER_DRAFT,
        message="Reminder draft ready", link_url="/reminders", session_id=sess.id,
    )
    assert n.id is not None
    assert n.recipient_callsign == "W0NE"
    assert n.read_at is None


def test_create_notification_dedupes_unread(db):
    from backend.modules.notifications.service import create_notification
    from backend.modules.notifications.models import Notification, NotificationKind
    _seed_user(db)
    sess = _seed_session(db)

    a = create_notification(
        db, "W0NE", NotificationKind.REMINDER_DRAFT,
        message="A", link_url="/reminders", session_id=sess.id,
    )
    b = create_notification(
        db, "W0NE", NotificationKind.REMINDER_DRAFT,
        message="B", link_url="/reminders", session_id=sess.id,
    )
    assert a.id == b.id
    assert db.query(Notification).count() == 1


def test_create_notification_no_dedupe_after_read(db):
    from datetime import datetime, timezone
    from backend.modules.notifications.service import create_notification
    from backend.modules.notifications.models import Notification, NotificationKind
    _seed_user(db)
    sess = _seed_session(db)

    a = create_notification(
        db, "W0NE", NotificationKind.REMINDER_DRAFT,
        message="A", session_id=sess.id,
    )
    a.read_at = datetime.now(tz=timezone.utc)
    db.commit()

    b = create_notification(
        db, "W0NE", NotificationKind.REMINDER_DRAFT,
        message="B", session_id=sess.id,
    )
    assert b.id != a.id
    assert db.query(Notification).count() == 2


def test_create_notification_dedupe_off_always_inserts(db):
    from backend.modules.notifications.service import create_notification
    from backend.modules.notifications.models import Notification, NotificationKind
    _seed_user(db)
    sess = _seed_session(db)

    create_notification(
        db, "W0NE", NotificationKind.DELIVERY_FAILURE,
        message="X", session_id=sess.id, dedupe=False,
    )
    create_notification(
        db, "W0NE", NotificationKind.DELIVERY_FAILURE,
        message="Y", session_id=sess.id, dedupe=False,
    )
    assert db.query(Notification).count() == 2


def test_list_for_user_unread_only_by_default(db):
    from datetime import datetime, timezone
    from backend.modules.notifications.service import create_notification, list_for_user
    from backend.modules.notifications.models import NotificationKind
    _seed_user(db)
    sess = _seed_session(db)

    a = create_notification(db, "W0NE", NotificationKind.REMINDER_DRAFT, message="A", session_id=sess.id)
    b = create_notification(db, "W0NE", NotificationKind.ROSTER_DRAFT, message="B", session_id=sess.id)
    a.read_at = datetime.now(tz=timezone.utc)
    db.commit()

    unread = list_for_user(db, "W0NE")
    assert [n.id for n in unread] == [b.id]

    all_ = list_for_user(db, "W0NE", include_read=True)
    assert {n.id for n in all_} == {a.id, b.id}


def test_mark_read_owned(db):
    from backend.modules.notifications.service import create_notification, mark_read
    from backend.modules.notifications.models import NotificationKind
    _seed_user(db)
    sess = _seed_session(db)

    n = create_notification(db, "W0NE", NotificationKind.REMINDER_DRAFT, message="A", session_id=sess.id)
    updated = mark_read(db, n.id, "W0NE")
    assert updated is not None
    assert updated.read_at is not None


def test_mark_read_not_owned_returns_none(db):
    from backend.modules.notifications.service import create_notification, mark_read
    from backend.modules.notifications.models import NotificationKind
    _seed_user(db, callsign="W0NE")
    _seed_user(db, callsign="KD0OTH")
    sess = _seed_session(db)

    n = create_notification(db, "W0NE", NotificationKind.REMINDER_DRAFT, message="A", session_id=sess.id)
    assert mark_read(db, n.id, "KD0OTH") is None


def test_mark_all_read_returns_count(db):
    from backend.modules.notifications.service import create_notification, mark_all_read
    from backend.modules.notifications.models import NotificationKind
    _seed_user(db)
    sess = _seed_session(db)

    create_notification(db, "W0NE", NotificationKind.REMINDER_DRAFT, message="A", session_id=sess.id)
    create_notification(db, "W0NE", NotificationKind.ROSTER_DRAFT, message="B", session_id=sess.id)
    count = mark_all_read(db, "W0NE")
    assert count == 2


def test_resolve_session_recipient_prefers_ncs(db):
    from backend.modules.notifications.service import resolve_session_recipient
    _seed_user(db, callsign="W0NE")
    _seed_user(db, callsign="W0ADM")
    sess = _seed_session(db, ncs="W0NE")
    assert resolve_session_recipient(db, sess) == "W0NE"


def test_resolve_session_recipient_falls_back_to_admin(db):
    from backend.auth.models import UserRole
    from backend.modules.notifications.service import resolve_session_recipient
    _seed_user(db, callsign="W0ADM", role=UserRole.ADMIN)
    # Create session with no NCS
    from datetime import date, time
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
    season = NetSeason(
        name="S", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        day_of_week=3, time=time(18, 0),
    )
    db.add(season); db.flush()
    sess = NetSession(
        season_id=season.id, start_date=date(2026, 5, 28),
        session_type=SessionType.REGULAR_CHECKIN, status=SessionStatus.SCHEDULED,
        net_control_callsign=None,
    )
    db.add(sess); db.commit()

    assert resolve_session_recipient(db, sess) == "W0ADM"


def test_resolve_session_recipient_returns_none_when_no_one(db):
    from backend.modules.notifications.service import resolve_session_recipient
    from datetime import date, time
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus
    season = NetSeason(
        name="S", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        day_of_week=3, time=time(18, 0),
    )
    db.add(season); db.flush()
    sess = NetSession(
        season_id=season.id, start_date=date(2026, 5, 28),
        session_type=SessionType.REGULAR_CHECKIN, status=SessionStatus.SCHEDULED,
        net_control_callsign=None,
    )
    db.add(sess); db.commit()
    assert resolve_session_recipient(db, sess) is None
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_notifications_service.py -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL with `ImportError: cannot import name 'create_notification'` (or similar for each function).

- [ ] **Step 3: Implement the service**

Create `backend/modules/notifications/service.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.auth.models import User, UserRole
from backend.modules.notifications.models import Notification, NotificationKind
from backend.modules.schedule.models import NetSession


def create_notification(
    db: Session,
    recipient_callsign: str,
    kind: NotificationKind,
    message: str,
    link_url: str | None = None,
    session_id: int | None = None,
    dedupe: bool = True,
) -> Notification:
    """Insert a notification. With dedupe=True, return an existing unread row with the same
    (recipient, kind, session_id) instead of creating a duplicate."""
    if dedupe:
        existing = (
            db.query(Notification)
            .filter(
                Notification.recipient_callsign == recipient_callsign,
                Notification.kind == kind,
                Notification.session_id == session_id,
                Notification.read_at.is_(None),
            )
            .first()
        )
        if existing is not None:
            return existing

    n = Notification(
        recipient_callsign=recipient_callsign,
        kind=kind,
        message=message,
        link_url=link_url,
        session_id=session_id,
        created_at=datetime.now(tz=timezone.utc),
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def list_for_user(
    db: Session, callsign: str, include_read: bool = False,
) -> list[Notification]:
    query = db.query(Notification).filter(Notification.recipient_callsign == callsign)
    if not include_read:
        query = query.filter(Notification.read_at.is_(None))
    return query.order_by(Notification.created_at.desc()).all()


def mark_read(
    db: Session, notification_id: int, callsign: str,
) -> Notification | None:
    n = db.get(Notification, notification_id)
    if n is None or n.recipient_callsign != callsign:
        return None
    if n.read_at is None:
        n.read_at = datetime.now(tz=timezone.utc)
        db.commit()
        db.refresh(n)
    return n


def mark_all_read(db: Session, callsign: str) -> int:
    now = datetime.now(tz=timezone.utc)
    result = (
        db.query(Notification)
        .filter(
            Notification.recipient_callsign == callsign,
            Notification.read_at.is_(None),
        )
        .update({Notification.read_at: now})
    )
    db.commit()
    return result


def resolve_session_recipient(db: Session, net_session: NetSession) -> str | None:
    """Return the session's net_control_callsign, or fall back to the lowest-id admin, or None."""
    if net_session.net_control_callsign:
        return net_session.net_control_callsign

    admin = (
        db.query(User)
        .filter(User.role == UserRole.ADMIN)
        .order_by(User.callsign)
        .first()
    )
    return admin.callsign if admin else None


def _format_session_date(net_session: NetSession) -> str:
    """Short, friendly date — 'May 28' when in the current year, else 'May 28, 2026'."""
    d = net_session.start_date
    today = datetime.now(tz=timezone.utc).date()
    if d.year == today.year:
        return d.strftime("%b %-d")
    return d.strftime("%b %-d, %Y")
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_notifications_service.py -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/modules/notifications/service.py tests/test_notifications_service.py
git commit -m "feat: add notifications service"
```

---

### Task 3: Backend — notifications routes

**Files:**
- Create: `backend/modules/notifications/routes.py`
- Modify: `backend/app.py`
- Test: `tests/test_notifications_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_notifications_routes.py`:

```python
import pytest
from datetime import date, datetime, time, timezone
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.auth.models import User, UserRole
from backend.auth.service import create_access_token
from backend.modules.notifications.routes import notifications_router
from backend.modules.notifications.models import Notification, NotificationKind
from backend.config import Settings


@pytest.fixture
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_expire_minutes=60,
    )


@pytest.fixture
def db_setup():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        admin = User(callsign="W0NE", oidc_subject="x|a", name="Admin", role=UserRole.ADMIN)
        viewer = User(callsign="KD0TST", oidc_subject="x|v", name="Viewer", role=UserRole.VIEWER)
        session.add_all([admin, viewer])
        session.commit()
    return factory


@pytest.fixture
def test_app(test_settings, db_setup):
    app = FastAPI()
    app.state.session_factory = db_setup
    app.state.settings = test_settings
    app.include_router(notifications_router, prefix="/api/notifications")
    return app


@pytest.fixture
async def test_client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _seed(db_setup, callsign, kind=NotificationKind.REMINDER_DRAFT, read=False):
    with db_setup() as session:
        n = Notification(
            recipient_callsign=callsign,
            kind=kind,
            message="Test",
            link_url="/reminders",
            created_at=datetime.now(tz=timezone.utc),
            read_at=datetime.now(tz=timezone.utc) if read else None,
        )
        session.add(n)
        session.commit()
        return n.id


@pytest.mark.asyncio
async def test_list_returns_unread_only_by_default(test_client, test_settings, db_setup):
    _seed(db_setup, "W0NE")
    _seed(db_setup, "W0NE", read=True)

    token = create_access_token("W0NE", "admin", test_settings)
    resp = await test_client.get(
        "/api/notifications/",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["read_at"] is None


@pytest.mark.asyncio
async def test_list_with_all_includes_read(test_client, test_settings, db_setup):
    _seed(db_setup, "W0NE")
    _seed(db_setup, "W0NE", read=True)

    token = create_access_token("W0NE", "admin", test_settings)
    resp = await test_client.get(
        "/api/notifications/?all=1",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_list_only_returns_users_own(test_client, test_settings, db_setup):
    _seed(db_setup, "W0NE")
    _seed(db_setup, "KD0TST")

    token = create_access_token("W0NE", "admin", test_settings)
    resp = await test_client.get(
        "/api/notifications/",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1


@pytest.mark.asyncio
async def test_mark_one_read(test_client, test_settings, db_setup):
    nid = _seed(db_setup, "W0NE")

    token = create_access_token("W0NE", "admin", test_settings)
    resp = await test_client.post(
        f"/api/notifications/{nid}/read",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert resp.json()["read_at"] is not None


@pytest.mark.asyncio
async def test_mark_one_read_not_owned_returns_404(test_client, test_settings, db_setup):
    nid = _seed(db_setup, "KD0TST")

    token = create_access_token("W0NE", "admin", test_settings)
    resp = await test_client.post(
        f"/api/notifications/{nid}/read",
        cookies={"access_token": token},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mark_all_read(test_client, test_settings, db_setup):
    _seed(db_setup, "W0NE")
    _seed(db_setup, "W0NE")
    _seed(db_setup, "KD0TST")  # should not be affected

    token = create_access_token("W0NE", "admin", test_settings)
    resp = await test_client.post(
        "/api/notifications/read-all",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert resp.json() == {"count": 2}


@pytest.mark.asyncio
async def test_list_requires_auth(test_client):
    resp = await test_client.get("/api/notifications/")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_notifications_routes.py -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL with `ImportError: cannot import name 'notifications_router'`.

- [ ] **Step 3: Implement the routes**

Create `backend/modules/notifications/routes.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session
from backend.auth.models import User
from backend.modules.notifications.models import Notification
from backend.modules.notifications.service import (
    list_for_user,
    mark_all_read,
    mark_read,
)

notifications_router = APIRouter()


def _to_response(n: Notification) -> dict:
    return {
        "id": n.id,
        "kind": n.kind.value,
        "session_id": n.session_id,
        "message": n.message,
        "link_url": n.link_url,
        "created_at": n.created_at.isoformat(),
        "read_at": n.read_at.isoformat() if n.read_at else None,
    }


@notifications_router.get("/")
async def list_notifications_route(
    all: int = Query(default=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    rows = list_for_user(db, user.callsign, include_read=bool(all))
    return [_to_response(n) for n in rows]


@notifications_router.post("/{notification_id}/read")
async def mark_read_route(
    notification_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    n = mark_read(db, notification_id, user.callsign)
    if n is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return _to_response(n)


@notifications_router.post("/read-all")
async def mark_all_read_route(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    count = mark_all_read(db, user.callsign)
    return {"count": count}
```

- [ ] **Step 4: Register the router in app.py**

In `backend/app.py`, find the existing router includes (e.g., `app.include_router(roster_router, prefix="/api/roster")`). Add:

```python
from backend.modules.notifications.routes import notifications_router
```
to the imports near the other router imports, and add:
```python
app.include_router(notifications_router, prefix="/api/notifications")
```
alongside the other `include_router` calls in `create_app`.

- [ ] **Step 5: Run tests and verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_notifications_routes.py -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass.

- [ ] **Step 6: Run the full backend suite**

Run: `nix-shell --run "python -m pytest tests/ -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add backend/modules/notifications/routes.py backend/app.py tests/test_notifications_routes.py
git commit -m "feat: add notifications API routes"
```

---

### Task 4: Backend — hook into reminders flow

**Files:**
- Modify: `backend/modules/reminders/service.py`
- Modify: `backend/modules/reminders/routes.py`
- Test: `tests/test_reminder_service.py` (add notification assertion)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_reminder_service.py`:

```python
def test_generate_due_drafts_creates_notification(db, season_and_sessions):
    """When the daily task generates a draft, the session's NCS gets a notification."""
    from datetime import date
    from unittest.mock import patch
    from backend.auth.models import User, UserRole
    from backend.modules.notifications.models import Notification, NotificationKind

    _, session1, _, _ = season_and_sessions
    db.add(User(callsign="W0NE", oidc_subject="x|w0ne", name="NCS", role=UserRole.NET_CONTROL))
    db.commit()

    create_template(
        db, name="Regular Default", template_type=TemplateType.REGULAR_CHECKIN,
        subject_template="Net {{ date }}", body_template="Body",
        lead_time_days=3, is_default=True,
    )

    # Force _today() such that lead-time is met for session1 (2026-04-10)
    with patch("backend.modules.reminders.service._today", return_value=date(2026, 4, 8)):
        generate_due_drafts(db)

    rows = (
        db.query(Notification)
        .filter(
            Notification.recipient_callsign == "W0NE",
            Notification.kind == NotificationKind.REMINDER_DRAFT,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].link_url == "/reminders"
    assert "Apr" in rows[0].message  # short date format
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `nix-shell --run "python -m pytest tests/test_reminder_service.py::test_generate_due_drafts_creates_notification -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL — no Notification rows because the hook isn't wired.

- [ ] **Step 3: Update `generate_due_drafts` in reminders service**

In `backend/modules/reminders/service.py`, find the existing `generate_due_drafts` function. Add imports near the top of the file (after other backend imports):

```python
from backend.modules.notifications.models import NotificationKind
from backend.modules.notifications.service import (
    _format_session_date,
    create_notification,
    resolve_session_recipient,
)
```

Find the line inside the loop where `generate_draft` is called and the resulting `log` is appended. After successfully generating a draft, add:

```python
            recipient = resolve_session_recipient(db, session)
            if recipient is not None:
                create_notification(
                    db,
                    recipient_callsign=recipient,
                    kind=NotificationKind.REMINDER_DRAFT,
                    message=f"Reminder draft ready for {_format_session_date(session)}",
                    link_url="/reminders",
                    session_id=session.id,
                )
```

Place this immediately after the `log = generate_draft(...)` and the `if log is not None:` block that appends to `drafts`. So the block becomes:

```python
            log = generate_draft(db, session.id, template_id=default_template.id)
            if log is not None:
                drafts.append(log)
                recipient = resolve_session_recipient(db, session)
                if recipient is not None:
                    create_notification(
                        db,
                        recipient_callsign=recipient,
                        kind=NotificationKind.REMINDER_DRAFT,
                        message=f"Reminder draft ready for {_format_session_date(session)}",
                        link_url="/reminders",
                        session_id=session.id,
                    )
```

Read the existing function body first to confirm the exact location.

- [ ] **Step 4: Wire the manual-generate route**

In `backend/modules/reminders/routes.py`, find `generate_draft_route` (the handler for `POST /generate/{session_id}`). After the successful service call, add a notification. The relevant pattern:

```python
@reminders_router.post("/generate/{session_id}")
async def generate_draft_route(
    session_id: int,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = generate_draft(db, session_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Session not found or no default template")
    return _reminder_to_response(log)
```

Change to:

```python
@reminders_router.post("/generate/{session_id}")
async def generate_draft_route(
    session_id: int,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    log = generate_draft(db, session_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Session not found or no default template")

    from backend.modules.notifications.models import NotificationKind
    from backend.modules.notifications.service import (
        _format_session_date,
        create_notification,
        resolve_session_recipient,
    )
    from backend.modules.schedule.models import NetSession
    net_session = db.get(NetSession, session_id)
    if net_session is not None:
        recipient = resolve_session_recipient(db, net_session)
        if recipient is not None:
            create_notification(
                db,
                recipient_callsign=recipient,
                kind=NotificationKind.REMINDER_DRAFT,
                message=f"Reminder draft ready for {_format_session_date(net_session)}",
                link_url="/reminders",
                session_id=net_session.id,
            )

    return _reminder_to_response(log)
```

Local imports keep this from polluting the module namespace; they execute every call but the cost is negligible.

- [ ] **Step 5: Run the new test and the reminder suite**

Run: `nix-shell --run "python -m pytest tests/test_reminder_service.py tests/test_reminder_routes.py -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass, including the new test.

- [ ] **Step 6: Run the full backend suite**

Run: `nix-shell --run "python -m pytest tests/ -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add backend/modules/reminders/service.py backend/modules/reminders/routes.py tests/test_reminder_service.py
git commit -m "feat: notify NCS when reminder drafts are generated"
```

---

### Task 5: Backend — hook into check-ins scan

**Files:**
- Modify: `backend/modules/checkins/service.py`
- Test: `tests/test_checkin_service.py` (add notification assertion)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_checkin_service.py`:

```python
def test_scan_creates_notification_when_checkins_imported(db, season_and_session):
    """After importing at least one check-in, the session's NCS gets a notification."""
    from datetime import datetime, timezone
    from backend.auth.models import User, UserRole
    from backend.modules.notifications.models import Notification, NotificationKind

    season, session = season_and_session
    session.net_control_callsign = "W0NE"
    db.add(User(callsign="W0NE", oidc_subject="x|w0ne", name="NCS", role=UserRole.NET_CONTROL))
    db.commit()

    raw_messages = [{
        "message_id": "MSG-NOTIFY-1",
        "from_address": "ka0xyz@winlink.org",
        "received_at": datetime.now(tz=timezone.utc),
        "subject": "Check-in",
        "body": "John Doe KA0XYZ Denver CO Winlink",
    }]

    from backend.modules.checkins.service import scan_and_import_messages
    imported = scan_and_import_messages(db, raw_messages, session)
    assert len(imported) >= 1

    rows = (
        db.query(Notification)
        .filter(
            Notification.recipient_callsign == "W0NE",
            Notification.kind == NotificationKind.CHECKINS_READY,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].link_url == f"/checkins?session={session.id}"


def test_scan_creates_no_notification_when_no_imports(db, season_and_session):
    """No new check-ins → no notification."""
    from backend.modules.notifications.models import Notification

    season, session = season_and_session
    from backend.modules.checkins.service import scan_and_import_messages
    result = scan_and_import_messages(db, [], session)
    assert result == []
    assert db.query(Notification).count() == 0
```

If the existing fixture in `tests/test_checkin_service.py` is named `season_and_session` (singular), use that. Otherwise adapt to whatever fixture builds one session — look for the existing test patterns.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_checkin_service.py::test_scan_creates_notification_when_checkins_imported tests/test_checkin_service.py::test_scan_creates_no_notification_when_no_imports -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL — no notification created.

- [ ] **Step 3: Update `scan_and_import_messages`**

In `backend/modules/checkins/service.py`, find the existing `scan_and_import_messages` function. At the very end, after the return-statement's value is computed but before the function returns, add the notification logic. The simplest pattern:

```python
def scan_and_import_messages(
    db: Session,
    raw_messages: list[dict],
    net_session: NetSession,
) -> list[CheckIn]:
    """Import raw message dicts, deduplicate by callsign (keep latest), skip existing."""
    # ...existing implementation...
    # (returns parsed_checkins.values() as a list, or [] if no new messages)

    result = list(parsed_checkins.values())

    if result:
        from backend.modules.notifications.models import NotificationKind
        from backend.modules.notifications.service import (
            _format_session_date,
            create_notification,
            resolve_session_recipient,
        )
        recipient = resolve_session_recipient(db, net_session)
        if recipient is not None:
            n = len(result)
            create_notification(
                db,
                recipient_callsign=recipient,
                kind=NotificationKind.CHECKINS_READY,
                message=f"{n} check-in(s) imported for {_format_session_date(net_session)}",
                link_url=f"/checkins?session={net_session.id}",
                session_id=net_session.id,
            )

    return result
```

Read the existing function body and adapt — the function currently returns either `[]` early (when `new_messages` is empty) or a list at the end. Make sure the notification fires only when `result` is non-empty, and the early return path stays as-is.

- [ ] **Step 4: Run the new tests**

Run: `nix-shell --run "python -m pytest tests/test_checkin_service.py -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass.

- [ ] **Step 5: Run the full backend suite**

Run: `nix-shell --run "python -m pytest tests/ -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add backend/modules/checkins/service.py tests/test_checkin_service.py
git commit -m "feat: notify NCS when mailbox scan imports check-ins"
```

---

### Task 6: Backend — hook into roster flow, remove `notify_ncs` stub

**Files:**
- Modify: `backend/modules/roster/service.py`
- Modify: `backend/modules/roster/routes.py`
- Test: `tests/test_roster_service.py` (add notification assertion)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_roster_service.py`:

```python
def test_generate_due_drafts_creates_notification(db, season_and_sessions, default_template):
    """When generate_due_drafts creates a roster, the session's NCS gets a notification."""
    from datetime import date
    from unittest.mock import patch
    from backend.auth.models import User, UserRole
    from backend.modules.notifications.models import Notification, NotificationKind
    from backend.modules.roster.service import generate_due_drafts

    _, session1, _, _ = season_and_sessions
    db.add(User(callsign="W0NE", oidc_subject="x|w0ne", name="NCS", role=UserRole.NET_CONTROL))
    db.commit()

    with patch("backend.modules.roster.service._today", return_value=date(2026, 4, 9)):
        generate_due_drafts(db)

    rows = (
        db.query(Notification)
        .filter(
            Notification.recipient_callsign == "W0NE",
            Notification.kind == NotificationKind.ROSTER_DRAFT,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].link_url == "/roster"
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `nix-shell --run "python -m pytest tests/test_roster_service.py::test_generate_due_drafts_creates_notification -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL — no notification created (only the no-op `notify_ncs` is called).

- [ ] **Step 3: Replace the `notify_ncs` call and remove the stub**

In `backend/modules/roster/service.py`:

Add imports near the top (after other backend imports):

```python
from backend.modules.notifications.models import NotificationKind
from backend.modules.notifications.service import (
    _format_session_date,
    create_notification,
    resolve_session_recipient,
)
```

Find the existing `notify_ncs(db, session)` call inside `generate_due_drafts` (around line 339). Replace it with:

```python
                recipient = resolve_session_recipient(db, session)
                if recipient is not None:
                    create_notification(
                        db,
                        recipient_callsign=recipient,
                        kind=NotificationKind.ROSTER_DRAFT,
                        message=f"Roster draft ready for {_format_session_date(session)}",
                        link_url="/roster",
                        session_id=session.id,
                    )
```

Then delete the `notify_ncs` function definition (around line 516, the stub `def notify_ncs(db, net_session) -> None: pass` block). Also remove any imports of it.

If `notify_ncs` is referenced from outside the module (search with `grep -rn "notify_ncs"`), update or remove those references too.

- [ ] **Step 4: Wire the manual-generate route**

In `backend/modules/roster/routes.py`, find the `POST /generate/{session_id}` handler (likely `generate_draft_for_session_route` or similar). After the successful service call, add a notification, same pattern as the reminder route:

```python
    from backend.modules.notifications.models import NotificationKind
    from backend.modules.notifications.service import (
        _format_session_date,
        create_notification,
        resolve_session_recipient,
    )
    from backend.modules.schedule.models import NetSession
    net_session = db.get(NetSession, session_id)
    if net_session is not None:
        recipient = resolve_session_recipient(db, net_session)
        if recipient is not None:
            create_notification(
                db,
                recipient_callsign=recipient,
                kind=NotificationKind.ROSTER_DRAFT,
                message=f"Roster draft ready for {_format_session_date(net_session)}",
                link_url="/roster",
                session_id=net_session.id,
            )
```

Place this immediately before the `return` statement of the route handler.

- [ ] **Step 5: Run the new test and the roster suite**

Run: `nix-shell --run "python -m pytest tests/test_roster_service.py tests/test_roster_routes.py -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass. If anything fails because it referenced `notify_ncs`, adjust.

- [ ] **Step 6: Run the full backend suite**

Run: `nix-shell --run "python -m pytest tests/ -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add backend/modules/roster/service.py backend/modules/roster/routes.py tests/test_roster_service.py
git commit -m "feat: notify NCS when roster drafts are generated"
```

---

### Task 7: Backend — hook delivery-failure notifications

**Files:**
- Modify: `backend/modules/reminders/service.py`
- Modify: `backend/modules/roster/service.py`
- Test: `tests/test_delivery_wiring.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_delivery_wiring.py` (the file added when we wired delivery; if it doesn't exist, create equivalent tests in `tests/test_reminder_service.py` and `tests/test_roster_service.py`):

```python
def test_reminder_send_failure_creates_delivery_failure_notification(db):
    from datetime import date, datetime, time, timezone
    from unittest.mock import patch
    from backend.auth.models import User, UserRole
    from backend.modules.reminders.service import mark_sent as reminder_mark_sent
    from backend.modules.reminders.models import ReminderLog, ReminderStatus
    from backend.modules.notifications.models import Notification, NotificationKind
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus

    season = NetSeason(name="S", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
                       day_of_week=3, time=time(18, 0))
    db.add(season); db.flush()
    sess = NetSession(season_id=season.id, start_date=date(2026, 5, 28),
                      session_type=SessionType.REGULAR_CHECKIN, status=SessionStatus.SCHEDULED,
                      net_control_callsign="W0NE")
    db.add(sess)
    db.add(User(callsign="W0NE", oidc_subject="x", name="N", role=UserRole.NET_CONTROL))
    db.flush()

    log = ReminderLog(
        session_id=sess.id, status=ReminderStatus.APPROVED,
        content_subject="S", content_body="B",
        drafted_at=datetime.now(tz=timezone.utc),
        approved_at=datetime.now(tz=timezone.utc), approved_by="W0NE",
    )
    db.add(log); db.commit()

    with patch(
        "backend.integrations.delivery.service.dispatch_delivery", return_value=False,
    ):
        result = reminder_mark_sent(db, log.id)

    assert result is None
    rows = (
        db.query(Notification)
        .filter(Notification.kind == NotificationKind.DELIVERY_FAILURE)
        .all()
    )
    assert len(rows) == 1
    assert "verify delivery backends" in rows[0].message.lower()
    assert rows[0].link_url == "/config"


def test_roster_send_failure_creates_delivery_failure_notification(db):
    from datetime import date, datetime, time, timezone
    from unittest.mock import patch
    from backend.auth.models import User, UserRole
    from backend.modules.roster.service import mark_sent as roster_mark_sent
    from backend.modules.roster.models import RosterLog, RosterStatus
    from backend.modules.notifications.models import Notification, NotificationKind
    from backend.modules.schedule.models import NetSeason, NetSession, SessionType, SessionStatus

    season = NetSeason(name="S", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
                       day_of_week=3, time=time(18, 0))
    db.add(season); db.flush()
    sess = NetSession(season_id=season.id, start_date=date(2026, 5, 28),
                      session_type=SessionType.REGULAR_CHECKIN, status=SessionStatus.SCHEDULED,
                      net_control_callsign="W0NE")
    db.add(sess)
    db.add(User(callsign="W0NE", oidc_subject="x", name="N", role=UserRole.NET_CONTROL))
    db.flush()

    log = RosterLog(
        session_id=sess.id, status=RosterStatus.APPROVED,
        content_subject="S", content_header="H", content_welcome="W",
        content_comments="C", content_footer="F",
        drafted_at=datetime.now(tz=timezone.utc),
        approved_at=datetime.now(tz=timezone.utc), approved_by="W0NE",
    )
    db.add(log); db.commit()

    with patch(
        "backend.integrations.delivery.service.dispatch_delivery", return_value=False,
    ):
        result = roster_mark_sent(db, log.id)

    assert result is None
    rows = (
        db.query(Notification)
        .filter(Notification.kind == NotificationKind.DELIVERY_FAILURE)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].link_url == "/config"
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_delivery_wiring.py -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL — no notification rows.

- [ ] **Step 3: Hook the reminder failure path**

In `backend/modules/reminders/service.py`, find `mark_sent`. The current body (after the delivery-wiring change) returns `None` when `dispatch_delivery` returns False. Add a notification before returning:

```python
def mark_sent(db: Session, reminder_id: int) -> ReminderLog | None:
    """Transition an APPROVED reminder to SENT via delivery backends."""
    log = db.get(ReminderLog, reminder_id)
    if log is None or log.status != ReminderStatus.APPROVED:
        return None

    from backend.integrations.delivery.service import dispatch_delivery

    delivered = dispatch_delivery(
        db, "reminder", log.id, log.content_subject, log.content_body
    )
    if not delivered:
        # Notify the session's NCS that the send failed
        net_session = db.get(NetSession, log.session_id)
        if net_session is not None:
            recipient = resolve_session_recipient(db, net_session)
            if recipient is not None:
                create_notification(
                    db,
                    recipient_callsign=recipient,
                    kind=NotificationKind.DELIVERY_FAILURE,
                    message=f"Send failed for reminder on {_format_session_date(net_session)} — verify delivery backends",
                    link_url="/config",
                    session_id=net_session.id,
                    dedupe=False,
                )
        return None

    log.status = ReminderStatus.SENT
    log.sent_at = datetime.now(tz=timezone.utc)
    db.commit()
    db.refresh(log)
    return log
```

`NetSession`, `NotificationKind`, `create_notification`, `resolve_session_recipient`, `_format_session_date` should already be imported at the top of the file from Task 4. Verify before adding.

- [ ] **Step 4: Hook the roster failure path**

In `backend/modules/roster/service.py`, find `mark_sent`. Apply the same pattern, replacing the `reminder` string with `roster`:

```python
def mark_sent(db: Session, roster_id: int) -> RosterLog | None:
    """Transition APPROVED → SENT via delivery backends."""
    log = db.get(RosterLog, roster_id)
    if log is None or log.status != RosterStatus.APPROVED:
        return None

    from backend.integrations.delivery.service import dispatch_delivery

    assembled = assemble_roster(db, roster_id)
    body = assembled if assembled else ""

    delivered = dispatch_delivery(db, "roster", log.id, log.content_subject, body)
    if not delivered:
        net_session = db.get(NetSession, log.session_id)
        if net_session is not None:
            recipient = resolve_session_recipient(db, net_session)
            if recipient is not None:
                create_notification(
                    db,
                    recipient_callsign=recipient,
                    kind=NotificationKind.DELIVERY_FAILURE,
                    message=f"Send failed for roster on {_format_session_date(net_session)} — verify delivery backends",
                    link_url="/config",
                    session_id=net_session.id,
                    dedupe=False,
                )
        return None

    log.status = RosterStatus.SENT
    log.sent_at = datetime.now(tz=timezone.utc)
    db.commit()
    db.refresh(log)
    return log
```

Imports should already be in scope from Task 6. Verify.

- [ ] **Step 5: Run the new tests and the delivery suite**

Run: `nix-shell --run "python -m pytest tests/test_delivery_wiring.py -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass.

- [ ] **Step 6: Run the full backend suite**

Run: `nix-shell --run "python -m pytest tests/ -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add backend/modules/reminders/service.py backend/modules/roster/service.py tests/test_delivery_wiring.py
git commit -m "feat: notify NCS on delivery failure"
```

---

### Task 8: Frontend — types

**Files:**
- Modify: `frontend/src/types/index.ts`

- [ ] **Step 1: Append the types**

Append to `frontend/src/types/index.ts`:

```typescript
export type NotificationKind =
  | "reminder_draft"
  | "checkins_ready"
  | "roster_draft"
  | "delivery_failure";

export interface Notification {
  id: number;
  kind: NotificationKind;
  session_id: number | null;
  message: string;
  link_url: string | null;
  created_at: string;
  read_at: string | null;
}
```

- [ ] **Step 2: Verify tsc compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/index.ts
git commit -m "feat: add Notification and NotificationKind types"
```

---

### Task 9: Frontend — API client

**Files:**
- Create: `frontend/src/api/notifications.ts`

- [ ] **Step 1: Create the client**

Create `frontend/src/api/notifications.ts`:

```typescript
import { apiFetch } from "./client";
import type { Notification } from "../types";

export async function fetchNotifications(includeRead = false): Promise<Notification[]> {
  const qs = includeRead ? "?all=1" : "";
  return apiFetch<Notification[]>(`/notifications/${qs}`);
}

export async function markNotificationRead(id: number): Promise<Notification> {
  return apiFetch<Notification>(`/notifications/${id}/read`, { method: "POST" });
}

export async function markAllNotificationsRead(): Promise<{ count: number }> {
  return apiFetch<{ count: number }>("/notifications/read-all", { method: "POST" });
}
```

- [ ] **Step 2: Verify tsc compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/notifications.ts
git commit -m "feat: add notifications API client"
```

---

### Task 10: Frontend — NotificationBell component

**Files:**
- Create: `frontend/src/components/NotificationBell.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/NotificationBell.tsx`:

```tsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from "../api/notifications";
import type { Notification } from "../types";

const POLL_INTERVAL_MS = 60_000;

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diff = Math.max(0, now - then);
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function NotificationBell() {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [showRead, setShowRead] = useState(false);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try {
      const data = await fetchNotifications(showRead);
      setNotifications(data);
    } catch {
      // swallow — bell is a background feature; toast would be noisy
    }
  }, [showRead]);

  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const unreadCount = useMemo(
    () => notifications.filter((n) => n.read_at === null).length,
    [notifications],
  );

  const handleClick = async (n: Notification) => {
    try {
      await markNotificationRead(n.id);
    } catch {
      // ignore
    }
    setOpen(false);
    if (n.link_url) {
      navigate(n.link_url);
    }
    load();
  };

  const handleMarkAll = async () => {
    try {
      await markAllNotificationsRead();
    } catch {
      // ignore
    }
    load();
  };

  return (
    <div className="relative" ref={containerRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="relative p-2 text-text-muted hover:text-text-primary rounded"
        aria-label={`Notifications${unreadCount ? ` (${unreadCount} unread)` : ""}`}
      >
        <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0" />
        </svg>
        {unreadCount > 0 && (
          <span className="absolute top-0.5 right-0.5 min-w-[16px] h-4 px-1 text-[0.625rem] font-medium bg-accent text-bg-base rounded-full flex items-center justify-center">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute bottom-full right-0 mb-2 w-80 bg-bg-surface border border-border rounded-lg shadow-lg overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 border-b border-border">
            <span className="text-sm font-semibold text-text-primary">Notifications</span>
            <button
              onClick={handleMarkAll}
              disabled={unreadCount === 0}
              className="text-xs text-accent hover:underline disabled:opacity-50 disabled:hover:no-underline"
            >
              Mark all read
            </button>
          </div>

          <div className="max-h-96 overflow-auto">
            {notifications.length === 0 ? (
              <p className="px-3 py-6 text-center text-sm text-text-muted">
                No {showRead ? "" : "new "}notifications.
              </p>
            ) : (
              notifications.map((n) => (
                <button
                  key={n.id}
                  onClick={() => handleClick(n)}
                  className={`w-full text-left px-3 py-2 border-b border-border last:border-b-0 hover:bg-bg-elevated/50 ${
                    n.read_at !== null ? "opacity-60" : ""
                  }`}
                >
                  <p className="text-sm text-text-primary">{n.message}</p>
                  <p className="text-xs text-text-muted mt-0.5">{relativeTime(n.created_at)}</p>
                </button>
              ))
            )}
          </div>

          <div className="px-3 py-2 border-t border-border flex justify-end">
            <label className="text-xs text-text-muted flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={showRead}
                onChange={(e) => setShowRead(e.target.checked)}
              />
              Show read
            </label>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify tsc compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/NotificationBell.tsx
git commit -m "feat: add NotificationBell component"
```

---

### Task 11: Frontend — mount bell in Sidebar and MobileMenu

**Files:**
- Modify: `frontend/src/layouts/Sidebar.tsx`
- Modify: `frontend/src/layouts/MobileMenu.tsx`

- [ ] **Step 1: Add the bell to Sidebar**

In `frontend/src/layouts/Sidebar.tsx`, add the import at the top of the file:

```tsx
import { NotificationBell } from "../components/NotificationBell";
```

The current authenticated footer has a flex row containing `<ThemeToggle />` and the callsign NavLink. Modify that row to insert the bell between them. The existing block:

```tsx
<div className="flex items-center justify-between">
  <ThemeToggle />
  <NavLink
    to="/profile"
    className="font-mono text-sm text-text-secondary hover:text-accent transition-colors"
  >
    {user.callsign}
  </NavLink>
</div>
```

Replace with:

```tsx
<div className="flex items-center justify-between">
  <ThemeToggle />
  <div className="flex items-center gap-1">
    <NotificationBell />
    <NavLink
      to="/profile"
      className="font-mono text-sm text-text-secondary hover:text-accent transition-colors"
    >
      {user.callsign}
    </NavLink>
  </div>
</div>
```

- [ ] **Step 2: Add the bell to MobileMenu**

In `frontend/src/layouts/MobileMenu.tsx`, add the same import and apply the same pattern. The MobileMenu footer's authenticated branch already has a callsign NavLink and a ThemeToggle (read the file to confirm the exact structure). Insert `<NotificationBell />` next to the callsign NavLink, using a small flex row to keep them side by side.

- [ ] **Step 3: Verify tsc compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/layouts/Sidebar.tsx frontend/src/layouts/MobileMenu.tsx
git commit -m "feat: mount NotificationBell in Sidebar and MobileMenu"
```

---

### Task 12: Full verification

- [ ] **Step 1: Run the full backend suite**

Run: `nix-shell --run "python -m pytest tests/ -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All tests pass. Count should be higher than the pre-plan baseline by the number of new tests added (model + service + routes + four hook tests + two delivery-failure tests).

- [ ] **Step 2: Verify frontend type-checks**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Manual UI verification (cannot be scripted)**

Sign in as net_control (callsign assigned to an upcoming session), then in a browser:

- Trigger a reminder draft (via `/reminders` "+ Generate draft" or wait for the daily cron). The bell badge should appear with "1" within ~60 seconds (or sooner if you click the bell, which fetches immediately). Click the bell → notification shows "Reminder draft ready for {date}". Click it → navigates to /reminders, bell badge clears.
- Scan a mailbox with at least one new check-in for the session → bell shows "N check-in(s) imported for {date}", link goes to /checkins?session={id}.
- Generate a roster draft → bell shows "Roster draft ready for {date}", link goes to /roster.
- Approve a reminder or roster with no delivery backends configured and click Send → the toast shows the existing error AND a separate bell notification "Send failed for {reminder/roster} on {date} — verify delivery backends", link goes to /config.
- Click "Mark all read" → all clear.
- Toggle "Show read" → previously read items reappear, muted.

Report any UI issues; do not claim the task complete without performing this verification.
