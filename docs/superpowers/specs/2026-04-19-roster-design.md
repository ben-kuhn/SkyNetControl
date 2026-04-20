# Module 5: Roster — Design Spec

## Overview

The Roster module generates post-session summaries from check-in data, with structured prose sections rendered via Jinja2 templates. Net control reviews check-in records and edits prose before approving. Approving the roster finalizes member records and marks the session complete. Sending assembles the full plain-text roster from live check-in data and prose sections.

## Data Model

### RosterTemplate

Configurable Jinja2 templates for roster prose sections. One default template exists; admins can create alternatives.

| Field | Type | Notes |
|-------|------|-------|
| `id` | Integer, PK | Auto-increment |
| `name` | String(255), unique | Human-readable label |
| `subject_template` | Text | Jinja2 — rendered subject line |
| `header_template` | Text | Jinja2 — session info prose (date, NCS, activity) |
| `welcome_template` | Text | Jinja2 — new member welcome messages |
| `comments_template` | Text | Jinja2 — comment response prose |
| `footer_template` | Text | Jinja2 — next week preview, closing, map link |
| `lead_time_days` | Integer, default 1 | Days after session end_date to auto-generate draft |
| `is_default` | Boolean, default False | Only one default at a time |

### RosterLog

One per session. Stores rendered prose sections (not the check-in table — that's live data from `check_ins`).

| Field | Type | Notes |
|-------|------|-------|
| `id` | Integer, PK | Auto-increment |
| `session_id` | Integer, FK(net_sessions.id), unique | One roster per session |
| `template_id` | Integer, FK(roster_templates.id), nullable | Preserved if template deleted |
| `status` | Enum: DRAFT, APPROVED, SENT, SKIPPED | Status lifecycle |
| `content_subject` | Text | Rendered subject line |
| `content_header` | Text | Rendered header prose |
| `content_welcome` | Text | Rendered welcome prose |
| `content_comments` | Text | Rendered comments prose |
| `content_footer` | Text | Rendered footer prose |
| `map_url` | String(500), nullable | Shareable map link if GPS data exists |
| `drafted_at` | DateTime(tz) | When draft was generated |
| `approved_at` | DateTime(tz), nullable | When NCS approved |
| `sent_at` | DateTime(tz), nullable | When posted to groups.io |
| `approved_by` | String(20), nullable | Approver callsign |

### RosterStatus Enum

`DRAFT` → `APPROVED` → `SENT`

`DRAFT` or `APPROVED` → `SKIPPED`

No other transitions are valid.

## Template Context Variables

The Jinja2 context dict passed to all roster template sections:

| Variable | Type | Source |
|----------|------|--------|
| `date` | str | Session start_date, formatted "Month Day, Year" |
| `time` | str | Season time, formatted "H:MM AM/PM" or "" |
| `day_of_week` | str | From season.day_of_week ("Monday", "Tuesday", etc.) |
| `net_control` | str | net_session.net_control_callsign or "" |
| `activity_title` | str | Activity title if activity session, else "" |
| `activity_instructions` | str | Activity instructions if activity session, else "" |
| `checkins` | list[dict] | Each dict: name, callsign, city, county, state, mode, comments, is_new_member |
| `new_members` | list[dict] | Subset of checkins where is_new_member is True |
| `total_count` | int | len(checkins) |
| `next_week_preview` | str | Next session preview (reuses reminders logic) |
| `map_url` | str | Shareable map URL if GPS data exists, else "" |

**Check-in dict ordering:** checkins are ordered by name (alphabetical), with fields: name, callsign, city, county, state, mode, comments, is_new_member.

## Workflow

### Automated Flow

1. **Net session ends** — PAT mailbox scan runs, imports check-in messages as `RawMessage` records, creates `CheckIn` records with parse status AUTO or MANUAL_REVIEW.
2. **`generate_due_drafts()`** scans for sessions past `end_date` (by `lead_time_days`) without a RosterLog. For each, renders template prose sections from check-in data and creates a DRAFT RosterLog.
3. **`notify_ncs(session)`** stub is called to notify net control that a draft is ready for review. (No-op for now; hook point for future notification system.)

### Review Flow

4. **Net control reviews check-ins** — uses existing checkin API (`GET /api/checkins/session/{id}`, `PATCH /api/checkins/{id}`) to correct names, cities, modes, etc. Check-in records are edited in place.
5. **Net control edits prose** — uses `PATCH /api/roster/{id}` to update welcome messages, comment responses, header, footer text.
6. **Net control previews** — `GET /api/roster/{id}/preview` assembles full plain text from current check-in data + stored prose sections. This shows exactly what "send" will produce.

### Approval and Send

7. **Approve** — `POST /api/roster/{id}/approve`. Calls `approve_session_checkins()` to finalize Member records and mark session COMPLETED. Sets roster status to APPROVED.
8. **Send** — `POST /api/roster/{id}/send`. Assembles final plain text (same as preview), marks status SENT. Groups.io posting is deferred — "send" just marks status for now.
9. **Skip** — `POST /api/roster/{id}/skip`. Valid from DRAFT or APPROVED. Sets status to SKIPPED.

## Service Layer

### Template CRUD

Same pattern as reminders module:
- `create_template(db, name, subject_template, header_template, welcome_template, comments_template, footer_template, lead_time_days, is_default)`
- `get_template(db, template_id)`
- `list_templates(db)`
- `update_template(db, template_id, **fields)`
- `delete_template(db, template_id)` — blocked if is_default

`_clear_default(db)` helper clears is_default on existing default when setting a new one.

### Context Building

`build_roster_context(db, net_session)` — builds the full Jinja2 context dict. Queries check-ins for the session, identifies new members, formats date/time from season, builds next_week_preview, determines map_url.

Date/time/day_of_week/activity/next_week_preview formatting reuses the same logic as the reminders module. The shared patterns are small enough to duplicate rather than extracting a shared utility.

### Rendering

`render_roster(template, context)` — renders all five template sections (subject, header, welcome, comments, footer) with the context dict. Each section wrapped in try/except for Jinja2 errors, storing error message on failure.

### Draft Generation

`generate_draft(db, session_id, template_id=None)` — idempotent (returns existing RosterLog if one exists for this session). Builds context, renders all sections, creates DRAFT RosterLog. Sets `map_url` if any check-ins have non-null latitude/longitude.

`generate_due_drafts(db)` — finds sessions where `end_date + lead_time_days <= today` and no RosterLog exists. Generates drafts for each. Calls `notify_ncs()` stub for each generated draft.

### Assembly

`assemble_roster(db, roster_id)` — reads current check-in data for the session, formats the plain-text table (columns: name, callsign, city, county, state, mode, comments; new members flagged with asterisk), concatenates: subject + header + table + welcome + comments + footer. Returns the full plain-text string.

This function is used by both the preview endpoint and the send action.

### Status Transitions

- `approve_roster(db, roster_id, approver_callsign)` — DRAFT → APPROVED. Calls `approve_session_checkins(db, session_id)` to finalize Member records.
- `mark_sent(db, roster_id)` — APPROVED → SENT. Calls `assemble_roster()` for the final text (for future groups.io posting).
- `skip_roster(db, roster_id)` — DRAFT or APPROVED → SKIPPED.
- `update_draft(db, roster_id, content_subject=None, content_header=None, content_welcome=None, content_comments=None, content_footer=None)` — edit prose sections while status is DRAFT.

### GeoJSON

`get_session_geojson(db, session_id)` — queries check-ins with non-null lat/lon for the given session. Returns a GeoJSON FeatureCollection. Each Feature includes properties: name, callsign, is_new_member.

### Notification Stub

`notify_ncs(db, net_session)` — no-op function. Takes the session, does nothing. Hook point for future notification system (email, in-app, etc.).

## API Endpoints

All under `/api/roster`. Auth required except where noted.

### Template Management (admin only)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/templates` | Create template |
| GET | `/templates` | List all templates |
| PATCH | `/templates/{id}` | Update template |
| DELETE | `/templates/{id}` | Delete (blocked if default) |

### Generation (admin, net_control)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/generate/{session_id}` | Generate draft for specific session |
| POST | `/generate` | Generate all due drafts |

### Roster Management

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/` | List rosters (optional `?status=` filter) | any authenticated |
| GET | `/session/{session_id}` | Get roster for session | any authenticated |
| GET | `/{id}/preview` | Assemble full plain text preview | any authenticated |
| PATCH | `/{id}` | Edit prose sections (DRAFT only) | admin, net_control |
| POST | `/{id}/approve` | Approve roster, finalize check-ins | admin, net_control |
| POST | `/{id}/send` | Mark as sent | admin, net_control |
| POST | `/{id}/skip` | Skip roster | admin, net_control |

### GeoJSON (public, no auth)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/session/{session_id}/geojson` | GeoJSON FeatureCollection of GPS check-ins |

## Error Handling

- **Generate for non-existent session:** 404
- **Generate when no default template:** 404 with detail message
- **Edit non-DRAFT roster:** 409 Conflict
- **Approve non-DRAFT roster:** 409 Conflict
- **Send non-APPROVED roster:** 409 Conflict
- **Skip SENT roster:** 409 Conflict
- **Delete default template:** 400
- **Invalid status filter:** 400
- **Jinja2 template errors:** caught per-section, error message stored in the section field, draft still created
- **Session with no check-ins:** draft generated with empty table; welcome and comments sections render with empty lists

## Seed Data

One default RosterTemplate with standard prose for W0NE Winlink Net:

- **Subject:** `W0NE Winlink Net Roster — {{ date }}`
- **Header:** Session date, net control, activity info if applicable, total count
- **Welcome:** Iterates `new_members` list with welcome message per new member
- **Comments:** Iterates `checkins` with non-empty comments, includes callsign and comment text
- **Footer:** Next week preview, map link if available, closing

## Deferred Items

- **Groups.io posting** — `mark_sent()` currently just updates status. Actual API integration is a future cross-cutting concern shared with reminders.
- **NCS notification** — `notify_ncs()` is a no-op stub. Future notification system will implement this.
- **Frontend** — React UI for roster review (check-in table editing, prose editing, preview, approve/send flow) to be built in a frontend phase.
- **Map frontend** — Leaflet.js page rendering the GeoJSON endpoint. The backend serves the data; the frontend is deferred.
- **Automated PAT scan trigger** — currently manual. Future scheduler/cron integration will automate the end-of-session scan + roster generation pipeline.
