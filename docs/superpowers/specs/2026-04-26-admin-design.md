# Admin Sub-Project — Design Spec

**Goal:** Build the User Management and App Configuration frontend pages, replacing the current placeholder pages at `/users` and `/config`. Add an audit log that records all admin actions (role changes, callsign approvals/rejections, config changes) for accountability.

**Architecture:** Two new page components (`UsersPage.tsx`, `ConfigPage.tsx`) following existing patterns (ProfilePage, SchedulePage). Two new API client modules. A new `AuditLog` model and service on the backend, with existing admin endpoints wired to log actions. An audit log section on the Users page shows recent admin activity.

**Tech Stack:** React, TypeScript, Tailwind CSS (existing frontend), Python, FastAPI, SQLAlchemy (existing backend)

---

## Audit Log

### Data Model

New `AuditLog` table in `backend/audit/models.py`:

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer, PK | Auto-increment |
| `actor_callsign` | String(20), FK → users.callsign | Admin who performed the action |
| `action` | String(50), NOT NULL | Action type (see below) |
| `target_callsign` | String(20), nullable | User affected (for user actions) |
| `details` | Text, nullable | JSON string with action-specific data |
| `created_at` | DateTime(tz), NOT NULL | Timestamp |

### Action Types

| Action | Target | Details |
|--------|--------|---------|
| `user.role_changed` | affected user | `{"from": "pending", "to": "viewer"}` |
| `user.callsign_approved` | affected user | `{"old": "W0OLD", "new": "W0NEW"}` |
| `user.callsign_rejected` | affected user | `{"pending": "W0NEW"}` |
| `config.updated` | null | `{"key": "default_net_control", "value": "W0NE"}` |

### Service

`backend/audit/service.py` — single function:

```python
def log_action(db: Session, actor: str, action: str, target: str | None = None, details: dict | None = None) -> None
```

Called from existing admin route handlers after the action succeeds. The audit log write is part of the same database transaction — if the action fails, no log entry is created.

### API Endpoint

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/audit/` | ADMIN | List recent audit log entries |

Query parameters:
- `limit` — max entries to return (default 50, max 200)

Returns entries newest-first with `actor_callsign`, `action`, `target_callsign`, `details` (parsed as object), `created_at`.

### Wiring to Existing Endpoints

The existing admin endpoints in `backend/auth/routes.py` and `backend/config_mgmt/routes.py` are modified to call `log_action()` after each successful mutation:

- `PATCH /api/auth/users/{callsign}` → logs `user.role_changed`
- `POST /api/auth/users/{callsign}/approve-callsign` → logs `user.callsign_approved`
- `DELETE /api/auth/users/{callsign}/pending-callsign` → logs `user.callsign_rejected`
- `PUT /api/config/{key}` → logs `config.updated`

### Migration

New Alembic migration creating the `audit_log` table.

---

## Existing Backend Endpoints

These endpoints are already implemented and tested:

### User Management (`backend/auth/routes.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/auth/users` | ADMIN | List all users |
| `PATCH` | `/api/auth/users/{callsign}` | ADMIN | Update user role |
| `POST` | `/api/auth/users/{callsign}/approve-callsign` | ADMIN | Approve pending callsign change |
| `DELETE` | `/api/auth/users/{callsign}/pending-callsign` | ADMIN | Reject pending callsign change |

### App Configuration (`backend/config_mgmt/routes.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/config/` | Any authenticated user | Get all config as key-value dict |
| `PUT` | `/api/config/{key}` | ADMIN | Set a config value |

---

## Users Page (`/users`)

Admin-only page displaying all users in a table with inline management actions.

### Layout

- Page heading: "Users"
- Top-right controls: callsign search input + role filter dropdown
- Pending actions banner (conditional, shown when pending callsign changes or pending-role users exist)
- User table
- User count footer

### Table Columns

| Column | Content |
|--------|---------|
| Callsign | Monospace, accent color. If user has `pending_callsign`, show arrow notation below: current → pending |
| Name | Plain text |
| Role | Inline `<select>` dropdown for other users. Static badge for the current admin's own row (cannot change own role). Options: pending, viewer, net control, admin |
| Email | Muted color, plain text |
| Actions | Approve/Reject buttons when `pending_callsign` is set. Empty dash otherwise |

### Behaviors

- **Role change:** Selecting a new role from the dropdown immediately calls `PATCH /api/auth/users/{callsign}` with the new role. On success, show a toast ("Role updated to viewer") and refresh the list. On error, revert the dropdown and show an error toast.
- **Callsign approval:** "Approve" calls `POST /api/auth/users/{callsign}/approve-callsign`. "Reject" calls `DELETE /api/auth/users/{callsign}/pending-callsign`. Both refresh the list on success and show a toast.
- **Pending actions banner:** Counts users with `pending_callsign !== null` plus users with `role === "pending"`. Shows "{N} pending actions require attention" with a warning-colored dot. Hidden when count is zero.
- **Search:** Filters the displayed table client-side by callsign (case-insensitive substring match). Applied as the user types (no submit button).
- **Role filter:** Dropdown with "All roles", "Admin", "Net Control", "Viewer", "Pending". Filters client-side.
- **Self-protection:** The current user's row shows a static role badge instead of a dropdown. This prevents admins from accidentally demoting themselves.
- **Loading state:** Spinner while fetching users. Error state with retry if fetch fails.
- **Empty state:** "No users found" when search/filter produces no results.

### Audit Log Section

Below the user table, a "Recent Activity" section shows the most recent admin actions:

- Fetches `GET /api/audit/?limit=20` on page load
- Each entry rendered as a single line: timestamp, actor callsign (monospace), human-readable action description
- Examples: "W0NE changed KD0ABC role from pending to viewer", "W0NE approved callsign change W0OLD → W0NEW", "W0NE updated config default_net_control"
- Muted text styling, compact list
- No pagination — just the most recent 20 entries

---

## Config Page (`/config`)

Admin-only page displaying application settings as a structured form grouped by purpose.

### Known Config Keys

| Key | Label | Group | Input Type | Placeholder | Help Text |
|-----|-------|-------|-----------|-------------|-----------|
| `default_net_control` | Default Net Control Callsign | Net Operations | text (monospace) | `W0NE` | Callsign assigned to new sessions by default |
| `net_address` | Net Winlink Address | Net Operations | text | `w0ne@winlink.org` | Winlink address used for check-in message parsing |
| `pat_mailbox_path` | PAT Mailbox Path | Integrations | text (monospace) | `/home/pat/.local/share/pat/mailbox/W0NE` | Local filesystem path to the PAT Winlink client mailbox directory |
| `claude_api_key` | Claude API Key | Integrations | password with toggle | `sk-ant-...` | API key for Claude-powered activity brainstorming (optional) |

### Layout

- Page heading: "Configuration"
- Two grouped sections, each in a card with an uppercase section label:
  - **Net Operations** — `default_net_control`, `net_address`
  - **Integrations** — `pat_mailbox_path`, `claude_api_key`
- Each field: label, input + Save button on same row, help text below

### Behaviors

- **Per-field save:** Each field has its own Save button. Clicking Save calls `PUT /api/config/{key}` with the current input value. On success, show a toast ("Setting saved"). On error, show error toast.
- **Initial load:** `GET /api/config/` fetches all config as a dict. Each field is populated with its current value, or left empty (showing placeholder) if the key has no value.
- **Password field:** The `claude_api_key` field uses `type="password"` with a show/hide toggle button. The raw value is stored and sent as-is — no server-side masking (the config API stores plain strings).
- **Dirty tracking:** The Save button is visually muted/disabled when the field value matches the last-saved value. Becomes active when the user edits the field.
- **Loading state:** Spinner while fetching config. Error state with retry if fetch fails.
- **Monospace inputs:** `default_net_control` and `pat_mailbox_path` use monospace font.

---

## New/Modified Files

| File | Change |
|------|--------|
| `backend/audit/__init__.py` | Create: empty |
| `backend/audit/models.py` | Create: `AuditLog` SQLAlchemy model |
| `backend/audit/service.py` | Create: `log_action()` function |
| `backend/audit/routes.py` | Create: `GET /api/audit/` endpoint |
| `backend/app.py` | Modify: register audit router |
| `backend/auth/routes.py` | Modify: add `log_action()` calls to role change, callsign approve/reject |
| `backend/config_mgmt/routes.py` | Modify: add `log_action()` call to config set |
| `alembic/env.py` | Modify: import audit models |
| `alembic/versions/xxx_add_audit_log.py` | Create: migration for `audit_log` table |
| `frontend/src/api/users.ts` | Create: `fetchUsers`, `updateUserRole`, `approveCallsign`, `rejectCallsign` |
| `frontend/src/api/config.ts` | Create: `fetchConfig`, `setConfigValue` |
| `frontend/src/api/audit.ts` | Create: `fetchAuditLog` |
| `frontend/src/types/index.ts` | Modify: add `AuditEntry` type |
| `frontend/src/pages/UsersPage.tsx` | Create: full user management table page with audit log section |
| `frontend/src/pages/ConfigPage.tsx` | Create: structured config form page |
| `frontend/src/App.tsx` | Modify: replace `PlaceholderPage` with `UsersPage` and `ConfigPage` for `/users` and `/config` routes |

### API Module: `api/users.ts`

```typescript
fetchUsers(): Promise<User[]>
// GET /api/auth/users

updateUserRole(callsign: string, role: UserRole): Promise<void>
// PATCH /api/auth/users/{callsign} body: { role }

approveCallsign(callsign: string): Promise<void>
// POST /api/auth/users/{callsign}/approve-callsign

rejectCallsign(callsign: string): Promise<void>
// DELETE /api/auth/users/{callsign}/pending-callsign
```

### API Module: `api/config.ts`

```typescript
fetchConfig(): Promise<Record<string, string>>
// GET /api/config/

setConfigValue(key: string, value: string): Promise<void>
// PUT /api/config/{key} body: { value }
```

### API Module: `api/audit.ts`

```typescript
fetchAuditLog(limit?: number): Promise<AuditEntry[]>
// GET /api/audit/?limit={limit}
```

### Types

The existing `User` interface in `types/index.ts` already has all the fields returned by `GET /api/auth/users`. One new type added:

```typescript
interface AuditEntry {
  id: number;
  actor_callsign: string;
  action: string;
  target_callsign: string | null;
  details: Record<string, string> | null;
  created_at: string;
}
```

---

## Testing

Backend tests covering:
- Audit log creation (log_action writes correct entries)
- Audit log query endpoint (returns entries newest-first, respects limit)
- Audit log wiring (role change, callsign approve/reject, config update all produce log entries)
- Auth: only admins can read audit log

No frontend unit tests in this spec (consistent with existing frontend approach).

---

## What This Spec Does NOT Build

- **Bulk user actions** (select multiple, batch role change) — not needed at current scale
- **User creation/deletion by admin** — users self-register via OAuth; admins approve by changing role from pending
- **Config key creation/deletion** — only the 4 known keys are exposed; arbitrary key-value editing is not needed
- **Config value validation** — the backend stores plain strings; the frontend uses appropriate input types but does not enforce formats
- **Audit log pagination** — only shows most recent 20 entries on the Users page; full history available via API
- **Audit log filtering** — no filtering by action type or actor in the UI
