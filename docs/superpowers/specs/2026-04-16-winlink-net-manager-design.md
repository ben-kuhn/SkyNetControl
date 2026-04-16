# SkyNetControl: Winlink Net Management Application

## Overview

SkyNetControl is a web application for managing a weekly Winlink net. It handles net scheduling, activity planning, participant reminders, check-in tracking, roster generation, and long-term participation records. The application integrates with PAT (Winlink client), groups.io (for posting reminders and rosters), and the Claude API (for brainstorming activities).

## Architecture

**Monolith** — single FastAPI application serving both the REST API and the React/TypeScript frontend (built as static assets). All modules share one SQLite database.

```
SkyNetControl/
├── backend/
│   ├── modules/
│   │   ├── schedule/
│   │   ├── activities/
│   │   ├── reminders/
│   │   ├── checkins/
│   │   └── roster/
│   ├── integrations/
│   │   ├── pat/          # Mailbox file reader
│   │   ├── groupsio/     # groups.io API client
│   │   └── claude/       # Claude API chat integration
│   ├── auth/             # OIDC integration
│   └── db/               # SQLAlchemy models, Alembic migrations
├── frontend/             # React/TypeScript SPA
├── default.nix           # Nix package derivation
├── module.nix            # NixOS module
├── oci.nix               # OCI image build
└── shell.nix             # Development environment
```

**Tech stack:**
- Backend: Python, FastAPI, SQLAlchemy, Alembic
- Frontend: React, TypeScript
- Database: SQLite
- Packaging: Nix overlay, NixOS module, OCI image via `dockerTools.buildLayeredImage`

## Module 1: Net Schedule

The schedule is the foundational module. Reminders, activities, check-ins, and rosters all reference net sessions.

### Configuration

- Season definitions with start/end dates (e.g., "Fall/Winter/Spring: Labor Day to Memorial Day", "Summer: Memorial Day to Labor Day")
- Per-season recurrence pattern: day of week + time for regular nets, or "week-long" for summer
- Every-other-week flag for activity weeks vs. regular check-in weeks
- Manual overrides: skip, cancel, or add individual sessions (e.g., holidays, special events)

### Data Model

**NetSeason:**
- `id` — primary key
- `name` — e.g., "Fall/Winter 2026"
- `start_date` — season start
- `end_date` — season end
- `day_of_week` — recurring day (e.g., Thursday), nullable for summer
- `time` — net start time
- `is_week_long` — boolean, true for summer sessions
- `activity_cadence` — every N weeks (e.g., 2 for every other week)

**NetSession:**
- `id` — primary key
- `season_id` — foreign key to NetSeason
- `date` — session date
- `session_type` — enum: `regular_checkin`, `activity`
- `status` — enum: `scheduled`, `completed`, `cancelled`
- `activity_id` — foreign key to Activity, nullable
- `net_control_callsign` — callsign of net control for this session, defaults to a global default but configurable per session

### Behavior

- When a season is configured, the app auto-generates all `NetSession` records based on the recurrence pattern, alternating between `regular_checkin` and `activity` types based on the activity cadence.
- Individual sessions can be overridden: cancelled, type changed, activity assigned, net control reassigned.
- Dashboard view shows upcoming sessions with their type, assigned activity, and net control.

## Module 2: Activities

### Activity Library

**Activity:**
- `id` — primary key
- `title` — e.g., "Simplex HF Net Exercise"
- `description` — brief summary
- `instructions` — detailed instructions (markdown), sent to participants
- `is_default` — boolean, true for the default check-in activity (cannot be deleted)
- `created_at` — timestamp
- `last_used_at` — timestamp, updated when assigned to a session

**ActivityTag:**
- `id` — primary key
- `name` — e.g., "HF", "VHF", "beginner-friendly", "winter", "emergency-prep"

**ActivityTagAssignment:** (many-to-many join table)
- `activity_id` — foreign key
- `tag_id` — foreign key

**ActivityUsage:**
- `id` — primary key
- `activity_id` — foreign key
- `session_id` — foreign key to NetSession
- `used_at` — date

### Default Activity

The app ships with a seed/default activity: "Standard Winlink Check-in" — instructions to send a one-line check-in or use the Winlink net check-in form. This activity is auto-assigned to `regular_checkin` sessions and cannot be deleted.

### Claude Chat Integration

- In-app chat interface within the activity module
- User starts a conversation describing the kind of activity they want
- Claude suggests activities; user refines back and forth
- On approval, the app creates an Activity record (title, description, instructions) from the conversation — user can edit before saving
- Chat history is stored per-activity for future reference

**ChatSession:**
- `id` — primary key
- `activity_id` — foreign key, nullable (linked after approval)
- `created_at` — timestamp

**ChatMessage:**
- `id` — primary key
- `chat_session_id` — foreign key
- `role` — enum: `user`, `assistant`
- `content` — message text
- `created_at` — timestamp

### Workflow

1. Browse existing activities or start a new brainstorm via Claude chat
2. Chat with Claude to develop a new activity
3. Approve and save — activity appears in the library with optional tags
4. Assign an activity to an activity-week session on the schedule
5. Instructions are included in the reminder for that week

## Module 3: Reminders

### Configuration

- Configurable lead time (e.g., 2 days before the net session)
- Reminder templates with placeholders: `{date}`, `{time}`, `{activity_title}`, `{activity_instructions}`, `{next_week_preview}`
- Separate templates for regular check-in weeks vs. activity weeks

### Data Model

**ReminderTemplate:**
- `id` — primary key
- `name` — e.g., "Activity Week Reminder"
- `template_type` — enum: `regular_checkin`, `activity`
- `subject_template` — email subject with placeholders
- `body_template` — email body with placeholders (markdown)
- `lead_time_days` — how many days before the session to generate the draft

**ReminderLog:**
- `id` — primary key
- `session_id` — foreign key to NetSession
- `status` — enum: `draft`, `approved`, `sent`, `skipped`
- `content_subject` — rendered subject
- `content_body` — rendered body
- `drafted_at` — timestamp
- `approved_at` — timestamp, nullable
- `sent_at` — timestamp, nullable
- `approved_by` — callsign of approver

### Workflow

1. Background task runs daily, checks if any reminders are due based on lead time
2. App renders the template with session data and saves a draft
3. Net control for that session is notified via in-app notification (and optionally email, configurable) to review the draft
4. Net control reviews in the app — edits if needed
5. Net control approves — reminder posts to groups.io via API
6. If not approved before the net, it stays in draft (never auto-posts)
7. Reminders are idempotent — only one draft per session

Status lifecycle: `draft` → `approved` → `sent` (or `skipped`).

## Module 4: Check-in Tracking

### PAT Mailbox Integration

- Configurable mailbox path (PAT stores messages as files on disk)
- Configurable net address (e.g., w0ne@winlink.org)
- On-demand scan (button in UI) or scheduled scan during/after net sessions
- Reads messages addressed to the net address, filtered by date window around the net session

### Message Parsing Pipeline

1. Read message from mailbox files
2. Detect type: Winlink check-in form vs. plain text vs. unknown
3. For forms: extract fields from structured form data
4. For plain text: parse the standard check-in string in expected order — name, callsign, city, county, state, mode, comments
5. If parsing fails or confidence is low: flag for manual review
6. Deduplicate by callsign per session (participant may send multiple messages; keep the latest)

### Data Model

**RawMessage:**
- `id` — primary key
- `message_id` — Winlink message ID (unique)
- `from_address` — sender
- `received_at` — timestamp
- `subject` — message subject
- `body` — full message body
- `message_type` — enum: `form`, `plain_text`, `unknown`
- `parsed` — boolean

**CheckIn:**
- `id` — primary key
- `session_id` — foreign key to NetSession
- `raw_message_id` — foreign key to RawMessage, nullable (for manually entered check-ins)
- `callsign` — participant callsign
- `name` — participant name
- `city` — nullable
- `county` — nullable
- `state` — nullable
- `mode` — check-in method/mode
- `comments` — nullable
- `latitude` — nullable, from GPS-equipped forms
- `longitude` — nullable, from GPS-equipped forms
- `parse_status` — enum: `auto`, `manual_review`, `manually_entered`
- `is_new_member` — boolean, determined by lookup in long-term roster

### Manual Review UI

- Flagged messages shown side-by-side: raw message on one side, parsed fields on the other
- Net control fills in or corrects fields and approves
- Option to manually add a check-in (e.g., someone checks in via voice relay)

### Check-in Review Workflow

1. Mailbox is scanned and check-ins are parsed
2. Net control for that session is notified via in-app notification (and optionally email, configurable) to review
3. Net control reviews in the app — corrects flagged or mis-parsed entries, adds manual check-ins
4. Net control approves the full set of check-ins for the session

### Check-in Mapping

- Check-ins that include GPS coordinates (`latitude`/`longitude` are non-null) are plotted on a map
- Check-ins without coordinates are not mapped (no geocoding)
- Per-session map view using Leaflet.js with OpenStreetMap tiles
- Pins show callsign, name, and location on hover/click
- New members shown with a distinct pin color/icon
- Shareable public link (no authentication required) for each session's map

## Module 5: Roster

### Generation

- Generated from approved check-ins for a given net session
- Format: detailed table with columns — callsign, name, city, county, state, mode, comments
- New members flagged (first time checking in to this net)
- Header includes: session date, net control callsign, activity name/description
- Footer includes: what's planned for next week (regular check-in or specific activity title + brief description)
- If any check-ins had GPS data, includes the shareable map link

### Workflow

1. Check-ins are reviewed and approved by net control
2. Net control selects the activity for next week (if it's an activity week), so the preview can be included
3. Roster is auto-generated from approved check-ins
4. Net control previews the roster — can edit the final output
5. Net control approves — roster posts to groups.io via API

**RosterLog:**
- `id` — primary key
- `session_id` — foreign key to NetSession
- `status` — enum: `draft`, `approved`, `sent`
- `content` — rendered roster text
- `map_url` — shareable map link, nullable
- `drafted_at` — timestamp
- `approved_at` — timestamp, nullable
- `sent_at` — timestamp, nullable
- `approved_by` — callsign

### Long-term Roster

**Member:**
- `id` — primary key
- `callsign` — unique
- `name` — participant name
- `first_check_in_date` — date of first check-in
- `last_check_in_date` — date of most recent check-in
- `total_check_ins` — running count

- Updated automatically when check-ins are approved for a session
- `is_new_member` on CheckIn is determined by whether the callsign exists in this table at the time of parsing
- Browsable in the UI with search and filter

## Module 6: Authentication & Authorization

### OIDC Integration

- Standard OpenID Connect authorization code flow
- The app is an OIDC relying party; the user manages the identity provider
- Configurable OIDC settings: issuer URL, client ID, client secret, scopes

### Roles

- `admin` — full access: manage schedule, configuration, all modules
- `net_control` — review and approve check-ins and rosters for assigned sessions, manage activities
- `viewer` — read-only: view schedules, rosters, maps, participation history

### Role Assignment

- First authenticated user is automatically granted `admin` role
- Admins assign roles to other users via the UI

## Module 7: Application Configuration

Admin-only settings managed in the UI:

- **Net address** — Winlink address for the net (e.g., w0ne@winlink.org)
- **PAT mailbox path** — filesystem path to PAT's mailbox directory
- **Groups.io API credentials** — API key/token for posting
- **Claude API key** — for the activity brainstorming chat
- **OIDC provider settings** — issuer, client ID, client secret
- **Default net control callsign** — used when not overridden per session
- **Reminder lead time** — default days before session
- **Reminder templates** — configurable per type

Secrets (API keys, OIDC client secret) are stored encrypted or managed via environment variables / NixOS secret management (sops-nix or agenix).

## Module 8: Deployment & Packaging

### Nix Package

- `default.nix` — builds the full application (Python backend + React frontend static assets)
- Overlay-friendly: can be imported via `callPackage` or added as a Nix overlay
- Structured for potential nixpkgs submission

### NixOS Module (`module.nix`)

- Declarative configuration for all settings (net address, mailbox path, OIDC, etc.)
- Runs as a systemd service
- SQLite database in a configurable state directory
- Secrets via sops-nix or agenix

### OCI Image (`oci.nix`)

- Built via `dockerTools.buildLayeredImage` — no Dockerfile
- Same Nix derivation as the NixOS package
- Configuration via environment variables
- SQLite database on a mounted volume

### Development Environment

- `shell.nix` — provides Python, Node.js, and all development dependencies

## Data Flow: Typical Net Week

```
1. Schedule has upcoming session defined
       │
2. Reminder becomes due (lead time before session)
       │
3. App drafts reminder from template + session data
       │
4. Net control notified → reviews/edits → approves → posts to groups.io
       │
5. Net session happens — participants send Winlink messages
       │
6. App scans PAT mailbox → parses check-ins
       │
7. Net control notified → reviews check-ins → corrects flagged entries → approves
       │
8. Net control selects next week's activity (if applicable)
       │
9. App generates roster + map link → net control previews → approves → posts to groups.io
       │
10. Long-term roster updated, new members flagged
```

Activity weeks: the assigned activity's instructions are included in the reminder at step 3.
Summer weeks: same flow, but the schedule pattern is week-long rather than single-day.
