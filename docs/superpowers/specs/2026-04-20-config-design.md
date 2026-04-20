# Module 7: Application Configuration — Design Spec

**Goal:** Provide a runtime key-value configuration store for admin-managed settings like mailbox paths, API keys, and default values. Separate from environment-based app settings.

**Architecture:** Simple `backend/config_mgmt/` package with a key-value model, service layer, and REST API. Used by other modules via `get_config_value()` service calls.

**Tech Stack:** FastAPI, SQLAlchemy 2.0+, Pydantic

---

## Data Model

### AppConfig

| Column | Type | Constraints |
|--------|------|-------------|
| `key` | String(255) | PK |
| `value` | Text | NOT NULL |
| `updated_at` | DateTime(tz) | NOT NULL, auto-updated |

Simple key-value store with timestamp tracking.

---

## Service Layer

- `get_config_value(db, key, default=None)` → `str | None` — retrieves value by key, returns default if not found
- `set_config_value(db, key, value)` → `None` — upsert: creates or updates, auto-commits
- `get_all_config(db)` → `dict[str, str]` — returns entire config as dictionary

---

## API Endpoints

All under `/api/config`.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | Authenticated | List all configuration as key-value dict |
| PUT | `/{key}` | Admin | Set config value (upsert) |

---

## Known Config Keys

| Key | Purpose | Used By |
|-----|---------|---------|
| `default_net_control` | Default NCS callsign for session generation | Schedule module |
| `pat_mailbox_path` | Filesystem path to PAT mailbox directory | Check-ins module |
| `net_address` | Winlink address (e.g., w0ne@winlink.org) | Check-ins module |
| `claude_api_key` | Anthropic API key for activity chat | Activities module |

---

## Error Handling

- Unauthenticated access: 401
- Non-admin attempting PUT: 403

---

## What This Phase Does NOT Include

- **Frontend settings UI** — deferred to frontend phase
- **Secret management** — API keys currently stored as plain text in DB. Future integration with sops-nix or agenix for encrypted secrets.
- **Validation** — no schema validation on config values; consumers handle their own defaults
