# Reminder & Roster One-Click Send Design

Date: 2026-09-16
Status: Draft

## Problem

Sending reminders and rosters requires a three-step manual chain: generate a
draft, "approve" it, then navigate to a different status bucket (or page) to
send. That matches the API state machine but is clicky for a human net-control
operator. The approval step is ceremonial for a single operator — the person who
reviews and hits send *is* the approver.

Two related defects discovered while mapping the flow:

1. **Double member finalization.** `approve_session_checkins()` bumps
   `total_check_ins` unconditionally, and the normal flow calls it twice: once
   from the check-ins "approve session" button (which marks the session
   completed) and again from `approve_roster()`. Member totals silently
   double-count. Folding approve into send would make this the *default* path, so
   it must be fixed as part of this work.
2. **No human entry point.** Rosters rely on the check-ins page's "Approve all
   check-ins and mark completed" button, which then drops the operator on the
   roster page with no draft generated.

## Goals

- Mid-session flow (roster): closing a session automatically generates a roster
  draft and drops the operator straight into the roster editor for it.
- One-click **Send** in both editors: save any tweaks, approve (as the acting
  operator), and dispatch in a single action. Remove the standalone Approve
  button from the human path (API state machine and its statuses stay intact).
- **Discard** (skip) and **Regenerate** remain as first-class buttons beside
  Send.
- Reminder flow: the drafts tab auto-generates due drafts on load and presents
  them with the same Send/Discard/Regenerate affordances.
- Fix member double-counting so the combined flow is safe.

## Non-goals

- No change to the API statuses (`draft` / `approved` / `sent` / `skipped`).
- No change to the delivery backends, templates, or audit fields (`approved_by`,
  `approved_at`, `sent_at` are still recorded).
- No pagination or dashboard insertion.

## Design

### Backend

#### 1. Idempotent member finalization

Add `members_finalized_at: DateTime(tz, nullable)` to `NetSession` (migration).
`approve_session_checkins()` becomes idempotent:

- If `session.members_finalized_at` is already set, skip the member upsert loop
  (still ensure `status == COMPLETED`).
- Otherwise run the existing upsert, stamp `members_finalized_at`, set status
  COMPLETED.

This makes both the check-ins approve path and the new submit path safe to call
for the same session.

#### 2. Combined submit endpoints

Add `POST /api/nets/{net_slug}/roster/{id}/submit` and
`POST /api/nets/{net_slug}/reminders/{id}/submit` (NET_CONTROL).

Request body: optional draft content fields (same shape as the existing
`DraftUpdate` schemas) applied first when status is `draft`.

Behavior:
- `draft` → apply optional edits → approve (approver = current callsign; roster
  approval calls `approve_session_checkins`, now idempotent) → send (dispatch).
- `approved` → send immediately (retry path).
- `sent` / `skipped` → 409.
- Delivery failure → stays `approved`, raise 502 with the delivery-backend
  errors (mirrors existing send route).

Returns the final log. This is the "Save + Approve + Send" one-click contract
the UI uses; the individual `/approve` and `/send` routes remain for API/edge
use.

#### 3. Auto-generate roster draft on session close

In `checkins/routes.py` `approve_session_route`, after
`approve_session_checkins`:
- Look up the net's default roster template. If one exists and no roster log
  already exists for the session, call `generate_draft(db, session_id)`.
- Return `roster_id` (nullable) alongside the existing
  `{session_status, members_updated}` so the frontend can navigate straight to
  the roster editor for that draft.

`generate_draft` is already idempotent (returns the existing log if present), so
re-closing or re-hitting approve is harmless.

### Frontend

#### 4. Roster editor (roster/DraftsTab.tsx)

- **Focus param**: read `?focus=<roster_id>` (and `?status=` optionally) from the
  URL; on load, switch to the `draft` (or given) filter and open/select that
  roster in the detail panel. Used by the check-ins page jump.
- **Button set** in `DetailPanel` for `draft`:
  - `Save draft` (unchanged), `Regenerate from check-ins` (unchanged),
  - **`Send`** (primary): PATCH current content → `POST submit` → on success
    replace roster, toast; on 502 show delivery errors.
  - **`Discard`** (replaces the `Skip` label for drafts; same `skip` call),
  - `Preview` (unchanged).
  - Remove the standalone `Approve` button from the draft state.
- `approved`: `Send` (retry) + `Discard`.
- `sent`: `Preview` + `Re-send`.

#### 5. Check-ins page (CheckInsPage.tsx)

After the approve-session call succeeds, navigate to
`/nets/{slug}/roster?focus=<roster_id>` when the response includes one; fall
back to `/nets/{slug}/roster` when it doesn't (no default template configured).

#### 6. Reminder drafts tab (reminders/DraftsTab.tsx)

- On mount, call `POST /reminders/generate` (due-drafts route, already exists) to
  auto-create any due drafts, then load reminders and sessions. If the page
  already has draft rows needing attention, auto-select the most recent one and
  open the detail panel (same convenience as the roster focus jump).
- `DetailPanel` button set for `draft`: `Save draft`, `Regenerate from template`,
  **`Send`** (primary), **`Discard`** (skip). Remove standalone `Approve`.
- `approved`: `Send` + `Discard`.

#### 7. API clients

- `frontend/src/api/roster.ts`: add `submitRoster(id, content, netSlug)`.
- `frontend/src/api/reminders.ts`: add `submitReminder(id, content, netSlug)`
  and `generateDueReminders(netSlug)`.
- `frontend/src/types`: no new types needed (submit returns the existing
  Roster/Reminder shapes).

## Affected files

| File | Change |
|------|--------|
| `backend/modules/schedule/models.py` | add `members_finalized_at` |
| `alembic/versions/<new>.py` | migration: add column |
| `backend/modules/checkins/service.py` | idempotent `approve_session_checkins` |
| `backend/modules/checkins/routes.py` | auto-generate roster draft + return id |
| `backend/modules/roster/service.py` | `submit_roster` service |
| `backend/modules/roster/routes.py` | `POST /{id}/submit` |
| `backend/modules/reminders/service.py` | `submit_reminder` service |
| `backend/modules/reminders/routes.py` | `POST /{id}/submit` |
| `frontend/src/api/roster.ts` | `submitRoster` |
| `frontend/src/api/reminders.ts` | `submitReminder`, `generateDueReminders` |
| `frontend/src/pages/roster/DraftsTab.tsx` | focus param, Send/Discard buttons |
| `frontend/src/pages/reminders/DraftsTab.tsx` | auto-gen due, Send/Discard buttons |
| `frontend/src/pages/CheckInsPage.tsx` | navigate to roster editor with focus |
| `tests/*` | coverage for all of the above |

## Reminder of correctness

- `submit` fires approve only when the log is `draft`; retries on `approved` do
  not re-finalize members (idempotent guard) and do not re-stamp
  `approved_at`.
- Roster send purges session source files only on successful send (unchanged).
- Templates/lead times/cooldowns unchanged.