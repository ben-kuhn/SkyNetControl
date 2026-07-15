# Live Events Design

**Date:** 2026-07-15
**Status:** Approved for planning

## Context

SkyNetControl currently manages asynchronous weekly Winlink nets: sessions are generated
from a season schedule, a background scanner imports check-ins from the PAT mailbox, and
net control reviews them after the fact. This design adds **live (real-time) events** —
the first of four sub-projects toward live-net support:

1. **Live event core** (this spec) — event lifecycle, check-in/check-out, per-participant
   notes, running net log, live-update transport.
2. Live map + APRS — position ingestion, participants on the existing Leaflet map.
3. Winlink traffic during events — fast inbound handling plus send/reply/forms.
4. Weather overlay — emergency-net weather layer on the map.

The module built here is intended to be re-wired later for live *scheduled* nets, which
will differ in some respects (e.g., a per-check-in comments/traffic field instead of the
unified event log).

## Requirements

- Two event types: **public service** and **emergency**.
- **NCS-driven**: users with the `net_control` role (or global admins) operate the event.
  Logged-in net members get a read-only live view. There is no participant self-service
  of any kind — no self check-in/out, no self-notes.
- **Any callsign** can be checked in; events have no membership concept. Name is
  prefilled from the existing callbook integration. Home-QTH lookup is not useful —
  participant location comes from the event (posts or freeform).
- **Posts/assignments are per-event and optional**: public service events typically
  pre-define structured posts (which may carry lat/lon); weather/emergency events
  typically use the freeform location field, which can change on the fly. Event type
  sets the expectation, not a rule — both mechanisms are always available.
- **Status lifecycle** per participant (not binary in/out): checked in → at post /
  en route → out of service → back in service → checked out, every transition
  timestamped. Re-check-in after checkout is supported (multiple stints).
- **One unified event log**: automatic system entries (check-ins, status changes),
  NCS freeform entries, and participant-tagged notes in a single chronological stream.
  Per-participant notes are the log filtered by callsign. Log entries can be pinned to
  surface sticky facts on a participant ("has medical training").
- **Lifecycle**: draft → active → closed, with a one-step create-and-activate path for
  emergencies. Reopen from closed is allowed (mistaken closes happen during real ops).
- **Multiple concurrent NCS operators**, with every write attributed to the
  authenticated operator.
- **After-action (minimal)**: closed events remain viewable read-only; print-friendly
  view and CSV export of participants (per-stint times, total hours) and the full log.
  ICS-214 generation is a future enhancement.

## Approach

Considered three approaches:

1. **New `events` module + cursor polling** — chosen.
2. Extend `NetSession`/`CheckIn` — rejected: `CheckIn` is shaped around the async
   Winlink parse flow (`raw_message_id`, `parse_status`, `timing_status`) and the
   seasons/scheduling machinery doesn't fit one-off events; coupling the two flows
   makes both harder to evolve.
3. New module + SSE push — rejected for v1: connection management, reconnect/catch-up
   logic, and proxy buffering are real complexity for latency headroom a human-paced
   voice net doesn't need. The polling payload is designed so SSE can be added later
   as a pure transport upgrade.

## Data model

New module `backend/modules/events/`, four tables, all net-scoped.

### `events`

| Column | Notes |
|---|---|
| `id` | PK |
| `net_id` | FK → nets.id |
| `name` | |
| `description` | nullable |
| `event_type` | enum: `public_service` \| `emergency` |
| `status` | enum: `draft` \| `active` \| `closed` |
| `scheduled_start` | nullable — planned events |
| `activated_at`, `closed_at` | nullable timestamps |
| `created_by` | callsign |

Lifecycle `draft → active → closed`, plus `closed → active` (reopen). Create-and-activate
in one step is an API convenience, not a distinct state. Closed events stay readable
forever; no event deletion.

### `event_posts`

| Column | Notes |
|---|---|
| `id` | PK |
| `event_id` | FK |
| `name` | unique per event |
| `description` | nullable |
| `lat`, `lon` | nullable — enables map placement without APRS (sub-project 2) |

Creatable at draft time or on the fly during an active event; deletable only while no
participant is assigned.

### `event_participants`

| Column | Notes |
|---|---|
| `id` | PK |
| `event_id` | FK; unique `(event_id, callsign)` |
| `callsign` | plain string, uppercased — no FK to users; events have no membership |
| `name` | prefilled from callbook, editable |
| `post_id` | FK → event_posts, nullable |
| `location` | free-text, nullable |
| `current_status` | enum: `checked_in` \| `at_post` \| `en_route` \| `out_of_service` \| `checked_out` |
| `checked_in_at`, `checked_out_at` | latest transition times; full history lives in the log |

One row per callsign per event. Re-check-in after checkout transitions the same row back
to `checked_in`; the log preserves each stint, and hours are computed by summing stints
from the log (each stint runs from a `checked_in` system entry to the next `checked_out`).

Transition rules: any status except `checked_out` may move directly to any other status
("back in service" after `out_of_service` is simply a transition to `checked_in`,
`at_post`, or `en_route`). From `checked_out`, the only legal transition is back to
`checked_in` (re-check-in). Anything else is a 422.

### `event_log`

| Column | Notes |
|---|---|
| `id` | PK |
| `event_id` | FK |
| `seq` | monotonic per event — the polling cursor |
| `entry_type` | enum: `system` \| `note` \| `participant_note` |
| `callsign` | nullable — the participant the entry concerns |
| `actor` | callsign of who caused/wrote the entry |
| `message` | |
| `pinned` | bool, default false |
| `created_at` | |

`system` entries are written automatically for check-ins, status changes, post/location
changes, and event open/close/reopen. Log entries are immutable (no edit/delete — it is
an operational record; typos get a correcting entry). Only `pinned` is mutable.

## API

All under `/api/nets/{slug}/events`. Writes require the `net_control` role on the net or
global admin; reads require authenticated net membership.

### Event lifecycle

- `GET /events` — list all (client-side filtering per house style; no pagination)
- `POST /events` — create draft; optional `activate: true` for the one-step emergency path
- `GET /events/{id}` — full snapshot: event, posts, participants, log
- `PATCH /events/{id}` — edit name/description/scheduled_start (draft or active)
- `POST /events/{id}/activate` / `close` / `reopen`

### Posts

- `POST /events/{id}/posts`
- `PATCH /events/{id}/posts/{post_id}`
- `DELETE /events/{id}/posts/{post_id}` — 409 if any participant is assigned

### Participants

- `POST /events/{id}/participants` — check-in: callsign + optional post/location/name.
  Server calls the callbook for name prefill when name not supplied. If the callsign
  already exists checked-out, transitions the existing row back to `checked_in`.
- `PATCH /events/{id}/participants/{pid}` — change status, post, location, name; every
  change auto-writes a `system` log entry.
- No DELETE — a mistaken check-in is checked out with a correcting note.

### Log

- `POST /events/{id}/log` — NCS note; optional `callsign` tag (becomes
  `participant_note`), optional `pinned`.
- `PATCH /events/{id}/log/{entry_id}` — pin/unpin only; message immutable.

### Live updates

- `GET /events/{id}/updates?since={seq}` — returns log entries with `seq > since`,
  **plus** the complete current participant/post/event state, plus `latest_seq`.
  State is small (dozens of rows), so sending it whole every poll avoids
  delta-reconstruction bugs; only the log is cursored.

Attribution always comes from the authenticated user — no client-supplied `actor`.

## Frontend

New routes `/nets/{slug}/events` (list) and `/nets/{slug}/events/{id}` (live dashboard).
Plain React state + `fetch()` per house style; no new libraries.

**Events list page** — table with type/status/date, client-side filter. `net_control`
sees "New event" (name, type, description, scheduled start; "Create & activate now"
button) and "Activate" on drafts.

**Event dashboard**, three zones:

1. **Check-in bar** (top, NCS only) — autofocused callsign input + post/location fields;
   Enter checks someone in. Keyboard-first: this is the hot path during a net.
2. **Participant board** (main) — row per participant: callsign, name, status badge,
   post/assignment, time in. One-click status changes from the row. Clicking a row opens
   a side panel with that participant's filtered log history, pinned notes, and
   "add note".
3. **Net log** (right column) — reverse-chronological unified timeline with an NCS
   compose box. System entries visually muted; pinned entries surfaced at the top of the
   participant panel.

**Live updates:** a `useEventUpdates(eventId)` hook polls `/updates?since=` every ~3s
while the event is active, pausing when the tab is hidden (Page Visibility API).
Read-only viewers see the same dashboard minus all write affordances. Closed events
render the same view statically — that is the archive view.

**Posts management** — a tab/panel on the event page, usable in draft (public service
pre-planning) and while active.

**Export** — print-friendly view of participants (per-stint times, total hours computed
from log stints) + full log; CSV download of both tables.

## Permissions, concurrency, error handling

**Permissions** — enforced server-side via the existing role-check dependency pattern.
There is no self-check-in endpoint at all, so participant self-service is unreachable
even by direct API calls.

**Concurrency** — all writes are row-inserts or single-row updates; last-write-wins is
acceptable for this domain. The one real race — two operators checking in the same
callsign — is guarded by the `(event_id, callsign)` unique constraint; the loser gets a
clean 409 and the UI shows "already checked in" and refreshes. `seq` assignment is a
per-event counter incremented inside the write transaction (SQLite serializes writes;
on PostgreSQL a `SELECT … FOR UPDATE` on the event row keeps it correct).

**Error handling**

- Callbook down → check-in proceeds with blank name, non-blocking toast.
- Poll failure → keep last-known state on screen, subtle "reconnecting…" indicator,
  retry with backoff. Never blank the dashboard mid-event.
- Invalid status transitions (e.g., `at_post` on a `checked_out` participant) → 422
  with a clear message; they must be checked in again first.
- Writes against a closed event → 409 (except `reopen`).

## Testing

Pytest, matching existing module test patterns:

- **Model/service:** event lifecycle transitions (including reopen and invalid
  transitions), participant status machine, re-check-in stint handling, `seq`
  monotonicity under concurrent inserts, hours computation from log stints.
- **API:** permission matrix (viewer / `net_control` / admin / non-member), duplicate
  check-in 409, closed-event 409, callbook-failure fallback, `updates?since=` cursor
  semantics (empty delta, catch-up, full state).
- **Frontend:** light — manual verification via dev server for the dashboard flow,
  consistent with the rest of the project.

## Non-goals (this sub-project)

- APRS positions and the live map layer (sub-project 2 — `event_posts.lat/lon` is
  deliberately in place for it)
- Winlink traffic in/out during events (sub-project 3)
- Weather overlays (sub-project 4)
- Live *scheduled* nets and their per-check-in comments/traffic field (future re-wiring
  of this module)
- ICS-214 report generation (future; the data model already captures what it needs)
- Participant self-check-in, log entry editing, event deletion
