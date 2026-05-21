# Check-ins Page Design Spec

**Goal:** Build the Check-ins frontend page with session-scoped check-in management, and add callbook lookup integration (HamQTH + QRZ) for auto-filling operator details during manual entry/editing.

**Architecture:** A session-scoped React page that displays check-ins for a selected net session with actions for scanning, manual entry, editing, and approval. A new callbook integration sub-package provides cached lookups from HamQTH and QRZ XML APIs.

**Tech Stack:** React/TypeScript, FastAPI, SQLAlchemy 2.0, httpx (callbook XML APIs)

---

## Scope

This spec covers the Check-ins page frontend and the callbook lookup backend. The existing check-in backend routes (`scan`, `manual`, `update`, `approve`, `session/{id}`, `members`) are already implemented and unchanged. The callbook system is a new backend integration.

---

## Page Layout

### Session Selector

A dropdown at the top of the page listing all net sessions, most recent first. Each option shows: date, session type, and status. Format: `"Wed, May 21, 2026 - regular checkin (scheduled)"`.

Default selection: the most recent session with status `scheduled`. If none, the most recent `completed` session. If no sessions exist, show "No sessions found."

Changing the selection reloads the check-ins table for that session.

### Action Bar

Visible only to users with role `net_control` or `admin`. Contains three buttons:

- **"Scan Mailbox"** — `POST /api/checkins/scan/{session_id}`. Shows a loading spinner during the request. On success, displays a toast: "Imported N check-ins". Refreshes the table.
- **"Add Check-in"** — Opens the Add Check-in modal.
- **"Approve Session"** — Shows a confirmation dialog: "Approve all check-ins and mark this session as completed? This updates member records." On confirm, calls `POST /api/checkins/approve/{session_id}`. On success, toast and refresh. Disabled (grayed out) when session status is `completed` or `cancelled`.

Viewers see the table read-only with no action bar.

### Stats Bar

A horizontal bar below the action bar showing summary counts derived from the loaded check-ins (computed client-side, no extra API call):

- Total check-ins
- Needs review (count where `parse_status === "manual_review"`)
- New members (count where `is_new_member === true`)
- On time / Early / Late (counts by `timing_status`)

### Check-ins Table

| Column | Source | Notes |
|--------|--------|-------|
| Callsign | `callsign` | Monospace font, bold |
| Name | `name` | |
| Location | `city`, `state` | Formatted as "City, ST" |
| Mode | `mode` | |
| Parse Status | `parse_status` | Badge: green "auto", yellow "manual review", cyan "manual entry" |
| Timing | `timing_status` | Badge: green "on time", cyan "early", yellow "late" |
| New | `is_new_member` | Star icon if true, empty otherwise |
| Comments | `comments` | Truncated with ellipsis, max ~180px |
| Actions | — | Edit button (pencil icon), net_control+ only |

Rows where `parse_status === "manual_review"` get a subtle yellow background highlight to draw attention.

Sorted by callsign alphabetically (matches backend default).

### Edit Check-in Modal

Opened by clicking the edit button on a row. Pre-populated with the check-in's current values. Fields:

- Callsign (text, mono, with Lookup button)
- Name (text)
- Mode (text)
- City (text)
- County (text)
- State (text)
- Comments (text)
- Parse Status (dropdown: auto, manual_review, manually_entered)

The **Lookup** button next to the callsign field calls `GET /api/checkins/lookup/{callsign}`. On success, auto-fills name, city, county, state fields — it overwrites all lookup-able fields regardless of current values, since the user reviews and confirms via the filled form before saving. On failure (not found), shows inline text "Not found in callbook". On error (not configured), shows "Callbook not configured".

Save calls `PATCH /api/checkins/{id}` with changed fields. Refreshes the table on success.

### Add Check-in Modal

Fields:

- Callsign (text, mono, with Lookup button — same behavior as edit)
- Name (text)
- Mode (select: Voice, Winlink, CW, Digital)
- City (text)
- County (text)
- State (text)
- Comments (text)

Save calls `POST /api/checkins/manual` with `session_id` from the current selection. Refreshes the table on success.

---

## Callbook Lookup Integration

### Provider Protocol

Two callbook providers following the same pattern as delivery backends:

```
CallbookProvider (protocol)
  lookup(callsign: str, session_token: str) -> CallbookResult | None
  authenticate(username: str, password: str) -> str  # returns session token

Implementations:
  HamQTHProvider
  QRZProvider
```

`CallbookResult` is a dataclass:

| Field | Type |
|-------|------|
| callsign | str |
| name | str \| None |
| city | str \| None |
| county | str \| None |
| state | str \| None |
| country | str \| None |
| latitude | float \| None |
| longitude | float \| None |
| source | str |

### HamQTH Provider

- Auth: `GET https://www.hamqth.com/xml.php?u={username}&p={password}` — returns XML with `<session_id>`
- Lookup: `GET https://www.hamqth.com/xml.php?id={session_id}&callsign={callsign}&prg=SkyNetControl`
- Response fields mapped: `<nick>` or `<adr_name>` -> name, `<adr_city>` -> city, `<us_county>` -> county, `<us_state>` -> state, `<adr_country>` -> country, `<latitude>` -> latitude, `<longitude>` -> longitude
- Session tokens expire after inactivity; re-authenticate on auth failure and retry once

### QRZ Provider

- Auth: `GET https://xmldata.qrz.com/xml/current/?username={username}&password={password}`  — returns XML with `<Key>`
- Lookup: `GET https://xmldata.qrz.com/xml/current/?s={key}&callsign={callsign}`
- Response fields mapped: `<fname>` + `<name>` -> name, `<addr2>` -> city, `<county>` -> county, `<state>` -> state, `<country>` -> country, `<lat>` -> latitude, `<lon>` -> longitude
- Session keys expire; re-authenticate on auth failure and retry once

### Configuration

Stored in the `AppConfig` key-value table:

| Key | Type | Description |
|-----|------|-------------|
| `callbook.providers` | JSON string | Ordered list of enabled providers, e.g. `["hamqth", "qrz"]` |
| `callbook.hamqth.username` | string | HamQTH username |
| `callbook.hamqth.password` | string | HamQTH password |
| `callbook.qrz.username` | string | QRZ username |
| `callbook.qrz.password` | string | QRZ password |

### Cache

**CallbookCache** table in `backend/integrations/callbook/models.py`:

| Column | Type | Notes |
|--------|------|-------|
| `callsign` | String(20), PK | Uppercased callsign |
| `name` | String(255), nullable | |
| `city` | String(255), nullable | |
| `county` | String(255), nullable | |
| `state` | String(100), nullable | |
| `country` | String(255), nullable | |
| `latitude` | Float, nullable | |
| `longitude` | Float, nullable | |
| `source` | String(20), NOT NULL | `"hamqth"` or `"qrz"` |
| `fetched_at` | DateTime(tz), NOT NULL | When the lookup was performed |

Cache TTL: 30 days. On lookup, check cache first. If cached entry exists and `fetched_at` is within 30 days, return it. Otherwise, query external APIs, update cache, return result.

### Lookup Service Flow

1. Check cache — if fresh, return cached result
2. Read `callbook.providers` config for ordered provider list
3. For each provider in order:
   a. Read credentials from config
   b. Authenticate (or use cached session token — stored in-memory, not DB)
   c. Call lookup
   d. On auth failure, re-authenticate once and retry
   e. On success, cache result and return it
   f. On failure (not found or error), try next provider
4. If all providers fail, return `None`

Session tokens are stored in a module-level dict (in-memory only) keyed by provider name. They're refreshed on auth failure.

### API Endpoint

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/checkins/lookup/{callsign}` | net_control, admin | Lookup callsign in configured callbooks |

**Response (200):**
```json
{
  "callsign": "W0ABC",
  "name": "John Smith",
  "city": "Denver",
  "county": "Denver",
  "state": "CO",
  "country": "United States",
  "latitude": 39.7392,
  "longitude": -104.9903,
  "source": "hamqth",
  "cached": true
}
```

**Response (404):** Callsign not found in any configured callbook.

**Response (503):** No callbook providers configured.

---

## File Structure

```
backend/integrations/callbook/
├── __init__.py
├── models.py          # CallbookCache model
├── providers.py       # HamQTHProvider, QRZProvider, CallbookResult
└── service.py         # lookup_callsign() orchestration + caching

frontend/src/
├── pages/CheckInsPage.tsx    # Full check-ins page
├── api/checkins.ts           # API client functions
```

### Modifications to Existing Files

| File | Change |
|------|--------|
| `backend/modules/checkins/routes.py` | Add `GET /lookup/{callsign}` endpoint |
| `frontend/src/App.tsx` | Replace CheckIns placeholder with `<CheckInsPage />` |
| `frontend/src/types/index.ts` | Add `CheckIn`, `CallbookResult` interfaces |
| `alembic/env.py` | Import `backend.integrations.callbook.models` |

---

## Testing Strategy

- **Callbook provider tests**: Mock HTTP responses for both HamQTH and QRZ XML APIs. Test auth, lookup, auth-retry-on-expiry, not-found responses.
- **Callbook service tests**: Test cache hit, cache miss with provider fallback, cache expiry, no providers configured.
- **Lookup route tests**: Auth checks (viewer denied, net_control allowed), 200/404/503 responses.
- **CheckInsPage component**: Not covered in this spec — frontend is tested manually via the mockup and browser.
