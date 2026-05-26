# In-App Notifications Design

## Overview

A lightweight in-app notification system so net control gets a visible nudge when something needs their attention — a reminder draft is generated, check-ins arrive from a scan, a roster draft is ready, or a delivery send fails. Closes the spec gap "in-app notification" referenced in Modules 3, 4, and the master design's `notify_ncs` stub.

## Scope

Backend: new `notifications` module + table + API + hook integrations in reminders, check-ins, roster.
Frontend: bell icon + dropdown in the sidebar (and mobile menu).
Polling: every 60s while the app is open. No WebSockets, no push.

## Data model

New table `notifications`:

| Column | Type | Notes |
|--------|------|-------|
| `id` | int, pk | autoincrement |
| `recipient_callsign` | str, fk → `users.callsign`, not null | who sees it |
| `kind` | enum | `reminder_draft` / `checkins_ready` / `roster_draft` / `delivery_failure` |
| `session_id` | int, fk → `net_sessions.id`, nullable | session this notification is about; null for non-session events |
| `message` | text, not null | rendered text shown in the dropdown |
| `link_url` | str, nullable | e.g., `/reminders`, `/checkins?session=42`; null = no action |
| `created_at` | timestamptz, not null | |
| `read_at` | timestamptz, nullable | null = unread |

Index on `(recipient_callsign, read_at)` for the per-user unread query.

## Backend

### Module layout

```
backend/modules/notifications/
├── __init__.py
├── models.py         # Notification model, NotificationKind enum
├── service.py        # create_notification, list_for_user, mark_read, mark_all_read
└── routes.py         # API surface
```

### Service functions

```python
def create_notification(
    db: Session,
    recipient_callsign: str,
    kind: NotificationKind,
    message: str,
    link_url: str | None = None,
    session_id: int | None = None,
    dedupe: bool = True,
) -> Notification:
    """Insert a notification, deduping against unread (recipient, kind, session_id) when dedupe=True."""
```

Dedup rule: if `dedupe=True` and an unread notification exists with the same `(recipient_callsign, kind, session_id)`, return the existing row without inserting. This prevents spam when a draft is regenerated. Delivery failures pass `dedupe=False` since each failure is a discrete event worth seeing.

```python
def list_for_user(db: Session, callsign: str, include_read: bool = False) -> list[Notification]:
    """Newest first."""

def mark_read(db: Session, notification_id: int, callsign: str) -> Notification | None:
    """Mark single read iff it belongs to the user. Returns None if missing or not owned."""

def mark_all_read(db: Session, callsign: str) -> int:
    """Mark all the user's unread notifications read. Returns the count updated."""

def resolve_session_recipient(db: Session, net_session) -> str | None:
    """Return net_session.net_control_callsign if set; otherwise the lowest-id admin's callsign; otherwise None."""
```

### API routes

`backend/modules/notifications/routes.py`:

| Method | Path | Auth | Behavior |
|--------|------|------|----------|
| GET | `/api/notifications/` | `get_current_user` | List the user's notifications. `?all=1` includes read; default unread only. |
| POST | `/api/notifications/{id}/read` | `get_current_user` | Mark single read. 404 if missing/not owned. Returns the row. |
| POST | `/api/notifications/read-all` | `get_current_user` | Mark all read. Returns `{count: N}`. |

Response shape per notification:

```json
{
  "id": 7,
  "kind": "reminder_draft",
  "session_id": 42,
  "message": "Reminder draft ready for May 28 net",
  "link_url": "/reminders",
  "created_at": "2026-05-26T18:00:00+00:00",
  "read_at": null
}
```

### Hook integrations

Each call site uses `resolve_session_recipient` to determine `recipient_callsign`. If it returns `None` (no NCS, no admin), skip the notification — no recipient to notify. The message template is computed at the call site for clarity.

| Trigger | File / function | Notification details |
|---------|-----------------|----------------------|
| Reminder draft generated (auto, in `generate_due_drafts`) | `backend/modules/reminders/service.py` | kind=`reminder_draft`, message=`f"Reminder draft ready for {session_date_str}"`, link_url=`/reminders` |
| Reminder draft generated (manual, in `generate_draft` route handler) | `backend/modules/reminders/routes.py::generate_draft_route` after the service call | same as above |
| Check-ins imported | `backend/modules/checkins/service.py::scan_and_import_messages` after the loop, only when `len(checkins) > 0` | kind=`checkins_ready`, message=`f"{N} check-in(s) imported for {session_date_str}"`, link_url=`f"/checkins?session={session.id}"` |
| Roster draft generated (auto) | `backend/modules/roster/service.py::generate_due_drafts` — replace the `notify_ncs(db, session)` call | kind=`roster_draft`, message=`f"Roster draft ready for {session_date_str}"`, link_url=`/roster` |
| Roster draft generated (manual) | `backend/modules/roster/routes.py::generate_draft_for_session_route` after the service call | same as above |
| Delivery failure | `backend/modules/reminders/service.py::mark_sent` and `backend/modules/roster/service.py::mark_sent` — when `dispatch_delivery(...)` returns False | kind=`delivery_failure`, dedupe=False, message=`f"Send failed for {kind_human} on {session_date_str} — verify delivery backends"`, link_url=`/config` |

The `notify_ncs` stub is removed in favor of direct `create_notification` calls.

For check-ins, manual creation via the UI doesn't trigger a notification — only mailbox scans do. Manual entry is the net control doing it themselves; they don't need a notification.

### Date formatting

The message uses a short date format like `"May 28"` or `"May 28, 2026"` if the year differs from current. Implement once in `backend/modules/notifications/service.py` as `_format_session_date(net_session)` and reuse from all call sites. Don't reach into module-specific render helpers.

### Migration

New Alembic migration creates the `notifications` table per the data model above. Uses batch mode for SQLite compatibility. No seed data.

## Frontend

### Types

Add to `frontend/src/types/index.ts`:

```typescript
export type NotificationKind =
  | "reminder_draft"
  | "checkins_ready"
  | "roster_draft"
  | "delivery_failure";

export interface Notification {
  id: number;
  kind: NotificationKind;
  session_id: number | null;
  message: string;
  link_url: string | null;
  created_at: string;
  read_at: string | null;
}
```

### API client

New file `frontend/src/api/notifications.ts`:

- `fetchNotifications(includeRead = false): Promise<Notification[]>`
- `markRead(id: number): Promise<Notification>`
- `markAllRead(): Promise<{count: number}>`

### NotificationBell component

New file `frontend/src/components/NotificationBell.tsx`. Rendered next to the user's callsign/logout in the sidebar footer and the mobile menu footer (only when `user` is non-null).

Behavior:
- Auto-polls `fetchNotifications()` every 60 seconds via `setInterval` plus an initial fetch on mount.
- Renders a bell SVG. When unread count > 0, render a small accent badge with the count.
- Click → toggles a dropdown panel positioned above the bell (sidebar) or below (mobile menu).
- Dropdown shows: header ("Notifications" + "Mark all read" button), list (most-recent-first, max ~10 items shown; scroll for more), footer ("Show read" toggle), empty state ("No new notifications.").
- Each notification row: message in main text, relative time below (e.g., "5 min ago"). Click → POST `markRead`, then navigate to `link_url` via React Router's `navigate()` if present; close dropdown.
- "Mark all read" → POST, refetch, dropdown stays open.
- Click outside dropdown or press Escape → closes it.

State: `notifications`, `showRead`, `panelOpen`. Polling tied to `panelOpen === false` is overkill — just always poll while the bell is mounted (cheap, low frequency).

### Integration

- `frontend/src/layouts/Sidebar.tsx`: inside the authenticated footer block, render `<NotificationBell />` next to the existing profile/logout.
- `frontend/src/layouts/MobileMenu.tsx`: same treatment.

The bell is hidden for anonymous users (the public CheckInsPage doesn't surface notifications).

## Testing

### Backend

`tests/test_notifications_service.py`:
- `create_notification` happy path returns a row with the right fields.
- Dedupe: a second call with same recipient/kind/session_id and unread row returns the existing row (count stays at 1).
- Dedupe off: `dedupe=False` always inserts.
- `mark_read` works for own notification; returns None for someone else's.
- `mark_all_read` only affects current user's rows; returns the count.
- `resolve_session_recipient`: returns `net_control_callsign` when set; falls back to first admin; returns None if neither.

`tests/test_notifications_routes.py`:
- `GET /api/notifications/` returns the user's unread only by default.
- `?all=1` includes read rows.
- `POST /{id}/read` marks one read; another user's ID → 404.
- `POST /read-all` returns the count.
- All routes require auth (401 without cookie).

`tests/test_reminder_service.py`, `tests/test_checkin_service.py`, `tests/test_roster_service.py`: add tests verifying notifications are created when each hook fires.

### Frontend

- `tsc --noEmit` passes.
- Manual verification:
  - Sign in as net_control assigned to a future session, generate a reminder draft → bell shows badge "1", dropdown lists "Reminder draft ready for {date}", click → navigates to /reminders and badge clears.
  - Scan a mailbox with messages → "N check-in(s) imported for {date}" appears.
  - Generate roster draft → "Roster draft ready for {date}".
  - Attempt send when no delivery backends are configured → "Send failed for reminder on {date} — verify delivery backends" appears.
  - Mark all read → all clear.
  - Toggle "Show read" → previously read items reappear in muted styling.

## Out of scope

- Email delivery of notifications (the existing reminder email setting is a separate flow).
- Per-user notification preferences (mute by kind, snooze, etc.).
- WebSockets / SSE / push notifications.
- Retention policy (delete-after-N-days) — read notifications stay in the DB; admins can purge via direct DB access if needed.
- Notification grouping/threading (multiple drafts for the same session over time — dedupe handles the worst case; everything else is its own row).
- Sound or browser notifications.
