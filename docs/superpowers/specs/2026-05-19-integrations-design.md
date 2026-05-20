# Integrations Design Spec

**Goal:** Add a pluggable delivery system for posting reminders and rosters (via groups.io, email, or Winlink), and a scheduled mailbox scanner for auto-importing check-ins from PAT's local mailbox.

**Architecture:** Two new sub-packages under `backend/integrations/`: a delivery system with pluggable backends, and a mailbox scanner that orchestrates existing check-in parsing on a schedule. The delivery system hooks into existing `mark_sent()` flows. The scanner wraps existing `mailbox_reader` and `scan_and_import_messages()`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, httpx (groups.io API), smtplib (email), asyncio background tasks

---

## Delivery System

### Backend Protocol

A `DeliveryBackend` protocol with three implementations:

```
DeliveryBackend (protocol)
  send(subject: str, body: str, config: dict) -> DeliveryResult

Implementations:
  GroupsIoBackend  — POST draft then post draft via groups.io API
  EmailBackend     — send via SMTP (reuses existing async email pattern)
  WinlinkBackend   — write .b2f file to PAT's out/ directory for next sync
```

`DeliveryResult` is a dataclass with `success: bool` and `error: str | None`.

Each backend runs via `asyncio.to_thread()` following the existing `auth/email.py` pattern. Backends are independent — a groups.io failure does not block email delivery.

### Data Model

**DeliveryLog** — new table in `backend/integrations/delivery/models.py`:

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer, PK | Auto-increment |
| `content_type` | String(20), NOT NULL | `"reminder"` or `"roster"` |
| `content_id` | Integer, NOT NULL | References ReminderLog.id or RosterLog.id |
| `backend` | String(20), NOT NULL | `"groupsio"`, `"email"`, `"winlink"` |
| `status` | Enum, NOT NULL | `pending`, `sent`, `failed` |
| `error_message` | Text, nullable | Error details on failure |
| `sent_at` | DateTime(tz), nullable | When delivery succeeded |
| `created_at` | DateTime(tz), NOT NULL | When attempt was created |

Unique constraint on `(content_type, content_id, backend)` — one attempt per backend per piece of content.

**DeliveryStatus** enum: `PENDING`, `SENT`, `FAILED`.

No foreign key constraint on `content_id` since it references two different tables depending on `content_type`.

### Configuration

Stored in existing `AppConfig` key-value table:

| Key | Type | Description |
|-----|------|-------------|
| `delivery.backends` | JSON string | List of enabled backends, e.g. `["groupsio", "email"]` |
| `delivery.groupsio.api_key` | string | groups.io API key (secret) |
| `delivery.groupsio.group_name` | string | Target group name, e.g. `w0ne-net` |
| `delivery.email.to_address` | string | Recipient email address |
| `delivery.winlink.target_address` | string | Winlink address to send to |

SMTP settings use the existing `Settings` object (`smtp_host`, `smtp_port`, etc.). Winlink outbound uses the existing `pat_mailbox_path` config to locate the `out/` directory.

### Delivery Flow

When `mark_sent()` is called on a reminder or roster:

1. Look up enabled backends from `delivery.backends` config
2. For each enabled backend, create a `DeliveryLog` entry with status `PENDING`
3. Attempt delivery via each backend:
   - **groups.io**: `POST /api/v1/newdraft` with subject + body, then `POST /api/v1/postdraft` with the returned draft ID. Auth via `Authorization: Bearer {api_key}`. All requests over HTTPS.
   - **email**: Send via SMTP using existing `_send_email_sync` pattern from `auth/email.py`, offloaded to thread pool.
   - **winlink**: Construct a `.b2f` formatted message file and write it to `{pat_mailbox_path}/{callsign}/out/`. PAT sends it on next sync. The callsign is derived from `net_address` config.
4. Update each `DeliveryLog` to `SENT` or `FAILED` with error details
5. The reminder/roster status moves to `SENT` only if **at least one** backend succeeds. If all fail, it stays `APPROVED` so the user can retry.

A "Retry Failed" action re-attempts only the failed backends for that content, creating new delivery attempts.

### API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/delivery/{content_type}/{content_id}` | viewer+ | List delivery attempts for a reminder/roster |
| `POST` | `/api/delivery/{content_type}/{content_id}/retry` | admin, net_control | Retry failed deliveries |

### groups.io API Details

- Base URL: `https://groups.io/api/v1`
- Auth: `Authorization: Bearer {api_key}` header
- Create draft: `POST /newdraft` with `group_name`, `subject`, `body`
- Post draft: `POST /postdraft` with `draft_id`, `group_id`
- Rate limiting: 429 responses handled with exponential backoff
- All requests via HTTPS (required by groups.io)

### Winlink .b2f Format

The `.b2f` (Binary Forwarding Format) file written to PAT's `out/` directory contains:
- Headers: `Mid`, `From`, `To`, `Subject`, `Date`, `Mbo`, `Body` length
- Body: plain text content
- Message ID generated as a unique string

PAT picks up files from `out/` on the next connect/sync and delivers them.

---

## Mailbox Scanner

### Behavior

A background task that periodically scans PAT's local mailbox for new check-in messages:

- Runs on a configurable interval (default: 5 minutes)
- Only scans during active session windows (session start minus grace period through session end plus grace period)
- Reads `.b2f` files from `{pat_mailbox_path}/{callsign}/in/`
- The callsign is derived from `net_address` config (e.g., `w0ne@winlink.org` -> `W0NE`)
- Filters by net address, deduplicates, parses into check-ins using existing `mailbox_reader` and `scan_and_import_messages()` pipeline
- When no session is in its active window, the scanner skips silently

### Configuration

| Key | Type | Description |
|-----|------|-------------|
| `scanner.enabled` | bool string | Whether auto-scanning is active (default: `"false"`) |
| `scanner.interval_minutes` | int string | Scan interval in minutes (default: `"5"`) |

### API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/scanner/status` | admin, net_control | Current scanner state (running, last scan time, next scan time, active session if any) |
| `POST` | `/api/scanner/trigger` | admin, net_control | Trigger an immediate scan regardless of schedule |

### Background Task Lifecycle

- Started on app startup if `scanner.enabled` is `"true"`
- Runs as an `asyncio` background task registered via FastAPI's lifespan
- Gracefully stops on app shutdown
- If config changes, scanner picks up new interval on next cycle (no restart needed)

---

## File Structure

```
backend/integrations/
├── __init__.py
├── delivery/
│   ├── __init__.py
│   ├── models.py              # DeliveryLog model, DeliveryStatus enum
│   ├── service.py             # dispatch_delivery(), retry_failed(), get_delivery_status()
│   ├── backends/
│   │   ├── __init__.py        # BACKENDS registry, get_backend()
│   │   ├── base.py            # DeliveryBackend protocol, DeliveryResult dataclass
│   │   ├── groupsio.py        # GroupsIoBackend
│   │   ├── email.py           # EmailBackend
│   │   └── winlink.py         # WinlinkBackend
│   └── routes.py              # Delivery status and retry endpoints
├── scanner/
│   ├── __init__.py
│   ├── service.py             # Scanner loop, scheduling, active window detection
│   └── routes.py              # Scanner status and manual trigger endpoints
```

### Modifications to Existing Files

| File | Change |
|------|--------|
| `backend/modules/reminders/service.py` | `mark_sent()` calls `dispatch_delivery("reminder", log.id, log.content_subject, log.content_body)` |
| `backend/modules/roster/service.py` | `mark_sent()` calls `dispatch_delivery("roster", log.id, subject, assembled_text)` |
| `backend/app.py` | Register delivery + scanner routes, start scanner background task in lifespan |
| `alembic/env.py` | Import `backend.integrations.delivery.models` |
| `frontend/src/pages/ConfigPage.tsx` | Add delivery backend and scanner config fields |

---

## Testing Strategy

- **Backend unit tests**: Each delivery backend tested with mocked HTTP/SMTP/filesystem
- **Service tests**: `dispatch_delivery()` tested with mock backends, verifying DeliveryLog creation and status transitions
- **Scanner tests**: Active window detection, scan triggering, integration with existing checkin pipeline (mocked mailbox reader)
- **Route tests**: Delivery status retrieval, retry endpoint, scanner status/trigger
- **Integration tests**: Full flow — approve reminder -> dispatch -> verify delivery logs
