# Module 3: Reminders — Design Spec

**Goal:** Build a reminder workflow where templates are rendered into drafts for upcoming net sessions, reviewed/edited by net control, and approved for posting. Groups.io integration and background scheduling are deferred to a future cross-cutting phase; this module provides manual trigger endpoints and a "mark as sent" placeholder.

**Architecture:** Follows the existing `models.py / service.py / routes.py` module pattern. Templates are rendered with Jinja2. Draft generation is idempotent (one reminder per session). An external cron or manual button triggers draft generation via API.

**Tech Stack:** FastAPI, SQLAlchemy 2.0+, Jinja2, Alembic

---

## Data Model

### Enums

**TemplateType:**
- `regular_checkin`
- `activity`

**ReminderStatus:**
- `draft`
- `approved`
- `sent`
- `skipped`

### ReminderTemplate

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | Integer | PK, auto-increment |
| `name` | String(255) | NOT NULL, unique |
| `template_type` | Enum(TemplateType) | NOT NULL |
| `subject_template` | Text | NOT NULL |
| `body_template` | Text | NOT NULL |
| `lead_time_days` | Integer | NOT NULL, default 2 |
| `is_default` | Boolean | NOT NULL, default False |

One default template per `template_type`. The default template is used when auto-generating drafts for sessions of that type.

### ReminderLog

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | Integer | PK, auto-increment |
| `session_id` | Integer | FK to `net_sessions.id`, NOT NULL, unique |
| `template_id` | Integer | FK to `reminder_templates.id`, nullable |
| `status` | Enum(ReminderStatus) | NOT NULL, default `draft` |
| `content_subject` | Text | NOT NULL |
| `content_body` | Text | NOT NULL |
| `drafted_at` | DateTime(timezone=True) | NOT NULL |
| `approved_at` | DateTime(timezone=True) | nullable |
| `sent_at` | DateTime(timezone=True) | nullable |
| `approved_by` | String(20) | nullable |

Relationships:
- `session` → NetSession (many-to-one)
- `template` → ReminderTemplate (many-to-one, nullable)

The unique constraint on `session_id` enforces one reminder per session (idempotency).

---

## Template Rendering

Templates use Jinja2 syntax. The following context variables are available:

| Variable | Source | Description |
|----------|--------|-------------|
| `date` | `net_session.start_date` formatted | Session date (e.g., "April 10, 2026") |
| `time` | `season.time` formatted | Session time (e.g., "6:00 PM UTC"), empty string if None |
| `day_of_week` | Derived from `season.day_of_week` | Day name (e.g., "Thursday") |
| `activity_title` | `activity.title` if activity week | Activity title, empty string if regular check-in |
| `activity_instructions` | `activity.instructions` if activity week | Activity instructions (markdown), empty string if regular |
| `net_control` | `net_session.net_control_callsign` | Net control callsign, empty string if None |
| `next_week_preview` | Derived from next session | Title + description of next week's session/activity, empty string if none |

`next_week_preview` is built by looking up the next NetSession after the current one (by `start_date`) within the same season. If that session is an activity week, the preview includes the activity title and a brief description. If it's a regular check-in, it says "Standard Winlink Check-in".

Example template (activity week):
```
Subject: W0NE Net Reminder — {{ date }}
Body:
This {{ day_of_week }}'s net activity: **{{ activity_title }}**

{{ activity_instructions }}

Net control: {{ net_control }}

{% if next_week_preview %}Next week: {{ next_week_preview }}{% endif %}
```

Rendering errors (e.g., undefined variables, bad syntax) are caught and the draft is saved with an error message in `content_body` so net control can see what went wrong and edit manually.

---

## Service Layer

### Template CRUD

- `create_template(db, name, template_type, subject_template, body_template, lead_time_days, is_default)` — Creates a new template. If `is_default=True`, clears `is_default` on any existing default of the same `template_type`.
- `get_template(db, template_id)` → `ReminderTemplate | None`
- `list_templates(db)` → `list[ReminderTemplate]`
- `update_template(db, template_id, **fields)` → `ReminderTemplate | None` — Same `is_default` logic as create.
- `delete_template(db, template_id)` — Prevents deletion of default templates.

### Template Rendering

- `build_template_context(db, net_session)` → `dict` — Builds the Jinja2 context dict from the session, its season, linked activity, and next session.
- `render_reminder(template, context)` → `tuple[str, str]` — Renders `subject_template` and `body_template` with the context. Returns `(rendered_subject, rendered_body)`. Catches Jinja2 errors and returns error messages.

### Draft Generation

- `generate_draft(db, session_id, template_id=None)` → `ReminderLog` — Idempotent. If a ReminderLog already exists for this session, returns it unchanged. Otherwise: picks the provided template or the default template matching the session's type, renders it, creates a ReminderLog with status `draft`.
- `generate_due_drafts(db)` → `list[ReminderLog]` — Finds all NetSessions where `status=SCHEDULED` and `start_date - today <= lead_time_days` (from the default template for the session type) and no ReminderLog exists. Generates a draft for each.

### Status Transitions

- `approve_reminder(db, reminder_id, approver_callsign)` → `ReminderLog | None` — Sets status to `approved`, records `approved_at` and `approved_by`. Only valid from `draft` status.
- `mark_sent(db, reminder_id)` → `ReminderLog | None` — Sets status to `sent`, records `sent_at`. Only valid from `approved` status. Placeholder for future groups.io integration.
- `skip_reminder(db, reminder_id)` → `ReminderLog | None` — Sets status to `skipped`. Valid from `draft` or `approved`.
- `update_draft(db, reminder_id, content_subject=None, content_body=None)` → `ReminderLog | None` — Edit draft content. Only valid while status is `draft`.

Status lifecycle: `draft` → `approved` → `sent`, or `draft`/`approved` → `skipped`.

---

## API Endpoints

All under `/api/reminders`. Auth follows existing patterns.

### Template Management

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/templates` | Authenticated | List all templates |
| POST | `/templates` | Admin/NetControl | Create template |
| PATCH | `/templates/{id}` | Admin/NetControl | Update template |
| DELETE | `/templates/{id}` | Admin | Delete template (not default) |

### Draft Generation

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/generate` | Admin/NetControl | Generate all due drafts |
| POST | `/generate/{session_id}` | Admin/NetControl | Generate draft for specific session |

### Reminder Management

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | Authenticated | List reminders (optional `?status=` filter) |
| GET | `/session/{session_id}` | Authenticated | Get reminder for a session |
| PATCH | `/{id}` | Admin/NetControl | Edit draft content (draft status only) |
| POST | `/{id}/approve` | Admin/NetControl | Approve reminder |
| POST | `/{id}/send` | Admin/NetControl | Mark as sent (placeholder) |
| POST | `/{id}/skip` | Admin/NetControl | Skip reminder |

### Pydantic Schemas

**TemplateCreate:**
- `name: str`
- `template_type: TemplateType`
- `subject_template: str`
- `body_template: str`
- `lead_time_days: int = 2`
- `is_default: bool = False`

**TemplateUpdate:**
- All fields optional

**DraftUpdate:**
- `content_subject: str | None = None`
- `content_body: str | None = None`

---

## Error Handling

- Template rendering errors are caught and stored in the draft body rather than failing the request. This lets net control see and fix the issue manually.
- Status transition violations return 409 Conflict (e.g., approving an already-sent reminder).
- Missing default template for a session type during `generate_due_drafts` skips that session and logs a warning (does not fail the batch).
- Deleting a default template returns 400 Bad Request.

---

## Seed Data

The Alembic migration seeds two default templates:

**Regular Check-in Reminder (default):**
```
Subject: W0NE Winlink Net Reminder — {{ date }}
Body:
Reminder: the W0NE Winlink Net check-in is this {{ day_of_week }}, {{ date }}.

Please send your check-in to w0ne@winlink.org with your name, callsign, city, county, state, and mode.

Net control: {{ net_control }}
{% if next_week_preview %}
Next week: {{ next_week_preview }}
{% endif %}
```

**Activity Week Reminder (default):**
```
Subject: W0NE Winlink Net — {{ activity_title }} — {{ date }}
Body:
This {{ day_of_week }}'s W0NE Winlink Net features a special activity: **{{ activity_title }}**

{{ activity_instructions }}

Please send your check-in to w0ne@winlink.org with your name, callsign, city, county, state, and mode.

Net control: {{ net_control }}
{% if next_week_preview %}
Next week: {{ next_week_preview }}
{% endif %}
```

---

## What This Phase Does NOT Include

- **Background scheduler** — No in-process scheduler (APScheduler/Celery). Draft generation is triggered manually or by external cron hitting `POST /api/reminders/generate`.
- **Groups.io integration** — The "send" action marks the reminder as sent but does not post anywhere. Groups.io client will be built in a future cross-cutting phase shared with Module 5 (Roster).
- **In-app notifications** — Net control is not notified when a draft is generated. They check the reminders list in the UI. Notifications can be added later.
