# One-Click Send — Implementation Plan

Date: 2026-09-16
Spec: `docs/superpowers/specs/2026-09-16-one-click-send-design.md`

## Sequencing

Land in atomically-committable slices. Each slice keeps the suite green.

### Slice A — Idempotent member finalization

1. `backend/modules/schedule/models.py`: add
   `members_finalized_at: Mapped[datetime | None]` to `NetSession`.
2. New Alembic migration `alembic/versions/<rev>_add_members_finalized_at.py`
   adding the nullable column (branch from current head `a7b1c9d3e5f0`).
3. `backend/modules/checkins/service.py` `approve_session_checkins()`:
   guard the member upsert loop on `members_finalized_at` already being set;
   stamp it after first finalization; keep `status = COMPLETED`.
4. Tests in `tests/test_checkin_service.py`: calling
   `approve_session_checkins` twice for the same session bumps
   `total_check_ins` once.

### Slice B — Combined submit services + routes

1. `backend/modules/roster/service.py`: `submit_roster(db, roster_id,
   approver_callsign, **content)`:
   - `draft` → `update_draft` (if content) → `approve_roster` → `mark_sent` (if
     still `approved` and delivery OK); mirror the existing
     `mark_sent` failure semantics (returns a tuple or raises) so the route can
     raise 502 with the delivery errors.
   - `approved` → `mark_sent`.
   - else → None (route 409).
2. `backend/modules/reminders/service.py`: `submit_reminder` same shape.
3. Routes `POST /{id}/submit` in both modules (NET_CONTROL, net-scoped via
   `_verify_log_net`), reusing `get_last_attempt_errors` on failure → 502.
4. Tests: `tests/test_roster_routes.py` / `tests/test_reminder_routes.py`
   for draft→sent one-shot, approved retry, 409 on sent/skipped, 502 on failure.

### Slice C — Auto-generate roster on session close

1. `backend/modules/checkins/routes.py` `approve_session_route`: after
   `approve_session_checkins`, if net has a default roster template and no
   roster exists yet, `generate_draft(db, session_id)`; add `roster_id` to the
   response.
2. Tests in `tests/test_checkin_service.py` / a check-ins API test: approve
   session with a configured default roster template produces a roster log and
   returns its id; without a template returns `roster_id: null`.

### Slice D — Frontend: roster editor + check-ins jump

1. `frontend/src/api/roster.ts`: `submitRoster(id, content, netSlug)` → POST
   `/submit`.
2. `frontend/src/api/reminders.ts`: `submitReminder(...)` + `generateDueReminders(netSlug)`
   → POST `/generate`.
3. `frontend/src/pages/roster/DraftsTab.tsx`:
   - `useSearchParams` → `focus` id + optional `status`; auto-select that
     roster after load.
   - `DetailPanel`: draft buttons = Save/Preview/Regenerate/**Send**/**Discard**;
     drop standalone Approve. `Send` = submit (PATCH current content first, then
     submit). `Discard` = skip call, label renamed.
   - `approved` = Send + Discard; `sent` = Preview + Re-send.
4. `frontend/src/pages/CheckInsPage.tsx` `handleApprove`: navigate to
   `/nets/{slug}/roster?focus=<roster_id>` when present, else `/roster`.

### Slice E — Frontend: reminders drafts tab

1. `frontend/src/pages/reminders/DraftsTab.tsx`:
   - On mount: `generateDueReminders(slug)` then load; if any `draft` rows
     appeared, auto-select the most recent and open the panel.
   - `DetailPanel`: draft buttons = Save/Regenerate/**Send**/**Discard**; drop
     standalone Approve; `approved` = Send + Discard.
   - `Send` = submit (PATCH content first, then submit).

## Verification

- `.venv/bin/python -m pytest -q` (full suite).
- `nix-shell --run "ruff check"`.
- `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`.

## Commits (atomic)

1. `fix(members): make approve_session_checkins idempotent (members_finalized_at)`
   + migration
2. `feat(send): one-click submit endpoints for roster and reminder drafts`
3. `feat(checkins): auto-generate roster draft when closing a session`
4. `feat(roster-ui): Send/Discard in roster editor + ?focus deep-link from check-ins`
5. `feat(reminders-ui): auto-generate due drafts + Send/Discard in reminder editor`
6. `docs: one-click send design + plan`

## Risk notes

- The migration adds a nullable column — no backfill needed, safe on existing
  DBs (docker2 included).
- Roster `submit` on a `draft` sets `approved_at`/`approved_by` (audit intact).
- The existing `Generate draft` modal and status-filter tabs stay; they're just
  no longer the primary path.