# Event Winlink Messaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route inbound Winlink traffic into an active event's Messages panel and let NCS send/reply plain-text messages under the net callsign, reusing the existing scanner and delivery pipeline.

**Architecture:** A new `EventMessage` model + `Event.msg_seq` cursor in the events module; an inbound `route_event_messages()` service called from the scanner cycle right after the check-in pass (dual-ingest from the shared, deduped `RawMessage`); outbound via the existing `dispatch_delivery()` with a new `event_message` content type; net-scoped API routes (list/compose/reply/patch-status/rescan) and a cursor-polled `MessagesPanel` on the event dashboard.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 mapped_column, Alembic, pytest + httpx ASGI client; React 19 + TypeScript + Tailwind, plain `fetch()` via `apiFetch`.

**Spec:** `docs/superpowers/specs/2026-07-16-event-winlink-messaging-design.md`.

## Global Constraints

- Host is NixOS: backend via `.venv/bin/...`; frontend via `cd frontend && nix-shell -p nodejs_22 --run "npm <…>"`.
- Lint: `nix-shell --run "ruff check"` — line-length 120, select E+F; production code has no per-file ignores.
- Commits: Conventional Commits (`feat(events): …`).
- Enum storage: SQLAlchemy `Enum(PyEnum)` persists member **names** (`'INBOUND'`); API emits `.value` (`"inbound"`). Matches every existing module.
- Timestamps: `datetime.now(timezone.utc)`, `DateTime(timezone=True)` columns.
- Inbound routes to **every active event of the net** from the shared deduped `RawMessage`; dual-ingest with check-ins is intended.
- Dedup: unique `(event_id, raw_message_id)`; per-message IntegrityError is caught and skipped.
- Message state is a single shared status `unread`/`read`/`dismissed`. Threading is explicit only (`reply_to_id`).
- Outbound goes out under the **net callsign** via the existing `WinlinkBackend`; attribution-to-operator (`actor`) is internal.
- `to_address` validation is permissive (callsign / Winlink / internet email); reject only empty/oversized/control chars; always strip CR/LF.
- Writes (compose/reply/dismiss/rescan) require `net_control` and an `active` event (else 409); reads require net membership (viewer).
- `msg_seq` assigned under the event row lock, same mechanism as `log_seq`.
- Attachments, forms, PAT transport control, weather are out of scope (later sub-projects).
- Do not push to remote; commit locally only.

## Interfaces this plan builds on (verified against the current tree)

- `backend/modules/events/service.py`: `locked_event(db, event_id) -> Event | None`; `add_log_entry(db, event, *, entry_type, message, actor, callsign=None, new_status=None, pinned=False) -> EventLogEntry` (increments `event.log_seq`, does NOT commit). Module has `EventLogType` enum with `SYSTEM`.
- `backend/modules/events/routes.py`: `events_router = APIRouter(prefix="/api/nets/{net_slug}/events")`; `_get_event_or_404(db, net_id, event_id) -> Event`; `_raise_for(err)`; response helpers. NCS dep: `require_net_role(NetRole.NET_CONTROL)`; viewer dep: `require_net_role(NetRole.VIEWER)`; `NetContext` has `.net.id`, `.user.callsign`.
- `backend/integrations/delivery/service.py`: `dispatch_delivery(db, content_type, content_id, subject, body, net_id) -> bool`; `_lookup_content(db, content_type, content_id) -> tuple[str,str]`; `retry_failed(...)`, `get_delivery_status(...)`.
- `backend/integrations/delivery/routes.py`: `_verify_content_belongs_to_net(db, content_type, content_id, net_id)` — currently handles `roster`/`reminder`.
- `backend/integrations/scanner/service.py`: `scan_one(db, net_id, mailbox, now) -> int` calls `read_mailbox(inbox_path, net_address)` then `scan_and_import_messages(db, messages, session, net_id=net_id)`. `read_mailbox` returns dicts with keys `path, message_id, from_address, to_address, subject, received_at, body`.
- `backend/modules/checkins/service.py`: `get_net_id_for_session(db, net_session) -> int | None`; `scan_and_import_messages` upserts `RawMessage` (deduped by `message_id`) — after it runs, every net-matched message has a `RawMessage` row.
- `backend/modules/checkins/models.py`: `RawMessage(id, message_id[unique], from_address, received_at, subject, body, message_type, parsed, source_path)`.
- Current alembic head: `b3f0a1c2d4e5`.
- Frontend: `EventDashboardPage.tsx` composes `ParticipantBoard` / `NetLogPanel` / `MapPanel`, uses `useEventUpdates(slug, eventId)`. `apiFetch` from `api/client.ts`. Toast: `useToast()` → `addToast(msg, "error"|"success"|"info")`.

---

### Task 1: EventMessage model + msg_seq + migration

**Files:**
- Modify: `backend/modules/events/models.py` (add `Event.msg_seq`, `EventMessage`, enums)
- Modify: `alembic/env.py` is already importing events models — no change needed (verify).
- Create: `alembic/versions/c4d1e2f3a5b6_add_event_messages.py`
- Test: `tests/test_event_message_models.py`

**Interfaces:**
- Consumes: `Base`, `Event`, `EventParticipant`, `RawMessage`.
- Produces: `MessageDirection` (INBOUND/OUTBOUND), `MessageStatus` (UNREAD/READ/DISMISSED); `EventMessage` model; `Event.msg_seq` counter column. Later tasks import from `backend.modules.events.models`.

- [ ] **Step 1: Write failing model tests**

```python
# tests/test_event_message_models.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.checkins.models import RawMessage, MessageType
from backend.modules.events.models import (
    Event,
    EventMessage,
    EventType,
    MessageDirection,
    MessageStatus,
)
from tests.conftest import make_test_net


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def event(db):
    net = make_test_net(db)
    event = Event(net_id=net.id, name="Tornado", event_type=EventType.EMERGENCY, created_by="W0NE")
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _raw(db, message_id="M1"):
    from datetime import datetime, timezone
    raw = RawMessage(
        message_id=message_id, from_address="KE0XYZ@winlink.org",
        received_at=datetime.now(timezone.utc), subject="SITREP", body="all clear",
        message_type=MessageType.UNKNOWN, parsed=False,
    )
    db.add(raw)
    db.commit()
    db.refresh(raw)
    return raw


def test_event_msg_seq_default(db, event):
    assert event.msg_seq == 0


def test_inbound_message_defaults(db, event):
    raw = _raw(db)
    m = EventMessage(
        event_id=event.id, msg_seq=1, direction=MessageDirection.INBOUND,
        raw_message_id=raw.id, from_callsign="KE0XYZ", to_address="W0NE@winlink.org",
        subject="SITREP", body="all clear",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    assert m.status == MessageStatus.UNREAD
    assert m.participant_id is None
    assert m.reply_to_id is None
    assert m.actor is None
    assert m.created_at is not None


def test_dedup_same_raw_same_event(db, event):
    raw = _raw(db)
    for seq in (1, 2):
        db.add(EventMessage(
            event_id=event.id, msg_seq=seq, direction=MessageDirection.INBOUND,
            raw_message_id=raw.id, from_callsign="KE0XYZ", to_address="W0NE",
            subject="s", body="b",
        ))
        if seq == 1:
            db.commit()
        else:
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()


def test_outbound_rows_not_dedup_constrained(db, event):
    # Two outbound rows (null raw_message_id) must coexist — SQLite treats NULLs
    # as distinct in a unique constraint.
    for seq in (1, 2):
        db.add(EventMessage(
            event_id=event.id, msg_seq=seq, direction=MessageDirection.OUTBOUND,
            raw_message_id=None, from_callsign="W0NE", to_address="jane@redcross.org",
            subject="reply", body="ack", status=MessageStatus.READ, actor="W0NC",
        ))
    db.commit()
    assert db.query(EventMessage).filter_by(direction=MessageDirection.OUTBOUND).count() == 2


def test_reply_threading_self_fk(db, event):
    raw = _raw(db)
    inbound = EventMessage(
        event_id=event.id, msg_seq=1, direction=MessageDirection.INBOUND,
        raw_message_id=raw.id, from_callsign="KE0XYZ", to_address="W0NE",
        subject="SITREP", body="b",
    )
    db.add(inbound)
    db.commit()
    reply = EventMessage(
        event_id=event.id, msg_seq=2, direction=MessageDirection.OUTBOUND,
        from_callsign="W0NE", to_address="KE0XYZ", subject="Re: SITREP", body="ack",
        status=MessageStatus.READ, actor="W0NC", reply_to_id=inbound.id,
    )
    db.add(reply)
    db.commit()
    db.refresh(reply)
    assert reply.reply_to_id == inbound.id


def test_cascade_delete_with_event(db, event):
    raw = _raw(db)
    db.add(EventMessage(
        event_id=event.id, msg_seq=1, direction=MessageDirection.INBOUND,
        raw_message_id=raw.id, from_callsign="KE0XYZ", to_address="W0NE", subject="s", body="b",
    ))
    db.commit()
    db.delete(event)
    db.commit()
    assert db.query(EventMessage).count() == 0
    # The shared raw message is NOT deleted by cascade.
    assert db.get(RawMessage, raw.id) is not None
```

- [ ] **Step 2: Run tests, verify failure**

Run: `.venv/bin/pytest tests/test_event_message_models.py -q`
Expected: FAIL — `ImportError` (`EventMessage`/`MessageDirection` not defined)

- [ ] **Step 3: Add the counter column and model**

In `backend/modules/events/models.py`, add `import enum` if absent, ensure `String`, `Text`, `Enum`, `ForeignKey`, `UniqueConstraint`, `DateTime`, `Integer` are imported. In `class Event`, after the `log_seq` column:

```python
    # Last-assigned event_messages.msg_seq for this event — the Messages-panel
    # polling cursor, incremented under the event row lock like log_seq.
    msg_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

Add the enums near the other event enums:

```python
class MessageDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageStatus(str, enum.Enum):
    UNREAD = "unread"
    READ = "read"
    DISMISSED = "dismissed"
```

Append the model at the end of the file:

```python
class EventMessage(Base):
    __tablename__ = "event_messages"
    __table_args__ = (
        UniqueConstraint("event_id", "raw_message_id", name="uq_event_messages_event_raw"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("events.id"), nullable=False)
    msg_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    direction: Mapped[MessageDirection] = mapped_column(Enum(MessageDirection), nullable=False)
    # Set for inbound (links the shared, deduped RawMessage); null for outbound.
    raw_message_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("raw_messages.id"), nullable=True)
    # Linked when the from-callsign matches a checked-in participant.
    participant_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("event_participants.id"), nullable=True
    )
    from_callsign: Mapped[str] = mapped_column(String(64), nullable=False)
    to_address: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus), nullable=False, default=MessageStatus.UNREAD
    )
    # Outbound reply → the inbound message it answers.
    reply_to_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("event_messages.id"), nullable=True)
    # Operator who sent an outbound message; null for inbound.
    actor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    event: Mapped["Event"] = relationship()
```

Add the relationship on `Event` (in the `relationship(...)` block with posts/participants/log_entries):

```python
    messages: Mapped[list["EventMessage"]] = relationship(
        back_populates=None, cascade="all, delete-orphan"
    )
```

Note: `_utcnow` already exists in this module (used by other models). If the `Event.messages` relationship's `back_populates=None` trips a mapper warning, drop the kwarg — a bare `relationship("EventMessage", cascade="all, delete-orphan")` is fine since `EventMessage.event` is a one-directional link.

- [ ] **Step 4: Run model tests, verify pass**

Run: `.venv/bin/pytest tests/test_event_message_models.py -q`
Expected: 7 passed

- [ ] **Step 5: Write the migration**

```python
# alembic/versions/c4d1e2f3a5b6_add_event_messages.py
"""add event messages

Revision ID: c4d1e2f3a5b6
Revises: b3f0a1c2d4e5
Create Date: 2026-07-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4d1e2f3a5b6'
down_revision: Union[str, None] = 'b3f0a1c2d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('msg_seq', sa.Integer(), nullable=False, server_default='0'))
    op.create_table(
        'event_messages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('msg_seq', sa.Integer(), nullable=False),
        sa.Column('direction', sa.Enum('INBOUND', 'OUTBOUND', name='messagedirection'), nullable=False),
        sa.Column('raw_message_id', sa.Integer(), nullable=True),
        sa.Column('participant_id', sa.Integer(), nullable=True),
        sa.Column('from_callsign', sa.String(length=64), nullable=False),
        sa.Column('to_address', sa.String(length=255), nullable=False),
        sa.Column('subject', sa.String(length=500), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('status', sa.Enum('UNREAD', 'READ', 'DISMISSED', name='messagestatus'), nullable=False),
        sa.Column('reply_to_id', sa.Integer(), nullable=True),
        sa.Column('actor', sa.String(length=20), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ),
        sa.ForeignKeyConstraint(['raw_message_id'], ['raw_messages.id'], ),
        sa.ForeignKeyConstraint(['participant_id'], ['event_participants.id'], ),
        sa.ForeignKeyConstraint(['reply_to_id'], ['event_messages.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'raw_message_id', name='uq_event_messages_event_raw'),
    )


def downgrade() -> None:
    op.drop_table('event_messages')
    op.drop_column('events', 'msg_seq')
```

- [ ] **Step 6: Verify migration on a scratch DB**

Run:
```bash
SKYNET_DATABASE_URL="sqlite:////tmp/claude-eventmsg-mig.db" .venv/bin/alembic upgrade head && rm -f /tmp/claude-eventmsg-mig.db
```
Expected: ends with `Running upgrade b3f0a1c2d4e5 -> c4d1e2f3a5b6, add event messages`, exit 0.

- [ ] **Step 7: Full suite + lint + commit**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add backend/modules/events/models.py alembic/versions/c4d1e2f3a5b6_add_event_messages.py tests/test_event_message_models.py
git commit -m "feat(events): EventMessage model, msg_seq cursor, and migration"
```

---

### Task 2: Inbound routing service

**Files:**
- Create: `backend/modules/events/messages.py`
- Test: `tests/test_event_message_routing.py`

**Interfaces:**
- Consumes: Task 1 models; `EventStatus`, `EventParticipant`, `add_log_entry`, `EventLogType`, `locked_event` from the events module; `RawMessage`.
- Produces (in `backend.modules.events.messages`):
  - `next_msg_seq(event) -> int` — increments `event.msg_seq`, returns it (caller commits).
  - `route_event_messages(db, net_id, raw_messages: list[dict]) -> int` — for each `active` event of `net_id`, create inbound `EventMessage`s for raw messages not already routed (deduped), link participant by SSID-stripped from-callsign, write a `system` log breadcrumb; returns total new messages created across all events. `raw_messages` are the dicts from `read_mailbox` (keys: `message_id`, `from_address`, `to_address`, `subject`, `body`, `received_at`).
  - `count_new_for_event(...)` is NOT needed — the rescan route counts directly.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_event_message_routing.py
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.checkins.models import MessageType, RawMessage
from backend.modules.events.models import (
    Event,
    EventLogEntry,
    EventLogType,
    EventMessage,
    EventStatus,
    EventType,
    MessageDirection,
    MessageStatus,
)
from backend.modules.events.messages import route_event_messages
from backend.modules.events.service import check_in, create_event
from tests.conftest import make_test_net


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def _no_callbook(monkeypatch):
    monkeypatch.setattr("backend.modules.events.service.lookup_callsign", lambda db, cs: None)


@pytest.fixture
def net(db):
    return make_test_net(db)


def _raw_dict(mid, frm="KE0XYZ@winlink.org", subj="SITREP", body="all clear"):
    return {
        "message_id": mid, "from_address": frm, "to_address": "W0NE@winlink.org",
        "subject": subj, "body": body, "received_at": datetime.now(timezone.utc), "path": None,
    }


def _persist_raw(db, d):
    raw = RawMessage(
        message_id=d["message_id"], from_address=d["from_address"], received_at=d["received_at"],
        subject=d["subject"], body=d["body"], message_type=MessageType.UNKNOWN, parsed=False,
    )
    db.add(raw)
    db.commit()
    return raw


def _active_event(db, net, name="E"):
    return create_event(db, net_id=net.id, name=name, event_type=EventType.EMERGENCY,
                        created_by="W0NE", activate=True)


class TestRouting:
    def test_routes_inbound_to_active_event(self, db, net):
        event = _active_event(db, net)
        d = _raw_dict("M1")
        _persist_raw(db, d)
        n = route_event_messages(db, net.id, [d])
        assert n == 1
        msg = db.query(EventMessage).one()
        assert msg.event_id == event.id
        assert msg.direction == MessageDirection.INBOUND
        assert msg.status == MessageStatus.UNREAD
        assert msg.from_callsign == "KE0XYZ"
        assert msg.msg_seq == 1

    def test_participant_linked_by_callsign(self, db, net):
        event = _active_event(db, net)
        check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        d = _raw_dict("M1", frm="ke0xyz-7@winlink.org")  # SSID + case → same participant
        _persist_raw(db, d)
        route_event_messages(db, net.id, [d])
        msg = db.query(EventMessage).one()
        assert msg.participant_id is not None

    def test_unmatched_sender_leaves_participant_null(self, db, net):
        _active_event(db, net)
        d = _raw_dict("M1", frm="W0OUT@winlink.org")
        _persist_raw(db, d)
        route_event_messages(db, net.id, [d])
        assert db.query(EventMessage).one().participant_id is None

    def test_breadcrumb_written_to_log(self, db, net):
        event = _active_event(db, net)
        d = _raw_dict("M1", subj="Road closed")
        _persist_raw(db, d)
        route_event_messages(db, net.id, [d])
        entry = (
            db.query(EventLogEntry)
            .filter(EventLogEntry.event_id == event.id, EventLogEntry.entry_type == EventLogType.SYSTEM)
            .order_by(EventLogEntry.seq.desc())
            .first()
        )
        assert "KE0XYZ" in entry.message
        assert "Road closed" in entry.message
        assert entry.callsign == "KE0XYZ"

    def test_dedup_on_rerun(self, db, net):
        _active_event(db, net)
        d = _raw_dict("M1")
        _persist_raw(db, d)
        assert route_event_messages(db, net.id, [d]) == 1
        assert route_event_messages(db, net.id, [d]) == 0  # second run: no dupes
        assert db.query(EventMessage).count() == 1

    def test_fans_out_to_all_active_events(self, db, net):
        e1 = _active_event(db, net, "E1")
        e2 = _active_event(db, net, "E2")
        d = _raw_dict("M1")
        _persist_raw(db, d)
        n = route_event_messages(db, net.id, [d])
        assert n == 2
        assert {m.event_id for m in db.query(EventMessage).all()} == {e1.id, e2.id}

    def test_skips_non_active_events(self, db, net):
        draft = create_event(db, net_id=net.id, name="D", event_type=EventType.EMERGENCY, created_by="W0NE")
        assert draft.status == EventStatus.DRAFT
        d = _raw_dict("M1")
        _persist_raw(db, d)
        assert route_event_messages(db, net.id, [d]) == 0

    def test_missing_raw_message_row_skipped(self, db, net):
        _active_event(db, net)
        d = _raw_dict("M1")
        # Note: NOT persisted — routing must resolve the RawMessage by message_id
        # and skip if absent (check-in pass persists it; if it didn't, skip).
        assert route_event_messages(db, net.id, [d]) == 0
```

- [ ] **Step 2: Run tests, verify failure**

Run: `.venv/bin/pytest tests/test_event_message_routing.py -q`
Expected: FAIL — `ImportError` (no `route_event_messages`)

- [ ] **Step 3: Implement the routing service**

```python
# backend/modules/events/messages.py
"""Inbound Winlink routing into active events, and the msg_seq cursor helper.

route_event_messages runs in the scanner cycle right after the check-in pass.
Both consume the same net-matched, deduped RawMessage rows, so one inbound
message can produce both a CheckIn and an EventMessage (intended dual-ingest)."""
import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.modules.checkins.models import RawMessage
from backend.modules.events.models import (
    Event,
    EventLogType,
    EventMessage,
    EventParticipant,
    EventStatus,
    MessageDirection,
    MessageStatus,
)
from backend.modules.events.service import add_log_entry

logger = logging.getLogger(__name__)


def next_msg_seq(event: Event) -> int:
    event.msg_seq += 1
    return event.msg_seq


def _base_callsign(address: str) -> str:
    """KE0XYZ-7@winlink.org -> KE0XYZ."""
    local = address.split("@")[0]
    return local.split("-")[0].strip().upper()


def route_event_messages(db: Session, net_id: int, raw_messages: list[dict]) -> int:
    """Create inbound EventMessages for every active event of net_id. Returns the
    total number of new messages created across all active events."""
    events = (
        db.query(Event)
        .filter(Event.net_id == net_id, Event.status == EventStatus.ACTIVE)
        .all()
    )
    if not events:
        return 0

    # Resolve RawMessage rows by message_id (the check-in pass already upserted
    # them). A message with no RawMessage row is skipped.
    msg_ids = [m["message_id"] for m in raw_messages]
    raw_by_id = {
        r.message_id: r
        for r in db.query(RawMessage).filter(RawMessage.message_id.in_(msg_ids)).all()
    }

    created = 0
    for event in events:
        participants = {
            p.callsign.upper(): p
            for p in db.query(EventParticipant).filter(EventParticipant.event_id == event.id).all()
        }
        for m in raw_messages:
            raw = raw_by_id.get(m["message_id"])
            if raw is None:
                continue
            # Dedup: skip if this raw message is already routed to this event.
            exists = (
                db.query(EventMessage)
                .filter(EventMessage.event_id == event.id, EventMessage.raw_message_id == raw.id)
                .first()
            )
            if exists is not None:
                continue

            base = _base_callsign(m["from_address"])
            participant = participants.get(base)
            seq = next_msg_seq(event)
            message = EventMessage(
                event_id=event.id,
                msg_seq=seq,
                direction=MessageDirection.INBOUND,
                raw_message_id=raw.id,
                participant_id=participant.id if participant else None,
                from_callsign=base,
                to_address=m.get("to_address") or "",
                subject=m.get("subject") or "",
                body=m.get("body") or "",
                status=MessageStatus.UNREAD,
                received_at=raw.received_at,
            )
            db.add(message)
            add_log_entry(
                db, event, entry_type=EventLogType.SYSTEM,
                message=f"\U0001F4E9 Winlink from {base}: {m.get('subject') or '(no subject)'}",
                actor=base, callsign=base,
            )
            try:
                db.commit()
                created += 1
            except IntegrityError:
                # Concurrent scan raced us to the same (event, raw). Roll back
                # this message; the seq we consumed is harmless (monotonic gap).
                db.rollback()
                continue
    return created
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_event_message_routing.py -q`
Expected: all pass

- [ ] **Step 5: Lint + commit**

```bash
nix-shell --run "ruff check"
git add backend/modules/events/messages.py tests/test_event_message_routing.py
git commit -m "feat(events): inbound Winlink routing into active events"
```

---

### Task 3: Wire routing into the scanner

**Files:**
- Modify: `backend/integrations/scanner/service.py` (`scan_one`)
- Test: `tests/test_scanner_event_routing.py`

**Interfaces:**
- Consumes: Task 2 `route_event_messages`.
- Produces: `scan_one` calls `route_event_messages` after `scan_and_import_messages`, so background scans feed active events. `scan_one` return value is unchanged (still the check-in count).

- [ ] **Step 1: Write failing test**

```python
# tests/test_scanner_event_routing.py
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.integrations.scanner.service as scanner_service
from backend.db.base import Base
from backend.modules.events.models import EventMessage, EventType
from backend.modules.events.service import create_event
from backend.modules.nets.config_service import set_net_config_bulk
from tests.conftest import make_test_net


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def _no_callbook(monkeypatch):
    monkeypatch.setattr("backend.modules.events.service.lookup_callsign", lambda db, cs: None)


def test_scan_one_routes_to_active_event(db, monkeypatch):
    net = make_test_net(db)
    set_net_config_bulk(db, net.id, {"net_address": "W0NE@winlink.org"})
    create_event(db, net_id=net.id, name="E", event_type=EventType.EMERGENCY,
                 created_by="W0NE", activate=True)

    # Stub the mailbox read to return one message; no session window needed for events.
    def fake_read_mailbox(inbox_path, net_address):
        return [{
            "message_id": "M1", "from_address": "KE0XYZ@winlink.org", "to_address": net_address,
            "subject": "SITREP", "body": "all clear", "received_at": datetime.now(timezone.utc),
            "path": None,
        }]
    monkeypatch.setattr(scanner_service, "read_mailbox", fake_read_mailbox)

    # No active NetSession → scan_and_import returns [] but the RawMessage must
    # still be persisted for event routing. Stub scan_and_import to upsert the raw
    # row (mirrors real behavior) and return no check-ins.
    from backend.modules.checkins.models import MessageType, RawMessage

    def fake_scan_and_import(db, messages, session, net_id=None):
        for m in messages:
            db.add(RawMessage(
                message_id=m["message_id"], from_address=m["from_address"],
                received_at=m["received_at"], subject=m["subject"], body=m["body"],
                message_type=MessageType.UNKNOWN, parsed=False,
            ))
        db.commit()
        return []
    monkeypatch.setattr(scanner_service, "scan_and_import_messages", fake_scan_and_import)
    # No active session: make find_active_session return None so the check-in path
    # short-circuits but event routing still runs.
    monkeypatch.setattr(scanner_service, "find_active_session", lambda db, now, net_id=None: None)

    now = datetime.now(timezone.utc)
    scanner_service.scan_one(db, net.id, "/tmp/fake-mailbox", now)

    assert db.query(EventMessage).count() == 1
```

- [ ] **Step 2: Run test, verify failure**

Run: `.venv/bin/pytest tests/test_scanner_event_routing.py -q`
Expected: FAIL — `EventMessage.count() == 0` (routing not wired; and `scan_one` currently returns early when `find_active_session` is None)

- [ ] **Step 3: Modify `scan_one`**

The current `scan_one` returns 0 early when there's no active session. Restructure so event routing runs regardless of a session window. Replace the body of `scan_one` (from the `session = find_active_session(...)` line onward) with:

```python
    inbox_path = os.path.join(mailbox, "in")
    messages = read_mailbox(inbox_path, net_address)

    session = find_active_session(db, now, net_id=net_id)
    checkins = []
    if session is not None:
        checkins = scan_and_import_messages(db, messages, session, net_id=net_id)
    else:
        # No check-in session window, but we still need RawMessage rows persisted
        # so active events can route. Upsert raw messages without creating check-ins.
        _persist_raw_messages(db, messages)

    from backend.modules.events.messages import route_event_messages

    routed = route_event_messages(db, net_id, messages)

    count = len(checkins)
    scanner_state.last_scan_time = now
    scanner_state.last_scan_count = count
    if session is not None:
        scanner_state.active_session_id = session.id

    logger.info(
        "Scanner completed for net_id=%d: %d check-ins, %d event messages", net_id, count, routed
    )
    return count
```

Add a small helper near the top of `scanner/service.py` (after imports):

```python
def _persist_raw_messages(db, messages):
    """Upsert RawMessage rows (deduped by message_id) without creating check-ins.
    Used when an event is active but no check-in session window is open, so event
    routing still has RawMessage rows to reference."""
    from backend.modules.checkins.models import MessageType, RawMessage

    ids = [m["message_id"] for m in messages]
    existing = {
        r[0] for r in db.query(RawMessage.message_id).filter(RawMessage.message_id.in_(ids)).all()
    }
    for m in messages:
        if m["message_id"] in existing:
            continue
        db.add(RawMessage(
            message_id=m["message_id"], from_address=m["from_address"],
            received_at=m["received_at"], subject=m["subject"], body=m["body"],
            message_type=MessageType.UNKNOWN, parsed=False, source_path=m.get("path"),
        ))
    db.commit()
```

Note: `scan_all_enabled` already skips nets whose `scanner.enabled` is not `"true"` and whose mailbox is unset — so event routing only runs for scanner-enabled nets, exactly as the spec requires. No change needed there.

- [ ] **Step 4: Run test + the existing scanner/check-in tests, verify pass**

Run: `.venv/bin/pytest tests/test_scanner_event_routing.py tests/test_checkin_service.py tests/test_checkin_modes.py -q`
Expected: all pass (the restructure must not regress the check-in path — the session-present branch behaves exactly as before).

- [ ] **Step 5: Full suite + lint + commit**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add backend/integrations/scanner/service.py tests/test_scanner_event_routing.py
git commit -m "feat(scanner): route inbound traffic into active events"
```

---

### Task 4: Outbound service + delivery content-type

**Files:**
- Create: `backend/modules/events/message_service.py`
- Modify: `backend/integrations/delivery/service.py` (`_lookup_content`)
- Modify: `backend/integrations/delivery/routes.py` (`_verify_content_belongs_to_net`)
- Test: `tests/test_event_message_service.py`

**Interfaces:**
- Consumes: Task 1 models; `dispatch_delivery`; `locked_event`; delivery lookup/verify.
- Produces (in `backend.modules.events.message_service`):
  - `validate_to_address(raw: str) -> str` — strips CR/LF + control chars, trims; raises `ValueError` on empty or > 255 chars. Accepts callsign / Winlink / internet-email forms (no format gatekeeping beyond length + control chars).
  - `send_event_message(db, event_id, *, actor, to_address, subject, body, reply_to_id=None) -> EventMessage` — validates address, creates outbound `EventMessage` (status=READ), assigns `msg_seq`, calls `dispatch_delivery(db, "event_message", msg.id, subject, body, net_id)`, returns the message. Raises `EventNotActiveError` if the event isn't active, `ValueError` on bad address.
  - `set_message_status(db, event_id, message_id, status) -> EventMessage | None` — sets `read`/`dismissed`; returns None if not found.
  - `_lookup_content` now returns `(subject, body)` for `content_type="event_message"`.
  - `_verify_content_belongs_to_net` now scopes `event_message` via `EventMessage.event_id -> Event.net_id`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_event_message_service.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import EventMessage, EventStatus, EventType, MessageDirection, MessageStatus
from backend.modules.events.message_service import (
    send_event_message,
    set_message_status,
    validate_to_address,
)
from backend.modules.events.service import EventNotActiveError, close_event, create_event
from backend.modules.nets.config_service import set_net_config_bulk
from tests.conftest import make_test_net


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def _no_callbook(monkeypatch):
    monkeypatch.setattr("backend.modules.events.service.lookup_callsign", lambda db, cs: None)


@pytest.fixture
def event(db):
    net = make_test_net(db)
    # No delivery backends configured → dispatch_delivery returns False but the
    # outbound row is still created (send failure is non-fatal, per spec).
    set_net_config_bulk(db, net.id, {"net_address": "W0NE@winlink.org"})
    return create_event(db, net_id=net.id, name="E", event_type=EventType.EMERGENCY,
                        created_by="W0NE", activate=True)


class TestValidateAddress:
    def test_accepts_callsign(self):
        assert validate_to_address(" ke0xyz ") == "ke0xyz"

    def test_accepts_winlink_and_email(self):
        assert validate_to_address("KE0XYZ@winlink.org") == "KE0XYZ@winlink.org"
        assert validate_to_address("jane@redcross.org") == "jane@redcross.org"

    def test_strips_crlf(self):
        assert "\n" not in validate_to_address("a@b.org\r\nCc: evil@x")
        assert "\r" not in validate_to_address("a@b.org\r\nCc: evil@x")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            validate_to_address("   ")

    def test_rejects_oversized(self):
        with pytest.raises(ValueError):
            validate_to_address("x" * 256 + "@y.org")


class TestSend:
    def test_creates_outbound_row(self, db, event):
        msg = send_event_message(
            db, event.id, actor="W0NC", to_address="jane@redcross.org",
            subject="Status", body="all clear",
        )
        assert msg.direction == MessageDirection.OUTBOUND
        assert msg.status == MessageStatus.READ
        assert msg.actor == "W0NC"
        assert msg.from_callsign == "W0NE"  # net callsign, derived from net_address
        assert msg.msg_seq == 1

    def test_reply_links_parent(self, db, event):
        from backend.modules.events.models import EventMessage as EM
        inbound = EM(
            event_id=event.id, msg_seq=99, direction=MessageDirection.INBOUND,
            from_callsign="KE0XYZ", to_address="W0NE", subject="SITREP", body="x",
        )
        db.add(inbound)
        db.commit()
        reply = send_event_message(
            db, event.id, actor="W0NC", to_address="KE0XYZ", subject="Re: SITREP",
            body="ack", reply_to_id=inbound.id,
        )
        assert reply.reply_to_id == inbound.id

    def test_send_on_closed_event_raises(self, db, event):
        close_event(db, event.id, actor="W0NE")
        with pytest.raises(EventNotActiveError):
            send_event_message(db, event.id, actor="W0NC", to_address="a@b.org",
                               subject="s", body="b")

    def test_bad_address_raises(self, db, event):
        with pytest.raises(ValueError):
            send_event_message(db, event.id, actor="W0NC", to_address="  ", subject="s", body="b")


class TestStatus:
    def test_mark_read_and_dismissed(self, db, event):
        from backend.modules.events.models import EventMessage as EM
        m = EM(event_id=event.id, msg_seq=1, direction=MessageDirection.INBOUND,
               from_callsign="KE0XYZ", to_address="W0NE", subject="s", body="b")
        db.add(m)
        db.commit()
        assert set_message_status(db, event.id, m.id, MessageStatus.READ).status == MessageStatus.READ
        assert set_message_status(db, event.id, m.id, MessageStatus.DISMISSED).status == MessageStatus.DISMISSED

    def test_missing_returns_none(self, db, event):
        assert set_message_status(db, event.id, 9999, MessageStatus.READ) is None
```

- [ ] **Step 2: Run tests, verify failure**

Run: `.venv/bin/pytest tests/test_event_message_service.py -q`
Expected: FAIL — ImportError

- [ ] **Step 3: Implement the message service**

```python
# backend/modules/events/message_service.py
"""Outbound event Winlink messages and message-status transitions.

Outbound send reuses the existing delivery pipeline (dispatch_delivery) with the
'event_message' content type — no new backend. Messages go out under the net
callsign (derived from net_address by _build_config in the delivery service)."""
from sqlalchemy.orm import Session

from backend.integrations.delivery.service import dispatch_delivery
from backend.modules.events.messages import next_msg_seq
from backend.modules.events.models import (
    EventMessage,
    EventStatus,
    MessageDirection,
    MessageStatus,
)
from backend.modules.events.service import EventNotActiveError, locked_event

MAX_ADDRESS_LEN = 255


def validate_to_address(raw: str) -> str:
    """Permissive: accept callsign / Winlink / internet-email forms. Strip CR/LF
    and other control chars (header-injection backstop), trim, and bound length.
    Winlink's CMS does the actual routing — we sanitize, not gatekeep."""
    cleaned = "".join(ch for ch in (raw or "") if ch.isprintable()).strip()
    if not cleaned:
        raise ValueError("Recipient address is required")
    if len(cleaned) > MAX_ADDRESS_LEN:
        raise ValueError("Recipient address is too long")
    return cleaned


def _net_callsign(db: Session, net_id: int) -> str:
    from backend.modules.nets.config_service import get_net_config

    net_address = get_net_config(db, net_id, "net_address", "") or ""
    return net_address.split("@")[0].upper() if net_address else ""


def send_event_message(
    db: Session,
    event_id: int,
    *,
    actor: str,
    to_address: str,
    subject: str,
    body: str,
    reply_to_id: int | None = None,
) -> EventMessage:
    to_address = validate_to_address(to_address)
    event = locked_event(db, event_id)
    if event is None or event.status != EventStatus.ACTIVE:
        raise EventNotActiveError("Event is not active")

    seq = next_msg_seq(event)
    message = EventMessage(
        event_id=event_id,
        msg_seq=seq,
        direction=MessageDirection.OUTBOUND,
        from_callsign=_net_callsign(db, event.net_id),
        to_address=to_address,
        subject=subject,
        body=body,
        status=MessageStatus.READ,
        actor=actor,
        reply_to_id=reply_to_id,
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    # Send through the existing delivery pipeline. A failure is non-fatal: the
    # row persists and the operator can retry via the delivery routes.
    dispatch_delivery(db, "event_message", message.id, subject, body, event.net_id)
    db.refresh(message)
    return message


def set_message_status(
    db: Session, event_id: int, message_id: int, status: MessageStatus
) -> EventMessage | None:
    message = (
        db.query(EventMessage)
        .filter(EventMessage.id == message_id, EventMessage.event_id == event_id)
        .one_or_none()
    )
    if message is None:
        return None
    message.status = status
    db.commit()
    db.refresh(message)
    return message
```

- [ ] **Step 4: Extend `_lookup_content` in the delivery service**

In `backend/integrations/delivery/service.py`, add to `_lookup_content` before the final `return "", ""`:

```python
    elif content_type == "event_message":
        from backend.modules.events.models import EventMessage

        msg = db.get(EventMessage, content_id)
        if msg:
            return msg.subject, msg.body
```

- [ ] **Step 5: Extend `_verify_content_belongs_to_net` in delivery routes**

In `backend/integrations/delivery/routes.py`, add an `event_message` branch inside `_verify_content_belongs_to_net` (before the `else` that raises "Unknown content type"):

```python
    if content_type == "event_message":
        from backend.modules.events.models import Event, EventMessage

        msg = db.get(EventMessage, content_id)
        if msg is None:
            raise HTTPException(status_code=404, detail="Not found")
        event = db.get(Event, msg.event_id)
        if event is None or event.net_id != net_id:
            raise HTTPException(status_code=404, detail="Not found")
        return
```

(Place this as the first `if`, keeping the existing `roster`/`reminder` branches; the trailing `else`/raise handles unknown types.)

- [ ] **Step 6: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_event_message_service.py tests/test_delivery_service.py tests/test_delivery_routes.py -q`
Expected: all pass (delivery tests must still pass — the added branches are additive).

- [ ] **Step 7: Full suite + lint + commit**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add backend/modules/events/message_service.py backend/integrations/delivery/service.py backend/integrations/delivery/routes.py tests/test_event_message_service.py
git commit -m "feat(events): outbound event messages via delivery pipeline"
```

---

### Task 5: API routes

**Files:**
- Modify: `backend/modules/events/routes.py` (append message routes + response helper)
- Test: `tests/test_event_message_routes.py`

**Interfaces:**
- Consumes: Tasks 2 & 4 services; `route_event_messages`; existing route helpers (`_get_event_or_404`, `_raise_for`); scanner `read_mailbox` + net config for rescan.
- Produces routes under `/api/nets/{net_slug}/events`:
  - `GET /{event_id}/messages?since=N&include_dismissed=false` (viewer) → `{messages: [...], latest_msg_seq}`; messages with `msg_seq > since`; dismissed excluded unless `include_dismissed=true`.
  - `POST /{event_id}/messages` (NCS) — `{to_address, subject, body, reply_to_id?}` → the created outbound message + `{delivered: bool}`; 422 on bad address, 409 on closed event.
  - `PATCH /{event_id}/messages/{message_id}` (NCS) — `{status: "read"|"dismissed"}` → updated message; 404 if missing.
  - `POST /{event_id}/rescan` (NCS) — reads the mailbox, runs check-in + event routing, returns `{new_messages: int}` for this event; 409 if event not active.

**Message response shape:** `{id, msg_seq, direction, raw_message_id, participant_id, from_callsign, to_address, subject, body, status, reply_to_id, actor, received_at, created_at}` (enums as `.value`, datetimes ISO).

- [ ] **Step 1: Write failing route tests**

```python
# tests/test_event_message_routes.py
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.checkins.models import MessageType, RawMessage
from backend.modules.events.messages import route_event_messages
from backend.modules.nets.config_service import set_net_config_bulk
from backend.modules.nets.models import Net, NetMembership, NetRole
from tests.conftest import make_test_token

NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/events"


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)


@pytest.fixture
def db_setup():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
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
        set_net_config_bulk(session, net.id, {"net_address": "W0NE@winlink.org"})
        session.commit()
        yield {"engine": engine, "factory": factory, "net_id": net.id}
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
    resp = await nc_client.post(BASE, json={"name": "E", "event_type": "emergency", "activate": True})
    return resp.json()["id"]


def _seed_inbound(db_setup, event_id, mid="M1", frm="KE0XYZ@winlink.org", subj="SITREP"):
    from datetime import datetime, timezone
    with db_setup["factory"]() as db:
        db.add(RawMessage(
            message_id=mid, from_address=frm, received_at=datetime.now(timezone.utc),
            subject=subj, body="body", message_type=MessageType.UNKNOWN, parsed=False,
        ))
        db.commit()
        route_event_messages(db, db_setup["net_id"], [{
            "message_id": mid, "from_address": frm, "to_address": "W0NE@winlink.org",
            "subject": subj, "body": "body", "received_at": datetime.now(timezone.utc),
        }])


class TestListMessages:
    async def test_lists_inbound(self, nc_client, viewer_client, active_event, db_setup):
        _seed_inbound(db_setup, active_event)
        resp = await viewer_client.get(f"{BASE}/{active_event}/messages", params={"since": 0})
        assert resp.status_code == 200
        body = resp.json()
        assert body["latest_msg_seq"] == 1
        assert len(body["messages"]) == 1
        assert body["messages"][0]["direction"] == "inbound"
        assert body["messages"][0]["from_callsign"] == "KE0XYZ"

    async def test_cursor_delta(self, nc_client, active_event, db_setup):
        _seed_inbound(db_setup, active_event, mid="M1")
        _seed_inbound(db_setup, active_event, mid="M2")
        resp = await nc_client.get(f"{BASE}/{active_event}/messages", params={"since": 1})
        body = resp.json()
        assert [m["msg_seq"] for m in body["messages"]] == [2]

    async def test_dismissed_hidden_by_default(self, nc_client, active_event, db_setup):
        _seed_inbound(db_setup, active_event)
        mid = (await nc_client.get(f"{BASE}/{active_event}/messages")).json()["messages"][0]["id"]
        await nc_client.patch(f"{BASE}/{active_event}/messages/{mid}", json={"status": "dismissed"})
        assert (await nc_client.get(f"{BASE}/{active_event}/messages")).json()["messages"] == []
        withd = await nc_client.get(f"{BASE}/{active_event}/messages", params={"include_dismissed": "true"})
        assert len(withd.json()["messages"]) == 1


class TestCompose:
    async def test_send(self, nc_client, active_event):
        resp = await nc_client.post(f"{BASE}/{active_event}/messages", json={
            "to_address": "jane@redcross.org", "subject": "Status", "body": "all clear",
        })
        assert resp.status_code == 201
        body = resp.json()
        assert body["message"]["direction"] == "outbound"
        assert body["message"]["to_address"] == "jane@redcross.org"
        assert "delivered" in body

    async def test_bad_address_422(self, nc_client, active_event):
        resp = await nc_client.post(f"{BASE}/{active_event}/messages", json={
            "to_address": "   ", "subject": "s", "body": "b"})
        assert resp.status_code == 422

    async def test_send_closed_event_409(self, nc_client, active_event):
        await nc_client.post(f"{BASE}/{active_event}/close")
        resp = await nc_client.post(f"{BASE}/{active_event}/messages", json={
            "to_address": "a@b.org", "subject": "s", "body": "b"})
        assert resp.status_code == 409

    async def test_viewer_cannot_send(self, viewer_client, active_event):
        resp = await viewer_client.post(f"{BASE}/{active_event}/messages", json={
            "to_address": "a@b.org", "subject": "s", "body": "b"})
        assert resp.status_code == 403


class TestPatchStatus:
    async def test_mark_read(self, nc_client, active_event, db_setup):
        _seed_inbound(db_setup, active_event)
        mid = (await nc_client.get(f"{BASE}/{active_event}/messages")).json()["messages"][0]["id"]
        resp = await nc_client.patch(f"{BASE}/{active_event}/messages/{mid}", json={"status": "read"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "read"

    async def test_missing_404(self, nc_client, active_event):
        resp = await nc_client.patch(f"{BASE}/{active_event}/messages/9999", json={"status": "read"})
        assert resp.status_code == 404


class TestRescan:
    async def test_rescan_reports_count(self, nc_client, active_event, monkeypatch):
        from datetime import datetime, timezone
        import backend.modules.events.routes as events_routes

        def fake_read(inbox_path, net_address):
            return [{
                "message_id": "RS1", "from_address": "KE0XYZ@winlink.org", "to_address": net_address,
                "subject": "hi", "body": "b", "received_at": datetime.now(timezone.utc), "path": None,
            }]
        monkeypatch.setattr(events_routes, "read_mailbox", fake_read)
        resp = await nc_client.post(f"{BASE}/{active_event}/rescan")
        assert resp.status_code == 200
        assert resp.json()["new_messages"] == 1

    async def test_rescan_closed_event_409(self, nc_client, active_event):
        await nc_client.post(f"{BASE}/{active_event}/close")
        resp = await nc_client.post(f"{BASE}/{active_event}/rescan")
        assert resp.status_code == 409
```

- [ ] **Step 2: Run tests, verify failure**

Run: `.venv/bin/pytest tests/test_event_message_routes.py -q`
Expected: FAIL — 404/405 (routes missing)

- [ ] **Step 3: Append the routes**

At the top of `backend/modules/events/routes.py`, extend imports:

```python
from backend.modules.events.models import EventMessage, EventStatus, MessageStatus
from backend.modules.events.messages import route_event_messages
from backend.modules.events.message_service import (
    send_event_message,
    set_message_status,
)
from backend.modules.checkins.mailbox_reader import read_mailbox
```

Note: importing `read_mailbox` as a module-level name in `events/routes.py` is deliberate — the rescan test monkeypatches `backend.modules.events.routes.read_mailbox`, which only works if the name is bound at module scope here (not imported inside the handler).

Add schemas near the other Pydantic models in the file:

```python
class MessageCompose(BaseModel):
    to_address: str
    subject: str = ""
    body: str = ""
    reply_to_id: int | None = None


class MessageStatusUpdate(BaseModel):
    status: MessageStatus
```

Add a response helper next to the other `_*_to_response` helpers:

```python
def _message_to_response(m: EventMessage) -> dict:
    return {
        "id": m.id,
        "msg_seq": m.msg_seq,
        "direction": m.direction.value,
        "raw_message_id": m.raw_message_id,
        "participant_id": m.participant_id,
        "from_callsign": m.from_callsign,
        "to_address": m.to_address,
        "subject": m.subject,
        "body": m.body,
        "status": m.status.value,
        "reply_to_id": m.reply_to_id,
        "actor": m.actor,
        "received_at": _iso(m.received_at),
        "created_at": _iso(m.created_at),
    }
```

(`_iso` already exists in this file.) Append the routes:

```python
@events_router.get("/{event_id}/messages")
async def list_messages_route(
    event_id: int,
    since: int = Query(default=0, ge=0),
    include_dismissed: bool = Query(default=False),
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    event = _get_event_or_404(db, ctx.net.id, event_id)
    query = db.query(EventMessage).filter(
        EventMessage.event_id == event.id, EventMessage.msg_seq > since
    )
    if not include_dismissed:
        query = query.filter(EventMessage.status != MessageStatus.DISMISSED)
    messages = query.order_by(EventMessage.msg_seq).all()
    return {
        "messages": [_message_to_response(m) for m in messages],
        "latest_msg_seq": event.msg_seq,
    }


@events_router.post("/{event_id}/messages", status_code=201)
async def compose_message_route(
    event_id: int,
    body: MessageCompose,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    try:
        message = send_event_message(
            db, event_id, actor=ctx.user.callsign,
            to_address=body.to_address, subject=body.subject, body=body.body,
            reply_to_id=body.reply_to_id,
        )
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err))
    except EventError as err:
        _raise_for(err)
    # Was anything actually delivered? Read the delivery logs for this message.
    from backend.integrations.delivery.service import get_delivery_status
    from backend.integrations.delivery.models import DeliveryStatus

    logs = get_delivery_status(db, "event_message", message.id)
    delivered = any(log.status == DeliveryStatus.SENT for log in logs)
    return {"message": _message_to_response(message), "delivered": delivered}


@events_router.patch("/{event_id}/messages/{message_id}")
async def patch_message_route(
    event_id: int,
    message_id: int,
    body: MessageStatusUpdate,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    message = set_message_status(db, event_id, message_id, body.status)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return _message_to_response(message)


@events_router.post("/{event_id}/rescan")
async def rescan_route(
    event_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    event = _get_event_or_404(db, ctx.net.id, event_id)
    if event.status != EventStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Event is not active")

    from backend.modules.nets.config_service import get_net_config
    import os

    mailbox = get_net_config(db, ctx.net.id, "pat_mailbox_path", "") or ""
    net_address = get_net_config(db, ctx.net.id, "net_address", "") or ""
    if not mailbox or not net_address:
        return {"new_messages": 0}

    messages = read_mailbox(os.path.join(mailbox, "in"), net_address)
    # Persist raw rows (no session context here) then route to active events.
    from backend.integrations.scanner.service import _persist_raw_messages

    _persist_raw_messages(db, messages)
    before = event.msg_seq
    route_event_messages(db, ctx.net.id, messages)
    db.refresh(event)
    return {"new_messages": event.msg_seq - before}
```

Note: `EventError` is already imported in this file (used by `_raise_for`). Confirm the import line includes it; if not, add `from backend.modules.events.service import EventError` alongside the other service imports.

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_event_message_routes.py -q`
Expected: all pass

- [ ] **Step 5: Full suite + lint + commit**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add backend/modules/events/routes.py tests/test_event_message_routes.py
git commit -m "feat(events): message list/compose/status/rescan API routes"
```

---

### Task 6: Frontend types, API, messages hook

**Files:**
- Modify: `frontend/src/types/index.ts` (append)
- Modify: `frontend/src/api/events.ts` (append)
- Create: `frontend/src/hooks/useEventMessages.ts`

**Interfaces:**
- Consumes: Task 5 payloads; `apiFetch`.
- Produces: types `MessageDirection`, `MessageStatus`, `EventMessage`, `EventMessages`; API `fetchEventMessages`, `composeEventMessage`, `setEventMessageStatus`, `rescanEventMailbox`; hook `useEventMessages(netSlug, eventId, enabled)` → `{ messages: EventMessage[], latestMsgSeq, unreadCount, refresh }`.

- [ ] **Step 1: Append types**

Append to `frontend/src/types/index.ts`:

```typescript
// --- Event Winlink messages ---

export type MessageDirection = "inbound" | "outbound";
export type MessageStatus = "unread" | "read" | "dismissed";

export interface EventMessage {
  id: number;
  msg_seq: number;
  direction: MessageDirection;
  raw_message_id: number | null;
  participant_id: number | null;
  from_callsign: string;
  to_address: string;
  subject: string;
  body: string;
  status: MessageStatus;
  reply_to_id: number | null;
  actor: string | null;
  received_at: string;
  created_at: string;
}

export interface EventMessages {
  messages: EventMessage[];
  latest_msg_seq: number;
}
```

- [ ] **Step 2: Append API functions**

Append to `frontend/src/api/events.ts` (add `EventMessage`, `EventMessages` to the type imports):

```typescript
// --- Event messages (Winlink) ---

export async function fetchEventMessages(
  eventId: number,
  since: number,
  netSlug: string,
  includeDismissed = false,
): Promise<EventMessages> {
  const params = new URLSearchParams({ since: String(since) });
  if (includeDismissed) params.set("include_dismissed", "true");
  return apiFetch<EventMessages>(`/nets/${netSlug}/events/${eventId}/messages?${params}`);
}

export interface ComposeMessageInput {
  to_address: string;
  subject: string;
  body: string;
  reply_to_id?: number | null;
}

export async function composeEventMessage(
  eventId: number,
  input: ComposeMessageInput,
  netSlug: string,
): Promise<{ message: EventMessage; delivered: boolean }> {
  return apiFetch<{ message: EventMessage; delivered: boolean }>(
    `/nets/${netSlug}/events/${eventId}/messages`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export async function setEventMessageStatus(
  eventId: number,
  messageId: number,
  status: MessageStatus,
  netSlug: string,
): Promise<EventMessage> {
  return apiFetch<EventMessage>(`/nets/${netSlug}/events/${eventId}/messages/${messageId}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

export async function rescanEventMailbox(
  eventId: number,
  netSlug: string,
): Promise<{ new_messages: number }> {
  return apiFetch<{ new_messages: number }>(`/nets/${netSlug}/events/${eventId}/rescan`, {
    method: "POST",
  });
}
```

Add `MessageStatus` to the type imports of `events.ts`.

- [ ] **Step 3: Write the hook**

```typescript
// frontend/src/hooks/useEventMessages.ts
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchEventMessages } from "../api/events";
import type { EventMessage } from "../types";

const POLL_MS = 5000;

/**
 * Cursor-polling event Winlink messages. Same accumulate-and-dedupe contract as
 * the log/positions hooks: the server returns rows with msg_seq > since; we
 * accumulate, dedupe by msg_seq, and replace a row when its status changes
 * (status is mutable, so a changed row can re-appear on a later poll with a
 * higher seq — but status changes do NOT bump msg_seq, so we also refetch the
 * full unread/dismissed state by re-reading from since=0 on each mount).
 * Polls only while `enabled` and the tab is visible.
 */
export function useEventMessages(netSlug: string, eventId: number, enabled: boolean) {
  const [messages, setMessages] = useState<EventMessage[]>([]);
  const [latestMsgSeq, setLatestMsgSeq] = useState(0);
  const byId = useRef<Map<number, EventMessage>>(new Map());
  const sinceRef = useRef(0);

  const refresh = useCallback(async () => {
    try {
      // Always re-read from 0: message status (read/dismissed) is mutable and
      // does not bump msg_seq, so a pure delta would miss status flips. The
      // message set per event is small (dozens), so a full read every 5s is fine.
      const u = await fetchEventMessages(eventId, 0, netSlug, true);
      const map = new Map<number, EventMessage>();
      for (const m of u.messages) map.set(m.id, m);
      byId.current = map;
      sinceRef.current = u.latest_msg_seq;
      setMessages([...map.values()].sort((a, b) => b.msg_seq - a.msg_seq));
      setLatestMsgSeq(u.latest_msg_seq);
    } catch {
      // keep last-known messages on a failed poll
    }
  }, [netSlug, eventId]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh, enabled]);

  const unreadCount = messages.filter((m) => m.status === "unread").length;
  return { messages, latestMsgSeq, unreadCount, refresh };
}
```

Note: this hook fetches with `include_dismissed=true` and filters in the component, so the "include dismissed" toggle is a pure client-side view flip with no extra request. The plan's earlier cursor discussion is superseded by this simpler full-read approach — justified because per-event message volume is small and status is mutable (a delta cursor can't see status flips). Record this as a deliberate deviation in the task report.

- [ ] **Step 4: Build check**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: builds cleanly.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/api/events.ts frontend/src/hooks/useEventMessages.ts
git commit -m "feat(events): frontend types, API, and messages polling hook"
```

---

### Task 7: MessagesPanel + dashboard integration

**Files:**
- Create: `frontend/src/pages/events/MessagesPanel.tsx`
- Create: `frontend/src/pages/events/MessageComposer.tsx`
- Modify: `frontend/src/pages/events/EventDashboardPage.tsx` (mount panel + hook)

**Interfaces:**
- Consumes: Task 6 hook/API; `useToast`; dashboard `canWrite`/`event`/`participants`.
- Produces: a Messages section on the dashboard (unread badge, filter chips, message open + reply/dismiss, composer modal, "Check mail now" button). Read-only for viewers.

- [ ] **Step 1: Write MessageComposer**

```tsx
// frontend/src/pages/events/MessageComposer.tsx
import { useState } from "react";
import { composeEventMessage } from "../../api/events";
import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Modal } from "../../components/Modal";
import type { EventMessage } from "../../types";

interface MessageComposerProps {
  netSlug: string;
  eventId: number;
  open: boolean;
  onClose: () => void;
  replyTo?: EventMessage | null;
  onSent: () => Promise<void>;
  onError: (message: string) => void;
}

function replyDefaults(replyTo: EventMessage | null | undefined) {
  if (!replyTo) return { to: "", subject: "", body: "" };
  const subject = replyTo.subject.replace(/^(re:\s*)+/i, "");
  const quoted = replyTo.body.split("\n").map((l) => `> ${l}`).join("\n");
  return { to: replyTo.from_callsign, subject: `Re: ${subject}`, body: `\n\n${quoted}` };
}

export function MessageComposer({ netSlug, eventId, open, onClose, replyTo, onSent, onError }: MessageComposerProps) {
  const defaults = replyDefaults(replyTo);
  const [to, setTo] = useState(defaults.to);
  const [subject, setSubject] = useState(defaults.subject);
  const [body, setBody] = useState(defaults.body);
  const [busy, setBusy] = useState(false);

  // Re-seed fields when the reply target changes (modal re-opened for a different message).
  // Keyed remount from the parent (key={replyTo?.id ?? "new"}) makes this reliable.

  async function submit() {
    if (!to.trim() || busy) return;
    setBusy(true);
    try {
      const { delivered } = await composeEventMessage(
        eventId,
        { to_address: to.trim(), subject, body, reply_to_id: replyTo?.id ?? null },
        netSlug,
      );
      onClose();
      await onSent();
      if (!delivered) onError("Message saved but not delivered — check delivery settings / retry.");
    } catch (e) {
      onError(e instanceof Error ? e.message : "Send failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={replyTo ? "Reply" : "New Winlink message"} size="lg">
      <div className="flex flex-col gap-3">
        <Input label="To" value={to} onChange={(e) => setTo(e.target.value)} placeholder="KE0XYZ or name@agency.org" mono />
        <Input label="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
        <label className="text-sm text-text-secondary">
          Body
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={8}
            className="mt-1 block w-full rounded-md bg-bg-elevated border border-border px-3 py-2 text-sm text-text-primary"
          />
        </label>
        <div className="flex gap-2 justify-end pt-1">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button loading={busy} disabled={!to.trim()} onClick={() => void submit()}>Send</Button>
        </div>
      </div>
    </Modal>
  );
}
```

- [ ] **Step 2: Write MessagesPanel**

```tsx
// frontend/src/pages/events/MessagesPanel.tsx
import { useMemo, useState } from "react";
import { rescanEventMailbox, setEventMessageStatus } from "../../api/events";
import { Button } from "../../components/Button";
import type { EventMessage, NetEvent } from "../../types";
import { MessageComposer } from "./MessageComposer";

type Filter = "all" | "unread" | "inbound" | "outbound";

interface MessagesPanelProps {
  netSlug: string;
  event: NetEvent;
  messages: EventMessage[];
  canWrite: boolean;
  onChanged: () => Promise<void>;
  onError: (message: string) => void;
}

export function MessagesPanel({ netSlug, event, messages, canWrite, onChanged, onError }: MessagesPanelProps) {
  const [filter, setFilter] = useState<Filter>("all");
  const [includeDismissed, setIncludeDismissed] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const [replyTo, setReplyTo] = useState<EventMessage | null>(null);
  const [composeOpen, setComposeOpen] = useState(false);
  const [rescanning, setRescanning] = useState(false);

  const visible = useMemo(() => {
    return messages.filter((m) => {
      if (!includeDismissed && m.status === "dismissed") return false;
      if (filter === "unread") return m.status === "unread";
      if (filter === "inbound") return m.direction === "inbound";
      if (filter === "outbound") return m.direction === "outbound";
      return true;
    });
  }, [messages, filter, includeDismissed]);

  const active = event.status === "active";

  async function open(m: EventMessage) {
    setOpenId(openId === m.id ? null : m.id);
    if (m.status === "unread" && m.direction === "inbound" && canWrite && active) {
      try {
        await setEventMessageStatus(event.id, m.id, "read", netSlug);
        await onChanged();
      } catch { /* non-fatal */ }
    }
  }

  async function dismiss(m: EventMessage) {
    try {
      await setEventMessageStatus(event.id, m.id, "dismissed", netSlug);
      await onChanged();
    } catch (e) {
      onError(e instanceof Error ? e.message : "Dismiss failed");
    }
  }

  async function rescan() {
    setRescanning(true);
    try {
      const { new_messages } = await rescanEventMailbox(event.id, netSlug);
      await onChanged();
      onError(new_messages > 0 ? `${new_messages} new message(s)` : "No new mail");
    } catch (e) {
      onError(e instanceof Error ? e.message : "Re-scan failed");
    } finally {
      setRescanning(false);
    }
  }

  return (
    <div className="rounded-md border border-border bg-bg-surface p-3">
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <h2 className="text-sm font-semibold text-text-primary">Messages</h2>
        <div className="flex gap-1">
          {(["all", "unread", "inbound", "outbound"] as Filter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-2 py-0.5 rounded text-xs ${filter === f ? "bg-accent/15 text-accent" : "text-text-muted hover:text-text-primary"}`}
            >
              {f}
            </button>
          ))}
        </div>
        <label className="text-xs text-text-muted flex items-center gap-1 ml-2">
          <input type="checkbox" checked={includeDismissed} onChange={(e) => setIncludeDismissed(e.target.checked)} />
          dismissed
        </label>
        {canWrite && active && (
          <div className="ml-auto flex gap-2">
            <Button size="sm" variant="secondary" loading={rescanning} onClick={() => void rescan()}>
              Check mail now
            </Button>
            <Button size="sm" onClick={() => { setReplyTo(null); setComposeOpen(true); }}>
              New message
            </Button>
          </div>
        )}
      </div>

      {visible.length === 0 ? (
        <p className="text-text-muted text-sm">No messages.</p>
      ) : (
        <div className="flex flex-col gap-1">
          {visible.map((m) => (
            <div key={m.id} className="border-b border-border pb-1">
              <div
                onClick={() => void open(m)}
                className="flex items-center gap-2 cursor-pointer py-1 text-sm"
              >
                {m.status === "unread" && m.direction === "inbound" && (
                  <span className="h-2 w-2 rounded-full bg-accent shrink-0" />
                )}
                <span className="font-mono text-text-primary">
                  {m.direction === "inbound" ? m.from_callsign : `→ ${m.to_address}`}
                </span>
                <span className="text-text-secondary truncate flex-1">{m.subject || "(no subject)"}</span>
                <span className="text-xs text-text-muted shrink-0">
                  {new Date(m.received_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
              </div>
              {openId === m.id && (
                <div className="pl-4 pb-2 text-sm">
                  <pre className="whitespace-pre-wrap font-sans text-text-secondary">{m.body}</pre>
                  {canWrite && active && (
                    <div className="flex gap-2 mt-2">
                      {m.direction === "inbound" && (
                        <button
                          onClick={() => { setReplyTo(m); setComposeOpen(true); }}
                          className="text-xs text-accent hover:underline"
                        >
                          Reply
                        </button>
                      )}
                      {m.status !== "dismissed" && (
                        <button onClick={() => void dismiss(m)} className="text-xs text-text-muted hover:text-danger">
                          Dismiss
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <MessageComposer
        key={replyTo?.id ?? "new"}
        netSlug={netSlug}
        eventId={event.id}
        open={composeOpen}
        onClose={() => setComposeOpen(false)}
        replyTo={replyTo}
        onSent={onChanged}
        onError={onError}
      />
    </div>
  );
}
```

- [ ] **Step 3: Mount in the dashboard**

In `frontend/src/pages/events/EventDashboardPage.tsx`:
- Import `MessagesPanel` and `useEventMessages`.
- Add hook + state near the other hooks (BEFORE the early-return spinner):

```tsx
  const [messagesOpen, setMessagesOpen] = useState(false);
  const eventMessages = useEventMessages(
    slug!,
    Number(eventId),
    messagesOpen || (updates?.event.status === "active"),
  );
```

  (Enabled whenever the event is active — the unread badge must update without the panel being expanded; message volume is small so this is cheap.)
- Render the panel below the participant/log grid (or as a collapsible section header showing the unread badge):

```tsx
      <div className="mt-6">
        <button
          onClick={() => setMessagesOpen(!messagesOpen)}
          className="text-sm font-semibold text-text-primary hover:text-accent flex items-center gap-2"
        >
          {messagesOpen ? "▾" : "▸"} Messages
          {eventMessages.unreadCount > 0 && (
            <span className="px-1.5 py-0.5 rounded-full text-xs bg-accent text-bg-base">
              {eventMessages.unreadCount}
            </span>
          )}
        </button>
        {messagesOpen && (
          <div className="mt-2">
            <MessagesPanel
              netSlug={slug!}
              event={event}
              messages={eventMessages.messages}
              canWrite={canWrite}
              onChanged={eventMessages.refresh}
              onError={onError}
            />
          </div>
        )}
      </div>
```

  Reuse the existing `onError` toast helper already defined in the dashboard (from SP1). If the dashboard's `onError` only surfaces errors, that's fine — the rescan "N new messages" success also routes through it as an info toast; if you'd rather show success distinctly, use `addToast(msg, "success")` directly in `MessagesPanel.rescan` instead of `onError`. Keep it simple: route through `onError` for now and note it.

- [ ] **Step 4: Build check**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: builds cleanly.

- [ ] **Step 5: Full backend suite + lint + commit**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add frontend/src/pages/events/MessagesPanel.tsx frontend/src/pages/events/MessageComposer.tsx frontend/src/pages/events/EventDashboardPage.tsx
git commit -m "feat(events): Messages panel with compose, reply, dismiss, and re-scan"
```

- [ ] **Step 6: Manual smoke test (human checkpoint)**

With `./run-dev.sh` and a net configured with `pat_mailbox_path` + `net_address`: activate an event, drop a test `.b2f` into `{mailbox}/in` addressed to the net, click "Check mail now" → message appears in the panel with an unread dot and a log breadcrumb; open it (marks read); reply (prefilled to/subject/quoted body); dismiss one and toggle "dismissed" to see it again; compose a new message; verify a viewer account sees the panel read-only. Confirm a message ingested while a check-in session is also active appears in both the Messages panel and the check-ins list.
