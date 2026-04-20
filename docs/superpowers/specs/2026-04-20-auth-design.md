# Module 6: Authentication & Authorization — Design Spec

**Goal:** Authenticate users via OIDC, issue JWT sessions, and enforce role-based access control across all API endpoints. Callsign is the user's primary identity.

**Architecture:** Separate `backend/auth/` package with models, service, routes, and dependency injection. Uses Authlib for OIDC, python-jose for JWT. Auth dependencies are injected into all module routes.

**Tech Stack:** FastAPI, SQLAlchemy 2.0+, Authlib, python-jose, Pydantic

---

## Data Model

### UserRole Enum

- `ADMIN` — full access: manage schedule, configuration, users, all modules
- `NET_CONTROL` — review/approve check-ins and rosters, manage activities, control sessions
- `VIEWER` — read-only: view schedules, rosters, maps, participation history

### User

| Column | Type | Constraints |
|--------|------|-------------|
| `callsign` | String(20) | PK |
| `oidc_subject` | String(255) | UNIQUE, NOT NULL |
| `name` | String(255) | NOT NULL |
| `role` | Enum(UserRole) | NOT NULL, default VIEWER |
| `created_at` | DateTime(tz) | NOT NULL, default utcnow |

Callsign is the primary key — globally unique in ham radio. All foreign keys referencing users (`net_control_callsign`, `approved_by`, etc.) use the callsign directly.

---

## OIDC Flow

1. **Login** (`GET /login`) — generates random 32-byte state token, stores in httponly cookie (10min TTL), redirects to OIDC provider authorization endpoint
2. **Callback** (`GET /callback`) — validates state via `secrets.compare_digest()` (CSRF protection), exchanges authorization code for token, fetches userinfo from OIDC provider
3. **User provisioning** — looks up user by `oidc_subject`. If new: first user auto-promoted to ADMIN, subsequent users default to VIEWER. Callsign derived from `preferred_username` (uppercase) or first 20 chars of OIDC subject
4. **JWT issuance** — creates HS256-signed JWT with `{sub: callsign, role: role, exp: expiry}`, set as httponly/secure/SameSite=lax cookie
5. **Redirect** — sends user to `app_base_url`

---

## JWT

- Algorithm: HS256, configurable secret key
- Payload: `{sub: callsign, role: role, exp: expiry_timestamp}`
- Default expiry: 24 hours (configurable via `jwt_expire_minutes`)
- Library: python-jose

---

## Service Layer

- `create_access_token(callsign, role, settings)` → `str` — creates signed JWT
- `decode_access_token(token, settings)` → `dict | None` — validates and decodes JWT, returns None on error

---

## Dependencies

- `get_settings(request)` → `Settings` — extracts settings from app state
- `get_db_session(request)` → yields `Session` — database session from app state factory
- `get_current_user(request, access_token, db, settings)` → `User` — decodes JWT from `access_token` cookie, looks up user by callsign. Raises 401 if no token, invalid token, or user not found
- `require_role(*roles)` → dependency — raises 403 if user's role not in allowed set. Composable: `require_role(UserRole.ADMIN, UserRole.NET_CONTROL)`

---

## API Endpoints

All under `/api/auth`.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/login` | None | Redirect to OIDC provider |
| GET | `/callback` | None | OIDC callback — provision user, issue JWT |
| GET | `/me` | Authenticated | Return current user info |
| POST | `/logout` | None | Delete access_token cookie |
| GET | `/users` | Admin | List all users |
| PATCH | `/users/{callsign}` | Admin | Update user role |

---

## Security

- **State token:** random 32-byte via `secrets.token_urlsafe()`, 10min TTL, httponly cookie, validated with `secrets.compare_digest()`
- **JWT cookie:** httponly, secure, SameSite=lax
- **OIDC subject uniqueness:** prevents multiple accounts from same identity provider
- **Role enforcement:** dependency injection, 403 on insufficient permissions

---

## Error Handling

- No access token: 401
- Invalid/expired token: 401
- User not found: 401
- Insufficient role: 403
- User not found for PATCH: 404

---

## What This Phase Does NOT Include

- **Frontend login UI** — deferred to frontend phase
- **Callsign self-registration** — currently auto-derived from OIDC preferred_username
- **Token refresh** — JWT expires, user must re-authenticate via OIDC
