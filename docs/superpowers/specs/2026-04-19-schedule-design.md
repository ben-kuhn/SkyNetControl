# Module 1: Net Schedule — Design Spec

**Goal:** Manage net seasons (recurring schedule definitions) and net sessions (individual occurrences). Seasons auto-generate sessions based on recurrence patterns. Ad-hoc sessions can be created independently for real-world events or one-off additions. All times are UTC per ham radio convention.

**Architecture:** Follows the existing `models.py / service.py / routes.py` module pattern. Season creation triggers automatic session generation. Individual sessions are mutable for overrides.

**Tech Stack:** FastAPI, SQLAlchemy 2.0+, Alembic

---

## Data Model

### Enums

**SessionType:**
- `REGULAR_CHECKIN` — standard weekly check-in
- `ACTIVITY` — practice activity week
- `REAL_EVENT` — actual emergency or real-world exercise

**SessionStatus:**
- `SCHEDULED`
- `COMPLETED`
- `CANCELLED`

### NetSeason

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | Integer | PK, auto-increment |
| `name` | String(255) | NOT NULL |
| `start_date` | Date | NOT NULL |
| `end_date` | Date | NOT NULL |
| `day_of_week` | Integer | nullable (0=Monday, 6=Sunday) |
| `time` | Time | nullable, always UTC |
| `is_week_long` | Boolean | NOT NULL, default False |
| `activity_cadence` | Integer | NOT NULL, default 2 |

Relationship: `sessions` → list of NetSession (cascade all, delete-orphan).

`day_of_week` uses Python's weekday convention (0=Monday, 6=Sunday). Converted to display strings ("Monday", "Thursday", etc.) for templates and API responses.

### NetSession

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | Integer | PK, auto-increment |
| `season_id` | Integer | FK to `net_seasons.id`, **nullable** |
| `start_date` | Date | NOT NULL |
| `end_date` | Date | **nullable** (null = open-ended, for real events) |
| `grace_period_hours` | Float | NOT NULL, default 24.0 |
| `session_type` | Enum(SessionType) | NOT NULL |
| `status` | Enum(SessionStatus) | NOT NULL, default SCHEDULED |
| `activity_id` | Integer | nullable |
| `net_control_callsign` | String(20) | nullable |

Relationship: `season` → NetSeason (many-to-one, nullable).

Key design decisions:
- **`season_id` nullable** — real event sessions are season-independent.
- **`end_date` nullable** — real event sessions stay open until the operator explicitly closes them.
- **`REAL_EVENT` session type** — uses the same downstream features (check-ins, roster, mapping) but is always manually created and skips the reminder flow since there's no lead time.

---

## Service Layer

### Session Generation

`generate_sessions(db, season, default_net_control, default_grace_period_hours=24.0)` — auto-generates NetSession records for a season based on its recurrence pattern. Two internal strategies:

- **`_generate_weekly_sessions`** — finds the first occurrence of `day_of_week` on or after `start_date`, then creates one session per week. Activity weeks assigned by cadence (`index % activity_cadence == 1`). Each session's `end_date` is `start_date + 1 day`.
- **`_generate_week_long_sessions`** — creates consecutive 7-day sessions. Last session truncated to season `end_date`. Same cadence logic for activity type.

Both set `net_control_callsign` from the default, `status` to `SCHEDULED`, and `grace_period_hours` from the provided default.

**Cadence logic:** `index % activity_cadence == 1` means the second session in a season is the first activity week, then every Nth after that. Setting `activity_cadence=0` means no activity weeks (all sessions are `REGULAR_CHECKIN`).

### Season CRUD

- `create_season(db, name, start_date, end_date, day_of_week, time, is_week_long, activity_cadence, default_net_control, default_grace_period_hours)` — creates the season record then calls `generate_sessions()`. Returns the season with its generated sessions.
- `get_season(db, season_id)` → `NetSeason | None`
- `list_seasons(db)` → `list[NetSeason]` — ordered by `start_date` descending.
- `delete_season(db, season_id)` — cascades to all sessions and their downstream data (reminders, check-ins, rosters).

No `update_season` — changing recurrence parameters after sessions are generated would require regeneration logic that isn't worth the complexity. Delete and recreate instead.

### Session Management

- `create_session(db, start_date, end_date, session_type, season_id=None, grace_period_hours=24.0, net_control_callsign=None, activity_id=None)` — creates a single ad-hoc session. Used for real events (no season, no end date) and one-off additions to a season.
- `update_session(db, session_id, **fields)` — updates any mutable fields. No status transition validation — the operator is trusted.
- `get_session(db, session_id)` → `NetSession | None`
- `list_sessions(db, season_id=None, status=None)` — list sessions with optional filters. Sessions without a season (real events) included when `season_id` is not specified.

---

## API Endpoints

All under `/api/schedule`. Auth follows existing patterns.

### Season Management

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/seasons` | Admin | Create season + auto-generate sessions |
| GET | `/seasons` | Authenticated | List all seasons (descending by start_date) |
| GET | `/seasons/{id}` | Authenticated | Get season with its sessions |
| GET | `/seasons/{id}/sessions` | Authenticated | List sessions for a season |
| DELETE | `/seasons/{id}` | Admin | Delete season and cascade all downstream data |

### Session Management

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/sessions` | Admin/NetControl | Create ad-hoc session (real events, one-offs) |
| GET | `/sessions` | Authenticated | List sessions (optional `?season_id=`, `?status=` filters) |
| GET | `/sessions/{id}` | Authenticated | Get single session |
| PATCH | `/sessions/{id}` | Admin/NetControl | Update session fields (no transition guards) |

### Pydantic Schemas

**SeasonCreate:**
- `name: str`
- `start_date: date`
- `end_date: date`
- `day_of_week: int | None = None`
- `time: str | None = None` — `"HH:MM"` format, always UTC
- `is_week_long: bool = False`
- `activity_cadence: int = 2`

**SeasonResponse:**
- `id: int`
- `name: str`
- `start_date: date`
- `end_date: date`
- `day_of_week: int | None`
- `time: str | None`
- `is_week_long: bool`
- `activity_cadence: int`
- `sessions: list[SessionResponse]`

**SessionCreate:**
- `start_date: date`
- `end_date: date | None = None`
- `session_type: SessionType`
- `season_id: int | None = None`
- `grace_period_hours: float = 24.0`
- `net_control_callsign: str | None = None`
- `activity_id: int | None = None`

**SessionUpdate:**
- `status: SessionStatus | None = None`
- `session_type: SessionType | None = None`
- `net_control_callsign: str | None = None`
- `activity_id: int | None = None`
- `grace_period_hours: float | None = None`
- `end_date: date | None = None`

**SessionResponse:**
- `id: int`
- `season_id: int | None`
- `start_date: date`
- `end_date: date | None`
- `grace_period_hours: float`
- `session_type: str`
- `status: str`
- `activity_id: int | None`
- `net_control_callsign: str | None`

---

## Error Handling

- **Create season with `end_date` before `start_date`:** 400
- **Create season with `is_week_long=False` and no `day_of_week`:** 400
- **Delete non-existent season:** 404
- **Update/get non-existent session:** 404
- **Create ad-hoc session with `REAL_EVENT` type and non-null `season_id`:** 400 — real events are season-independent

---

## Behavior Notes

- **Time is always UTC** — consistent with ham radio convention. No timezone field needed.
- **Real event sessions** are always manually created via `POST /sessions` with `session_type=REAL_EVENT`, `season_id=None`, and `end_date=None`. They use the same downstream features (check-ins, roster, mapping) but skip the reminder flow since there's no lead time.
- **Closing a real event** — operator sets `end_date` and/or `status=COMPLETED` via `PATCH /sessions/{id}`.
- **Season deletion cascades** — all sessions and their downstream data (reminders, check-ins, rosters) are deleted. The operator should understand this before deleting.
- **No season updates** — recurrence changes require delete + recreate. Individual session overrides cover most needs.

---

## What This Phase Does NOT Include

- **Season update endpoint** — changing recurrence after generation is deferred. Delete and recreate.
- **Session reordering or bulk operations** — individual PATCH covers override needs.
- **Frontend** — React UI for schedule management deferred to frontend phase.
- **Background scheduling** — no in-process scheduler. Session generation happens at season creation time.
