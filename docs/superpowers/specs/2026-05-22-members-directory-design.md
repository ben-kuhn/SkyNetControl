# Members Directory Design

## Overview

A page at `/members` that displays the long-term member roster as a sortable, searchable table with a drill-down panel showing each member's check-in history. Closes the spec gap "browsable in the UI with search and filter" from the master design's Long-term Roster section.

## Access

Any authenticated user. Matches the existing `GET /api/checkins/members` permission (`get_current_user`, no role gating). Listed in the sidebar under the existing nav entries available to all signed-in users.

## Backend

### Existing — no changes

`GET /api/checkins/members` — returns all members ordered by callsign. Response shape per member:

```json
{
  "callsign": "W0NE",
  "name": "Ben Kuhn",
  "first_check_in_date": "2026-01-15T18:30:00+00:00",
  "last_check_in_date": "2026-05-21T18:31:00+00:00",
  "total_check_ins": 12
}
```

### New endpoint

`GET /api/checkins/by-callsign/{callsign}` — returns the full check-in history for one callsign across all sessions. Used by the drill-down panel. Auth: `get_current_user` (matches `/members`).

Response: list ordered by session start_date descending. Each item embeds session metadata so the frontend doesn't need extra fetches.

```json
[
  {
    "id": 42,
    "session_id": 7,
    "session_date": "2026-05-21",
    "callsign": "W0NE",
    "name": "Ben Kuhn",
    "city": "Bozeman",
    "state": "MT",
    "mode": "Winlink",
    "comments": "Running well",
    "is_new_member": false,
    "parse_status": "auto",
    "timing_status": "on_time"
  }
]
```

If the callsign has no check-ins, returns `[]` (200). Callsign lookup is case-insensitive (normalize to upper).

## Frontend

### Route

`/members` — added to `App.tsx`, wrapped in `ProtectedRoute` with `minRole={["viewer", "net_control", "admin"]}` (matches the rest of the app's authenticated-only pages).

### Sidebar

New entry "Members" between "Check-ins" and "Schedule" (or whichever ordering reads best; keep adjacent to Check-ins since they're conceptually related).

### Page layout

Two-pane layout, same pattern as `CheckInsPage`:

- **Left pane (always visible):** members table with search input above it.
- **Right pane (conditional):** empty state when no row selected; member detail panel when one is selected. Selecting another row replaces the panel content. A close button (or clicking the selected row again) dismisses it.

On narrow viewports (`< lg`), the two panes stack vertically.

### Table

| Column | Source | Format |
|--------|--------|--------|
| Callsign | `callsign` | Monospace, bold |
| Name | `name` | Plain |
| First check-in | `first_check_in_date` | Short date (e.g. "Jan 15, 2026") |
| Last check-in | `last_check_in_date` | Short date |
| Total | `total_check_ins` | Right-aligned integer |

Behavior:

- Sortable column headers. Default sort: callsign ascending. Clicking a sorted column toggles asc/desc.
- Row click selects the row (highlights it and opens the detail panel). Clicking the selected row again deselects.
- No pagination; all members rendered in one pass.
- Empty state: "No members yet. Members are added automatically when their check-ins are approved."

### Search

Single text input above the table, placeholder "Search callsign or name…". Client-side filter, case-insensitive, matches on `callsign` or `name` substring. Filtering preserves the current sort and selection (if the selected row is filtered out, the panel stays open until the user dismisses or selects another).

### Detail panel

Header:
- Callsign in large monospace, name beside it
- Close button

Stats row (one line):
- First check-in: short date
- Last check-in: short date
- Total: count

History list (most recent first):
- Each row: session date, mode, comments (truncated with title attr on hover)
- Clicking a history row navigates to `/checkins?session=<id>` (the existing CheckInsPage)
- Empty fallback: "No check-ins recorded for this callsign." (Shouldn't occur since the member exists only because they checked in, but defensive.)

Loading state: spinner inside the panel while the by-callsign fetch resolves. Error state: short error message with a retry button.

## Data flow

1. Page mounts → `fetchMembers()` populates the table.
2. User clicks a row → set `selectedCallsign` state → effect triggers `fetchMemberHistory(callsign)` → render panel.
3. User types in search → derives filtered list via `useMemo`. No new fetch.
4. User clicks a history row → React Router navigates to CheckInsPage with `?session=<id>`.

## Testing

Backend:
- `tests/test_checkin_routes.py`: add tests for the new `/by-callsign/{callsign}` endpoint — happy path, case-insensitive match, empty result, auth required.

Frontend:
- TypeScript compilation passes (`tsc --noEmit`).
- Manual verification of the page: search filters in place, sort works on each column, row click opens panel, history rows link to the right session.

## Out of scope

- Editing or deleting members
- Profile photos, contact info, additional member fields
- Server-side pagination/filtering
- Exporting the member list (CSV/PDF)

These can be added later if needed; the user has explicitly stated pagination is not wanted.
