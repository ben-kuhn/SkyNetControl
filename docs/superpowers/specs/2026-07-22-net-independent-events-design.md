# Net-Independent Events Design

**Date:** 2026-07-22
**Status:** Approved for planning

## Context

The live-events roadmap (SP1–SP6) built events as a **net-scoped** feature: an
`Event` belongs to a `Net` (required `net_id`), every route lives under
`/api/nets/{net_slug}/events/…`, permissions come from net membership
(`require_net_role`), and *all* event config (APRS, weather, PAT/Winlink,
delivery) resolves through `get_net_config(event.net_id, …)`.

Real-world testing surfaced the problem: a spontaneous emergency like a **skywarn
callout** has no associated *scheduled* net, so forcing an operator to enter a net
and create a session first is backwards. The decision: **events should be
first-class and net-independent.** Any operator builds one directly; it carries
its own ownership and config; nets keep their other jobs (schedule, roster,
reminders, async check-ins) but no longer own events.

### Binding decisions (from the Q&A)

1. **Fully net-independent.** Events are a unique top-level entity, not tied to a
   net at all. A net member who wants an event just builds one.
2. **Ownership + co-operators + public view.** Any approved operator creates an
   event and becomes its owner/NCS; they add co-operators (equal NCS control); the
   live view is public / link-shareable to non-logged-in people.
3. **Full capability parity** with today's net-scoped events (APRS map, weather
   overlay, check-ins, running log, Winlink messaging + forms, PAT transport,
   delivery).
4. **Config = global defaults + per-event overrides.** No per-operator "station"
   profile. Admins set global defaults once; each event overrides per-event.
   Identity keys default to the creator's callsign.
5. **Reset, don't migrate.** Existing (test-system) events are dropped for a clean
   slate; no data-preservation logic.
6. **Public directory + link.** Public events are both link-shareable and listed
   in a public "active events" directory.

## Architecture

Events become a first-class top-level entity. Nets are unchanged except that they
no longer own events.

### Data model

- **`events`**: `net_id` leaves the event workflow — nullable in the migration and
  removed from the permission/config path. `created_by` (existing) is the
  **owner**. Add `public_token` (unguessable, `secrets.token_urlsafe`, rotatable)
  and `visibility` (`private` | `public`, default `private`).
- **`event_operators`** (new): `(event_id, callsign, added_by, added_at)` —
  co-operators with equal NCS control. PK `(event_id, callsign)`.
- **`event_config`** (new): `(event_id, key, value)`, PK `(event_id, key)` —
  per-event config overrides, structurally identical to `net_config`.
- All existing event child tables (`event_posts`, `event_participants`,
  `event_log`, `event_messages`, `event_message_forms`) already key off
  `event_id` only — unchanged.
- **`pat_connection_sessions.net_id`** (currently required) → **nullable**, since
  an event-triggered PAT session now has no net. Sessions already carry a nullable
  `event_id`.

### Permissions

A new `require_event_role(min_role)` dependency replaces `require_net_role` on
event routes. Two levels:

- **CONTROL** (run the event as NCS — check-ins, log, messages, PAT connect, edit
  config, etc.): the **owner**, anyone in `event_operators`, or an **app admin**.
- **READ**: everyone with CONTROL, **plus** — when `visibility == public` — any
  authenticated user *and* anonymous visitors presenting a valid `public_token`. A
  `private` event is readable only by owner / operators / admin.

**Owner-only** actions (a notch above operators): `DELETE` the event, manage the
co-operator list, toggle `visibility`, rotate `public_token`, and transfer
ownership.

**Creation** requires only an approved (non-pending, non-deleted) logged-in
operator — no role or membership. The creator becomes owner.

**Anonymous public read** mirrors the existing public-net anonymous path (where
`NetContext.user` can be `None`): the read endpoints resolve the event by
`public_token` and grant READ if the event is public; they serve only read
surfaces (event detail, positions/map, weather, log, message *read*). Anonymous
visitors can never check in, post, or control — those require CONTROL.

### Config

New `event_config` table + `get_event_config(db, event_id, key, default=None)`
resolving **event override → global `AppConfig` default → hardcoded default**,
reusing the existing global-config machinery and the sensitive-key encryption
(`is_sensitive_key` + `secret_box`), so a per-event `pat_http_password` is
encrypted at rest exactly as net secrets are today. Plus `set_event_config` /
bulk, mirroring the net-config service.

**Identity-key defaults.** Pure global defaults don't fit station identity, so the
APRS callsign and `net_address` (the "from" identity) **default to the event
creator's callsign** (`created_by`); shared-infra keys (APRS-IS server/port,
NWS/weather host, PAT base URL) fall back to global `AppConfig`. All overridable
per-event.

**Integration re-sourcing** — each swaps `get_net_config(event.net_id, …)` for
`get_event_config(event.id, …)`:

- **APRS** (`aprs/manager.py`) — already keyed by `event_id`; reads `aprs.*` +
  the event's own range/beacon fields from event config.
- **Weather** (`weather/service.py`) — already keyed by `event_id`;
  `weather.enabled/alert_states/nws_contact` from event config.
- **Event messages / PAT** (`message_service.py`, `pat_config.py`, `pat_routes.py`)
  — `net_address`, `pat_transport_enabled`, `pat_http_*`, `pat_mailbox_path` from
  event config.
- **Delivery (the shared seam)** — `dispatch_delivery` today builds config from
  `net_id` and is used by *both* net workflows (roster/reminders) and event
  messages. Delivery-config resolution becomes **scope-aware**: net workflows keep
  net-config sourcing; event messages resolve from event config; both share the
  same backend send machinery. This is the trickiest cross-cutting change and gets
  its own task.

**Global-defaults admin UI.** The full config surface becomes global `AppConfig`
keys on the existing admin `/config` page (an admin sets `aprs.server`,
weather/NWS defaults, PAT/delivery defaults once); the per-event config UI
overrides them.

### Routes

Event endpoints move from `/api/nets/{slug}/events/…` to top-level `/api/events/…`:

- `POST /api/events` — create (any approved operator → owner).
- `GET /api/events` — "mine" (owner or co-operator).
- `GET /api/events/public` — active public directory.
- `GET /api/events/{id}` — READ-gated.
- Control actions + sub-resources (`positions`, `messages`, `weather`, `pat`,
  `participants`, `log`) under `/api/events/{id}/…` — CONTROL-gated (or READ for
  the read ones).
- Owner-only: `DELETE /api/events/{id}`, `POST`/`DELETE .../operators`,
  `PATCH .../visibility`, `POST .../token/rotate`, `POST .../transfer`.
- Public read: read endpoints accept an authorized user *or* a valid
  `public_token` for a `public` event. Frontend public page is `/e/{public_token}`.

Data flow is otherwise unchanged — the APRS/weather/PAT managers are already keyed
by `event_id`, so only config-sourcing and auth change under them.

## Migration (reset)

A single Alembic migration:

1. Create `event_config` and `event_operators`.
2. Add `public_token` + `visibility` to `events`.
3. Make `events.net_id` and `pat_connection_sessions.net_id` **nullable** (remove
   from the event flow).
4. **Delete all existing events and their child rows** (`event_posts`,
   `event_participants`, `event_log`, `event_messages`, `event_message_forms`,
   event-scoped `pat_connection_sessions`) for a clean slate — no data
   preservation.

Downgrade recreates the prior nullability constraints and drops the new
tables/columns (data loss on downgrade is acceptable — this is a reset).

## Build sequence

Two sub-projects (~15–20 tasks total); the backend spine must land atomically
before the UI is usable:

- **EP1 — Backend decoupling**: model (ownership + `event_operators` +
  `public_token`/`visibility` + `net_id` out of flow), `require_event_role`,
  `event_config` + `get_event_config` (incl. secret encryption + identity
  defaults), integration re-sourcing (APRS/weather/PAT/message + the scope-aware
  delivery seam), top-level `/api/events` routes (incl. public/anonymous read +
  the public directory), the reset migration.
- **EP2 — Frontend + public view**: top-level **Events** nav section (list/create
  from anywhere), per-event config UI, co-operator management, global-defaults
  admin UI, and the anonymous public event page (`/e/{token}`).

Each sub-project gets its own spec → plan → SDD cycle. This spec is the shared
design both draw from; EP1 and EP2 specs refine their halves.

## Error handling

- `require_event_role`: 404 on unknown event, 403 on insufficient role, anonymous
  access rejected on non-public events / control actions.
- `public_token` resolution: invalid/rotated token → 404 (indistinguishable from a
  missing event, no enumeration signal).
- Config resolution never raises for a missing key (falls through to global →
  default); sensitive keys never returned in plaintext by config reads.
- The scope-aware delivery change must not regress net-workflow delivery
  (roster/reminders): existing net delivery tests stay green.
- Integrations degrade exactly as today when their config is absent/disabled
  (APRS disabled, weather `disabled`, PAT off).

## Testing

Backend (real pytest coverage):

- Event CRUD + ownership; `event_operators` add/remove; transfer.
- Full **permission matrix**: owner / co-operator / admin / other-authenticated /
  anonymous × `private` / `public`, across create / read / control / owner-only.
- `require_event_role` (404/403/anonymous paths); `public_token` anonymous read +
  rotation revoking old links.
- `get_event_config` resolution (override → global → default), sensitive-key
  encryption, and the creator-callsign identity default.
- **Scope-aware delivery**: an event message resolves delivery from event config;
  a net roster/reminder still resolves from net config — both green.
- Each integration (APRS/weather/PAT/message) reads event config.
- The reset migration up/down on a scratch DB; existing-events deletion.

Frontend (EP2) is build-gated (no test harness), with a manual smoke checklist.

## Non-goals

- Per-operator "station" config profiles (chose global defaults + per-event
  overrides).
- Migrating existing events (reset instead).
- Changing nets' other features — schedule, roster, reminders, async check-ins,
  and their delivery config stay net-scoped.
- Real-time push (keep the existing cursor-polling).
- Event roles finer than owner / operator (no per-operator sub-scopes).
- A global event *history*/archive browser beyond the "mine" + active-public
  lists (future).
