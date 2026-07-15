# Live Map + APRS Design

**Date:** 2026-07-15
**Status:** Approved for planning

## Context

Sub-project 2 of live events (see `2026-07-15-live-events-design.md`, shipped). Events
now have participants with a status lifecycle, posts with optional lat/lon, and a
cursor-polled dashboard. This sub-project adds live positions: event participants (and
optionally all nearby stations) on a Leaflet map, fed by APRS-IS, plus transmission of
event posts as APRS objects so stations on RF see them too.

Prior decisions from user Q&A:

- Direct APRS-IS connection (filtered feed), not the aprs.fi API.
- Both a dashboard map panel and a dedicated full-screen map view.
- Transmit posts as APRS objects (drops receive-only; verified login).
- Participants always shown; "other stations in range" is an opt-in layer per event.
- Always-on breadcrumb trails — movement is signal.
- Positions are in-memory only; no persistence, no replay.
- Per-station hide control (client-side) to manage clutter.

## Requirements

- Per-net APRS configuration; connection only while an APRS-enabled net has an active
  event.
- Live participant positions matched by base callsign across any SSID, with trails.
- Optional per-event "other stations in range" layer (dimmed, bounded).
- Posts with lat/lon beaconed as APRS objects under the net's callsign, behind an
  explicit per-event toggle (default off), with kill packets on removal/close.
- Map appears as a collapsible dashboard panel and a full-screen route; viewer role can
  see both; NCS controls the layers/beacon toggles.
- APRS connection status visible on the map; APRS failures never affect event operation.

## Architecture

New integration `backend/integrations/aprs/`, following the scanner integration's shape.
New dependency: **`aprslib`** (parsing all APRS position flavors — plain, compressed,
Mic-E, objects — and providing `aprslib.passcode()`).

### Passcode note

APRS-IS login is callsign + passcode, where the passcode is a public 15-bit XOR hash of
the base callsign (officially distributed "by software authors," long since public in
every open-source APRS client; `aprslib.passcode()` implements it). It is an
honor-system gate, not cryptography: nothing secret to store, computed at connect time.
The operator's obligation is that the configured callsign is genuinely theirs — the
config UI says so next to the field.

### Config (per net, via NetConfig + net settings UI)

| Key | Default | Notes |
|---|---|---|
| `aprs.enabled` | `false` | master switch |
| `aprs.callsign` | — | transmitting callsign; labeled "must be a callsign you are licensed to use" |
| `aprs.server` | `rotate.aprs2.net` | |
| `aprs.port` | `14580` | filtered feed port |

### Connection lifecycle

One asyncio client task per active event of an APRS-enabled net:

- started on event activation; started for already-active events at app boot (lifespan,
  like the scanner loop); stopped on event close.
- Verified login (`aprslib.passcode`), since we transmit objects.
- Reconnect with capped exponential backoff (max 5 min). Connection status
  (connected / reconnecting / error+reason / disabled) is exposed to the API for the
  map badge. A dropped connection never affects the event; positions go stale.

### Filter management

Server-side filter command, re-sent whenever inputs change:

- Always: buddy terms for every checked-in participant, wildcarded for SSIDs
  (`b/KE0XYZ*/W0NE*/...`).
- When the event's other-stations layer is on: plus `r/lat/lon/km` (center/radius from
  event settings; defaults derived from post locations when first enabled).
- Live updates: check-in service nudges the client via an in-process callback; a 60 s
  re-sync loop is the fallback.

## Position ingestion and store

- Every line through `aprslib.parse()`; keep packets that normalize to a lat/lon
  (position reports in any encoding; object/item reports from other stations).
  Malformed packets are dropped at debug level — never let one kill the client task.
- **Classification:** source callsign matched against event participants by base
  callsign (SSID stripped). Match → `participant` station (tagged with the
  participant's callsign); else `other` (only kept while the range layer is on;
  LRU-capped at 200 stations).
- **Store (per event, in-memory):** keyed by full station ID (`KE0XYZ-9`): latest
  point, bounded trail (deque of last 120 points), APRS symbol/comment, `last_heard`.
  Every accepted point gets a monotonic per-event `pos_seq`. The store dies with the
  event task or a server restart — deliberately unpersisted.

### Positions endpoint

`GET /api/nets/{slug}/events/{id}/positions?since={pos_seq}` — viewer role.

Returns the **complete current station roster** every poll (bounded: participants +
≤200 others; each with kind, participant callsign link, symbol, `last_heard`), where
each station carries only its points with `pos_seq > since`, plus `latest_pos_seq` and
`aprs_status`. `since=0` yields full trails; later polls yield deltas; the client
accumulates points but **replaces** the roster — so stations dropped by the store
(other-layer toggled off, LRU eviction) disappear from the map on the next poll.
Hiding markers is client-side view state only; the feed is never filtered by hides.

## Object beaconing (transmit)

- Event-level toggle **"Beacon posts as APRS objects"** (`aprs_beacon_posts`), default
  **off** — transmitting under the net's callsign is deliberate. NCS-only control on
  the posts panel.
- Objects = posts with lat/lon. Object name derived from post name: uppercased,
  non-alphanumerics stripped, truncated to 9 chars, uniquified with numeric suffix on
  collision. Shown read-only in the post UI ("on the air as RESTSTOP3").
- Beacon cycle: all active objects every 10 minutes, and immediately on create/change.
  Comment: `SkyNetControl event: <event name>`.
- Post renamed → kill packet for old name + fresh object. Post moved → updated object.
  Post deleted, toggle turned off, or event closed → kill packets for all announced
  objects (retried a few times; failures logged at warning — orphaned objects age out
  on other clients).
- Beaconer checks connection state (never queues blindly); after a reconnect the
  current object set is re-sent once on login.

## Data model delta

`events` gains (small migration; settings persist even though positions do not):

- `aprs_other_stations: bool, default false`
- `aprs_range_lat/lon: float, nullable`, `aprs_range_km: float, nullable`
- `aprs_beacon_posts: bool, default false`

No new tables.

New API surface besides `/positions`: the existing `PATCH /events/{id}` body gains the
four APRS event fields (`aprs_other_stations`, `aprs_range_lat`, `aprs_range_lon`,
`aprs_range_km`, `aprs_beacon_posts`), NCS-only as today; plus net-settings entries for
the four `aprs.*` NetConfig keys following the existing config patterns. Changing these
fields nudges the running client (filter re-send / beacon start-stop) the same way
check-ins do.

## Frontend

New components in `frontend/src/pages/events/`; existing `CheckInMap` untouched.

**`EventMap`** (shared Leaflet component; theme-aware CARTO tiles as in `CheckInMap`):

- Participant markers labeled `CALLSIGN-SSID`, colored by participant status (board
  palette), fading polyline trail per station.
- Post markers with distinct icon; show on-air object name when beaconing is enabled.
- Other-stations layer: dimmed gray markers.
- Staleness: markers dim after 15 min without a beacon; popup shows "last heard hh:mm".
- Hide: popup action per marker; hidden stations collect in a collapsible
  "Hidden (n)" list for unhiding. Pure client state.
- Leaflet layers control: participants / trails / posts / other stations. (Weather and
  radar overlays plug in here in sub-project 4.)

**Dashboard panel:** collapsible `EventMap` card on the event dashboard with an expand
link. **Full-screen view:** `/nets/{slug}/events/{id}/map` (viewer role) — map fills
the viewport; slim header with event name, APRS connection badge, back link.

**Data flow:** `useEventPositions(eventId)` hook — cursor-accumulate like
`useEventUpdates`, polling `/positions` every ~5 s only while a map is mounted and the
tab is visible. Collapsed panel costs nothing.

**NCS controls:** other-stations toggle + range on the map; beacon toggle on the posts
panel; all viewer-invisible.

## Error handling

- APRS-IS unreachable or login rejected → map badge shows reconnecting/error with
  reason; backoff capped at 5 min; event operation unaffected.
- APRS disabled or callsign unconfigured → map works (posts only) with a hint linking
  to net settings.
- Parse failures dropped (debug); per-line exception guard in the client loop.
- TX failure → retried next cycle; kill packets on close retried, then warned.

## Testing

Pytest, no live network:

- Filter-string building (buddy wildcards, range term, re-send on check-in).
- Classification: SSID stripping, participant vs other, LRU cap.
- Store cursor semantics: `since` deltas, trail bound, `pos_seq` monotonicity.
- Object lifecycle: name derivation/uniquing, create/update/rename→kill+new,
  close→kill-all — asserting exact packet strings.
- Client loop against an in-process fake APRS-IS server (asyncio streams): login line
  including computed passcode, filter command, reconnect/backoff, object re-send after
  reconnect.
- Routes: permission matrix, cursor behavior, cross-net 404 scoping (patterns from
  sub-project 1).
- Frontend: build gate + manual smoke.

## Non-goals (this sub-project)

- Weather/radar overlays (sub-project 4; `EventMap` layer control is the seam).
- Position persistence, track replay, coverage analysis.
- APRS messaging (two-way), igate/digipeater functionality.
- Positions on the weekly-net `CheckInMap`.
