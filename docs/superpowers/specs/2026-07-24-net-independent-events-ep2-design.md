# Net-Independent Events — EP2 (Frontend + Public View) Design

**Date:** 2026-07-24
**Status:** Approved for planning
**Depends on:** EP1 (backend decoupling), complete and merged to local main.
**Shared design:** `docs/superpowers/specs/2026-07-22-net-independent-events-design.md`
(this spec refines that document's EP2 half and adds the backend deltas EP2 needs).

## Context

EP1 made events a first-class, net-independent entity on the **backend**: dropped
`events.net_id`, added ownership + `event_operators` + `event_config` +
`public_token`/`visibility`, `require_event_role` auth, and moved every event
route to top-level `/api/events`. The **frontend was left untouched**, so today
it is *broken against merged main*: every function in `frontend/src/api/events.ts`
still calls the EP1-deleted `/nets/{slug}/events/…` routes and threads a
`netSlug`; the event pages live inside the `/nets/:slug/*` shell
(`CurrentNetProvider` + `RequireNetRole`); and "Events" is a per-net sidebar item.

**EP2 is therefore a migration, not just additive UI.** It repoints the whole
event frontend at `/api/events`, lifts it out of the net shell into a
net-independent home, swaps net-role gating for event-role gating, and adds the
new surfaces (per-event settings, co-operator management, global-defaults admin,
anonymous public page). It also carries a small set of **backend deltas** that the
public-view privacy model and the per-event config UI require.

### Binding decisions (from the Q&A)

1. **Events IA — top-level + net shortcut.** A top-level global "Events" section
   is the primary home; a "New Event" shortcut also appears inside a net, but the
   event it creates is *not* bound to that net (the link is just an entry point).
2. **Public page — map + log + weather, no messages.** An anonymous visitor at
   `/e/{public_token}` sees event detail, live map/positions, the running log, and
   the weather overlay — but **not** Winlink message content. Control actions
   (check-in, posting, PAT) are always operator-only. This is enforced at the API,
   not merely hidden in the UI.
3. **Event settings — own page, mirror `NetSettingsPage`.** A dedicated
   `/events/:id/settings` page reusing the existing `SettingsSection` +
   field-group components; owner-only sections gated for non-owner operators.
4. **PAT config UI (from the shared design's PAT notes).** `pat_http_base_url` is
   the primary field; the auth fields sit behind an **Advanced** toggle, off by
   default, at both event and net scope; secrets masked on read, never inherited
   across an ownership boundary; `pat_mailbox_path` retained alongside HTTP keys.

## Architecture

### Migration approach — reuse + re-plumb

Keep the working event components (dashboard, map, panels, hooks) and change only
their *plumbing*: the API URL prefix, the data/auth context they read from, and
the route mounting. This is lower-risk than a rewrite — the components already
work; EP1 only changed how they are addressed and authorized.

- **`api/events.ts`** — drop the `netSlug` parameter from every function; repoint
  from `/nets/${netSlug}/events/…` to `/api/events/…`. The attachment URL helper
  and the forms catalog/render URLs move to their event-scoped equivalents.
- **`EventProvider`** (new) replaces `CurrentNetProvider` for event pages. It
  fetches `GET /api/events/{id}` once and exposes `{ event, isControl }`
  (`is_control` comes from the EP1 response, which only includes `public_token` +
  `operators` for control users). All event child components/hooks read the event
  id and control flag from this context instead of `useParams().slug` + net role.
- **`RequireEventRole`** (new) replaces `RequireNetRole` for event routes: `min`
  is `"read"` or `"control"`. It relies on the backend gate (401/403/404 per EP1)
  and renders the appropriate not-authorized/redirect state, mirroring how
  `RequireNetRole` behaves today.
- The ~18 coupled files (event pages: `EventDashboardPage`, `EventMapPage`,
  `EventReportPage`, `EventsPage`; panels: `MessagesPanel`, `MessageComposer`,
  `MapPanel`, `PostsPanel`, `CheckInBar`, `PatConnectModal`, `FormCatalog`,
  `FormCompose`, `FormFillFrame`; hooks: `useEventUpdates`, `useEventPositions`,
  `useEventMessages`, `useEventWeather`, `usePatSession`, `useFormCompose`) lose
  their `netSlug` param and read from `EventProvider`.

### Navigation & routes

- **Top-level "Events"** nav item in the sidebar, visible to any approved
  (non-pending, non-deleted) logged-in operator — *not* admin-only. It sits in a
  new non-net, non-admin nav group (the sidebar currently has only per-net items
  and admin-only global items; Events is the first global-but-not-admin item).
- Event routes are lifted out of `/nets/:slug/*` to top level, in their own shell
  (an app shell without `CurrentNetProvider`):
  - `/events` — list ("mine": owner or co-operator) + create.
  - `/events/:id` — dashboard (READ-gated; control affordances shown when
    `isControl`).
  - `/events/:id/map`, `/events/:id/report` — as today, event-scoped.
  - `/events/:id/settings` — settings page (see below).
  - `/e/:token` — anonymous public page (no app shell, no auth; see below).
- **Net shortcut:** a "New Event" link on a net page (e.g. the net home/schedule
  header) that deep-links to `/events` create. The created event has no net
  association.
- **Removals:** the under-net event routes (`/nets/:slug/events…`) and the per-net
  "Events" sidebar item are deleted. The slug-less `/events` alias that currently
  redirects into a net is replaced by the real top-level route.

### Event settings page (`/events/:id/settings`)

Mirrors `NetSettingsPage`: stacked `SettingsSection`s, each with its own save.
Reuses the existing `ConfigField` / field-group rendering.

- **General** — event name, scheduled start/time. (control)
- **Visibility & public link** — `private`|`public` toggle; when public, show the
  shareable `/e/{public_token}` URL with copy + **Rotate link** (revokes the old
  token). (owner-only)
- **Config** — `net_address` (from-identity; defaults to creator callsign), APRS
  (`aprs.*`), weather (`weather.*`), and PAT: `pat_http_base_url` +
  `pat_mailbox_path` primary, with an **Advanced** toggle revealing
  `pat_http_auth_mode` + username/password/token. Secrets render masked (`***`).
  (control)
- **Co-operators** — list, add by callsign, remove. (owner-only)
- **Danger zone** — transfer ownership, delete event. (owner-only)

Owner-only sections are hidden (or disabled with an explanation) for non-owner
operators. The visibility/link, operators, transfer, and delete routes already
exist from EP1; the **Config** section needs the new event-config routes (below).

### Global-defaults admin UI (`ConfigPage`)

New `SettingsSection`s on the existing admin `/config` page, writing global
`AppConfig` via the existing `PUT /api/config/bulk` route (which already accepts
arbitrary keys and encrypts sensitive ones — **no backend change**):

- **PAT defaults** — `pat_http_base_url` + Advanced auth block.
- **APRS defaults** — `aprs.server`, `aprs.port`.
- **Weather defaults** — NWS/weather host + defaults.
- **Delivery defaults** — the shared delivery keys.

These populate the global fallback layer that a net-free event inherits when it
has no per-event override (`get_event_config` → global `AppConfig` → default).

### Public page (`/e/:token`)

A lean, read-only shell with no sidebar/app chrome.

**Bootstrap (token → event).** The public URL carries a `public_token`, but every
EP1 read route is keyed by `event_id` (with `?token=` for anonymous grant). EP1
has no token→event resolver, and the `GET /public` directory can't substitute (it
omits tokens and lists only `ACTIVE` events, so a scheduled-but-not-yet-active or
closed public event wouldn't resolve). So EP2 adds **`GET /api/events/by-token/{token}`**
(anonymous; returns the READ snapshot for a `public` event whose token matches,
404 otherwise with no enumeration signal). The page calls it once to get the event
id + detail, then polls the existing sub-resource READ routes by id with `?token=`.

It shows: event detail (name/status/time), the **live map + positions**, the
**running log**, and the **weather overlay** — reusing the same read components and
the same cursor-polling hooks as the dashboard, passing `?token=`. **No messages
panel.** When the token is invalid/rotated or the event is private, the routes
return 404 and the page shows a generic "event not found".

## Backend deltas (EP2 is not pure frontend)

1. **Add event-config routes** — `GET /api/events/{id}/config` (CONTROL-gated;
   masks sensitive values as `***`, exactly like `GET /nets/{slug}/config`) and
   `PUT /api/events/{id}/config/bulk` (CONTROL-gated; encrypts sensitive values on
   write via `secret_box`, like the net-config bulk route). This closes EP1's
   known gap (per-event overrides were unreachable over HTTP). Reuses
   `event_config_service` + `is_sensitive_key`.
2. **Messages → CONTROL.** Change `GET /api/events/{id}/messages` and the
   attachment download (`…/attachments/{id}`) from `require_event_role(READ)` to
   `require_event_role(CONTROL)`, so anonymous public-token holders (and public
   authenticated non-operators) cannot fetch message content or attachments.
3. **Audit the READ snapshot/detail.** Verify `GET /api/events/{id}` (`_snapshot`)
   and the `updates` snapshot carry log/positions/weather/detail but **never**
   embedded message content; if any message data rides the READ path, remove it.
   This is what actually enforces "no public messages". (`_snapshot` already omits
   `public_token`/`operators` for non-control viewers — confirm messages are
   likewise absent.)
4. **Add `GET /api/events/by-token/{token}`** — anonymous token→event resolver for
   the public page (returns the non-control READ snapshot for a `public` event,
   404 otherwise). Reuses `_snapshot` with an anonymous/non-control context so it
   emits exactly what an anonymous viewer may see (no token/operators, no
   messages).
5. **Global defaults** — no backend change (the `/config` bulk route already
   accepts these keys).

## Error handling

- Event pages rely on EP1's gate: 401 (unauthenticated on control), 403
  (authenticated non-control on private), 404 (anonymous / bad token / unknown).
  `RequireEventRole` and the public page render matching states; a rotated token
  yields a generic not-found with no enumeration signal.
- Config reads never return secrets (masked `***`); config writes encrypt
  sensitive keys. The Advanced PAT block being collapsed never sends or clears the
  masked secret unless the operator edits it.
- Live-data hooks keep their existing "hold last-known on a failed poll" behavior
  (never blank the dashboard/public page mid-event).
- A non-owner operator hitting an owner-only action gets the same 403 the backend
  already returns; the UI hides those controls proactively.

## Testing

- **Backend deltas** get real pytest coverage: event-config route masking +
  sensitive-key encryption round-trip + CONTROL gating; messages/attachments now
  return 403 for authenticated non-control and 404 for anonymous-token on a public
  event; the READ snapshot/detail excludes message content; the `by-token` resolver
  returns a snapshot for a public event, 404 for a private event / wrong / rotated
  token, and never emits token/operators/messages.
- **Frontend** is build-gated (no test harness): `tsc` typecheck + `vite build`
  must pass. Plus a **manual smoke checklist**: create an event from the top-level
  Events section and from the net shortcut; run it (check-in, log, map, messages,
  PAT) as owner; add a co-operator and confirm they get control; a non-owner
  operator can't see owner-only sections; toggle public + open `/e/{token}` in a
  logged-out browser (map/log/weather visible, no messages); rotate the token and
  confirm the old link 404s; set global defaults in `/config` and confirm a new
  event inherits them; set a per-event override and confirm it wins.

## Non-goals

- **Real-time push.** Keep the existing cursor-polling for all event live data
  (log/positions/messages) — including the public page; no WebSocket/SSE push for
  event data. A push channel (with anonymous-token socket auth + rotation) would be
  its own follow-on sub-project spanning net surfaces too.
- Per-operator "station" config profiles (global defaults + per-event overrides
  instead).
- An event history/archive browser beyond "mine" + the active public directory.
- Any change to nets' own features (schedule, roster, reminders, async check-ins).
- Migrating old net-scoped event data (EP1 already reset it).
