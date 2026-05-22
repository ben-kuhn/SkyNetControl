# Public Check-Ins Design

## Overview

Make the `/checkins` page publicly viewable for completed sessions, so rosters delivered via email/groups.io can link recipients directly to the check-ins table + map without requiring a login. Replaces the abandoned "shareable public map page" idea — since the map already lives inside CheckInsPage, we just need to lift the auth gate for completed sessions and update the roster's link target.

This also deletes the now-unused `/api/roster/session/{id}/geojson` endpoint and renames the roster's `map_url` column to `session_url` to reflect its new target.

## Backend

### `GET /api/checkins/session/{session_id}` — optional auth

Switch this route from `get_current_user` to an optional-auth pattern.

- If the caller is authenticated, behavior is unchanged: returns all check-ins for the session regardless of session status.
- If the caller is **not** authenticated:
  - If the session's `status` is `completed`, return the check-ins.
  - Otherwise, return 404 (do not distinguish "session doesn't exist" from "session not yet public" — public callers see both as 404).

Add a dependency helper `get_optional_user(db, request)` if one doesn't already exist; it returns `User | None` based on the cookie. Reuse the existing JWT-decoding logic in `get_current_user` but treat missing/invalid tokens as `None` instead of 401.

### Delete `/api/roster/session/{id}/geojson`

Remove:
- The route handler in `backend/modules/roster/routes.py` (`geojson_route` around line 297) and its `get_session_geojson_service` import.
- The service function `get_session_geojson` in `backend/modules/roster/service.py`.
- The corresponding test(s) in `tests/test_roster_routes.py` that exercised this endpoint.

### Rename `map_url` → `session_url`

This is a rename across model, service, route, default template, and tests:

1. **Model** (`backend/modules/roster/models.py:54`): rename column.
2. **Alembic migration** (new): rename the column on `roster_logs`. Also `UPDATE` any existing `roster_templates` rows whose template body contains `{{ map_url }}` or `Check-in map:` to use `{{ session_url }}` / `Check-in details:`.
3. **Service** (`backend/modules/roster/service.py`):
   - In `build_roster_context` (~line 224): replace the `map_url` variable with `session_url`. Build it as `f"{settings.app_base_url}/checkins?session={net_session.id}"`.
   - **Always include** `session_url` in the context, not gated on `has_gps` — the page is useful for the table even without map pins.
   - In `generate_draft` (~line 310): pass `session_url=context["session_url"]` to the `RosterLog` constructor.
4. **Route** (`backend/modules/roster/routes.py:88`): update the dict key `map_url` → `session_url` in the response builder.
5. **Default template body**: the seed lives in `alembic/versions/f5b2383f6dd3_add_roster_tables.py` and is the sole source for fresh installs. That migration is immutable history — do **not** edit it. The new rename migration handles correctness for both fresh and existing databases by running an `UPDATE` after the column rename:
   - Replace `{{ map_url }}` with `{{ session_url }}` in all `roster_templates` rows.
   - Replace `Check-in map:` with `Check-in details:` in the same rows.
   For a brand-new DB, Alembic runs migrations in order: the old seed inserts a row with the old placeholder, then the rename migration updates it. Net effect is correct.

### Settings dependency

`backend/config.py` already defines `app_base_url` (default `http://localhost:8000`). Import `settings` from `backend.config` in the service module if not already imported. Production deployments override via `SKYNET_APP_BASE_URL`.

## Frontend

### Route structure

Today the `/checkins` route lives inside the `<ProtectedRoute><AppShell /></ProtectedRoute>` group in `frontend/src/App.tsx`. Lift it out so it renders for any user.

- Remove the inner `<ProtectedRoute minRole={…}>` wrapper around `<CheckInsPage />`.
- Move the `/checkins` route to a sibling position that still uses `AppShell` but doesn't require auth.

Concretely, restructure to:

```tsx
{/* Public routes wrapped in AppShell */}
<Route element={<AppShell />}>
  <Route path="/checkins" element={<CheckInsPage />} />
</Route>

{/* Authenticated routes wrapped in ProtectedRoute + AppShell */}
<Route element={<ProtectedRoute><AppShell /></ProtectedRoute>}>
  <Route path="/schedule" ... />
  <Route path="/members" ... />
  …rest…
</Route>
```

This means `AppShell` now renders for both authenticated and anonymous users on `/checkins`.

### AppShell

`frontend/src/layouts/AppShell.tsx` already pulls `user` from `useAuth`. Today it renders the mobile top bar with `{user?.callsign}` — that already optional-chains, so anonymous users will see an empty callsign slot. Replace with a "Sign in" link (anchored to `/login`) when `user` is null. No other changes needed.

### Sidebar

`frontend/src/layouts/Sidebar.tsx`:

- `navItems.filter(...)` already evaluates to `[]` when `user` is null because every item's `minRole` array excludes anonymous viewers. The sidebar will render with zero nav items.
- Replace the existing profile/logout footer block with a single "Sign in" link to `/login` when `user` is null.
- `frontend/src/layouts/MobileMenu.tsx` follows the same pattern (also pulls `user, logout` from `useAuth`). Apply the equivalent change: hide nav items and show a "Sign in" link instead of the logout button when `user` is null.

### CheckInsPage

`frontend/src/pages/CheckInsPage.tsx`:

- **Session selector dropdown**: when `user` is null, filter the available sessions to only those with `status === "completed"`. The `Session` type in `frontend/src/types/index.ts` already includes `status`, so client-side filtering after `fetchRecentSessions()` is sufficient.
- **Action controls** (Scan / Add check-in / Approve session / per-row Edit): gate on `user != null` in addition to existing role checks. Hide them entirely for anonymous viewers.
- **404 handling**: when `fetchSessionCheckins(sessionId)` returns 404, render a friendly state — "This session is not yet available for public viewing." — instead of the table.
- **No other layout changes**: the map pane, table, and detail interactions remain identical.

### Login redirect on link click

Anonymous users clicking the "Sign in" link from the sidebar end up at `/login`. After login the user lands at `/schedule` per existing flow. Acceptable — no special return-to-checkins flow needed.

## Data flow

1. Net control approves a roster → status transitions to APPROVED → `mark_sent` triggers delivery → recipients get the body containing `Check-in details: https://app.example.com/checkins?session=42`.
2. Recipient clicks link → browser navigates to public `/checkins?session=42` page.
3. Frontend mounts CheckInsPage anonymously → calls `fetchSessionCheckins(42)` → backend confirms `session.status == COMPLETED` → returns data → page renders table + map.
4. Recipient sees check-ins; no Scan/Add/Edit/Approve controls visible; sidebar shows only "Sign in".

## Testing

### Backend

- `tests/test_checkin_routes.py`:
  - Add `test_get_checkins_session_public_completed` — no auth, session `COMPLETED`, expects 200 + data.
  - Add `test_get_checkins_session_public_not_completed` — no auth, session not completed, expects 404.
  - Add `test_get_checkins_session_public_unknown` — no auth, no such session, expects 404 (same as not-completed; do not leak existence).
  - Update or add tests covering the authenticated case still returns data for any session status.
- `tests/test_roster_routes.py`:
  - Delete the `test_geojson_route` test (and any related tests).
- `tests/test_roster_service.py`:
  - Rename references from `map_url` → `session_url`.
  - Update `test_build_roster_context_map_url` (line 285) and `test_generate_draft_sets_map_url_when_gps` (line 426) to use the new name; the latter should be renamed since the URL is no longer gated on GPS — instead test that the URL is always present and matches `{app_base_url}/checkins?session={id}`.
- `tests/test_roster_models.py`: rename `map_url` → `session_url`.

### Frontend

- `tsc --noEmit` passes after type and prop adjustments.
- Manual verification:
  - In a private/incognito window, visit `/checkins?session=<completed-session-id>` → page renders, no edit controls.
  - Same window, visit `/checkins?session=<scheduled-session-id>` → "not yet available" message.
  - Same window, click "Sign in" → lands at /login.
  - Logged in, visit `/checkins` → identical behavior to today (selector, controls all visible).

## Migration considerations

- Alembic migration runs on deploy and renames the column; sqlite users may need an `ALTER TABLE` workaround (Alembic batch mode). Confirm the existing migrations use batch mode if SQLite is in scope (it is — SQLite is the default).
- Existing `roster_templates` rows containing the old placeholder are updated in the same migration. New deployments inherit the corrected seed.

## Out of scope

- A standalone full-bleed public map page (the existing CheckInsPage already shows the map).
- Public access to `/api/checkins/members`, `/api/checkins/by-callsign`, `/api/checkins/modes`, or scan/manual/edit endpoints — those remain authenticated.
- Iframe-friendly embeds, custom branding, or open-graph card metadata for shared links.
- Re-introducing the geojson endpoint or a separate GeoJSON download.
