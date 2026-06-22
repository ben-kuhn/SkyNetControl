# Multi-Net Support — Design

**Status:** Approved for planning
**Date:** 2026-06-22

## Summary

SkyNetControl currently models a single weekly net. This change makes "net" a
first-class container that owns all net-specific data — seasons, sessions,
check-ins, members, templates, integrations — so one deployment can manage
multiple nets independently (e.g., a Winlink net and a packet net). Admins
create nets and assign per-net permissions. The active net is selected via a
slug in the URL (`/nets/<slug>/...`); a localStorage hint redirects slug-less
URLs to the user's last net.

## Goals

- One deployment hosts N independent nets with no data leakage between them.
- Each net has its own integrations (PAT mailbox, scanner, delivery channels),
  templates, activity library, and members directory.
- Users have per-net roles (`net_control`, `viewer`); `admin` is a global
  flag granting cross-net access plus net-management ability.
- URLs are shareable per net (`/nets/<slug>/checkins`) so external links can
  target a specific net.
- Existing single-net data upgrades cleanly into a "Default Net" with no
  operator action.

## Non-goals

- Per-net OIDC providers / SMTP / global system config — these remain shared.
- Cross-net aggregate dashboards.
- Per-net templates flavored by net kind (deferred — current seed templates
  are Winlink-tailored; a separate spec/branch handles "live nets" features
  including kind-flavored template defaults).
- Bulk operations across nets.
- Downgrade safety. Pre-alpha; the migration is one-way.

## Data partitioning

| Resource | Scope |
|---|---|
| Seasons, sessions, check-ins, raw messages | per-net |
| Roster + reminder logs and templates | per-net |
| Activity library + tags + chat sessions | per-net |
| Members directory | per-net (one row per (net, callsign)) |
| Per-net integrations (winlink address, default NCO, PAT mailbox, scanner, delivery, callbook) | per-net |
| Notifications | per-net (inherit via session/activity FK) |
| Users / OIDC accounts | global (one login, per-net roles) |
| SMTP, OIDC providers, JWT, forms-library cache, registration toggle | global |

## Data model

### New tables

```
nets
  id              INTEGER PK
  slug            VARCHAR(64)  UNIQUE   -- URL segment: 1-64 chars, [a-z0-9-], must start and end alphanumeric, no consecutive hyphens
  name            VARCHAR(255)
  is_public       BOOLEAN  default TRUE
  created_at      TIMESTAMP

net_memberships
  user_callsign   VARCHAR(20)  FK→users.callsign   PK part 1
  net_id          INTEGER      FK→nets.id          PK part 2
  role            ENUM(net_control, viewer)
  created_at      TIMESTAMP

net_config              -- per-net AppConfig replacement
  net_id          INTEGER  PK part 1  FK→nets.id
  key             VARCHAR  PK part 2
  value           TEXT
  updated_at      TIMESTAMP
```

### Changes to existing tables

| Table | Change |
|---|---|
| `net_seasons`, `roster_templates`, `reminder_templates`, `activities`, `activity_tags`, `members` | Add `net_id INTEGER NOT NULL FK→nets.id` |
| `net_sessions`, `check_ins`, `roster_logs`, `reminder_logs`, `activity_usages`, `chat_sessions`, `raw_messages`, `notifications` | No direct column; inherit net via parent FK (queries join through `net_sessions` / `activities`) |
| `users` | Drop `role`. Add `is_admin: BOOLEAN default FALSE`. |
| `personal_access_tokens` | Add `net_id INTEGER NULL FK→nets.id`; null = global admin token |
| `audit_log` | Add `net_id INTEGER NULL` for net-scoped events |

Uniqueness adjustments:

- `roster_templates.name`, `reminder_templates.name`, `activity_tags.name`:
  `UNIQUE(name)` → `UNIQUE(net_id, name)`.
- `members`: PK changes from `(callsign)` to `(net_id, callsign)`.

### Config split

Keys staying in `app_config` (system-level): SMTP, OIDC providers, JWT,
OAuth registration toggle, forms-library cache, Claude API key,
`setup_completed` sentinel.

Keys moving to `net_config` (per-net): `net_address`, `default_net_control`,
`pat_mailbox_path`, `scanner.*`, `delivery.*`, `callbook.*`.

## Permissions

- `User.is_admin: bool` is global. Admins act in any net and manage nets,
  users, and system config.
- `net_memberships.role ∈ {net_control, viewer}` is per-net. No row =
  no access (net-scoped pages return 404 to non-members).
- Admin promotions, role changes, and net-membership add/remove bump
  `User.token_version` so existing JWTs lose access immediately.

### JWT

Carries `callsign`, `tv`, and `is_admin`. Per-net role is resolved at request
time by joining `net_memberships` against the slug in the URL.

### FastAPI dependencies

- `require_admin()` — global check for system-level routes.
- `require_net_role(slug, min_role)` — reads slug from path, resolves caller's
  membership (or `is_admin` bypass), compares to `min_role`.
- Public net-scoped routes (e.g., the public check-ins page) check the net's
  `is_public` flag and bypass auth.

### PATs

- New `net_id` column. `NULL` = admin-only token.
- Non-null `net_id` = net-scoped; existing scopes (`schedule:write`,
  `checkins:write`, etc.) apply within that net.
- Issuing a net-scoped PAT requires the caller's current membership in that
  net at the appropriate role; `validate_scopes_for_role` is reused.
- New global scopes: `nets:read`, `nets:write`, `nets:members:write`.

## API surface

### Net-scoped routes gain a slug segment

| Old | New |
|---|---|
| `/api/schedule/...` | `/api/nets/{slug}/schedule/...` |
| `/api/checkins/...` | `/api/nets/{slug}/checkins/...` |
| `/api/roster/...` | `/api/nets/{slug}/roster/...` |
| `/api/reminders/...` | `/api/nets/{slug}/reminders/...` |
| `/api/activities/...` | `/api/nets/{slug}/activities/...` |
| `/api/members/...` | `/api/nets/{slug}/members/...` |
| `/api/notifications/...` | `/api/nets/{slug}/notifications/...` |
| `/api/scanner/...` | `/api/nets/{slug}/scanner/...` |
| `/api/forms/render` (per-session data) | `/api/nets/{slug}/forms/...` |

### System routes stay slug-less

`/api/auth/*`, `/api/users/*`, `/api/config/*` (system keys only),
`/api/setup/*`, `/api/recovery/*`, `/api/oauth/*`, `/api/smtp/*`,
`/api/forms/library` (global forms cache), `/api/version`, `/api/health`.

### New endpoints

```
GET    /api/nets                              -- list nets visible to caller
POST   /api/nets                              -- admin: create
GET    /api/nets/{slug}                       -- net detail
PATCH  /api/nets/{slug}                       -- admin: rename, change slug, toggle is_public
DELETE /api/nets/{slug}                       -- admin: delete (cascades)
GET    /api/nets/{slug}/members               -- list memberships
PUT    /api/nets/{slug}/members/{callsign}    -- admin: add/change role
DELETE /api/nets/{slug}/members/{callsign}    -- admin: remove
GET    /api/nets/{slug}/config                -- per-net config keys
PUT    /api/nets/{slug}/config/{key}          -- per-net config write
```

### Public endpoints

Check-in read endpoints honor the net's `is_public` flag. No "default public
net" — visitors must know the slug.

## Frontend

### Routing

All net-scoped pages under `/nets/:slug/...`:

- `/nets/:slug/schedule`
- `/nets/:slug/checkins` (public if net's `is_public`)
- `/nets/:slug/checkins/map`
- `/nets/:slug/members`
- `/nets/:slug/reminders`
- `/nets/:slug/roster`
- `/nets/:slug/activities`
- `/nets/:slug/settings` (per-net config, integrations, public toggle —
  net_control+)

System routes stay slug-less: `/users`, `/config`, `/profile`, `/login`,
`/setup`, `/recovery`.

New admin page `/nets` — list, create, rename, delete, manage memberships.

### Slug-less landing

Visiting `/`, `/schedule`, `/checkins`, etc. redirects to the equivalent
`/nets/<slug>/...` resolving slug in this order:

1. `localStorage["lastNetSlug"]` if the user still has access to it
2. The user's alphabetically-first accessible net
3. Unauthenticated visitor on a public route → `/login`
4. User has zero nets → "No nets available — contact your admin" page

### App shell

- New `NetPicker` component in the top nav. Visible only when the URL has a
  slug. Dropdown of nets the user can access; selecting one navigates to the
  same page under the new slug and writes `localStorage`.
- `useCurrentNet()` reads slug from `useParams`, fetches net metadata once,
  caches via React Query / context.

### API client

Each net-scoped fetcher takes a `slug` parameter and prefixes the URL. Hooks
(`useSchedule`, `useCheckins`, etc.) read the current slug from
`useCurrentNet()` so individual pages don't pass it explicitly.

### Auth context

`User` drops `role`, gains `isAdmin: boolean` and
`nets: Array<{ slug, name, role }>`. `ProtectedRoute`'s `minRole` prop becomes
either a global check (admin) or a per-net check that resolves against the
current slug.

## Migration

Single Alembic revision, applied in order:

1. Create `nets`, `net_memberships`, `net_config` tables.
2. Insert one `nets` row:
   - `name`: from `app_config["net_address"]` if set, else `"Default Net"`.
   - `slug`: derived from name, lowercase + alphanumeric/hyphens, fallback `"default"`.
   - `is_public`: `TRUE`.
3. Add `net_id` to `net_seasons`, `roster_templates`, `reminder_templates`,
   `activities`, `activity_tags`, `members`. Backfill to the new net id.
   `ALTER` to `NOT NULL`.
4. Replace template-name / activity-tag-name unique constraints with
   `UNIQUE(net_id, name)`. Change `members` PK to `(net_id, callsign)`.
5. Add `users.is_admin`; backfill `is_admin = (role = 'admin')`.
6. Backfill `net_memberships`: every non-admin, non-pending, non-deleted user
   gets a row for the default net with their current `role`. Admins do not
   need rows.
7. Drop `users.role`.
8. Add nullable `net_id` to `personal_access_tokens`. Backfill: PATs with any
   non-admin scope get the default net id; admin-only PATs stay `NULL`.
9. Add nullable `net_id` to `audit_log`. Best-effort backfill where the
   event is net-scoped; unmappable events stay `NULL`.
10. Move per-net `app_config` rows (`net_address`, `default_net_control`,
    `pat_mailbox_path`, `scanner.*`, `delivery.*`, `callbook.*`) into
    `net_config` for the default net id; delete from `app_config`.

Downgrade body raises `NotImplementedError`. Pre-alpha — no rollback story.

## New-net creation behavior

When an admin creates a net via `POST /api/nets`:

1. Insert `nets` row (name, slug, `is_public=true`).
2. Run the existing roster + reminder template seeds against the new net id.
   Integrations (scanner, PAT mailbox, delivery) start disabled.
3. Schedule, check-ins, and members start empty. (No `net_memberships` row
   is required for the creating admin — admins see all nets in the picker
   regardless.)

Note: seed templates are Winlink-flavored; a follow-up spec will add
typed/kind-aware template defaults.

## Implementation scope

### In scope this branch

- Schema + migration above.
- New `nets` module: models, service, routes.
- Admin `/nets` UI: list / create / rename / delete / manage memberships.
- Per-net `/nets/:slug/settings` UI (config, integrations, public toggle).
- Frontend route restructure + `NetPicker` + `useCurrentNet`.
- All existing routes ported to per-net slug.
- PAT issuance flow updated (select a net, or "admin-global").
- Tests: per-net isolation in each module, slug-less-redirect, public/private
  gating, PAT scope-vs-net validation.

### Deferred

- Per-net-kind template defaults (covered by "live nets" branch).
- Cross-net aggregate dashboards.
- Per-net OIDC provider isolation.
- Bulk operations across nets.
- Migration downgrade.

## Touchpoints (rough file list)

- Backend new: `backend/modules/nets/{models,service,routes}.py`.
- Backend changes: every `backend/modules/*/routes.py` (slug param),
  `backend/auth/dependencies.py` (`require_net_role`),
  `backend/auth/scopes.py` (new admin scopes),
  `backend/auth/pat_models.py` + `pat_service.py` + `pat_routes.py`
  (net_id field), `backend/config_mgmt/service.py` (`net_config` reader/
  writer), new Alembic revision under `alembic/versions/`.
- Frontend new: `pages/NetsAdminPage.tsx`, `pages/NetSettingsPage.tsx`,
  `components/NetPicker.tsx`, `context/CurrentNetContext.tsx` /
  `hooks/useCurrentNet.ts`.
- Frontend changes: `App.tsx` (route restructure), every existing page's
  API hook signature, `AuthContext` (drop `role`, add `isAdmin` + `nets`).
