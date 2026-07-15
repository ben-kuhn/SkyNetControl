# Live Map + APRS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live APRS positions for event participants (plus opt-in nearby stations) on a Leaflet map, with event posts transmitted as APRS objects.

**Architecture:** New `backend/integrations/aprs/` package: pure protocol helpers, an in-memory per-event position store with a `pos_seq` cursor, and an asyncio APRS-IS client task per active event (verified login, server-side filters, object beaconing with kill packets) supervised by a small manager keyed by event id. One new cursored endpoint (`/positions`) feeds a shared `EventMap` Leaflet component rendered as a collapsible dashboard panel and a full-screen route.

**Tech Stack:** aprslib (parsing + passcode), asyncio streams, FastAPI, SQLAlchemy; React 19 + TS + Leaflet 1.9.

**Spec:** `docs/superpowers/specs/2026-07-15-live-map-aprs-design.md`.

## Global Constraints

- Host is NixOS: backend via `.venv/bin/...`; frontend via `cd frontend && nix-shell -p nodejs_22 --run "npm <…>"`. After editing `pyproject.toml`, refresh the venv with `nix-shell --run :` (re-pip-installs `.[dev]`).
- Lint: `nix-shell --run "ruff check"` — line-length 120, select E+F.
- Commits: Conventional Commits (`feat(aprs): …`, `feat(events): …`).
- Positions are **in-memory only** — no DB persistence of position data.
- Transmit is deliberate: object beaconing only when `event.aprs_beacon_posts` is true AND event is active; kill packets on close/toggle-off/post-delete.
- The positions payload returns the complete station roster every poll; only points are cursored (`pos_seq > since`). Client replaces roster, accumulates points.
- Hides are client-side view state; the feed is never filtered by hides.
- APRS failures never affect event operation; per-line exception guard; reconnect backoff capped at 300 s.
- Timestamps `datetime.now(timezone.utc)`. All writes attributed/gated exactly as in the events module (NCS writes, viewer reads).
- Frontend: no new libraries; plain fetch/useState patterns; build (`npm run build`) is the frontend gate.

---

### Task 1: Event APRS settings — model, migration, PATCH plumbing, aprslib dependency

**Files:**
- Modify: `backend/modules/events/models.py` (Event class)
- Modify: `backend/modules/events/service.py` (`update_event`)
- Modify: `backend/modules/events/routes.py` (`EventUpdate`, `_event_to_response`)
- Modify: `pyproject.toml` (add aprslib)
- Create: `alembic/versions/b3f0a1c2d4e5_add_event_aprs_settings.py`
- Test: `tests/test_event_aprs_settings.py`

**Interfaces:**
- Consumes: existing Event model / `_UNSET` sentinel / `EventUpdate` exclude-unset PATCH flow.
- Produces: `Event.aprs_other_stations: bool`, `Event.aprs_range_lat/lon/km: float | None`, `Event.aprs_beacon_posts: bool`; all five PATCH-able via `PATCH /events/{id}` and present in every event response; `aprslib` importable.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_event_aprs_settings.py
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.events.models import Event, EventType
from backend.modules.nets.models import Net, NetMembership, NetRole
from tests.conftest import make_test_net, make_test_token

NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/events"


def test_aprslib_importable():
    import aprslib

    assert aprslib.passcode("N0CALL") == 13023


def test_event_aprs_defaults():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        net = make_test_net(db)
        event = Event(net_id=net.id, name="E", event_type=EventType.EMERGENCY, created_by="W0NE")
        db.add(event)
        db.commit()
        db.refresh(event)
        assert event.aprs_other_stations is False
        assert event.aprs_beacon_posts is False
        assert event.aprs_range_lat is None
        assert event.aprs_range_lon is None
        assert event.aprs_range_km is None
    engine.dispose()


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
        net = Net(slug=NET_SLUG, name="Test Net", is_public=False)
        session.add_all([nc, net])
        session.flush()
        session.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
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


class TestAprsSettingsPatch:
    async def test_response_includes_aprs_fields(self, nc_client):
        resp = await nc_client.post(BASE, json={"name": "E", "event_type": "emergency", "activate": True})
        body = resp.json()
        assert body["aprs_other_stations"] is False
        assert body["aprs_beacon_posts"] is False
        assert body["aprs_range_km"] is None

    async def test_patch_aprs_fields(self, nc_client):
        event_id = (await nc_client.post(
            BASE, json={"name": "E", "event_type": "emergency", "activate": True}
        )).json()["id"]
        resp = await nc_client.patch(f"{BASE}/{event_id}", json={
            "aprs_other_stations": True,
            "aprs_range_lat": 39.1,
            "aprs_range_lon": -94.6,
            "aprs_range_km": 50,
            "aprs_beacon_posts": True,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["aprs_other_stations"] is True
        assert body["aprs_range_km"] == 50
        assert body["aprs_beacon_posts"] is True

    async def test_patch_partial_leaves_others(self, nc_client):
        event_id = (await nc_client.post(
            BASE, json={"name": "E", "event_type": "emergency", "activate": True}
        )).json()["id"]
        await nc_client.patch(f"{BASE}/{event_id}", json={"aprs_beacon_posts": True})
        resp = await nc_client.patch(f"{BASE}/{event_id}", json={"name": "Renamed"})
        assert resp.json()["aprs_beacon_posts"] is True
```

- [ ] **Step 2: Run tests, verify failure**

Run: `.venv/bin/pytest tests/test_event_aprs_settings.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aprslib'` (or AttributeError on the model fields)

- [ ] **Step 3: Add dependency and refresh venv**

In `pyproject.toml` `dependencies`, after `"bleach>=6.0.0",` add:

```toml
    "aprslib>=0.7.0",
```

Run: `nix-shell --run :` (reinstalls `.[dev]` into the venv). Verify: `.venv/bin/python -c "import aprslib; print(aprslib.passcode('N0CALL'))"` prints `13023`.

- [ ] **Step 4: Add model columns**

In `backend/modules/events/models.py`, inside `class Event`, after the `log_seq` column:

```python
    # Per-event APRS map settings (sub-project 2). Positions themselves are
    # in-memory only; these persisted flags configure the live client.
    aprs_other_stations: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    aprs_range_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    aprs_range_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    aprs_range_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    aprs_beacon_posts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

(`Float` is already imported in this file.)

- [ ] **Step 5: Write the migration**

```python
# alembic/versions/b3f0a1c2d4e5_add_event_aprs_settings.py
"""add event aprs settings

Revision ID: b3f0a1c2d4e5
Revises: e7a3c9d41b20
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f0a1c2d4e5'
down_revision: Union[str, None] = 'e7a3c9d41b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOT NULL adds to an existing table need a server_default for old rows.
    op.add_column('events', sa.Column('aprs_other_stations', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column('events', sa.Column('aprs_range_lat', sa.Float(), nullable=True))
    op.add_column('events', sa.Column('aprs_range_lon', sa.Float(), nullable=True))
    op.add_column('events', sa.Column('aprs_range_km', sa.Float(), nullable=True))
    op.add_column('events', sa.Column('aprs_beacon_posts', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('events', 'aprs_beacon_posts')
    op.drop_column('events', 'aprs_range_km')
    op.drop_column('events', 'aprs_range_lon')
    op.drop_column('events', 'aprs_range_lat')
    op.drop_column('events', 'aprs_other_stations')
```

Verify: `SKYNET_DATABASE_URL="sqlite:////tmp/claude-aprs-mig.db" .venv/bin/alembic upgrade head && rm -f /tmp/claude-aprs-mig.db` runs clean.

- [ ] **Step 6: Extend service `update_event`**

In `backend/modules/events/service.py`, extend the `update_event` signature and body (keep existing params):

```python
def update_event(
    db: Session,
    event_id: int,
    *,
    name: str | None = None,
    description: object = _UNSET,
    scheduled_start: object = _UNSET,
    aprs_other_stations: object = _UNSET,
    aprs_range_lat: object = _UNSET,
    aprs_range_lon: object = _UNSET,
    aprs_range_km: object = _UNSET,
    aprs_beacon_posts: object = _UNSET,
) -> Event | None:
```

and after the `scheduled_start` assignment:

```python
    if aprs_other_stations is not _UNSET:
        event.aprs_other_stations = aprs_other_stations
    if aprs_range_lat is not _UNSET:
        event.aprs_range_lat = aprs_range_lat
    if aprs_range_lon is not _UNSET:
        event.aprs_range_lon = aprs_range_lon
    if aprs_range_km is not _UNSET:
        event.aprs_range_km = aprs_range_km
    if aprs_beacon_posts is not _UNSET:
        event.aprs_beacon_posts = aprs_beacon_posts
```

- [ ] **Step 7: Extend routes schema + response**

In `backend/modules/events/routes.py`:

`EventUpdate` gains:

```python
    aprs_other_stations: bool | None = None
    aprs_range_lat: float | None = None
    aprs_range_lon: float | None = None
    aprs_range_km: float | None = None
    aprs_beacon_posts: bool | None = None
```

`_event_to_response` gains, before the closing brace:

```python
        "aprs_other_stations": event.aprs_other_stations,
        "aprs_range_lat": event.aprs_range_lat,
        "aprs_range_lon": event.aprs_range_lon,
        "aprs_range_km": event.aprs_range_km,
        "aprs_beacon_posts": event.aprs_beacon_posts,
```

- [ ] **Step 8: Run tests, full suite, lint**

Run: `.venv/bin/pytest tests/test_event_aprs_settings.py -q` — expected all pass.
Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml backend/modules/events/ alembic/versions/b3f0a1c2d4e5_add_event_aprs_settings.py tests/test_event_aprs_settings.py
git commit -m "feat(events): per-event APRS settings and aprslib dependency"
```

---

### Task 2: APRS protocol helpers (pure functions)

**Files:**
- Create: `backend/integrations/aprs/__init__.py` (empty)
- Create: `backend/integrations/aprs/protocol.py`
- Test: `tests/test_aprs_protocol.py`

**Interfaces:**
- Consumes: `aprslib.passcode`.
- Produces (all in `backend.integrations.aprs.protocol`):
  - `login_line(callsign: str, filter_spec: str) -> str`
  - `filter_command(filter_spec: str) -> str` (`"#filter <spec>"`)
  - `build_filter(callsigns: set[str], *, range_lat=None, range_lon=None, range_km=None) -> str` (empty string when nothing to filter)
  - `object_name(post_name: str, taken: set[str]) -> str` (9-char APRS object name, uniquified)
  - `object_packet(src_callsign: str, name: str, lat: float, lon: float, comment: str, *, kill: bool = False, now: datetime | None = None) -> str` (full TNC2 frame)
  - Constants: `APRS_DEST = "APZSNC"`, `BEACON_INTERVAL_S = 600`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_aprs_protocol.py
from datetime import datetime, timezone

from backend.integrations.aprs.protocol import (
    build_filter,
    filter_command,
    login_line,
    object_name,
    object_packet,
)


class TestLogin:
    def test_login_line_contains_computed_passcode(self):
        line = login_line("N0CALL", "b/N0CALL*")
        assert line.startswith("user N0CALL pass 13023 vers SkyNetControl")
        assert line.endswith("filter b/N0CALL*")

    def test_login_line_without_filter(self):
        line = login_line("n0call", "")
        assert "filter" not in line
        assert "N0CALL" in line


class TestFilter:
    def test_buddy_terms_wildcard_and_strip_ssid(self):
        spec = build_filter({"ke0xyz", "W0NE-9"})
        assert spec == "b/KE0XYZ* b/W0NE*"  # sorted, base callsign, wildcarded

    def test_range_term(self):
        spec = build_filter(set(), range_lat=39.1234, range_lon=-94.5678, range_km=50)
        assert spec == "r/39.1234/-94.5678/50"

    def test_combined(self):
        spec = build_filter({"W0NE"}, range_lat=39.0, range_lon=-94.0, range_km=25)
        assert spec == "b/W0NE* r/39.0000/-94.0000/25"

    def test_empty(self):
        assert build_filter(set()) == ""

    def test_partial_range_ignored(self):
        assert build_filter(set(), range_lat=39.0) == ""

    def test_filter_command(self):
        assert filter_command("b/W0NE*") == "#filter b/W0NE*"


class TestObjectName:
    def test_derivation(self):
        assert object_name("Rest Stop 3", set()) == "RESTSTOP3"

    def test_truncation(self):
        assert object_name("Water Station Alpha", set()) == "WATERSTAT"

    def test_uniquify(self):
        assert object_name("Rest Stop 3", {"RESTSTOP3"}) == "RESTSTO2"

    def test_empty_falls_back(self):
        assert object_name("!!!", set()) == "POST"


class TestObjectPacket:
    NOW = datetime(2026, 7, 15, 18, 30, 0, tzinfo=timezone.utc)

    def test_live_object(self):
        pkt = object_packet("W0NE", "RESTSTOP3", 39.0625, -94.5786, "SkyNetControl event: Marathon", now=self.NOW)
        assert pkt == (
            "W0NE>APZSNC,TCPIP*:;RESTSTOP3*151830z3903.75N/09434.72Wo"
            "SkyNetControl event: Marathon"
        )

    def test_kill_object(self):
        pkt = object_packet("W0NE", "EOC", 39.0, -94.0, "", kill=True, now=self.NOW)
        # 9-char name space-padded, '_' = kill flag
        assert ";EOC      _151830z" in pkt
        assert pkt.startswith("W0NE>APZSNC,TCPIP*:")

    def test_southern_eastern_hemispheres(self):
        pkt = object_packet("W0NE", "X", -33.8688, 151.2093, "", now=self.NOW)
        assert "3352.13S/15112.56E" in pkt
```

- [ ] **Step 2: Run tests, verify failure**

Run: `.venv/bin/pytest tests/test_aprs_protocol.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.integrations.aprs'`

- [ ] **Step 3: Implement**

Create `backend/integrations/aprs/__init__.py` (empty), then:

```python
# backend/integrations/aprs/protocol.py
"""Pure APRS/APRS-IS protocol helpers: login, filters, and object packets.

No I/O here — everything is a string in, string out, so it's all unit-testable
without a network.
"""
import re
from datetime import datetime, timezone

import aprslib

APRS_DEST = "APZSNC"  # APZ* = experimental destination; SNC = SkyNetControl
OBJECT_SYMBOL_TABLE = "/"
OBJECT_SYMBOL_CODE = "o"  # primary-table 'o' (EOC) — used for all event post objects
BEACON_INTERVAL_S = 600  # normal object re-beacon cadence
APP_VERSION = "0.1.0"


def login_line(callsign: str, filter_spec: str) -> str:
    """APRS-IS login. The passcode is the public 15-bit hash of the base
    callsign (aprslib implements it) — verified login, since we transmit."""
    callsign = callsign.upper()
    line = f"user {callsign} pass {aprslib.passcode(callsign)} vers SkyNetControl {APP_VERSION}"
    if filter_spec:
        line += f" filter {filter_spec}"
    return line


def filter_command(filter_spec: str) -> str:
    """Mid-session filter replacement command."""
    return f"#filter {filter_spec}"


def build_filter(
    callsigns: set[str],
    *,
    range_lat: float | None = None,
    range_lon: float | None = None,
    range_km: float | None = None,
) -> str:
    """Server-side filter: wildcarded buddy terms for each participant's base
    callsign (matches every SSID), plus an optional range term. Empty string
    when there is nothing to ask for — the filtered port sends nothing then."""
    bases = sorted({cs.split("-")[0].upper() for cs in callsigns if cs.strip()})
    terms = [f"b/{base}*" for base in bases]
    if range_lat is not None and range_lon is not None and range_km is not None:
        terms.append(f"r/{range_lat:.4f}/{range_lon:.4f}/{range_km:.0f}")
    return " ".join(terms)


def object_name(post_name: str, taken: set[str]) -> str:
    """APRS object names are max 9 chars. Uppercase, strip non-alphanumerics,
    truncate, uniquify with a numeric suffix on collision."""
    base = re.sub(r"[^A-Z0-9]", "", post_name.upper())[:9] or "POST"
    if base not in taken:
        return base
    for i in range(2, 100):
        suffix = str(i)
        candidate = base[: 9 - len(suffix)] + suffix
        if candidate not in taken:
            return candidate
    raise ValueError(f"Could not uniquify object name for {post_name!r}")


def _fmt_lat(lat: float) -> str:
    hemi = "N" if lat >= 0 else "S"
    lat = abs(lat)
    degrees = int(lat)
    minutes = (lat - degrees) * 60
    return f"{degrees:02d}{minutes:05.2f}{hemi}"


def _fmt_lon(lon: float) -> str:
    hemi = "E" if lon >= 0 else "W"
    lon = abs(lon)
    degrees = int(lon)
    minutes = (lon - degrees) * 60
    return f"{degrees:03d}{minutes:05.2f}{hemi}"


def object_packet(
    src_callsign: str,
    name: str,
    lat: float,
    lon: float,
    comment: str,
    *,
    kill: bool = False,
    now: datetime | None = None,
) -> str:
    """APRS object report as a full TNC2 frame. kill=True emits the object
    with the '_' flag so other clients delete it."""
    flag = "_" if kill else "*"
    ts = (now or datetime.now(timezone.utc)).strftime("%d%H%M")
    body = (
        f";{name:<9}{flag}{ts}z"
        f"{_fmt_lat(lat)}{OBJECT_SYMBOL_TABLE}{_fmt_lon(lon)}{OBJECT_SYMBOL_CODE}"
        f"{comment}"
    )
    return f"{src_callsign.upper()}>{APRS_DEST},TCPIP*:{body}"
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_aprs_protocol.py -q` — expected all pass. (If the exact-string assertions fail on minute rounding, fix the test expectation by computing by hand: 39.0625° → 39° 03.75'; -94.5786° → 094° 34.72'W — the implementation is the spec here.)

- [ ] **Step 5: Lint + commit**

```bash
nix-shell --run "ruff check"
git add backend/integrations/aprs/ tests/test_aprs_protocol.py
git commit -m "feat(aprs): protocol helpers - login, filters, object packets"
```

---

### Task 3: In-memory position store

**Files:**
- Create: `backend/integrations/aprs/store.py`
- Test: `tests/test_aprs_store.py`

**Interfaces:**
- Consumes: nothing project-specific.
- Produces (in `backend.integrations.aprs.store`):
  - `TRAIL_MAX_POINTS = 120`, `OTHER_STATIONS_CAP = 200`
  - `class EventPositionStore` with:
    - `add_point(station_id: str, lat: float, lon: float, *, kind: str, callsign: str | None = None, symbol: str | None = None, comment: str | None = None, ts: datetime | None = None) -> int` (returns the assigned `pos_seq`; kind is `"participant"` or `"other"`)
    - `latest_pos_seq: int` (property)
    - `drop_others() -> None`
    - `snapshot(since: int = 0) -> dict` — `{"stations": [...], "latest_pos_seq": int}` where each station is `{station_id, kind, callsign, symbol, comment, last_heard (iso), points: [{lat, lon, ts (iso), pos_seq}]}` with only points `pos_seq > since`; the roster always lists every station.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_aprs_store.py
from datetime import datetime, timezone

from backend.integrations.aprs.store import (
    OTHER_STATIONS_CAP,
    TRAIL_MAX_POINTS,
    EventPositionStore,
)


def _ts(minute):
    return datetime(2026, 7, 15, 18, minute % 60, 0, tzinfo=timezone.utc)


class TestAddAndSnapshot:
    def test_pos_seq_monotonic(self):
        store = EventPositionStore()
        s1 = store.add_point("KE0XYZ-9", 39.0, -94.0, kind="participant", callsign="KE0XYZ")
        s2 = store.add_point("KE0XYZ-9", 39.1, -94.1, kind="participant", callsign="KE0XYZ")
        assert (s1, s2) == (1, 2)
        assert store.latest_pos_seq == 2

    def test_snapshot_full_and_delta(self):
        store = EventPositionStore()
        store.add_point("KE0XYZ-9", 39.0, -94.0, kind="participant", callsign="KE0XYZ", ts=_ts(0))
        store.add_point("KE0XYZ-9", 39.1, -94.1, kind="participant", callsign="KE0XYZ", ts=_ts(1))
        full = store.snapshot(since=0)
        assert len(full["stations"]) == 1
        assert len(full["stations"][0]["points"]) == 2
        assert full["latest_pos_seq"] == 2

        delta = store.snapshot(since=1)
        assert len(delta["stations"][0]["points"]) == 1
        assert delta["stations"][0]["points"][0]["pos_seq"] == 2

    def test_roster_always_complete_even_with_no_new_points(self):
        store = EventPositionStore()
        store.add_point("KE0XYZ-9", 39.0, -94.0, kind="participant", callsign="KE0XYZ")
        snap = store.snapshot(since=99)
        assert len(snap["stations"]) == 1
        assert snap["stations"][0]["points"] == []
        assert snap["stations"][0]["station_id"] == "KE0XYZ-9"

    def test_station_metadata(self):
        store = EventPositionStore()
        store.add_point(
            "KE0XYZ-9", 39.0, -94.0, kind="participant", callsign="KE0XYZ",
            symbol="/>", comment="mobile", ts=_ts(5),
        )
        st = store.snapshot()["stations"][0]
        assert st["kind"] == "participant"
        assert st["callsign"] == "KE0XYZ"
        assert st["symbol"] == "/>"
        assert st["comment"] == "mobile"
        assert st["last_heard"] == _ts(5).isoformat()

    def test_trail_bounded(self):
        store = EventPositionStore()
        for i in range(TRAIL_MAX_POINTS + 30):
            store.add_point("W0NE-9", 39.0 + i * 0.001, -94.0, kind="participant", callsign="W0NE")
        pts = store.snapshot()["stations"][0]["points"]
        assert len(pts) == TRAIL_MAX_POINTS
        # oldest points dropped, newest kept
        assert pts[-1]["pos_seq"] == TRAIL_MAX_POINTS + 30


class TestOthers:
    def test_lru_cap(self):
        store = EventPositionStore()
        for i in range(OTHER_STATIONS_CAP + 10):
            store.add_point(f"X{i}", 39.0, -94.0, kind="other")
        others = [s for s in store.snapshot()["stations"] if s["kind"] == "other"]
        assert len(others) == OTHER_STATIONS_CAP
        ids = {s["station_id"] for s in others}
        assert "X0" not in ids  # evicted
        assert f"X{OTHER_STATIONS_CAP + 9}" in ids

    def test_readd_refreshes_lru(self):
        store = EventPositionStore()
        for i in range(OTHER_STATIONS_CAP):
            store.add_point(f"X{i}", 39.0, -94.0, kind="other")
        store.add_point("X0", 39.5, -94.5, kind="other")  # refresh oldest
        store.add_point("NEW", 39.0, -94.0, kind="other")  # evicts X1, not X0
        ids = {s["station_id"] for s in store.snapshot()["stations"]}
        assert "X0" in ids
        assert "X1" not in ids

    def test_drop_others_keeps_participants(self):
        store = EventPositionStore()
        store.add_point("KE0XYZ-9", 39.0, -94.0, kind="participant", callsign="KE0XYZ")
        store.add_point("STRANGER", 39.0, -94.0, kind="other")
        store.drop_others()
        stations = store.snapshot()["stations"]
        assert len(stations) == 1
        assert stations[0]["kind"] == "participant"
```

- [ ] **Step 2: Run tests, verify failure**

Run: `.venv/bin/pytest tests/test_aprs_store.py -q`
Expected: FAIL — import error

- [ ] **Step 3: Implement**

```python
# backend/integrations/aprs/store.py
"""Per-event in-memory position store. Deliberately unpersisted: dies with
the event's client task or a server restart (spec decision)."""
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

TRAIL_MAX_POINTS = 120
OTHER_STATIONS_CAP = 200


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TrackPoint:
    lat: float
    lon: float
    ts: datetime
    pos_seq: int


@dataclass
class Station:
    station_id: str  # full callsign-SSID, e.g. "KE0XYZ-9"
    kind: str  # "participant" | "other"
    callsign: str | None  # participant base callsign (None for others)
    symbol: str | None
    comment: str | None
    last_heard: datetime
    points: deque = field(default_factory=lambda: deque(maxlen=TRAIL_MAX_POINTS))


class EventPositionStore:
    def __init__(self):
        self._participants: dict[str, Station] = {}
        self._others: OrderedDict[str, Station] = OrderedDict()
        self._seq = 0

    @property
    def latest_pos_seq(self) -> int:
        return self._seq

    def add_point(
        self,
        station_id: str,
        lat: float,
        lon: float,
        *,
        kind: str,
        callsign: str | None = None,
        symbol: str | None = None,
        comment: str | None = None,
        ts: datetime | None = None,
    ) -> int:
        ts = ts or _utcnow()
        self._seq += 1
        pool = self._participants if kind == "participant" else self._others
        station = pool.get(station_id)
        if station is None:
            station = Station(
                station_id=station_id, kind=kind, callsign=callsign,
                symbol=symbol, comment=comment, last_heard=ts,
            )
            pool[station_id] = station
        station.last_heard = ts
        if symbol is not None:
            station.symbol = symbol
        if comment is not None:
            station.comment = comment
        station.points.append(TrackPoint(lat=lat, lon=lon, ts=ts, pos_seq=self._seq))

        if kind == "other":
            self._others.move_to_end(station_id)
            while len(self._others) > OTHER_STATIONS_CAP:
                self._others.popitem(last=False)
        return self._seq

    def drop_others(self) -> None:
        self._others.clear()

    def snapshot(self, since: int = 0) -> dict:
        stations = []
        for station in list(self._participants.values()) + list(self._others.values()):
            stations.append({
                "station_id": station.station_id,
                "kind": station.kind,
                "callsign": station.callsign,
                "symbol": station.symbol,
                "comment": station.comment,
                "last_heard": station.last_heard.isoformat(),
                "points": [
                    {"lat": p.lat, "lon": p.lon, "ts": p.ts.isoformat(), "pos_seq": p.pos_seq}
                    for p in station.points
                    if p.pos_seq > since
                ],
            })
        return {"stations": stations, "latest_pos_seq": self._seq}
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_aprs_store.py -q` — expected all pass

- [ ] **Step 5: Lint + commit**

```bash
nix-shell --run "ruff check"
git add backend/integrations/aprs/store.py tests/test_aprs_store.py
git commit -m "feat(aprs): in-memory per-event position store with pos_seq cursor"
```

---

### Task 4: APRS-IS client task + manager (receive side)

**Files:**
- Create: `backend/integrations/aprs/manager.py`
- Create: `backend/integrations/aprs/client.py`
- Test: `tests/test_aprs_client.py`

**Interfaces:**
- Consumes: Tasks 2–3 (`protocol`, `EventPositionStore`); `get_net_config`; events models.
- Produces (in `backend.integrations.aprs.manager`):
  - `class AprsClientState`: fields `event_id`, `session_factory`, `store: EventPositionStore`, `status: str` (`"connected" | "reconnecting" | "error" | "disabled"`), `status_detail: str`, `running: bool`, `dirty: asyncio.Event`, `participant_calls: set[str]`, `other_enabled: bool`, `announced: dict[str, tuple[float, float]]` (object name → lat/lon), `objects_by_post: dict[int, str]`, `task`
  - `get_state(event_id: int) -> AprsClientState | None`
  - `aprs_config(db, net_id: int) -> dict | None` — `{"callsign", "server", "port"}` or None when disabled/unconfigured
  - `ensure_started(session_factory, event_id: int) -> None` — idempotent; no-op without a running loop or when APRS disabled
  - `stop(event_id: int) -> None` — flags the client to kill objects and exit
  - `nudge(event_id: int) -> None` — re-check filter/beacons
  - `start_for_active_events(session_factory) -> None` (boot)
  - `async shutdown_all() -> None`
- Task 5 extends `client.py` with beaconing; this task creates `refresh_config()` and the RX loop with a `_send_objects` stub that Task 5 fills (stub sends nothing but exists and is called, so Task 5 is additive).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_aprs_client.py
import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.integrations.aprs.client as aprs_client
from backend.db.base import Base
from backend.integrations.aprs import manager
from backend.modules.events.models import Event, EventType
from backend.modules.events.service import check_in, create_event
from backend.modules.nets.config_service import set_net_config_bulk
from tests.conftest import make_test_net


class FakeAprsServer:
    """Minimal in-process APRS-IS stand-in: greets, records every line the
    client sends, and lets tests push packet lines to the client."""

    def __init__(self):
        self.received: list[str] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.server = None
        self.port = None
        self.connections = 0

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        self.connections += 1
        self.writers.append(writer)
        writer.write(b"# aprsc test server\r\n")
        await writer.drain()
        while True:
            line = await reader.readline()
            if not line:
                break
            self.received.append(line.decode().strip())

    async def send(self, line: str):
        for w in self.writers:
            w.write((line + "\r\n").encode())
            await w.drain()

    async def stop(self):
        for w in self.writers:
            w.close()
        self.server.close()
        await self.server.wait_closed()


@pytest.fixture
def db_factory():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def aprs_event(db_factory, monkeypatch):
    """An active event on an APRS-enabled net with one checked-in participant."""
    monkeypatch.setattr(
        "backend.modules.events.service.lookup_callsign", lambda db, cs: None
    )
    with db_factory() as db:
        net = make_test_net(db)
        set_net_config_bulk(db, net.id, {
            "aprs.enabled": "true",
            "aprs.callsign": "W0NE",
        })
        event = create_event(
            db, net_id=net.id, name="Tornado", event_type=EventType.EMERGENCY,
            created_by="W0NE", activate=True,
        )
        check_in(db, event.id, callsign="KE0XYZ", actor="W0NC")
        return event.id


@pytest.fixture(autouse=True)
def _clean_states():
    manager._states.clear()
    yield
    manager._states.clear()


async def _wait_for(predicate, timeout=5.0):
    """Poll until predicate() is truthy or fail the test."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met within timeout")


class TestConfig:
    def test_aprs_config_reads_net_config(self, db_factory):
        with db_factory() as db:
            net = make_test_net(db)
            assert manager.aprs_config(db, net.id) is None  # disabled by default
            set_net_config_bulk(db, net.id, {"aprs.enabled": "true", "aprs.callsign": "W0NE"})
            cfg = manager.aprs_config(db, net.id)
            assert cfg == {"callsign": "W0NE", "server": "rotate.aprs2.net", "port": 14580}

    def test_missing_callsign_means_disabled(self, db_factory):
        with db_factory() as db:
            net = make_test_net(db)
            set_net_config_bulk(db, net.id, {"aprs.enabled": "true"})
            assert manager.aprs_config(db, net.id) is None


class TestClientLoop:
    async def test_login_filter_and_position_ingest(self, db_factory, aprs_event):
        server = FakeAprsServer()
        await server.start()
        try:
            with db_factory() as db:
                net_id = db.get(Event, aprs_event).net_id
                set_net_config_bulk(db, net_id, {"aprs.server": "127.0.0.1", "aprs.port": str(server.port)})

            manager.ensure_started(db_factory, aprs_event)
            state = manager.get_state(aprs_event)
            assert state is not None

            await _wait_for(lambda: state.status == "connected")
            await _wait_for(lambda: len(server.received) >= 1)
            login = server.received[0]
            assert login.startswith("user W0NE pass ")
            assert "b/KE0XYZ*" in login

            # Participant position (uncompressed) flows into the store
            await server.send("KE0XYZ-9>APRS,TCPIP*:!3903.75N/09434.72W>Mobile")
            await _wait_for(lambda: state.store.latest_pos_seq >= 1)
            snap = state.store.snapshot()
            assert snap["stations"][0]["station_id"] == "KE0XYZ-9"
            assert snap["stations"][0]["kind"] == "participant"
            assert snap["stations"][0]["callsign"] == "KE0XYZ"

            # Unknown station with other-layer off → dropped
            await server.send("STRANGR-1>APRS,TCPIP*:!3900.00N/09400.00W>hi")
            await asyncio.sleep(0.2)
            assert all(s["station_id"] != "STRANGR-1" for s in state.store.snapshot()["stations"])

            # Garbage line must not kill the loop
            await server.send("not an aprs packet at all")
            await server.send("KE0XYZ-9>APRS,TCPIP*:!3904.00N/09435.00W>still here")
            await _wait_for(lambda: state.store.latest_pos_seq >= 2)
        finally:
            manager.stop(aprs_event)
            state = manager.get_state(aprs_event)
            if state and state.task:
                await asyncio.wait_for(state.task, timeout=5)
            await server.stop()

    async def test_nudge_resends_filter_after_new_checkin(self, db_factory, aprs_event, monkeypatch):
        monkeypatch.setattr(
            "backend.modules.events.service.lookup_callsign", lambda db, cs: None
        )
        server = FakeAprsServer()
        await server.start()
        try:
            with db_factory() as db:
                net_id = db.get(Event, aprs_event).net_id
                set_net_config_bulk(db, net_id, {"aprs.server": "127.0.0.1", "aprs.port": str(server.port)})

            manager.ensure_started(db_factory, aprs_event)
            state = manager.get_state(aprs_event)
            await _wait_for(lambda: state.status == "connected")

            with db_factory() as db:
                check_in(db, aprs_event, callsign="N0DES", actor="W0NC")
            manager.nudge(aprs_event)

            await _wait_for(lambda: any(
                line.startswith("#filter") and "b/N0DES*" in line for line in server.received
            ))
        finally:
            manager.stop(aprs_event)
            state = manager.get_state(aprs_event)
            if state and state.task:
                await asyncio.wait_for(state.task, timeout=5)
            await server.stop()

    async def test_reconnect_after_drop(self, db_factory, aprs_event, monkeypatch):
        monkeypatch.setattr(aprs_client, "RECONNECT_BASE_S", 0.05)
        server = FakeAprsServer()
        await server.start()
        try:
            with db_factory() as db:
                net_id = db.get(Event, aprs_event).net_id
                set_net_config_bulk(db, net_id, {"aprs.server": "127.0.0.1", "aprs.port": str(server.port)})

            manager.ensure_started(db_factory, aprs_event)
            state = manager.get_state(aprs_event)
            await _wait_for(lambda: state.status == "connected")

            # Drop the connection server-side; client must reconnect
            for w in server.writers:
                w.close()
            server.writers.clear()
            await _wait_for(lambda: server.connections >= 2)
            await _wait_for(lambda: state.status == "connected")
        finally:
            manager.stop(aprs_event)
            state = manager.get_state(aprs_event)
            if state and state.task:
                await asyncio.wait_for(state.task, timeout=5)
            await server.stop()

    async def test_ensure_started_noop_when_aprs_disabled(self, db_factory, monkeypatch):
        monkeypatch.setattr(
            "backend.modules.events.service.lookup_callsign", lambda db, cs: None
        )
        with db_factory() as db:
            net = make_test_net(db, slug="noaprs")
            event = create_event(
                db, net_id=net.id, name="E", event_type=EventType.EMERGENCY,
                created_by="W0NE", activate=True,
            )
        manager.ensure_started(db_factory, event.id)
        assert manager.get_state(event.id) is None
```

- [ ] **Step 2: Run tests, verify failure**

Run: `.venv/bin/pytest tests/test_aprs_client.py -q`
Expected: FAIL — import errors

- [ ] **Step 3: Implement manager**

```python
# backend/integrations/aprs/manager.py
"""Registry and lifecycle for per-event APRS-IS client tasks.

ensure_started/stop/nudge are safe to call from sync code running inside the
FastAPI event loop (route handlers); without a running loop they no-op, so
tests and CLI paths never accidentally open sockets.
"""
import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.integrations.aprs.store import EventPositionStore
from backend.modules.nets.config_service import get_net_config

logger = logging.getLogger(__name__)


@dataclass
class AprsClientState:
    event_id: int
    session_factory: object
    store: EventPositionStore = field(default_factory=EventPositionStore)
    status: str = "reconnecting"  # connected | reconnecting | error | disabled
    status_detail: str = ""
    running: bool = False
    dirty: asyncio.Event = field(default_factory=asyncio.Event)
    participant_calls: set = field(default_factory=set)
    other_enabled: bool = False
    announced: dict = field(default_factory=dict)  # object name -> (lat, lon)
    objects_by_post: dict = field(default_factory=dict)  # post_id -> object name
    task: asyncio.Task | None = None


_states: dict[int, AprsClientState] = {}


def get_state(event_id: int) -> AprsClientState | None:
    return _states.get(event_id)


def aprs_config(db: Session, net_id: int) -> dict | None:
    """The net's APRS connection settings, or None when APRS is off/unusable."""
    if get_net_config(db, net_id, "aprs.enabled", "false") != "true":
        return None
    callsign = (get_net_config(db, net_id, "aprs.callsign", "") or "").strip()
    if not callsign:
        return None
    server = get_net_config(db, net_id, "aprs.server", "rotate.aprs2.net") or "rotate.aprs2.net"
    try:
        port = int(get_net_config(db, net_id, "aprs.port", "14580"))
    except (TypeError, ValueError):
        port = 14580
    return {"callsign": callsign, "server": server, "port": port}


def ensure_started(session_factory, event_id: int) -> None:
    existing = _states.get(event_id)
    if existing is not None and existing.running:
        return
    from backend.modules.events.models import Event, EventStatus

    with session_factory() as db:
        event = db.get(Event, event_id)
        if event is None or event.status != EventStatus.ACTIVE:
            return
        config = aprs_config(db, event.net_id)
    if config is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (sync tests, CLI) — APRS simply doesn't run
    from backend.integrations.aprs.client import run_event_client

    state = AprsClientState(event_id=event_id, session_factory=session_factory)
    state.running = True
    state.task = loop.create_task(run_event_client(state, config))
    _states[event_id] = state
    logger.info("APRS client started for event %s", event_id)


def stop(event_id: int) -> None:
    state = _states.get(event_id)
    if state is None:
        return
    state.running = False
    state.dirty.set()  # wake the loop so it can kill objects and exit


def nudge(event_id: int) -> None:
    state = _states.get(event_id)
    if state is not None:
        state.dirty.set()


def start_for_active_events(session_factory) -> None:
    """Boot-time: resume clients for events that were active at shutdown."""
    from backend.modules.events.models import Event, EventStatus

    with session_factory() as db:
        active_ids = [e.id for e in db.query(Event).filter(Event.status == EventStatus.ACTIVE).all()]
    for event_id in active_ids:
        ensure_started(session_factory, event_id)


async def shutdown_all() -> None:
    for state in list(_states.values()):
        state.running = False
        state.dirty.set()
    for state in list(_states.values()):
        if state.task is not None:
            try:
                await asyncio.wait_for(state.task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                state.task.cancel()
    _states.clear()
```

- [ ] **Step 4: Implement client (RX loop)**

```python
# backend/integrations/aprs/client.py
"""The per-event APRS-IS client coroutine: connect, verified login, filtered
receive, live filter updates, and (Task 5) object beaconing.

DB reads are short synchronous queries executed in the loop thread — the same
trade-off the rest of the codebase makes (scanner)."""
import asyncio
import logging

import aprslib

from backend.integrations.aprs.protocol import (
    BEACON_INTERVAL_S,
    build_filter,
    filter_command,
    login_line,
)

logger = logging.getLogger(__name__)

RECONNECT_BASE_S = 5
RECONNECT_MAX_S = 300
READ_TIMEOUT_S = 1.0


def refresh_config(state) -> str:
    """Re-read participants + event APRS settings; returns the filter spec.
    Also updates state's classification inputs (participant set, other flag)."""
    from backend.modules.events.models import Event, EventParticipant

    with state.session_factory() as db:
        event = db.get(Event, state.event_id)
        participants = (
            db.query(EventParticipant)
            .filter(EventParticipant.event_id == state.event_id)
            .all()
        )
    state.participant_calls = {p.callsign.split("-")[0].upper() for p in participants}
    state.other_enabled = bool(event and event.aprs_other_stations)
    kwargs = {}
    if state.other_enabled and event.aprs_range_lat is not None:
        kwargs = {
            "range_lat": event.aprs_range_lat,
            "range_lon": event.aprs_range_lon,
            "range_km": event.aprs_range_km,
        }
    return build_filter(state.participant_calls, **kwargs)


def handle_line(state, raw: bytes) -> None:
    """Parse one APRS-IS line into the store. Never raises."""
    try:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text or text.startswith("#"):
            return
        try:
            packet = aprslib.parse(text)
        except (aprslib.ParseError, aprslib.UnknownFormat):
            return
        lat = packet.get("latitude")
        lon = packet.get("longitude")
        src = packet.get("from", "")
        if lat is None or lon is None or not src:
            return
        base = src.split("-")[0].upper()
        symbol = None
        if packet.get("symbol_table") and packet.get("symbol"):
            symbol = f"{packet['symbol_table']}{packet['symbol']}"
        comment = packet.get("comment") or None
        if base in state.participant_calls:
            state.store.add_point(
                src.upper(), lat, lon, kind="participant", callsign=base,
                symbol=symbol, comment=comment,
            )
        elif state.other_enabled:
            state.store.add_point(src.upper(), lat, lon, kind="other", symbol=symbol, comment=comment)
    except Exception:  # noqa: BLE001 — one bad packet must never kill the loop
        logger.debug("Unhandled error for APRS line %r", raw, exc_info=True)


async def _send(writer, line: str) -> None:
    writer.write((line + "\r\n").encode())
    await writer.drain()


async def _send_objects(state, writer, config, *, force: bool = False) -> None:
    """Object beaconing — implemented in the beaconer task (Task 5)."""
    return None


async def _kill_all_objects(state, writer, config) -> None:
    """Kill packets on shutdown — implemented in the beaconer task (Task 5)."""
    return None


async def run_event_client(state, config) -> None:
    loop = asyncio.get_running_loop()
    backoff = RECONNECT_BASE_S
    try:
        while state.running:
            writer = None
            try:
                reader, writer = await asyncio.open_connection(config["server"], config["port"])
                await reader.readline()  # server banner
                current_spec = refresh_config(state)
                await _send(writer, login_line(config["callsign"], current_spec))
                state.status = "connected"
                state.status_detail = ""
                backoff = RECONNECT_BASE_S
                state.announced.clear()
                state.objects_by_post.clear()
                await _send_objects(state, writer, config, force=True)
                next_beacon = loop.time() + BEACON_INTERVAL_S

                while state.running:
                    try:
                        line = await asyncio.wait_for(reader.readline(), timeout=READ_TIMEOUT_S)
                        if line == b"":
                            raise ConnectionError("connection closed by server")
                        handle_line(state, line)
                    except asyncio.TimeoutError:
                        pass

                    if state.dirty.is_set():
                        state.dirty.clear()
                        if not state.running:
                            break
                        new_spec = refresh_config(state)
                        if new_spec != current_spec:
                            current_spec = new_spec
                            await _send(writer, filter_command(new_spec or "b/NOCALL"))
                        if not state.other_enabled:
                            state.store.drop_others()
                        await _send_objects(state, writer, config)

                    if loop.time() >= next_beacon:
                        await _send_objects(state, writer, config, force=True)
                        next_beacon = loop.time() + BEACON_INTERVAL_S

                # Clean stop: remove our objects from the network first.
                await _kill_all_objects(state, writer, config)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if not state.running:
                    break
                state.status = "reconnecting"
                state.status_detail = str(exc)
                logger.warning("APRS client for event %s: %s (retry in %ss)", state.event_id, exc, backoff)
                try:
                    await asyncio.wait_for(state.dirty.wait(), timeout=backoff)
                    state.dirty.clear()
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, RECONNECT_MAX_S)
            finally:
                if writer is not None:
                    writer.close()
    finally:
        state.status = "disabled"
        logger.info("APRS client stopped for event %s", state.event_id)
```

- [ ] **Step 5: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_aprs_client.py -q` — expected all pass. These are timing-sensitive tests; if a `_wait_for` flakes, raise its timeout rather than adding sleeps.

- [ ] **Step 6: Full suite + lint + commit**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add backend/integrations/aprs/manager.py backend/integrations/aprs/client.py tests/test_aprs_client.py
git commit -m "feat(aprs): per-event APRS-IS client task with live filters and reconnect"
```

---

### Task 5: Object beaconer (transmit side)

**Files:**
- Create: `backend/integrations/aprs/beacon.py`
- Modify: `backend/integrations/aprs/client.py` (replace the `_send_objects` / `_kill_all_objects` stubs with delegation to beacon.py)
- Test: `tests/test_aprs_beacon.py`

**Interfaces:**
- Consumes: Task 2 `object_name`/`object_packet`; Task 4 state (`announced`, `objects_by_post`, `session_factory`, `event_id`).
- Produces (in `backend.integrations.aprs.beacon`):
  - `desired_objects(state) -> dict[str, tuple[float, float, int]]` — object name → (lat, lon, post_id); empty when beaconing off or event not active
  - `async send_objects(state, writer, config, *, force: bool = False) -> None` — diffs desired vs `state.announced`: sends objects for new/moved, kill+new for renamed, kills for removed; `force=True` re-sends the whole desired set
  - `async kill_all(state, writer, config) -> None`
- `client.py`'s `_send_objects` / `_kill_all_objects` become one-line delegations to these.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_aprs_beacon.py
import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.integrations.aprs import manager
from backend.integrations.aprs.beacon import desired_objects, kill_all, send_objects
from backend.modules.events.models import EventType
from backend.modules.events.service import close_event, create_event, create_post, update_event
from backend.modules.nets.config_service import set_net_config_bulk
from tests.conftest import make_test_net

CONFIG = {"callsign": "W0NE", "server": "x", "port": 1}


class CollectingWriter:
    """Duck-typed StreamWriter capturing written lines."""

    def __init__(self):
        self.lines: list[str] = []

    def write(self, data: bytes):
        self.lines.append(data.decode().strip())

    async def drain(self):
        pass

    def close(self):
        pass


@pytest.fixture
def db_factory():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def event_with_posts(db_factory):
    with db_factory() as db:
        net = make_test_net(db)
        set_net_config_bulk(db, net.id, {"aprs.enabled": "true", "aprs.callsign": "W0NE"})
        event = create_event(
            db, net_id=net.id, name="Marathon", event_type=EventType.PUBLIC_SERVICE,
            created_by="W0NE", activate=True,
        )
        create_post(db, event.id, name="Rest Stop 3", lat=39.0625, lon=-94.5786)
        create_post(db, event.id, name="No Coords Post")  # must not beacon
        update_event(db, event.id, aprs_beacon_posts=True)
        return event.id


def make_state(db_factory, event_id):
    return manager.AprsClientState(event_id=event_id, session_factory=db_factory)


class TestDesired:
    def test_only_posts_with_coords(self, db_factory, event_with_posts):
        state = make_state(db_factory, event_with_posts)
        desired = desired_objects(state)
        assert list(desired.keys()) == ["RESTSTOP3"]
        name, (lat, lon, post_id) = next(iter(desired.items()))
        assert (lat, lon) == (39.0625, -94.5786)

    def test_empty_when_toggle_off(self, db_factory, event_with_posts):
        with db_factory() as db:
            update_event(db, event_with_posts, aprs_beacon_posts=False)
        state = make_state(db_factory, event_with_posts)
        assert desired_objects(state) == {}

    def test_empty_when_event_closed(self, db_factory, event_with_posts):
        with db_factory() as db:
            close_event(db, event_with_posts, actor="W0NE")
        state = make_state(db_factory, event_with_posts)
        assert desired_objects(state) == {}


class TestSendObjects:
    async def test_initial_beacon_and_bookkeeping(self, db_factory, event_with_posts):
        state = make_state(db_factory, event_with_posts)
        writer = CollectingWriter()
        await send_objects(state, writer, CONFIG, force=True)
        assert len(writer.lines) == 1
        assert writer.lines[0].startswith("W0NE>APZSNC,TCPIP*:;RESTSTOP3*")
        assert "SkyNetControl event: Marathon" in writer.lines[0]
        assert state.announced == {"RESTSTOP3": (39.0625, -94.5786)}
        assert list(state.objects_by_post.values()) == ["RESTSTOP3"]

    async def test_no_resend_without_force_or_change(self, db_factory, event_with_posts):
        state = make_state(db_factory, event_with_posts)
        writer = CollectingWriter()
        await send_objects(state, writer, CONFIG, force=True)
        writer.lines.clear()
        await send_objects(state, writer, CONFIG)  # nothing changed
        assert writer.lines == []

    async def test_removed_post_gets_kill(self, db_factory, event_with_posts):
        state = make_state(db_factory, event_with_posts)
        writer = CollectingWriter()
        await send_objects(state, writer, CONFIG, force=True)
        with db_factory() as db:
            update_event(db, event_with_posts, aprs_beacon_posts=False)
        writer.lines.clear()
        await send_objects(state, writer, CONFIG)
        assert len(writer.lines) == 1
        assert ";RESTSTOP3_" in writer.lines[0]
        assert state.announced == {}

    async def test_kill_all(self, db_factory, event_with_posts):
        state = make_state(db_factory, event_with_posts)
        writer = CollectingWriter()
        await send_objects(state, writer, CONFIG, force=True)
        writer.lines.clear()
        await kill_all(state, writer, CONFIG)
        assert any(";RESTSTOP3_" in line for line in writer.lines)
        assert state.announced == {}
```

- [ ] **Step 2: Run tests, verify failure**

Run: `.venv/bin/pytest tests/test_aprs_beacon.py -q`
Expected: FAIL — `ModuleNotFoundError` for beacon module

- [ ] **Step 3: Implement beacon.py**

```python
# backend/integrations/aprs/beacon.py
"""Object beaconing: event posts with coordinates go out as APRS objects
under the net's callsign — only while the event is active AND the per-event
aprs_beacon_posts toggle is on. Kills are sent when objects disappear."""
import logging

from backend.integrations.aprs.protocol import object_name, object_packet

logger = logging.getLogger(__name__)


def desired_objects(state) -> dict:
    """name -> (lat, lon, post_id) for every post that should be on the air."""
    from backend.modules.events.models import Event, EventPost, EventStatus

    with state.session_factory() as db:
        event = db.get(Event, state.event_id)
        if event is None or event.status != EventStatus.ACTIVE or not event.aprs_beacon_posts:
            return {}
        posts = (
            db.query(EventPost)
            .filter(EventPost.event_id == state.event_id)
            .order_by(EventPost.id)
            .all()
        )
        desired: dict = {}
        # Deterministic naming: posts ordered by id, names uniquified in that
        # order — the same post set always produces the same object names.
        for post in posts:
            if post.lat is None or post.lon is None:
                continue
            name = object_name(post.name, set(desired.keys()))
            desired[name] = (post.lat, post.lon, post.id)
        comment_event_name = event.name
    state._beacon_comment = f"SkyNetControl event: {comment_event_name}"
    return desired


async def _send(writer, line: str) -> None:
    writer.write((line + "\r\n").encode())
    await writer.drain()


async def send_objects(state, writer, config, *, force: bool = False) -> None:
    desired = desired_objects(state)
    comment = getattr(state, "_beacon_comment", "SkyNetControl event")

    # Kills first: anything announced that's no longer desired.
    for name in [n for n in state.announced if n not in desired]:
        lat, lon = state.announced[name]
        await _send(writer, object_packet(config["callsign"], name, lat, lon, "", kill=True))
        del state.announced[name]

    # Then live objects: new, moved, or everything when forced.
    for name, (lat, lon, post_id) in desired.items():
        if force or state.announced.get(name) != (lat, lon):
            await _send(writer, object_packet(config["callsign"], name, lat, lon, comment))
        state.announced[name] = (lat, lon)

    state.objects_by_post = {post_id: name for name, (_lat, _lon, post_id) in desired.items()}


async def kill_all(state, writer, config) -> None:
    for name, (lat, lon) in list(state.announced.items()):
        try:
            await _send(writer, object_packet(config["callsign"], name, lat, lon, "", kill=True))
        except Exception:  # noqa: BLE001 — orphaned objects age out on other clients
            logger.warning("Failed to send kill for object %s (event %s)", name, state.event_id)
    state.announced.clear()
    state.objects_by_post.clear()
```

- [ ] **Step 4: Wire into client.py**

Replace the two stubs in `backend/integrations/aprs/client.py`:

```python
async def _send_objects(state, writer, config, *, force: bool = False) -> None:
    from backend.integrations.aprs.beacon import send_objects

    await send_objects(state, writer, config, force=force)


async def _kill_all_objects(state, writer, config) -> None:
    from backend.integrations.aprs.beacon import kill_all

    await kill_all(state, writer, config)
```

- [ ] **Step 5: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_aprs_beacon.py tests/test_aprs_client.py -q` — expected all pass (client tests re-run because the stub was replaced; the aprs_event fixture has no posts and beaconing off, so no object lines interfere).

- [ ] **Step 6: Full suite + lint + commit**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add backend/integrations/aprs/beacon.py backend/integrations/aprs/client.py tests/test_aprs_beacon.py
git commit -m "feat(aprs): object beaconing with diff-based kills"
```

---

### Task 6: Positions endpoint + lifecycle wiring

**Files:**
- Modify: `backend/modules/events/routes.py` (positions route + manager hooks in lifecycle/check-in/post/patch routes)
- Modify: `backend/app.py` (lifespan boot/shutdown)
- Test: `tests/test_aprs_routes.py`

**Interfaces:**
- Consumes: Task 4 manager API; existing route helpers (`_get_event_or_404`, `require_net_role`).
- Produces:
  - `GET /api/nets/{slug}/events/{event_id}/positions?since=N` (viewer) → `{stations, latest_pos_seq, aprs_status, aprs_status_detail, objects: [{post_id, name}]}` — `aprs_status: "disabled"` with empty payload when no client is running
  - Lifecycle hooks: activate/reopen → `ensure_started`; close → `stop`; check-in, participant PATCH, post create/update/delete, event PATCH → `nudge`
  - Boot/shutdown: `start_for_active_events` in lifespan startup, `await shutdown_all()` on shutdown

- [ ] **Step 1: Write failing tests**

```python
# tests/test_aprs_routes.py
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.integrations.aprs import manager
from backend.integrations.aprs.store import EventPositionStore
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


@pytest.fixture(autouse=True)
def _clean_states():
    manager._states.clear()
    yield
    manager._states.clear()


@pytest.fixture
async def active_event(nc_client):
    resp = await nc_client.post(BASE, json={"name": "E", "event_type": "emergency", "activate": True})
    return resp.json()["id"]


class TestPositionsRoute:
    async def test_disabled_when_no_client(self, viewer_client, active_event):
        resp = await viewer_client.get(f"{BASE}/{active_event}/positions")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "stations": [], "latest_pos_seq": 0,
            "aprs_status": "disabled", "aprs_status_detail": "", "objects": [],
        }

    async def test_snapshot_from_running_state(self, viewer_client, active_event, db_setup):
        state = manager.AprsClientState(event_id=active_event, session_factory=db_setup["factory"])
        state.status = "connected"
        state.store.add_point("KE0XYZ-9", 39.0, -94.0, kind="participant", callsign="KE0XYZ")
        state.objects_by_post = {1: "RESTSTOP3"}
        manager._states[active_event] = state

        resp = await viewer_client.get(f"{BASE}/{active_event}/positions", params={"since": 0})
        body = resp.json()
        assert body["aprs_status"] == "connected"
        assert body["latest_pos_seq"] == 1
        assert body["stations"][0]["station_id"] == "KE0XYZ-9"
        assert body["objects"] == [{"post_id": 1, "name": "RESTSTOP3"}]

        # Cursor: no new points, but roster still complete
        resp = await viewer_client.get(f"{BASE}/{active_event}/positions", params={"since": 1})
        body = resp.json()
        assert body["stations"][0]["points"] == []

    async def test_missing_event_404(self, viewer_client):
        assert (await viewer_client.get(f"{BASE}/9999/positions")).status_code == 404

    async def test_anonymous_401(self, app, active_event):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as anon:
            assert (await anon.get(f"{BASE}/{active_event}/positions")).status_code == 401


class TestLifecycleHooks:
    async def test_activate_calls_ensure_started(self, nc_client, monkeypatch):
        calls = []
        monkeypatch.setattr(manager, "ensure_started", lambda sf, eid: calls.append(("start", eid)))
        resp = await nc_client.post(BASE, json={"name": "E", "event_type": "emergency", "activate": True})
        event_id = resp.json()["id"]
        assert ("start", event_id) in calls

    async def test_close_calls_stop_and_reopen_restarts(self, nc_client, monkeypatch):
        calls = []
        monkeypatch.setattr(manager, "ensure_started", lambda sf, eid: calls.append(("start", eid)))
        monkeypatch.setattr(manager, "stop", lambda eid: calls.append(("stop", eid)))
        event_id = (await nc_client.post(
            BASE, json={"name": "E", "event_type": "emergency", "activate": True}
        )).json()["id"]
        await nc_client.post(f"{BASE}/{event_id}/close")
        assert ("stop", event_id) in calls
        await nc_client.post(f"{BASE}/{event_id}/reopen")
        assert calls.count(("start", event_id)) == 2

    async def test_checkin_and_posts_and_patch_nudge(self, nc_client, monkeypatch):
        nudges = []
        monkeypatch.setattr(manager, "ensure_started", lambda sf, eid: None)
        monkeypatch.setattr(manager, "nudge", lambda eid: nudges.append(eid))
        event_id = (await nc_client.post(
            BASE, json={"name": "E", "event_type": "emergency", "activate": True}
        )).json()["id"]
        await nc_client.post(f"{BASE}/{event_id}/participants", json={"callsign": "KE0XYZ"})
        post_id = (await nc_client.post(
            f"{BASE}/{event_id}/posts", json={"name": "EOC", "lat": 39.0, "lon": -94.0}
        )).json()["id"]
        await nc_client.patch(f"{BASE}/{event_id}/posts/{post_id}", json={"lat": 39.1})
        await nc_client.patch(f"{BASE}/{event_id}", json={"aprs_beacon_posts": True})
        await nc_client.delete(f"{BASE}/{event_id}/posts/{post_id}")
        assert nudges.count(event_id) >= 5
```

- [ ] **Step 2: Run tests, verify failure**

Run: `.venv/bin/pytest tests/test_aprs_routes.py -q`
Expected: FAIL — 404 on positions route; hook assertions fail

- [ ] **Step 3: Add the positions route**

In `backend/modules/events/routes.py`, add `Request` to the fastapi import if absent, and append:

```python
# --- APRS positions (sub-project 2) ---


@events_router.get("/{event_id}/positions")
async def positions_route(
    event_id: int,
    since: int = Query(default=0, ge=0),
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    from backend.integrations.aprs import manager as aprs_manager

    state = aprs_manager.get_state(event_id)
    if state is None:
        return {
            "stations": [], "latest_pos_seq": 0,
            "aprs_status": "disabled", "aprs_status_detail": "", "objects": [],
        }
    snapshot = state.store.snapshot(since)
    snapshot["aprs_status"] = state.status
    snapshot["aprs_status_detail"] = state.status_detail
    snapshot["objects"] = [
        {"post_id": post_id, "name": name} for post_id, name in sorted(state.objects_by_post.items())
    ]
    return snapshot
```

- [ ] **Step 4: Wire lifecycle hooks**

At the top of `backend/modules/events/routes.py` add:

```python
from fastapi import Request
from backend.integrations.aprs import manager as aprs_manager
```

Then add hooks (each is 1–2 lines at the end of the existing handler, before its `return`; each handler that needs `request` gains a `request: Request` parameter):

- `create_event_route`: after the service call — `if event.status == EventStatus.ACTIVE: aprs_manager.ensure_started(request.app.state.session_factory, event.id)` (import `EventStatus` from models in the existing import).
- `activate_event_route` and `reopen_event_route`: `aprs_manager.ensure_started(request.app.state.session_factory, event_id)`
- `close_event_route`: `aprs_manager.stop(event_id)`
- `update_event_route`, `check_in_route`, `update_participant_route`, `create_post_route`, `update_post_route`, `delete_post_route`: `aprs_manager.nudge(event_id)` (after the successful service call; for `update_event_route` also call `aprs_manager.ensure_started(request.app.state.session_factory, event_id)` — turning APRS settings on mid-event must start a client that config previously kept off).

- [ ] **Step 5: Wire lifespan in app.py**

In `backend/app.py`'s `lifespan`, after the scanner task setup (inside its own try/except so APRS can never block boot):

```python
        try:
            from backend.integrations.aprs.manager import start_for_active_events

            start_for_active_events(session_factory)
        except Exception:
            pass
```

and in the shutdown section (after the scanner cancellation):

```python
        try:
            from backend.integrations.aprs.manager import shutdown_all

            await shutdown_all()
        except Exception:
            pass
```

- [ ] **Step 6: Run tests, full suite, lint, commit**

Run: `.venv/bin/pytest tests/test_aprs_routes.py -q` then `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add backend/modules/events/routes.py backend/app.py tests/test_aprs_routes.py
git commit -m "feat(aprs): positions endpoint and event lifecycle wiring"
```

---

### Task 7: Frontend types, API, positions hook, net settings

**Files:**
- Modify: `frontend/src/types/index.ts` (append)
- Modify: `frontend/src/api/events.ts` (append + extend)
- Create: `frontend/src/hooks/useEventPositions.ts`
- Modify: `frontend/src/pages/NetSettingsPage.tsx` (APRS config section)

**Interfaces:**
- Consumes: Task 6 payload shapes; existing `apiFetch`, `SettingsSection`/`ConfigField` patterns.
- Produces:
  - Types: `StationKind`, `EventTrackPoint {lat, lon, ts, pos_seq}`, `EventStation {station_id, kind, callsign, symbol, comment, last_heard, points}`, `BeaconedObject {post_id, name}`, `EventPositions {stations, latest_pos_seq, aprs_status, aprs_status_detail, objects}`; `NetEvent` gains the five `aprs_*` fields.
  - `fetchEventPositions(eventId: number, since: number, netSlug: string): Promise<EventPositions>`; `updateEvent`'s body type widened to include the `aprs_*` fields.
  - `useEventPositions(netSlug: string, eventId: number, enabled: boolean)` → `{ stations: Map<string, EventStation>, aprsStatus: string, aprsStatusDetail: string, objects: BeaconedObject[], refresh: () => Promise<void> }` — polls every 5 s while `enabled` and tab visible; replaces roster, accumulates points per station, dedupes by `pos_seq`.

- [ ] **Step 1: Append types**

In `frontend/src/types/index.ts`, extend `NetEvent` with:

```typescript
  aprs_other_stations: boolean;
  aprs_range_lat: number | null;
  aprs_range_lon: number | null;
  aprs_range_km: number | null;
  aprs_beacon_posts: boolean;
```

and append:

```typescript
// --- APRS live positions ---

export type StationKind = "participant" | "other";

export interface EventTrackPoint {
  lat: number;
  lon: number;
  ts: string;
  pos_seq: number;
}

export interface EventStation {
  station_id: string;
  kind: StationKind;
  callsign: string | null;
  symbol: string | null;
  comment: string | null;
  last_heard: string;
  points: EventTrackPoint[];
}

export interface BeaconedObject {
  post_id: number;
  name: string;
}

export interface EventPositions {
  stations: EventStation[];
  latest_pos_seq: number;
  aprs_status: string;
  aprs_status_detail: string;
  objects: BeaconedObject[];
}
```

- [ ] **Step 2: Extend api/events.ts**

Widen `updateEvent`'s body parameter type:

```typescript
export async function updateEvent(
  id: number,
  body: Partial<
    Pick<
      NetEvent,
      | "name"
      | "description"
      | "scheduled_start"
      | "aprs_other_stations"
      | "aprs_range_lat"
      | "aprs_range_lon"
      | "aprs_range_km"
      | "aprs_beacon_posts"
    >
  >,
  netSlug: string,
): Promise<NetEvent> {
```

and append (with `EventPositions` added to the type imports):

```typescript
// --- APRS positions ---

export async function fetchEventPositions(
  eventId: number,
  since: number,
  netSlug: string,
): Promise<EventPositions> {
  return apiFetch<EventPositions>(`/nets/${netSlug}/events/${eventId}/positions?since=${since}`);
}
```

- [ ] **Step 3: Write the hook**

```typescript
// frontend/src/hooks/useEventPositions.ts
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchEventPositions } from "../api/events";
import type { BeaconedObject, EventStation } from "../types";

const POLL_MS = 5000;

/**
 * Cursor-polling APRS positions. The server sends the complete station
 * roster every poll with only new points (pos_seq > since); we replace the
 * roster and append points per station, deduped by pos_seq (overlapping
 * polls return overlapping ranges — same contract as the event log).
 * Polls only while `enabled` (a map is actually mounted/expanded) and the
 * tab is visible.
 */
export function useEventPositions(netSlug: string, eventId: number, enabled: boolean) {
  const [stations, setStations] = useState<Map<string, EventStation>>(new Map());
  const [aprsStatus, setAprsStatus] = useState("disabled");
  const [aprsStatusDetail, setAprsStatusDetail] = useState("");
  const [objects, setObjects] = useState<BeaconedObject[]>([]);
  const sinceRef = useRef(0);
  const pointsRef = useRef<Map<string, EventStation>>(new Map());

  const refresh = useCallback(async () => {
    try {
      const u = await fetchEventPositions(eventId, sinceRef.current, netSlug);
      const next = new Map<string, EventStation>();
      for (const station of u.stations) {
        const prev = pointsRef.current.get(station.station_id);
        const prevPoints = prev ? prev.points : [];
        const lastSeq = prevPoints.length > 0 ? prevPoints[prevPoints.length - 1].pos_seq : 0;
        next.set(station.station_id, {
          ...station,
          points: [...prevPoints, ...station.points.filter((p) => p.pos_seq > lastSeq)],
        });
      }
      pointsRef.current = next; // roster replaced: dropped stations disappear
      sinceRef.current = Math.max(sinceRef.current, u.latest_pos_seq);
      setStations(next);
      setAprsStatus(u.aprs_status);
      setAprsStatusDetail(u.aprs_status_detail);
      setObjects(u.objects);
    } catch {
      // Keep last-known positions on a failed poll; the aprs_status badge
      // reflects backend connectivity, not this fetch.
    }
  }, [netSlug, eventId]);

  useEffect(() => {
    if (!enabled) return;
    sinceRef.current = 0;
    pointsRef.current = new Map();
    setStations(new Map());
    void refresh();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh, enabled]);

  return { stations, aprsStatus, aprsStatusDetail, objects, refresh };
}
```

- [ ] **Step 4: Net settings APRS section**

In `frontend/src/pages/NetSettingsPage.tsx`, define next to `PAT_FIELDS`:

```typescript
const APRS_FIELDS: ConfigField[] = [
  {
    key: "aprs.enabled",
    label: "APRS-IS",
    type: "boolean",
    helpText: "Connect to APRS-IS during active events to show live positions and beacon event posts.",
  },
  {
    key: "aprs.callsign",
    label: "APRS Callsign",
    placeholder: "W0NE",
    mono: true,
    helpText:
      "Callsign used to log in and transmit objects — must be a callsign you are licensed to use.",
    visibleWhen: (v) => v["aprs.enabled"] === "true",
  },
  {
    key: "aprs.server",
    label: "APRS-IS Server",
    placeholder: "rotate.aprs2.net",
    mono: true,
    helpText: "Leave default unless you run your own server.",
    visibleWhen: (v) => v["aprs.enabled"] === "true",
  },
  {
    key: "aprs.port",
    label: "APRS-IS Port",
    placeholder: "14580",
    helpText: "Filtered-feed port.",
    visibleWhen: (v) => v["aprs.enabled"] === "true",
  },
];
```

and render a new `<SettingsSection title="APRS" fields={APRS_FIELDS} …>` alongside the existing sections, following exactly how the PAT section is passed config/save props (read the surrounding JSX and mirror it — same `config`, `onChange`/save wiring).

- [ ] **Step 5: Build + commit**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — expected clean.

```bash
git add frontend/src/types/index.ts frontend/src/api/events.ts frontend/src/hooks/useEventPositions.ts frontend/src/pages/NetSettingsPage.tsx
git commit -m "feat(aprs): frontend types, positions API + hook, net APRS settings"
```

---

### Task 8: EventMap component + full-screen map page

**Files:**
- Create: `frontend/src/pages/events/EventMap.tsx`
- Create: `frontend/src/pages/events/EventMapPage.tsx`
- Modify: `frontend/src/App.tsx` (route)

**Interfaces:**
- Consumes: Task 7 hook/types; `useEventUpdates` (participants/posts/event); Leaflet patterns from `frontend/src/components/CheckInMap.tsx` (tile URLs, theme swap via `setUrl`, ResizeObserver + `invalidateSize`).
- Produces:
  - `EventMap({ stations, participants, posts, objects, hidden, onToggleHide }: EventMapProps)` — pure-presentation Leaflet component; `hidden: Set<string>`, `onToggleHide(stationId: string)`
  - `EventMapPage` at `/nets/:slug/events/:eventId/map` (viewer role) — full-viewport map, slim header with event name, APRS status badge, back link; owns the hooks and hide-state.

- [ ] **Step 1: Write EventMap**

```tsx
// frontend/src/pages/events/EventMap.tsx
import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useTheme } from "../../hooks/useTheme";
import type { BeaconedObject, EventParticipant, EventPost, EventStation, ParticipantStatus } from "../../types";

const TILE_URL_DARK = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
const TILE_URL_LIGHT = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png";
const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/">CARTO</a>';
const DEFAULT_CENTER: L.LatLngExpression = [39.8283, -98.5795];
const DEFAULT_ZOOM = 4;
const STALE_MS = 15 * 60 * 1000; // dim markers not heard for 15 min

const STATUS_COLOR: Record<ParticipantStatus, string> = {
  checked_in: "#22c55e",
  at_post: "#22d3ee",
  en_route: "#fbbf24",
  out_of_service: "#ef4444",
  checked_out: "#71717a",
};
const OTHER_COLOR = "#9ca3af";
const POST_COLOR = "#a78bfa";

export interface EventMapProps {
  stations: Map<string, EventStation>;
  participants: EventParticipant[];
  posts: EventPost[];
  objects: BeaconedObject[];
  hidden: Set<string>;
  onToggleHide: (stationId: string) => void;
}

function popupContent(title: string, lines: string[], hideId: string | null, onHide: (id: string) => void) {
  const el = document.createElement("div");
  el.innerHTML =
    `<strong style="font-family:monospace">${title}</strong>` +
    lines.map((l) => `<br/>${l}`).join("");
  if (hideId !== null) {
    const btn = document.createElement("button");
    btn.textContent = "hide";
    btn.style.cssText = "display:block;margin-top:4px;font-size:11px;text-decoration:underline;cursor:pointer";
    btn.onclick = () => onHide(hideId);
    el.appendChild(btn);
  }
  return el;
}

export function EventMap({ stations, participants, posts, objects, hidden, onToggleHide }: EventMapProps) {
  const { theme } = useTheme();
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const tileLayerRef = useRef<L.TileLayer | null>(null);
  const layersRef = useRef<{
    participants: L.LayerGroup;
    trails: L.LayerGroup;
    posts: L.LayerGroup;
    others: L.LayerGroup;
  } | null>(null);
  const fittedRef = useRef(false);

  // Init once (same conventions as CheckInMap: invalidateSize on settle/resize)
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const container = containerRef.current;
    const map = L.map(container, { center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM });
    tileLayerRef.current = L.tileLayer(theme === "light" ? TILE_URL_LIGHT : TILE_URL_DARK, {
      attribution: TILE_ATTR,
      maxZoom: 18,
    }).addTo(map);

    const groups = {
      participants: L.layerGroup().addTo(map),
      trails: L.layerGroup().addTo(map),
      posts: L.layerGroup().addTo(map),
      others: L.layerGroup().addTo(map),
    };
    L.control
      .layers(undefined, {
        Participants: groups.participants,
        Trails: groups.trails,
        Posts: groups.posts,
        "Other stations": groups.others,
      })
      .addTo(map);
    layersRef.current = groups;
    mapRef.current = map;

    requestAnimationFrame(() => map.invalidateSize());
    const ro = new ResizeObserver(() => map.invalidateSize());
    ro.observe(container);
    return () => {
      ro.disconnect();
      map.remove();
      mapRef.current = null;
      tileLayerRef.current = null;
      layersRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    tileLayerRef.current?.setUrl(theme === "light" ? TILE_URL_LIGHT : TILE_URL_DARK);
  }, [theme]);

  // Redraw layers on data change
  useEffect(() => {
    const map = mapRef.current;
    const groups = layersRef.current;
    if (!map || !groups) return;
    groups.participants.clearLayers();
    groups.trails.clearLayers();
    groups.posts.clearLayers();
    groups.others.clearLayers();

    const statusByCallsign = new Map(participants.map((p) => [p.callsign, p.current_status]));
    const objectNameByPost = new Map(objects.map((o) => [o.post_id, o.name]));
    const now = Date.now();
    const allPoints: L.LatLngExpression[] = [];

    for (const post of posts) {
      if (post.lat == null || post.lon == null) continue;
      const marker = L.circleMarker([post.lat, post.lon], {
        radius: 7,
        fillColor: POST_COLOR,
        fillOpacity: 0.9,
        color: "#ffffff",
        weight: 1,
      });
      const objName = objectNameByPost.get(post.id);
      marker.bindPopup(
        popupContent(post.name, objName ? [`on the air as ${objName}`] : [], null, onToggleHide),
        { closeButton: false },
      );
      marker.addTo(groups.posts);
      allPoints.push([post.lat, post.lon]);
    }

    for (const station of stations.values()) {
      if (hidden.has(station.station_id) || station.points.length === 0) continue;
      const latest = station.points[station.points.length - 1];
      const stale = now - new Date(station.last_heard).getTime() > STALE_MS;
      const isParticipant = station.kind === "participant";
      const color = isParticipant
        ? STATUS_COLOR[statusByCallsign.get(station.callsign ?? "") ?? "checked_in"]
        : OTHER_COLOR;

      const marker = L.circleMarker([latest.lat, latest.lon], {
        radius: isParticipant ? 8 : 5,
        fillColor: color,
        fillOpacity: stale ? 0.3 : isParticipant ? 0.9 : 0.5,
        color: "#ffffff",
        weight: isParticipant ? 1.5 : 0.5,
      });
      const heard = new Date(station.last_heard).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });
      marker.bindTooltip(station.station_id, {
        permanent: isParticipant,
        direction: "top",
        className: "font-mono text-xs",
        offset: [0, -6],
      });
      marker.bindPopup(
        popupContent(
          station.station_id,
          [
            `last heard ${heard}${stale ? " (stale)" : ""}`,
            ...(station.comment ? [station.comment] : []),
          ],
          station.station_id,
          onToggleHide,
        ),
        { closeButton: false },
      );
      marker.addTo(isParticipant ? groups.participants : groups.others);

      if (station.points.length > 1) {
        L.polyline(
          station.points.map((p) => [p.lat, p.lon] as L.LatLngExpression),
          { color, weight: 2, opacity: 0.45, dashArray: isParticipant ? undefined : "4 4" },
        ).addTo(groups.trails);
      }
      allPoints.push([latest.lat, latest.lon]);
    }

    // Fit bounds once when content first appears; after that the operator
    // owns the viewport (refitting every poll would fight their panning).
    if (!fittedRef.current && allPoints.length > 0) {
      fittedRef.current = true;
      map.fitBounds(L.latLngBounds(allPoints), { padding: [40, 40], maxZoom: 13 });
    }
  }, [stations, participants, posts, objects, hidden, onToggleHide]);

  return <div ref={containerRef} className="h-full w-full rounded-md overflow-hidden" />;
}
```

- [ ] **Step 2: Write EventMapPage**

```tsx
// frontend/src/pages/events/EventMapPage.tsx
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Spinner } from "../../components/Spinner";
import { useEventPositions } from "../../hooks/useEventPositions";
import { useEventUpdates } from "../../hooks/useEventUpdates";
import { EventMap } from "./EventMap";

const STATUS_BADGE: Record<string, string> = {
  connected: "bg-success/15 text-success",
  reconnecting: "bg-warning/15 text-warning",
  error: "bg-danger/15 text-danger",
  disabled: "bg-bg-elevated text-text-muted",
};

export function EventMapPage() {
  const { slug, eventId } = useParams<{ slug: string; eventId: string }>();
  const { updates } = useEventUpdates(slug!, Number(eventId));
  const { stations, aprsStatus, aprsStatusDetail, objects } = useEventPositions(
    slug!,
    Number(eventId),
    true,
  );
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  if (!updates) {
    return (
      <div className="flex justify-center py-16">
        <Spinner size="lg" />
      </div>
    );
  }

  const toggleHide = (id: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)] p-2 gap-2">
      <div className="flex items-center gap-3 px-2">
        <Link
          to={`/nets/${slug}/events/${updates.event.id}`}
          className="text-text-muted hover:text-accent text-sm"
        >
          ← {updates.event.name}
        </Link>
        <span
          className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_BADGE[aprsStatus] ?? STATUS_BADGE.disabled}`}
          title={aprsStatusDetail}
        >
          APRS {aprsStatus}
        </span>
        {hidden.size > 0 && (
          <details className="text-xs text-text-muted">
            <summary className="cursor-pointer">Hidden ({hidden.size})</summary>
            <div className="absolute z-[1000] bg-bg-surface border border-border rounded-md p-2 mt-1 flex flex-col gap-1">
              {[...hidden].map((id) => (
                <button
                  key={id}
                  onClick={() => toggleHide(id)}
                  className="font-mono text-left hover:text-accent"
                >
                  {id} ✕
                </button>
              ))}
            </div>
          </details>
        )}
      </div>
      <div className="flex-1 min-h-0">
        <EventMap
          stations={stations}
          participants={updates.participants}
          posts={updates.posts}
          objects={objects}
          hidden={hidden}
          onToggleHide={toggleHide}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Add the route**

In `frontend/src/App.tsx`, import `EventMapPage` and add after the event report route:

```tsx
<Route path="events/:eventId/map" element={<RequireNetRole min="viewer"><EventMapPage /></RequireNetRole>} />
```

- [ ] **Step 4: Build + commit**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — expected clean.

```bash
git add frontend/src/pages/events/EventMap.tsx frontend/src/pages/events/EventMapPage.tsx frontend/src/App.tsx
git commit -m "feat(aprs): EventMap component and full-screen map page"
```

---

### Task 9: Dashboard map panel + NCS APRS controls

**Files:**
- Create: `frontend/src/pages/events/MapPanel.tsx`
- Modify: `frontend/src/pages/events/EventDashboardPage.tsx` (mount panel)
- Modify: `frontend/src/pages/events/PostsPanel.tsx` (beacon toggle + on-air names)

**Interfaces:**
- Consumes: Tasks 7–8 (`useEventPositions`, `EventMap`), `updateEvent`, dashboard's `act`/`canWrite`/`refresh` wiring, `NetEvent` APRS fields.
- Produces:
  - The positions hook lives in **EventDashboardPage** (hooks must be called unconditionally; the `enabled` flag is `mapExpanded || event.aprs_beacon_posts`, so on-air names work even with the panel collapsed, and a fully idle dashboard polls nothing).
  - `MapPanel({ netSlug, event, participants, posts, canWrite, expanded, onToggleExpanded, positions, onEventChanged, onError })` — collapsible card: collapsed renders just the header; expanded renders `EventMap` (~360 px tall), the APRS status badge (with a net-settings link when status is `disabled`), the Hidden(n) unhide list, an "Expand" link to `/events/{id}/map`, and (NCS, active event) the other-stations toggle with a range input
  - `PostsPanel` gains: a "Beacon posts as APRS objects" checkbox (PATCHes `aprs_beacon_posts` via `updateEvent`) and, per post with coords while beaconing is on, an "on the air as NAME" hint fed by an `objects: BeaconedObject[]` prop.

- [ ] **Step 1: Write MapPanel (props-driven — no hook of its own)**

```tsx
// frontend/src/pages/events/MapPanel.tsx
import { useState } from "react";
import { Link } from "react-router-dom";
import { updateEvent } from "../../api/events";
import type { BeaconedObject, EventParticipant, EventPost, EventStation, NetEvent } from "../../types";
import { EventMap } from "./EventMap";

const STATUS_BADGE: Record<string, string> = {
  connected: "bg-success/15 text-success",
  reconnecting: "bg-warning/15 text-warning",
  error: "bg-danger/15 text-danger",
  disabled: "bg-bg-elevated text-text-muted",
};

export interface PositionsData {
  stations: Map<string, EventStation>;
  aprsStatus: string;
  aprsStatusDetail: string;
  objects: BeaconedObject[];
}

interface MapPanelProps {
  netSlug: string;
  event: NetEvent;
  participants: EventParticipant[];
  posts: EventPost[];
  canWrite: boolean;
  expanded: boolean;
  onToggleExpanded: () => void;
  positions: PositionsData;
  onEventChanged: () => Promise<void>;
  onError: (message: string) => void;
}

export function MapPanel({
  netSlug,
  event,
  participants,
  posts,
  canWrite,
  expanded,
  onToggleExpanded,
  positions,
  onEventChanged,
  onError,
}: MapPanelProps) {
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [rangeKm, setRangeKm] = useState(event.aprs_range_km?.toString() ?? "50");
  const { stations, aprsStatus, aprsStatusDetail, objects } = positions;

  const toggleHide = (id: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  async function toggleOthers() {
    try {
      if (!event.aprs_other_stations) {
        // Default range center: mean of post coords, else first participant point
        const coords = posts.filter((p) => p.lat != null && p.lon != null);
        const lat =
          event.aprs_range_lat ??
          (coords.length > 0 ? coords.reduce((s, p) => s + (p.lat as number), 0) / coords.length : 39.8283);
        const lon =
          event.aprs_range_lon ??
          (coords.length > 0 ? coords.reduce((s, p) => s + (p.lon as number), 0) / coords.length : -98.5795);
        await updateEvent(
          event.id,
          {
            aprs_other_stations: true,
            aprs_range_lat: lat,
            aprs_range_lon: lon,
            aprs_range_km: Number(rangeKm) || 50,
          },
          netSlug,
        );
      } else {
        await updateEvent(event.id, { aprs_other_stations: false }, netSlug);
      }
      await onEventChanged();
    } catch (e) {
      onError(e instanceof Error ? e.message : "Failed to update APRS settings");
    }
  }

  return (
    <div className="rounded-md border border-border bg-bg-surface mb-4">
      <div className="flex items-center gap-3 px-3 py-2">
        <button
          onClick={onToggleExpanded}
          className="text-sm font-semibold text-text-primary hover:text-accent"
        >
          {expanded ? "▾" : "▸"} Live map
        </button>
        {expanded && (
          <span
            className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_BADGE[aprsStatus] ?? STATUS_BADGE.disabled}`}
            title={aprsStatusDetail}
          >
            APRS {aprsStatus}
          </span>
        )}
        {expanded && aprsStatus === "disabled" && (
          <Link to={`/nets/${netSlug}/settings`} className="text-xs text-text-muted hover:text-accent underline">
            enable in net settings
          </Link>
        )}
        {expanded && hidden.size > 0 && (
          <details className="text-xs text-text-muted relative">
            <summary className="cursor-pointer">Hidden ({hidden.size})</summary>
            <div className="absolute z-[1000] bg-bg-surface border border-border rounded-md p-2 mt-1 flex flex-col gap-1">
              {[...hidden].map((id) => (
                <button key={id} onClick={() => toggleHide(id)} className="font-mono text-left hover:text-accent">
                  {id} ✕
                </button>
              ))}
            </div>
          </details>
        )}
        <div className="ml-auto flex items-center gap-3">
          {expanded && canWrite && event.status === "active" && (
            <label className="flex items-center gap-1 text-xs text-text-muted">
              <input type="checkbox" checked={event.aprs_other_stations} onChange={() => void toggleOthers()} />
              Other stations
              {!event.aprs_other_stations && (
                <input
                  value={rangeKm}
                  onChange={(e) => setRangeKm(e.target.value)}
                  className="w-14 rounded bg-bg-elevated border border-border px-1 py-0.5 text-xs"
                  title="Range (km)"
                />
              )}
            </label>
          )}
          <Link to={`/nets/${netSlug}/events/${event.id}/map`} className="text-xs text-accent hover:underline">
            Expand
          </Link>
        </div>
      </div>
      {expanded && (
        <div className="h-[360px] border-t border-border">
          <EventMap
            stations={stations}
            participants={participants}
            posts={posts}
            objects={objects}
            hidden={hidden}
            onToggleHide={toggleHide}
          />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Mount in the dashboard (hook lifted here)**

In `frontend/src/pages/events/EventDashboardPage.tsx`, import `MapPanel` and `useEventPositions`, add panel state + the hook near the other hooks (unconditional call — the `enabled` flag does the gating):

```tsx
  const [mapExpanded, setMapExpanded] = useState(false);
  const positions = useEventPositions(
    slug!,
    Number(eventId),
    mapExpanded || (updates?.event.aprs_beacon_posts ?? false),
  );
```

and render between the check-in bar and the participant/log grid:

```tsx
      <MapPanel
        netSlug={slug!}
        event={event}
        participants={participants}
        posts={posts}
        canWrite={canWrite}
        expanded={mapExpanded}
        onToggleExpanded={() => setMapExpanded(!mapExpanded)}
        positions={positions}
        onEventChanged={refresh}
        onError={onError}
      />
```

(`positions` from the hook is structurally a `PositionsData` — `{stations, aprsStatus, aprsStatusDetail, objects, refresh}`; the extra `refresh` key is fine for the prop type.)

- [ ] **Step 3: Beacon toggle + on-air names in PostsPanel**

Extend `PostsPanel`'s props with `event: NetEvent`, `objects: BeaconedObject[]`, `canWrite: boolean` (import types; import `updateEvent`). Above the posts list add:

```tsx
      {canWrite && (
        <label className="flex items-center gap-2 text-xs text-text-muted mb-2">
          <input
            type="checkbox"
            checked={event.aprs_beacon_posts}
            onChange={async (e) => {
              try {
                await updateEvent(event.id, { aprs_beacon_posts: e.target.checked }, netSlug);
                await onChanged();
              } catch (err) {
                onError(err instanceof Error ? err.message : "Failed to toggle beaconing");
              }
            }}
          />
          Beacon posts as APRS objects (transmits under the net's callsign)
        </label>
      )}
```

and in each post row, after the name/coords span:

```tsx
      {event.aprs_beacon_posts && objectName(p.id) && (
        <span className="text-xs text-accent font-mono ml-2">on air: {objectName(p.id)}</span>
      )}
```

with a small helper inside the component: `const objectName = (postId: number) => objects.find((o) => o.post_id === postId)?.name;`

Update the dashboard's `<PostsPanel …>` call site to pass `event={event}`, `canWrite={canWrite}`, and `objects={positions.objects}` — the lifted hook (Step 2) is enabled whenever `event.aprs_beacon_posts` is true, so on-air names are populated even while the map panel is collapsed. `EventMapPage` keeps its own hook instance.

- [ ] **Step 4: Build + commit**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — expected clean.

```bash
git add frontend/src/pages/events/
git commit -m "feat(aprs): dashboard map panel, other-stations toggle, beacon controls"
```

---

### Task 10: Final verification sweep

**Files:**
- Test: none new — full gates + manual checklist

- [ ] **Step 1: Full backend suite + lint**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

- [ ] **Step 2: Frontend build**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — expected clean.

- [ ] **Step 3: Migration on a scratch DB**

Run: `SKYNET_DATABASE_URL="sqlite:////tmp/claude-aprs-final.db" .venv/bin/alembic upgrade head && rm -f /tmp/claude-aprs-final.db` — expected clean through `b3f0a1c2d4e5`.

- [ ] **Step 4: Commit anything outstanding, then manual smoke (human checkpoint)**

With `./run-dev.sh` and net settings pointing `aprs.server` at a real or fake APRS-IS: enable APRS on the net, activate an event, check in a callsign that's beaconing (or feed the fake server), watch the marker + trail appear on the dashboard panel and the full-screen map; toggle other stations; enable post beaconing and verify the object name shows; close the event and confirm the client disconnects (kill packets in server log).
