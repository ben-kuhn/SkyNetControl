# Reminders Page Design

## Overview

A page at `/reminders` for net control to review/edit/approve/send/skip reminder drafts and manage reminder templates. Closes the spec gap "Module 3: Reminders" frontend.

## Access

`/reminders` route is already wrapped with `minRole={["net_control", "admin"]}` in `App.tsx`. Keep as-is.

## Layout

Top-level **Tabs**: `Drafts` (default) | `Templates`.

State is component-local; URL stays `/reminders` (no query param tracking for which tab is active — YAGNI; survives navigation but not page reload, which is fine for a small workflow page).

### Drafts tab

Above the list:
- "Generate draft" button. Click → modal with a session-picker → submit calls `POST /api/reminders/generate/{session_id}` → list refreshes, status sub-tab switches to `Draft`.
- The session picker lists sessions with `status === "scheduled"`, ordered by `start_date` ascending. Sessions that already have a reminder are still shown — the backend is idempotent (one reminder per session); re-generating returns the existing log, no error.

Status sub-tabs (with count badges):
- `Draft` (default)
- `Approved`
- `Sent`
- `Skipped`

The list is fetched once per Drafts-tab mount via `GET /api/reminders/` (returns all statuses) and grouped client-side. Counts come from the same response. Re-fetch after any successful action (approve/send/skip/generate/edit).

Each row (in any sub-tab):

| Column | Source | Notes |
|--------|--------|-------|
| Session date | derived from `session_id` via the session lookup (see Data section) | Sortable; default newest first |
| Subject | `content_subject` | Truncated; full subject visible via tooltip or panel |
| Status | `status` | Status pill; matches the active sub-tab |
| Action buttons | depend on status | See per-status below |

Action buttons per status:
- **Draft**: `View / Edit`, `Approve`, `Skip`
- **Approved**: `View`, `Send`, `Skip`
- **Sent**: `View`
- **Skipped**: `View`

Clicking the row (anywhere except a button) opens the **detail panel** on the right (two-pane layout, same pattern as CheckIns and Members).

### Detail panel

Header:
- Session date (bold)
- Status pill
- Close button

Body when `status === "draft"`:
- Editable `<input>` for `content_subject`
- Editable `<textarea>` for `content_body` (large, monospace, plain text editor — no markdown preview in v1)
- "Save draft" button — calls `PATCH /api/reminders/{id}` with the changed fields, refreshes the list
- "Regenerate from template" button — re-renders the subject and body against the current session and its assigned activity. Calls `POST /api/reminders/{id}/regenerate`. Shows a confirm dialog ("Replace the current subject and body with a fresh render? Any unsaved edits will be lost.") before sending. On success the panel fields update to the re-rendered content. Useful when an activity is assigned/changed after the draft was originally generated.
- "Approve" button — calls `POST /api/reminders/{id}/approve`
- "Skip" button — calls `POST /api/reminders/{id}/skip`

Body when `status === "approved"`:
- Read-only subject + body
- "Send" button — calls `POST /api/reminders/{id}/send`. If the response is a 409 with detail "Reminder not in approved status" — shouldn't happen, but defensive. If response indicates no delivery backends configured (the existing dispatch returns False, which makes `mark_sent` return None and the route returns 409), show a toast: "Send failed — verify delivery backends are configured."
- "Skip" button — calls `POST /api/reminders/{id}/skip`

Body when `status === "sent"` or `status === "skipped"`:
- Read-only subject + body
- Metadata (when applicable): `approved_at`, `approved_by`, `sent_at`

### Templates tab

A simple table:

| Column | Source |
|--------|--------|
| Name | `name` |
| Type | `template_type` (regular_checkin / activity) |
| Lead time | `lead_time_days` (number of days) |
| Default | `is_default` (badge "default" if true) |
| Actions | Edit / Delete buttons (Delete disabled when `is_default` is true) |

Above the table:
- "New template" button — opens a modal form

Edit row → opens the same modal form pre-filled.

Modal form fields:
- `name` (text)
- `template_type` (select: regular_checkin / activity)
- `subject_template` (text)
- `body_template` (textarea, larger)
- `lead_time_days` (number)
- `is_default` (checkbox)

Save: calls `POST /api/reminders/templates` (create) or `PATCH /api/reminders/templates/{id}` (update). Close on success, refresh list.

Delete button: calls `DELETE /api/reminders/templates/{id}`. Backend rejects if `is_default` is true (returns appropriate error); UI also disables the button as a hint.

## Backend

Most endpoints already exist. One new endpoint is added to support the regenerate affordance.

**Existing:**
- `GET /api/reminders/` (list, optional status filter)
- `GET /api/reminders/session/{session_id}` (single, not strictly needed by this page but available)
- `PATCH /api/reminders/{id}` (edit draft)
- `POST /api/reminders/{id}/approve`
- `POST /api/reminders/{id}/send`
- `POST /api/reminders/{id}/skip`
- `POST /api/reminders/generate/{session_id}` (manual generation)
- `GET/POST/PATCH/DELETE /api/reminders/templates` (template CRUD)

**New:**
- `POST /api/reminders/{id}/regenerate` (admin / net_control). Re-renders the draft from the current session state and template, overwriting `content_subject` and `content_body`. Returns 409 if the reminder is not in `draft` status (you can only regenerate while still editable). Returns 404 if the reminder doesn't exist. Backend implementation: a new service function `regenerate_draft(db, reminder_id)` that:
  1. Fetches the `ReminderLog`. If missing → return None (route → 404). If status ≠ DRAFT → return None with a sentinel (route → 409).
  2. Loads the session and the original template (via `log.template_id`; if null, fall back to the default template for the session's type — same logic as `generate_draft`).
  3. Calls `build_template_context(db, net_session)` and `render_reminder(template, context)` to get the fresh subject/body.
  4. Writes them onto the existing log, commits, returns the updated log.

The page reuses the existing `fetchRecentSessions` from `frontend/src/api/schedule.ts` for the session picker in the "Generate draft" modal.

## Frontend file structure

**New files:**

| File | Responsibility |
|------|---------------|
| `frontend/src/api/reminders.ts` | API client: `fetchReminders`, `fetchTemplates`, `updateReminderDraft`, `approveReminder`, `sendReminder`, `skipReminder`, `regenerateReminderDraft`, `generateReminderDraft`, `createTemplate`, `updateTemplate`, `deleteTemplate` |
| `frontend/src/pages/RemindersPage.tsx` | Top-level page with tab switcher; thin shell that mounts the two tab components |
| `frontend/src/pages/reminders/DraftsTab.tsx` | Drafts list + sub-tabs + detail panel + generate modal |
| `frontend/src/pages/reminders/TemplatesTab.tsx` | Templates table + form modal |

Splitting Drafts and Templates into separate components keeps each file focused. Both are imported by `RemindersPage.tsx`.

**Modified files:**

| File | Change |
|------|--------|
| `frontend/src/App.tsx` | Replace `PlaceholderPage title="Reminders"` with `<RemindersPage />` |
| `frontend/src/types/index.ts` | Add `Reminder` and `ReminderTemplate` types |

## Types

```typescript
export type ReminderStatus = "draft" | "approved" | "sent" | "skipped";
export type ReminderTemplateType = "regular_checkin" | "activity";

export interface Reminder {
  id: number;
  session_id: number;
  template_id: number | null;
  status: ReminderStatus;
  content_subject: string;
  content_body: string;
  drafted_at: string;
  approved_at: string | null;
  sent_at: string | null;
  approved_by: string | null;
}

export interface ReminderTemplate {
  id: number;
  name: string;
  template_type: ReminderTemplateType;
  subject_template: string;
  body_template: string;
  lead_time_days: number;
  is_default: boolean;
}
```

## Data flow

**Drafts tab on mount:**
1. `fetchReminders()` (no filter — fetch all, group client-side)
2. `fetchRecentSessions()` (cached/shared if already fetched elsewhere; otherwise call again — small payload)
3. Display Draft sub-tab by default.

**Edit flow:**
1. User clicks a Draft row → panel opens, fields populated from row data (no extra fetch).
2. User edits subject/body → local state, "Save draft" button enabled.
3. Click Save → `updateReminderDraft(id, {content_subject, content_body})` → on success, replace the row in the list with the response.

**Approve / Send / Skip:**
1. Optimistic update isn't worth the complexity here — these actions are infrequent.
2. Call the action, on success refetch the reminders list (or splice in the returned row).
3. If the action fails (e.g., 409, network), show a toast with the error detail.

**Generate flow:**
1. User clicks "Generate draft" → modal opens with session picker.
2. User picks session, clicks Generate → `generateReminderDraft(sessionId)` → on success, refetch list, close modal, switch to Draft sub-tab.

**Templates tab on mount:**
1. `fetchTemplates()` → renders table.

**Template create / update / delete:** identical pattern — call API, refetch on success, surface errors via toast.

## Error handling

- API errors surface as toasts. The existing `useToast` hook (used elsewhere in the app) covers this.
- 409 from approve/send/skip = stale UI state. Show toast and refetch the list to sync.
- 409 from send specifically may indicate no delivery backends configured. Show: "Send failed — verify delivery backends are configured (Config page)."
- Network failures: generic "Action failed — try again."

## Testing

Backend tests for the new regenerate endpoint:
- `tests/test_reminder_service.py`: tests for `regenerate_draft` — happy path (rewrites subject/body), 409 equivalent for non-draft status (returns sentinel), 404 equivalent for missing log, regenerates against new activity if the session's activity changed between generation and regenerate.
- `tests/test_reminder_routes.py`: tests for `POST /api/reminders/{id}/regenerate` — 200 on draft, 409 on non-draft, 404 on missing, auth required (admin/net_control).

Frontend:
- `tsc --noEmit` passes.
- Manual UI verification of the full workflow:
  - Generate a draft for an upcoming session
  - Edit the draft, save
  - Approve it
  - Send it (with delivery backends configured) — confirm transitions to Sent
  - Skip a different draft — confirm transitions to Skipped
  - Create a new template, edit it, delete a non-default template

## Out of scope

- Markdown preview of the body (plain text editor is sufficient for v1)
- In-app notifications when new drafts appear (covered by gap #5 — a separate plan)
- Template versioning or history
- Bulk approve/send/skip
- Unapprove (back to draft) action
- Diff view between original template render and current edited content
