# Module 2: Activities — Design Spec

**Date:** 2026-04-19
**Status:** Implemented

## Goal

Manage a library of net activities (check-ins, exercises, drills) that can be assigned to net sessions. Includes an AI-assisted authoring workflow via Claude to help operators draft activity instructions through a chat interface.

## Architecture

Activities live in a standalone module under `backend/activities/`. The module exposes a FastAPI router mounted at `/api/activities` and is split into a data model layer, a service layer, and route handlers. Chat functionality is a sub-feature of this module, not a separate module.

## Tech Stack

- **Backend:** FastAPI, SQLAlchemy (sync), Alembic migrations
- **AI:** Anthropic Python SDK — `claude-sonnet-4-20250514`, max_tokens 1024
- **Database:** PostgreSQL (via existing app engine)

---

## Data Model

### Enums

**ChatMessageRole:** `USER`, `ASSISTANT`

### Activity

| Column | Type | Constraints |
|---|---|---|
| id | Integer | PK, auto-increment |
| title | String(255) | NOT NULL |
| description | Text | NOT NULL |
| instructions | Text | NOT NULL |
| is_default | Boolean | NOT NULL, default False |
| created_at | DateTime(tz) | NOT NULL, default utcnow |
| last_used_at | DateTime(tz) | nullable |

Relationships: `tags` (many-to-many via `activity_tag_assignments`), `usages` (one-to-many `ActivityUsage`), `chat_sessions` (one-to-many `ChatSession`)

### ActivityTag

| Column | Type | Constraints |
|---|---|---|
| id | Integer | PK, auto-increment |
| name | String(100) | NOT NULL, UNIQUE |

### ActivityTagAssignment (join table)

Composite PK: `(activity_id FK → activities.id, tag_id FK → activity_tags.id)`

### ActivityUsage

| Column | Type | Constraints |
|---|---|---|
| id | Integer | PK, auto-increment |
| activity_id | Integer | FK → activities.id, NOT NULL |
| session_id | Integer | FK → net_sessions.id, NOT NULL |
| used_at | DateTime(tz) | NOT NULL, default utcnow |

### ChatSession

| Column | Type | Constraints |
|---|---|---|
| id | Integer | PK, auto-increment |
| activity_id | Integer | FK → activities.id, nullable |
| created_at | DateTime(tz) | NOT NULL, default utcnow |

Relationships: `messages` (one-to-many `ChatMessage`, cascade delete, ordered by `created_at`)

### ChatMessage

| Column | Type | Constraints |
|---|---|---|
| id | Integer | PK, auto-increment |
| chat_session_id | Integer | FK → chat_sessions.id, NOT NULL |
| role | Enum(ChatMessageRole) | NOT NULL |
| content | Text | NOT NULL |
| created_at | DateTime(tz) | NOT NULL, default utcnow |

---

## Service Layer

### Activity Service

- `get_or_create_tags(db, tag_names)` → `list[ActivityTag]` — reuses existing tags by name, creates missing ones
- `create_activity(db, title, description, instructions, tag_names=None, is_default=False)` → `Activity`
- `get_activity(db, activity_id)` → `Activity | None`
- `list_activities(db)` → `list[Activity]` — ordered by title
- `update_activity(db, activity_id, title=None, description=None, instructions=None, tag_names=None)` → `Activity | None` — replaces all tags when `tag_names` is provided
- `delete_activity(db, activity_id)` → `bool` — blocked if `is_default` is True

### Chat Service

System prompt: `"You are a helpful assistant for a ham radio Winlink net manager..."`
Model: `claude-sonnet-4-20250514`, max_tokens: `1024`

- `create_chat_session(db)` → `ChatSession`
- `get_chat_session(db, chat_session_id)` → `ChatSession | None`
- `get_chat_history(db, chat_session_id)` → `list[ChatMessage]` — ordered by `created_at`
- `send_message(db, chat_session_id, user_content, api_key)` → `tuple[ChatMessage, ChatMessage]` — saves user message, sends full history to Claude, saves and returns assistant message
- `link_chat_to_activity(db, chat_session_id, activity_id)` → `None`

---

## API Endpoints

All routes mounted at `/api/activities`.

### Activity CRUD

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/` | Admin / NetControl | Create activity |
| GET | `/` | Authenticated | List all activities |
| GET | `/tags` | Authenticated | List all tags |
| GET | `/{id}` | Authenticated | Get activity by ID |
| PATCH | `/{id}` | Admin / NetControl | Update activity |
| DELETE | `/{id}` | Admin | Delete activity (blocked if default) |

### Chat

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/chat/sessions` | Admin / NetControl | Create a new chat session |
| GET | `/chat/sessions/{id}` | Authenticated | Get chat session with full message history |
| POST | `/chat/sessions/{id}/messages` | Admin / NetControl | Send a message; receives Claude reply |
| POST | `/chat/sessions/{id}/approve` | Admin / NetControl | Promote chat output into a saved activity |

---

## Error Handling

| Condition | HTTP Status |
|---|---|
| Activity not found | 404 |
| Chat session not found | 404 |
| Delete attempted on default activity | 403 |
| Claude API key missing | 503 |
| Claude API error | 502 |

---

## Seed Data

One default activity is seeded via the Alembic migration:

- **Title:** Standard Winlink Check-in
- **is_default:** True
- **Instructions:** One-line check-in format and guidance on using Winlink forms for structured check-ins.

---

## Deferred Items

- **Frontend** — React UI for browsing, editing, and chatting is deferred to the frontend phase.
- **Activity scheduling integration** — Automatic assignment of activities to net sessions based on tags or usage history is not yet implemented.
