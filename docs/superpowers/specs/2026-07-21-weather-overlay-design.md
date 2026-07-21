# Weather Overlay Design (SP6)

**Date:** 2026-07-21
**Status:** Approved for planning

## Context

SP6 is the last planned piece of the live-events roadmap (SP1 live event core, SP2
live map + APRS, SP3 event Winlink messaging, SP4a/b Winlink forms, SP5 PAT
transport — all merged to local main). It adds a **weather overlay** to the live
event map for emergency/skywarn nets, fulfilling the original events requirement:
"for emergency nets, it would be awesome if we could overlay weather information."

The map already exists: `frontend/src/pages/events/EventMap.tsx` (Leaflet 1.9.4,
CARTO base tiles) renders APRS participants/trails/posts/others as four
`L.LayerGroup`s toggled by a native `L.control.layers()` control, fed by the
cursor-polling `useEventPositions` hook (`/events/{id}/positions`). SP6 adds two
more toggleable layers to that control.

### Binding decisions (from the SP6 Q&A)

1. **Two overlays:** precipitation **radar** (RainViewer) + active **NWS
   watch/warning polygons** (`api.weather.gov`) — the skywarn-standard combo,
   both free/US, no API key.
2. **Radar is an animated loop** — cycle the last ~2h of RainViewer frames with
   play/pause + a time slider (shows storm motion), not a single frozen frame.
3. **Radar is client-side; warnings are backend-proxied.** Radar image tiles come
   straight from RainViewer's CDN (no key, CORS-fine) — proxying them would be
   pure overhead. NWS warnings go through a backend module for a polite
   User-Agent, shared caching, area filtering, and testability (the frontend has
   no test harness).
4. **Gated per-net** by a `weather.enabled` config flag; layers appear in the map
   layer control, off by default.

## Architecture

Two independent overlays, both event-scoped, both running **only while the map is
mounted + the tab is visible** (matching `useEventPositions`).

### Radar (client-side, no backend)

- On map mount the frontend fetches RainViewer's public index
  (`https://api.rainviewer.com/public/weather-maps.json`) — a small JSON list of
  the last ~2h of radar frame timestamps + the tile-host path. No key; CORS-fine.
- A **radar animator** builds a Leaflet tile layer per frame (tile URL
  `{host}{frame.path}/{size}/{z}/{x}/{y}/{color}/{options}.png`, size 256) into a
  "Weather: Radar" `L.LayerGroup`, and cycles them (show one, hide the rest) on a
  play timer. A React control overlaid on the map provides **play/pause, a frame
  slider, and the current frame's timestamp**. Layer opacity ≈0.6 so the base map
  shows through.
- The index is refetched every few minutes to stay current.

### Warnings (backend-proxied)

New module `backend/integrations/weather/`, following the callbook/PAT
integration shape (pure client + thin service) rather than the heavier APRS
per-event background-task manager — alerts change slowly, so a cache-on-read is
enough (no long-lived task).

- **`client.py`** — `fetch_active_alerts(states: list[str], *, user_agent: str)
  -> dict`: calls `https://api.weather.gov/alerts/active?area={ST}` per state via
  `httpx` with a descriptive `User-Agent` (NWS requires one; format
  `SkyNetControl (<contact>)`), merges the FeatureCollections, dedups by alert
  `id`. Also `lookup_state(lat, lon, *, user_agent) -> str | None` via
  `api.weather.gov/points/{lat},{lon}` for area derivation. Typed error
  `WeatherUnavailable` on transport/HTTP failure. Pure transport; tested with
  `httpx.MockTransport`.
- **`service.py`** — `get_event_alerts(db, event_id) -> {alerts, updated_at,
  status}` with a small in-memory **TTL cache** (≈60s) keyed by the resolved
  area, so many operators polling share one upstream fetch. Behavior:
  - `weather.enabled` off → `status: "disabled"`, empty alerts, no fetch.
  - No resolvable area (no configured states, no event location) → `status:
    "no_area"`, empty.
  - Cache fresh → serve it (`status: "ok"`).
  - Cache stale/miss → refetch; on success `status: "ok"`; on
    `WeatherUnavailable` serve the last good cache with `status: "stale"`, or
    `status: "unavailable"` + empty if nothing cached.
- **Area derivation** (which alerts are relevant to the event):
  1. If `weather.alert_states` (per-net JSON list of 2-letter codes) is set, use
     it — explicit, no lookup.
  2. Otherwise derive from the event's location (the posts centroid the map
     already centers on) via one cached `lookup_state` call → that state.
  3. If neither exists → `status: "no_area"`.
- **Route** — `GET /api/nets/{slug}/events/{id}/weather` (VIEWER-gated, like
  `/positions`) → `{ alerts: <GeoJSON FeatureCollection>, updated_at, status }`.
  Best-effort: NWS problems surface as `status`, never a 500.

### Frontend rendering

- **Radar:** `useRadarFrames` hook (fetch index, expose frames + host) + the
  animator/control described above, in/around `EventMap.tsx`.
- **Warnings:** `useEventWeather` hook (mirrors `useEventPositions`, ~60s
  interval) polls `/weather`; renders the GeoJSON via `L.geoJSON` into a "Weather:
  Warnings" `L.LayerGroup`, **styled by severity** (tornado/severe-warning red,
  watches amber, advisories muted — from the alert `severity`/`event` fields),
  each polygon with a popup (event, headline, severity, expires). A small status
  chip shows `unavailable`/`stale` (mirroring the APRS status badge), keeping the
  last polygons on a transient failure.
- **Layer control:** the two weather entries join the existing
  participants/trails/posts/others control, **off by default**; present only when
  weather is enabled.
- **Viewer gating signal:** the map is viewer-visible but net config is
  admin-only, so the event detail response (already fetched for the dashboard)
  gains a viewer-readable `weather_enabled` boolean that gates whether the weather
  toggles render. Radar uses it directly (client-side); warnings additionally read
  `/weather`'s `status`.

## Config

Per-net `weather.*` keys (no secrets — nothing to encrypt):

| Key | Type | Description |
|-----|------|-------------|
| `weather.enabled` | `"true"`/`"false"` | Default `"false"`. Master switch; off = no layers, no backend fetch. |
| `weather.alert_states` | JSON list of 2-letter codes (optional) | Explicit NWS coverage area. If unset, derived from the event location. |
| `weather.nws_contact` | string (optional) | Contact embedded in the NWS `User-Agent`. Defaults to `net_address` if unset. |

Set from a "Weather overlay" section on `NetSettingsPage`.

## Error handling & gating

- Every weather failure is contained: a failed radar index or warnings fetch never
  disturbs the base map or APRS layers — the weather layers are purely additive.
- `weather.enabled=false` ⇒ no toggles render, no backend fetch, zero cost.
- Radar index unavailable → the radar layer simply shows nothing + a small note;
  no map disruption.
- Warnings `status` of `unavailable`/`stale` → status chip; last-known polygons
  retained on transient failure.
- Available on all event types (the use case is emergency/skywarn, but
  public-service nets near weather can toggle it too).

## Testing

- **Backend** (real coverage via `httpx.MockTransport`, the codebase pattern):
  - NWS client: fetch alerts per state, merge + dedup by `id`, `lookup_state`
    area derivation, `WeatherUnavailable` on transport/HTTP failure.
  - Service: TTL cache (shared fetch), stale-while-error, and every `status`
    value (`ok`/`stale`/`unavailable`/`disabled`/`no_area`).
  - Route: enabled/disabled/no_area paths, VIEWER gate, cross-net 404, response
    shape.
  - Event detail: `weather_enabled` reflects config (VIEWER-readable).
- **Frontend:** build-gated (no test harness) + manual visual check of the radar
  animator (play/pause/slider) and the warning polygons/popups.

## Non-goals (this spec)

- Proxying radar tiles through the backend (client-side by design).
- Historical/archived weather (live only).
- Non-NWS providers and non-US warnings (RainViewer radar is global; NWS warnings
  are US-only — fine for a US net).
- Lightning, satellite, wind, or point-forecast layers (future).
- Per-alert audio/desktop notifications or alerting logic.
- Persisting weather data (the service cache is in-memory, like the APRS store).
- Auto-enabling by `event_type` (available on all types; operator toggles).
