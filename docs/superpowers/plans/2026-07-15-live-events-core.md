# Live Events Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add live-event support (public service + emergency): NCS-driven check-in/out with a status lifecycle, unified event log, cursor-polled live dashboard, and a minimal after-action export.

**Architecture:** New self-contained `backend/modules/events/` module (four tables: `events`, `event_posts`, `event_participants`, `event_log`) with a service layer raising typed exceptions and FastAPI routes under `/api/nets/{net_slug}/events`. Live updates via cursor polling (`?since=seq` against a per-event monotonic `log_seq` counter). Frontend: events list page + live dashboard + report page under `frontend/src/pages/events/`, polling via a `useEventUpdates` hook.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 mapped_column style, Alembic, pytest + httpx ASGI client; React 19 + TypeScript + Tailwind, plain `fetch()` via `apiFetch`.

**Spec:** `docs/superpowers/specs/2026-07-15-live-events-design.md`. One refinement over the spec: `event_log` gains a nullable `new_status` column (the structured record of what status a system transition set) so stint/hours computation never parses message text.

## Global Constraints

- Host is NixOS: backend commands via `.venv/bin/...`; frontend via `cd frontend && nix-shell -p nodejs_22 --run "npm <…>"`.
- Lint: `nix-shell --run "ruff check"` — line-length 120, `select = ["E", "F"]`; production code has no per-file ignores.
- Commits: Conventional Commits (`feat(events): …`, `test(events): …`).
- UI lists: NO pagination, NO infinite scroll — load all rows, client-side filter/sort.
- Enum storage: SQLAlchemy `Enum(PyEnum)` persists member **names** (e.g. `'DRAFT'`); API responses emit `.value` (e.g. `"draft"`). This matches every existing module.
- Timestamps: `datetime.now(timezone.utc)`, `DateTime(timezone=True)` columns.
- All writes attributed to the authenticated user (`ctx.user.callsign`) — never client-supplied.
- Do not push to remote; commit locally only.

---

### Task 1: Events data model + migration

**Files:**
- Create: `backend/modules/events/__init__.py` (empty)
- Create: `backend/modules/events/models.py`
- Modify: `alembic/env.py` (add models import after line 15)
- Create: `alembic/versions/e7a3c9d41b20_add_events_tables.py`
- Test: `tests/test_event_models.py`

**Interfaces:**
- Consumes: `backend.db.base.Base`, `nets.id` FK.
- Produces: `Event`, `EventPost`, `EventParticipant`, `EventLogEntry` models; enums `EventType` (PUBLIC_SERVICE/EMERGENCY), `EventStatus` (DRAFT/ACTIVE/CLOSED), `ParticipantStatus` (CHECKED_IN/AT_POST/EN_ROUTE/OUT_OF_SERVICE/CHECKED_OUT), `EventLogType` (SYSTEM/NOTE/PARTICIPANT_NOTE). `Event.log_seq` counter column. All later backend tasks import from `backend.modules.events.models`.

- [ ] **Step 1: Write failing model tests**

```python
# tests/test_event_models.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import (
    Event,
    EventLogEntry,
    EventLogType,
    EventParticipant,
    EventPost,
    EventStatus,
    EventType,
    ParticipantStatus,
)
from tests.conftest import make_test_net


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def net(db):
    return make_test_net(db)


def make_event(db, net, **overrides):
    event = Event(
        net_id=net.id,
        name=overrides.get("name", "Field Day"),
        event_type=overrides.get("event_type", EventType.PUBLIC_SERVICE),
        created_by=overrides.get("created_by", "W0NE"),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def test_event_defaults(db, net):
    event = make_event(db, net)
    assert event.status == EventStatus.DRAFT
    assert event.log_seq == 0
    assert event.activated_at is None
    assert event.closed_at is None
    assert event.created_at is not None


def test_post_unique_name_per_event(db, net):
    event = make_event(db, net)
    db.add(EventPost(event_id=event.id, name="Rest Stop 3"))
    db.commit()
    db.add(EventPost(event_id=event.id, name="Rest Stop 3"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    # Same name on a different event is fine
    other = make_event(db, net, name="Other")
    db.add(EventPost(event_id=other.id, name="Rest Stop 3"))
    db.commit()


def test_participant_unique_callsign_per_event(db, net):
    event = make_event(db, net)
    db.add(EventParticipant(event_id=event.id, callsign="KE0XYZ"))
    db.commit()
    db.add(EventParticipant(event_id=event.id, callsign="KE0XYZ"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_participant_defaults(db, net):
    event = make_event(db, net)
    p = EventParticipant(event_id=event.id, callsign="KE0XYZ")
    db.add(p)
    db.commit()
    db.refresh(p)
    assert p.current_status == ParticipantStatus.CHECKED_IN
    assert p.checked_in_at is not None
    assert p.checked_out_at is None
    assert p.post_id is None


def test_log_entry_unique_seq_per_event(db, net):
    event = make_event(db, net)
    db.add(EventLogEntry(
        event_id=event.id, seq=1, entry_type=EventLogType.SYSTEM,
        actor="W0NE", message="Event activated",
    ))
    db.commit()
    db.add(EventLogEntry(
        event_id=event.id, seq=1, entry_type=EventLogType.NOTE,
        actor="W0NE", message="dup seq",
    ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_log_entry_defaults(db, net):
    event = make_event(db, net)
    entry = EventLogEntry(
        event_id=event.id, seq=1, entry_type=EventLogType.SYSTEM,
        actor="W0NE", message="KE0XYZ checked in", callsign="KE0XYZ",
        new_status=ParticipantStatus.CHECKED_IN,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    assert entry.pinned is False
    assert entry.created_at is not None


def test_event_cascade_delete(db, net):
    event = make_event(db, net)
    db.add(EventPost(event_id=event.id, name="EOC"))
    db.add(EventParticipant(event_id=event.id, callsign="KE0XYZ"))
    db.add(EventLogEntry(
        event_id=event.id, seq=1, entry_type=EventLogType.SYSTEM,
        actor="W0NE", message="x",
    ))
    db.commit()
    db.delete(event)
    db.commit()
    assert db.query(EventPost).count() == 0
    assert db.query(EventParticipant).count() == 0
    assert db.query(EventLogEntry).count() == 0
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/test_event_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.modules.events'`

- [ ] **Step 3: Write the models**

Create `backend/modules/events/__init__.py` (empty file), then:

```python
# backend/modules/events/models.py
import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventType(str, enum.Enum):
    PUBLIC_SERVICE = "public_service"
    EMERGENCY = "emergency"


class EventStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    CLOSED = "closed"


class ParticipantStatus(str, enum.Enum):
    CHECKED_IN = "checked_in"
    AT_POST = "at_post"
    EN_ROUTE = "en_route"
    OUT_OF_SERVICE = "out_of_service"
    CHECKED_OUT = "checked_out"


class EventLogType(str, enum.Enum):
    SYSTEM = "system"
    NOTE = "note"
    PARTICIPANT_NOTE = "participant_note"


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    net_id: Mapped[int] = mapped_column(Integer, ForeignKey("nets.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[EventType] = mapped_column(Enum(EventType), nullable=False)
    status: Mapped[EventStatus] = mapped_column(Enum(EventStatus), nullable=False, default=EventStatus.DRAFT)
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    # Last-assigned event_log.seq for this event. Incremented under the event
    # row lock (SELECT ... FOR UPDATE on PostgreSQL; SQLite serializes writes)
    # so concurrent operators can't mint the same seq.
    log_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    posts: Mapped[list["EventPost"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    participants: Mapped[list["EventParticipant"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    log_entries: Mapped[list["EventLogEntry"]] = relationship(back_populates="event", cascade="all, delete-orphan")


class EventPost(Base):
    __tablename__ = "event_posts"
    __table_args__ = (UniqueConstraint("event_id", "name", name="uq_event_posts_event_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("events.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    event: Mapped["Event"] = relationship(back_populates="posts")


class EventParticipant(Base):
    __tablename__ = "event_participants"
    __table_args__ = (UniqueConstraint("event_id", "callsign", name="uq_event_participants_event_callsign"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("events.id"), nullable=False)
    # Plain string, uppercased by the service — events have no membership
    # concept and any callsign (mutual aid, walk-ups) can participate.
    callsign: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    post_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("event_posts.id"), nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_status: Mapped[ParticipantStatus] = mapped_column(
        Enum(ParticipantStatus), nullable=False, default=ParticipantStatus.CHECKED_IN
    )
    # Latest transition times; full history lives in event_log.
    checked_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    checked_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    event: Mapped["Event"] = relationship(back_populates="participants")
    post: Mapped["EventPost | None"] = relationship()


class EventLogEntry(Base):
    __tablename__ = "event_log"
    __table_args__ = (UniqueConstraint("event_id", "seq", name="uq_event_log_event_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("events.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_type: Mapped[EventLogType] = mapped_column(Enum(EventLogType), nullable=False)
    # The participant this entry concerns (nullable — event-wide entries).
    callsign: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Who caused/wrote the entry. Always the authenticated operator.
    actor: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured record of the status a SYSTEM transition set — stint/hours
    # computation reads this instead of parsing message text.
    new_status: Mapped[ParticipantStatus | None] = mapped_column(Enum(ParticipantStatus), nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    event: Mapped["Event"] = relationship(back_populates="log_entries")
```

- [ ] **Step 4: Register models with alembic**

In `alembic/env.py`, after `import backend.modules.roster.models  # noqa: F401` (line 15), add:

```python
import backend.modules.events.models  # noqa: F401
```

- [ ] **Step 5: Run model tests, verify they pass**

Run: `.venv/bin/pytest tests/test_event_models.py -q`
Expected: 7 passed

- [ ] **Step 6: Write the migration**

```python
# alembic/versions/e7a3c9d41b20_add_events_tables.py
"""add events tables

Revision ID: e7a3c9d41b20
Revises: 834e2b6db91d
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a3c9d41b20'
down_revision: Union[str, None] = '834e2b6db91d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('net_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('event_type', sa.Enum('PUBLIC_SERVICE', 'EMERGENCY', name='eventtype'), nullable=False),
    sa.Column('status', sa.Enum('DRAFT', 'ACTIVE', 'CLOSED', name='eventstatus'), nullable=False),
    sa.Column('scheduled_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('log_seq', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['net_id'], ['nets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('event_posts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('lat', sa.Float(), nullable=True),
    sa.Column('lon', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_id', 'name', name='uq_event_posts_event_name')
    )
    op.create_table('event_participants',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('callsign', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=True),
    sa.Column('post_id', sa.Integer(), nullable=True),
    sa.Column('location', sa.Text(), nullable=True),
    sa.Column('current_status', sa.Enum('CHECKED_IN', 'AT_POST', 'EN_ROUTE', 'OUT_OF_SERVICE', 'CHECKED_OUT', name='participantstatus'), nullable=False),
    sa.Column('checked_in_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('checked_out_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ),
    sa.ForeignKeyConstraint(['post_id'], ['event_posts.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_id', 'callsign', name='uq_event_participants_event_callsign')
    )
    op.create_table('event_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('entry_type', sa.Enum('SYSTEM', 'NOTE', 'PARTICIPANT_NOTE', name='eventlogtype'), nullable=False),
    sa.Column('callsign', sa.String(length=20), nullable=True),
    sa.Column('actor', sa.String(length=20), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('new_status', sa.Enum('CHECKED_IN', 'AT_POST', 'EN_ROUTE', 'OUT_OF_SERVICE', 'CHECKED_OUT', name='participantstatus'), nullable=True),
    sa.Column('pinned', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_id', 'seq', name='uq_event_log_event_seq')
    )


def downgrade() -> None:
    op.drop_table('event_log')
    op.drop_table('event_participants')
    op.drop_table('event_posts')
    op.drop_table('events')
```

- [ ] **Step 7: Verify the migration runs against a scratch DB**

Run:
```bash
SKYNET_DATABASE_URL="sqlite:////tmp/claude-events-migration-test.db" .venv/bin/alembic upgrade head
rm -f /tmp/claude-events-migration-test.db
```
Expected: runs through all revisions ending with `Running upgrade 834e2b6db91d -> e7a3c9d41b20, add events tables` and exits 0.

- [ ] **Step 8: Full test suite + lint**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"`
Expected: all pass, no lint errors

- [ ] **Step 9: Commit**

```bash
git add backend/modules/events/ alembic/env.py alembic/versions/e7a3c9d41b20_add_events_tables.py tests/test_event_models.py
git commit -m "feat(events): data model and migration for live events"
```

---

### Task 2: Service layer — event lifecycle, log seq machinery, posts

**Files:**
- Create: `backend/modules/events/service.py`
- Test: `tests/test_event_service.py`

**Interfaces:**
- Consumes: models from Task 1; `Session` from SQLAlchemy.
- Produces (all in `backend.modules.events.service`):
  - Exceptions: `EventError` (base), `EventNotActiveError`, `InvalidLifecycleError`, `DuplicatePostError`, `PostAssignedError`, `InvalidPostError`, `DuplicateParticipantError`, `InvalidStatusTransitionError` (the last two used from Task 3 onward).
  - `locked_event(db, event_id) -> Event | None` — fetch with `with_for_update()`.
  - `add_log_entry(db, event, *, entry_type, message, actor, callsign=None, new_status=None, pinned=False) -> EventLogEntry` — increments `event.log_seq`, does NOT commit.
  - `create_event(db, *, net_id, name, event_type, created_by, description=None, scheduled_start=None, activate=False) -> Event`
  - `activate_event(db, event_id, *, actor) -> Event` / `close_event(...)` / `reopen_event(...)` — raise `InvalidLifecycleError` on wrong current status.
  - `update_event(db, event_id, *, name=None, description=..., scheduled_start=...) -> Event` — raises `EventNotActiveError` if closed.
  - `create_post(db, event_id, *, name, description=None, lat=None, lon=None) -> EventPost` — `DuplicatePostError` on name clash, `EventNotActiveError` if event closed.
  - `update_post(db, event_id, post_id, **fields) -> EventPost | None` (None = not found)
  - `delete_post(db, event_id, post_id) -> bool` — `PostAssignedError` if any participant assigned; False if not found.

- [ ] **Step 1: Write failing service tests**

```python
# tests/test_event_service.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import (
    Event,
    EventLogEntry,
    EventLogType,
    EventParticipant,
    EventStatus,
    EventType,
)
from backend.modules.events.service import (
    DuplicatePostError,
    EventNotActiveError,
    InvalidLifecycleError,
    PostAssignedError,
    activate_event,
    close_event,
    create_event,
    create_post,
    delete_post,
    reopen_event,
    update_event,
    update_post,
)
from tests.conftest import make_test_net


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def net(db):
    return make_test_net(db)


def _log_messages(db, event_id):
    entries = (
        db.query(EventLogEntry)
        .filter(EventLogEntry.event_id == event_id)
        .order_by(EventLogEntry.seq)
        .all()
    )
    return [(e.seq, e.entry_type, e.message) for e in entries]


class TestLifecycle:
    def test_create_draft(self, db, net):
        event = create_event(
            db, net_id=net.id, name="Marathon", event_type=EventType.PUBLIC_SERVICE, created_by="W0NE"
        )
        assert event.status == EventStatus.DRAFT
        assert event.activated_at is None
        assert _log_messages(db, event.id) == []

    def test_create_and_activate(self, db, net):
        event = create_event(
            db, net_id=net.id, name="Tornado", event_type=EventType.EMERGENCY,
            created_by="W0NE", activate=True,
        )
        assert event.status == EventStatus.ACTIVE
        assert event.activated_at is not None
        msgs = _log_messages(db, event.id)
        assert msgs == [(1, EventLogType.SYSTEM, "Event activated")]
        assert event.log_seq == 1

    def test_activate_draft(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.PUBLIC_SERVICE, created_by="W0NE"
        )
        result = activate_event(db, event.id, actor="W0NC")
        assert result.status == EventStatus.ACTIVE
        assert result.activated_at is not None

    def test_activate_non_draft_raises(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.EMERGENCY, created_by="W0NE", activate=True
        )
        with pytest.raises(InvalidLifecycleError):
            activate_event(db, event.id, actor="W0NE")

    def test_close_active(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.EMERGENCY, created_by="W0NE", activate=True
        )
        result = close_event(db, event.id, actor="W0NE")
        assert result.status == EventStatus.CLOSED
        assert result.closed_at is not None
        assert _log_messages(db, event.id)[-1][2] == "Event closed"

    def test_close_draft_raises(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.PUBLIC_SERVICE, created_by="W0NE"
        )
        with pytest.raises(InvalidLifecycleError):
            close_event(db, event.id, actor="W0NE")

    def test_reopen_closed(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.EMERGENCY, created_by="W0NE", activate=True
        )
        close_event(db, event.id, actor="W0NE")
        result = reopen_event(db, event.id, actor="W0NE")
        assert result.status == EventStatus.ACTIVE
        assert result.closed_at is None
        assert _log_messages(db, event.id)[-1][2] == "Event reopened"

    def test_reopen_active_raises(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.EMERGENCY, created_by="W0NE", activate=True
        )
        with pytest.raises(InvalidLifecycleError):
            reopen_event(db, event.id, actor="W0NE")

    def test_seq_is_monotonic(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.EMERGENCY, created_by="W0NE", activate=True
        )
        close_event(db, event.id, actor="W0NE")
        reopen_event(db, event.id, actor="W0NE")
        seqs = [s for s, _, _ in _log_messages(db, event.id)]
        assert seqs == [1, 2, 3]
        db.refresh(event)
        assert event.log_seq == 3

    def test_update_event_fields(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.PUBLIC_SERVICE, created_by="W0NE"
        )
        result = update_event(db, event.id, name="Renamed", description="desc")
        assert result.name == "Renamed"
        assert result.description == "desc"

    def test_update_closed_event_raises(self, db, net):
        event = create_event(
            db, net_id=net.id, name="E", event_type=EventType.EMERGENCY, created_by="W0NE", activate=True
        )
        close_event(db, event.id, actor="W0NE")
        with pytest.raises(EventNotActiveError):
            update_event(db, event.id, name="Nope")


class TestPosts:
    @pytest.fixture
    def event(self, db, net):
        return create_event(
            db, net_id=net.id, name="Marathon", event_type=EventType.PUBLIC_SERVICE,
            created_by="W0NE", activate=True,
        )

    def test_create_post(self, db, event):
        post = create_post(db, event.id, name="Rest Stop 3", lat=39.1, lon=-94.6)
        assert post.id is not None
        assert post.lat == 39.1

    def test_create_post_on_draft_event(self, db, net):
        draft = create_event(
            db, net_id=net.id, name="D", event_type=EventType.PUBLIC_SERVICE, created_by="W0NE"
        )
        post = create_post(db, draft.id, name="SAG 1")
        assert post.id is not None

    def test_create_post_on_closed_event_raises(self, db, event):
        close_event(db, event.id, actor="W0NE")
        with pytest.raises(EventNotActiveError):
            create_post(db, event.id, name="Late")

    def test_duplicate_post_name_raises(self, db, event):
        create_post(db, event.id, name="EOC")
        with pytest.raises(DuplicatePostError):
            create_post(db, event.id, name="EOC")

    def test_update_post(self, db, event):
        post = create_post(db, event.id, name="EOC")
        result = update_post(db, event.id, post.id, name="EOC Main", lat=39.0)
        assert result.name == "EOC Main"
        assert result.lat == 39.0

    def test_update_post_duplicate_name_raises(self, db, event):
        create_post(db, event.id, name="EOC")
        post2 = create_post(db, event.id, name="Shelter A")
        with pytest.raises(DuplicatePostError):
            update_post(db, event.id, post2.id, name="EOC")

    def test_delete_unassigned_post(self, db, event):
        post = create_post(db, event.id, name="EOC")
        assert delete_post(db, event.id, post.id) is True

    def test_delete_assigned_post_raises(self, db, event):
        post = create_post(db, event.id, name="EOC")
        db.add(EventParticipant(event_id=event.id, callsign="KE0XYZ", post_id=post.id))
        db.commit()
        with pytest.raises(PostAssignedError):
            delete_post(db, event.id, post.id)

    def test_delete_missing_post_returns_false(self, db, event):
        assert delete_post(db, event.id, 9999) is False
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/test_event_service.py -q`
Expected: FAIL — `ImportError` (no `backend.modules.events.service`)

- [ ] **Step 3: Write the service**

```python
# backend/modules/events/service.py
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.modules.events.models import (
    Event,
    EventLogEntry,
    EventLogType,
    EventParticipant,
    EventPost,
    EventStatus,
    EventType,
    ParticipantStatus,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- Exceptions ---------------------------------------------------------


class EventError(Exception):
    """Base for events service errors."""


class EventNotActiveError(EventError):
    """Write attempted against an event that isn't in the required status (→ 409)."""


class InvalidLifecycleError(EventError):
    """activate/close/reopen from the wrong current status (→ 409)."""


class DuplicatePostError(EventError):
    """Post name already exists on this event (→ 409)."""


class PostAssignedError(EventError):
    """Post still has assigned participants (→ 409)."""


class InvalidPostError(EventError):
    """post_id does not belong to this event (→ 422)."""


class DuplicateParticipantError(EventError):
    """Callsign already checked in to this event (→ 409)."""


class InvalidStatusTransitionError(EventError):
    """Illegal participant status transition (→ 422)."""


# --- Log / seq machinery -------------------------------------------------


def locked_event(db: Session, event_id: int) -> Event | None:
    """Fetch the event row with FOR UPDATE so concurrent writers serialize on
    it (log_seq assignment depends on this). No-op lock on SQLite, which
    serializes writes anyway."""
    return db.query(Event).filter(Event.id == event_id).with_for_update().one_or_none()


def add_log_entry(
    db: Session,
    event: Event,
    *,
    entry_type: EventLogType,
    message: str,
    actor: str,
    callsign: str | None = None,
    new_status: ParticipantStatus | None = None,
    pinned: bool = False,
) -> EventLogEntry:
    """Append a log entry with the next seq. Caller must hold the event row
    (via locked_event) and is responsible for committing."""
    event.log_seq += 1
    entry = EventLogEntry(
        event_id=event.id,
        seq=event.log_seq,
        entry_type=entry_type,
        message=message,
        actor=actor,
        callsign=callsign,
        new_status=new_status,
        pinned=pinned,
    )
    db.add(entry)
    return entry


# --- Event lifecycle ------------------------------------------------------


def create_event(
    db: Session,
    *,
    net_id: int,
    name: str,
    event_type: EventType,
    created_by: str,
    description: str | None = None,
    scheduled_start: datetime | None = None,
    activate: bool = False,
) -> Event:
    event = Event(
        net_id=net_id,
        name=name,
        event_type=event_type,
        description=description,
        scheduled_start=scheduled_start,
        created_by=created_by,
    )
    db.add(event)
    db.flush()
    if activate:
        event.status = EventStatus.ACTIVE
        event.activated_at = _utcnow()
        add_log_entry(db, event, entry_type=EventLogType.SYSTEM, message="Event activated", actor=created_by)
    db.commit()
    db.refresh(event)
    return event


def activate_event(db: Session, event_id: int, *, actor: str) -> Event:
    event = locked_event(db, event_id)
    if event is None:
        raise InvalidLifecycleError("Event not found")
    if event.status != EventStatus.DRAFT:
        raise InvalidLifecycleError(f"Cannot activate an event in status {event.status.value}")
    event.status = EventStatus.ACTIVE
    event.activated_at = _utcnow()
    add_log_entry(db, event, entry_type=EventLogType.SYSTEM, message="Event activated", actor=actor)
    db.commit()
    db.refresh(event)
    return event


def close_event(db: Session, event_id: int, *, actor: str) -> Event:
    event = locked_event(db, event_id)
    if event is None or event.status != EventStatus.ACTIVE:
        raise InvalidLifecycleError("Only an active event can be closed")
    add_log_entry(db, event, entry_type=EventLogType.SYSTEM, message="Event closed", actor=actor)
    event.status = EventStatus.CLOSED
    event.closed_at = _utcnow()
    db.commit()
    db.refresh(event)
    return event


def reopen_event(db: Session, event_id: int, *, actor: str) -> Event:
    event = locked_event(db, event_id)
    if event is None or event.status != EventStatus.CLOSED:
        raise InvalidLifecycleError("Only a closed event can be reopened")
    event.status = EventStatus.ACTIVE
    event.closed_at = None
    add_log_entry(db, event, entry_type=EventLogType.SYSTEM, message="Event reopened", actor=actor)
    db.commit()
    db.refresh(event)
    return event


_UNSET = object()


def update_event(
    db: Session,
    event_id: int,
    *,
    name: str | None = None,
    description: object = _UNSET,
    scheduled_start: object = _UNSET,
) -> Event | None:
    event = locked_event(db, event_id)
    if event is None:
        return None
    if event.status == EventStatus.CLOSED:
        raise EventNotActiveError("Cannot edit a closed event")
    if name is not None:
        event.name = name
    if description is not _UNSET:
        event.description = description
    if scheduled_start is not _UNSET:
        event.scheduled_start = scheduled_start
    db.commit()
    db.refresh(event)
    return event


# --- Posts ---------------------------------------------------------------


def _post_name_taken(db: Session, event_id: int, name: str, exclude_id: int | None = None) -> bool:
    query = db.query(EventPost).filter(EventPost.event_id == event_id, EventPost.name == name)
    if exclude_id is not None:
        query = query.filter(EventPost.id != exclude_id)
    return query.first() is not None


def create_post(
    db: Session,
    event_id: int,
    *,
    name: str,
    description: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> EventPost:
    event = locked_event(db, event_id)
    if event is None or event.status == EventStatus.CLOSED:
        raise EventNotActiveError("Cannot add posts to a closed event")
    if _post_name_taken(db, event_id, name):
        raise DuplicatePostError(f"Post '{name}' already exists")
    post = EventPost(event_id=event_id, name=name, description=description, lat=lat, lon=lon)
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def update_post(
    db: Session,
    event_id: int,
    post_id: int,
    *,
    name: str | None = None,
    description: object = _UNSET,
    lat: object = _UNSET,
    lon: object = _UNSET,
) -> EventPost | None:
    post = db.query(EventPost).filter(EventPost.id == post_id, EventPost.event_id == event_id).one_or_none()
    if post is None:
        return None
    if name is not None:
        if _post_name_taken(db, event_id, name, exclude_id=post_id):
            raise DuplicatePostError(f"Post '{name}' already exists")
        post.name = name
    if description is not _UNSET:
        post.description = description
    if lat is not _UNSET:
        post.lat = lat
    if lon is not _UNSET:
        post.lon = lon
    db.commit()
    db.refresh(post)
    return post


def delete_post(db: Session, event_id: int, post_id: int) -> bool:
    post = db.query(EventPost).filter(EventPost.id == post_id, EventPost.event_id == event_id).one_or_none()
    if post is None:
        return False
    assigned = db.query(EventParticipant).filter(EventParticipant.post_id == post_id).first()
    if assigned is not None:
        raise PostAssignedError("Post has assigned participants")
    db.delete(post)
    db.commit()
    return True
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `.venv/bin/pytest tests/test_event_service.py tests/test_event_models.py -q`
Expected: all pass

- [ ] **Step 5: Lint + commit**

Run: `nix-shell --run "ruff check"` — expected clean.

```bash
git add backend/modules/events/service.py tests/test_event_service.py
git commit -m "feat(events): lifecycle, log-seq, and posts service"
```

---

### Task 3: Service layer — participants, status machine, notes, report

**Files:**
- Modify: `backend/modules/events/service.py` (append)
- Test: `tests/test_event_participant_service.py`

**Interfaces:**
- Consumes: Task 2 helpers (`locked_event`, `add_log_entry`, exceptions, `_UNSET`); `backend.integrations.callbook.service.lookup_callsign`.
- Produces:
  - `check_in(db, event_id, *, callsign, actor, name=None, post_id=None, location=None) -> EventParticipant` — raises `EventNotActiveError`, `DuplicateParticipantError`, `InvalidPostError`. Callbook prefill when `name is None` (failure → name stays None).
  - `update_participant(db, event_id, participant_id, *, actor, status=_UNSET, post_id=_UNSET, location=_UNSET, name=_UNSET) -> EventParticipant | None` — raises `InvalidStatusTransitionError`, `InvalidPostError`, `EventNotActiveError`.
  - `add_note(db, event_id, *, actor, message, callsign=None, pinned=False) -> EventLogEntry` — raises `EventNotActiveError`.
  - `set_log_pinned(db, event_id, entry_id, pinned) -> EventLogEntry | None`
  - `compute_report(db, event) -> list[dict]` — per participant: `{"callsign", "name", "post", "location", "stints": [{"start": iso, "end": iso|None}], "total_seconds": int}`.

**Status machine (from spec):** any status except `CHECKED_OUT` may move directly to any other status. From `CHECKED_OUT` the only legal transition is `CHECKED_IN` (re-check-in). Transition to same status is a no-op (no log entry). `CHECKED_OUT` sets `checked_out_at`; re-entry to `CHECKED_IN` from `CHECKED_OUT` sets `checked_in_at` and clears `checked_out_at`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_event_participant_service.py
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.modules.events.service as events_service
from backend.db.base import Base
from backend.modules.events.models import (
    EventLogEntry,
    EventLogType,
    EventType,
    ParticipantStatus,
)
from backend.modules.events.service import (
    DuplicateParticipantError,
    EventNotActiveError,
    InvalidPostError,
    InvalidStatusTransitionError,
    add_note,
    check_in,
    close_event,
    compute_report,
    create_event,
    create_post,
    set_log_pinned,
    update_participant,
)
from tests.conftest import make_test_net


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def net(db):
    return make_test_net(db)


@pytest.fixture
def event(db, net):
    return create_event(
        db, net_id=net.id, name="Tornado Watch", event_type=EventType.EMERGENCY,
        created_by="W0NE", activate=True,
    )


@pytest.fixture(autouse=True)
def _no_callbook(monkeypatch):
    """Default: callbook returns nothing. Tests that exercise prefill override."""
    monkeypatch.setattr(events_service, "lookup_callsign", lambda db, cs: None)


def _last_log(db, event_id) -> EventLogEntry:
    return (
        db.query(EventLogEntry)
        .filter(EventLogEntry.event_id == event_id)
        .order_by(EventLogEntry.seq.desc())
        .first()
    )


class TestCheckIn:
    def test_check_in_uppercases_and_logs(self, db, event):
        p = check_in(db, event.id, callsign="ke0xyz", actor="W0NC")
        assert p.callsign == "KE0XYZ"
        assert p.current_status == ParticipantStatus.CHECKED_IN
        entry = _last_log(db, event.id)
        assert entry.entry_type == EventLogType.SYSTEM
        assert entry.callsign == "KE0XYZ"
        assert entry.new_status == ParticipantStatus.CHECKED_IN
        assert entry.actor == "W0NC"

    def test_callbook_prefill(self, db, event, monkeypatch):
        monkeypatch.setattr(
            events_service, "lookup_callsign",
            lambda db, cs: {"callsign": cs, "name": "Jane Doe"},
        )
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        assert p.name == "Jane Doe"

    def test_explicit_name_skips_callbook(self, db, event, monkeypatch):
        def boom(db, cs):
            raise AssertionError("should not be called")
        monkeypatch.setattr(events_service, "lookup_callsign", boom)
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC", name="Bob")
        assert p.name == "Bob"

    def test_callbook_failure_is_nonfatal(self, db, event, monkeypatch):
        def boom(db, cs):
            raise RuntimeError("provider down")
        monkeypatch.setattr(events_service, "lookup_callsign", boom)
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        assert p.name is None

    def test_duplicate_active_check_in_raises(self, db, event):
        check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        with pytest.raises(DuplicateParticipantError):
            check_in(db, event.id, callsign="ke0xyz", actor="W0NC")

    def test_recheck_in_after_checkout_reuses_row(self, db, event):
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.CHECKED_OUT)
        p2 = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        assert p2.id == p.id
        assert p2.current_status == ParticipantStatus.CHECKED_IN
        assert p2.checked_out_at is None

    def test_check_in_with_post(self, db, event):
        post = create_post(db, event.id, name="EOC")
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC", post_id=post.id)
        assert p.post_id == post.id
        assert "EOC" in _last_log(db, event.id).message

    def test_check_in_with_foreign_post_raises(self, db, net, event):
        other = create_event(
            db, net_id=net.id, name="Other", event_type=EventType.EMERGENCY,
            created_by="W0NE", activate=True,
        )
        foreign_post = create_post(db, other.id, name="Elsewhere")
        with pytest.raises(InvalidPostError):
            check_in(db, event.id, callsign="KE0XYZ", actor="W0NC", post_id=foreign_post.id)

    def test_check_in_on_inactive_event_raises(self, db, net):
        draft = create_event(
            db, net_id=net.id, name="D", event_type=EventType.EMERGENCY, created_by="W0NE"
        )
        with pytest.raises(EventNotActiveError):
            check_in(db, draft.id, callsign="KE0XYZ", actor="W0NC")


class TestStatusMachine:
    def test_valid_transitions(self, db, event):
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        for status in (
            ParticipantStatus.EN_ROUTE,
            ParticipantStatus.AT_POST,
            ParticipantStatus.OUT_OF_SERVICE,
            ParticipantStatus.AT_POST,
            ParticipantStatus.CHECKED_OUT,
        ):
            p = update_participant(db, event.id, p.id, actor="W0NC", status=status)
            assert p.current_status == status
        assert p.checked_out_at is not None

    def test_checked_out_only_returns_via_checked_in(self, db, event):
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.CHECKED_OUT)
        with pytest.raises(InvalidStatusTransitionError):
            update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.AT_POST)
        p = update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.CHECKED_IN)
        assert p.current_status == ParticipantStatus.CHECKED_IN
        assert p.checked_out_at is None

    def test_status_change_logs_with_new_status(self, db, event):
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.EN_ROUTE)
        entry = _last_log(db, event.id)
        assert entry.new_status == ParticipantStatus.EN_ROUTE
        assert entry.callsign == "KE0XYZ"

    def test_same_status_is_noop(self, db, event):
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        seq_before = _last_log(db, event.id).seq
        update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.CHECKED_IN)
        assert _last_log(db, event.id).seq == seq_before

    def test_location_and_post_changes_log(self, db, event):
        post = create_post(db, event.id, name="Shelter A")
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        update_participant(db, event.id, p.id, actor="W0NC", location="Mobile, Hwy 9")
        assert "Hwy 9" in _last_log(db, event.id).message
        update_participant(db, event.id, p.id, actor="W0NC", post_id=post.id)
        assert "Shelter A" in _last_log(db, event.id).message

    def test_name_change_does_not_log(self, db, event):
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        seq_before = _last_log(db, event.id).seq
        update_participant(db, event.id, p.id, actor="W0NC", name="Corrected Name")
        assert _last_log(db, event.id).seq == seq_before

    def test_update_on_closed_event_raises(self, db, event):
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        close_event(db, event.id, actor="W0NE")
        with pytest.raises(EventNotActiveError):
            update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.CHECKED_OUT)


class TestNotes:
    def test_event_note(self, db, event):
        entry = add_note(db, event.id, actor="W0NC", message="Course clear")
        assert entry.entry_type == EventLogType.NOTE
        assert entry.callsign is None

    def test_participant_note(self, db, event):
        check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        entry = add_note(db, event.id, actor="W0NC", message="Has medical training",
                         callsign="ke0xyz", pinned=True)
        assert entry.entry_type == EventLogType.PARTICIPANT_NOTE
        assert entry.callsign == "KE0XYZ"
        assert entry.pinned is True

    def test_note_on_closed_event_raises(self, db, event):
        close_event(db, event.id, actor="W0NE")
        with pytest.raises(EventNotActiveError):
            add_note(db, event.id, actor="W0NC", message="too late")

    def test_pin_unpin(self, db, event):
        entry = add_note(db, event.id, actor="W0NC", message="x")
        result = set_log_pinned(db, event.id, entry.id, True)
        assert result.pinned is True
        result = set_log_pinned(db, event.id, entry.id, False)
        assert result.pinned is False

    def test_pin_missing_entry_returns_none(self, db, event):
        assert set_log_pinned(db, event.id, 9999, True) is None


class TestReport:
    def test_two_stints_hours(self, db, event):
        p = check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.CHECKED_OUT)
        check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        update_participant(db, event.id, p.id, actor="W0NC", status=ParticipantStatus.CHECKED_OUT)
        db.refresh(event)
        report = compute_report(db, event)
        assert len(report) == 1
        entry = report[0]
        assert entry["callsign"] == "KE0XYZ"
        assert len(entry["stints"]) == 2
        assert all(s["end"] is not None for s in entry["stints"])
        assert entry["total_seconds"] >= 0

    def test_open_stint_ends_at_close(self, db, event):
        check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        close_event(db, event.id, actor="W0NE")
        db.refresh(event)
        report = compute_report(db, event)
        stint = report[0]["stints"][0]
        assert stint["end"] is None  # still open in the payload
        closed_at = event.closed_at.replace(tzinfo=timezone.utc)
        start = datetime.fromisoformat(stint["start"])
        expected = int((closed_at - start.replace(tzinfo=timezone.utc)).total_seconds())
        assert abs(report[0]["total_seconds"] - expected) <= 1
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/test_event_participant_service.py -q`
Expected: FAIL — ImportError (`check_in` etc. not defined)

- [ ] **Step 3: Append participant/notes/report code to service.py**

Add `from backend.integrations.callbook.service import lookup_callsign` to the imports at the top of `backend/modules/events/service.py`, then append:

```python
# --- Participants ---------------------------------------------------------


def _human_status(status: ParticipantStatus) -> str:
    return status.value.replace("_", " ")


def _callbook_name(db: Session, callsign: str) -> str | None:
    """Best-effort name prefill. Never raises; a down callbook must not block
    a check-in mid-event. NOTE: lookup_callsign commits internally when it
    refreshes its cache — call this BEFORE taking the event row lock."""
    try:
        result = lookup_callsign(db, callsign)
    except Exception:
        return None
    if result is None:
        return None
    return result.get("name")


def _resolve_post(db: Session, event_id: int, post_id: int) -> EventPost:
    post = db.query(EventPost).filter(EventPost.id == post_id, EventPost.event_id == event_id).one_or_none()
    if post is None:
        raise InvalidPostError("Post does not belong to this event")
    return post


def check_in(
    db: Session,
    event_id: int,
    *,
    callsign: str,
    actor: str,
    name: str | None = None,
    post_id: int | None = None,
    location: str | None = None,
) -> EventParticipant:
    callsign = callsign.strip().upper()
    if not callsign:
        raise DuplicateParticipantError("Callsign is required")

    # Callbook lookup does network I/O and commits its cache — do it before
    # taking the event row lock so the lock isn't held across a network call.
    if name is None:
        name = _callbook_name(db, callsign)

    event = locked_event(db, event_id)
    if event is None or event.status != EventStatus.ACTIVE:
        raise EventNotActiveError("Event is not active")

    post = _resolve_post(db, event_id, post_id) if post_id is not None else None

    existing = (
        db.query(EventParticipant)
        .filter(EventParticipant.event_id == event_id, EventParticipant.callsign == callsign)
        .one_or_none()
    )
    if existing is not None:
        if existing.current_status != ParticipantStatus.CHECKED_OUT:
            raise DuplicateParticipantError(f"{callsign} is already checked in")
        # Re-check-in: new stint on the same row.
        existing.current_status = ParticipantStatus.CHECKED_IN
        existing.checked_in_at = _utcnow()
        existing.checked_out_at = None
        if post is not None:
            existing.post_id = post.id
        if location is not None:
            existing.location = location
        if name is not None:
            existing.name = name
        participant = existing
    else:
        participant = EventParticipant(
            event_id=event_id,
            callsign=callsign,
            name=name,
            post_id=post.id if post is not None else None,
            location=location,
        )
        db.add(participant)
        db.flush()

    message = f"{callsign} checked in"
    if post is not None:
        message += f" at {post.name}"
    add_log_entry(
        db, event, entry_type=EventLogType.SYSTEM, message=message, actor=actor,
        callsign=callsign, new_status=ParticipantStatus.CHECKED_IN,
    )
    db.commit()
    db.refresh(participant)
    return participant


def update_participant(
    db: Session,
    event_id: int,
    participant_id: int,
    *,
    actor: str,
    status: object = _UNSET,
    post_id: object = _UNSET,
    location: object = _UNSET,
    name: object = _UNSET,
) -> EventParticipant | None:
    event = locked_event(db, event_id)
    if event is None:
        return None
    if event.status != EventStatus.ACTIVE:
        raise EventNotActiveError("Event is not active")
    participant = (
        db.query(EventParticipant)
        .filter(EventParticipant.id == participant_id, EventParticipant.event_id == event_id)
        .one_or_none()
    )
    if participant is None:
        return None
    callsign = participant.callsign

    if status is not _UNSET and status != participant.current_status:
        if (
            participant.current_status == ParticipantStatus.CHECKED_OUT
            and status != ParticipantStatus.CHECKED_IN
        ):
            raise InvalidStatusTransitionError(
                f"{callsign} is checked out — they must check in again first"
            )
        participant.current_status = status
        if status == ParticipantStatus.CHECKED_OUT:
            participant.checked_out_at = _utcnow()
            message = f"{callsign} checked out"
        elif status == ParticipantStatus.CHECKED_IN and participant.checked_out_at is not None:
            participant.checked_in_at = _utcnow()
            participant.checked_out_at = None
            message = f"{callsign} checked in"
        else:
            message = f"{callsign} status: {_human_status(status)}"
        add_log_entry(
            db, event, entry_type=EventLogType.SYSTEM, message=message, actor=actor,
            callsign=callsign, new_status=status,
        )

    if post_id is not _UNSET:
        if post_id is None:
            participant.post_id = None
            add_log_entry(
                db, event, entry_type=EventLogType.SYSTEM,
                message=f"{callsign} unassigned from post", actor=actor, callsign=callsign,
            )
        else:
            post = _resolve_post(db, event_id, post_id)
            participant.post_id = post.id
            add_log_entry(
                db, event, entry_type=EventLogType.SYSTEM,
                message=f"{callsign} assigned to {post.name}", actor=actor, callsign=callsign,
            )

    if location is not _UNSET:
        participant.location = location
        add_log_entry(
            db, event, entry_type=EventLogType.SYSTEM,
            message=f"{callsign} location: {location or '(cleared)'}", actor=actor, callsign=callsign,
        )

    if name is not _UNSET:
        participant.name = name  # correction, not an operational fact — no log entry

    db.commit()
    db.refresh(participant)
    return participant


# --- Notes ----------------------------------------------------------------


def add_note(
    db: Session,
    event_id: int,
    *,
    actor: str,
    message: str,
    callsign: str | None = None,
    pinned: bool = False,
) -> EventLogEntry:
    event = locked_event(db, event_id)
    if event is None or event.status != EventStatus.ACTIVE:
        raise EventNotActiveError("Event is not active")
    callsign = callsign.strip().upper() if callsign else None
    entry_type = EventLogType.PARTICIPANT_NOTE if callsign else EventLogType.NOTE
    entry = add_log_entry(
        db, event, entry_type=entry_type, message=message, actor=actor,
        callsign=callsign, pinned=pinned,
    )
    db.commit()
    db.refresh(entry)
    return entry


def set_log_pinned(db: Session, event_id: int, entry_id: int, pinned: bool) -> EventLogEntry | None:
    entry = (
        db.query(EventLogEntry)
        .filter(EventLogEntry.id == entry_id, EventLogEntry.event_id == event_id)
        .one_or_none()
    )
    if entry is None:
        return None
    entry.pinned = pinned
    db.commit()
    db.refresh(entry)
    return entry


# --- Report ----------------------------------------------------------------


def compute_report(db: Session, event: Event) -> list[dict]:
    """Per-participant stints and total on-event seconds, derived from the
    structured new_status on SYSTEM log entries. An open stint (no checkout)
    counts until event.closed_at, or now for a still-active event; its "end"
    stays None in the payload."""
    participants = (
        db.query(EventParticipant)
        .filter(EventParticipant.event_id == event.id)
        .order_by(EventParticipant.callsign)
        .all()
    )
    entries = (
        db.query(EventLogEntry)
        .filter(
            EventLogEntry.event_id == event.id,
            EventLogEntry.new_status.in_([ParticipantStatus.CHECKED_IN, ParticipantStatus.CHECKED_OUT]),
        )
        .order_by(EventLogEntry.seq)
        .all()
    )
    by_callsign: dict[str, list[EventLogEntry]] = {}
    for entry in entries:
        by_callsign.setdefault(entry.callsign, []).append(entry)

    if event.closed_at is not None:
        cutoff = event.closed_at
    else:
        cutoff = _utcnow()
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)

    report = []
    for participant in participants:
        stints: list[dict] = []
        open_start: datetime | None = None
        for entry in by_callsign.get(participant.callsign, []):
            created = entry.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if entry.new_status == ParticipantStatus.CHECKED_IN and open_start is None:
                open_start = created
            elif entry.new_status == ParticipantStatus.CHECKED_OUT and open_start is not None:
                stints.append({"start": open_start, "end": created})
                open_start = None
        if open_start is not None:
            stints.append({"start": open_start, "end": None})

        total = sum(((s["end"] or cutoff) - s["start"]).total_seconds() for s in stints)
        report.append({
            "callsign": participant.callsign,
            "name": participant.name,
            "post": participant.post.name if participant.post else None,
            "location": participant.location,
            "stints": [
                {"start": s["start"].isoformat(), "end": s["end"].isoformat() if s["end"] else None}
                for s in stints
            ],
            "total_seconds": int(total),
        })
    return report
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `.venv/bin/pytest tests/test_event_participant_service.py tests/test_event_service.py -q`
Expected: all pass

- [ ] **Step 5: Lint + commit**

Run: `nix-shell --run "ruff check"` — expected clean.

```bash
git add backend/modules/events/service.py tests/test_event_participant_service.py
git commit -m "feat(events): participant status machine, notes, and report service"
```

---

### Task 4: API routes — event lifecycle + posts

**Files:**
- Create: `backend/modules/events/routes.py`
- Modify: `backend/app.py` (import + register router)
- Test: `tests/test_event_lifecycle_routes.py`

**Interfaces:**
- Consumes: Tasks 2–3 service functions + exceptions; `require_net_role`, `NetContext`, `get_db_session`.
- Produces: `events_router` (`APIRouter(prefix="/api/nets/{net_slug}/events", tags=["events"])`) with lifecycle + posts routes; response-shape helpers `_event_to_response`, `_post_to_response`, `_participant_to_response`, `_log_to_response`, and `_get_event_or_404(db, net_id, event_id)` — Task 5 appends to this file and reuses all of them.

**Response shapes (used by frontend Tasks 6+):**

```
event:       {id, net_id, name, description, event_type, status, scheduled_start,
              activated_at, closed_at, created_by, created_at}
post:        {id, event_id, name, description, lat, lon}
participant: {id, event_id, callsign, name, post_id, location, current_status,
              checked_in_at, checked_out_at}
log entry:   {id, seq, entry_type, callsign, actor, message, new_status, pinned, created_at}
```
All datetimes ISO-formatted or null; enums as `.value` strings.

**Error mapping:** `InvalidStatusTransitionError` and `InvalidPostError` → 422; every other `EventError` → 409.

- [ ] **Step 1: Write failing route tests**

```python
# tests/test_event_lifecycle_routes.py
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.nets.models import Net, NetMembership, NetRole
from tests.conftest import make_test_token

NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/events"


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)


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
        net_control = User(callsign="W0NC", oidc_subject="auth0|nc", name="Net Control")
        viewer = User(callsign="KD0TST", oidc_subject="auth0|viewer", name="Viewer")
        outsider = User(callsign="W0OUT", oidc_subject="auth0|out", name="Outsider")
        net = Net(slug=NET_SLUG, name="Test Net", is_public=False)
        session.add_all([net_control, viewer, outsider, net])
        session.flush()
        session.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
        session.add(NetMembership(user_callsign="KD0TST", net_id=net.id, role=NetRole.VIEWER))
        session.commit()
        yield {"engine": engine, "factory": factory, "net": net}
    engine.dispose()


@pytest.fixture
def app(test_settings, db_setup):
    from backend.app import create_app

    application = create_app(settings=test_settings)
    application.state.engine = db_setup["engine"]
    application.state.session_factory = db_setup["factory"]
    return application


def _client(app, test_settings, callsign, **kwargs):
    token = make_test_token(callsign, test_settings, **kwargs)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", cookies={"access_token": token})


@pytest.fixture
async def nc_client(app, test_settings):
    async with _client(app, test_settings, "W0NC") as c:
        yield c


@pytest.fixture
async def viewer_client(app, test_settings):
    async with _client(app, test_settings, "KD0TST") as c:
        yield c


@pytest.fixture
async def outsider_client(app, test_settings):
    async with _client(app, test_settings, "W0OUT") as c:
        yield c


DRAFT_BODY = {"name": "Marathon", "event_type": "public_service"}
ACTIVE_BODY = {"name": "Tornado", "event_type": "emergency", "activate": True}


class TestLifecycleRoutes:
    async def test_create_draft(self, nc_client):
        resp = await nc_client.post(BASE, json=DRAFT_BODY)
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "draft"
        assert body["event_type"] == "public_service"
        assert body["created_by"] == "W0NC"

    async def test_create_and_activate(self, nc_client):
        resp = await nc_client.post(BASE, json=ACTIVE_BODY)
        assert resp.status_code == 201
        assert resp.json()["status"] == "active"

    async def test_list_events(self, nc_client):
        await nc_client.post(BASE, json=DRAFT_BODY)
        await nc_client.post(BASE, json=ACTIVE_BODY)
        resp = await nc_client.get(BASE)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_get_snapshot(self, nc_client):
        event_id = (await nc_client.post(BASE, json=ACTIVE_BODY)).json()["id"]
        resp = await nc_client.get(f"{BASE}/{event_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["event"]["id"] == event_id
        assert body["posts"] == []
        assert body["participants"] == []
        assert len(body["log"]) == 1  # "Event activated"

    async def test_activate_close_reopen(self, nc_client):
        event_id = (await nc_client.post(BASE, json=DRAFT_BODY)).json()["id"]
        assert (await nc_client.post(f"{BASE}/{event_id}/activate")).status_code == 200
        assert (await nc_client.post(f"{BASE}/{event_id}/activate")).status_code == 409
        assert (await nc_client.post(f"{BASE}/{event_id}/close")).status_code == 200
        assert (await nc_client.post(f"{BASE}/{event_id}/close")).status_code == 409
        resp = await nc_client.post(f"{BASE}/{event_id}/reopen")
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    async def test_patch_event(self, nc_client):
        event_id = (await nc_client.post(BASE, json=DRAFT_BODY)).json()["id"]
        resp = await nc_client.patch(f"{BASE}/{event_id}", json={"name": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    async def test_patch_closed_event_409(self, nc_client):
        event_id = (await nc_client.post(BASE, json=ACTIVE_BODY)).json()["id"]
        await nc_client.post(f"{BASE}/{event_id}/close")
        resp = await nc_client.patch(f"{BASE}/{event_id}", json={"name": "Nope"})
        assert resp.status_code == 409

    async def test_missing_event_404(self, nc_client):
        assert (await nc_client.get(f"{BASE}/9999")).status_code == 404


class TestPostRoutes:
    async def test_post_crud(self, nc_client):
        event_id = (await nc_client.post(BASE, json=ACTIVE_BODY)).json()["id"]
        resp = await nc_client.post(f"{BASE}/{event_id}/posts", json={"name": "EOC", "lat": 39.1, "lon": -94.6})
        assert resp.status_code == 201
        post_id = resp.json()["id"]

        dup = await nc_client.post(f"{BASE}/{event_id}/posts", json={"name": "EOC"})
        assert dup.status_code == 409

        resp = await nc_client.patch(f"{BASE}/{event_id}/posts/{post_id}", json={"name": "EOC Main"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "EOC Main"

        resp = await nc_client.delete(f"{BASE}/{event_id}/posts/{post_id}")
        assert resp.status_code == 204


class TestPermissions:
    async def test_viewer_can_read(self, nc_client, viewer_client):
        event_id = (await nc_client.post(BASE, json=ACTIVE_BODY)).json()["id"]
        assert (await viewer_client.get(BASE)).status_code == 200
        assert (await viewer_client.get(f"{BASE}/{event_id}")).status_code == 200

    async def test_viewer_cannot_write(self, nc_client, viewer_client):
        event_id = (await nc_client.post(BASE, json=ACTIVE_BODY)).json()["id"]
        assert (await viewer_client.post(BASE, json=DRAFT_BODY)).status_code == 403
        assert (await viewer_client.post(f"{BASE}/{event_id}/close")).status_code == 403
        assert (await viewer_client.post(f"{BASE}/{event_id}/posts", json={"name": "X"})).status_code == 403

    async def test_outsider_denied(self, outsider_client):
        assert (await outsider_client.get(BASE)).status_code == 403

    async def test_anonymous_denied(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as anon:
            assert (await anon.get(BASE)).status_code == 401
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/test_event_lifecycle_routes.py -q`
Expected: FAIL — 404s everywhere (router not registered / module missing)

- [ ] **Step 3: Write the routes**

```python
# backend/modules/events/routes.py
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import NetContext, get_db_session, require_net_role
from backend.modules.events.models import (
    Event,
    EventLogEntry,
    EventParticipant,
    EventPost,
    EventType,
)
from backend.modules.events.service import (
    EventError,
    InvalidPostError,
    InvalidStatusTransitionError,
    activate_event as activate_event_service,
    close_event as close_event_service,
    create_event as create_event_service,
    create_post as create_post_service,
    delete_post as delete_post_service,
    reopen_event as reopen_event_service,
    update_event as update_event_service,
    update_post as update_post_service,
)
from backend.modules.nets.models import NetRole

events_router = APIRouter(prefix="/api/nets/{net_slug}/events", tags=["events"])


# --- Pydantic schemas ---


class EventCreate(BaseModel):
    name: str
    event_type: EventType
    description: str | None = None
    scheduled_start: datetime | None = None
    activate: bool = False


class EventUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    scheduled_start: datetime | None = None


class PostCreate(BaseModel):
    name: str
    description: str | None = None
    lat: float | None = None
    lon: float | None = None


class PostUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    lat: float | None = None
    lon: float | None = None


# --- Helpers ---


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _event_to_response(event: Event) -> dict:
    return {
        "id": event.id,
        "net_id": event.net_id,
        "name": event.name,
        "description": event.description,
        "event_type": event.event_type.value,
        "status": event.status.value,
        "scheduled_start": _iso(event.scheduled_start),
        "activated_at": _iso(event.activated_at),
        "closed_at": _iso(event.closed_at),
        "created_by": event.created_by,
        "created_at": _iso(event.created_at),
    }


def _post_to_response(post: EventPost) -> dict:
    return {
        "id": post.id,
        "event_id": post.event_id,
        "name": post.name,
        "description": post.description,
        "lat": post.lat,
        "lon": post.lon,
    }


def _participant_to_response(p: EventParticipant) -> dict:
    return {
        "id": p.id,
        "event_id": p.event_id,
        "callsign": p.callsign,
        "name": p.name,
        "post_id": p.post_id,
        "location": p.location,
        "current_status": p.current_status.value,
        "checked_in_at": _iso(p.checked_in_at),
        "checked_out_at": _iso(p.checked_out_at),
    }


def _log_to_response(entry: EventLogEntry) -> dict:
    return {
        "id": entry.id,
        "seq": entry.seq,
        "entry_type": entry.entry_type.value,
        "callsign": entry.callsign,
        "actor": entry.actor,
        "message": entry.message,
        "new_status": entry.new_status.value if entry.new_status else None,
        "pinned": entry.pinned,
        "created_at": _iso(entry.created_at),
    }


def _get_event_or_404(db: Session, net_id: int, event_id: int) -> Event:
    event = db.get(Event, event_id)
    if event is None or event.net_id != net_id:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def _raise_for(err: EventError):
    if isinstance(err, (InvalidStatusTransitionError, InvalidPostError)):
        raise HTTPException(status_code=422, detail=str(err))
    raise HTTPException(status_code=409, detail=str(err))


def _snapshot(db: Session, event: Event) -> dict:
    posts = db.query(EventPost).filter(EventPost.event_id == event.id).order_by(EventPost.name).all()
    participants = (
        db.query(EventParticipant)
        .filter(EventParticipant.event_id == event.id)
        .order_by(EventParticipant.callsign)
        .all()
    )
    log = (
        db.query(EventLogEntry)
        .filter(EventLogEntry.event_id == event.id)
        .order_by(EventLogEntry.seq)
        .all()
    )
    return {
        "event": _event_to_response(event),
        "posts": [_post_to_response(p) for p in posts],
        "participants": [_participant_to_response(p) for p in participants],
        "log": [_log_to_response(e) for e in log],
    }


# --- Event lifecycle routes ---


@events_router.get("")
async def list_events_route(
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    events = db.query(Event).filter(Event.net_id == ctx.net.id).order_by(Event.created_at.desc()).all()
    return [_event_to_response(e) for e in events]


@events_router.post("", status_code=201)
async def create_event_route(
    body: EventCreate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    event = create_event_service(
        db,
        net_id=ctx.net.id,
        name=body.name,
        event_type=body.event_type,
        created_by=ctx.user.callsign,
        description=body.description,
        scheduled_start=body.scheduled_start,
        activate=body.activate,
    )
    return _event_to_response(event)


@events_router.get("/{event_id}")
async def get_event_route(
    event_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    event = _get_event_or_404(db, ctx.net.id, event_id)
    return _snapshot(db, event)


@events_router.patch("/{event_id}")
async def update_event_route(
    event_id: int,
    body: EventUpdate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    data = body.model_dump(exclude_unset=True)
    try:
        event = update_event_service(db, event_id, **data)
    except EventError as err:
        _raise_for(err)
    return _event_to_response(event)


@events_router.post("/{event_id}/activate")
async def activate_event_route(
    event_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        event = activate_event_service(db, event_id, actor=ctx.user.callsign)
    except EventError as err:
        _raise_for(err)
    return _event_to_response(event)


@events_router.post("/{event_id}/close")
async def close_event_route(
    event_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        event = close_event_service(db, event_id, actor=ctx.user.callsign)
    except EventError as err:
        _raise_for(err)
    return _event_to_response(event)


@events_router.post("/{event_id}/reopen")
async def reopen_event_route(
    event_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        event = reopen_event_service(db, event_id, actor=ctx.user.callsign)
    except EventError as err:
        _raise_for(err)
    return _event_to_response(event)


# --- Post routes ---


@events_router.post("/{event_id}/posts", status_code=201)
async def create_post_route(
    event_id: int,
    body: PostCreate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        post = create_post_service(
            db, event_id, name=body.name, description=body.description, lat=body.lat, lon=body.lon
        )
    except EventError as err:
        _raise_for(err)
    return _post_to_response(post)


@events_router.patch("/{event_id}/posts/{post_id}")
async def update_post_route(
    event_id: int,
    post_id: int,
    body: PostUpdate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    data = body.model_dump(exclude_unset=True)
    try:
        post = update_post_service(db, event_id, post_id, **data)
    except EventError as err:
        _raise_for(err)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return _post_to_response(post)


@events_router.delete("/{event_id}/posts/{post_id}", status_code=204)
async def delete_post_route(
    event_id: int,
    post_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        deleted = delete_post_service(db, event_id, post_id)
    except EventError as err:
        _raise_for(err)
    if not deleted:
        raise HTTPException(status_code=404, detail="Post not found")
```

- [ ] **Step 4: Register the router in app.py**

In `backend/app.py`, after `from backend.modules.forms.routes import forms_router` add:

```python
from backend.modules.events.routes import events_router
```

and after `app.include_router(roster_router)  # prefix: /api/nets/{net_slug}/roster` add:

```python
app.include_router(events_router)  # prefix: /api/nets/{net_slug}/events
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `.venv/bin/pytest tests/test_event_lifecycle_routes.py -q`
Expected: all pass

- [ ] **Step 6: Full suite + lint + commit**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add backend/modules/events/routes.py backend/app.py tests/test_event_lifecycle_routes.py
git commit -m "feat(events): lifecycle and posts API routes"
```

---

### Task 5: API routes — participants, log, updates, report

**Files:**
- Modify: `backend/modules/events/routes.py` (append)
- Test: `tests/test_event_participant_routes.py`

**Interfaces:**
- Consumes: Task 4 helpers (`_get_event_or_404`, `_raise_for`, response helpers, `_snapshot`); Task 3 service functions.
- Produces routes:
  - `POST /{event_id}/participants` (201) — body `{callsign, name?, post_id?, location?}`
  - `PATCH /{event_id}/participants/{participant_id}` — body any of `{status, post_id, location, name}` (exclude_unset semantics; explicit `"post_id": null` unassigns)
  - `POST /{event_id}/log` (201) — body `{message, callsign?, pinned?}`
  - `PATCH /{event_id}/log/{entry_id}` — body `{pinned}` only
  - `GET /{event_id}/updates?since=N` — `{event, posts, participants, log (seq>N), latest_seq}`
  - `GET /{event_id}/report` — `{participants: [{callsign, name, post, location, stints, total_seconds}]}`

- [ ] **Step 1: Write failing route tests**

```python
# tests/test_event_participant_routes.py
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.nets.models import Net, NetMembership, NetRole
from tests.conftest import make_test_token

NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/events"


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)


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
        nc = User(callsign="W0NC", oidc_subject="auth0|nc", name="NC")
        viewer = User(callsign="KD0TST", oidc_subject="auth0|v", name="V")
        net = Net(slug=NET_SLUG, name="Test Net", is_public=False)
        session.add_all([nc, viewer, net])
        session.flush()
        session.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
        session.add(NetMembership(user_callsign="KD0TST", net_id=net.id, role=NetRole.VIEWER))
        session.commit()
        yield {"engine": engine, "factory": factory}
    engine.dispose()


@pytest.fixture
def app(test_settings, db_setup):
    from backend.app import create_app

    application = create_app(settings=test_settings)
    application.state.engine = db_setup["engine"]
    application.state.session_factory = db_setup["factory"]
    return application


@pytest.fixture
async def nc_client(app, test_settings):
    token = make_test_token("W0NC", test_settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"access_token": token}) as c:
        yield c


@pytest.fixture
async def viewer_client(app, test_settings):
    token = make_test_token("KD0TST", test_settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"access_token": token}) as c:
        yield c


@pytest.fixture
async def active_event(nc_client):
    resp = await nc_client.post(BASE, json={"name": "SKYWARN", "event_type": "emergency", "activate": True})
    return resp.json()["id"]


class TestParticipantRoutes:
    async def test_check_in(self, nc_client, active_event):
        resp = await nc_client.post(f"{BASE}/{active_event}/participants", json={"callsign": "ke0xyz"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["callsign"] == "KE0XYZ"
        assert body["current_status"] == "checked_in"

    async def test_duplicate_check_in_409(self, nc_client, active_event):
        await nc_client.post(f"{BASE}/{active_event}/participants", json={"callsign": "KE0XYZ"})
        resp = await nc_client.post(f"{BASE}/{active_event}/participants", json={"callsign": "KE0XYZ"})
        assert resp.status_code == 409

    async def test_status_change_and_invalid_transition(self, nc_client, active_event):
        pid = (await nc_client.post(
            f"{BASE}/{active_event}/participants", json={"callsign": "KE0XYZ"}
        )).json()["id"]
        resp = await nc_client.patch(
            f"{BASE}/{active_event}/participants/{pid}", json={"status": "checked_out"}
        )
        assert resp.status_code == 200
        assert resp.json()["current_status"] == "checked_out"
        resp = await nc_client.patch(
            f"{BASE}/{active_event}/participants/{pid}", json={"status": "at_post"}
        )
        assert resp.status_code == 422

    async def test_unassign_post_via_null(self, nc_client, active_event):
        post_id = (await nc_client.post(
            f"{BASE}/{active_event}/posts", json={"name": "EOC"}
        )).json()["id"]
        pid = (await nc_client.post(
            f"{BASE}/{active_event}/participants",
            json={"callsign": "KE0XYZ", "post_id": post_id},
        )).json()["id"]
        resp = await nc_client.patch(
            f"{BASE}/{active_event}/participants/{pid}", json={"post_id": None}
        )
        assert resp.status_code == 200
        assert resp.json()["post_id"] is None

    async def test_write_on_closed_event_409(self, nc_client, active_event):
        await nc_client.post(f"{BASE}/{active_event}/close")
        resp = await nc_client.post(f"{BASE}/{active_event}/participants", json={"callsign": "KE0XYZ"})
        assert resp.status_code == 409

    async def test_viewer_cannot_write(self, viewer_client, active_event):
        resp = await viewer_client.post(f"{BASE}/{active_event}/participants", json={"callsign": "KE0XYZ"})
        assert resp.status_code == 403


class TestLogRoutes:
    async def test_add_note(self, nc_client, active_event):
        resp = await nc_client.post(f"{BASE}/{active_event}/log", json={"message": "Course clear"})
        assert resp.status_code == 201
        assert resp.json()["entry_type"] == "note"

    async def test_participant_note_with_pin(self, nc_client, active_event):
        await nc_client.post(f"{BASE}/{active_event}/participants", json={"callsign": "KE0XYZ"})
        resp = await nc_client.post(
            f"{BASE}/{active_event}/log",
            json={"message": "Medic", "callsign": "ke0xyz", "pinned": True},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["entry_type"] == "participant_note"
        assert body["callsign"] == "KE0XYZ"
        assert body["pinned"] is True

    async def test_pin_toggle(self, nc_client, active_event):
        entry_id = (await nc_client.post(
            f"{BASE}/{active_event}/log", json={"message": "x"}
        )).json()["id"]
        resp = await nc_client.patch(f"{BASE}/{active_event}/log/{entry_id}", json={"pinned": True})
        assert resp.status_code == 200
        assert resp.json()["pinned"] is True


class TestUpdatesRoute:
    async def test_cursor_semantics(self, nc_client, viewer_client, active_event):
        # since=0 → full log (activation entry)
        resp = await viewer_client.get(f"{BASE}/{active_event}/updates", params={"since": 0})
        assert resp.status_code == 200
        body = resp.json()
        assert body["latest_seq"] == 1
        assert len(body["log"]) == 1

        await nc_client.post(f"{BASE}/{active_event}/participants", json={"callsign": "KE0XYZ"})

        # catch-up from previous cursor → only the new entry
        resp = await viewer_client.get(f"{BASE}/{active_event}/updates", params={"since": 1})
        body = resp.json()
        assert body["latest_seq"] == 2
        assert len(body["log"]) == 1
        assert body["log"][0]["seq"] == 2
        # state always included whole
        assert len(body["participants"]) == 1

        # up-to-date cursor → empty delta
        resp = await viewer_client.get(f"{BASE}/{active_event}/updates", params={"since": 2})
        assert resp.json()["log"] == []


class TestReportRoute:
    async def test_report(self, nc_client, viewer_client, active_event):
        pid = (await nc_client.post(
            f"{BASE}/{active_event}/participants", json={"callsign": "KE0XYZ"}
        )).json()["id"]
        await nc_client.patch(
            f"{BASE}/{active_event}/participants/{pid}", json={"status": "checked_out"}
        )
        resp = await viewer_client.get(f"{BASE}/{active_event}/report")
        assert resp.status_code == 200
        participants = resp.json()["participants"]
        assert len(participants) == 1
        assert participants[0]["callsign"] == "KE0XYZ"
        assert len(participants[0]["stints"]) == 1
        assert participants[0]["stints"][0]["end"] is not None
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/test_event_participant_routes.py -q`
Expected: FAIL — 404/405 (routes missing)

- [ ] **Step 3: Append routes**

Extend the service import block in `backend/modules/events/routes.py` with:

```python
    add_note as add_note_service,
    check_in as check_in_service,
    compute_report as compute_report_service,
    set_log_pinned as set_log_pinned_service,
    update_participant as update_participant_service,
```

Add `Query` to the fastapi import and `ParticipantStatus` to the models import. Append the schemas and routes:

```python
# --- Participant / log schemas ---


class ParticipantCheckIn(BaseModel):
    callsign: str
    name: str | None = None
    post_id: int | None = None
    location: str | None = None


class ParticipantUpdate(BaseModel):
    status: ParticipantStatus | None = None
    post_id: int | None = None
    location: str | None = None
    name: str | None = None


class NoteCreate(BaseModel):
    message: str
    callsign: str | None = None
    pinned: bool = False


class LogPinUpdate(BaseModel):
    pinned: bool


# --- Participant routes ---


@events_router.post("/{event_id}/participants", status_code=201)
async def check_in_route(
    event_id: int,
    body: ParticipantCheckIn,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        participant = check_in_service(
            db, event_id,
            callsign=body.callsign,
            actor=ctx.user.callsign,
            name=body.name,
            post_id=body.post_id,
            location=body.location,
        )
    except EventError as err:
        _raise_for(err)
    return _participant_to_response(participant)


@events_router.patch("/{event_id}/participants/{participant_id}")
async def update_participant_route(
    event_id: int,
    participant_id: int,
    body: ParticipantUpdate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    data = body.model_dump(exclude_unset=True)
    try:
        participant = update_participant_service(
            db, event_id, participant_id, actor=ctx.user.callsign, **data
        )
    except EventError as err:
        _raise_for(err)
    if participant is None:
        raise HTTPException(status_code=404, detail="Participant not found")
    return _participant_to_response(participant)


# --- Log routes ---


@events_router.post("/{event_id}/log", status_code=201)
async def add_note_route(
    event_id: int,
    body: NoteCreate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        entry = add_note_service(
            db, event_id,
            actor=ctx.user.callsign,
            message=body.message,
            callsign=body.callsign,
            pinned=body.pinned,
        )
    except EventError as err:
        _raise_for(err)
    return _log_to_response(entry)


@events_router.patch("/{event_id}/log/{entry_id}")
async def pin_log_route(
    event_id: int,
    entry_id: int,
    body: LogPinUpdate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    entry = set_log_pinned_service(db, event_id, entry_id, body.pinned)
    if entry is None:
        raise HTTPException(status_code=404, detail="Log entry not found")
    return _log_to_response(entry)


# --- Updates (polling cursor) + report ---


@events_router.get("/{event_id}/updates")
async def updates_route(
    event_id: int,
    since: int = Query(default=0, ge=0),
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    event = _get_event_or_404(db, ctx.net.id, event_id)
    snapshot = _snapshot(db, event)
    log = (
        db.query(EventLogEntry)
        .filter(EventLogEntry.event_id == event.id, EventLogEntry.seq > since)
        .order_by(EventLogEntry.seq)
        .all()
    )
    snapshot["log"] = [_log_to_response(e) for e in log]
    snapshot["latest_seq"] = event.log_seq
    return snapshot


@events_router.get("/{event_id}/report")
async def report_route(
    event_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    event = _get_event_or_404(db, ctx.net.id, event_id)
    return {"participants": compute_report_service(db, event)}
```

Note: `_snapshot` already queries the full log; the updates route overwrites the `log` key with the cursored query. If that double query bothers you, refactor `_snapshot` to take an optional `since` — but keep behavior identical.

- [ ] **Step 4: Run tests, verify they pass**

Run: `.venv/bin/pytest tests/test_event_participant_routes.py tests/test_event_lifecycle_routes.py -q`
Expected: all pass

- [ ] **Step 5: Full suite + lint + commit**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add backend/modules/events/routes.py tests/test_event_participant_routes.py
git commit -m "feat(events): participant, log, updates, and report API routes"
```

---

### Task 6: Frontend types + API client

**Files:**
- Modify: `frontend/src/types/index.ts` (append)
- Create: `frontend/src/api/events.ts`

**Interfaces:**
- Consumes: `apiFetch` from `frontend/src/api/client.ts`; backend response shapes from Tasks 4–5.
- Produces: types `NetEvent`, `EventPost`, `EventParticipant`, `EventLogEntry`, `EventUpdates`, `EventSnapshot`, `EventReportParticipant`, unions `EventType`/`EventStatus`/`ParticipantStatus`/`EventLogType`; all fetch functions in `api/events.ts` (names below). Tasks 7–10 import these.

- [ ] **Step 1: Append types**

Append to `frontend/src/types/index.ts`:

```typescript
// --- Live events ---

export type EventType = "public_service" | "emergency";
export type EventStatus = "draft" | "active" | "closed";
export type ParticipantStatus =
  | "checked_in"
  | "at_post"
  | "en_route"
  | "out_of_service"
  | "checked_out";
export type EventLogType = "system" | "note" | "participant_note";

/** Named NetEvent to avoid clashing with the DOM Event type. */
export interface NetEvent {
  id: number;
  net_id: number;
  name: string;
  description: string | null;
  event_type: EventType;
  status: EventStatus;
  scheduled_start: string | null;
  activated_at: string | null;
  closed_at: string | null;
  created_by: string;
  created_at: string;
}

export interface EventPost {
  id: number;
  event_id: number;
  name: string;
  description: string | null;
  lat: number | null;
  lon: number | null;
}

export interface EventParticipant {
  id: number;
  event_id: number;
  callsign: string;
  name: string | null;
  post_id: number | null;
  location: string | null;
  current_status: ParticipantStatus;
  checked_in_at: string;
  checked_out_at: string | null;
}

export interface EventLogEntry {
  id: number;
  seq: number;
  entry_type: EventLogType;
  callsign: string | null;
  actor: string;
  message: string;
  new_status: ParticipantStatus | null;
  pinned: boolean;
  created_at: string;
}

export interface EventSnapshot {
  event: NetEvent;
  posts: EventPost[];
  participants: EventParticipant[];
  log: EventLogEntry[];
}

export interface EventUpdates extends EventSnapshot {
  latest_seq: number;
}

export interface EventStint {
  start: string;
  end: string | null;
}

export interface EventReportParticipant {
  callsign: string;
  name: string | null;
  post: string | null;
  location: string | null;
  stints: EventStint[];
  total_seconds: number;
}
```

- [ ] **Step 2: Write the API module**

```typescript
// frontend/src/api/events.ts
import { apiFetch } from "./client";
import type {
  EventLogEntry,
  EventParticipant,
  EventPost,
  EventReportParticipant,
  EventSnapshot,
  EventType,
  EventUpdates,
  NetEvent,
  ParticipantStatus,
} from "../types";

// --- Events ---

export interface EventCreateInput {
  name: string;
  event_type: EventType;
  description?: string | null;
  scheduled_start?: string | null;
  activate?: boolean;
}

export async function fetchEvents(netSlug: string): Promise<NetEvent[]> {
  return apiFetch<NetEvent[]>(`/nets/${netSlug}/events`);
}

export async function createEvent(input: EventCreateInput, netSlug: string): Promise<NetEvent> {
  return apiFetch<NetEvent>(`/nets/${netSlug}/events`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function fetchEvent(id: number, netSlug: string): Promise<EventSnapshot> {
  return apiFetch<EventSnapshot>(`/nets/${netSlug}/events/${id}`);
}

export async function updateEvent(
  id: number,
  body: Partial<Pick<NetEvent, "name" | "description" | "scheduled_start">>,
  netSlug: string,
): Promise<NetEvent> {
  return apiFetch<NetEvent>(`/nets/${netSlug}/events/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function activateEvent(id: number, netSlug: string): Promise<NetEvent> {
  return apiFetch<NetEvent>(`/nets/${netSlug}/events/${id}/activate`, { method: "POST" });
}

export async function closeEvent(id: number, netSlug: string): Promise<NetEvent> {
  return apiFetch<NetEvent>(`/nets/${netSlug}/events/${id}/close`, { method: "POST" });
}

export async function reopenEvent(id: number, netSlug: string): Promise<NetEvent> {
  return apiFetch<NetEvent>(`/nets/${netSlug}/events/${id}/reopen`, { method: "POST" });
}

// --- Posts ---

export interface PostInput {
  name?: string;
  description?: string | null;
  lat?: number | null;
  lon?: number | null;
}

export async function createEventPost(
  eventId: number,
  input: PostInput & { name: string },
  netSlug: string,
): Promise<EventPost> {
  return apiFetch<EventPost>(`/nets/${netSlug}/events/${eventId}/posts`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateEventPost(
  eventId: number,
  postId: number,
  input: PostInput,
  netSlug: string,
): Promise<EventPost> {
  return apiFetch<EventPost>(`/nets/${netSlug}/events/${eventId}/posts/${postId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteEventPost(eventId: number, postId: number, netSlug: string): Promise<void> {
  return apiFetch<void>(`/nets/${netSlug}/events/${eventId}/posts/${postId}`, { method: "DELETE" });
}

// --- Participants ---

export interface CheckInInput {
  callsign: string;
  name?: string | null;
  post_id?: number | null;
  location?: string | null;
}

export async function checkInParticipant(
  eventId: number,
  input: CheckInInput,
  netSlug: string,
): Promise<EventParticipant> {
  return apiFetch<EventParticipant>(`/nets/${netSlug}/events/${eventId}/participants`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export interface ParticipantUpdateInput {
  status?: ParticipantStatus;
  post_id?: number | null;
  location?: string | null;
  name?: string | null;
}

export async function updateParticipant(
  eventId: number,
  participantId: number,
  input: ParticipantUpdateInput,
  netSlug: string,
): Promise<EventParticipant> {
  return apiFetch<EventParticipant>(
    `/nets/${netSlug}/events/${eventId}/participants/${participantId}`,
    { method: "PATCH", body: JSON.stringify(input) },
  );
}

// --- Log ---

export interface NoteInput {
  message: string;
  callsign?: string | null;
  pinned?: boolean;
}

export async function addEventNote(
  eventId: number,
  input: NoteInput,
  netSlug: string,
): Promise<EventLogEntry> {
  return apiFetch<EventLogEntry>(`/nets/${netSlug}/events/${eventId}/log`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function setEventLogPinned(
  eventId: number,
  entryId: number,
  pinned: boolean,
  netSlug: string,
): Promise<EventLogEntry> {
  return apiFetch<EventLogEntry>(`/nets/${netSlug}/events/${eventId}/log/${entryId}`, {
    method: "PATCH",
    body: JSON.stringify({ pinned }),
  });
}

// --- Updates + report ---

export async function fetchEventUpdates(
  eventId: number,
  since: number,
  netSlug: string,
): Promise<EventUpdates> {
  return apiFetch<EventUpdates>(`/nets/${netSlug}/events/${eventId}/updates?since=${since}`);
}

export async function fetchEventReport(
  eventId: number,
  netSlug: string,
): Promise<{ participants: EventReportParticipant[] }> {
  return apiFetch<{ participants: EventReportParticipant[] }>(
    `/nets/${netSlug}/events/${eventId}/report`,
  );
}
```

- [ ] **Step 3: Type-check via build**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: builds cleanly (tsc + vite, no errors)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/api/events.ts
git commit -m "feat(events): frontend types and API client"
```

---

### Task 7: Events list page + routing + nav

**Files:**
- Create: `frontend/src/pages/events/EventsPage.tsx`
- Modify: `frontend/src/App.tsx` (routes)
- Modify: `frontend/src/layouts/Sidebar.tsx` (nav item)

**Interfaces:**
- Consumes: `fetchEvents`, `createEvent` from Task 6; `useCurrentNet`, `Button`, `Input`, `Modal`, `Spinner` components.
- Produces: route `/nets/:slug/events` (min role viewer); dashboard route stubs are added in Task 8 — this task links each row to `/nets/${slug}/events/${id}` even though the target 404s until Task 8.

- [ ] **Step 1: Write the page**

```tsx
// frontend/src/pages/events/EventsPage.tsx
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { createEvent, fetchEvents } from "../../api/events";
import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Modal } from "../../components/Modal";
import { Spinner } from "../../components/Spinner";
import { useCurrentNet } from "../../hooks/useCurrentNet";
import type { EventStatus, EventType, NetEvent } from "../../types";

const STATUS_BADGE: Record<EventStatus, string> = {
  draft: "bg-bg-elevated text-text-muted",
  active: "bg-success/15 text-success",
  closed: "bg-bg-elevated text-text-secondary",
};

const TYPE_LABEL: Record<EventType, string> = {
  public_service: "Public service",
  emergency: "Emergency",
};

export function EventsPage() {
  const { slug } = useParams<{ slug: string }>();
  const { role } = useCurrentNet();
  const canWrite = role === "net_control" || role === "admin";

  const [events, setEvents] = useState<NetEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  // Create form state
  const [name, setName] = useState("");
  const [eventType, setEventType] = useState<EventType>("public_service");
  const [description, setDescription] = useState("");
  const [scheduledStart, setScheduledStart] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    if (!slug) return;
    try {
      setEvents(await fetchEvents(slug));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load events");
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => {
    void load();
  }, [load]);

  async function submit(activate: boolean) {
    if (!slug || !name.trim()) return;
    setSaving(true);
    try {
      await createEvent(
        {
          name: name.trim(),
          event_type: eventType,
          description: description.trim() || null,
          scheduled_start: scheduledStart ? new Date(scheduledStart).toISOString() : null,
          activate,
        },
        slug,
      );
      setShowCreate(false);
      setName("");
      setDescription("");
      setScheduledStart("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create event");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner size="lg" />
      </div>
    );
  }

  return (
    <div className="p-4 md:p-6 max-w-5xl">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-text-primary">Events</h1>
        {canWrite && <Button onClick={() => setShowCreate(true)}>New event</Button>}
      </div>

      {error && <p className="text-danger text-sm mb-3">{error}</p>}

      {events.length === 0 ? (
        <p className="text-text-muted text-sm">No events yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-text-muted border-b border-border">
              <th className="py-2 pr-4">Name</th>
              <th className="py-2 pr-4">Type</th>
              <th className="py-2 pr-4">Status</th>
              <th className="py-2 pr-4">Scheduled</th>
              <th className="py-2">Activated</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.id} className="border-b border-border hover:bg-bg-elevated">
                <td className="py-2 pr-4">
                  <Link to={`/nets/${slug}/events/${event.id}`} className="text-accent hover:underline">
                    {event.name}
                  </Link>
                </td>
                <td className="py-2 pr-4 text-text-secondary">{TYPE_LABEL[event.event_type]}</td>
                <td className="py-2 pr-4">
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_BADGE[event.status]}`}>
                    {event.status}
                  </span>
                </td>
                <td className="py-2 pr-4 text-text-muted">
                  {event.scheduled_start ? new Date(event.scheduled_start).toLocaleString() : "—"}
                </td>
                <td className="py-2 text-text-muted">
                  {event.activated_at ? new Date(event.activated_at).toLocaleString() : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="New event">
        <div className="flex flex-col gap-3">
          <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} />
          <label className="text-sm text-text-secondary">
            Type
            <select
              value={eventType}
              onChange={(e) => setEventType(e.target.value as EventType)}
              className="mt-1 block w-full rounded-md bg-bg-elevated border border-border px-3 py-2 text-sm text-text-primary"
            >
              <option value="public_service">Public service</option>
              <option value="emergency">Emergency</option>
            </select>
          </label>
          <Input label="Description" value={description} onChange={(e) => setDescription(e.target.value)} />
          <Input
            label="Scheduled start"
            type="datetime-local"
            value={scheduledStart}
            onChange={(e) => setScheduledStart(e.target.value)}
          />
          <div className="flex gap-2 justify-end pt-2">
            <Button variant="secondary" onClick={() => setShowCreate(false)}>Cancel</Button>
            <Button variant="secondary" loading={saving} onClick={() => void submit(false)}>
              Create draft
            </Button>
            <Button loading={saving} onClick={() => void submit(true)}>
              Create &amp; activate now
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
```

- [ ] **Step 2: Wire routes and nav**

In `frontend/src/App.tsx`:
- Add import: `import { EventsPage } from "./pages/events/EventsPage";`
- In the auth-required per-net route group (after the `members` route), add:

```tsx
<Route path="events" element={<RequireNetRole min="viewer"><EventsPage /></RequireNetRole>} />
```

- In the slug-less aliases block, add:

```tsx
<Route path="/events" element={<SlugRedirect to="events" />} />
```

In `frontend/src/layouts/Sidebar.tsx`, add to `netNavItems` after the Members entry:

```tsx
  { label: "Events",     subpath: "events",     minRole: "viewer" },
```

Check `frontend/src/layouts/MobileMenu.tsx` — if it has its own nav-item list, add the same entry there.

- [ ] **Step 3: Build check**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: builds cleanly

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/events/EventsPage.tsx frontend/src/App.tsx frontend/src/layouts/
git commit -m "feat(events): events list page, routing, and nav"
```

---

### Task 8: Event dashboard — polling hook + read-only view

**Files:**
- Create: `frontend/src/hooks/useEventUpdates.ts`
- Create: `frontend/src/pages/events/EventDashboardPage.tsx`
- Create: `frontend/src/pages/events/ParticipantBoard.tsx`
- Create: `frontend/src/pages/events/NetLogPanel.tsx`
- Modify: `frontend/src/App.tsx` (dashboard route)

**Interfaces:**
- Consumes: Task 6 API + types.
- Produces:
  - `useEventUpdates(netSlug: string, eventId: number): { updates: EventUpdates | null; connected: boolean; refresh: () => Promise<void> }` — polls every 3s while the event is `active` and the tab visible; accumulates log entries across polls; `refresh()` forces an immediate poll (Task 9 calls it after every write).
  - `EventDashboardPage` at route `/nets/:slug/events/:eventId`; renders header (name, type, status badge, reconnecting indicator), `ParticipantBoard` (left, with `onSelect`), `NetLogPanel` (right), and a participant detail side panel showing that callsign's filtered log + pinned notes.
  - `ParticipantBoard({ participants, posts, selectedId, onSelect, actions })` and `NetLogPanel({ log, composer })` accept an optional `actions`/`composer` ReactNode slot — Task 9 fills these with NCS controls; this task passes nothing (read-only).

- [ ] **Step 1: Write the polling hook**

```typescript
// frontend/src/hooks/useEventUpdates.ts
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchEventUpdates } from "../api/events";
import type { EventLogEntry, EventUpdates } from "../types";

const POLL_MS = 3000;

/**
 * Cursor-polling live updates for an event dashboard.
 *
 * - First poll uses since=0 (full log), then advances the cursor to
 *   latest_seq; log entries accumulate client-side.
 * - Polls every 3s while the event is active and the tab is visible.
 * - On failure keeps last-known state and flips `connected` false; next
 *   successful poll recovers (never blank the dashboard mid-event).
 */
export function useEventUpdates(netSlug: string, eventId: number) {
  const [updates, setUpdates] = useState<EventUpdates | null>(null);
  const [connected, setConnected] = useState(true);
  const sinceRef = useRef(0);
  const logRef = useRef<EventLogEntry[]>([]);
  const statusRef = useRef<string>("active");

  const refresh = useCallback(async () => {
    try {
      const u = await fetchEventUpdates(eventId, sinceRef.current, netSlug);
      logRef.current = [...logRef.current, ...u.log];
      sinceRef.current = u.latest_seq;
      statusRef.current = u.event.status;
      setUpdates({ ...u, log: logRef.current });
      setConnected(true);
    } catch {
      setConnected(false);
    }
  }, [netSlug, eventId]);

  useEffect(() => {
    sinceRef.current = 0;
    logRef.current = [];
    statusRef.current = "active";
    setUpdates(null);
    void refresh();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible" && statusRef.current === "active") {
        void refresh();
      }
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  return { updates, connected, refresh };
}
```

- [ ] **Step 2: Write ParticipantBoard**

```tsx
// frontend/src/pages/events/ParticipantBoard.tsx
import type { ReactNode } from "react";
import type { EventParticipant, EventPost, ParticipantStatus } from "../../types";

export const STATUS_LABEL: Record<ParticipantStatus, string> = {
  checked_in: "Checked in",
  at_post: "At post",
  en_route: "En route",
  out_of_service: "Out of service",
  checked_out: "Checked out",
};

export const STATUS_BADGE: Record<ParticipantStatus, string> = {
  checked_in: "bg-success/15 text-success",
  at_post: "bg-accent/15 text-accent",
  en_route: "bg-warning/15 text-warning",
  out_of_service: "bg-danger/15 text-danger",
  checked_out: "bg-bg-elevated text-text-muted",
};

interface ParticipantBoardProps {
  participants: EventParticipant[];
  posts: EventPost[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  /** Per-row NCS action cell; Task 9 supplies this. Null = read-only. */
  actions?: (p: EventParticipant) => ReactNode;
}

export function ParticipantBoard({ participants, posts, selectedId, onSelect, actions }: ParticipantBoardProps) {
  const postName = (id: number | null) => posts.find((p) => p.id === id)?.name ?? null;

  if (participants.length === 0) {
    return <p className="text-text-muted text-sm py-6">No participants checked in yet.</p>;
  }

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-text-muted border-b border-border">
          <th className="py-2 pr-3">Callsign</th>
          <th className="py-2 pr-3">Name</th>
          <th className="py-2 pr-3">Status</th>
          <th className="py-2 pr-3">Post / location</th>
          <th className="py-2 pr-3">In since</th>
          {actions && <th className="py-2">Actions</th>}
        </tr>
      </thead>
      <tbody>
        {participants.map((p) => (
          <tr
            key={p.id}
            onClick={() => onSelect(p.id)}
            className={`border-b border-border cursor-pointer ${
              selectedId === p.id ? "bg-accent/5" : "hover:bg-bg-elevated"
            }`}
          >
            <td className="py-2 pr-3 font-mono text-text-primary">{p.callsign}</td>
            <td className="py-2 pr-3 text-text-secondary">{p.name ?? "—"}</td>
            <td className="py-2 pr-3">
              <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_BADGE[p.current_status]}`}>
                {STATUS_LABEL[p.current_status]}
              </span>
            </td>
            <td className="py-2 pr-3 text-text-secondary">
              {postName(p.post_id) ?? p.location ?? "—"}
            </td>
            <td className="py-2 pr-3 text-text-muted">
              {p.current_status === "checked_out"
                ? "—"
                : new Date(p.checked_in_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </td>
            {actions && <td className="py-2" onClick={(e) => e.stopPropagation()}>{actions(p)}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

Note: verify `warning` is a defined Tailwind color token in this project (grep `text-warning` under `frontend/src`); if not, substitute the token the theme actually defines (e.g. amber classes used by `CheckInMap.tsx`).

- [ ] **Step 3: Write NetLogPanel**

```tsx
// frontend/src/pages/events/NetLogPanel.tsx
import type { ReactNode } from "react";
import type { EventLogEntry } from "../../types";

interface NetLogPanelProps {
  log: EventLogEntry[];
  /** Composer element (textarea + submit) supplied by Task 9 for NCS. */
  composer?: ReactNode;
}

export function NetLogPanel({ log, composer }: NetLogPanelProps) {
  const reversed = [...log].sort((a, b) => b.seq - a.seq);

  return (
    <div className="flex flex-col h-full">
      <h2 className="text-sm font-semibold text-text-primary mb-2">Net log</h2>
      {composer}
      <div className="flex-1 overflow-y-auto flex flex-col gap-1 mt-2">
        {reversed.length === 0 && <p className="text-text-muted text-sm">No log entries yet.</p>}
        {reversed.map((entry) => (
          <div
            key={entry.seq}
            className={`rounded px-2 py-1.5 text-sm ${
              entry.entry_type === "system"
                ? "text-text-muted"
                : "bg-bg-elevated text-text-primary"
            }`}
          >
            <span className="text-xs text-text-muted font-mono mr-2">
              {new Date(entry.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </span>
            {entry.entry_type !== "system" && (
              <span className="text-xs text-accent font-mono mr-2">{entry.actor}</span>
            )}
            {entry.pinned && <span className="mr-1" title="Pinned">📌</span>}
            {entry.message}
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Write the dashboard page (read-only)**

```tsx
// frontend/src/pages/events/EventDashboardPage.tsx
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Spinner } from "../../components/Spinner";
import { useCurrentNet } from "../../hooks/useCurrentNet";
import { useEventUpdates } from "../../hooks/useEventUpdates";
import { NetLogPanel } from "./NetLogPanel";
import { ParticipantBoard, STATUS_LABEL } from "./ParticipantBoard";

export function EventDashboardPage() {
  const { slug, eventId } = useParams<{ slug: string; eventId: string }>();
  const { role } = useCurrentNet();
  const canWrite = role === "net_control" || role === "admin";
  const { updates, connected, refresh } = useEventUpdates(slug!, Number(eventId));
  const [selectedId, setSelectedId] = useState<number | null>(null);

  if (!updates) {
    return (
      <div className="flex justify-center py-16">
        <Spinner size="lg" />
      </div>
    );
  }

  const { event, posts, participants, log } = updates;
  const selected = participants.find((p) => p.id === selectedId) ?? null;
  const selectedLog = selected ? log.filter((e) => e.callsign === selected.callsign) : [];
  const pinned = selected ? selectedLog.filter((e) => e.pinned) : [];

  // canWrite/refresh wired up by the NCS-controls task; referenced here so
  // the read-only build stays lint-clean.
  void canWrite;
  void refresh;

  return (
    <div className="p-4 md:p-6">
      {/* Header */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <Link to={`/nets/${slug}/events`} className="text-text-muted hover:text-accent text-sm">
          ← Events
        </Link>
        <h1 className="text-xl font-semibold text-text-primary">{event.name}</h1>
        <span className="px-2 py-0.5 rounded text-xs font-medium bg-bg-elevated text-text-secondary">
          {event.event_type === "public_service" ? "Public service" : "Emergency"}
        </span>
        <span
          className={`px-2 py-0.5 rounded text-xs font-medium ${
            event.status === "active" ? "bg-success/15 text-success" : "bg-bg-elevated text-text-muted"
          }`}
        >
          {event.status}
        </span>
        {!connected && (
          <span className="text-xs text-danger animate-pulse">reconnecting…</span>
        )}
        <div className="ml-auto">
          <Link
            to={`/nets/${slug}/events/${event.id}/report`}
            className="text-sm text-accent hover:underline"
          >
            Report
          </Link>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Participant board */}
        <div className="lg:col-span-2">
          <ParticipantBoard
            participants={participants}
            posts={posts}
            selectedId={selectedId}
            onSelect={(id) => setSelectedId(id === selectedId ? null : id)}
          />

          {/* Participant detail */}
          {selected && (
            <div className="mt-4 rounded-md border border-border bg-bg-surface p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="font-mono font-semibold text-text-primary">{selected.callsign}</span>
                <span className="text-text-secondary text-sm">{selected.name}</span>
                <span className="text-xs text-text-muted">{STATUS_LABEL[selected.current_status]}</span>
              </div>
              {pinned.length > 0 && (
                <div className="mb-2 flex flex-col gap-1">
                  {pinned.map((e) => (
                    <div key={e.seq} className="text-sm bg-warning/10 rounded px-2 py-1">
                      📌 {e.message}
                    </div>
                  ))}
                </div>
              )}
              <div className="flex flex-col gap-1 max-h-64 overflow-y-auto">
                {[...selectedLog].reverse().map((e) => (
                  <div key={e.seq} className="text-sm text-text-secondary">
                    <span className="text-xs text-text-muted font-mono mr-2">
                      {new Date(e.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    </span>
                    {e.message}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Net log */}
        <div className="min-h-[300px] lg:h-[calc(100vh-12rem)]">
          <NetLogPanel log={log} />
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Add the route**

In `frontend/src/App.tsx`, import `EventDashboardPage` and add after the `events` route:

```tsx
<Route path="events/:eventId" element={<RequireNetRole min="viewer"><EventDashboardPage /></RequireNetRole>} />
```

- [ ] **Step 6: Build check + commit**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: builds cleanly

```bash
git add frontend/src/hooks/useEventUpdates.ts frontend/src/pages/events/ frontend/src/App.tsx
git commit -m "feat(events): live dashboard with cursor-polling (read-only)"
```

---

### Task 9: Dashboard NCS controls

**Files:**
- Create: `frontend/src/pages/events/CheckInBar.tsx`
- Create: `frontend/src/pages/events/PostsPanel.tsx`
- Modify: `frontend/src/pages/events/EventDashboardPage.tsx`

**Interfaces:**
- Consumes: Task 6 API functions, Task 8 components (`actions` / `composer` slots), `refresh` from the hook.
- Produces: full NCS write surface — check-in bar (keyboard-first), per-row status quick-actions, participant note composer + pin, net-log composer, posts panel (add/edit/delete), lifecycle buttons (Activate / Close / Reopen). All write handlers `await` the API call then `await refresh()`; API errors surface via toast (use the project's `useToast` hook per `ToastContext.tsx`) without blanking state.

- [ ] **Step 1: Write CheckInBar**

```tsx
// frontend/src/pages/events/CheckInBar.tsx
import { useRef, useState } from "react";
import { checkInParticipant } from "../../api/events";
import { Button } from "../../components/Button";
import type { EventPost } from "../../types";

interface CheckInBarProps {
  netSlug: string;
  eventId: number;
  posts: EventPost[];
  onDone: () => Promise<void>;
  onError: (message: string) => void;
}

/** Keyboard-first check-in: callsign autofocused, Enter submits, focus
 *  returns to the callsign field for the next check-in. */
export function CheckInBar({ netSlug, eventId, posts, onDone, onError }: CheckInBarProps) {
  const [callsign, setCallsign] = useState("");
  const [postId, setPostId] = useState<number | "">("");
  const [location, setLocation] = useState("");
  const [busy, setBusy] = useState(false);
  const callsignRef = useRef<HTMLInputElement>(null);

  async function submit() {
    const cs = callsign.trim().toUpperCase();
    if (!cs || busy) return;
    setBusy(true);
    try {
      await checkInParticipant(
        eventId,
        { callsign: cs, post_id: postId === "" ? null : postId, location: location.trim() || null },
        netSlug,
      );
      setCallsign("");
      setLocation("");
      await onDone();
    } catch (e) {
      onError(e instanceof Error ? e.message : "Check-in failed");
    } finally {
      setBusy(false);
      callsignRef.current?.focus();
    }
  }

  return (
    <div className="flex gap-2 items-end flex-wrap rounded-md border border-border bg-bg-surface p-3 mb-4">
      <label className="text-sm text-text-secondary">
        Callsign
        <input
          ref={callsignRef}
          autoFocus
          value={callsign}
          onChange={(e) => setCallsign(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === "Enter" && void submit()}
          className="mt-1 block w-36 rounded-md bg-bg-elevated border border-border px-3 py-2 text-sm font-mono text-text-primary"
        />
      </label>
      {posts.length > 0 && (
        <label className="text-sm text-text-secondary">
          Post
          <select
            value={postId}
            onChange={(e) => setPostId(e.target.value === "" ? "" : Number(e.target.value))}
            className="mt-1 block w-44 rounded-md bg-bg-elevated border border-border px-3 py-2 text-sm text-text-primary"
          >
            <option value="">—</option>
            {posts.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>
      )}
      <label className="text-sm text-text-secondary flex-1 min-w-40">
        Location
        <input
          value={location}
          onChange={(e) => setLocation(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void submit()}
          className="mt-1 block w-full rounded-md bg-bg-elevated border border-border px-3 py-2 text-sm text-text-primary"
        />
      </label>
      <Button loading={busy} onClick={() => void submit()}>Check in</Button>
    </div>
  );
}
```

- [ ] **Step 2: Write PostsPanel**

```tsx
// frontend/src/pages/events/PostsPanel.tsx
import { useState } from "react";
import { createEventPost, deleteEventPost } from "../../api/events";
import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import type { EventPost } from "../../types";

interface PostsPanelProps {
  netSlug: string;
  eventId: number;
  posts: EventPost[];
  onChanged: () => Promise<void>;
  onError: (message: string) => void;
}

export function PostsPanel({ netSlug, eventId, posts, onChanged, onError }: PostsPanelProps) {
  const [name, setName] = useState("");
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [busy, setBusy] = useState(false);

  async function add() {
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      await createEventPost(
        eventId,
        {
          name: name.trim(),
          lat: lat.trim() ? Number(lat) : null,
          lon: lon.trim() ? Number(lon) : null,
        },
        netSlug,
      );
      setName("");
      setLat("");
      setLon("");
      await onChanged();
    } catch (e) {
      onError(e instanceof Error ? e.message : "Failed to add post");
    } finally {
      setBusy(false);
    }
  }

  async function remove(postId: number) {
    try {
      await deleteEventPost(eventId, postId, netSlug);
      await onChanged();
    } catch (e) {
      onError(e instanceof Error ? e.message : "Failed to delete post (still assigned?)");
    }
  }

  return (
    <div className="rounded-md border border-border bg-bg-surface p-3">
      <h3 className="text-sm font-semibold text-text-primary mb-2">Posts</h3>
      {posts.length > 0 && (
        <ul className="mb-3 flex flex-col gap-1">
          {posts.map((p) => (
            <li key={p.id} className="flex items-center justify-between text-sm">
              <span className="text-text-secondary">
                {p.name}
                {p.lat != null && p.lon != null && (
                  <span className="text-xs text-text-muted ml-2">({p.lat}, {p.lon})</span>
                )}
              </span>
              <button
                onClick={() => void remove(p.id)}
                className="text-xs text-text-muted hover:text-danger"
              >
                remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2 items-end flex-wrap">
        <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} />
        <Input label="Lat" value={lat} onChange={(e) => setLat(e.target.value)} className="w-24" />
        <Input label="Lon" value={lon} onChange={(e) => setLon(e.target.value)} className="w-24" />
        <Button size="sm" loading={busy} onClick={() => void add()}>Add post</Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Wire NCS controls into the dashboard**

Modify `frontend/src/pages/events/EventDashboardPage.tsx`:

1. Add imports: `CheckInBar`, `PostsPanel`, `Button`, `activateEvent`, `closeEvent`, `reopenEvent`, `updateParticipant`, `addEventNote`, `setEventLogPinned`, the toast hook, `useState` additions, and `STATUS_BADGE`/`ParticipantStatus` as needed.
2. Remove the `void canWrite; void refresh;` placeholder lines from Task 8.
3. Add a toast-driven error reporter and generic action helper inside the component (`useToast` comes from `../../context/ToastContext` and exposes `addToast(message, type?)` with type `"success" | "error" | "info"`):

```tsx
  const { addToast } = useToast();
  const onError = (message: string) => addToast(message, "error");

  async function act(fn: () => Promise<unknown>, failMessage: string) {
    try {
      await fn();
      await refresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : failMessage);
    }
  }
```

4. Header gains lifecycle buttons when `canWrite`:

```tsx
  {canWrite && event.status === "draft" && (
    <Button size="sm" onClick={() => void act(() => activateEvent(event.id, slug!), "Activate failed")}>
      Activate
    </Button>
  )}
  {canWrite && event.status === "active" && (
    <Button size="sm" variant="danger" onClick={() => void act(() => closeEvent(event.id, slug!), "Close failed")}>
      Close event
    </Button>
  )}
  {canWrite && event.status === "closed" && (
    <Button size="sm" variant="secondary" onClick={() => void act(() => reopenEvent(event.id, slug!), "Reopen failed")}>
      Reopen
    </Button>
  )}
```

5. Above the participant board, when `canWrite && event.status === "active"`, render:

```tsx
  <CheckInBar netSlug={slug!} eventId={event.id} posts={posts} onDone={refresh} onError={onError} />
```

6. Pass an `actions` render prop to `ParticipantBoard` when `canWrite && event.status === "active"` — a status `<select>` per row:

```tsx
  actions={(p) => (
    <select
      value={p.current_status}
      onChange={(e) =>
        void act(
          () => updateParticipant(event.id, p.id, { status: e.target.value as ParticipantStatus }, slug!),
          "Status change failed",
        )
      }
      className="rounded-md bg-bg-elevated border border-border px-2 py-1 text-xs text-text-primary"
    >
      {(p.current_status === "checked_out"
        ? ["checked_out", "checked_in"]
        : ["checked_in", "at_post", "en_route", "out_of_service", "checked_out"]
      ).map((s) => (
        <option key={s} value={s}>{STATUS_LABEL[s as ParticipantStatus]}</option>
      ))}
    </select>
  )}
```

7. Pass a `composer` to `NetLogPanel` when `canWrite && event.status === "active"`. Add `const [noteText, setNoteText] = useState("");` to the page and pass:

```tsx
  composer={
    <div className="flex gap-2">
      <input
        value={noteText}
        onChange={(e) => setNoteText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && noteText.trim()) {
            void act(async () => {
              await addEventNote(event.id, { message: noteText.trim() }, slug!);
              setNoteText("");
            }, "Failed to add note");
          }
        }}
        placeholder="Add log entry…"
        className="flex-1 rounded-md bg-bg-elevated border border-border px-3 py-2 text-sm text-text-primary"
      />
      <Button
        size="sm"
        disabled={!noteText.trim()}
        onClick={() =>
          void act(async () => {
            await addEventNote(event.id, { message: noteText.trim() }, slug!);
            setNoteText("");
          }, "Failed to add note")
        }
      >
        Log
      </Button>
    </div>
  }
```

8. In the participant detail panel, when `canWrite && event.status === "active"`, add a participant note composer. Add `const [participantNote, setParticipantNote] = useState("");` and `const [pinNote, setPinNote] = useState(false);` to the page; render below the participant's log history:

```tsx
  <div className="flex gap-2 items-center mt-2">
    <input
      value={participantNote}
      onChange={(e) => setParticipantNote(e.target.value)}
      placeholder={`Note on ${selected.callsign}…`}
      className="flex-1 rounded-md bg-bg-elevated border border-border px-3 py-2 text-sm text-text-primary"
    />
    <label className="text-xs text-text-muted flex items-center gap-1">
      <input type="checkbox" checked={pinNote} onChange={(e) => setPinNote(e.target.checked)} />
      Pin
    </label>
    <Button
      size="sm"
      disabled={!participantNote.trim()}
      onClick={() =>
        void act(async () => {
          await addEventNote(
            event.id,
            { message: participantNote.trim(), callsign: selected.callsign, pinned: pinNote },
            slug!,
          );
          setParticipantNote("");
          setPinNote(false);
        }, "Failed to add note")
      }
    >
      Add note
    </Button>
  </div>
```

   And on each pinned entry in the detail panel, add an unpin control:

```tsx
  {canWrite && event.status === "active" && (
    <button
      onClick={() => void act(() => setEventLogPinned(event.id, e.id, false, slug!), "Unpin failed")}
      className="ml-2 text-xs text-text-muted hover:text-danger"
    >
      unpin
    </button>
  )}
```

9. Below the participant detail (or in the right column under the log), when `canWrite && event.status !== "closed"`, render:

```tsx
  <PostsPanel netSlug={slug!} eventId={event.id} posts={posts} onChanged={refresh} onError={onError} />
```

- [ ] **Step 4: Build check + commit**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: builds cleanly

```bash
git add frontend/src/pages/events/
git commit -m "feat(events): NCS dashboard controls (check-in bar, status, notes, posts, lifecycle)"
```

---

### Task 10: After-action report page (print view + CSV)

**Files:**
- Create: `frontend/src/pages/events/EventReportPage.tsx`
- Modify: `frontend/src/App.tsx` (route)

**Interfaces:**
- Consumes: `fetchEvent`, `fetchEventReport` from Task 6.
- Produces: route `/nets/:slug/events/:eventId/report` (min role viewer) — participants table (callsign, name, post/location, stints, total hours), full chronological log table, Print button (`window.print()`), and two CSV downloads (participants, log) generated client-side.

- [ ] **Step 1: Write the page**

```tsx
// frontend/src/pages/events/EventReportPage.tsx
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchEvent, fetchEventReport } from "../../api/events";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import type { EventReportParticipant, EventSnapshot } from "../../types";

function csvEscape(value: string): string {
  return `"${value.replace(/"/g, '""')}"`;
}

function toCsv(rows: (string | number | null)[][]): string {
  return rows.map((r) => r.map((c) => csvEscape(c == null ? "" : String(c))).join(",")).join("\n");
}

function downloadCsv(filename: string, csv: string) {
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function fmtHours(totalSeconds: number): string {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.round((totalSeconds % 3600) / 60);
  return `${h}:${String(m).padStart(2, "0")}`;
}

function fmt(dt: string | null): string {
  return dt ? new Date(dt).toLocaleString() : "";
}

export function EventReportPage() {
  const { slug, eventId } = useParams<{ slug: string; eventId: string }>();
  const [snapshot, setSnapshot] = useState<EventSnapshot | null>(null);
  const [report, setReport] = useState<EventReportParticipant[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!slug || !eventId) return;
    const id = Number(eventId);
    Promise.all([fetchEvent(id, slug), fetchEventReport(id, slug)])
      .then(([snap, rep]) => {
        setSnapshot(snap);
        setReport(rep.participants);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load report"));
  }, [slug, eventId]);

  if (error) return <p className="p-6 text-danger text-sm">{error}</p>;
  if (!snapshot || !report) {
    return (
      <div className="flex justify-center py-16">
        <Spinner size="lg" />
      </div>
    );
  }

  const { event, log } = snapshot;

  function participantsCsv() {
    downloadCsv(
      `event-${event.id}-participants.csv`,
      toCsv([
        ["callsign", "name", "post", "location", "stints", "total_hours"],
        ...report!.map((p) => [
          p.callsign,
          p.name,
          p.post,
          p.location,
          p.stints.map((s) => `${fmt(s.start)} - ${s.end ? fmt(s.end) : "open"}`).join("; "),
          fmtHours(p.total_seconds),
        ]),
      ]),
    );
  }

  function logCsv() {
    downloadCsv(
      `event-${event.id}-log.csv`,
      toCsv([
        ["seq", "time", "type", "callsign", "actor", "message"],
        ...log.map((e) => [e.seq, fmt(e.created_at), e.entry_type, e.callsign, e.actor, e.message]),
      ]),
    );
  }

  return (
    <div className="p-4 md:p-6 max-w-4xl">
      <div className="flex items-center gap-3 mb-1 print:hidden">
        <Link to={`/nets/${slug}/events/${event.id}`} className="text-text-muted hover:text-accent text-sm">
          ← Dashboard
        </Link>
        <div className="ml-auto flex gap-2">
          <Button size="sm" variant="secondary" onClick={participantsCsv}>Participants CSV</Button>
          <Button size="sm" variant="secondary" onClick={logCsv}>Log CSV</Button>
          <Button size="sm" onClick={() => window.print()}>Print</Button>
        </div>
      </div>

      <h1 className="text-xl font-semibold text-text-primary">{event.name} — After-action report</h1>
      <p className="text-sm text-text-muted mb-6">
        {event.event_type === "public_service" ? "Public service" : "Emergency"} event
        {event.activated_at && ` · activated ${fmt(event.activated_at)}`}
        {event.closed_at && ` · closed ${fmt(event.closed_at)}`}
      </p>

      <h2 className="text-base font-semibold text-text-primary mb-2">Participants</h2>
      <table className="w-full text-sm mb-8">
        <thead>
          <tr className="text-left text-text-muted border-b border-border">
            <th className="py-1.5 pr-3">Callsign</th>
            <th className="py-1.5 pr-3">Name</th>
            <th className="py-1.5 pr-3">Post / location</th>
            <th className="py-1.5 pr-3">Stints</th>
            <th className="py-1.5">Total (h:mm)</th>
          </tr>
        </thead>
        <tbody>
          {report.map((p) => (
            <tr key={p.callsign} className="border-b border-border align-top">
              <td className="py-1.5 pr-3 font-mono">{p.callsign}</td>
              <td className="py-1.5 pr-3">{p.name ?? "—"}</td>
              <td className="py-1.5 pr-3">{p.post ?? p.location ?? "—"}</td>
              <td className="py-1.5 pr-3">
                {p.stints.map((s, i) => (
                  <div key={i}>
                    {fmt(s.start)} → {s.end ? fmt(s.end) : "(open)"}
                  </div>
                ))}
              </td>
              <td className="py-1.5">{fmtHours(p.total_seconds)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 className="text-base font-semibold text-text-primary mb-2">Event log</h2>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-text-muted border-b border-border">
            <th className="py-1.5 pr-3">Time</th>
            <th className="py-1.5 pr-3">Type</th>
            <th className="py-1.5 pr-3">Callsign</th>
            <th className="py-1.5 pr-3">By</th>
            <th className="py-1.5">Entry</th>
          </tr>
        </thead>
        <tbody>
          {log.map((e) => (
            <tr key={e.seq} className="border-b border-border">
              <td className="py-1.5 pr-3 whitespace-nowrap text-text-muted">{fmt(e.created_at)}</td>
              <td className="py-1.5 pr-3 text-text-muted">{e.entry_type}</td>
              <td className="py-1.5 pr-3 font-mono">{e.callsign ?? ""}</td>
              <td className="py-1.5 pr-3 font-mono">{e.actor}</td>
              <td className="py-1.5">{e.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Add the route**

In `frontend/src/App.tsx`, import `EventReportPage` and add after the dashboard route:

```tsx
<Route path="events/:eventId/report" element={<RequireNetRole min="viewer"><EventReportPage /></RequireNetRole>} />
```

- [ ] **Step 3: Build check + full backend suite**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: builds cleanly

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/events/EventReportPage.tsx frontend/src/App.tsx
git commit -m "feat(events): after-action report page with print view and CSV export"
```

- [ ] **Step 5: Manual smoke test (human checkpoint)**

Run `./run-dev.sh`, then in the browser: create an event (draft + activate paths), define a post, check in a couple of callsigns, change statuses, add notes (event-wide + participant + pinned), verify a second browser tab sees updates within ~3s, close the event, view the report, download both CSVs, print preview. Verify a viewer-role account sees everything read-only with no write controls.
