# Public Check-Ins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `/checkins` page viewable by anonymous users for completed sessions, delete the unused roster geojson endpoint, and rename `roster_logs.map_url` → `session_url` so embedded roster links go to the full check-ins page.

**Architecture:** Backend grows an optional-auth dependency, the session-check-ins endpoint applies a status filter for anonymous callers, the geojson endpoint/service are removed, and the roster module renames its URL field across model, migration, service, route, and tests. Frontend lifts `/checkins` out of the `ProtectedRoute` group and gracefully handles anonymous viewers in AppShell, Sidebar, MobileMenu, and the CheckInsPage itself (read-only mode + 404 state).

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, React/TypeScript.

---

## File Structure

**Modified files:**

| File | Change |
|------|--------|
| `backend/auth/dependencies.py` | Add `get_optional_user` |
| `backend/modules/checkins/routes.py` | Session-checkins route uses optional auth + status filter |
| `backend/modules/roster/routes.py` | Remove geojson route, remove map_url from response, rename to session_url |
| `backend/modules/roster/service.py` | Remove `get_session_geojson`, build `session_url` with `app_base_url`, rename field references |
| `backend/modules/roster/models.py` | Rename column `map_url` → `session_url` |
| `tests/test_checkin_routes.py` | Public-access tests; existing tests adjusted if needed |
| `tests/test_roster_routes.py` | Delete geojson test |
| `tests/test_roster_service.py` | Rename references; update GPS-gating test |
| `tests/test_roster_models.py` | Rename references |
| `frontend/src/layouts/AppShell.tsx` | Show "Sign in" link instead of callsign chip for null user |
| `frontend/src/layouts/Sidebar.tsx` | Show "Sign in" footer when null |
| `frontend/src/layouts/MobileMenu.tsx` | Show "Sign in" instead of logout when null |
| `frontend/src/App.tsx` | Move `/checkins` out of `ProtectedRoute` group |
| `frontend/src/pages/CheckInsPage.tsx` | Filter sessions when anonymous, hide controls, render 404 state |

**New files:**

| File | Responsibility |
|------|---------------|
| `alembic/versions/<rev>_rename_map_url_to_session_url.py` | Column rename + UPDATE existing roster_templates rows |

---

### Task 1: `get_optional_user` dependency

**Files:**
- Modify: `backend/auth/dependencies.py`
- Test: `tests/test_auth_routes.py` (or a new `tests/test_auth_dependencies.py` if you prefer; the plan uses the former for proximity)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auth_routes.py`:

```python
@pytest.mark.asyncio
async def test_get_optional_user_returns_none_without_cookie(test_app):
    """get_optional_user returns None when no auth cookie is sent."""
    from backend.auth.dependencies import get_optional_user

    # Build a fake Request with no cookies/headers
    from fastapi import Request
    from starlette.requests import Request as StarletteRequest

    async def receive():
        return {"type": "http.request"}

    scope = {
        "type": "http",
        "headers": [],
        "app": test_app,
        "state": {},
    }
    req = Request(scope, receive)

    with test_app.state.session_factory() as session:
        result = get_optional_user(
            request=req,
            access_token=None,
            authorization=None,
            db=session,
            app_settings=test_app.state.settings,
        )
    assert result is None


@pytest.mark.asyncio
async def test_get_optional_user_returns_user_with_valid_cookie(test_app, test_settings):
    """get_optional_user returns the user when a valid cookie is present."""
    from backend.auth.dependencies import get_optional_user
    from backend.auth.service import create_access_token
    from fastapi import Request

    # Seed a viewer user
    with test_app.state.session_factory() as session:
        user = User(
            callsign="W0OPT",
            oidc_subject="opt|sub",
            name="Opt",
            role=UserRole.VIEWER,
        )
        session.add(user)
        session.commit()

    token = create_access_token("W0OPT", "viewer", test_settings)
    scope = {"type": "http", "headers": [], "app": test_app, "state": {}}

    async def receive():
        return {"type": "http.request"}

    req = Request(scope, receive)
    with test_app.state.session_factory() as session:
        result = get_optional_user(
            request=req,
            access_token=token,
            authorization=None,
            db=session,
            app_settings=test_settings,
        )
    assert result is not None
    assert result.callsign == "W0OPT"


@pytest.mark.asyncio
async def test_get_optional_user_returns_none_with_invalid_cookie(test_app, test_settings):
    """get_optional_user returns None when the cookie is malformed/expired."""
    from backend.auth.dependencies import get_optional_user
    from fastapi import Request

    scope = {"type": "http", "headers": [], "app": test_app, "state": {}}

    async def receive():
        return {"type": "http.request"}

    req = Request(scope, receive)
    with test_app.state.session_factory() as session:
        result = get_optional_user(
            request=req,
            access_token="not-a-real-jwt",
            authorization=None,
            db=session,
            app_settings=test_settings,
        )
    assert result is None
```

If `User` and `UserRole` are not already imported at the top of the file, add them: `from backend.auth.models import User, UserRole`.

- [ ] **Step 2: Run tests and verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_auth_routes.py::test_get_optional_user_returns_none_without_cookie tests/test_auth_routes.py::test_get_optional_user_returns_user_with_valid_cookie tests/test_auth_routes.py::test_get_optional_user_returns_none_with_invalid_cookie -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL with `ImportError: cannot import name 'get_optional_user'`.

- [ ] **Step 3: Add the helper**

In `backend/auth/dependencies.py`, after `get_current_user` (around line 64), add:

```python
def get_optional_user(
    request: Request,
    access_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db_session),
    app_settings: Settings = Depends(get_settings),
) -> User | None:
    """Like get_current_user, but returns None instead of raising 401.

    Used by routes that have both authenticated and anonymous behavior.
    DELETED users are treated as anonymous.
    """
    try:
        return get_current_user(
            request=request,
            access_token=access_token,
            authorization=authorization,
            db=db,
            app_settings=app_settings,
        )
    except HTTPException:
        return None
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_auth_routes.py::test_get_optional_user_returns_none_without_cookie tests/test_auth_routes.py::test_get_optional_user_returns_user_with_valid_cookie tests/test_auth_routes.py::test_get_optional_user_returns_none_with_invalid_cookie -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/auth/dependencies.py tests/test_auth_routes.py
git commit -m "feat: add get_optional_user dependency"
```

---

### Task 2: Public check-ins endpoint

**Files:**
- Modify: `backend/modules/checkins/routes.py`
- Test: `tests/test_checkin_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_checkin_routes.py`:

```python
@pytest.mark.asyncio
async def test_get_session_checkins_public_completed(test_client, test_settings, db_setup):
    """Anonymous viewers can fetch check-ins for a COMPLETED session."""
    from backend.modules.checkins.models import CheckIn, ParseStatus, TimingStatus
    from backend.modules.schedule.models import SessionStatus

    with db_setup() as session:
        net_session = session.query(NetSession).first()
        net_session.status = SessionStatus.COMPLETED
        checkin = CheckIn(
            session_id=net_session.id,
            callsign="W0NE",
            name="Test",
            mode="Winlink",
            parse_status=ParseStatus.AUTO,
            timing_status=TimingStatus.ON_TIME,
            is_new_member=False,
        )
        session.add(checkin)
        session.commit()
        net_session_id = net_session.id

    resp = await test_client.get(f"/api/checkins/session/{net_session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["callsign"] == "W0NE"


@pytest.mark.asyncio
async def test_get_session_checkins_public_not_completed_returns_404(test_client, db_setup):
    """Anonymous viewers cannot fetch check-ins for a non-COMPLETED session."""
    with db_setup() as session:
        net_session = session.query(NetSession).first()
        # Default fixture creates session with SCHEDULED status (see db_setup)
        net_session_id = net_session.id

    resp = await test_client.get(f"/api/checkins/session/{net_session_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_session_checkins_public_unknown_session_returns_404(test_client):
    """Anonymous viewers see 404 for unknown sessions (same as not-completed)."""
    resp = await test_client.get("/api/checkins/session/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_session_checkins_authenticated_sees_non_completed(test_client, test_settings, db_setup):
    """Authenticated callers can fetch check-ins for any session status."""
    # db_setup leaves the default session as SCHEDULED (not COMPLETED).
    with db_setup() as session:
        net_session = session.query(NetSession).first()
        net_session_id = net_session.id

    token = create_access_token("W0NE", "admin", test_settings)
    resp = await test_client.get(
        f"/api/checkins/session/{net_session_id}",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert resp.json() == []  # no check-ins, but route reachable
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `nix-shell --run "python -m pytest tests/test_checkin_routes.py::test_get_session_checkins_public_completed tests/test_checkin_routes.py::test_get_session_checkins_public_not_completed_returns_404 tests/test_checkin_routes.py::test_get_session_checkins_public_unknown_session_returns_404 tests/test_checkin_routes.py::test_get_session_checkins_authenticated_sees_non_completed -v" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: FAIL with 401 on the public ones (currently the route requires auth).

- [ ] **Step 3: Update the route**

In `backend/modules/checkins/routes.py`:

Add `get_optional_user` to the imports from `backend.auth.dependencies` (already imports `get_current_user`, `get_db_session`, `require_role` — append the new helper).

Add `SessionStatus` import: at the top of the file, find the existing `from backend.modules.schedule.models import NetSession` line and change to:

```python
from backend.modules.schedule.models import NetSession, SessionStatus
```

Replace the existing `get_session_checkins_route` (lines 121-128) with:

```python
@checkins_router.get("/session/{session_id}")
async def get_session_checkins_route(
    session_id: int,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db_session),
):
    net_session = db.get(NetSession, session_id)
    if net_session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if user is None and net_session.status != SessionStatus.COMPLETED:
        raise HTTPException(status_code=404, detail="Session not found")

    checkins = get_checkins_for_session(db, session_id)
    return [_checkin_to_response(c) for c in checkins]
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `nix-shell --run "python -m pytest tests/test_checkin_routes.py -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All checkin route tests pass (including the four new ones).

If any existing test fails because it called the endpoint without auth, update it to either authenticate or set the session to COMPLETED. The existing `test_get_checkins_for_session` should still pass since it authenticates.

- [ ] **Step 5: Run the full backend suite**

Run: `nix-shell --run "python -m pytest tests/ -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/modules/checkins/routes.py tests/test_checkin_routes.py
git commit -m "feat: make session check-ins endpoint optionally public"
```

---

### Task 3: Delete geojson endpoint

**Files:**
- Modify: `backend/modules/roster/routes.py`
- Modify: `backend/modules/roster/service.py`
- Modify: `tests/test_roster_routes.py`

- [ ] **Step 1: Find existing test(s) for the geojson route**

Run: `grep -n "geojson\|get_session_geojson" tests/test_roster_routes.py`

Note the test name(s) — typically `test_geojson_route` (around line 337).

- [ ] **Step 2: Delete the route handler**

In `backend/modules/roster/routes.py`:

Remove the import `get_session_geojson as get_session_geojson_service` from the service import block (around line 15).

Remove the route handler around lines 294–302:

```python
# --- GeoJSON route (no auth) ---


@roster_router.get("/session/{session_id}/geojson")
async def geojson_route(
    session_id: int,
    db: Session = Depends(get_db_session),
):
    return get_session_geojson_service(db, session_id)
```

- [ ] **Step 3: Delete the service helper**

In `backend/modules/roster/service.py`, remove the function `get_session_geojson` (starts around line 487). Read the function body and delete it entirely, including any helper imports it pulled in if those imports are now unused (verify with grep before removing).

- [ ] **Step 4: Delete the test(s)**

In `tests/test_roster_routes.py`, delete the `test_geojson_route` test and any related GeoJSON tests. If the deletion leaves an unused import (e.g., a `GeoJSON` schema import) at the top of the file, remove that too.

- [ ] **Step 5: Run the roster test suite**

Run: `nix-shell --run "python -m pytest tests/test_roster_routes.py tests/test_roster_service.py -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All remaining tests pass (the geojson-related ones are gone, others unaffected).

- [ ] **Step 6: Commit**

```bash
git add backend/modules/roster/routes.py backend/modules/roster/service.py tests/test_roster_routes.py
git commit -m "feat: remove unused roster geojson endpoint"
```

---

### Task 4: Rename `map_url` → `session_url`

**Files:**
- Modify: `backend/modules/roster/models.py`
- Create: `alembic/versions/<new_rev>_rename_map_url_to_session_url.py`
- Modify: `backend/modules/roster/service.py`
- Modify: `backend/modules/roster/routes.py`
- Modify: `tests/test_roster_models.py`
- Modify: `tests/test_roster_service.py`

- [ ] **Step 1: Generate the Alembic migration scaffold**

Run: `nix-shell --run "cd /home/ku0hn/dev/SkyNetControl && alembic revision -m 'rename map_url to session_url'" /home/ku0hn/dev/SkyNetControl/shell.nix`

Note the path/filename of the generated migration (e.g., `alembic/versions/abc123_rename_map_url_to_session_url.py`).

- [ ] **Step 2: Edit the migration to rename the column and update existing template rows**

Open the generated file and replace its body with:

```python
"""rename map_url to session_url

Revision ID: <auto>
Revises: <auto>
Create Date: <auto>
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "<auto>"
down_revision = "<auto>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("roster_logs") as batch_op:
        batch_op.alter_column("map_url", new_column_name="session_url")

    # Update existing roster_templates to use the new placeholder name and label
    op.execute(
        "UPDATE roster_templates "
        "SET footer_template = REPLACE(footer_template, '{{ map_url }}', '{{ session_url }}')"
    )
    op.execute(
        "UPDATE roster_templates "
        "SET footer_template = REPLACE(footer_template, 'Check-in map:', 'Check-in details:')"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE roster_templates "
        "SET footer_template = REPLACE(footer_template, 'Check-in details:', 'Check-in map:')"
    )
    op.execute(
        "UPDATE roster_templates "
        "SET footer_template = REPLACE(footer_template, '{{ session_url }}', '{{ map_url }}')"
    )

    with op.batch_alter_table("roster_logs") as batch_op:
        batch_op.alter_column("session_url", new_column_name="map_url")
```

(Keep the `<auto>` placeholder values that Alembic populated in the scaffold — only the function bodies change.)

- [ ] **Step 3: Update the model**

In `backend/modules/roster/models.py:54`, change:

```python
    map_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
```

To:

```python
    session_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
```

- [ ] **Step 4: Update the service**

In `backend/modules/roster/service.py`:

Add (if not already present) `from backend.config import settings` near the top of the imports.

Replace the `map_url` block in `build_roster_context` (around lines 224–226 and the `"map_url"` dict key around line 239) with:

```python
    session_url = f"{settings.app_base_url}/checkins?session={net_session.id}"
```

And in the returned dict (around line 239), replace:

```python
        "map_url": map_url,
```

With:

```python
        "session_url": session_url,
```

Remove any remaining references to `has_gps` that exist only to gate `map_url`. The `has_gps` variable can stay if it's used elsewhere — verify with grep.

In `generate_draft` (around line 310), change:

```python
        map_url=context["map_url"] or None,
```

To:

```python
        session_url=context["session_url"] or None,
```

- [ ] **Step 5: Update the route response**

In `backend/modules/roster/routes.py:88`, change:

```python
        "map_url": log.map_url,
```

To:

```python
        "session_url": log.session_url,
```

- [ ] **Step 6: Update the tests**

In `tests/test_roster_models.py:95`, change `log.map_url` → `log.session_url`.

In `tests/test_roster_service.py`:
- Line 285: rename `test_build_roster_context_map_url` → `test_build_roster_context_session_url` and update body to assert `ctx["session_url"]` starts with the configured `app_base_url` and contains `/checkins?session=`.
- Line 303: assert `ctx["session_url"] != ""`.
- Line 315: change the inline template to use `{{ session_url }}` and label `Check-in details:`.
- Line 333: change `"map_url": ""` → `"session_url": ""`.
- Line 426: rename `test_generate_draft_sets_map_url_when_gps` → `test_generate_draft_sets_session_url`. The new behavior is: `session_url` is set on every draft, not just when GPS is present. Update the test body accordingly:

```python
def test_generate_draft_sets_session_url(db, season_and_sessions, default_template):
    _, session1, _, _ = season_and_sessions
    log = generate_draft(db, session1.id)
    assert log is not None
    assert log.session_url is not None
    assert log.session_url != ""
    assert "/checkins?session=" in log.session_url
```

Any test that previously relied on `map_url` being empty when there's no GPS data should be deleted or rewritten — the new design always sets the URL.

- [ ] **Step 7: Run the roster tests**

Run: `nix-shell --run "python -m pytest tests/test_roster_models.py tests/test_roster_service.py tests/test_roster_routes.py -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass.

- [ ] **Step 8: Run the full backend suite**

Run: `nix-shell --run "python -m pytest tests/ -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All pass.

- [ ] **Step 9: Verify migration runs forward and back**

Run: `nix-shell --run "alembic upgrade head" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: Migration applies cleanly.

Run: `nix-shell --run "alembic downgrade -1 && alembic upgrade head" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: Migration round-trips cleanly with no errors.

(If `alembic` errors because no DB is configured locally, skip this step and rely on the test suite to confirm correctness. The migration is executed at test setup via `Base.metadata.create_all`, not via `alembic upgrade`, so the rename is reflected in the schema purely via the model definition.)

- [ ] **Step 10: Commit**

```bash
git add backend/modules/roster/models.py alembic/versions/*rename_map_url* backend/modules/roster/service.py backend/modules/roster/routes.py tests/test_roster_models.py tests/test_roster_service.py
git commit -m "feat: rename roster_logs.map_url to session_url"
```

---

### Task 5: AppShell + Sidebar + MobileMenu handle anonymous users

**Files:**
- Modify: `frontend/src/layouts/AppShell.tsx`
- Modify: `frontend/src/layouts/Sidebar.tsx`
- Modify: `frontend/src/layouts/MobileMenu.tsx`

- [ ] **Step 1: Update AppShell mobile top bar**

In `frontend/src/layouts/AppShell.tsx`, find the mobile top bar (around lines 17–28). Replace the trailing callsign span:

```tsx
        <span className="font-mono text-xs text-text-muted">{user?.callsign}</span>
```

With:

```tsx
        {user ? (
          <span className="font-mono text-xs text-text-muted">{user.callsign}</span>
        ) : (
          <a href="/login" className="text-xs text-accent hover:underline">Sign in</a>
        )}
```

- [ ] **Step 2: Update Sidebar footer**

Open `frontend/src/layouts/Sidebar.tsx`. Find the profile/logout footer block (look near the bottom of the file for the `logout` button or the `user.callsign` display).

Replace whatever currently renders the footer with a conditional:

```tsx
{user ? (
  <div className="...existing classes for profile/logout block...">
    {/* existing profile/logout markup */}
  </div>
) : (
  <a
    href="/login"
    className="block px-4 py-2 text-sm text-accent hover:underline"
  >
    Sign in
  </a>
)}
```

Read the existing file to preserve the exact wrapper classes and markup for the authenticated case — do not change that branch.

- [ ] **Step 3: Update MobileMenu**

In `frontend/src/layouts/MobileMenu.tsx`, find the logout button (around line 85) and apply the same pattern: when `user` is null, render a "Sign in" link to `/login` instead of the logout button. The nav-items list will naturally be empty for anonymous users since `filter((item) => user && item.minRole.includes(user.role))` already returns `[]`.

- [ ] **Step 4: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/layouts/AppShell.tsx frontend/src/layouts/Sidebar.tsx frontend/src/layouts/MobileMenu.tsx
git commit -m "feat: handle anonymous users in app layout chrome"
```

---

### Task 6: Lift `/checkins` out of `ProtectedRoute`

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Restructure the routes**

In `frontend/src/App.tsx`, the current structure is:

```tsx
<Route
  element={
    <ProtectedRoute>
      <AppShell />
    </ProtectedRoute>
  }
>
  <Route path="/schedule" element={<SchedulePage />} />
  <Route path="/profile" ... />
  <Route path="/checkins" element={<ProtectedRoute minRole={["viewer", "net_control", "admin"] as UserRole[]}><CheckInsPage /></ProtectedRoute>} />
  <Route path="/members" ... />
  ...
  <Route path="/privacy" element={<PrivacyPolicyPage />} />
</Route>
```

Replace the entire authenticated-group block with two sibling groups — one public-with-shell, one protected-with-shell:

```tsx
{/* Public routes that share the AppShell chrome */}
<Route element={<AppShell />}>
  <Route path="/checkins" element={<CheckInsPage />} />
</Route>

{/* Authenticated routes */}
<Route
  element={
    <ProtectedRoute>
      <AppShell />
    </ProtectedRoute>
  }
>
  <Route path="/schedule" element={<SchedulePage />} />
  <Route
    path="/profile"
    element={
      <ProtectedRoute minRole={["viewer", "net_control", "admin"] as UserRole[]}>
        <ProfilePage />
      </ProtectedRoute>
    }
  />
  <Route path="/members" element={<ProtectedRoute minRole={["viewer", "net_control", "admin"] as UserRole[]}><MembersPage /></ProtectedRoute>} />
  <Route path="/reminders" element={<ProtectedRoute minRole={["net_control", "admin"] as UserRole[]}><PlaceholderPage title="Reminders" /></ProtectedRoute>} />
  <Route path="/roster" element={<ProtectedRoute minRole={["net_control", "admin"] as UserRole[]}><PlaceholderPage title="Roster" /></ProtectedRoute>} />
  <Route path="/activities" element={<ProtectedRoute minRole={["admin"] as UserRole[]}><PlaceholderPage title="Activities" /></ProtectedRoute>} />
  <Route path="/users" element={<ProtectedRoute minRole={["admin"] as UserRole[]}><UsersPage /></ProtectedRoute>} />
  <Route path="/config" element={<ProtectedRoute minRole={["admin"] as UserRole[]}><ConfigPage /></ProtectedRoute>} />
  <Route path="/privacy" element={<PrivacyPolicyPage />} />
</Route>
```

Notes:
- `/checkins` loses its `ProtectedRoute minRole` wrapper entirely. The CheckInsPage itself handles role-based UI gating (Task 7).
- `/privacy` stays in the protected group — out of scope for this plan.
- The default `/` → `/schedule` redirect stays as it is.

- [ ] **Step 2: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: make /checkins route public"
```

---

### Task 7: CheckInsPage anonymous behavior

**Files:**
- Modify: `frontend/src/pages/CheckInsPage.tsx`

- [ ] **Step 1: Read the file to locate the relevant sections**

The page uses `useAuth()` already (or a similar hook) to access the user. Search for `user`, `userCanEdit`, `canEditCheckins`, `fetchRecentSessions`, and the buttons for "Scan", "Add", "Approve".

Run: `grep -n "useAuth\|userCanEdit\|canEditCheckins\|Scan\|Add\|Approve\|fetchRecentSessions" frontend/src/pages/CheckInsPage.tsx`

This will give you the line numbers for the next edits.

- [ ] **Step 2: Gate the session list on anonymous users**

Find where `fetchRecentSessions()` results are consumed. After the fetch resolves, when `user` is null, filter to completed sessions:

```tsx
const visibleSessions = useMemo(() => {
  if (user) return sessions;
  return sessions.filter((s) => s.status === "completed");
}, [sessions, user]);
```

Use `visibleSessions` in the dropdown's options and as the source for any "no sessions available" empty state.

- [ ] **Step 3: Hide action controls when user is null**

Find every button that triggers Scan, Add check-in, Approve session, or per-row Edit. The existing role-gating likely looks like `canEditCheckins && <button>...</button>`. Augment each to also require `user`:

```tsx
{user && canEditCheckins && <button>Scan</button>}
```

Specifically:
- Scan button
- "Add check-in" button (and the modal trigger)
- Approve session button
- The Edit icon column in `CheckinTable` (controlled by `canEditCheckins` prop)

The `canEditCheckins` prop derives from the user's role; if it's already `false` for null user, the controls are already hidden. Verify by reading: `const canEditCheckins = user?.role === "admin" || user?.role === "net_control";` — if that's the case, no change needed. Otherwise add an explicit `Boolean(user)` guard.

- [ ] **Step 4: Add 404 handling for not-yet-public sessions**

Find the `fetchSessionCheckins` call. The `apiFetch` helper throws `ApiError` with `status` and `detail` on non-2xx. Wrap the call to catch 404 specifically:

```tsx
useEffect(() => {
  if (!selectedSessionId) return;
  setCheckinsLoading(true);
  setNotPublic(false);
  fetchSessionCheckins(selectedSessionId)
    .then((data) => {
      setCheckins(data);
    })
    .catch((e) => {
      if (e?.status === 404 && !user) {
        setNotPublic(true);
      } else {
        // existing error handling
      }
    })
    .finally(() => setCheckinsLoading(false));
}, [selectedSessionId, user]);
```

Add a new state variable `const [notPublic, setNotPublic] = useState(false);` near the other state declarations.

Replace the main content render branch so that when `notPublic` is true, the page renders:

```tsx
{notPublic ? (
  <p className="text-text-muted text-sm py-8 text-center">
    This session is not yet available for public viewing.
  </p>
) : checkinsLoading ? (
  <Spinner />
) : ...existing two-pane layout...}
```

- [ ] **Step 5: Verify TypeScript compiles**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/CheckInsPage.tsx
git commit -m "feat: anonymous read-only mode for check-ins page"
```

---

### Task 8: Full verification

- [ ] **Step 1: Run the complete backend suite**

Run: `nix-shell --run "python -m pytest tests/ -q" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: All tests pass. Count should be at least 7 higher than before this plan started (3 for the optional-user helper, 4 for the public checkins endpoint, minus 1 for the deleted geojson test).

- [ ] **Step 2: Verify frontend type-checks**

Run: `nix-shell --run "cd frontend && npx tsc --noEmit" /home/ku0hn/dev/SkyNetControl/shell.nix`
Expected: No errors.

- [ ] **Step 3: Manual UI verification (cannot be scripted)**

Start the dev server and verify in a browser:

Anonymous (private window or signed-out state):
- Visit `/checkins?session=<id-of-completed-session>` — table renders, map renders, sidebar shows only a "Sign in" link, no Scan/Add/Approve/Edit controls visible.
- Visit `/checkins?session=<id-of-scheduled-session>` — "Session not yet available for public viewing" message.
- Click "Sign in" — lands at `/login`.

Authenticated (signed in as admin):
- Visit `/checkins?session=<any-id>` — full controls visible, behavior matches before this plan.
- Generate and approve a roster for a completed session — the rendered body contains `Check-in details: http://localhost:8000/checkins?session=<id>` (or whatever `app_base_url` is set to).
- Click the link in the rendered roster — opens the public check-ins page.

Report any UI regressions. Do not claim completion without doing this step.
