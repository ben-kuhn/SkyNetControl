# Net-Independent Events — EP1 (Backend Decoupling) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple events from nets on the backend — events become first-class, net-independent entities with event-level ownership/permissions, event-scoped config (override → global), and top-level `/api/events` routes.

**Architecture:** Drop `events.net_id`; add event ownership (`created_by` = owner) + `event_operators` (co-ops) + `public_token`/`visibility`. Replace `require_net_role` with `require_event_role` (CONTROL = owner/operator/admin; READ adds public + anonymous-via-token). Replace `get_net_config(event.net_id, …)` with `get_event_config(event.id, …)` (event override → global `AppConfig` → default) across every integration, including a scope-aware delivery seam. Reset (delete) existing events in the migration. Frontend is EP2 — NOT in this plan.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic (SQLite via `batch_alter_table`; PostgreSQL supported), pytest + httpx AsyncClient. No new dependencies.

## Global Constraints

- **Events are net-free.** `events.net_id` is dropped (column + FK). No event code path references `event.net_id` after this plan. `pat_connection_sessions.net_id` becomes **nullable** (net-scoped OR event-scoped sessions).
- **Permissions:** `require_event_role(min_role)` — CONTROL = event owner (`created_by`) OR a row in `event_operators` OR `user.is_admin`; READ = CONTROL, OR `event.visibility == "public"` for any authenticated user, OR anonymous with a valid `public_token`. Owner-only actions: delete, manage operators, toggle visibility, rotate token, transfer. Create needs only an approved (`is_pending=False`) logged-in user.
- **Config:** `get_event_config(db, event_id, key, default=None)` resolves **event override → global `AppConfig` (`get_config_value`) → default**. Sensitive keys (`SENSITIVE_KEY_FRAGMENTS = ("api_key","password","secret","token")`) are `secret_box.encrypt`'d on write and decrypted on read; never returned plaintext by config reads. The from-identity callsign / `net_address` defaults to the event **creator's callsign** when unset.
- **`visibility`** is exactly `"private"` (default) | `"public"`. `public_token` is `secrets.token_urlsafe(16)`, rotatable; a rotated token 404s old links (no enumeration signal).
- **Delivery is scope-aware:** net workflows (roster/reminder) keep net-config sourcing; event messages resolve from event config; same backend send machinery. Existing net-delivery tests stay green.
- **Reset migration:** delete all existing events + child rows FIRST, then drop `events.net_id`. Down-revision is the current head `f62789139379`.
- **Routes** live at top-level `/api/events/…` (no `{net_slug}`). Alembic head is `f62789139379`.
- Ruff: line-length 120, `select = ["E","F"]`, no per-file ignores in production code. `nix-shell --run "ruff check"` before every commit. Backend tests `.venv/bin/pytest -q`.

## File Structure

- `backend/modules/events/models.py` (modify) — Event: drop `net_id`, add `public_token`, `visibility`; new `EventOperator`, `EventConfig` models.
- `backend/integrations/winlink/models.py` (modify) — `PatConnectionSession.net_id` nullable.
- `alembic/versions/<rev>_net_independent_events.py` (new) — reset + schema migration.
- `backend/modules/events/event_config_service.py` (new) — `get_event_config`/`set_event_config`/`set_event_config_bulk`, `event_from_callsign`.
- `backend/modules/events/event_auth.py` (new) — `EventRole`, `EventContext`, `require_event_role`, `resolve_public_event`.
- `backend/modules/events/service.py` (modify) — `create_event` drops `net_id`, sets owner; operator helpers (`add_operator`/`remove_operator`/`transfer_owner`); token/visibility helpers.
- `backend/integrations/aprs/manager.py` (modify) — `aprs_config` reads event config.
- `backend/integrations/weather/service.py` (modify) — read event config.
- `backend/modules/events/message_service.py` (modify) — read event config; dispatch via event scope.
- `backend/integrations/winlink/pat_config.py`, `pat_routes.py`, `pat_session.py` (modify) — event-scoped config + nullable session net_id.
- `backend/integrations/delivery/service.py` (modify) — scope-aware `dispatch_delivery`/`retry_failed`/`_build_config`.
- `backend/modules/roster/service.py`, `backend/modules/reminders/service.py`, `backend/integrations/delivery/routes.py` (modify) — pass a net scope to delivery.
- `backend/modules/events/routes.py` (rewrite) — top-level `/api/events` router, `require_event_role`, owner-only + public routes.
- `backend/app.py` (modify) — mount the new router; drop the net-scoped events router.
- Tests: `tests/test_event_config_service.py`, `tests/test_event_auth.py`, `tests/test_event_routes_standalone.py`, `tests/test_event_delivery_scope.py` (new), plus updates to existing event-route tests.

---

### Task 1: Schema — drop net_id, add ownership/config tables, reset migration

**Files:**
- Modify: `backend/modules/events/models.py`
- Modify: `backend/integrations/winlink/models.py`
- Create: `alembic/versions/a7b1c9d3e5f0_net_independent_events.py`
- Create: `tests/test_event_models_standalone.py`

**Interfaces:**
- Produces: `Event` (no `net_id`; new `public_token: str`, `visibility: str`); `EventOperator(event_id, callsign, added_by, added_at)`; `EventConfig(event_id, key, value)`; `EventVisibility` = `"private"`/`"public"` string constants. `PatConnectionSession.net_id: int | None`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_event_models_standalone.py
from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import Event, EventOperator, EventConfig, EventType, EventStatus


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_event_has_no_net_id_and_has_ownership_columns():
    cols = {c.name for c in Event.__table__.columns}
    assert "net_id" not in cols
    assert {"created_by", "public_token", "visibility"} <= cols


def test_event_row_roundtrip_without_net():
    db = _db()
    ev = Event(name="Skywarn", event_type=EventType.EMERGENCY, status=EventStatus.DRAFT,
               created_by="W0NC", public_token="tok123", visibility="private")
    db.add(ev); db.commit()
    got = db.query(Event).one()
    assert got.created_by == "W0NC"
    assert got.visibility == "private"


def test_event_operator_and_config_tables():
    db = _db()
    ev = Event(name="E", event_type=EventType.EMERGENCY, status=EventStatus.DRAFT,
               created_by="W0NC", public_token="t", visibility="private")
    db.add(ev); db.flush()
    db.add(EventOperator(event_id=ev.id, callsign="KD0OP", added_by="W0NC",
                         added_at=datetime.now(tz=timezone.utc)))
    db.add(EventConfig(event_id=ev.id, key="aprs.callsign", value="W0NC-9"))
    db.commit()
    assert db.query(EventOperator).one().callsign == "KD0OP"
    assert db.query(EventConfig).one().value == "W0NC-9"


def test_pat_session_net_id_nullable():
    from backend.integrations.winlink.models import PatConnectionSession
    assert PatConnectionSession.__table__.c.net_id.nullable is True
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/test_event_models_standalone.py -q` — FAIL.

- [ ] **Step 3: Modify the `Event` model + add `EventOperator`/`EventConfig`**

In `backend/modules/events/models.py`:
- Delete the `net_id` column line (`net_id: Mapped[int] = mapped_column(ForeignKey("nets.id"), nullable=False)`).
- Add after `created_by`:
```python
    public_token: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
```
- Add the two new models (with the other event models, before the module end):
```python
class EventOperator(Base):
    __tablename__ = "event_operators"
    __table_args__ = (UniqueConstraint("event_id", "callsign", name="uq_event_operator"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    callsign: Mapped[str] = mapped_column(String(20), nullable=False)
    added_by: Mapped[str] = mapped_column(String(20), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class EventConfig(Base):
    __tablename__ = "event_config"

    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
```
Ensure `UniqueConstraint`, `String`, `Text`, `Integer`, `DateTime`, `ForeignKey` are imported (mirror existing imports). `_utcnow` already exists in the file.

- [ ] **Step 4: Make `PatConnectionSession.net_id` nullable**

In `backend/integrations/winlink/models.py`, change:
```python
    net_id: Mapped[int] = mapped_column(ForeignKey("nets.id", ondelete="CASCADE"), nullable=False)
```
to
```python
    net_id: Mapped[int | None] = mapped_column(ForeignKey("nets.id", ondelete="CASCADE"), nullable=True)
```

- [ ] **Step 5: Write the reset + schema migration**

`.venv/bin/alembic heads` should print `f62789139379`. Create `alembic/versions/a7b1c9d3e5f0_net_independent_events.py`:
```python
"""net-independent events (reset)

Revision ID: a7b1c9d3e5f0
Revises: f62789139379
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b1c9d3e5f0"
down_revision: Union[str, None] = "f62789139379"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Reset: delete all existing events + child rows (clean slate, no preservation).
    #    event-scoped PAT sessions first (FK to events), then event children, then events.
    op.execute("UPDATE pat_connection_sessions SET event_id = NULL WHERE event_id IS NOT NULL")
    op.execute("DELETE FROM event_message_forms")
    op.execute("DELETE FROM event_messages")
    op.execute("DELETE FROM event_log")
    op.execute("DELETE FROM event_participants")
    op.execute("DELETE FROM event_posts")
    op.execute("DELETE FROM events")

    # 2. New tables.
    op.create_table(
        "event_operators",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("callsign", sa.String(length=20), nullable=False),
        sa.Column("added_by", sa.String(length=20), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "callsign", name="uq_event_operator"),
    )
    op.create_table(
        "event_config",
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id", "key"),
    )

    # 3. events: add ownership columns, drop net_id.
    with op.batch_alter_table("events") as batch:
        batch.add_column(sa.Column("public_token", sa.String(length=64), nullable=False, server_default=""))
        batch.add_column(sa.Column("visibility", sa.String(length=16), nullable=False, server_default="private"))
        batch.create_index("ix_events_public_token", ["public_token"])
        batch.drop_column("net_id")
    # server_default was only to satisfy the NOT NULL add on an (now empty) table; drop it.
    with op.batch_alter_table("events") as batch:
        batch.alter_column("public_token", server_default=None)
        batch.alter_column("visibility", server_default=None)

    # 4. pat_connection_sessions.net_id -> nullable.
    with op.batch_alter_table("pat_connection_sessions") as batch:
        batch.alter_column("net_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("pat_connection_sessions") as batch:
        batch.alter_column("net_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("events") as batch:
        batch.drop_index("ix_events_public_token")
        batch.add_column(sa.Column("net_id", sa.Integer(), nullable=True))  # data gone; nullable on downgrade
        batch.drop_column("visibility")
        batch.drop_column("public_token")
    op.drop_table("event_config")
    op.drop_table("event_operators")
```
Note: the two-step server_default add-then-clear keeps SQLite happy on a table that may have rows deleted just above (it's empty, so the NOT NULL add is safe). Verify empirically in Step 6.

- [ ] **Step 6: Run tests + migration chain**

Run: `.venv/bin/pytest tests/test_event_models_standalone.py -q` — PASS.
Run: `SKYNET_DATABASE_URL="sqlite:////tmp/ep1-mig.db" .venv/bin/alembic upgrade head && SKYNET_DATABASE_URL="sqlite:////tmp/ep1-mig.db" .venv/bin/alembic downgrade -1 && rm -f /tmp/ep1-mig.db` — clean up+down. Then `nix-shell --run "ruff check"`.
Regression note: many existing tests still reference `event.net_id` / net-scoped event routes and WILL fail now — that is expected; later tasks (8–13) rewrite them. Do NOT try to keep them green here; only the model + migration tests must pass. Confirm `.venv/bin/pytest tests/test_event_models_standalone.py -q` is green and commit.

- [ ] **Step 7: Commit**

```bash
git add backend/modules/events/models.py backend/integrations/winlink/models.py alembic/versions/a7b1c9d3e5f0_net_independent_events.py tests/test_event_models_standalone.py
git commit -m "feat(events): drop net_id, add ownership + event_config schema (reset migration)"
```

---

### Task 2: Event config service (`event_config_service.py`)

**Files:**
- Create: `backend/modules/events/event_config_service.py`
- Create: `tests/test_event_config_service.py`

**Interfaces:**
- Consumes: `EventConfig`/`Event` (Task 1); `get_config_value` (`backend/config_mgmt/service.py`); `is_sensitive_key` (same); `secret_box.encrypt/decrypt` (`backend/auth/secret_box.py`).
- Produces: `get_event_config(db, event_id, key, default=None) -> str | None` (event override → global → default; decrypts sensitive); `set_event_config(db, event_id, key, value)`; `set_event_config_bulk(db, event_id, values: dict)`; `event_from_callsign(db, event) -> str` (event `net_address` override → global → creator callsign).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_event_config_service.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth import secret_box
from backend.db.base import Base
from backend.config_mgmt.service import set_config_value
from backend.modules.events.models import Event, EventType, EventStatus
from backend.modules.events.event_config_service import (
    get_event_config, set_event_config, event_from_callsign,
)

secret_box.install_key_material("test-secret")


def _db_event(callsign="W0NC"):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    ev = Event(name="E", event_type=EventType.EMERGENCY, status=EventStatus.ACTIVE,
               created_by=callsign, public_token="t", visibility="private")
    db.add(ev); db.flush()
    return db, ev


def test_override_beats_global_beats_default():
    db, ev = _db_event()
    assert get_event_config(db, ev.id, "aprs.server", "d") == "d"           # default
    set_config_value(db, "aprs.server", "global.aprs2.net")
    assert get_event_config(db, ev.id, "aprs.server", "d") == "global.aprs2.net"  # global
    set_event_config(db, ev.id, "aprs.server", "event.aprs2.net")
    assert get_event_config(db, ev.id, "aprs.server", "d") == "event.aprs2.net"   # override


def test_sensitive_key_encrypted_at_rest_and_decrypted_on_read():
    db, ev = _db_event()
    set_event_config(db, ev.id, "pat_http_password", "s3cret")
    from backend.modules.events.models import EventConfig
    raw = db.get(EventConfig, (ev.id, "pat_http_password")).value
    assert raw != "s3cret" and raw.startswith("enc:")
    assert get_event_config(db, ev.id, "pat_http_password") == "s3cret"


def test_from_callsign_defaults_to_creator():
    db, ev = _db_event(callsign="KE0ABC")
    assert event_from_callsign(db, ev) == "KE0ABC"          # default: creator
    set_event_config(db, ev.id, "net_address", "W0NE@winlink.org")
    assert event_from_callsign(db, ev) == "W0NE"            # override wins
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/pytest tests/test_event_config_service.py -q` — FAIL.

- [ ] **Step 3: Implement `event_config_service.py`**

```python
# backend/modules/events/event_config_service.py
"""Per-event config: event override -> global AppConfig -> default. Sensitive keys
are encrypted at rest and decrypted on read, mirroring net/global config."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.auth import secret_box
from backend.config_mgmt.service import get_config_value, is_sensitive_key
from backend.modules.events.models import Event, EventConfig


def get_event_config(db: Session, event_id: int, key: str, default: str | None = None) -> str | None:
    row = db.get(EventConfig, (event_id, key))
    if row is not None:
        return secret_box.decrypt(row.value) if is_sensitive_key(key) else row.value
    # fall back to global AppConfig (which itself decrypts sensitive globals), then default
    return get_config_value(db, key, default)


def set_event_config(db: Session, event_id: int, key: str, value: str) -> None:
    stored = secret_box.encrypt(value) if (is_sensitive_key(key) and value) else value
    row = db.get(EventConfig, (event_id, key))
    if row is None:
        db.add(EventConfig(event_id=event_id, key=key, value=stored))
    else:
        row.value = stored
        row.updated_at = datetime.now(timezone.utc)
    db.commit()


def set_event_config_bulk(db: Session, event_id: int, values: dict[str, str]) -> None:
    now = datetime.now(timezone.utc)
    for key, value in values.items():
        stored = secret_box.encrypt(value) if (is_sensitive_key(key) and value) else value
        row = db.get(EventConfig, (event_id, key))
        if row is None:
            db.add(EventConfig(event_id=event_id, key=key, value=stored))
        else:
            row.value = stored
            row.updated_at = now
    db.commit()


def event_from_callsign(db: Session, event: Event) -> str:
    """The event's Winlink/APRS 'from' callsign: net_address override -> global ->
    the event creator's callsign."""
    net_address = get_event_config(db, event.id, "net_address", "") or ""
    if net_address:
        return net_address.split("@")[0].upper()
    return (event.created_by or "").upper()
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_event_config_service.py -q` — PASS. Then `nix-shell --run "ruff check"`.

- [ ] **Step 5: Commit**

```bash
git add backend/modules/events/event_config_service.py tests/test_event_config_service.py
git commit -m "feat(events): event config service (override -> global) with secret + identity default"
```

---

### Task 3: Event auth (`event_auth.py`) — `require_event_role`

**Files:**
- Create: `backend/modules/events/event_auth.py`
- Create: `tests/test_event_auth.py`

**Interfaces:**
- Consumes: `get_current_user`, `get_db_session`, `get_optional_user` (see note) from `backend/auth/dependencies.py`; `Event`, `EventOperator` (Task 1); `User` (`backend/auth/models.py`).
- Produces: `EventRole` (`READ`/`CONTROL` str-enum); `EventContext(user: User | None, event: Event, is_control: bool)`; `require_event_role(min_role: EventRole) -> dependency` reading `{event_id}` path param + optional `token` query param; `event_has_control(db, user, event) -> bool`.

- [ ] **Step 1: Note on the optional-user dependency**

`require_event_role(READ)` must allow anonymous callers (for public events via token). Check `backend/auth/dependencies.py` for an existing optional-user dependency (one that yields `User | None` without 401). The net public-read path (`require_net_read`) already does this — reuse its user-resolution helper. If there is no reusable `get_optional_user`, add one in `dependencies.py` that mirrors `get_current_user` but returns `None` instead of raising on a missing/invalid token. Use it in `require_event_role`.

- [ ] **Step 2: Write failing tests**

```python
# tests/test_event_auth.py
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.config import Settings
from backend.db.base import Base
from backend.auth.models import User
from backend.modules.events.models import Event, EventOperator, EventType, EventStatus
from backend.modules.events.event_auth import EventRole, EventContext, require_event_role
from backend.auth.dependencies import get_db_session
from tests.conftest import make_test_token


@pytest.fixture
def app_ctx():
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret")
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([User(callsign="OWNER", oidc_subject="x|o", name="O"),
                    User(callsign="OP", oidc_subject="x|op", name="P"),
                    User(callsign="OTHER", oidc_subject="x|ot", name="T"),
                    User(callsign="ADM", oidc_subject="x|a", name="A", is_admin=True)])
        priv = Event(name="P", event_type=EventType.EMERGENCY, status=EventStatus.ACTIVE,
                     created_by="OWNER", public_token="ptok", visibility="private")
        pub = Event(name="U", event_type=EventType.EMERGENCY, status=EventStatus.ACTIVE,
                    created_by="OWNER", public_token="utok", visibility="public")
        db.add_all([priv, pub]); db.flush()
        db.add(EventOperator(event_id=priv.id, callsign="OP", added_by="OWNER",
                             added_at=__import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc)))
        db.commit()
        ids = {"priv": priv.id, "pub": pub.id}
    app = FastAPI(); app.state.session_factory = factory; app.state.settings = settings

    @app.get("/ev/{event_id}/read")
    async def read(ctx: EventContext = Depends(require_event_role(EventRole.READ))):
        return {"control": ctx.is_control}

    @app.get("/ev/{event_id}/control")
    async def control(ctx: EventContext = Depends(require_event_role(EventRole.CONTROL))):
        return {"ok": True}

    return app, settings, ids


def _c(app, settings, callsign=None, **kw):
    cookies = {}
    if callsign:
        cookies["access_token"] = make_test_token(callsign, settings, **kw)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies)


@pytest.mark.asyncio
async def test_control_matrix(app_ctx):
    app, settings, ids = app_ctx
    p = ids["priv"]
    async with _c(app, settings, "OWNER") as c:  assert (await c.get(f"/ev/{p}/control")).status_code == 200
    async with _c(app, settings, "OP") as c:     assert (await c.get(f"/ev/{p}/control")).status_code == 200
    async with _c(app, settings, "ADM", is_admin=True) as c: assert (await c.get(f"/ev/{p}/control")).status_code == 200
    async with _c(app, settings, "OTHER") as c:  assert (await c.get(f"/ev/{p}/control")).status_code == 403
    async with _c(app, settings) as c:           assert (await c.get(f"/ev/{p}/control")).status_code == 401


@pytest.mark.asyncio
async def test_read_matrix(app_ctx):
    app, settings, ids = app_ctx
    priv, pub = ids["priv"], ids["pub"]
    # private: only control users read
    async with _c(app, settings, "OTHER") as c:  assert (await c.get(f"/ev/{priv}/read")).status_code == 403
    # public: any authed user reads
    async with _c(app, settings, "OTHER") as c:  assert (await c.get(f"/ev/{pub}/read")).status_code == 200
    # public: anonymous with correct token reads; wrong/absent token 404
    async with _c(app, settings) as c:           assert (await c.get(f"/ev/{pub}/read?token=utok")).status_code == 200
    async with _c(app, settings) as c:           assert (await c.get(f"/ev/{pub}/read?token=bad")).status_code == 404
    async with _c(app, settings) as c:           assert (await c.get(f"/ev/{priv}/read?token=ptok")).status_code == 404
```

- [ ] **Step 3: Run tests, verify fail**

Run: `.venv/bin/pytest tests/test_event_auth.py -q` — FAIL.

- [ ] **Step 4: Implement `event_auth.py`**

```python
# backend/modules/events/event_auth.py
"""Event-level authorization. CONTROL = owner / co-operator / admin. READ = CONTROL,
or a public event for any authenticated user, or anonymous with a valid public_token."""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session, get_optional_user
from backend.auth.models import User
from backend.modules.events.models import Event, EventOperator


class EventRole(str, enum.Enum):
    READ = "read"
    CONTROL = "control"


@dataclass
class EventContext:
    user: User | None
    event: Event
    is_control: bool


def event_has_control(db: Session, user: User | None, event: Event) -> bool:
    if user is None:
        return False
    if user.is_admin or event.created_by == user.callsign:
        return True
    # EventOperator has an integer PK + a (event_id, callsign) unique constraint — query it.
    return (
        db.query(EventOperator)
          .filter(EventOperator.event_id == event.id, EventOperator.callsign == user.callsign)
          .first()
        is not None
    )


def require_event_role(min_role: EventRole) -> Callable:
    def dep(
        event_id: int = Path(...),
        token: str | None = Query(default=None),
        user: User | None = Depends(get_optional_user),
        db: Session = Depends(get_db_session),
    ) -> EventContext:
        event = db.get(Event, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found")
        control = event_has_control(db, user, event)

        if min_role == EventRole.CONTROL:
            if user is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if not control:
                raise HTTPException(status_code=403, detail="Not authorized for this event")
            return EventContext(user=user, event=event, is_control=True)

        # READ
        if control:
            return EventContext(user=user, event=event, is_control=True)
        if event.visibility == "public":
            if user is not None:
                return EventContext(user=user, event=event, is_control=False)
            if token and token == event.public_token:
                return EventContext(user=None, event=event, is_control=False)
        # private-to-this-caller, or bad/absent token: 404 (no existence signal)
        raise HTTPException(status_code=404, detail="Event not found")

    return dep
```
If `get_optional_user` doesn't exist in `dependencies.py`, add it per Step 1.

- [ ] **Step 5: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_event_auth.py -q` — PASS. Then `nix-shell --run "ruff check"`.

- [ ] **Step 6: Commit**

```bash
git add backend/modules/events/event_auth.py backend/auth/dependencies.py tests/test_event_auth.py
git commit -m "feat(events): require_event_role event-level auth (control/read/public/anonymous)"
```

---

### Task 4: Event service layer — net-free create + ownership helpers

**Files:**
- Modify: `backend/modules/events/service.py`
- Create/extend: `tests/test_event_service_ownership.py`

**Interfaces:**
- Consumes: `Event`/`EventOperator` (Task 1).
- Produces: `create_event(db, *, name, event_type, created_by, description=None, scheduled_start=None) -> Event` (NO `net_id`; generates `public_token = secrets.token_urlsafe(16)`, `visibility="private"`, `created_by` = owner); `add_operator(db, event, callsign, added_by)`; `remove_operator(db, event, callsign)`; `transfer_owner(db, event, new_owner)`; `set_visibility(db, event, visibility)`; `rotate_public_token(db, event) -> str`; `list_operators(db, event) -> list[str]`. Existing lifecycle fns (`activate_event`/`close_event`/`reopen_event`/`update_event`/post/participant/log helpers) keep their signatures but must not reference `net_id`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_event_service_ownership.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import EventType
from backend.modules.events import service


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_create_event_sets_owner_and_token():
    db = _db()
    ev = service.create_event(db, name="Skywarn", event_type=EventType.EMERGENCY, created_by="W0NC")
    assert ev.created_by == "W0NC"
    assert ev.visibility == "private"
    assert ev.public_token and len(ev.public_token) >= 16
    assert not hasattr(ev, "net_id") or "net_id" not in {c.name for c in ev.__table__.columns}


def test_operator_add_remove_and_transfer():
    db = _db()
    ev = service.create_event(db, name="E", event_type=EventType.EMERGENCY, created_by="W0NC")
    service.add_operator(db, ev, "KD0OP", added_by="W0NC")
    assert service.list_operators(db, ev) == ["KD0OP"]
    service.remove_operator(db, ev, "KD0OP")
    assert service.list_operators(db, ev) == []
    service.transfer_owner(db, ev, "KE0NEW")
    assert ev.created_by == "KE0NEW"


def test_rotate_token_changes_it():
    db = _db()
    ev = service.create_event(db, name="E", event_type=EventType.EMERGENCY, created_by="W0NC")
    old = ev.public_token
    new = service.rotate_public_token(db, ev)
    assert new != old and ev.public_token == new
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/pytest tests/test_event_service_ownership.py -q` — FAIL.

- [ ] **Step 3: Update `create_event` + add ownership helpers**

In `backend/modules/events/service.py`:
- Change `create_event` to drop the `net_id` parameter and generate ownership fields:
```python
import secrets
# ...
def create_event(db: Session, *, name: str, event_type: EventType, created_by: str,
                 description: str | None = None, scheduled_start=None) -> Event:
    event = Event(
        name=name, event_type=event_type, status=EventStatus.DRAFT,
        description=description, scheduled_start=scheduled_start, created_by=created_by,
        public_token=secrets.token_urlsafe(16), visibility="private",
    )
    db.add(event); db.commit(); db.refresh(event)
    return event
```
- Add the ownership helpers:
```python
def add_operator(db: Session, event: Event, callsign: str, *, added_by: str) -> None:
    cs = callsign.strip().upper()
    exists = (db.query(EventOperator)
                .filter(EventOperator.event_id == event.id, EventOperator.callsign == cs).first())
    if exists is None and cs and cs != (event.created_by or "").upper():
        db.add(EventOperator(event_id=event.id, callsign=cs, added_by=added_by,
                             added_at=datetime.now(timezone.utc)))
        db.commit()

def remove_operator(db: Session, event: Event, callsign: str) -> None:
    (db.query(EventOperator)
       .filter(EventOperator.event_id == event.id, EventOperator.callsign == callsign.strip().upper())
       .delete())
    db.commit()

def list_operators(db: Session, event: Event) -> list[str]:
    return [o.callsign for o in db.query(EventOperator)
            .filter(EventOperator.event_id == event.id).order_by(EventOperator.callsign).all()]

def transfer_owner(db: Session, event: Event, new_owner: str) -> None:
    event.created_by = new_owner.strip().upper()
    db.commit()

def set_visibility(db: Session, event: Event, visibility: str) -> None:
    if visibility not in ("private", "public"):
        raise ValueError("visibility must be 'private' or 'public'")
    event.visibility = visibility
    db.commit()

def rotate_public_token(db: Session, event: Event) -> str:
    event.public_token = secrets.token_urlsafe(16)
    db.commit()
    return event.public_token
```
Import `EventOperator`, `secrets`, and (already present) `datetime`/`timezone`. Grep the file for any remaining `net_id` reference in `activate_event`/`close_event`/`reopen_event`/`update_event`/posts/participants/log and remove them (they should only touch `event_id`).

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_event_service_ownership.py -q` — PASS. `nix-shell --run "ruff check"`.

- [ ] **Step 5: Commit**

```bash
git add backend/modules/events/service.py tests/test_event_service_ownership.py
git commit -m "feat(events): net-free create_event + ownership/operator/token helpers"
```

---

### Task 5: APRS manager reads event config

**Files:**
- Modify: `backend/integrations/aprs/manager.py`
- Modify/extend: `tests/test_aprs_manager*.py` (or add `tests/test_aprs_event_config.py`)

**Interfaces:**
- Consumes: `get_event_config` + `event_from_callsign` (Task 2).
- Produces: `aprs_config(db, event_id) -> dict | None` reads `aprs.*` from **event** config (with the callsign defaulting to the event creator). `ensure_started` calls `aprs_config(db, event_id)`.

- [ ] **Step 1: Write a failing test**

```python
# tests/test_aprs_event_config.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import Event, EventType, EventStatus
from backend.modules.events.event_config_service import set_event_config
from backend.integrations.aprs.manager import aprs_config


def _db_event():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    ev = Event(name="E", event_type=EventType.EMERGENCY, status=EventStatus.ACTIVE,
               created_by="W0NC", public_token="t", visibility="private")
    db.add(ev); db.flush()
    return db, ev


def test_aprs_config_off_by_default():
    db, ev = _db_event()
    assert aprs_config(db, ev.id) is None


def test_aprs_config_from_event_with_creator_callsign_default():
    db, ev = _db_event()
    set_event_config(db, ev.id, "aprs.enabled", "true")
    cfg = aprs_config(db, ev.id)
    assert cfg["callsign"] == "W0NC"          # defaulted to creator
    assert cfg["server"] == "rotate.aprs2.net"
    assert cfg["port"] == 14580
    set_event_config(db, ev.id, "aprs.callsign", "W0NC-9")
    assert aprs_config(db, ev.id)["callsign"] == "W0NC-9"   # override wins
```

- [ ] **Step 2: Run test, verify fail**

Run: `.venv/bin/pytest tests/test_aprs_event_config.py -q` — FAIL.

- [ ] **Step 3: Re-source `aprs_config` + `ensure_started`**

In `backend/integrations/aprs/manager.py`, replace `aprs_config(db, net_id)`:
```python
from backend.modules.events.event_config_service import get_event_config, event_from_callsign
from backend.modules.events.models import Event

def aprs_config(db: Session, event_id: int) -> dict | None:
    """The event's APRS connection settings, or None when APRS is off/unusable."""
    if get_event_config(db, event_id, "aprs.enabled", "false") != "true":
        return None
    callsign = (get_event_config(db, event_id, "aprs.callsign", "") or "").strip()
    if not callsign:
        event = db.get(Event, event_id)
        callsign = event_from_callsign(db, event) if event else ""
    if not callsign:
        return None
    server = get_event_config(db, event_id, "aprs.server", "rotate.aprs2.net") or "rotate.aprs2.net"
    try:
        port = int(get_event_config(db, event_id, "aprs.port", "14580"))
    except (TypeError, ValueError):
        port = 14580
    return {"callsign": callsign, "server": server, "port": port}
```
In `ensure_started`, change `config = aprs_config(db, event.net_id)` → `config = aprs_config(db, event.id)`. Remove the now-unused `get_net_config` import if present.

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_aprs_event_config.py -q` and any existing `tests/test_aprs*` — PASS (update existing APRS tests that passed a `net_id` to `aprs_config` to pass an `event_id`). `nix-shell --run "ruff check"`.

- [ ] **Step 5: Commit**

```bash
git add backend/integrations/aprs/manager.py tests/test_aprs_event_config.py tests/test_aprs*
git commit -m "feat(aprs): source event APRS config from event_config (creator callsign default)"
```

---

### Task 6: Weather service reads event config

**Files:**
- Modify: `backend/integrations/weather/service.py`
- Modify: `tests/test_weather_service.py`

**Interfaces:**
- Consumes: `get_event_config`, `event_from_callsign` (Task 2).
- Produces: `_weather_enabled(db, event)`, `_user_agent(db, event)`, `_resolve_states(db, event, client)` read from **event** config; `get_event_alerts` unchanged externally.

- [ ] **Step 1: Update `test_weather_service.py`**

The existing tests set `set_net_config(db, net_id, "weather.enabled", "true")`. Change them to `set_event_config(db, ev.id, "weather.enabled", "true")` (import from `event_config_service`) and drop the `Net` setup (events are net-free — the `_db()` helper there builds an Event with `aprs_range_lat/lon` already). Keep the same assertions (`ok`/`disabled`/`no_area`/`stale`/`unavailable`, TTL, cache). Run to confirm they now fail against the old net-config code.

- [ ] **Step 2: Re-source the weather helpers**

In `backend/integrations/weather/service.py`, swap the three helpers to take the `event` (or event_id) and read event config:
```python
from backend.modules.events.event_config_service import get_event_config

def _weather_enabled(db, event) -> bool:
    return (get_event_config(db, event.id, "weather.enabled") or "").strip().lower() == "true"

def _user_agent(db, event) -> str:
    from backend.modules.events.event_config_service import event_from_callsign
    contact = (get_event_config(db, event.id, "weather.nws_contact") or "").strip()
    if not contact:
        na = (get_event_config(db, event.id, "net_address") or "").strip()
        contact = na or event_from_callsign(db, event)
    return f"SkyNetControl ({contact})" if contact else "SkyNetControl"

def _resolve_states(db, event, client) -> list[str]:
    raw = get_event_config(db, event.id, "weather.alert_states")
    # ... rest unchanged (json.loads / upper / fallback to lookup_state) ...
```
Update `get_event_alerts` to call `_weather_enabled(db, event)` / `_user_agent(db, event)` (it already has `event` in scope after `db.get(Event, event_id)`). Remove the `get_net_config`/`event.net_id` references.

- [ ] **Step 3: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_weather_service.py -q` — PASS. `nix-shell --run "ruff check"`.

- [ ] **Step 4: Commit**

```bash
git add backend/integrations/weather/service.py tests/test_weather_service.py
git commit -m "feat(weather): source event weather config from event_config"
```

---

### Task 7: Message service + PAT read event config; PAT session net-optional

**Files:**
- Modify: `backend/modules/events/message_service.py`
- Modify: `backend/integrations/winlink/pat_config.py`
- Modify: `backend/integrations/winlink/pat_routes.py`, `pat_session.py`
- Modify: `tests/test_event_form_compose.py`, `tests/test_winlink_pat_routes.py`, `tests/test_pat_config.py`

**Interfaces:**
- Consumes: `get_event_config`, `event_from_callsign` (Task 2); event-scoped dispatch from Task 8 (dispatch with `event_id=`).
- Produces: `resolve_pat_config_for_event(db, event_id) -> PatHttpConfig` and `pat_transport_enabled_for_event(db, event_id) -> bool` in `pat_config.py`; message_service reads `net_address`/callsign from event config; PAT routes/sessions use `event_id` (net_id optional).

- [ ] **Step 1: Add event-scoped PAT config**

In `backend/integrations/winlink/pat_config.py`, add an event-config reader mirroring `_get`/`resolve_pat_config` but sourcing per-event:
```python
from backend.modules.events.event_config_service import get_event_config

def _get_event(db, event_id, key, default=""):
    val = get_event_config(db, event_id, key)
    return val if val is not None else default

def resolve_pat_config_for_event(db, event_id) -> PatHttpConfig:
    enabled = _get_event(db, event_id, "pat_transport_enabled").strip().lower() == "true"
    base_url = _get_event(db, event_id, "pat_http_base_url").strip()
    mode = _get_event(db, event_id, "pat_http_auth_mode", "none").strip().lower() or "none"
    username = _get_event(db, event_id, "pat_http_username")
    password = _get_event(db, event_id, "pat_http_password")   # get_event_config already decrypts sensitive
    token = _get_event(db, event_id, "pat_http_token")
    raw_timeout = _get_event(db, event_id, "pat_http_timeout_seconds", "15").strip()
    try:
        timeout = float(raw_timeout)
    except ValueError:
        timeout = 15.0
    return PatHttpConfig(base_url=base_url, auth=PatAuth(mode=mode, username=username, password=password, token=token),
                         timeout=timeout, enabled=enabled)

def pat_transport_enabled_for_event(db, event_id) -> bool:
    cfg = resolve_pat_config_for_event(db, event_id)
    return cfg.enabled and bool(cfg.base_url)
```
(Note: `get_event_config` decrypts sensitive values, so no explicit `secret_box.decrypt` here — unlike the net `resolve_pat_config` which decrypts the raw net value.)

- [ ] **Step 2: Re-source message_service**

In `backend/modules/events/message_service.py`, replace `_net_callsign(db, net_id)` / `_build_context(db, net_id, …)` uses with the event: `send_event_message` / `send_event_form_message` / `compose_form_preview` already fetch/lock the event, so:
- `from_callsign` = `event_from_callsign(db, event)` (import from `event_config_service`).
- `_build_context(db, event, datetime_stamp)` reads `event_from_callsign(db, event)`.
- Every `dispatch_delivery(db, "event_message", message.id, subject, body, event.net_id, …)` becomes `dispatch_delivery(db, "event_message", message.id, subject, body, event_id=event.id, …)` (Task 8 adds the `event_id` keyword).

- [ ] **Step 3: PAT routes/session event-scoping**

In `backend/integrations/winlink/pat_routes.py`, the connect routes are currently net-scoped. For EP1, event-triggered connects use `resolve_pat_config_for_event`/`pat_transport_enabled_for_event`, and create a `PatConnectionSession` with `net_id=None, event_id=<event_id>`. The net-scoped connect entry (async-net use) keeps `net_id`. Update `pat_session.py` where a session row is created to accept `net_id=None`. (The PAT *route move* to `/api/events/{id}/pat/…` happens in Task 11; this task only makes the config + session model support event scope.) Update the tests accordingly.

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_event_form_compose.py tests/test_winlink_pat_routes.py tests/test_pat_config.py tests/test_pat_session.py -q` — PASS (update them to event-scoped fixtures). `nix-shell --run "ruff check"`.

- [ ] **Step 5: Commit**

```bash
git add backend/modules/events/message_service.py backend/integrations/winlink/ tests/
git commit -m "feat(events): message service + PAT read event config; PAT session net-optional"
```

---

### Task 8: Scope-aware delivery seam

**Files:**
- Modify: `backend/integrations/delivery/service.py`
- Modify: `backend/modules/roster/service.py`, `backend/modules/reminders/service.py` (call sites unchanged in behavior — verify)
- Create: `tests/test_event_delivery_scope.py`
- Modify: `tests/test_delivery_service.py`, `tests/test_delivery_winlink*.py`

**Interfaces:**
- Consumes: `get_event_config` (Task 2), `resolve_pat_config_for_event` (Task 7).
- Produces: `dispatch_delivery(db, content_type, content_id, subject, body, net_id=None, *, event_id=None, backends=None, config_overrides=None) -> bool` and `retry_failed(..., net_id=None, *, event_id=None, …)` — exactly one of `net_id`/`event_id`. `_build_config(db, backend_name, *, net_id=None, event_id=None)` sources from event config when `event_id` is set, else net config (unchanged).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_event_delivery_scope.py
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import Event, EventType, EventStatus
from backend.modules.events.event_config_service import set_event_config
from backend.integrations.delivery.service import dispatch_delivery


def _db_event():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    ev = Event(name="E", event_type=EventType.EMERGENCY, status=EventStatus.ACTIVE,
               created_by="W0NC", public_token="t", visibility="private")
    db.add(ev); db.flush()
    return db, ev


def test_event_delivery_uses_event_config(monkeypatch):
    db, ev = _db_event()
    set_event_config(db, ev.id, "pat_mailbox_path", "/tmp/evbox")
    captured = {}

    def fake_send(self, subject, body, config):
        captured.update(config)
        from backend.integrations.delivery.backends.base import DeliveryResult
        return DeliveryResult(success=True, error=None)

    from backend.integrations.delivery.backends.winlink import WinlinkBackend
    monkeypatch.setattr(WinlinkBackend, "send", fake_send)
    ok = dispatch_delivery(db, "event_message", 1, "s", "b", event_id=ev.id,
                           backends=["winlink"], config_overrides={"target_address": "KE0X"})
    assert ok is True
    assert captured["mailbox_path"] == "/tmp/evbox"    # sourced from EVENT config
    assert captured["target_address"] == "KE0X"
```

- [ ] **Step 2: Run test, verify fail**

Run: `.venv/bin/pytest tests/test_event_delivery_scope.py -q` — FAIL (`dispatch_delivery` has no `event_id`).

- [ ] **Step 3: Add the event scope to delivery**

In `backend/integrations/delivery/service.py`:
- `_build_config(db, backend_name, *, net_id=None, event_id=None)`: keep the existing net-sourced logic when `event_id is None`; when `event_id` is set, read the same keys via `get_event_config(db, event_id, key)` and use `resolve_pat_config_for_event(db, event_id)` for the winlink PAT config. Keep the global keys (SMTP, `delivery.groupsio.api_key`) global in both scopes.
- `dispatch_delivery` and `retry_failed`: change `net_id` from a required positional to `net_id: int | None = None` and add `event_id: int | None = None` (keyword). Assert exactly one is set. When reading the `backends` default, use `get_event_config(db, event_id, "delivery.backends")` for event scope, else net config. Pass `net_id`/`event_id` through to `_build_config`.
- Net callers (roster/reminders/delivery-routes retry for net content) keep passing `net_id` positionally — unchanged, byte-identical behavior. Verify their tests stay green.

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_event_delivery_scope.py tests/test_delivery_service.py tests/test_delivery_winlink*.py tests/test_delivery_routes.py -q` — PASS. Net-workflow delivery (`test_delivery_service`) must be unchanged-green. `nix-shell --run "ruff check"`.

- [ ] **Step 5: Commit**

```bash
git add backend/integrations/delivery/service.py tests/test_event_delivery_scope.py tests/test_delivery*
git commit -m "feat(delivery): scope-aware config (event_id sources event config; net path unchanged)"
```

---

### Task 9: Top-level `/api/events` router — CRUD + lifecycle

**Files:**
- Rewrite: `backend/modules/events/routes.py` (this task establishes the new router + helpers + CRUD/lifecycle; Tasks 10–12 add the remaining routes into the same file)
- Create: `tests/test_event_routes_standalone.py`

**Interfaces:**
- Consumes: `require_event_role`/`EventRole`/`EventContext` (Task 3); event `service` fns + ownership helpers (Task 4); `get_event_config`/`event_from_callsign` (Task 2); `aprs_manager` (Task 5); `get_event_alerts` (Task 6).
- Produces: `events_router = APIRouter(prefix="/api/events", tags=["events"])`; helpers `_event_to_response(db, event, ctx)` (includes `owner`, `visibility`, `public_token` **only for control users**, `operators`, `is_control`; NO `net_id`), `_snapshot(db, event, ctx)`, `_require_approved_user`. Routes: `POST /` (create), `GET /` (mine), `GET /public` (active public directory), `GET /{event_id}` (detail, READ), `PATCH/POST` lifecycle (CONTROL), `DELETE /{event_id}` (owner-only).

- [ ] **Step 1: Establish the new router + helpers**

Rewrite the top of `backend/modules/events/routes.py`:
```python
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, get_db_session
from backend.auth.models import User
from backend.modules.events.event_auth import EventRole, EventContext, require_event_role
from backend.modules.events import service
from backend.modules.events.models import Event, EventStatus, EventType
from backend.integrations.aprs import manager as aprs_manager

events_router = APIRouter(prefix="/api/events", tags=["events"])


def _require_approved_user(user: User = Depends(get_current_user)) -> User:
    if user.is_pending or user.is_deleted:
        raise HTTPException(status_code=403, detail="Account not approved")
    return user


def _event_to_response(db: Session, event: Event, ctx: EventContext | None) -> dict:
    is_control = bool(ctx and ctx.is_control)
    out = {
        "id": event.id, "name": event.name, "description": event.description,
        "event_type": event.event_type.value, "status": event.status.value,
        "owner": event.created_by, "created_by": event.created_by,
        "visibility": event.visibility,
        "created_at": _iso(event.created_at),
        "scheduled_start": _iso(event.scheduled_start),
        "activated_at": _iso(event.activated_at), "closed_at": _iso(event.closed_at),
        "aprs_other_stations": event.aprs_other_stations,
        "aprs_range_lat": event.aprs_range_lat, "aprs_range_lon": event.aprs_range_lon,
        "aprs_range_km": event.aprs_range_km, "aprs_beacon_posts": event.aprs_beacon_posts,
        "weather_enabled": (get_event_config(db, event.id, "weather.enabled") == "true"),
        "is_control": is_control,
    }
    if is_control:
        out["operators"] = service.list_operators(db, event)
        out["public_token"] = event.public_token   # only control users see the share token
    return out
```
`_snapshot(db, event, ctx)` mirrors the old snapshot but takes `ctx` (for `is_control`) and drops the `get_net_config(event.net_id, …)` call (use `get_event_config(db, event.id, "weather.enabled")`). The old `_get_event_or_404(db, net_id, event_id)` is **deleted** — the `require_event_role` dependency now resolves and authorizes the event, and routes receive `ctx.event`.

- [ ] **Step 2: Write failing tests**

```python
# tests/test_event_routes_standalone.py
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.config import Settings
from backend.db.base import Base
from backend.auth.models import User
from backend.modules.events.routes import events_router
from tests.conftest import make_test_token


@pytest.fixture
def app_s():
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret")
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([User(callsign="W0NC", oidc_subject="x|a", name="A"),
                    User(callsign="W0OUT", oidc_subject="x|o", name="O")])
        db.commit()
    app = FastAPI(); app.state.session_factory = factory; app.state.settings = settings
    app.include_router(events_router)
    return app, settings


def _c(app, settings, callsign=None):
    ck = {"access_token": make_test_token(callsign, settings)} if callsign else {}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=ck)


@pytest.mark.asyncio
async def test_any_operator_creates_and_owns(app_s):
    app, settings = app_s
    async with _c(app, settings, "W0NC") as c:
        r = await c.post("/api/events", json={"name": "Skywarn", "event_type": "emergency"})
    assert r.status_code == 201
    body = r.json()
    assert body["owner"] == "W0NC" and body["visibility"] == "private" and body["is_control"] is True


@pytest.mark.asyncio
async def test_mine_lists_only_my_events(app_s):
    app, settings = app_s
    async with _c(app, settings, "W0NC") as c:
        await c.post("/api/events", json={"name": "E1", "event_type": "emergency"})
        mine = (await c.get("/api/events")).json()
    assert len(mine) == 1
    async with _c(app, settings, "W0OUT") as c:
        assert (await c.get("/api/events")).json() == []   # not owner/op


@pytest.mark.asyncio
async def test_owner_only_delete(app_s):
    app, settings = app_s
    async with _c(app, settings, "W0NC") as c:
        eid = (await c.post("/api/events", json={"name": "E", "event_type": "emergency"})).json()["id"]
    async with _c(app, settings, "W0OUT") as c:
        assert (await c.delete(f"/api/events/{eid}")).status_code in (403, 404)
    async with _c(app, settings, "W0NC") as c:
        assert (await c.delete(f"/api/events/{eid}")).status_code == 204
```

- [ ] **Step 3: Implement the CRUD + lifecycle routes**

```python
class EventCreate(BaseModel):
    name: str
    event_type: EventType
    description: str | None = None
    scheduled_start: str | None = None


@events_router.post("", status_code=201)
async def create_event_route(body: EventCreate, user: User = Depends(_require_approved_user),
                             db: Session = Depends(get_db_session)):
    event = service.create_event(db, name=body.name, event_type=body.event_type,
                                 created_by=user.callsign, description=body.description,
                                 scheduled_start=body.scheduled_start)
    return _event_to_response(db, event, EventContext(user=user, event=event, is_control=True))


@events_router.get("")
async def list_mine_route(user: User = Depends(_require_approved_user),
                          db: Session = Depends(get_db_session)):
    op_ids = [o.event_id for o in db.query(EventOperator)
              .filter(EventOperator.callsign == user.callsign).all()]
    q = db.query(Event).filter((Event.created_by == user.callsign) | (Event.id.in_(op_ids)))
    events = q.order_by(Event.created_at.desc()).all()
    return [_event_to_response(db, e, EventContext(user=user, event=e, is_control=True)) for e in events]


@events_router.get("/public")
async def list_public_route(db: Session = Depends(get_db_session)):
    events = (db.query(Event).filter(Event.visibility == "public", Event.status == EventStatus.ACTIVE)
                .order_by(Event.activated_at.desc()).all())
    return [_event_to_response(db, e, None) for e in events]


@events_router.get("/{event_id}")
async def get_event_route(ctx: EventContext = Depends(require_event_role(EventRole.READ)),
                          db: Session = Depends(get_db_session)):
    return _snapshot(db, ctx.event, ctx)


@events_router.delete("/{event_id}", status_code=204)
async def delete_event_route(ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
                             db: Session = Depends(get_db_session)):
    if ctx.event.created_by != ctx.user.callsign and not ctx.user.is_admin:
        raise HTTPException(status_code=403, detail="Only the owner can delete")
    aprs_manager.stop(ctx.event.id)
    db.delete(ctx.event); db.commit()
```
Lifecycle routes (`activate`/`close`/`reopen`/`update`) transform 1:1 from the old net-scoped versions: drop `{net_slug}`, gate with `require_event_role(EventRole.CONTROL)`, use `ctx.event` instead of `_get_event_or_404(...)`, keep the `service.*` + `aprs_manager.*` calls, and return `_event_to_response(db, ctx.event, ctx)`. Import `EventOperator` where used.

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_event_routes_standalone.py -q` — PASS. `nix-shell --run "ruff check"`.

- [ ] **Step 5: Commit**

```bash
git add backend/modules/events/routes.py tests/test_event_routes_standalone.py
git commit -m "feat(events): top-level /api/events router — create/mine/public/detail/lifecycle"
```

---

### Task 10: Sub-resource routes — posts, participants, log, updates, report, positions, weather

**Files:**
- Modify: `backend/modules/events/routes.py`
- Modify: `tests/test_event_routes_standalone.py` (extend)

**Interfaces:**
- Consumes: Task 9 router + helpers; `aprs_manager`, `get_event_alerts`.

Transform each of the following existing routes into the new router. **Uniform transformation**: (a) path loses `/api/nets/{net_slug}` → becomes `/{event_id}/…` under `events_router`; (b) the gate becomes `require_event_role(EventRole.VIEWER→READ)` for GETs and `require_event_role(EventRole.CONTROL)` for mutations; (c) `_get_event_or_404(db, ctx.net.id, event_id)` is removed — use `ctx.event`; (d) every `get_net_config(event.net_id, key)` becomes `get_event_config(db, ctx.event.id, key)`; (e) `aprs_manager.*(event_id)` and `get_event_alerts(db, event_id)` use `ctx.event.id`.

| New route | Method | Path | Gate | Service call |
|---|---|---|---|---|
| create_post | POST | `/{event_id}/posts` | CONTROL | `service.create_post(...)` + `aprs_manager.nudge(ctx.event.id)` |
| update_post | PATCH | `/{event_id}/posts/{post_id}` | CONTROL | `service.update_post(...)` + nudge |
| delete_post | DELETE | `/{event_id}/posts/{post_id}` | CONTROL | `service.delete_post(...)` + nudge |
| check_in | POST | `/{event_id}/participants` | CONTROL | `service.check_in(...)` + nudge |
| update_participant | PATCH | `/{event_id}/participants/{participant_id}` | CONTROL | `service.update_participant(...)` + nudge |
| add_note | POST | `/{event_id}/log` | CONTROL | `service.add_note(...)` |
| pin_log | PATCH | `/{event_id}/log/{entry_id}` | CONTROL | `service.set_log_pinned(...)` |
| updates | GET | `/{event_id}/updates` | READ | `_snapshot(db, ctx.event, ctx)` w/ `since` |
| report | GET | `/{event_id}/report` | READ | `service.compute_report(db, ctx.event)` |
| positions | GET | `/{event_id}/positions` | READ | `aprs_manager.get_state(ctx.event.id)` |
| weather | GET | `/{event_id}/weather` | READ | `get_event_alerts(db, ctx.event.id)` |

- [ ] **Step 1: Write failing tests** — extend `test_event_routes_standalone.py` with: a control user can add a post/participant/note; a non-control user gets 403 on those; a public event's `/positions` and `/weather` are readable anonymously with the token; a private event's are 404 anonymously.

- [ ] **Step 2..N**: Run (fail) → implement the table above → run (pass) → ruff → commit.

```bash
git add backend/modules/events/routes.py tests/test_event_routes_standalone.py
git commit -m "feat(events): sub-resource routes (posts/participants/log/updates/report/positions/weather)"
```

---

### Task 11: Message + form + rescan + attachment + PAT-connect routes (event-scoped)

**Files:**
- Modify: `backend/modules/events/routes.py`
- Modify: `backend/integrations/winlink/pat_routes.py` (mount event connect under `/api/events/{id}/pat/…`)
- Modify: `tests/test_event_message_routes.py`, `tests/test_event_form_compose.py`, `tests/test_winlink_pat_routes.py`

**Interfaces:**
- Consumes: message_service (Task 7, event-scoped dispatch), `resolve_pat_config_for_event`/`pat_transport_enabled_for_event` (Task 7), delivery `retry_failed(..., event_id=…)` (Task 8).

Transform, using the same rules as Task 10 (path → `/{event_id}/…`, `require_event_role`, `ctx.event`, event config):

| New route | Method | Path | Gate |
|---|---|---|---|
| list_messages | GET | `/{event_id}/messages` | READ (reads `pat_mailbox_path`/`net_address`/`pat_transport_enabled` via **event** config) |
| compose_message | POST | `/{event_id}/messages` | CONTROL (`send_event_message`) |
| patch_message | PATCH | `/{event_id}/messages/{message_id}` | CONTROL |
| retry_message | POST | `/{event_id}/messages/{message_id}/retry` | CONTROL (`retry_failed(db, "event_message", message_id, event_id=ctx.event.id, …)` — the event-message retry moves here from the net-scoped delivery route) |
| download_attachment | GET | `/{event_id}/messages/{message_id}/attachments/{attachment_id}` | READ |
| rescan | POST | `/{event_id}/rescan` | CONTROL (uses `pat_transport_enabled_for_event(db, ctx.event.id)` + event `pat_mailbox_path`/`net_address`) |
| form_preview | POST | `/{event_id}/forms/preview` | CONTROL |
| form_send | POST | `/{event_id}/form-messages` | CONTROL |
| reply_form | GET | `/{event_id}/messages/{message_id}/reply-form` | CONTROL |
| pat_connect | POST | `/{event_id}/pat/connect` | CONTROL (event-scoped session: `net_id=None, event_id=ctx.event.id`) |
| pat_session_status | GET | `/{event_id}/pat/sessions/{session_id}` | READ |
| pat_abort | POST | `/{event_id}/pat/sessions/{session_id}/abort` | CONTROL |
| pat_connect_options | GET | `/{event_id}/pat/connect-options` | CONTROL |
| pat_test | POST | `/{event_id}/pat/test` | CONTROL |

In the net-scoped delivery retry route (`backend/integrations/delivery/routes.py`), REMOVE the `event_message` special-casing (its rebuild-form + attachment logic) — that now lives in the event `retry_message` route; the net delivery retry route keeps only net content (roster/reminder).

- [ ] **Step 1: Write failing tests** (event-scoped fixtures: an event with `pat_transport_enabled` in event config; a control user sends/retries; `list_messages` reflects delivery status; a viewer/anonymous can't compose).
- [ ] **Step 2..N**: fail → implement → pass → ruff → commit.

```bash
git add backend/modules/events/routes.py backend/integrations/winlink/pat_routes.py backend/integrations/delivery/routes.py tests/
git commit -m "feat(events): event-scoped message/form/rescan/attachment/PAT routes"
```

---

### Task 12: Owner-only routes — operators, visibility, token rotation, transfer

**Files:**
- Modify: `backend/modules/events/routes.py`
- Modify: `tests/test_event_routes_standalone.py`

**Interfaces:**
- Consumes: `service.add_operator/remove_operator/list_operators/set_visibility/rotate_public_token/transfer_owner` (Task 4).
- Produces: owner-only routes; a shared `_require_owner(ctx)` guard.

- [ ] **Step 1: Write failing tests** — owner can add/remove an operator, toggle visibility, rotate the token (old token then 404s on anonymous read), and transfer ownership; a co-operator (control but not owner) gets 403 on all of these; after transfer the new owner has control and the old owner does not.

- [ ] **Step 2: Implement**

```python
def _require_owner(ctx: EventContext) -> None:
    if not (ctx.user and (ctx.user.is_admin or ctx.event.created_by == ctx.user.callsign)):
        raise HTTPException(status_code=403, detail="Only the owner can do this")


class OperatorBody(BaseModel):
    callsign: str

class VisibilityBody(BaseModel):
    visibility: str

class TransferBody(BaseModel):
    callsign: str


@events_router.post("/{event_id}/operators", status_code=201)
async def add_operator_route(body: OperatorBody, ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
                             db: Session = Depends(get_db_session)):
    _require_owner(ctx)
    service.add_operator(db, ctx.event, body.callsign, added_by=ctx.user.callsign)
    return {"operators": service.list_operators(db, ctx.event)}


@events_router.delete("/{event_id}/operators/{callsign}", status_code=204)
async def remove_operator_route(callsign: str, ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
                                db: Session = Depends(get_db_session)):
    _require_owner(ctx)
    service.remove_operator(db, ctx.event, callsign)


@events_router.patch("/{event_id}/visibility")
async def set_visibility_route(body: VisibilityBody, ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
                               db: Session = Depends(get_db_session)):
    _require_owner(ctx)
    try:
        service.set_visibility(db, ctx.event, body.visibility)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"visibility": ctx.event.visibility}


@events_router.post("/{event_id}/token/rotate")
async def rotate_token_route(ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
                             db: Session = Depends(get_db_session)):
    _require_owner(ctx)
    return {"public_token": service.rotate_public_token(db, ctx.event)}


@events_router.post("/{event_id}/transfer")
async def transfer_route(body: TransferBody, ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
                         db: Session = Depends(get_db_session)):
    _require_owner(ctx)
    service.transfer_owner(db, ctx.event, body.callsign)
    return {"owner": ctx.event.created_by}
```

- [ ] **Step 3: Run tests, verify pass** — `.venv/bin/pytest tests/test_event_routes_standalone.py -q`. `nix-shell --run "ruff check"`.

- [ ] **Step 4: Commit**

```bash
git add backend/modules/events/routes.py tests/test_event_routes_standalone.py
git commit -m "feat(events): owner-only routes (operators/visibility/token/transfer)"
```

---

### Task 13: Wire the new router; remove net-scoped events; fix remaining tests

**Files:**
- Modify: `backend/app.py`
- Modify/delete: old net-scoped event tests (`tests/test_event_lifecycle_routes.py`, `tests/test_event_message_routes.py`, etc. — rewrite to `/api/events` + event auth, or delete the redundant ones)
- Modify: `backend/modules/nets/routes.py` if it referenced the events router / net events tab

**Interfaces:**
- Produces: `events_router` mounted at the app; the old `/api/nets/{slug}/events` router gone.

- [ ] **Step 1: Mount + unmount**

In `backend/app.py`, ensure `from backend.modules.events.routes import events_router` is included (`app.include_router(events_router)`) and that the OLD net-scoped events router registration is removed (the router object is the same name but now has the `/api/events` prefix, so a single include is correct). Confirm no other module still imports the old `require_net_role`-based event routes.

- [ ] **Step 2: Rewrite/prune the legacy event-route tests**

The pre-EP1 tests that hit `/api/nets/{slug}/events/…` with net membership are obsolete. For each, either rewrite it against `/api/events` + `require_event_role` (owner/operator/admin/anonymous), or delete it if `test_event_routes_standalone.py` already covers the behavior. Do NOT leave dead net-scoped event tests.

- [ ] **Step 3: Full suite green**

Run: `.venv/bin/pytest -q` — the WHOLE suite must pass now (this is the task that reconciles everything). Fix any straggler that still references `event.net_id` or a net-scoped event path. `nix-shell --run "ruff check"`.

- [ ] **Step 4: Commit**

```bash
git add backend/app.py backend/modules/nets/routes.py tests/
git commit -m "feat(events): mount /api/events, remove net-scoped event routes + legacy tests"
```

---

### Task 14: Final verification sweep

- [ ] **Step 1: Full backend suite + lint**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — all pass.

- [ ] **Step 2: Migration chain on a scratch DB**

Run: `SKYNET_DATABASE_URL="sqlite:////tmp/ep1-final.db" .venv/bin/alembic upgrade head && SKYNET_DATABASE_URL="sqlite:////tmp/ep1-final.db" .venv/bin/alembic downgrade -1 && rm -f /tmp/ep1-final.db` — clean up+down through `a7b1c9d3e5f0`.

- [ ] **Step 3: Grep guard — no lingering event↔net coupling**

Run: `grep -rn "event.net_id\|nets/{net_slug}/events\|_get_event_or_404" backend/ | grep -v test` — expected: NO hits (all event code is net-free). Any hit is a miss to fix.

- [ ] **Step 4: Net-workflow regression**

Confirm net delivery (roster/reminders) tests are green and unchanged — the scope-aware delivery seam must not have regressed net content: `.venv/bin/pytest tests/test_delivery_service.py tests/test_roster* tests/test_reminders* -q`.

- [ ] **Step 5: Human checkpoint (note for the controller)**

EP1 is backend-only; there is no usable UI yet (EP2 builds it). A curl smoke against `/api/events` (create → mine → detail → operator → public directory) with a minted token confirms the API. Full operator smoke happens after EP2.

---

## Notes for the implementer

- **Reset is destructive and intentional** — the migration deletes existing events. This is a test system; the spec chose reset over migration.
- **Net workflows are untouched** — nets keep schedule/roster/reminders/async check-ins and their net-config. Only *events* decouple. The delivery seam adds an event path; the net path stays byte-identical.
- **`require_event_role` resolves + authorizes** — routes no longer take `net_slug` or call `_get_event_or_404`; they receive `ctx.event`. The `public_token` is only ever returned to control users; anonymous public read is via `?token=`.
- **Order matters** — Tasks 1–8 (schema→config→auth→service→integrations→delivery) must land before the routes (9–12); Task 13 is where the whole suite goes green (earlier tasks intentionally leave legacy net-scoped tests red).
- **`get_optional_user`** — if it doesn't already exist, add it in `dependencies.py` (mirror `get_current_user` but return `None` instead of raising). It's the anonymous-public-read enabler.
