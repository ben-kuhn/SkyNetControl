# Settings Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move net-scoped settings out of the global ConfigPage into per-net NetSettingsPage, switch from per-field to per-section Save buttons, and add a `winlink_enabled` capability flag that gates Winlink-specific fields per-net.

**Architecture:** Two new transactional bulk-set endpoints (`PUT /api/config/bulk`, `PUT /api/nets/{slug}/config/bulk`) back a new `SettingsSection` React component that does section-level dirty tracking. An Alembic migration seeds existing per-net configs from the old global rows, sets `winlink_enabled=true` on every existing net, and deletes the moved keys from `app_config`.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, React, TypeScript, pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-29-settings-reorganization-design.md` — read it before starting.
- All toolchain commands run inside `nix-shell` (see CLAUDE.md). Backend tests: `.venv/bin/pytest -q`. Lint: `nix-shell --run "ruff check"`. Frontend: `cd frontend && nix-shell -p nodejs_22 --run "npm <cmd>"`.
- Ruff: line-length 120, `select = ["E", "F"]`.
- Commits: Conventional Commits. Scope when it clarifies (`feat(config):`, `refactor(ui):`, `fix(migration):`).
- Single-key endpoints stay (OAuth providers and internal callers depend on them). Only the section-level UI calls the new bulk endpoints.
- Capability flags live in `net_config` as boolean strings (`"true"` / `"false"`) — same convention as `scanner.enabled`. No schema change.
- The set of keys this work treats as "now per-net" (referenced by spec and migration):
  - `default_net_control`
  - `net_address`
  - `pat_mailbox_path`
  - `scanner.enabled`
  - `scanner.interval_minutes`
  - `delivery.backends`
  - `delivery.email.to_address`
  - `delivery.groupsio.group_name`
  - `delivery.winlink.target_address`
- Winlink-gated per-net keys (hidden when `winlink_enabled=false`):
  - `net_address`, `pat_mailbox_path`, `scanner.enabled`, `scanner.interval_minutes`, `delivery.winlink.target_address`
- Sensitive-key encryption discipline (see `backend/config_mgmt/service.py:14`) — the bulk endpoint must encrypt the same keys the single-key endpoint encrypts.

---

## File Structure

**Backend (modify):**
- `backend/config_mgmt/service.py` — add `set_config_values_bulk(db, values)` (single transaction).
- `backend/config_mgmt/routes.py` — add `PUT /bulk` endpoint.
- `backend/modules/nets/config_service.py` — add `set_net_config_bulk(db, net_id, values)`.
- `backend/modules/nets/routes.py` — add `PUT /{net_slug}/config/bulk` endpoint.

**Backend (create):**
- `alembic/versions/<rev>_seed_net_config_from_app_config.py` — data migration.

**Backend tests (create):**
- `tests/test_config_bulk_route.py` — bulk global PUT.
- `tests/test_net_config_bulk_route.py` — bulk per-net PUT.
- `tests/test_migration_seed_net_config.py` — migration data correctness.

**Frontend (modify):**
- `frontend/src/api/config.ts` — add `setConfigBulk(values)`.
- `frontend/src/api/nets.ts` — add `setNetConfigBulk(slug, values)`.
- `frontend/src/pages/ConfigPage.tsx` — remove per-net fields, add Auth section, switch to section-level Save.
- `frontend/src/pages/NetSettingsPage.tsx` — add `winlink_enabled` toggle, add Delivery section, gate Winlink fields, switch to section-level Save.

**Frontend (create):**
- `frontend/src/components/SettingsSection.tsx` — reusable section card with dirty tracking and Save.

---

## Task 1: Backend `set_config_values_bulk` service helper

**Files:**
- Modify: `backend/config_mgmt/service.py`
- Test: `tests/test_config_mgmt.py`

**Interfaces:**
- Consumes: existing `AppConfig` model.
- Produces: `set_config_values_bulk(db: Session, values: dict[str, str]) -> None` — upserts every key, commits once. Caller is responsible for any per-value pre-processing (e.g., encryption).

- [ ] **Step 1: Add a failing test**

Open `tests/test_config_mgmt.py` and append:

```python
def test_set_config_values_bulk_upserts_all_keys():
    from backend.config_mgmt.service import set_config_values_bulk, get_all_config

    db = _make_db()  # use whichever helper this file already has
    # If the file uses a different fixture, mirror its pattern. Adapt _make_db() to whatever exists.
    set_config_values_bulk(db, {"k1": "v1", "k2": "v2"})
    assert get_all_config(db) == {"k1": "v1", "k2": "v2"}


def test_set_config_values_bulk_updates_existing_keys():
    from backend.config_mgmt.service import set_config_value, set_config_values_bulk, get_all_config

    db = _make_db()
    set_config_value(db, "k1", "old")
    set_config_values_bulk(db, {"k1": "new", "k2": "fresh"})
    assert get_all_config(db) == {"k1": "new", "k2": "fresh"}


def test_set_config_values_bulk_empty_dict_is_noop():
    from backend.config_mgmt.service import set_config_values_bulk, get_all_config

    db = _make_db()
    set_config_values_bulk(db, {})
    assert get_all_config(db) == {}
```

If `tests/test_config_mgmt.py` lacks a `_make_db()` helper, copy the one from `tests/test_net_config_service.py:12-20`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config_mgmt.py -v -k set_config_values_bulk`
Expected: ImportError or AttributeError on `set_config_values_bulk`.

- [ ] **Step 3: Implement the helper**

Add to `backend/config_mgmt/service.py`:

```python
def set_config_values_bulk(db: Session, values: dict[str, str]) -> None:
    """Upsert many config keys in a single transaction.

    Caller is responsible for any per-value pre-processing (e.g. encrypting
    sensitive values via secret_box). See backend.config_mgmt.routes for
    the route-level encryption policy.
    """
    for key, value in values.items():
        config = db.get(AppConfig, key)
        if config is None:
            db.add(AppConfig(key=key, value=value))
        else:
            config.value = value
    db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config_mgmt.py -v -k set_config_values_bulk`
Expected: 3 passed.

- [ ] **Step 5: Lint and commit**

```bash
nix-shell --run "ruff check backend/config_mgmt/service.py tests/test_config_mgmt.py"
git add backend/config_mgmt/service.py tests/test_config_mgmt.py
git commit -m "feat(config): bulk set helper for app_config upserts"
```

---

## Task 2: Backend bulk PUT route for global config

**Files:**
- Create: `tests/test_config_bulk_route.py`
- Modify: `backend/config_mgmt/routes.py`

**Interfaces:**
- Consumes: `set_config_values_bulk` from Task 1; `is_sensitive_key`, `encrypt`, `log_action` from existing modules.
- Produces: `PUT /api/config/bulk` accepting body `{"values": {"k1": "v1", ...}}`; returns `{"ok": true, "count": N}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_bulk_route.py`:

```python
"""Tests for PUT /api/config/bulk."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.auth.secret_box import decrypt
from backend.config import Settings
from backend.config_mgmt.models import AppConfig
from backend.config_mgmt.routes import config_router
from backend.db.base import Base
from tests.conftest import make_test_token


@pytest.fixture
def db_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(User(callsign="W0ADM", oidc_subject="oidc|adm", name="Admin", is_admin=True))
        session.commit()
    return factory


@pytest.fixture
def app(db_factory):
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)
    a = FastAPI()
    a.state.session_factory = db_factory
    a.state.settings = settings
    a.include_router(config_router, prefix="/api/config")
    return a


def _auth_headers():
    token = make_test_token("W0ADM", is_admin=True)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_bulk_put_upserts_all_keys(app, db_factory):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put(
            "/api/config/bulk",
            headers=_auth_headers(),
            json={"values": {"net_callsign_demo": "W0NE", "claude_model_demo": "opus"}},
        )
    assert r.status_code == 200, r.text
    with db_factory() as s:
        rows = {c.key: c.value for c in s.query(AppConfig).all()}
    assert rows["net_callsign_demo"] == "W0NE"
    assert rows["claude_model_demo"] == "opus"


@pytest.mark.asyncio
async def test_bulk_put_encrypts_sensitive_keys(app, db_factory):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put(
            "/api/config/bulk",
            headers=_auth_headers(),
            json={"values": {"claude_api_key": "sk-secret", "public_field": "plain"}},
        )
    assert r.status_code == 200, r.text
    with db_factory() as s:
        rows = {c.key: c.value for c in s.query(AppConfig).all()}
    # public field stored plaintext
    assert rows["public_field"] == "plain"
    # sensitive field stored encrypted, decryptable back to original
    assert rows["claude_api_key"] != "sk-secret"
    assert decrypt(rows["claude_api_key"]) == "sk-secret"


@pytest.mark.asyncio
async def test_bulk_put_empty_values_returns_ok(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put("/api/config/bulk", headers=_auth_headers(), json={"values": {}})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "count": 0}


@pytest.mark.asyncio
async def test_bulk_put_missing_values_field_returns_422(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put("/api/config/bulk", headers=_auth_headers(), json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_put_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put("/api/config/bulk", json={"values": {"k": "v"}})
    assert r.status_code in (401, 403)
```

If `tests/conftest.py` lacks `make_test_token`, check existing tests like `tests/test_config_routes.py` for the helper they use to mint tokens, and adapt accordingly.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config_bulk_route.py -v`
Expected: all FAIL with 404 (route not registered).

- [ ] **Step 3: Implement the route**

Modify `backend/config_mgmt/routes.py`. Add the import:

```python
from backend.config_mgmt.service import (
    get_all_config,
    is_sensitive_key,
    set_config_value,
    set_config_values_bulk,
)
```

Add a new request model below `ConfigValueRequest`:

```python
class ConfigBulkRequest(BaseModel):
    values: dict[str, str]
```

Add the route at the end of the file:

```python
@config_router.put("/bulk")
async def update_config_bulk(
    body: ConfigBulkRequest,
    principal: Principal = Depends(require_admin_or_recovery),
    db: Session = Depends(get_db_session),
):
    # Encrypt sensitive values on the way down, mirroring the single-key
    # route's behavior (and what the typed OAuth/SMTP routes do via
    # secret_box). Empty strings pass through unencrypted.
    prepared: dict[str, str] = {}
    for key, value in body.values.items():
        if is_sensitive_key(key) and value:
            prepared[key] = encrypt(value)
        else:
            prepared[key] = value
    set_config_values_bulk(db, prepared)
    for key, value in body.values.items():
        audit_value = "[REDACTED]" if is_sensitive_key(key) else value
        log_action(
            db,
            actor=principal.callsign,
            action="config.updated",
            details={"key": key, "value": audit_value},
        )
    return {"ok": True, "count": len(body.values)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config_bulk_route.py -v`
Expected: 5 passed.

Also rerun the existing route tests to make sure nothing broke:

Run: `.venv/bin/pytest tests/test_config_routes.py -v`
Expected: all green.

- [ ] **Step 5: Lint and commit**

```bash
nix-shell --run "ruff check backend/config_mgmt/routes.py tests/test_config_bulk_route.py"
git add backend/config_mgmt/routes.py tests/test_config_bulk_route.py
git commit -m "feat(config): PUT /api/config/bulk for section-level saves"
```

---

## Task 3: Backend `set_net_config_bulk` + bulk PUT for per-net config

**Files:**
- Modify: `backend/modules/nets/config_service.py`
- Modify: `backend/modules/nets/routes.py`
- Create: `tests/test_net_config_bulk_route.py`
- Test (existing): `tests/test_net_config_service.py`

**Interfaces:**
- Consumes: `NetConfig` model.
- Produces:
  - `set_net_config_bulk(db: Session, net_id: int, values: dict[str, str]) -> None` — single-transaction upsert.
  - `PUT /api/nets/{net_slug}/config/bulk` accepting `{"values": {"k": "v", ...}}`; requires NET_CONTROL role; returns `{"ok": true, "count": N}`.

- [ ] **Step 1: Append failing tests to `tests/test_net_config_service.py`**

```python
def test_set_net_config_bulk_upserts_all_keys():
    from backend.modules.nets.config_service import set_net_config_bulk

    db = _make_db()
    net = _make_net(db)
    set_net_config_bulk(db, net.id, {"k1": "v1", "k2": "v2"})
    assert get_net_config(db, net.id, "k1") == "v1"
    assert get_net_config(db, net.id, "k2") == "v2"


def test_set_net_config_bulk_updates_existing_keys():
    from backend.modules.nets.config_service import set_net_config_bulk

    db = _make_db()
    net = _make_net(db)
    set_net_config(db, net.id, "k1", "old")
    set_net_config_bulk(db, net.id, {"k1": "new", "k2": "fresh"})
    assert get_net_config(db, net.id, "k1") == "new"
    assert get_net_config(db, net.id, "k2") == "fresh"


def test_set_net_config_bulk_isolated_per_net():
    from backend.modules.nets.config_service import set_net_config_bulk

    db = _make_db()
    net_a = _make_net(db, "net-a")
    net_b = _make_net(db, "net-b")
    set_net_config_bulk(db, net_a.id, {"k": "a"})
    set_net_config_bulk(db, net_b.id, {"k": "b"})
    assert get_net_config(db, net_a.id, "k") == "a"
    assert get_net_config(db, net_b.id, "k") == "b"


def test_set_net_config_bulk_empty_dict_is_noop():
    from backend.modules.nets.config_service import set_net_config_bulk

    db = _make_db()
    net = _make_net(db)
    set_net_config_bulk(db, net.id, {})
    assert get_net_config(db, net.id, "anything") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_net_config_service.py -v -k bulk`
Expected: ImportError on `set_net_config_bulk`.

- [ ] **Step 3: Implement the service helper**

Edit `backend/modules/nets/config_service.py`. Add at the end:

```python
def set_net_config_bulk(db: Session, net_id: int, values: dict[str, str]) -> None:
    """Upsert many per-net config keys in a single transaction."""
    now = datetime.now(timezone.utc)
    for key, value in values.items():
        row = db.get(NetConfig, (net_id, key))
        if row is None:
            db.add(NetConfig(net_id=net_id, key=key, value=value))
        else:
            row.value = value
            row.updated_at = now
    db.commit()
```

- [ ] **Step 4: Run service tests to verify they pass**

Run: `.venv/bin/pytest tests/test_net_config_service.py -v -k bulk`
Expected: 4 passed.

- [ ] **Step 5: Write the failing route tests**

Create `tests/test_net_config_bulk_route.py`:

```python
"""Tests for PUT /api/nets/{slug}/config/bulk."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.nets.models import Net, NetConfig, NetMembership, NetRole
from backend.modules.nets.routes import router as nets_router
from tests.conftest import make_test_token


@pytest.fixture
def db_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        nc = User(callsign="W0NC", oidc_subject="oidc|nc", name="Net Control")
        outsider = User(callsign="W0OUT", oidc_subject="oidc|out", name="Outsider")
        net = Net(slug="weekly", name="Weekly Net")
        session.add_all([nc, outsider, net])
        session.flush()
        session.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
        session.commit()
    return factory


@pytest.fixture
def app(db_factory):
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)
    a = FastAPI()
    a.state.session_factory = db_factory
    a.state.settings = settings
    a.include_router(nets_router)
    return a


def _net_control_headers():
    return {"Authorization": f"Bearer {make_test_token('W0NC')}"}


def _outsider_headers():
    return {"Authorization": f"Bearer {make_test_token('W0OUT')}"}


@pytest.mark.asyncio
async def test_bulk_put_upserts_all_keys(app, db_factory):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put(
            "/api/nets/weekly/config/bulk",
            headers=_net_control_headers(),
            json={"values": {"winlink_enabled": "true", "default_net_control": "W0NE"}},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "count": 2}
    with db_factory() as s:
        net = s.query(Net).filter(Net.slug == "weekly").one()
        rows = {c.key: c.value for c in s.query(NetConfig).filter(NetConfig.net_id == net.id).all()}
    assert rows == {"winlink_enabled": "true", "default_net_control": "W0NE"}


@pytest.mark.asyncio
async def test_bulk_put_requires_net_control(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put(
            "/api/nets/weekly/config/bulk",
            headers=_outsider_headers(),
            json={"values": {"k": "v"}},
        )
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_bulk_put_unknown_net_returns_404(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put(
            "/api/nets/does-not-exist/config/bulk",
            headers=_net_control_headers(),
            json={"values": {"k": "v"}},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_bulk_put_empty_values_is_noop(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.put(
            "/api/nets/weekly/config/bulk",
            headers=_net_control_headers(),
            json={"values": {}},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "count": 0}
```

- [ ] **Step 6: Run route tests to verify they fail**

Run: `.venv/bin/pytest tests/test_net_config_bulk_route.py -v`
Expected: all FAIL with 404 / 405 (route not registered).

- [ ] **Step 7: Implement the route**

Edit `backend/modules/nets/routes.py`. Update the import:

```python
from backend.modules.nets.config_service import set_net_config, set_net_config_bulk
```

Add a Pydantic model near the existing `MemberIn`:

```python
class NetConfigBulkIn(BaseModel):
    values: dict[str, str]
```

Add the route at the end of the file (after `put_config`):

```python
@router.put("/{net_slug}/config/bulk")
def put_config_bulk(
    body: NetConfigBulkIn,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    set_net_config_bulk(db, ctx.net.id, body.values)
    return {"ok": True, "count": len(body.values)}
```

- [ ] **Step 8: Run all tests in this area to verify green**

Run: `.venv/bin/pytest tests/test_net_config_service.py tests/test_net_config_bulk_route.py -v`
Expected: all green.

- [ ] **Step 9: Lint and commit**

```bash
nix-shell --run "ruff check backend/modules/nets/config_service.py backend/modules/nets/routes.py tests/test_net_config_service.py tests/test_net_config_bulk_route.py"
git add backend/modules/nets/config_service.py backend/modules/nets/routes.py tests/test_net_config_service.py tests/test_net_config_bulk_route.py
git commit -m "feat(nets): PUT /api/nets/{slug}/config/bulk for section-level saves"
```

---

## Task 4: Alembic migration — seed net_config from app_config and set winlink_enabled

**Files:**
- Create: `alembic/versions/<rev>_seed_net_config_from_app_config.py`
- Create: `tests/test_migration_seed_net_config.py`

**Interfaces:**
- Consumes: existing `app_config`, `nets`, `net_config` tables.
- Produces: a one-shot data migration that:
  1. For each net and each key in the moved set, inserts a `net_config` row with the value from `app_config` only if no such row exists for that (net_id, key).
  2. Inserts `winlink_enabled=true` for every net that doesn't already have a row for that key.
  3. Deletes the moved keys from `app_config`.

- [ ] **Step 1: Generate the migration skeleton**

Run: `nix-shell --run "alembic revision -m 'seed net_config from app_config and set winlink_enabled'"`
Expected: a new file `alembic/versions/<rev>_seed_net_config_from_app_config.py` appears. Note the revision id from the filename.

- [ ] **Step 2: Confirm the parent revision**

Run: `nix-shell --run "alembic heads"`
Expected: a single head id matching `down_revision` in the new file (currently `7e2d4f81b3a9`, but verify — there may be later commits).

If the new migration's `down_revision` isn't the current head, fix it. Plan assumes `down_revision = "7e2d4f81b3a9"` at write time; verify before relying on it.

- [ ] **Step 3: Write the failing migration test**

Create `tests/test_migration_seed_net_config.py`:

```python
"""Tests for the migration that seeds net_config from app_config."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, String, Text, create_engine, MetaData, Table, ForeignKey, DateTime
from sqlalchemy.engine import Engine

MOVED_KEYS = [
    "default_net_control",
    "net_address",
    "pat_mailbox_path",
    "scanner.enabled",
    "scanner.interval_minutes",
    "delivery.backends",
    "delivery.email.to_address",
    "delivery.groupsio.group_name",
    "delivery.winlink.target_address",
]


def _find_migration_module():
    versions_dir = Path(__file__).parent.parent / "alembic" / "versions"
    candidates = list(versions_dir.glob("*_seed_net_config_from_app_config.py"))
    assert len(candidates) == 1, f"Expected one matching migration, got {candidates}"
    spec = importlib.util.spec_from_file_location("seed_net_config_migration", candidates[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_pre_migration_schema(engine: Engine):
    """Build just enough schema to test the migration's data movement."""
    metadata = MetaData()
    Table(
        "app_config",
        metadata,
        Column("key", String, primary_key=True),
        Column("value", Text, nullable=False),
        Column("updated_at", DateTime),
    )
    Table(
        "nets",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("slug", String, nullable=False),
        Column("name", String, nullable=False),
    )
    Table(
        "net_config",
        metadata,
        Column("net_id", Integer, ForeignKey("nets.id"), primary_key=True),
        Column("key", String, primary_key=True),
        Column("value", Text, nullable=False),
        Column("updated_at", DateTime),
    )
    metadata.create_all(engine)
    return metadata


@pytest.fixture
def engine():
    return create_engine("sqlite://")


def _run_upgrade(engine: Engine):
    """Invoke the migration's upgrade() against an alembic-style context."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mod = _find_migration_module()
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        op_proxy = Operations(ctx)
        # The migration uses module-level `op` import. Monkey-patch the
        # global so the function operates on our test connection.
        import alembic
        from alembic import op as alembic_op
        # Push our op proxy onto the proxy stack
        token = alembic.op._proxy.context.push(op_proxy)
        try:
            mod.upgrade()
        finally:
            alembic.op._proxy.context.pop(token)


def test_upgrade_seeds_net_config_for_missing_keys(engine):
    md = _build_pre_migration_schema(engine)
    with engine.begin() as c:
        c.execute(md.tables["nets"].insert(), [
            {"id": 1, "slug": "alpha", "name": "Alpha"},
            {"id": 2, "slug": "beta", "name": "Beta"},
        ])
        c.execute(md.tables["app_config"].insert(), [
            {"key": k, "value": f"global-{k}"} for k in MOVED_KEYS
        ])

    _run_upgrade(engine)

    with engine.begin() as c:
        rows = list(c.execute(md.tables["net_config"].select()))
        app_rows = list(c.execute(md.tables["app_config"].select()))

    by_net: dict[int, dict[str, str]] = {1: {}, 2: {}}
    for r in rows:
        by_net[r.net_id][r.key] = r.value
    # Each net got all moved keys seeded + winlink_enabled
    for net_id in (1, 2):
        for k in MOVED_KEYS:
            assert by_net[net_id][k] == f"global-{k}", f"net {net_id} missing {k}"
        assert by_net[net_id]["winlink_enabled"] == "true"
    # All moved keys removed from app_config
    app_keys = {r.key for r in app_rows}
    assert app_keys.isdisjoint(set(MOVED_KEYS))


def test_upgrade_preserves_existing_net_config_values(engine):
    md = _build_pre_migration_schema(engine)
    with engine.begin() as c:
        c.execute(md.tables["nets"].insert(), [{"id": 1, "slug": "alpha", "name": "Alpha"}])
        c.execute(md.tables["app_config"].insert(), [
            {"key": "default_net_control", "value": "global-call"},
        ])
        # Net already has a value — must not be overwritten
        c.execute(md.tables["net_config"].insert(), [
            {"net_id": 1, "key": "default_net_control", "value": "net-specific-call"},
        ])

    _run_upgrade(engine)

    with engine.begin() as c:
        rows = list(c.execute(md.tables["net_config"].select().where(
            md.tables["net_config"].c.net_id == 1
        )))
    by_key = {r.key: r.value for r in rows}
    assert by_key["default_net_control"] == "net-specific-call"


def test_upgrade_leaves_unrelated_app_config_keys(engine):
    md = _build_pre_migration_schema(engine)
    with engine.begin() as c:
        c.execute(md.tables["nets"].insert(), [{"id": 1, "slug": "alpha", "name": "Alpha"}])
        c.execute(md.tables["app_config"].insert(), [
            {"key": "claude_api_key", "value": "sk-keep"},
            {"key": "callbook.providers", "value": '["hamqth"]'},
            {"key": "default_net_control", "value": "W0NE"},
        ])

    _run_upgrade(engine)

    with engine.begin() as c:
        rows = {r.key: r.value for r in c.execute(md.tables["app_config"].select())}
    assert rows.get("claude_api_key") == "sk-keep"
    assert rows.get("callbook.providers") == '["hamqth"]'
    assert "default_net_control" not in rows


def test_upgrade_with_no_app_config_rows_still_sets_winlink_enabled(engine):
    md = _build_pre_migration_schema(engine)
    with engine.begin() as c:
        c.execute(md.tables["nets"].insert(), [{"id": 1, "slug": "alpha", "name": "Alpha"}])

    _run_upgrade(engine)

    with engine.begin() as c:
        rows = list(c.execute(md.tables["net_config"].select()))
    assert any(r.key == "winlink_enabled" and r.value == "true" and r.net_id == 1 for r in rows)
    # Nothing else seeded — no app_config to copy from
    assert all(r.key == "winlink_enabled" for r in rows)
```

Note: the `_run_upgrade` monkey-patch uses Alembic internals; if the `_proxy.context.push` shape has changed in your alembic version, replace with a wrapper that calls `mod.upgrade()` under `with Operations.context(...)`. The test's intent (run `upgrade()` against the test DB) is what matters; adjust mechanism to fit.

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_migration_seed_net_config.py -v`
Expected: ImportError / NotImplementedError (upgrade() body is empty).

- [ ] **Step 5: Implement the migration**

Open the generated migration file. Replace its body with:

```python
"""seed net_config from app_config and set winlink_enabled

Revision ID: <leave-existing>
Revises: <leave-existing>
Create Date: <leave-existing>

Moves nine previously-global keys from app_config into per-net net_config
(see docs/superpowers/specs/2026-06-29-settings-reorganization-design.md).
For each net missing a row for a moved key, copies the value from
app_config. Sets winlink_enabled=true for every net (preserves today's
Winlink-net behavior; non-Winlink-net operators opt out in the UI).
Finally deletes the moved keys from app_config.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers — preserved from the generated stub
revision: str = "<generated>"
down_revision: Union[str, None] = "<parent>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MOVED_KEYS = (
    "default_net_control",
    "net_address",
    "pat_mailbox_path",
    "scanner.enabled",
    "scanner.interval_minutes",
    "delivery.backends",
    "delivery.email.to_address",
    "delivery.groupsio.group_name",
    "delivery.winlink.target_address",
)


def upgrade() -> None:
    conn = op.get_bind()
    # Reflect just the tables we need
    nets = sa.table("nets", sa.column("id", sa.Integer))
    app_config = sa.table(
        "app_config",
        sa.column("key", sa.String),
        sa.column("value", sa.Text),
    )
    net_config = sa.table(
        "net_config",
        sa.column("net_id", sa.Integer),
        sa.column("key", sa.String),
        sa.column("value", sa.Text),
        sa.column("updated_at", sa.DateTime),
    )

    net_ids = [r.id for r in conn.execute(sa.select(nets.c.id))]
    moved_values: dict[str, str] = {}
    for row in conn.execute(
        sa.select(app_config.c.key, app_config.c.value).where(app_config.c.key.in_(MOVED_KEYS))
    ):
        moved_values[row.key] = row.value

    now = sa.func.now()

    for net_id in net_ids:
        existing = {
            r.key
            for r in conn.execute(
                sa.select(net_config.c.key).where(net_config.c.net_id == net_id)
            )
        }
        # Seed each moved key the net doesn't already have
        for key in MOVED_KEYS:
            if key in existing:
                continue
            if key not in moved_values:
                continue
            conn.execute(
                net_config.insert().values(
                    net_id=net_id,
                    key=key,
                    value=moved_values[key],
                    updated_at=now,
                )
            )
        # winlink_enabled: default true unless already present
        if "winlink_enabled" not in existing:
            conn.execute(
                net_config.insert().values(
                    net_id=net_id,
                    key="winlink_enabled",
                    value="true",
                    updated_at=now,
                )
            )

    # Drop the moved keys from app_config
    conn.execute(app_config.delete().where(app_config.c.key.in_(MOVED_KEYS)))


def downgrade() -> None:
    # Data migration — downgrade is intentionally a no-op. Restoring the
    # previous global rows from per-net values is ambiguous on a multi-net
    # install (which net's value wins?). Operators who need to roll back
    # should restore from a backup.
    pass
```

Keep the `revision`, `down_revision`, and `Create Date` values that Alembic generated — only replace the body.

- [ ] **Step 6: Run migration tests to verify they pass**

Run: `.venv/bin/pytest tests/test_migration_seed_net_config.py -v`
Expected: 4 passed.

- [ ] **Step 7: Sanity-check the head moves forward**

Run: `nix-shell --run "alembic heads"`
Expected: the new revision id is now the (sole) head.

Run: `nix-shell --run "alembic upgrade head --sql" > /tmp/migration.sql 2>&1 && head -50 /tmp/migration.sql`
Expected: no errors; SQL output includes the new revision's operations.

- [ ] **Step 8: Run the full test suite (regression check)**

Run: `.venv/bin/pytest -q`
Expected: green.

- [ ] **Step 9: Lint and commit**

```bash
nix-shell --run "ruff check alembic/versions tests/test_migration_seed_net_config.py"
git add alembic/versions/*seed_net_config_from_app_config.py tests/test_migration_seed_net_config.py
git commit -m "feat(migration): seed net_config from app_config and set winlink_enabled per net"
```

---

## Task 5: Reusable `SettingsSection` component + ConfigPage refactor

**Files:**
- Create: `frontend/src/components/SettingsSection.tsx`
- Modify: `frontend/src/api/config.ts`
- Modify: `frontend/src/pages/ConfigPage.tsx`

**Interfaces:**
- Consumes: bulk endpoint from Task 2.
- Produces:
  - `SettingsSection<F extends { key: string; visibleWhen?: (v: Record<string,string>) => boolean }>` React component (props below).
  - `setConfigBulk(values: Record<string, string>): Promise<void>` API client function.

**SettingsSection props contract** (used here AND by Task 6 — match it exactly):

```ts
interface SettingsSectionProps {
  title: string;
  fields: ConfigField[];                      // see ConfigField below
  values: Record<string, string>;             // current draft values
  savedValues: Record<string, string>;        // last saved snapshot
  onChange: (key: string, value: string) => void;
  onSave: (keys: string[]) => Promise<void>;  // saves the section's keys
  saving: boolean;
  children?: React.ReactNode;                 // extra UI under the field list (e.g., Test buttons)
}

interface ConfigField {
  key: string;
  label: string;
  type?: "text" | "boolean" | "multiselect";
  placeholder?: string;
  helpText: string;
  mono?: boolean;
  secret?: boolean;
  options?: { value: string; label: string }[];
  visibleWhen?: (values: Record<string, string>) => boolean;
}
```

- [ ] **Step 1: Add the API client function**

Edit `frontend/src/api/config.ts`. Append:

```ts
export async function setConfigBulk(
  values: Record<string, string>,
): Promise<void> {
  await apiFetch<unknown>(`/config/bulk`, {
    method: "PUT",
    body: JSON.stringify({ values }),
  });
}
```

- [ ] **Step 2: Create the `SettingsSection` component**

Create `frontend/src/components/SettingsSection.tsx`:

```tsx
import { useState } from "react";
import { Button } from "./Button";

export interface ConfigField {
  key: string;
  label: string;
  type?: "text" | "boolean" | "multiselect";
  placeholder?: string;
  helpText: string;
  mono?: boolean;
  secret?: boolean;
  options?: { value: string; label: string }[];
  visibleWhen?: (values: Record<string, string>) => boolean;
}

interface SettingsSectionProps {
  title: string;
  fields: ConfigField[];
  values: Record<string, string>;
  savedValues: Record<string, string>;
  onChange: (key: string, value: string) => void;
  onSave: (keys: string[]) => Promise<void>;
  saving: boolean;
  children?: React.ReactNode;
}

function parseStringArray(raw: string): string[] {
  try {
    const v = JSON.parse(raw || "[]");
    return Array.isArray(v) ? v.filter((s) => typeof s === "string") : [];
  } catch {
    return [];
  }
}

function FieldRow({
  field,
  value,
  onChange,
}: {
  field: ConfigField;
  value: string;
  onChange: (value: string) => void;
}) {
  const [showSecret, setShowSecret] = useState(false);
  const type = field.type ?? "text";

  let input: React.ReactNode;
  if (type === "boolean") {
    const checked = value === "true";
    input = (
      <label className="inline-flex items-center gap-2 text-sm text-text-primary">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked ? "true" : "false")}
          className="accent-accent"
        />
        <span className="text-text-secondary">{checked ? "Enabled" : "Disabled"}</span>
      </label>
    );
  } else if (type === "multiselect") {
    const selected = parseStringArray(value);
    const toggle = (v: string) => {
      const next = selected.includes(v)
        ? selected.filter((s) => s !== v)
        : [...selected, v];
      onChange(JSON.stringify(next));
    };
    input = (
      <div className="flex flex-col gap-1">
        {(field.options ?? []).map((opt) => (
          <label key={opt.value} className="inline-flex items-center gap-2 text-sm text-text-primary">
            <input
              type="checkbox"
              checked={selected.includes(opt.value)}
              onChange={() => toggle(opt.value)}
              className="accent-accent"
            />
            <span className="text-text-secondary">{opt.label}</span>
          </label>
        ))}
      </div>
    );
  } else {
    input = (
      <div className="relative max-w-md">
        <input
          type={field.secret && !showSecret ? "password" : "text"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder}
          className={`w-full bg-bg-elevated border border-border rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted ${
            field.mono ? "font-mono" : ""
          } ${field.secret ? "pr-10" : ""}`}
        />
        {field.secret && (
          <button
            type="button"
            onClick={() => setShowSecret(!showSecret)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary text-xs px-1"
            title={showSecret ? "Hide" : "Show"}
          >
            {showSecret ? "Hide" : "Show"}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="mb-4 last:mb-0">
      <label className="block text-sm text-text-secondary mb-1">{field.label}</label>
      {input}
      <div className="text-xs text-text-muted mt-1">{field.helpText}</div>
    </div>
  );
}

export function SettingsSection({
  title,
  fields,
  values,
  savedValues,
  onChange,
  onSave,
  saving,
  children,
}: SettingsSectionProps) {
  const visibleFields = fields.filter(
    (f) => !f.visibleWhen || f.visibleWhen(values),
  );
  if (visibleFields.length === 0 && !children) return null;

  const visibleKeys = visibleFields.map((f) => f.key);
  const dirty = visibleKeys.some(
    (k) => (values[k] ?? "") !== (savedValues[k] ?? ""),
  );

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-6 mb-4">
      <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-4">
        {title}
      </h2>
      {visibleFields.map((field) => (
        <FieldRow
          key={field.key}
          field={field}
          value={values[field.key] ?? ""}
          onChange={(v) => onChange(field.key, v)}
        />
      ))}
      <div className="flex items-center gap-2 mt-2">
        <div className="flex-1">{children}</div>
        <Button
          size="sm"
          variant={dirty ? "primary" : "secondary"}
          onClick={() => onSave(visibleKeys)}
          loading={saving}
          disabled={!dirty}
        >
          Save
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Rewrite `ConfigPage.tsx`**

Replace the entire contents of `frontend/src/pages/ConfigPage.tsx` with:

```tsx
import { useEffect, useState } from "react";
import { useToast } from "../context/ToastContext";
import { fetchConfig, setConfigBulk, sendGroupsIoTest } from "../api/config";
import { getFormsStatus, fetchFormsLibrary } from "../api/forms";
import type { FormsStatus } from "../api/forms";
import { Button } from "../components/Button";
import { OAuthProviderList } from "../components/OAuthProviderList";
import { SettingsSection } from "../components/SettingsSection";
import type { ConfigField } from "../components/SettingsSection";
import { SmtpForm } from "../components/SmtpForm";
import { Spinner } from "../components/Spinner";

const CALLBOOK_PROVIDER_OPTIONS = [
  { value: "hamqth", label: "HamQTH" },
  { value: "qrz", label: "QRZ" },
];

function parseStringArray(raw: string): string[] {
  try {
    const v = JSON.parse(raw || "[]");
    return Array.isArray(v) ? v.filter((s) => typeof s === "string") : [];
  } catch {
    return [];
  }
}

const AUTH_FIELDS: ConfigField[] = [
  {
    key: "registration_open",
    label: "Open Registration",
    type: "boolean",
    helpText:
      "When off, new OAuth sign-ins are refused (existing users still sign in). Turn off to prevent drive-by sign-ups from filling the database with pending rows.",
  },
];

const INTEGRATIONS_FIELDS: ConfigField[] = [
  {
    key: "claude_api_key",
    label: "Claude API Key",
    placeholder: "sk-ant-...",
    helpText: "API key for Claude-powered activity brainstorming (optional)",
    secret: true,
  },
];

const DELIVERY_GLOBAL_FIELDS: ConfigField[] = [
  {
    key: "delivery.groupsio.api_key",
    label: "Groups.io API Key",
    placeholder: "your-api-key",
    helpText: "API key for posting to groups.io. Shared across all nets that deliver via groups.io.",
    secret: true,
  },
];

const CALLBOOK_FIELDS: ConfigField[] = [
  {
    key: "callbook.providers",
    label: "Enabled Callbook Providers",
    type: "multiselect",
    options: CALLBOOK_PROVIDER_OPTIONS,
    helpText:
      "Providers tried in order when a check-in needs name/city resolution. Leave empty to disable callbook lookup.",
  },
  {
    key: "callbook.hamqth.username",
    label: "HamQTH Username",
    placeholder: "yourcall",
    helpText: "HamQTH.com login (the callsign you registered with)",
    visibleWhen: (v) => parseStringArray(v["callbook.providers"] ?? "").includes("hamqth"),
  },
  {
    key: "callbook.hamqth.password",
    label: "HamQTH Password",
    placeholder: "",
    helpText: "HamQTH.com account password",
    secret: true,
    visibleWhen: (v) => parseStringArray(v["callbook.providers"] ?? "").includes("hamqth"),
  },
  {
    key: "callbook.qrz.username",
    label: "QRZ Username",
    placeholder: "yourcall",
    helpText: "QRZ.com login (paid XML subscription required for lookups)",
    visibleWhen: (v) => parseStringArray(v["callbook.providers"] ?? "").includes("qrz"),
  },
  {
    key: "callbook.qrz.password",
    label: "QRZ Password",
    placeholder: "",
    helpText: "QRZ.com account password",
    secret: true,
    visibleWhen: (v) => parseStringArray(v["callbook.providers"] ?? "").includes("qrz"),
  },
];

function WinlinkFormsSection() {
  const { addToast } = useToast();
  const [status, setStatus] = useState<FormsStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetching, setFetching] = useState(false);

  const loadStatus = () => {
    setLoading(true);
    getFormsStatus()
      .then(setStatus)
      .catch(() => addToast("Failed to load Winlink Forms status", "error"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadStatus();
  }, []);

  const handleFetch = async () => {
    setFetching(true);
    try {
      const result = await fetchFormsLibrary();
      setStatus((prev) => prev ? { ...prev, library_version: result.library_version, last_fetched_at: result.last_fetched_at } : prev);
      addToast(`Forms library updated to version ${result.library_version}`, "success");
    } catch {
      addToast("Failed to fetch Winlink Standard Forms library", "error");
    } finally {
      setFetching(false);
    }
  };

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-6 mb-4">
      <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-4">
        Winlink Standard Forms
      </h2>
      {loading ? (
        <div className="flex justify-center py-4"><Spinner /></div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="text-sm text-text-secondary">
            <div>
              <span className="font-medium text-text-primary">Library version:</span>{" "}
              {status?.library_version ?? <span className="text-text-muted">Not downloaded</span>}
            </div>
            <div>
              <span className="font-medium text-text-primary">Last fetched:</span>{" "}
              {status?.last_fetched_at
                ? new Date(status.last_fetched_at).toLocaleString()
                : <span className="text-text-muted">—</span>}
            </div>
          </div>
          <div>
            <Button
              size="sm"
              variant="secondary"
              onClick={handleFetch}
              loading={fetching}
              title={status?.source_url ? `Download from ${status.source_url}` : "Fetch latest Winlink Standard Forms library"}
            >
              Fetch latest
            </Button>
            <div className="text-xs text-text-muted mt-1">
              Downloads and extracts the Winlink Standard Forms library used for rendering form check-ins.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function ConfigPage() {
  const { addToast } = useToast();
  const [values, setValues] = useState<Record<string, string>>({});
  const [savedValues, setSavedValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingSection, setSavingSection] = useState<string | null>(null);

  const loadConfig = () => {
    setLoading(true);
    setError(null);
    fetchConfig()
      .then((config) => {
        setValues(config);
        setSavedValues(config);
      })
      .catch(() => setError("Failed to load configuration"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadConfig();
  }, []);

  const handleSectionSave = (sectionId: string) => async (keys: string[]) => {
    setSavingSection(sectionId);
    try {
      const payload: Record<string, string> = {};
      for (const k of keys) {
        if ((values[k] ?? "") !== (savedValues[k] ?? "")) {
          payload[k] = values[k] ?? "";
        }
      }
      await setConfigBulk(payload);
      setSavedValues((prev) => ({ ...prev, ...payload }));
      addToast("Settings saved", "success");
    } catch {
      addToast("Failed to save settings", "error");
    } finally {
      setSavingSection(null);
    }
  };

  if (loading) {
    return <div className="flex justify-center py-8"><Spinner /></div>;
  }
  if (error) {
    return (
      <div className="text-center py-8">
        <p className="text-danger text-sm mb-2">{error}</p>
        <button onClick={loadConfig} className="text-accent text-sm hover:underline">Retry</button>
      </div>
    );
  }

  const handleGroupsIoTest = async () => {
    if (!confirm("Post a test message to the configured groups.io group?")) return;
    try {
      const result = await sendGroupsIoTest();
      if (result.ok) addToast("Test message posted to groups.io.", "success");
      else addToast(`Groups.io test failed: ${result.error ?? "unknown error"}`, "error");
    } catch (e: any) {
      addToast(`Groups.io test failed: ${e?.detail ?? e?.message ?? "request error"}`, "error");
    }
  };

  return (
    <div className="max-w-2xl">
      <h1 className="text-xl font-bold text-text-primary mb-6">Configuration</h1>

      <SettingsSection
        title="Auth"
        fields={AUTH_FIELDS}
        values={values}
        savedValues={savedValues}
        onChange={(k, v) => setValues((prev) => ({ ...prev, [k]: v }))}
        onSave={handleSectionSave("auth")}
        saving={savingSection === "auth"}
      />

      <OAuthProviderList />

      <SmtpForm />

      <WinlinkFormsSection />

      <SettingsSection
        title="Integrations"
        fields={INTEGRATIONS_FIELDS}
        values={values}
        savedValues={savedValues}
        onChange={(k, v) => setValues((prev) => ({ ...prev, [k]: v }))}
        onSave={handleSectionSave("integrations")}
        saving={savingSection === "integrations"}
      />

      <SettingsSection
        title="Delivery (global)"
        fields={DELIVERY_GLOBAL_FIELDS}
        values={values}
        savedValues={savedValues}
        onChange={(k, v) => setValues((prev) => ({ ...prev, [k]: v }))}
        onSave={handleSectionSave("delivery-global")}
        saving={savingSection === "delivery-global"}
      >
        {savedValues["delivery.groupsio.api_key"] && (
          <Button size="sm" variant="secondary" onClick={handleGroupsIoTest}>
            Send groups.io test
          </Button>
        )}
      </SettingsSection>

      <SettingsSection
        title="Callbook"
        fields={CALLBOOK_FIELDS}
        values={values}
        savedValues={savedValues}
        onChange={(k, v) => setValues((prev) => ({ ...prev, [k]: v }))}
        onSave={handleSectionSave("callbook")}
        saving={savingSection === "callbook"}
      />
    </div>
  );
}
```

- [ ] **Step 4: Type-check the frontend**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: build succeeds without TypeScript errors. (If `Button` doesn't accept the `onClick={async () => ...}` shape, wrap in a sync handler — match how `SmtpForm.tsx` does it.)

- [ ] **Step 5: Browser smoke-test**

Start the dev servers: `./run-dev.sh` (in another terminal).
Browser-test:
- Open `http://localhost:5173/config` as an admin.
- Confirm `default_net_control`, `net_address`, `pat_mailbox_path`, `scanner.*`, and `delivery.*` route fields are GONE.
- Confirm new "Auth" section at top with `registration_open` toggle.
- Edit one Callbook field → Save button activates; click Save → toast confirms; reload page → value persists.
- Edit `delivery.groupsio.api_key` → Save activates; save it; "Send groups.io test" button appears below if the field has a value.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/config.ts frontend/src/components/SettingsSection.tsx frontend/src/pages/ConfigPage.tsx
git commit -m "refactor(ui): section-level save on ConfigPage; remove per-net fields; add Auth section"
```

---

## Task 6: NetSettingsPage — winlink_enabled toggle, Delivery section, Winlink gating, section saves

**Files:**
- Modify: `frontend/src/api/nets.ts`
- Modify: `frontend/src/pages/NetSettingsPage.tsx`

**Interfaces:**
- Consumes: `set_net_config_bulk` endpoint from Task 3; `SettingsSection` and `ConfigField` from Task 5.
- Produces: a NetSettingsPage where `winlink_enabled` lives in General, Net Operations/PAT/Delivery sections each have one Save button, and Winlink-only fields are hidden when `winlink_enabled !== "true"`.

- [ ] **Step 1: Add the API client function**

Edit `frontend/src/api/nets.ts`. Append:

```ts
export async function setNetConfigBulk(
  slug: string,
  values: Record<string, string>,
): Promise<void> {
  await apiFetch(`/nets/${encodeURIComponent(slug)}/config/bulk`, {
    method: "PUT",
    body: JSON.stringify({ values }),
  });
}
```

- [ ] **Step 2: Rewrite `NetSettingsPage.tsx`**

Replace the entire contents of `frontend/src/pages/NetSettingsPage.tsx` with:

```tsx
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { SettingsSection } from "../components/SettingsSection";
import type { ConfigField } from "../components/SettingsSection";
import { Spinner } from "../components/Spinner";
import { useToast } from "../context/ToastContext";
import { useAuth } from "../hooks/useAuth";
import { useCurrentNet } from "../hooks/useCurrentNet";
import { getNetConfig, patchNet, setNetConfigBulk } from "../api/nets";

function parseStringArray(raw: string): string[] {
  try {
    const v = JSON.parse(raw || "[]");
    return Array.isArray(v) ? v.filter((s) => typeof s === "string") : [];
  } catch {
    return [];
  }
}

const NET_OPS_FIELDS: ConfigField[] = [
  {
    key: "default_net_control",
    label: "Net Callsign",
    placeholder: "WAØXYZ",
    helpText: "Your net's club callsign — used as {{ net_callsign }} in templates.",
    mono: true,
  },
  {
    key: "net_address",
    label: "Net Winlink Address",
    placeholder: "yournet@winlink.org",
    helpText:
      "Winlink address used for check-in message parsing and as {{ net_address }} in templates.",
    visibleWhen: (v) => v["winlink_enabled"] === "true",
  },
];

const PAT_FIELDS: ConfigField[] = [
  {
    key: "pat_mailbox_path",
    label: "PAT Mailbox Path",
    placeholder: "~/.local/share/pat/mailbox/YOURCALL",
    helpText: "Local filesystem path to the PAT Winlink client mailbox directory.",
    mono: true,
  },
  {
    key: "scanner.enabled",
    label: "Auto-Scanner",
    type: "boolean",
    helpText: "Automatically scan the PAT mailbox for new check-ins on a timer.",
  },
  {
    key: "scanner.interval_minutes",
    label: "Scan Interval (minutes)",
    placeholder: "5",
    helpText: "How often to scan the mailbox for new check-ins.",
    visibleWhen: (v) => v["scanner.enabled"] === "true",
  },
];

function deliveryFields(winlinkEnabled: boolean): ConfigField[] {
  const backendOptions = [
    { value: "email", label: "Email" },
    { value: "groupsio", label: "Groups.io" },
  ];
  if (winlinkEnabled) {
    backendOptions.push({ value: "winlink", label: "Winlink" });
  }
  return [
    {
      key: "delivery.backends",
      label: "Enabled Delivery Backends",
      type: "multiselect",
      options: backendOptions,
      helpText: "Channels for sending reminders and rosters from this net.",
    },
    {
      key: "delivery.email.to_address",
      label: "Email Recipient",
      placeholder: "net-list@example.com",
      helpText: "Email address this net sends reminders and rosters to.",
      visibleWhen: (v) => parseStringArray(v["delivery.backends"] ?? "").includes("email"),
    },
    {
      key: "delivery.groupsio.group_name",
      label: "Groups.io Group Name",
      placeholder: "your-net",
      helpText: "Target group name on groups.io for this net.",
      visibleWhen: (v) => parseStringArray(v["delivery.backends"] ?? "").includes("groupsio"),
    },
    {
      key: "delivery.winlink.target_address",
      label: "Winlink Delivery Address",
      placeholder: "NET@winlink.org",
      helpText: "Winlink address this net sends reminders and rosters to.",
      visibleWhen: (v) =>
        v["winlink_enabled"] === "true" &&
        parseStringArray(v["delivery.backends"] ?? "").includes("winlink"),
    },
  ];
}

export function NetSettingsPage() {
  const { net, slug } = useCurrentNet();
  const { user, refreshUser } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();

  const [name, setName] = useState("");
  const [slugDraft, setSlugDraft] = useState("");
  const [isPublic, setIsPublic] = useState(false);
  const [winlinkEnabled, setWinlinkEnabled] = useState(true);
  const [savingMeta, setSavingMeta] = useState(false);

  const [config, setConfig] = useState<Record<string, string>>({});
  const [savedConfig, setSavedConfig] = useState<Record<string, string>>({});
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [savingSection, setSavingSection] = useState<string | null>(null);

  const isAdmin = user?.is_admin === true;

  useEffect(() => {
    if (!net) return;
    setName(net.name);
    setSlugDraft(net.slug);
    setIsPublic(net.is_public);
  }, [net]);

  useEffect(() => {
    if (!slug) return;
    setLoadingConfig(true);
    getNetConfig(slug)
      .then((c) => {
        setConfig(c);
        setSavedConfig(c);
        setWinlinkEnabled((c["winlink_enabled"] ?? "true") === "true");
      })
      .catch(() => addToast("Failed to load per-net config", "error"))
      .finally(() => setLoadingConfig(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  if (!net) {
    return <div className="flex justify-center py-8"><Spinner /></div>;
  }

  const winlinkEnabledSaved = (savedConfig["winlink_enabled"] ?? "true") === "true";
  const generalDirty =
    name !== net.name ||
    slugDraft !== net.slug ||
    isPublic !== net.is_public ||
    winlinkEnabled !== winlinkEnabledSaved;

  const handleSaveGeneral = async () => {
    setSavingMeta(true);
    try {
      const patch: { name?: string; slug?: string; is_public?: boolean } = {};
      if (name !== net.name) patch.name = name;
      if (isAdmin && slugDraft !== net.slug) patch.slug = slugDraft;
      if (isAdmin && isPublic !== net.is_public) patch.is_public = isPublic;
      const newSlug =
        Object.keys(patch).length > 0
          ? (await patchNet(net.slug, patch)).slug
          : net.slug;
      if (winlinkEnabled !== winlinkEnabledSaved) {
        const wlValue = winlinkEnabled ? "true" : "false";
        await setNetConfigBulk(newSlug, { winlink_enabled: wlValue });
        setSavedConfig((prev) => ({ ...prev, winlink_enabled: wlValue }));
        setConfig((prev) => ({ ...prev, winlink_enabled: wlValue }));
      }
      addToast("Settings saved", "success");
      await refreshUser();
      if (patch.slug && patch.slug !== net.slug) {
        navigate(`/nets/${newSlug}/settings`, { replace: true });
      }
    } catch (e) {
      addToast(`Save failed: ${e instanceof Error ? e.message : "unknown"}`, "error");
    } finally {
      setSavingMeta(false);
    }
  };

  const handleSectionSave = (sectionId: string) => async (keys: string[]) => {
    setSavingSection(sectionId);
    try {
      const payload: Record<string, string> = {};
      for (const k of keys) {
        if ((config[k] ?? "") !== (savedConfig[k] ?? "")) {
          payload[k] = config[k] ?? "";
        }
      }
      await setNetConfigBulk(slug, payload);
      setSavedConfig((prev) => ({ ...prev, ...payload }));
      addToast("Settings saved", "success");
    } catch {
      addToast("Failed to save settings", "error");
    } finally {
      setSavingSection(null);
    }
  };

  return (
    <div className="max-w-2xl">
      <h1 className="text-xl font-bold text-text-primary mb-6">
        Net Settings: {net.name}
      </h1>

      <div className="bg-bg-surface border border-border rounded-lg p-6 mb-4">
        <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-4">
          General
        </h2>
        <div className="flex flex-col gap-4">
          <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} />
          <div>
            <Input
              label="Slug"
              value={slugDraft}
              onChange={(e) => setSlugDraft(e.target.value)}
              mono
              disabled={!isAdmin}
            />
            {!isAdmin && (
              <p className="text-xs text-text-muted mt-1">Slug changes require admin.</p>
            )}
          </div>
          <label className="inline-flex items-center gap-2 text-sm text-text-primary">
            <input
              type="checkbox"
              checked={isPublic}
              onChange={(e) => setIsPublic(e.target.checked)}
              disabled={!isAdmin}
              className="accent-accent"
            />
            <span className="text-text-secondary">
              Public net (anonymous read access to check-ins)
            </span>
          </label>
          {!isAdmin && (
            <p className="text-xs text-text-muted -mt-2">
              Visibility changes require admin.
            </p>
          )}
          <label className="inline-flex items-center gap-2 text-sm text-text-primary">
            <input
              type="checkbox"
              checked={winlinkEnabled}
              onChange={(e) => setWinlinkEnabled(e.target.checked)}
              className="accent-accent"
            />
            <span className="text-text-secondary">
              Winlink-enabled (shows Winlink Address, PAT, and Winlink delivery options)
            </span>
          </label>
          <div>
            <Button
              variant={generalDirty ? "primary" : "secondary"}
              onClick={handleSaveGeneral}
              loading={savingMeta}
              disabled={!generalDirty}
            >
              Save
            </Button>
          </div>
        </div>
      </div>

      {loadingConfig ? (
        <div className="flex justify-center py-4"><Spinner /></div>
      ) : (
        <>
          <SettingsSection
            title="Net Operations"
            fields={NET_OPS_FIELDS}
            values={config}
            savedValues={savedConfig}
            onChange={(k, v) => setConfig((prev) => ({ ...prev, [k]: v }))}
            onSave={handleSectionSave("net-ops")}
            saving={savingSection === "net-ops"}
          />

          {winlinkEnabledSaved && (
            <SettingsSection
              title="PAT"
              fields={PAT_FIELDS}
              values={config}
              savedValues={savedConfig}
              onChange={(k, v) => setConfig((prev) => ({ ...prev, [k]: v }))}
              onSave={handleSectionSave("pat")}
              saving={savingSection === "pat"}
            />
          )}

          <SettingsSection
            title="Delivery"
            fields={deliveryFields(winlinkEnabledSaved)}
            values={config}
            savedValues={savedConfig}
            onChange={(k, v) => setConfig((prev) => ({ ...prev, [k]: v }))}
            onSave={handleSectionSave("delivery")}
            saving={savingSection === "delivery"}
          />
        </>
      )}
    </div>
  );
}
```

Notes on the implementation choices in this rewrite:
- `winlinkEnabled` lives in component state alongside the General-section fields so the General-section Save handles it.
- Gating uses `winlinkEnabledSaved` (last saved value) — so toggling but not saving doesn't yet hide the dependent sections; the user sees a coherent state until they commit.
- The Delivery section's `winlink` backend option only appears when Winlink is saved-enabled; the Winlink target_address field is gated by both `winlink_enabled` and presence of "winlink" in `delivery.backends`.

- [ ] **Step 3: Type-check the frontend**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: build succeeds.

- [ ] **Step 4: Browser smoke-test**

With `./run-dev.sh` running:
- Visit `/nets/<slug>/settings` as a net-control user.
- Confirm General has the new Winlink-enabled checkbox.
- Edit Net Callsign in Net Operations → Save button activates → click Save → toast → reload → persists.
- Toggle Winlink off, click Save in General → PAT section disappears, Net Winlink Address disappears, Winlink option vanishes from delivery backends.
- Toggle Winlink back on, Save → PAT section reappears.
- Edit `delivery.backends` to include groups.io → "Groups.io Group Name" field appears → fill it → Save → persists.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/nets.ts frontend/src/pages/NetSettingsPage.tsx
git commit -m "feat(nets): per-net Winlink toggle, Delivery section, section-level saves"
```

---

## Task 7: Full regression check + push

**Files:** none (verification only).

- [ ] **Step 1: Run full backend test suite**

Run: `.venv/bin/pytest -q`
Expected: green.

- [ ] **Step 2: Run lint**

Run: `nix-shell --run "ruff check"`
Expected: clean.

- [ ] **Step 3: Re-build the frontend production bundle**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: success.

- [ ] **Step 4: Migration dry-run against current SQLite DB (manual sanity check)**

If `skynetcontrol.db` exists in the repo root: back it up first (`cp skynetcontrol.db skynetcontrol.db.pre-settings-reorg`), then run `nix-shell --run "alembic upgrade head"`. Expected: no errors. Inspect a couple of nets with sqlite3 to confirm `winlink_enabled=true` is present and the moved keys appear in `net_config`.

- [ ] **Step 5: Push (ONLY after user confirmation per CLAUDE.md)**

Ask the user whether to push. Do not push without explicit confirmation. If approved:

```bash
git push
```

Then watch CI: `gh run list --branch main --limit 4 --json status,conclusion,name,headSha` (parse with python3, not jq). Wait until both `CI` and `Container` workflows are green before considering the task complete.

---

## Self-review notes

- All nine "now per-net" keys appear in: the migration's `MOVED_KEYS`, the spec, the removed-fields commentary in Task 5, and either Net Operations / PAT / Delivery in Task 6. Cross-checked.
- `winlink_enabled` defaults to `"true"` everywhere it's read: migration seeds it, the NetSettingsPage init defaults if missing, and the saved-vs-unsaved comparison treats absence as `"true"`.
- The `SettingsSection` component contract is identical in Task 5 and Task 6.
- No backend-call-site changes needed for `winlink_enabled` — current callers of scanner / reminders / roster already short-circuit when their required per-net values are missing or off, so a non-Winlink net naturally produces no scanner activity, no Winlink delivery, etc. If a future bug shows up where backend logic should branch on `winlink_enabled`, that's a follow-up — out of scope here.
