# Weather Overlay (SP6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a weather overlay to the live event map — an animated RainViewer radar loop (client-side) and NWS watch/warning polygons (backend-proxied) — for emergency/skywarn nets.

**Architecture:** Radar is a client-side Leaflet tile-layer animator fed by RainViewer's public JSON index (no key). Warnings go through a new `backend/integrations/weather` module (cache-on-read, no background task) that fetches `api.weather.gov` alerts for the event's area and serves GeoJSON on a VIEWER-gated `/weather` route the frontend polls. Both hang off `EventMap`'s existing `L.control.layers`, gated per-net by `weather.enabled`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, httpx (already a dep — NO new backend dep), React 19 + TypeScript, Leaflet 1.9.4 (built-in `L.geoJSON` + `L.tileLayer` — NO new frontend dep), pytest + httpx `MockTransport`.

## Global Constraints

- **No new dependencies.** `httpx>=0.28.0` is already present; Leaflet 1.9.4 covers GeoJSON + tile layers. Radar needs no key/account; warnings need no key.
- **No background task.** The weather service is cache-on-read (a module-level TTL cache like callbook's `_session_tokens`), NOT an APRS-style per-event asyncio task.
- **Every NWS call goes through `backend/integrations/weather/client.py`** with a descriptive `User-Agent` (`SkyNetControl (<contact>)`); tests mock it via httpx `MockTransport` (no live network in CI).
- **Feature gated per-net by `weather.enabled`** (default `"false"`): off ⇒ `/weather` returns `status: "disabled"`, the event detail's `weather_enabled` is false, and no layers/toggles render. `weather.*` keys are NOT sensitive (no `api_key`/`password`/`secret`/`token` fragment) — stored plaintext, correct.
- **`/weather` route status values** are exactly: `"ok"`, `"stale"`, `"unavailable"`, `"disabled"`, `"no_area"`. Best-effort — NWS problems surface as `status`, never a 500.
- **Weather is purely additive** to the map: any radar/warnings failure must never disturb the base map or the APRS layers.
- **Poll only while the map is mounted + tab visible**, matching `useEventPositions` (`document.visibilityState === "visible"`). Warnings poll ~60s; radar re-fetches its index every few minutes.
- Ruff: line-length 120, `select = ["E", "F"]`, no per-file ignores in production code. `nix-shell --run "ruff check"` before every commit.
- Backend tests `.venv/bin/pytest -q`; frontend build `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`.

## File Structure

- `backend/integrations/weather/__init__.py` (new) — package marker.
- `backend/integrations/weather/client.py` (new) — `WeatherClient` (NWS httpx seam): `fetch_active_alerts`, `lookup_state`; `WeatherUnavailable`.
- `backend/integrations/weather/service.py` (new) — `get_event_alerts` (TTL cache, area derivation, status), `_resolve_states`, `_event_location`.
- `backend/modules/events/routes.py` (modify) — add `GET /{event_id}/weather` route; add `weather_enabled` to `_event_to_response`.
- `frontend/src/types/index.ts` (modify) — `WeatherAlerts`, `WeatherFeature`, status type; `weather_enabled` on `NetEvent`.
- `frontend/src/api/events.ts` (modify) — `fetchEventWeather`.
- `frontend/src/hooks/useEventWeather.ts` (new) — poll `/weather`.
- `frontend/src/hooks/useRadarFrames.ts` (new) — fetch RainViewer index + tile-URL builder.
- `frontend/src/pages/events/EventMap.tsx` (modify) — warnings GeoJSON layer + radar animated layer + both in the layer control.
- `frontend/src/pages/events/MapPanel.tsx` (modify) — pass weather data, radar controls, weather status chip.
- `frontend/src/pages/NetSettingsPage.tsx` (modify) — "Weather overlay" config section.

---

### Task 1: NWS weather client (`weather/client.py`)

**Files:**
- Create: `backend/integrations/weather/__init__.py` (empty)
- Create: `backend/integrations/weather/client.py`
- Create: `tests/test_weather_client.py`

**Interfaces:**
- Produces: `WeatherClient(*, user_agent: str, timeout: float = 15.0)` with `_transport` test seam; `fetch_active_alerts(states: list[str]) -> dict` (a merged, id-deduped GeoJSON FeatureCollection), `lookup_state(lat: float, lon: float) -> str | None`. Exception `WeatherUnavailable`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_weather_client.py
import httpx
import pytest

from backend.integrations.weather.client import WeatherClient, WeatherUnavailable


def _client(handler):
    c = WeatherClient(user_agent="SkyNetControl (test@example.com)", timeout=5.0)
    c._transport = httpx.MockTransport(handler)
    return c


def test_fetch_active_alerts_merges_and_dedups_by_id():
    def handler(request: httpx.Request) -> httpx.Response:
        area = request.url.params.get("area")
        assert request.url.path == "/alerts/active"
        assert request.headers["user-agent"] == "SkyNetControl (test@example.com)"
        if area == "MN":
            return httpx.Response(200, json={"type": "FeatureCollection", "features": [
                {"id": "A", "properties": {"event": "Tornado Warning"}},
                {"id": "B", "properties": {"event": "Flood Watch"}},
            ]})
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [
            {"id": "B", "properties": {"event": "Flood Watch"}},  # dup across states
            {"id": "C", "properties": {"event": "Severe Thunderstorm Warning"}},
        ]})

    fc = _client(handler).fetch_active_alerts(["MN", "WI"])
    assert fc["type"] == "FeatureCollection"
    ids = sorted(f["id"] for f in fc["features"])
    assert ids == ["A", "B", "C"]  # B deduped


def test_lookup_state_reads_relative_location():
    def handler(request):
        assert request.url.path == "/points/44.98,-93.27"
        return httpx.Response(200, json={"properties": {
            "relativeLocation": {"properties": {"city": "Minneapolis", "state": "MN"}}}})
    assert _client(handler).lookup_state(44.98, -93.27) == "MN"


def test_lookup_state_missing_returns_none():
    def handler(request):
        return httpx.Response(200, json={"properties": {}})
    assert _client(handler).lookup_state(0.0, 0.0) is None


def test_http_error_maps_to_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(WeatherUnavailable):
        _client(handler).fetch_active_alerts(["MN"])
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/pytest tests/test_weather_client.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `client.py`**

```python
# backend/integrations/weather/client.py
"""HTTP client seam for the US National Weather Service API (api.weather.gov).
Every NWS call goes through here with a descriptive User-Agent; tests mock the
transport, so no live network in CI."""
from __future__ import annotations

import httpx

NWS_BASE = "https://api.weather.gov"


class WeatherUnavailable(Exception):
    """NWS could not be reached or returned an error."""


class WeatherClient:
    def __init__(self, *, user_agent: str, timeout: float = 15.0):
        self.user_agent = user_agent
        self.timeout = timeout
        self._transport: httpx.BaseTransport | None = None  # test seam

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=NWS_BASE,
            headers={"User-Agent": self.user_agent, "Accept": "application/geo+json"},
            timeout=self.timeout,
            transport=self._transport,
        )

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            with self._client() as c:
                resp = c.get(path, params=params)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise WeatherUnavailable(f"NWS {path} unavailable: {exc}") from exc

    def fetch_active_alerts(self, states: list[str]) -> dict:
        """Merge active alerts across states, deduped by feature id."""
        features: dict[str, dict] = {}
        for state in states:
            data = self._get("/alerts/active", params={"area": state})
            for feat in data.get("features", []):
                fid = feat.get("id") or feat.get("properties", {}).get("id")
                if fid:
                    features[fid] = feat
        return {"type": "FeatureCollection", "features": list(features.values())}

    def lookup_state(self, lat: float, lon: float) -> str | None:
        data = self._get(f"/points/{lat},{lon}")
        rel = data.get("properties", {}).get("relativeLocation", {})
        return rel.get("properties", {}).get("state") or None
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_weather_client.py -q` — PASS (4). Then `nix-shell --run "ruff check"`.

- [ ] **Step 5: Commit**

```bash
git add backend/integrations/weather/__init__.py backend/integrations/weather/client.py tests/test_weather_client.py
git commit -m "feat(weather): NWS API client seam (active alerts + state lookup)"
```

---

### Task 2: Weather service (`weather/service.py`)

**Files:**
- Create: `backend/integrations/weather/service.py`
- Create: `tests/test_weather_service.py`

**Interfaces:**
- Consumes: `WeatherClient`/`WeatherUnavailable` (Task 1); `get_net_config` (`backend/modules/nets/config_service.py`); `Event`/`EventPost` (`backend/modules/events/models.py`).
- Produces: `get_event_alerts(db, event_id, *, client=None, now=None) -> dict` returning `{"alerts": <FeatureCollection>, "updated_at": str | None, "status": str}`; `clear_weather_cache()` (test helper); constant `WEATHER_TTL_SECONDS = 60.0`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_weather_service.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.nets.models import Net
from backend.modules.nets.config_service import set_net_config
from backend.modules.events.models import Event, EventPost, EventType, EventStatus
from backend.integrations.weather import service as weather_service
from backend.integrations.weather.client import WeatherUnavailable

EMPTY = {"type": "FeatureCollection", "features": []}
ONE = {"type": "FeatureCollection", "features": [{"id": "A", "properties": {"event": "Tornado Warning"}}]}


class FakeClient:
    def __init__(self, *, alerts=None, state="MN", unavailable=False):
        self._alerts = alerts if alerts is not None else ONE
        self._state = state
        self._unavailable = unavailable
        self.calls = 0

    def fetch_active_alerts(self, states):
        self.calls += 1
        if self._unavailable:
            raise WeatherUnavailable("down")
        return self._alerts

    def lookup_state(self, lat, lon):
        return self._state


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    net = Net(slug="t", name="T"); db.add(net); db.flush()
    ev = Event(net_id=net.id, name="E", event_type=EventType.EMERGENCY, status=EventStatus.ACTIVE,
               created_by="W0NE", aprs_range_lat=44.98, aprs_range_lon=-93.27)
    db.add(ev); db.flush()
    return db, net.id, ev.id


def setup_function():
    weather_service.clear_weather_cache()


def test_disabled_returns_disabled_status():
    db, net_id, ev_id = _db()
    r = weather_service.get_event_alerts(db, ev_id, client=FakeClient())
    assert r["status"] == "disabled"
    assert r["alerts"] == EMPTY


def test_enabled_with_configured_states_fetches_ok():
    db, net_id, ev_id = _db()
    set_net_config(db, net_id, "weather.enabled", "true")
    set_net_config(db, net_id, "weather.alert_states", '["MN"]')
    r = weather_service.get_event_alerts(db, ev_id, client=FakeClient(alerts=ONE))
    assert r["status"] == "ok"
    assert r["alerts"]["features"][0]["id"] == "A"
    assert r["updated_at"] is not None


def test_area_derived_from_event_location_when_no_states():
    db, net_id, ev_id = _db()
    set_net_config(db, net_id, "weather.enabled", "true")
    fc = FakeClient(state="MN")
    r = weather_service.get_event_alerts(db, ev_id, client=fc)
    assert r["status"] == "ok"  # derived MN from aprs_range_lat/lon


def test_no_area_when_no_states_and_no_location():
    db, net_id, ev_id = _db()
    set_net_config(db, net_id, "weather.enabled", "true")
    # remove the event location + posts
    ev = db.get(Event, ev_id); ev.aprs_range_lat = None; ev.aprs_range_lon = None; db.commit()
    r = weather_service.get_event_alerts(db, ev_id, client=FakeClient(state=None))
    assert r["status"] == "no_area"


def test_cache_shared_within_ttl():
    db, net_id, ev_id = _db()
    set_net_config(db, net_id, "weather.enabled", "true")
    set_net_config(db, net_id, "weather.alert_states", '["MN"]')
    fc = FakeClient()
    weather_service.get_event_alerts(db, ev_id, client=fc, now=1000.0)
    weather_service.get_event_alerts(db, ev_id, client=fc, now=1030.0)  # within 60s
    assert fc.calls == 1  # second served from cache
    weather_service.get_event_alerts(db, ev_id, client=fc, now=1100.0)  # past TTL
    assert fc.calls == 2


def test_stale_while_error_serves_last_good():
    db, net_id, ev_id = _db()
    set_net_config(db, net_id, "weather.enabled", "true")
    set_net_config(db, net_id, "weather.alert_states", '["MN"]')
    weather_service.get_event_alerts(db, ev_id, client=FakeClient(alerts=ONE), now=1000.0)
    r = weather_service.get_event_alerts(db, ev_id, client=FakeClient(unavailable=True), now=1100.0)
    assert r["status"] == "stale"
    assert r["alerts"]["features"][0]["id"] == "A"  # last good retained


def test_unavailable_with_no_cache():
    db, net_id, ev_id = _db()
    set_net_config(db, net_id, "weather.enabled", "true")
    set_net_config(db, net_id, "weather.alert_states", '["MN"]')
    r = weather_service.get_event_alerts(db, ev_id, client=FakeClient(unavailable=True))
    assert r["status"] == "unavailable"
    assert r["alerts"] == EMPTY
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/pytest tests/test_weather_service.py -q` — FAIL.

- [ ] **Step 3: Implement `service.py`**

```python
# backend/integrations/weather/service.py
"""Event weather alerts: resolve the event's NWS area, fetch active alerts with a
short shared TTL cache (cache-on-read, no background task), and degrade gracefully
when NWS is unavailable."""
from __future__ import annotations

import json
import time as _time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.modules.events.models import Event, EventPost
from backend.modules.nets.config_service import get_net_config
from backend.integrations.weather.client import WeatherClient, WeatherUnavailable

WEATHER_TTL_SECONDS = 60.0
_EMPTY = {"type": "FeatureCollection", "features": []}

# area-key (tuple of sorted states) -> (alerts FeatureCollection, updated_at ISO, fetched_monotonic)
_CACHE: dict[tuple[str, ...], tuple[dict, str, float]] = {}


def clear_weather_cache() -> None:
    _CACHE.clear()


def _weather_enabled(db: Session, net_id: int) -> bool:
    return (get_net_config(db, net_id, "weather.enabled") or "").strip().lower() == "true"


def _user_agent(db: Session, net_id: int) -> str:
    contact = (get_net_config(db, net_id, "weather.nws_contact")
               or get_net_config(db, net_id, "net_address") or "").strip()
    return f"SkyNetControl ({contact})" if contact else "SkyNetControl"


def _event_location(db: Session, event: Event) -> tuple[float, float] | None:
    if event.aprs_range_lat is not None and event.aprs_range_lon is not None:
        return (event.aprs_range_lat, event.aprs_range_lon)
    coords = [(p.lat, p.lon) for p in db.query(EventPost).filter(EventPost.event_id == event.id).all()
              if p.lat is not None and p.lon is not None]
    if not coords:
        return None
    return (sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords))


def _resolve_states(db: Session, event: Event, client: WeatherClient) -> list[str]:
    raw = get_net_config(db, event.net_id, "weather.alert_states")
    if raw:
        try:
            states = [str(s).strip().upper() for s in json.loads(raw) if str(s).strip()]
            if states:
                return states
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    loc = _event_location(db, event)
    if loc is None:
        return []
    state = client.lookup_state(loc[0], loc[1])
    return [state] if state else []


def get_event_alerts(db: Session, event_id: int, *, client: WeatherClient | None = None,
                     now: float | None = None) -> dict:
    event = db.get(Event, event_id)
    if event is None:
        return {"alerts": _EMPTY, "updated_at": None, "status": "no_area"}
    if not _weather_enabled(db, event.net_id):
        return {"alerts": _EMPTY, "updated_at": None, "status": "disabled"}

    if client is None:
        client = WeatherClient(user_agent=_user_agent(db, event.net_id))
    if now is None:
        now = _time.monotonic()

    try:
        states = _resolve_states(db, event, client)
    except WeatherUnavailable:
        states = []
    if not states:
        return {"alerts": _EMPTY, "updated_at": None, "status": "no_area"}

    key = tuple(sorted(states))
    cached = _CACHE.get(key)
    if cached is not None and (now - cached[2]) < WEATHER_TTL_SECONDS:
        return {"alerts": cached[0], "updated_at": cached[1], "status": "ok"}

    try:
        alerts = client.fetch_active_alerts(list(key))
    except WeatherUnavailable:
        if cached is not None:
            return {"alerts": cached[0], "updated_at": cached[1], "status": "stale"}
        return {"alerts": _EMPTY, "updated_at": None, "status": "unavailable"}

    updated_at = datetime.now(timezone.utc).isoformat()
    _CACHE[key] = (alerts, updated_at, now)
    return {"alerts": alerts, "updated_at": updated_at, "status": "ok"}
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_weather_service.py -q` — PASS (7). Then `nix-shell --run "ruff check"`.

- [ ] **Step 5: Commit**

```bash
git add backend/integrations/weather/service.py tests/test_weather_service.py
git commit -m "feat(weather): event alerts service with area derivation + TTL cache"
```

---

### Task 3: Weather route + `weather_enabled` on event detail

**Files:**
- Modify: `backend/modules/events/routes.py`
- Modify: `tests/test_event_routes.py` (or the events-route test file; create `tests/test_weather_route.py` if cleaner)

**Interfaces:**
- Consumes: `get_event_alerts` (Task 2); `get_net_config`; `require_net_role(NetRole.VIEWER)`, `_get_event_or_404`, `get_db_session`, `NetContext`, `events_router` (existing).
- Produces: `GET /api/nets/{slug}/events/{event_id}/weather` → `{alerts, updated_at, status}`; `_event_to_response(event, *, weather_enabled=False)` now includes `"weather_enabled"`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_weather_route.py
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI

from backend.db.base import Base
from backend.config import Settings
from backend.auth.models import User  # User model lives here in this repo
from backend.modules.nets.models import Net, NetMembership, NetRole
from backend.modules.nets.config_service import set_net_config
from backend.modules.events.models import Event, EventType, EventStatus
from backend.modules.events.routes import events_router
from backend.integrations.weather import service as weather_service
from tests.conftest import make_test_token

BASE = "/api/nets/t/events"


@pytest.fixture(autouse=True)
def _clear_cache():
    weather_service.clear_weather_cache()
    yield


@pytest.fixture
def app_and_ids():
    settings = Settings(database_url="sqlite:///", jwt_secret_key="test-secret")
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(User(callsign="KD0TST", oidc_subject="x|v", name="V"))
        net = Net(slug="t", name="T"); db.add(net); db.flush()
        db.add(NetMembership(user_callsign="KD0TST", net_id=net.id, role=NetRole.VIEWER))
        ev = Event(net_id=net.id, name="E", event_type=EventType.EMERGENCY,
                   status=EventStatus.ACTIVE, created_by="KD0TST",
                   aprs_range_lat=44.98, aprs_range_lon=-93.27)
        db.add(ev); db.commit()
        ev_id = ev.id
    app = FastAPI(); app.state.session_factory = factory; app.state.settings = settings
    app.include_router(events_router)
    return app, settings, ev_id


async def _viewer(app, settings):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       cookies={"access_token": make_test_token("KD0TST", settings)})


@pytest.mark.asyncio
async def test_weather_disabled_by_default(app_and_ids):
    app, settings, ev_id = app_and_ids
    async with await _viewer(app, settings) as c:
        r = await c.get(f"{BASE}/{ev_id}/weather")
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"


@pytest.mark.asyncio
async def test_weather_enabled_flag_on_event_detail(app_and_ids, monkeypatch):
    app, settings, ev_id = app_and_ids
    with app.state.session_factory() as db:
        net_id = db.get(Event, ev_id).net_id
        set_net_config(db, net_id, "weather.enabled", "true")
    async with await _viewer(app, settings) as c:
        r = await c.get(f"{BASE}/{ev_id}")
    assert r.status_code == 200
    assert r.json()["weather_enabled"] is True


@pytest.mark.asyncio
async def test_weather_route_cross_net_404(app_and_ids):
    app, settings, ev_id = app_and_ids
    async with await _viewer(app, settings) as c:
        r = await c.get(f"{BASE}/99999/weather")
    assert r.status_code == 404
```

(If the events-route detail GET differs, adapt the detail assertion; the point is `weather_enabled` appears in `_event_to_response`'s output.)

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/pytest tests/test_weather_route.py -q` — FAIL.

- [ ] **Step 3: Add the route + serializer field**

In `backend/modules/events/routes.py`, import the service near the other integration imports:
```python
from backend.integrations.weather.service import get_event_alerts
from backend.modules.nets.config_service import get_net_config  # if not already imported
```
Add the route next to `positions_route`:
```python
@events_router.get("/{event_id}/weather")
async def weather_route(
    event_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    return get_event_alerts(db, event_id)
```
Change `_event_to_response` to accept and emit the flag:
```python
def _event_to_response(event: Event, *, weather_enabled: bool = False) -> dict:
    return {
        # ... all existing fields unchanged ...
        "weather_enabled": weather_enabled,
    }
```
At EVERY call site of `_event_to_response`, compute the flag once per request from net config and pass it. For a single event (detail route): `_event_to_response(event, weather_enabled=(get_net_config(db, event.net_id, "weather.enabled") == "true"))`. For a list route, compute the net's flag once (it's per-net, same for all events in the net) and pass it to each. Grep for `_event_to_response(` and update all callers.

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_weather_route.py -q` — PASS. Then regression: `.venv/bin/pytest tests/test_event_routes.py -q` (existing event-route tests must still pass — if any asserts the exact `_event_to_response` shape, `weather_enabled` is additive). Then `nix-shell --run "ruff check"`.

- [ ] **Step 5: Commit**

```bash
git add backend/modules/events/routes.py tests/test_weather_route.py
git commit -m "feat(weather): /weather route + weather_enabled on event detail"
```

---

### Task 4: Frontend types + API + weather/radar hooks

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/api/events.ts`
- Create: `frontend/src/hooks/useEventWeather.ts`
- Create: `frontend/src/hooks/useRadarFrames.ts`

**Interfaces:**
- Produces: types `WeatherFeature`, `WeatherAlerts`, `WeatherStatus`, `WeatherData`; `weather_enabled: boolean` on `NetEvent`; API `fetchEventWeather`; hooks `useEventWeather(netSlug, eventId, enabled) -> WeatherData` and `useRadarFrames(enabled) -> { frames, tileUrl }`.

- [ ] **Step 1: Add types**

Append to `frontend/src/types/index.ts`, and add `weather_enabled: boolean;` to the `NetEvent` interface (next to the `aprs_*` fields):
```typescript
export type WeatherStatus = "ok" | "stale" | "unavailable" | "disabled" | "no_area";

export interface WeatherFeature {
  id?: string;
  type: "Feature";
  geometry: unknown;                 // GeoJSON geometry, passed straight to L.geoJSON
  properties: {
    event?: string;                  // e.g. "Tornado Warning"
    headline?: string;
    severity?: string;               // Extreme | Severe | Moderate | Minor | Unknown
    expires?: string;
    [k: string]: unknown;
  };
}

export interface WeatherAlerts {
  type: "FeatureCollection";
  features: WeatherFeature[];
}

export interface WeatherData {
  alerts: WeatherAlerts;
  updated_at: string | null;
  status: WeatherStatus;
}
```

- [ ] **Step 2: Add the API function**

Append to `frontend/src/api/events.ts` (import `WeatherData`):
```typescript
export async function fetchEventWeather(eventId: number, netSlug: string): Promise<WeatherData> {
  return apiFetch<WeatherData>(`/nets/${netSlug}/events/${eventId}/weather`);
}
```

- [ ] **Step 3: The warnings poll hook**

```typescript
// frontend/src/hooks/useEventWeather.ts
import { useCallback, useEffect, useState } from "react";
import { fetchEventWeather } from "../api/events";
import type { WeatherData } from "../types";

const POLL_MS = 60000;
const EMPTY: WeatherData = { alerts: { type: "FeatureCollection", features: [] }, updated_at: null, status: "disabled" };

export function useEventWeather(netSlug: string, eventId: number, enabled: boolean): WeatherData {
  const [data, setData] = useState<WeatherData>(EMPTY);

  const refresh = useCallback(async () => {
    try {
      setData(await fetchEventWeather(eventId, netSlug));
    } catch {
      // keep last-known on transient failure
    }
  }, [netSlug, eventId]);

  useEffect(() => {
    if (!enabled) { setData(EMPTY); return; }
    void refresh();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh, enabled]);

  return data;
}
```

- [ ] **Step 4: The radar-frames hook**

```typescript
// frontend/src/hooks/useRadarFrames.ts
import { useCallback, useEffect, useState } from "react";

const INDEX_URL = "https://api.rainviewer.com/public/weather-maps.json";
const REFETCH_MS = 5 * 60 * 1000;

export interface RadarFrame { time: number; path: string; }

interface RadarState {
  frames: RadarFrame[];
  tileUrl: (frame: RadarFrame) => string;
}

const EMPTY: RadarState = { frames: [], tileUrl: () => "" };

export function useRadarFrames(enabled: boolean): RadarState {
  const [state, setState] = useState<RadarState>(EMPTY);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(INDEX_URL);
      if (!res.ok) return;
      const json = await res.json();
      const host: string = json.host;
      const past: RadarFrame[] = json?.radar?.past ?? [];
      const nowcast: RadarFrame[] = json?.radar?.nowcast ?? [];
      const frames = [...past, ...nowcast];
      // RainViewer tile template: {host}{path}/{size}/{z}/{x}/{y}/{color}/{smooth}_{snow}.png
      const tileUrl = (frame: RadarFrame) => `${host}${frame.path}/256/{z}/{x}/{y}/2/1_1.png`;
      setState({ frames, tileUrl });
    } catch {
      // leave last-known frames on failure
    }
  }, []);

  useEffect(() => {
    if (!enabled) { setState(EMPTY); return; }
    void refresh();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, REFETCH_MS);
    return () => window.clearInterval(id);
  }, [refresh, enabled]);

  return state;
}
```

- [ ] **Step 5: Build check + commit**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — clean.
```bash
git add frontend/src/types/index.ts frontend/src/api/events.ts frontend/src/hooks/useEventWeather.ts frontend/src/hooks/useRadarFrames.ts
git commit -m "feat(weather): frontend types, weather API, weather + radar hooks"
```

---

### Task 5: Warnings GeoJSON layer + layer-control integration + status chip

**Files:**
- Modify: `frontend/src/pages/events/EventMap.tsx`
- Modify: `frontend/src/pages/events/MapPanel.tsx`

**Interfaces:**
- Consumes: `useEventWeather` (Task 4); `WeatherData`/`NetEvent.weather_enabled` (Task 4); the existing `EventMap` layer groups + `L.control.layers` (Task-0 existing code).
- Produces: a "Weather: Warnings" `L.LayerGroup` rendered from the alerts GeoJSON, styled by severity, added to the layer control only when weather is enabled; a weather status chip in `MapPanel`. `EventMapProps` gains `weatherEnabled: boolean` and `alerts: WeatherAlerts`.

- [ ] **Step 1: Add a severity style helper + the warnings layer group in `EventMap.tsx`**

Add near the top of `EventMap.tsx`:
```typescript
import type { WeatherAlerts, WeatherFeature } from "../../types";

function alertStyle(feature?: WeatherFeature): L.PathOptions {
  const ev = (feature?.properties?.event ?? "").toLowerCase();
  const sev = (feature?.properties?.severity ?? "").toLowerCase();
  const isWarning = ev.includes("warning");
  let color = "#9ca3af";                       // muted default (advisories/statements)
  if (ev.includes("tornado") && isWarning) color = "#dc2626";       // red
  else if (isWarning && (sev === "extreme" || sev === "severe")) color = "#ef4444";
  else if (ev.includes("watch")) color = "#f59e0b";                 // amber
  return { color, weight: 2, fillColor: color, fillOpacity: 0.15 };
}
```
Extend the `layersRef` groups object to include `weather` and add it to the map + control. In the map-init effect, where `L.control.layers(undefined, {...})` is built, conditionally include the weather entry:
```typescript
const groups = {
  participants: L.layerGroup().addTo(map),
  trails: L.layerGroup().addTo(map),
  posts: L.layerGroup().addTo(map),
  others: L.layerGroup().addTo(map),
  weather: L.layerGroup(),   // NOT addTo(map) — off by default
};
const overlays: Record<string, L.Layer> = {
  Participants: groups.participants,
  Trails: groups.trails,
  Posts: groups.posts,
  "Other stations": groups.others,
};
if (weatherEnabled) overlays["Weather: Warnings"] = groups.weather;
L.control.layers(undefined, overlays).addTo(map);
```

- [ ] **Step 2: Render the alerts on each data change**

Add a `useEffect` keyed on `alerts` that repopulates the weather group (mirroring how the APRS groups are cleared/repopulated):
```typescript
useEffect(() => {
  const groups = layersRef.current;
  if (!groups) return;
  groups.weather.clearLayers();
  if (!weatherEnabled || alerts.features.length === 0) return;
  L.geoJSON(alerts as unknown as GeoJSON.GeoJsonObject, {
    style: (f) => alertStyle(f as unknown as WeatherFeature),
    onEachFeature: (f, layer) => {
      const p = (f as unknown as WeatherFeature).properties ?? {};
      const title = String(p.event ?? "Alert");
      const headline = String(p.headline ?? "");
      const expires = p.expires ? `<br/>Expires: ${new Date(String(p.expires)).toLocaleString()}` : "";
      layer.bindPopup(`<b>${title}</b><br/>${headline}${expires}`);
    },
  }).addTo(groups.weather);
}, [alerts, weatherEnabled]);
```
Add `weatherEnabled: boolean;` and `alerts: WeatherAlerts;` to `EventMapProps`.

- [ ] **Step 3: Wire `MapPanel` — call the hook, pass props, show a status chip**

In `MapPanel.tsx`: call `const weather = useEventWeather(netSlug, event.id, expanded && event.weather_enabled);` (only poll when the panel is expanded and the feature is enabled). Pass `weatherEnabled={event.weather_enabled}` and `alerts={weather.alerts}` to `<EventMap>`. Add a weather status chip next to the APRS badge, shown only when `event.weather_enabled`, reusing the badge style:
```tsx
{event.weather_enabled && (
  <span className={`px-2 py-0.5 rounded text-xs font-medium ${
    weather.status === "ok" ? "bg-success/15 text-success"
    : weather.status === "stale" ? "bg-warning/15 text-warning"
    : weather.status === "unavailable" ? "bg-danger/15 text-danger"
    : "bg-bg-elevated text-text-muted"}`} title={`NWS alerts: ${weather.status}`}>
    Wx {weather.status}
  </span>
)}
```

- [ ] **Step 4: Build check + commit**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — clean. (If `@types/geojson` isn't available so `GeoJSON.GeoJsonObject` doesn't resolve, cast via `alerts as unknown as object` in the `L.geoJSON` call and note it — Leaflet accepts the runtime object regardless.)
```bash
git add frontend/src/pages/events/EventMap.tsx frontend/src/pages/events/MapPanel.tsx
git commit -m "feat(weather): NWS warning polygons layer + status chip on the event map"
```

---

### Task 6: Animated radar loop + play/pause/slider control

**Files:**
- Modify: `frontend/src/pages/events/EventMap.tsx`
- Modify: `frontend/src/pages/events/MapPanel.tsx`

**Interfaces:**
- Consumes: `useRadarFrames` (Task 4); the layer groups + control from Task 5.
- Produces: a "Weather: Radar" `L.LayerGroup` holding one tile layer per RainViewer frame, animated by a play timer; a radar control (play/pause + frame slider + timestamp) surfaced in `MapPanel`. `EventMapProps` gains `frames: RadarFrame[]`, `radarTileUrl: (f: RadarFrame) => string`, `radarFrameIndex: number`, `onRadarFrameCount: (n: number) => void`. (The play/pause state lives in `MapPanel`, which drives `radarFrameIndex` — `EventMap` only shows the active frame.)

- [ ] **Step 1: Build the radar tile layers + show the active frame in `EventMap.tsx`**

Add a `weatherRadar` group to the groups object (created but not `addTo(map)`; `if (weatherEnabled) overlays["Weather: Radar"] = groups.weatherRadar;`). Maintain a ref of per-frame tile layers and show only the active index:
```typescript
const radarLayersRef = useRef<L.TileLayer[]>([]);

// Rebuild tile layers when the frame list changes.
useEffect(() => {
  const groups = layersRef.current;
  if (!groups) return;
  groups.weatherRadar.clearLayers();
  radarLayersRef.current = frames.map((f) =>
    L.tileLayer(radarTileUrl(f), { opacity: 0, zIndex: 250 }));
  radarLayersRef.current.forEach((tl) => tl.addTo(groups.weatherRadar));
  onRadarFrameCount(frames.length);
}, [frames, radarTileUrl]);

// Show only the active frame (opacity 0.6), hide the rest.
useEffect(() => {
  radarLayersRef.current.forEach((tl, i) =>
    tl.setOpacity(i === radarFrameIndex ? 0.6 : 0));
}, [radarFrameIndex, frames]);
```
Add the new props to `EventMapProps`.

- [ ] **Step 2: Drive the animation + control from `MapPanel.tsx`**

In `MapPanel.tsx`, add radar state and a play timer, and pass the props:
```tsx
const radar = useRadarFrames(expanded && event.weather_enabled);
const [frameCount, setFrameCount] = useState(0);
const [frameIndex, setFrameIndex] = useState(0);
const [playing, setPlaying] = useState(true);

useEffect(() => {
  if (!playing || frameCount === 0) return;
  const id = window.setInterval(() => setFrameIndex((i) => (i + 1) % frameCount), 700);
  return () => window.clearInterval(id);
}, [playing, frameCount]);

// pass to EventMap:
// frames={radar.frames} radarTileUrl={radar.tileUrl} radarPlaying={playing}
// radarFrameIndex={frameIndex} onRadarFrameCount={setFrameCount}
```
Render a small radar control (only when `event.weather_enabled && radar.frames.length > 0`):
```tsx
{event.weather_enabled && radar.frames.length > 0 && (
  <div className="flex items-center gap-2 text-xs mt-1">
    <button className="px-2 py-0.5 rounded bg-bg-elevated" onClick={() => setPlaying((p) => !p)}>
      {playing ? "⏸" : "▶"}
    </button>
    <input type="range" min={0} max={frameCount - 1} value={frameIndex}
      onChange={(e) => { setPlaying(false); setFrameIndex(Number(e.target.value)); }} className="flex-1" />
    <span className="text-text-muted tabular-nums">
      {radar.frames[frameIndex] ? new Date(radar.frames[frameIndex].time * 1000).toLocaleTimeString() : ""}
    </span>
  </div>
)}
```
(`frame.time` is a Unix epoch in seconds.)

- [ ] **Step 3: Build check + commit**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — clean.
```bash
git add frontend/src/pages/events/EventMap.tsx frontend/src/pages/events/MapPanel.tsx
git commit -m "feat(weather): animated RainViewer radar loop with play/pause + slider"
```

---

### Task 7: NetSettingsPage weather config section

**Files:**
- Modify: `frontend/src/pages/NetSettingsPage.tsx`

**Interfaces:**
- Consumes: the existing `ConfigField`/`SettingsSection` pattern + the section save flow (`handleSectionSave`, `setNetConfigBulk`).

- [ ] **Step 1: Add the field definitions + section**

Add a `WEATHER_FIELDS: ConfigField[]` array (mirroring `PAT_TRANSPORT_FIELDS`):
```typescript
const WEATHER_FIELDS: ConfigField[] = [
  { key: "weather.enabled", label: "Weather overlay", type: "boolean",
    helpText: "Show a radar loop + NWS warning polygons on the live event map." },
  { key: "weather.alert_states", label: "Alert states (optional)", type: "text",
    placeholder: '["MN","WI"]', mono: true,
    helpText: 'JSON list of 2-letter state codes for NWS alerts. Leave blank to auto-detect from the event location.',
    visibleWhen: (v) => v["weather.enabled"] === "true" },
  { key: "weather.nws_contact", label: "NWS contact (optional)", type: "text",
    placeholder: "you@example.com",
    helpText: "Contact included in the NWS API request identifier. Defaults to the net Winlink address.",
    visibleWhen: (v) => v["weather.enabled"] === "true" },
];
```
Render a `<SettingsSection title="Weather overlay" fields={WEATHER_FIELDS} ...>` in the page alongside the other sections (same `values={config}`, `savedValues={savedConfig}`, `onChange`, `onSave={handleSectionSave("weather")}`, `saving` wiring the existing sections use).

- [ ] **Step 2: Build check + commit**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — clean.
```bash
git add frontend/src/pages/NetSettingsPage.tsx
git commit -m "feat(weather): net-settings weather overlay config section"
```

---

### Task 8: Final verification sweep

- [ ] **Step 1: Full backend suite + lint**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — all pass.

- [ ] **Step 2: Frontend build**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — clean.

- [ ] **Step 3: Off-by-default regression check**

Confirm that with `weather.enabled` unset (default), event responses carry `weather_enabled: false`, `/weather` returns `status: "disabled"`, and no weather layers/toggles or backend NWS calls occur — the map behaves exactly as before SP6 (existing event/map tests unchanged).

- [ ] **Step 4: Manual smoke test (human checkpoint)**

With `./run-dev.sh`, an active event, and a net with `weather.enabled=true` (+ either `weather.alert_states` or event posts with coords): open the event map → the layer control shows "Weather: Radar" and "Weather: Warnings" → toggle Radar: the animated loop plays with a working play/pause + slider + timestamp → toggle Warnings: active NWS polygons render, colored by severity, with popups → the Wx status chip reflects ok/stale/unavailable → a viewer (not just NCS) sees the layers (read-only) → with `weather.enabled=false`, neither toggle appears. Note: needs live internet (RainViewer + api.weather.gov); pick a state with active alerts to see polygons, or trust the "no alerts" empty case.

---

## Notes for the implementer

- **No new dependencies** — do not add packages. httpx and Leaflet cover everything.
- **No background task** — the weather service caches on read (module-level `_CACHE`); do not add lifespan wiring or an asyncio task.
- **`weather.*` keys are not secret** — no `api_key`/`password`/`secret`/`token` fragment, so they're stored plaintext (correct — NWS/RainViewer need no key).
- **Radar is entirely client-side** — the browser fetches RainViewer's index + tiles directly; the backend never sees radar.
- **`frame.time` is Unix epoch seconds** — multiply by 1000 for `new Date(...)`.
- **Additive-only** — every weather failure path must leave the base map + APRS layers working; the weather groups start off (not `addTo(map)`), toggled by the operator.
