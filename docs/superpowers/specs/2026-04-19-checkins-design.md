# Module 4: Check-in Tracking — Design Spec

**Goal:** Ingest Winlink messages sent to the net address, parse them into structured check-in records, support manual entry and corrections, and finalize sessions by updating the member registry.

**Architecture:** Follows the existing `models.py / service.py / routes.py` module pattern. A mailbox reader ingests raw `.mime` / `.b2f` / `.eml` files from the PAT mailbox directory. A parsing pipeline classifies messages and extracts fields. A service layer handles deduplication, timing classification, and member tracking. The API exposes endpoints for scanning, review, manual entry, and session approval.

**Tech Stack:** FastAPI, SQLAlchemy 2.0+, Alembic, Python `email` (stdlib)

---

## Data Model

### Enums

**MessageType:**
- `FORM` — structured key:value form message
- `PLAIN_TEXT` — freeform text containing a recognizable callsign
- `UNKNOWN` — could not be classified

**ParseStatus:**
- `AUTO` — parsed with high or medium confidence
- `MANUAL_REVIEW` — low confidence; net control must review
- `MANUALLY_ENTERED` — created directly via the manual check-in endpoint

**TimingStatus:**
- `ON_TIME`
- `EARLY`
- `LATE`

### RawMessage

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | Integer | PK, auto-increment |
| `message_id` | String(255) | UNIQUE |
| `from_address` | String(255) | NOT NULL |
| `received_at` | DateTime(tz) | NOT NULL |
| `subject` | String(500) | NOT NULL |
| `body` | Text | NOT NULL |
| `message_type` | Enum(MessageType) | NOT NULL |
| `parsed` | Boolean | default False |

### CheckIn

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | Integer | PK, auto-increment |
| `session_id` | Integer | FK to `net_sessions.id`, NOT NULL |
| `raw_message_id` | Integer | FK to `raw_messages.id`, nullable |
| `callsign` | String(20) | NOT NULL |
| `name` | String(255) | NOT NULL |
| `city` | String(255) | nullable |
| `county` | String(255) | nullable |
| `state` | String(100) | nullable |
| `mode` | String(100) | NOT NULL |
| `comments` | Text | nullable |
| `latitude` | Float | nullable |
| `longitude` | Float | nullable |
| `parse_status` | Enum(ParseStatus) | NOT NULL |
| `timing_status` | Enum(TimingStatus) | NOT NULL |
| `is_new_member` | Boolean | default False |

### Member

| Column | Type | Constraints |
|--------|------|-------------|
| `callsign` | String(20) | PK |
| `name` | String(255) | NOT NULL |
| `first_check_in_date` | DateTime(tz) | NOT NULL |
| `last_check_in_date` | DateTime(tz) | NOT NULL |
| `total_check_ins` | Integer | default 0 |

---

## Message Parsing Pipeline

### `detect_message_type(body)` → `MessageType`

- If ≥3 lines contain `":"` with the left side matching known form field names: `FORM`
- Else if body contains a callsign regex match: `PLAIN_TEXT`
- Else: `UNKNOWN`

### `parse_form_message(body)` → `dict`

- Extracts `key: value` pairs and maps them to known fields: `name`, `callsign`, `city`, `county`, `state`, `mode`, `comments`, `latitude`, `longitude`
- Confidence: `"high"` if all required fields (`name`, `callsign`, `mode`) are present; otherwise `"low"`

### `parse_plain_text_message(body)` → `dict`

- Extracts callsign using regex `\b[A-Z]{1,2}\d[A-Z]{1,3}\b`
- Text before the callsign is treated as name; text after is parsed for location, mode, and comments
- Known modes: `winlink`, `vara`, `ardop`, `packet`, `pactor`, `telnet`, `ax.25`
- Confidence: `"medium"` if both callsign and name are found; otherwise `"low"`

### `parse_message(body)` → `tuple[MessageType, dict]`

- Calls `detect_message_type`, then dispatches to `parse_form_message` or `parse_plain_text_message` accordingly

---

## Mailbox Reader

### `read_message_file(file_path)` → `dict | None`

- Parses a MIME-format file using the Python stdlib `email` library
- Extracts: `message_id`, `from_address`, `to_address`, `subject`, `received_at`, `body`
- Returns `None` if `Message-Id` or `From` headers are missing

### `read_mailbox(mailbox_path, net_address)` → `list[dict]`

- Reads all `.mime`, `.b2f`, and `.eml` files from the given directory
- Filters to messages where `to_address` contains `net_address` (case-insensitive substring match)

---

## Service Layer

### `classify_timing(net_session, received_at)` → `TimingStatus`

- Open-ended sessions (`end_date=None`): any message received after `start_date` is `ON_TIME`
- Bounded sessions: evaluated against the session window plus `grace_period_hours`

### `is_new_member(db, callsign)` → `bool`

- Returns `True` if no Member record exists for the given callsign

### `process_raw_message(db, raw, net_session)` → `CheckIn`

- Parses message body, extracts fields, sets `parse_status` to `AUTO` if confidence is `"high"` or `"medium"`, otherwise `MANUAL_REVIEW`
- Classifies timing, checks new member status, creates and returns a `CheckIn` record

### `scan_and_import_messages(db, raw_messages, net_session)` → `list[CheckIn]`

- Deduplicates by `message_id` — skips any message already present in `raw_messages`
- Deduplicates by callsign within the batch — keeps the latest message per callsign
- Returns the final list of imported `CheckIn` records

### `get_checkins_for_session(db, session_id)` → `list[CheckIn]`

- Returns all check-ins for the session, ordered by callsign

### `create_manual_checkin(db, session_id, callsign, name, mode, city=None, county=None, state=None, comments=None)` → `CheckIn`

- Sets `parse_status = MANUALLY_ENTERED`, `timing_status = ON_TIME`
- No `raw_message_id` association

### `update_checkin(db, checkin_id, **fields)` → `CheckIn | None`

- Updates any provided fields on an existing check-in
- Returns `None` if the check-in does not exist

### `approve_session_checkins(db, session_id)` → `None`

- Creates or updates a `Member` record for each check-in in the session (updating `last_check_in_date` and incrementing `total_check_ins`)
- Marks the session status as `COMPLETED`

---

## API Endpoints

All under `/api/checkins`.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/scan/{session_id}` | Admin/NetControl | Read mailbox and import new messages for the session |
| GET | `/session/{session_id}` | Authenticated | List check-ins for a session |
| POST | `/manual` | Admin/NetControl | Create a manual check-in |
| PATCH | `/{id}` | Admin/NetControl | Update a check-in |
| POST | `/approve/{session_id}` | Admin/NetControl | Approve all check-ins and finalize the session |
| GET | `/members` | Authenticated | List all members |

---

## Error Handling

- Session not found: `404 Not Found`
- Check-in not found for update: `404 Not Found`
- PAT mailbox path or net address not configured: `503 Service Unavailable`

---

## Deferred Items

- **Frontend** — React UI for check-in review: side-by-side raw message body and parsed fields, with inline editing for `MANUAL_REVIEW` records
- **Automated PAT scan trigger** — scan is currently initiated manually via `POST /scan/{session_id}`; a background scheduler or webhook trigger is deferred
- **GPS coordinate geocoding** — `latitude` and `longitude` are stored when present in the message but not geocoded from address fields
- **Check-in mapping** — Leaflet.js map view of check-in locations is deferred to the frontend phase
