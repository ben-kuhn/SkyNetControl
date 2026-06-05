# Config UX + Pending-Admin Unstuck — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unblock the first-admin signup path (admin lands with a `PENDING-…` placeholder callsign and no way to set their real callsign), and overhaul the `/config` UI so it isn't asking admins to type JSON arrays or showing irrelevant fields. Genericize the W0NE branding that's hardcoded into seed templates and placeholder strings so the app works for nets other than `w0ne@winlink.org`.

**Architecture:**
- Backend: shift the `/api/auth/register` gate from `role == PENDING` to `callsign.startswith("PENDING-")` — the placeholder prefix is what really determines "this is a not-yet-claimed user." Inject `net_callsign` and `net_address` (from existing `default_net_control` / `net_address` config keys) into reminder & roster Jinja contexts so seeded templates can reference them. Add an Alembic migration that rewrites the existing seeded `W0NE …` template rows to use the new Jinja vars, matching only rows whose body is still the original seed (so user-edited templates are left alone).
- Frontend: in `ProtectedRoute`, treat any user with a `PENDING-…` callsign as needing registration regardless of role. Rebuild `ConfigPage`'s field model to support typed widgets (boolean checkbox, multi-select) and conditional visibility predicates, so `delivery.backends` becomes checkboxes, each backend's sub-fields only appear when that backend is selected, `scanner.interval_minutes` only appears when scanner is on, and PAT mailbox + scanner controls live in one "PAT" group.

**Tech Stack:** FastAPI · SQLAlchemy · Alembic · React + TypeScript · Vite · pytest · ruff

**Workflow notes** (from `CLAUDE.md`):
- Use a worktree (`EnterWorktree`) so `main` stays clean. The worktree branches from `origin/main`; cherry-pick the local plan commit into the worktree first if needed.
- Backend tests: `.venv/bin/pytest -q`. Lint: `nix-shell --run "ruff check"`. Both must pass before each commit.
- Frontend has no unit tests — verify those changes by running `./run-dev.sh` and exercising the page in the browser.
- Conventional Commits.

---

## File Structure

**Backend — modify:**
- `backend/auth/routes.py` — `/register` gate change.
- `backend/modules/reminders/service.py` — extend `build_template_context` with `net_callsign` + `net_address`.
- `backend/modules/roster/service.py` — same for `build_roster_context`.

**Backend — create:**
- `alembic/versions/<new>_genericize_w0ne_seed_templates.py` — UPDATE the three seeded template rows from `f5b2383f6dd3` and `4d657143fdea` to use `{{ net_callsign }}` / `{{ net_address }}`, scoped by the current literal text so user-edited rows are untouched.

**Backend — tests:**
- `tests/test_auth_registration.py` — add the placeholder-callsign-as-admin case.
- `tests/test_reminder_service.py` — add a context-vars test.
- `tests/test_roster_service.py` — add a context-vars test.

**Frontend — modify:**
- `frontend/src/ProtectedRoute.tsx` — extend the pending-routing block to also trigger on placeholder callsigns.
- `frontend/src/pages/ConfigPage.tsx` — typed field model, multiselect + boolean widgets, conditional visibility, regrouping, placeholder genericization.
- `frontend/src/pages/RegisterPage.tsx` and `frontend/src/pages/ProfilePage.tsx` — change "e.g., W0NE, KD0ABC" error string to non-W0NE example.

---

## Task 1: Backend — allow placeholder-callsign users to register regardless of role

**Files:**
- Modify: `backend/auth/routes.py:184` (the `if user.role != UserRole.PENDING` check inside `register`)
- Test: `tests/test_auth_registration.py` (add new test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_auth_registration.py` (after `test_register_already_registered`):

```python
@pytest.mark.asyncio
async def test_register_admin_with_placeholder_callsign(test_client, test_settings, db_setup):
    """First-signup admins start with a PENDING-... placeholder callsign and must be
    able to claim a real one via /register without going through the
    admin-approval round-trip."""
    _, factory = db_setup
    with factory() as session:
        session.add(
            User(
                callsign="PENDING-pocketid:80",
                oidc_subject="pocketid:80f1abc",
                name="First Admin",
                role=UserRole.ADMIN,
            )
        )
        session.commit()

    token = create_access_token("PENDING-pocketid:80", "admin", test_settings)
    response = await test_client.post(
        "/api/auth/register",
        json={"callsign": "W0ABC"},
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["callsign"] == "W0ABC"
    assert body["role"] == "admin"
```

- [ ] **Step 2: Run test, verify it fails**

```
.venv/bin/pytest tests/test_auth_registration.py::test_register_admin_with_placeholder_callsign -v
```

Expected: FAIL with HTTP 409 "User already registered" (the role-based check rejects admins).

- [ ] **Step 3: Update the register-gate condition**

In `backend/auth/routes.py`, find the `register` handler and replace:

```python
    if user.role != UserRole.PENDING:
        raise HTTPException(status_code=409, detail="User already registered")
```

with:

```python
    if not user.callsign.startswith("PENDING-"):
        raise HTTPException(status_code=409, detail="User already registered")
```

- [ ] **Step 4: Verify the new test passes and existing tests still pass**

```
.venv/bin/pytest tests/test_auth_registration.py -v
```

Expected: all green, including the existing `test_register_already_registered` (it uses a real callsign so the new condition correctly returns 409).

- [ ] **Step 5: Lint**

```
nix-shell --run "ruff check"
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add backend/auth/routes.py tests/test_auth_registration.py
git commit -m "fix(auth): let placeholder-callsign admins finish registration

First-signup admins start with role=ADMIN but callsign=PENDING-<subject>,
and the role-based gate in /register kicked them out with 409. Switch
the gate to a callsign-prefix check: any user still carrying the
PENDING- placeholder can claim a real callsign, regardless of role.
Existing-real-callsign users are still rejected."
```

---

## Task 2: Frontend — route placeholder-callsign users to /register

**Files:**
- Modify: `frontend/src/ProtectedRoute.tsx`

- [ ] **Step 1: Update the pending-redirect block**

In `frontend/src/ProtectedRoute.tsx`, replace the existing `if (user.role === "pending") { … }` block with one that also fires when the callsign is a placeholder. The full block becomes:

```tsx
  // Treat anyone still carrying a PENDING-... placeholder callsign as
  // needing registration, even if their role is already ADMIN (the
  // first-signup case).
  const hasPlaceholderCallsign = user.callsign.startsWith("PENDING-");
  if (user.role === "pending" || hasPlaceholderCallsign) {
    if (pendingOnly && !hasPlaceholderCallsign) {
      return <Navigate to="/pending" replace />;
    }
    if (!allowPending) {
      if (hasPlaceholderCallsign) {
        return <Navigate to="/register" replace />;
      }
      return <Navigate to="/pending" replace />;
    }
  }
```

(Note: this replaces the old `user.callsign.startsWith("PENDING-")` reads with the local `hasPlaceholderCallsign` const, but keeps the same routing decisions. The only behavioural change is the outer condition now includes non-pending roles with placeholder callsigns.)

- [ ] **Step 2: Manually verify in dev**

Start the dev servers:

```
./run-dev.sh
```

In a browser (incognito, to avoid the previous cache):

1. Sign in via OIDC as the first user (you become ADMIN with `PENDING-…` callsign).
2. Expected: lands on `/register`, not `/schedule`.
3. Submit a real callsign (e.g. `W0ABC`).
4. Expected: backend returns 200, frontend refreshes, you're routed onward (`/schedule` for admin), profile shows the real callsign.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/ProtectedRoute.tsx
git commit -m "fix(frontend): route placeholder-callsign users to /register

Match the backend rule: PENDING-... callsign means 'not yet claimed',
regardless of role. Otherwise first-signup admins land on /schedule
with no obvious way to set their real callsign."
```

---

## Task 3: Backend — inject net_callsign + net_address into reminder template context

**Files:**
- Modify: `backend/modules/reminders/service.py` (the `build_template_context` function around line 161)
- Test: `tests/test_reminder_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_reminder_service.py` (alongside existing context tests; mirror the existing fixture patterns):

```python
def test_build_template_context_includes_net_identity(db_session, sample_session):
    """The reminder template context exposes net_callsign and net_address
    from config so seeded templates can avoid hardcoded W0NE branding."""
    from backend.config_mgmt.service import set_config_value
    from backend.modules.reminders.service import build_template_context

    set_config_value(db_session, "default_net_control", "K0XYZ")
    set_config_value(db_session, "net_address", "k0xyz@winlink.org")

    context = build_template_context(db_session, sample_session)

    assert context["net_callsign"] == "K0XYZ"
    assert context["net_address"] == "k0xyz@winlink.org"
```

If `tests/test_reminder_service.py` doesn't have a `sample_session` / `db_session` fixture, inspect existing tests in the file for the fixture names they use and reuse those — do not invent fixtures that don't exist. Match the file's existing style.

- [ ] **Step 2: Run, verify it fails**

```
.venv/bin/pytest tests/test_reminder_service.py::test_build_template_context_includes_net_identity -v
```

Expected: FAIL with `KeyError: 'net_callsign'`.

- [ ] **Step 3: Implement**

In `backend/modules/reminders/service.py`, add an import at the top of the file (alongside the other `backend.` imports):

```python
from backend.config_mgmt.service import get_config_value
```

Then in `build_template_context`, just before the final `return {…}`, add:

```python
    net_callsign = get_config_value(db, "default_net_control", default="") or ""
    net_address = get_config_value(db, "net_address", default="") or ""
```

And extend the returned dict to include both new keys:

```python
    return {
        "date": date_str,
        "time": time_str,
        "day_of_week": day_of_week,
        "activity_title": activity_title,
        "activity_instructions": activity_instructions,
        "net_control": net_control,
        "net_callsign": net_callsign,
        "net_address": net_address,
        "next_week_preview": next_week_preview,
    }
```

- [ ] **Step 4: Verify the new test passes and the full file passes**

```
.venv/bin/pytest tests/test_reminder_service.py -v
```

Expected: green.

- [ ] **Step 5: Lint**

```
nix-shell --run "ruff check"
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add backend/modules/reminders/service.py tests/test_reminder_service.py
git commit -m "feat(reminders): expose net_callsign + net_address in template context

Pull both from the existing default_net_control / net_address config
keys. Templates can now reference {{ net_callsign }} and
{{ net_address }} instead of hardcoding W0NE branding."
```

---

## Task 4: Backend — same for roster template context

**Files:**
- Modify: `backend/modules/roster/service.py` (`build_roster_context`, around line 169)
- Test: `tests/test_roster_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_roster_service.py`, mirroring whatever `db_session` / `sample_session` fixtures the existing tests in that file use:

```python
def test_build_roster_context_includes_net_identity(db_session, sample_session):
    """Roster template context exposes net_callsign and net_address so seeded
    templates can avoid hardcoded W0NE branding."""
    from backend.config_mgmt.service import set_config_value
    from backend.modules.roster.service import build_roster_context

    set_config_value(db_session, "default_net_control", "K0XYZ")
    set_config_value(db_session, "net_address", "k0xyz@winlink.org")

    context = build_roster_context(db_session, sample_session)

    assert context["net_callsign"] == "K0XYZ"
    assert context["net_address"] == "k0xyz@winlink.org"
```

- [ ] **Step 2: Run, verify it fails**

```
.venv/bin/pytest tests/test_roster_service.py::test_build_roster_context_includes_net_identity -v
```

Expected: FAIL with `KeyError`.

- [ ] **Step 3: Implement**

In `backend/modules/roster/service.py`, ensure `from backend.config_mgmt.service import get_config_value` is imported (add it next to existing `backend.` imports if not).

Inside `build_roster_context`, just before the final `return {…}`, add:

```python
    net_callsign = get_config_value(db, "default_net_control", default="") or ""
    net_address = get_config_value(db, "net_address", default="") or ""
```

And extend the returned dict with both keys (keep all existing keys in place):

```python
        "net_callsign": net_callsign,
        "net_address": net_address,
```

- [ ] **Step 4: Verify**

```
.venv/bin/pytest tests/test_roster_service.py -v
```

Expected: green.

- [ ] **Step 5: Lint**

```
nix-shell --run "ruff check"
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add backend/modules/roster/service.py tests/test_roster_service.py
git commit -m "feat(roster): expose net_callsign + net_address in template context

Mirrors the reminder-service change so roster templates can also
reference {{ net_callsign }} / {{ net_address }} rather than baking in
W0NE."
```

---

## Task 5: Migration — rewrite W0NE-branded seed templates to use new vars

**Files:**
- Create: `alembic/versions/<rev>_genericize_w0ne_seed_templates.py`

This migration runs on every fresh install (immediately after the old seeds insert the W0NE rows) and on every existing instance (rewriting any unchanged seed rows). It scopes UPDATEs by matching the exact original text so admin-edited templates are left alone.

- [ ] **Step 1: Generate the migration scaffold**

```
.venv/bin/alembic revision -m "genericize w0ne seed templates"
```

Note the generated filename and revision id. The new file is created under `alembic/versions/`.

- [ ] **Step 2: Find the previous head revision**

The new file's auto-generated `down_revision` should already point to whatever was head before this revision. Confirm with:

```
.venv/bin/alembic history | head -5
```

The new revision should sit immediately above the previous head; no edit needed unless alembic got it wrong.

- [ ] **Step 3: Write upgrade() and downgrade()**

Replace the body of the new migration with:

```python
"""genericize w0ne seed templates

Revision ID: <auto>
Revises: <auto>
Create Date: <auto>

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "<auto>"
down_revision: Union[str, None] = "<auto>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --- Roster template (from f5b2383f6dd3) ---

OLD_ROSTER_NAME = "W0NE Net Roster"
NEW_ROSTER_NAME = "Default Net Roster"

OLD_ROSTER_SUBJECT = "W0NE Winlink Net Roster — {{ date }}"
NEW_ROSTER_SUBJECT = "{{ net_callsign }} Winlink Net Roster — {{ date }}"

OLD_ROSTER_HEADER = (
    "W0NE Winlink Net Roster for {{ day_of_week }}, {{ date }}{% if time %} at {{ time }} UTC{% endif %}.\n"
    "Net Control: {{ net_control }}\n"
    "{% if activity_title %}Activity: {{ activity_title }}{% endif %}\n"
    "Total Check-ins: {{ total_count }}"
)
NEW_ROSTER_HEADER = (
    "{{ net_callsign }} Winlink Net Roster for {{ day_of_week }}, {{ date }}"
    "{% if time %} at {{ time }} UTC{% endif %}.\n"
    "Net Control: {{ net_control }}\n"
    "{% if activity_title %}Activity: {{ activity_title }}{% endif %}\n"
    "Total Check-ins: {{ total_count }}"
)

OLD_ROSTER_WELCOME = "{% for m in new_members %}Welcome to the W0NE Winlink Net, {{ m.name }} ({{ m.callsign }})!\n{% endfor %}"
NEW_ROSTER_WELCOME = "{% for m in new_members %}Welcome to the {{ net_callsign }} Winlink Net, {{ m.name }} ({{ m.callsign }})!\n{% endfor %}"

OLD_ROSTER_FOOTER = (
    "{% if next_week_preview %}Next week: {{ next_week_preview }}\n{% endif %}"
    "{% if map_url %}Check-in map: {{ map_url }}\n{% endif %}"
    "73 de W0NE"
)
NEW_ROSTER_FOOTER = (
    "{% if next_week_preview %}Next week: {{ next_week_preview }}\n{% endif %}"
    "{% if map_url %}Check-in map: {{ map_url }}\n{% endif %}"
    "73 de {{ net_callsign }}"
)

# --- Reminder templates (from 4d657143fdea) ---

OLD_REGULAR_SUBJECT = "W0NE Winlink Net Reminder — {{ date }}"
NEW_REGULAR_SUBJECT = "{{ net_callsign }} Winlink Net Reminder — {{ date }}"

OLD_REGULAR_BODY = (
    "Reminder: the W0NE Winlink Net check-in is this {{ day_of_week }}, {{ date }}.\n\n"
    "Please send your check-in to w0ne@winlink.org with your name, callsign, city, county, state, and mode.\n\n"
    "Net control: {{ net_control }}\n"
    "{% if next_week_preview %}\nNext week: {{ next_week_preview }}\n{% endif %}"
)
NEW_REGULAR_BODY = (
    "Reminder: the {{ net_callsign }} Winlink Net check-in is this {{ day_of_week }}, {{ date }}.\n\n"
    "Please send your check-in to {{ net_address }} with your name, callsign, city, county, state, and mode.\n\n"
    "Net control: {{ net_control }}\n"
    "{% if next_week_preview %}\nNext week: {{ next_week_preview }}\n{% endif %}"
)

OLD_ACTIVITY_SUBJECT = "W0NE Winlink Net — {{ activity_title }} — {{ date }}"
NEW_ACTIVITY_SUBJECT = "{{ net_callsign }} Winlink Net — {{ activity_title }} — {{ date }}"

OLD_ACTIVITY_BODY = (
    "This {{ day_of_week }}'s W0NE Winlink Net features a special activity: **{{ activity_title }}**\n\n"
    "{{ activity_instructions }}\n\n"
    "Please send your check-in to w0ne@winlink.org with your name, callsign, city, county, state, and mode.\n\n"
    "Net control: {{ net_control }}\n"
    "{% if next_week_preview %}\nNext week: {{ next_week_preview }}\n{% endif %}"
)
NEW_ACTIVITY_BODY = (
    "This {{ day_of_week }}'s {{ net_callsign }} Winlink Net features a special activity: **{{ activity_title }}**\n\n"
    "{{ activity_instructions }}\n\n"
    "Please send your check-in to {{ net_address }} with your name, callsign, city, county, state, and mode.\n\n"
    "Net control: {{ net_control }}\n"
    "{% if next_week_preview %}\nNext week: {{ next_week_preview }}\n{% endif %}"
)


def _replace_template_row(
    table: str,
    name_col_old: str,
    name_col_new: str,
    field_old_new_pairs: list[tuple[str, str, str]],
) -> None:
    """Update one template row to use generic strings, only if every old value
    still matches (i.e. the admin hasn't edited it)."""
    set_clause = ", ".join(f"{col} = :new_{col}" for col, _, _ in field_old_new_pairs)
    where_clause = " AND ".join(f"{col} = :old_{col}" for col, _, _ in field_old_new_pairs)
    params = {"name_old": name_col_old, "name_new": name_col_new}
    for col, old, new in field_old_new_pairs:
        params[f"old_{col}"] = old
        params[f"new_{col}"] = new

    sql = (
        f"UPDATE {table} SET name = :name_new, {set_clause} "
        f"WHERE name = :name_old AND {where_clause}"
    )
    op.execute(sa.text(sql).bindparams(**params))


def upgrade() -> None:
    import sqlalchemy as sa  # local import; alembic templates already import elsewhere

    # Roster template
    op.execute(
        sa.text(
            "UPDATE roster_templates SET "
            "name = :name_new, "
            "subject_template = :subject_new, "
            "header_template = :header_new, "
            "welcome_template = :welcome_new, "
            "footer_template = :footer_new "
            "WHERE name = :name_old "
            "AND subject_template = :subject_old "
            "AND header_template = :header_old "
            "AND welcome_template = :welcome_old "
            "AND footer_template = :footer_old"
        ).bindparams(
            name_old=OLD_ROSTER_NAME,
            name_new=NEW_ROSTER_NAME,
            subject_old=OLD_ROSTER_SUBJECT,
            subject_new=NEW_ROSTER_SUBJECT,
            header_old=OLD_ROSTER_HEADER,
            header_new=NEW_ROSTER_HEADER,
            welcome_old=OLD_ROSTER_WELCOME,
            welcome_new=NEW_ROSTER_WELCOME,
            footer_old=OLD_ROSTER_FOOTER,
            footer_new=NEW_ROSTER_FOOTER,
        )
    )

    # Reminder: regular
    op.execute(
        sa.text(
            "UPDATE reminder_templates SET "
            "subject_template = :subject_new, "
            "body_template = :body_new "
            "WHERE name = :name "
            "AND subject_template = :subject_old "
            "AND body_template = :body_old"
        ).bindparams(
            name="Regular Check-in Reminder",
            subject_old=OLD_REGULAR_SUBJECT,
            subject_new=NEW_REGULAR_SUBJECT,
            body_old=OLD_REGULAR_BODY,
            body_new=NEW_REGULAR_BODY,
        )
    )

    # Reminder: activity
    op.execute(
        sa.text(
            "UPDATE reminder_templates SET "
            "subject_template = :subject_new, "
            "body_template = :body_new "
            "WHERE name = :name "
            "AND subject_template = :subject_old "
            "AND body_template = :body_old"
        ).bindparams(
            name="Activity Week Reminder",
            subject_old=OLD_ACTIVITY_SUBJECT,
            subject_new=NEW_ACTIVITY_SUBJECT,
            body_old=OLD_ACTIVITY_BODY,
            body_new=NEW_ACTIVITY_BODY,
        )
    )


def downgrade() -> None:
    import sqlalchemy as sa

    # Roster
    op.execute(
        sa.text(
            "UPDATE roster_templates SET "
            "name = :name_old, "
            "subject_template = :subject_old, "
            "header_template = :header_old, "
            "welcome_template = :welcome_old, "
            "footer_template = :footer_old "
            "WHERE name = :name_new "
            "AND subject_template = :subject_new "
            "AND header_template = :header_new "
            "AND welcome_template = :welcome_new "
            "AND footer_template = :footer_new"
        ).bindparams(
            name_old=OLD_ROSTER_NAME,
            name_new=NEW_ROSTER_NAME,
            subject_old=OLD_ROSTER_SUBJECT,
            subject_new=NEW_ROSTER_SUBJECT,
            header_old=OLD_ROSTER_HEADER,
            header_new=NEW_ROSTER_HEADER,
            welcome_old=OLD_ROSTER_WELCOME,
            welcome_new=NEW_ROSTER_WELCOME,
            footer_old=OLD_ROSTER_FOOTER,
            footer_new=NEW_ROSTER_FOOTER,
        )
    )

    # Reminder: regular
    op.execute(
        sa.text(
            "UPDATE reminder_templates SET "
            "subject_template = :subject_old, "
            "body_template = :body_old "
            "WHERE name = :name "
            "AND subject_template = :subject_new "
            "AND body_template = :body_new"
        ).bindparams(
            name="Regular Check-in Reminder",
            subject_old=OLD_REGULAR_SUBJECT,
            subject_new=NEW_REGULAR_SUBJECT,
            body_old=OLD_REGULAR_BODY,
            body_new=NEW_REGULAR_BODY,
        )
    )

    # Reminder: activity
    op.execute(
        sa.text(
            "UPDATE reminder_templates SET "
            "subject_template = :subject_old, "
            "body_template = :body_old "
            "WHERE name = :name "
            "AND subject_template = :subject_new "
            "AND body_template = :body_new"
        ).bindparams(
            name="Activity Week Reminder",
            subject_old=OLD_ACTIVITY_SUBJECT,
            subject_new=NEW_ACTIVITY_SUBJECT,
            body_old=OLD_ACTIVITY_BODY,
            body_new=NEW_ACTIVITY_BODY,
        )
    )
```

Then remove the unused `_replace_template_row` helper from the top (it was a sketch; the inline `op.execute(sa.text(...).bindparams(...))` blocks above are what's actually used). Final file should also have `import sqlalchemy as sa` at the top of the file (not just inside the functions); follow whatever pattern the most recent alembic revision in the repo uses.

- [ ] **Step 4: Run migrations against a fresh SQLite test DB**

From a fresh shell:

```
rm -f /tmp/skynet-migration-check.db
DATABASE_URL=sqlite:////tmp/skynet-migration-check.db .venv/bin/alembic upgrade head
```

Expected: completes without error.

- [ ] **Step 5: Verify the rows were rewritten**

```
sqlite3 /tmp/skynet-migration-check.db "SELECT name, subject_template FROM roster_templates;"
sqlite3 /tmp/skynet-migration-check.db "SELECT name, subject_template FROM reminder_templates;"
```

Expected: rows show `{{ net_callsign }}` in the subjects, no literal `W0NE`. (If `sqlite3` isn't on PATH, drop into `nix-shell -p sqlite --run "sqlite3 …"`.)

- [ ] **Step 6: Run the migration round-trip to exercise downgrade**

```
DATABASE_URL=sqlite:////tmp/skynet-migration-check.db .venv/bin/alembic downgrade -1
DATABASE_URL=sqlite:////tmp/skynet-migration-check.db .venv/bin/alembic upgrade head
```

Expected: both succeed.

- [ ] **Step 7: Run full backend test suite**

```
.venv/bin/pytest -q
```

Expected: green.

- [ ] **Step 8: Lint**

```
nix-shell --run "ruff check"
```

Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add alembic/versions/
git commit -m "feat(templates): genericize W0NE-branded seed templates

Rewrite the three rows seeded by f5b2383f6dd3 and 4d657143fdea to use
{{ net_callsign }} and {{ net_address }} (populated from the
default_net_control / net_address config keys). UPDATE only rows whose
current values still match the original seed text so admins who have
already customized their templates are left alone."
```

---

## Task 6: Frontend — refactor ConfigPage field model (boolean + multiselect + visibleWhen)

This task introduces the new shape and the new widgets but keeps the existing visible behaviour. Task 7 wires up the conditional visibility and regrouping.

**Files:**
- Modify: `frontend/src/pages/ConfigPage.tsx`

- [ ] **Step 1: Extend the `ConfigField` type and define widget options**

In `frontend/src/pages/ConfigPage.tsx`, replace the existing `ConfigField` interface with:

```ts
type ConfigFieldType = "text" | "boolean" | "multiselect";

interface MultiSelectOption {
  value: string;
  label: string;
}

interface ConfigField {
  key: string;
  label: string;
  group: string;
  helpText: string;
  type?: ConfigFieldType; // defaults to "text"
  placeholder?: string;   // text fields only
  mono?: boolean;         // text fields only
  secret?: boolean;       // text fields only
  options?: MultiSelectOption[]; // multiselect only
  visibleWhen?: (values: Record<string, string>) => boolean;
}

const DELIVERY_BACKEND_OPTIONS: MultiSelectOption[] = [
  { value: "email", label: "Email" },
  { value: "groupsio", label: "Groups.io" },
  { value: "winlink", label: "Winlink" },
];

function parseBackends(raw: string): string[] {
  try {
    const v = JSON.parse(raw || "[]");
    return Array.isArray(v) ? v.filter((s) => typeof s === "string") : [];
  } catch {
    return [];
  }
}
```

- [ ] **Step 2: Update `ConfigFieldRow` to dispatch on `field.type`**

Replace the existing `ConfigFieldRow` body so it renders the right widget. Boolean fields render a checkbox (true ↔ "true", false ↔ "false"); multiselect fields render one checkbox per option and stringify the resulting array as JSON; text fields keep current behaviour.

Replace the entire `ConfigFieldRow` function with:

```tsx
function ConfigFieldRow({
  field,
  value,
  savedValue,
  onChange,
  onSave,
  saving,
}: {
  field: ConfigField;
  value: string;
  savedValue: string;
  onChange: (value: string) => void;
  onSave: () => void;
  saving: boolean;
}) {
  const [showSecret, setShowSecret] = useState(false);
  const isDirty = value !== savedValue;
  const type = field.type ?? "text";

  let input: React.ReactNode;
  if (type === "boolean") {
    const checked = value === "true";
    input = (
      <label className="inline-flex items-center gap-2 text-sm text-text-primary">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked ? "true" : "false")}
          className="accent-accent"
        />
        <span className="text-text-secondary">{checked ? "Enabled" : "Disabled"}</span>
      </label>
    );
  } else if (type === "multiselect") {
    const selected = parseBackends(value);
    const toggle = (v: string) => {
      const next = selected.includes(v)
        ? selected.filter((s) => s !== v)
        : [...selected, v];
      onChange(JSON.stringify(next));
    };
    input = (
      <div className="flex flex-col gap-1">
        {(field.options ?? []).map((opt) => (
          <label key={opt.value} className="inline-flex items-center gap-2 text-sm text-text-primary">
            <input
              type="checkbox"
              checked={selected.includes(opt.value)}
              onChange={() => toggle(opt.value)}
              className="accent-accent"
            />
            <span className="text-text-secondary">{opt.label}</span>
          </label>
        ))}
      </div>
    );
  } else {
    input = (
      <div className="relative flex-1 max-w-md">
        <input
          type={field.secret && !showSecret ? "password" : "text"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder}
          className={`w-full bg-bg-elevated border border-border rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted ${
            field.mono ? "font-mono" : ""
          } ${field.secret ? "pr-10" : ""}`}
        />
        {field.secret && (
          <button
            type="button"
            onClick={() => setShowSecret(!showSecret)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary text-xs px-1"
            title={showSecret ? "Hide" : "Show"}
          >
            {showSecret ? "Hide" : "Show"}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="mb-4 last:mb-0">
      <label className="block text-sm text-text-secondary mb-1">
        {field.label}
      </label>
      <div className="flex gap-2 items-start">
        <div className="flex-1">{input}</div>
        <Button
          size="sm"
          variant={isDirty ? "primary" : "secondary"}
          onClick={onSave}
          loading={saving}
          disabled={!isDirty}
        >
          Save
        </Button>
      </div>
      <div className="text-xs text-text-muted mt-1">{field.helpText}</div>
    </div>
  );
}
```

- [ ] **Step 3: Verify it still builds**

```
cd frontend && nix-shell -p nodejs_22 --run "npm run build"
```

Expected: build succeeds. (No behaviour change yet — text fields still render the same way; we just added support for the other types.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/ConfigPage.tsx
git commit -m "refactor(config): typed field model for boolean + multiselect widgets

ConfigField now carries an optional type discriminator and ConfigFieldRow
dispatches to text/checkbox/multiselect renderers. Existing text fields
are unchanged. Prepares the page for typed widgets and conditional
visibility in the next commits."
```

---

## Task 7: Frontend — switch delivery.backends + scanner.enabled to typed widgets, group PAT, gate visibility, genericize placeholders

**Files:**
- Modify: `frontend/src/pages/ConfigPage.tsx`
- Modify: `frontend/src/pages/RegisterPage.tsx` (one string)
- Modify: `frontend/src/pages/ProfilePage.tsx` (one string)

- [ ] **Step 1: Replace `CONFIG_FIELDS` and `GROUPS` with the regrouped, typed, gated version**

In `frontend/src/pages/ConfigPage.tsx`, replace the existing `CONFIG_FIELDS` array and `GROUPS` constant with:

```ts
const CONFIG_FIELDS: ConfigField[] = [
  {
    key: "default_net_control",
    label: "Net Callsign",
    group: "Net Operations",
    placeholder: "WAØXYZ",
    helpText: "Your net's callsign — used as the default net-control assignment for new sessions and as {{ net_callsign }} in templates",
    mono: true,
  },
  {
    key: "net_address",
    label: "Net Winlink Address",
    group: "Net Operations",
    placeholder: "yournet@winlink.org",
    helpText: "Winlink address used for check-in message parsing and as {{ net_address }} in templates",
  },
  {
    key: "pat_mailbox_path",
    label: "PAT Mailbox Path",
    group: "PAT",
    placeholder: "~/.local/share/pat/mailbox/YOURCALL",
    helpText: "Local filesystem path to the PAT Winlink client mailbox directory",
    mono: true,
  },
  {
    key: "scanner.enabled",
    label: "Auto-Scanner",
    group: "PAT",
    type: "boolean",
    helpText: "Automatically scan the PAT mailbox for new check-ins on a timer",
  },
  {
    key: "scanner.interval_minutes",
    label: "Scan Interval (minutes)",
    group: "PAT",
    placeholder: "5",
    helpText: "How often to scan the mailbox for new check-ins",
    visibleWhen: (v) => v["scanner.enabled"] === "true",
  },
  {
    key: "claude_api_key",
    label: "Claude API Key",
    group: "Integrations",
    placeholder: "sk-ant-...",
    helpText: "API key for Claude-powered activity brainstorming (optional)",
    secret: true,
  },
  {
    key: "delivery.backends",
    label: "Enabled Delivery Backends",
    group: "Delivery",
    type: "multiselect",
    options: DELIVERY_BACKEND_OPTIONS,
    helpText: "Channels for sending reminders and rosters",
  },
  {
    key: "delivery.email.to_address",
    label: "Email Recipient",
    group: "Delivery",
    placeholder: "net-list@example.com",
    helpText: "Email address to send reminders and rosters to",
    visibleWhen: (v) => parseBackends(v["delivery.backends"] ?? "").includes("email"),
  },
  {
    key: "delivery.groupsio.api_key",
    label: "Groups.io API Key",
    group: "Delivery",
    placeholder: "your-api-key",
    helpText: "API key for posting to groups.io",
    secret: true,
    visibleWhen: (v) => parseBackends(v["delivery.backends"] ?? "").includes("groupsio"),
  },
  {
    key: "delivery.groupsio.group_name",
    label: "Groups.io Group Name",
    group: "Delivery",
    placeholder: "your-net",
    helpText: "Target group name on groups.io",
    visibleWhen: (v) => parseBackends(v["delivery.backends"] ?? "").includes("groupsio"),
  },
  {
    key: "delivery.winlink.target_address",
    label: "Winlink Delivery Address",
    group: "Delivery",
    placeholder: "NET@winlink.org",
    helpText: "Winlink address to send reminders and rosters to",
    visibleWhen: (v) => parseBackends(v["delivery.backends"] ?? "").includes("winlink"),
  },
];

const GROUPS = ["Net Operations", "PAT", "Integrations", "Delivery"];
```

- [ ] **Step 2: Filter by `visibleWhen` in the render loop and hide groups whose fields are all hidden**

Replace the existing `GROUPS.map(...)` block at the bottom of the component with:

```tsx
      {GROUPS.map((group) => {
        const visibleFields = CONFIG_FIELDS.filter(
          (f) => f.group === group && (!f.visibleWhen || f.visibleWhen(values)),
        );
        if (visibleFields.length === 0) return null;
        return (
          <div
            key={group}
            className="bg-bg-surface border border-border rounded-lg p-6 mb-4"
          >
            <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-4">
              {group}
            </h2>
            {visibleFields.map((field) => (
              <ConfigFieldRow
                key={field.key}
                field={field}
                value={values[field.key] || ""}
                savedValue={savedValues[field.key] || ""}
                onChange={(v) =>
                  setValues((prev) => ({ ...prev, [field.key]: v }))
                }
                onSave={() => handleSave(field.key)}
                saving={savingKey === field.key}
              />
            ))}
          </div>
        );
      })}
```

- [ ] **Step 3: Genericize the W0NE example in callsign error messages**

In `frontend/src/pages/RegisterPage.tsx:24`, replace:

```ts
      setError("Invalid callsign format (e.g., W0NE, KD0ABC)");
```

with:

```ts
      setError("Invalid callsign format (e.g., WAØXYZ, KD0ABC)");
```

In `frontend/src/pages/ProfilePage.tsx:77`, the same swap:

```ts
      setCallsignError("Invalid callsign format (e.g., WAØXYZ, KD0ABC)");
```

In `frontend/src/pages/ProfilePage.tsx:209`, change the input placeholder:

```tsx
                placeholder="WAØXYZ"
```

- [ ] **Step 4: Build the frontend**

```
cd frontend && nix-shell -p nodejs_22 --run "npm run build"
```

Expected: clean build.

- [ ] **Step 5: Manually verify in dev**

```
./run-dev.sh
```

In a browser as admin, hit `/config` and confirm:

1. Four group cards: Net Operations, PAT, Integrations, Delivery.
2. PAT card holds: PAT Mailbox Path, Auto-Scanner (checkbox), Scan Interval (hidden until Auto-Scanner is checked).
3. Auto-Scanner toggling shows/hides Scan Interval.
4. Delivery card shows: Enabled Delivery Backends (three checkboxes — Email / Groups.io / Winlink). With none checked, no sub-fields appear. Check Email → Email Recipient appears. Check Groups.io → both Groups.io fields appear. Check Winlink → Winlink Delivery Address appears.
5. Toggling a backend checkbox and hitting Save persists the array (verify with `curl -sS …` to `/api/config/` if you want, but the round-trip is the verification — refresh the page and confirm the checkboxes still reflect the saved state).
6. Placeholders show generic strings (no W0NE / w0ne@winlink.org).

- [ ] **Step 6: Lint + tests**

```
nix-shell --run "ruff check"
.venv/bin/pytest -q
```

Expected: clean / green.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/ConfigPage.tsx frontend/src/pages/RegisterPage.tsx frontend/src/pages/ProfilePage.tsx
git commit -m "feat(config): typed widgets, conditional visibility, PAT group, genericize placeholders

- delivery.backends is now a multi-select of checkboxes instead of a
  free-form JSON-array text field.
- Each delivery sub-field only appears when its backend is enabled.
- scanner.enabled is a real checkbox; scanner.interval_minutes only
  shows when the scanner is on.
- PAT mailbox path moves out of Integrations and joins the scanner
  controls in a dedicated PAT group.
- W0NE / w0ne@winlink.org placeholders and the WA0XYZ example in
  callsign errors are now generic."
```

---

## Task 8: Wrap up — full verify + finish branch

- [ ] **Step 1: Run the full test suite and lint one more time**

```
.venv/bin/pytest -q
nix-shell --run "ruff check"
cd frontend && nix-shell -p nodejs_22 --run "npm run build" && cd ..
```

Expected: all green.

- [ ] **Step 2: Smoke-test the end-to-end flow once more in dev**

`./run-dev.sh`, sign in as a fresh first user (or wipe the DB if iterating), confirm:
- First user with `PENDING-…` callsign and ADMIN role is routed to `/register`.
- After choosing a real callsign, lands on `/schedule`.
- `/config` page works as in Task 7 verification.
- Generate a reminder/roster draft for a session; confirm rendered subject/body use the net callsign from config rather than the literal "W0NE".

- [ ] **Step 3: Hand off to the finishing skill**

Invoke `superpowers:finishing-a-development-branch` to merge back to `main` (per CLAUDE.md, default to local merge for solo work). After merge, wait for CI green on `main` before starting the next task.

---

## Self-review

- **Spec coverage:** Issues 1–5 from the conversation each have at least one task: admin PENDING-callsign (Tasks 1+2), W0NE in templates (Tasks 3+4+5), W0NE in placeholders (Task 7 Step 3), delivery-backends JSON-array UI (Tasks 6+7), backend-conditional visibility (Task 7), PAT regrouping (Task 7).
- **Placeholders:** no TBDs or "add appropriate error handling" lines; every code step contains the actual code.
- **Type consistency:** `net_callsign` / `net_address` keys used in Tasks 3, 4, 5, 7 — matched. `ConfigField` shape introduced in Task 6 (with `type`, `visibleWhen`, `options`) is exactly what Task 7's field list uses.
- **One thing to check during execution:** Task 5's migration uses exact-text WHERE-matching. If any of the `OLD_*` constants in this plan don't byte-identically match what the original migrations actually wrote into the rows (newline handling, whitespace, escape sequences), the UPDATE silently no-ops and the rows stay W0NE-branded. Verify with the sqlite3 query at Step 5; if the UPDATE missed, the original seed text in `f5b2383f6dd3` / `4d657143fdea` is the source of truth — copy exactly from there.
