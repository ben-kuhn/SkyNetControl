# Roster Page Design

## Overview

A page at `/roster` for net control to review/edit/approve/send/skip roster drafts and manage roster templates. Closes the spec gap "Module 5: Roster" frontend. Mirrors the Reminders page structure (shipped previously); differs because rosters have four editable body sections plus a separately-assembled preview.

## Access

`/roster` route is already wrapped with `minRole={["net_control", "admin"]}` in `App.tsx`. Keep as-is.

## Layout

Top-level **Tabs**: `Drafts` (default) | `Templates`. State is component-local; URL stays `/roster`.

### Drafts tab

Above the list:
- "+ Generate draft" button. Click → modal with a session picker → submit calls `POST /api/roster/generate/{session_id}` → list refreshes, status sub-tab switches to `Draft`, the new draft becomes the selected row.
- The session picker lists sessions with `status === "completed"`, ordered by `start_date` descending (rosters are typically built right after the net runs). Sessions that already have a roster are still shown — `generate_draft` is idempotent and returns the existing log.

Status sub-tabs (with count badges):
- `Draft` (default)
- `Approved`
- `Sent`
- `Skipped`

The list is fetched once per Drafts-tab mount via `GET /api/roster/` (returns all statuses) and grouped client-side. Re-fetch after any successful action (approve/send/skip/regenerate/generate/save).

Each row:

| Column | Source | Notes |
|--------|--------|-------|
| Session date | from session lookup (separate fetch of `/api/schedule/sessions`) | Default sort: most recent first |
| Subject | `content_subject` | Truncated; full subject in detail panel |
| Status | `status` | Status pill |

Click row → selects it, opens the detail panel on the right (two-pane layout, same pattern as CheckIns/Members/Reminders).

### Detail panel

Header:
- Session date (long format) + status pill
- Close button
- Metadata line: `Drafted <date>` · `Approved <date> by <callsign>` (when applicable) · `Sent <date>` (when applicable)
- Below header: small clickable link showing `session_url` (e.g. `https://app.example.com/checkins?session=42`) so net control can verify the public link embedded in the body.

Subject:
- Single-line text input. Disabled when status isn't `draft`.

Body sections (stacked, in this order):
1. **Header** — textarea, ~6 rows
2. **Welcome** — textarea, ~6 rows
3. **Comments** — textarea, ~6 rows
4. **Footer** — textarea, ~4 rows

All four section textareas are disabled when status isn't `draft`. Each has a label above it.

Actions (gated by status):
- **Draft**: `Save draft` (primary) · `Preview` · `Regenerate from check-ins` · `Approve` · `Skip`
- **Approved**: `Preview` · `Send` (primary) · `Skip`
- **Sent**: `Preview` only
- **Skipped**: `Preview` only

Save → `PATCH /api/roster/{id}` with whichever of `content_subject`/`content_header`/`content_welcome`/`content_comments`/`content_footer` changed. Backend returns the updated log; replace the row in state.

Approve → `POST /api/roster/{id}/approve`. Transitions to APPROVED.

Send → `POST /api/roster/{id}/send`. Returns 409 if the underlying delivery dispatcher returns False (no backends configured). UI shows: "Send failed — verify delivery backends are configured (Config page)." Otherwise transitions to SENT.

Skip → confirm prompt → `POST /api/roster/{id}/skip`. Transitions to SKIPPED.

Preview → `GET /api/roster/{id}/preview` → opens a modal showing the assembled body text (monospace, scrollable). Close to dismiss.

Regenerate → confirm prompt ("Replace all sections with a fresh render from current check-ins? Any unsaved edits will be lost.") → `POST /api/roster/{id}/regenerate` (new endpoint, see Backend). On success, the detail panel field state resets to the returned content.

### Templates tab

Same shape as the Reminders Templates tab, minus the type dropdown (rosters have one template kind, no `template_type`).

Table columns:
- Name
- Lead time (days)
- Default (badge when `is_default`)
- Actions: Edit / Delete (Delete disabled when `is_default`)

"+ New template" button above the table → opens form modal. Edit row → opens the same modal pre-filled.

Modal form fields:
- `name` (text, required)
- `subject_template` (text, required)
- `header_template` (textarea, required)
- `welcome_template` (textarea, required)
- `comments_template` (textarea, required)
- `footer_template` (textarea, required)
- `lead_time_days` (number, default 1)
- `is_default` (checkbox)

Each textarea has a small helper line showing available placeholders: `{{ date }}`, `{{ time }}`, `{{ day_of_week }}`, `{{ net_control }}`, `{{ activity_title }}`, `{{ activity_instructions }}`, `{{ next_week_preview }}`, `{{ session_url }}`, `{{ total_count }}`, `{{ checkins }}`, `{{ new_members }}` — the last three are loops/lists usable via `{% for c in checkins %}…{% endfor %}` style.

Save → `POST /api/roster/templates` (create) or `PATCH /api/roster/templates/{id}` (update). Refresh list on success.

Delete → confirm prompt → `DELETE /api/roster/templates/{id}`. Backend rejects default templates with 400; UI also disables the button. Permission: admin/net_control (matches reminders parity — confirm during implementation; if backend currently requires admin-only, apply the same one-line fix we did for reminders).

## Backend

Most endpoints already exist. One new endpoint added for regenerate.

**Existing:**
- `GET /api/roster/` (list, optional status filter)
- `GET /api/roster/session/{session_id}`
- `GET /api/roster/{id}/preview`
- `PATCH /api/roster/{id}` (edit any of the five content fields)
- `POST /api/roster/{id}/approve`
- `POST /api/roster/{id}/send`
- `POST /api/roster/{id}/skip`
- `POST /api/roster/generate/{session_id}` (manual generation; idempotent)
- `POST /api/roster/generate` (generate all due)
- `GET/POST/PATCH/DELETE /api/roster/templates` (template CRUD)

**New:**
- `POST /api/roster/{id}/regenerate` (admin/net_control). Re-renders all four sections + subject from the current session, check-ins, and template. Overwrites the stored content fields. Returns 409 if not in `draft`, 404 if missing. New service function `regenerate_draft(db, roster_id)`:
  1. Fetch `RosterLog`. Return None if missing or status != DRAFT.
  2. Load `NetSession`; return None if missing.
  3. Load template (try `log.template_id`; fall back to the default template); return None if no template.
  4. Call `build_roster_context(db, net_session)` and `render_roster(template, context)`.
  5. Overwrite `content_subject`, `content_header`, `content_welcome`, `content_comments`, `content_footer`, and `session_url` from the rendered output.
  6. Commit, return the updated log.

**Delete-template permission check:** Confirm `DELETE /api/roster/templates/{id}` allows both admin and net_control (matching reminders post-fix). If it currently restricts to admin only, update it as part of this plan.

## Frontend file structure

**New files:**

| File | Responsibility |
|------|---------------|
| `frontend/src/api/roster.ts` | API client: list / save / approve / send / skip / generate / regenerate / preview rosters; CRUD templates |
| `frontend/src/pages/RosterPage.tsx` | Top-level shell with the Drafts/Templates tab switcher |
| `frontend/src/pages/roster/DraftsTab.tsx` | List + sub-tabs + detail panel + Generate modal + Preview modal |
| `frontend/src/pages/roster/TemplatesTab.tsx` | Template table + form modal |

**Modified files:**

| File | Change |
|------|--------|
| `backend/modules/roster/service.py` | Add `regenerate_draft` |
| `backend/modules/roster/routes.py` | Add `POST /{id}/regenerate`; widen DELETE template role if needed |
| `tests/test_roster_service.py` | Tests for `regenerate_draft` |
| `tests/test_roster_routes.py` | Tests for the new route |
| `frontend/src/types/index.ts` | Add `Roster`, `RosterStatus`, `RosterTemplate` types |
| `frontend/src/App.tsx` | Replace `PlaceholderPage title="Roster"` with `<RosterPage />` |

## Types

```typescript
export type RosterStatus = "draft" | "approved" | "sent" | "skipped";

export interface Roster {
  id: number;
  session_id: number;
  template_id: number | null;
  status: RosterStatus;
  content_subject: string;
  content_header: string;
  content_welcome: string;
  content_comments: string;
  content_footer: string;
  session_url: string | null;
  drafted_at: string;
  approved_at: string | null;
  sent_at: string | null;
  approved_by: string | null;
}

export interface RosterTemplate {
  id: number;
  name: string;
  subject_template: string;
  header_template: string;
  welcome_template: string;
  comments_template: string;
  footer_template: string;
  lead_time_days: number;
  is_default: boolean;
}
```

## Data flow

**Drafts tab on mount:**
1. `Promise.all([fetchRosters(), fetchSessions()])` (sessions for date lookup).
2. Show Draft sub-tab by default.

**Edit:**
1. User clicks a Draft row → panel opens, fields populated.
2. User edits any field → local state, "Save draft" enabled.
3. Click Save → `PATCH /api/roster/{id}` with changed fields → replace row in state.

**Approve / Send / Skip / Regenerate:**
- No optimistic update; call API, on success splice in the returned row, on failure show toast and refetch.

**Preview:**
- Click Preview → fetch `/api/roster/{id}/preview` → render assembled text in a modal.

**Generate:**
- Modal session picker → `POST /api/roster/generate/{sessionId}` → adds/replaces row, switches to Draft sub-tab, selects new row, closes modal.

**Templates tab on mount:** `fetchRosterTemplates()`. CRUD same pattern as reminders.

## Error handling

- Toasts for API errors.
- 409 from approve/send/skip = stale state → refetch + toast.
- 409 from send specifically → "verify delivery backends are configured."

## Testing

Backend:
- `tests/test_roster_service.py`: tests for `regenerate_draft` — happy path (rewrites all five content fields), picks up changes when check-ins are added between generation and regenerate, 409-equivalent for non-draft, 404-equivalent for missing.
- `tests/test_roster_routes.py`: tests for `POST /api/roster/{id}/regenerate` — 200 on draft, 409 on non-draft, 404 on missing, 403 for viewer.

Frontend:
- `tsc --noEmit` passes.
- Manual UI verification of the full workflow:
  - Generate a draft for a completed session
  - Edit individual sections → Save
  - Preview → modal renders assembled body
  - Regenerate → sections reset to template-rendered content
  - Approve → transitions
  - Send (with delivery backends configured) → transitions to Sent
  - Skip a different draft → transitions to Skipped
  - Create / edit / delete templates; default template delete is blocked

## Out of scope

- Markdown rendering inside the preview modal (plain text is sufficient)
- In-app notifications when drafts appear (covered by gap #5)
- Per-row check-in editing from the roster page (use the Check-ins page)
- Template versioning or diff view
- Bulk approve/send/skip
- Unapprove (back to draft)
