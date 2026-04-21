# Auth Provider Configuration & Secrets Management — Design Spec

**Goal:** Replace hardcoded OIDC endpoint paths with discovery, add a registration flow with admin approval for new users, introduce a PENDING role, allow callsign changes, and document secrets management patterns.

**Architecture:** OIDC discovery via `.well-known/openid-configuration` cached in app state. New users land in PENDING status with a placeholder callsign, then self-register with a validated callsign. Admins approve users via the existing role-update endpoint. Callsign changes cascade through a single transaction.

**Tech Stack:** FastAPI, Authlib, python-jose, SQLAlchemy 2.0+, Alembic, httpx

---

## OIDC Discovery

Replace the current hardcoded endpoint construction (`{issuer}/authorize`, `{issuer}/token`, `{issuer}/userinfo`) with standard OIDC discovery.

**On app startup:**

1. Fetch `{oidc_issuer_url}/.well-known/openid-configuration`
2. Extract and cache `authorization_endpoint`, `token_endpoint`, `userinfo_endpoint` in `app.state.oidc_config`
3. If the fetch fails, log an error and raise — the app cannot start without valid OIDC configuration

**Usage in auth routes:**

- `login` reads `app.state.oidc_config["authorization_endpoint"]` instead of constructing `{issuer}/authorize`
- `callback` reads `token_endpoint` and `userinfo_endpoint` similarly

**Config changes:** No new settings. The existing `oidc_issuer_url` is the discovery base URL.

**Why discovery matters:** Different OIDC providers (Authentik, PocketID, social providers behind a proxy) use different endpoint paths. Discovery eliminates provider-specific assumptions.

---

## Registration Flow

New users created during OIDC callback start in `PENDING` status with a placeholder callsign. They must complete registration before accessing the app.

### OIDC Callback Changes

When a new user arrives (no existing `oidc_subject` match):

1. Generate a placeholder callsign: `PENDING-{oidc_subject[:12]}` (unique, clearly not a real callsign)
2. Create `User(callsign=placeholder, oidc_subject=..., name=..., role=PENDING)`
3. Issue JWT and redirect to app (frontend handles the PENDING state)

When an existing user arrives: no change to current behavior.

### Registration Endpoint

`POST /api/auth/register`

**Request body:**
```json
{
  "callsign": "W0ABC"
}
```

**Validation:**
- User must be authenticated (valid JWT)
- User must have role `PENDING` (already-registered users get 409)
- Callsign must match regex: `^[A-Z]{1,2}\d[A-Z]{1,4}$`
- Callsign must not already exist in the database (unique constraint, 409)

**On success:**
- Update user's `callsign` (primary key change — see Callsign Changes section)
- Role remains `PENDING` (admin approval still required)
- Return updated user object

**On failure:**
- 400: Invalid callsign format
- 409: Callsign already taken or user already registered
- 401: Not authenticated

---

## PENDING Role

Add `PENDING = "pending"` to the `UserRole` enum.

### Access Control

PENDING users can access:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/schedule/*` | GET | View net schedule |
| `/api/auth/me` | GET | View own profile |
| `/api/auth/register` | POST | Complete registration |
| `/api/auth/logout` | POST | Log out |
| `/api/auth/me` | PATCH | Change callsign |

Everything else returns 403 for PENDING users.

### Implementation

Modify `require_role()` in `dependencies.py`: no changes needed — it already checks `user.role not in roles`. Routes that should exclude PENDING users already use `require_role(ADMIN)`, `require_role(ADMIN, NET_CONTROL)`, etc.

Add a new dependency `require_not_pending()` that rejects PENDING users with a 403. Apply it to routes that currently have no role requirement but should exclude PENDING users (e.g., check-in endpoints).

The schedule GET endpoints and auth endpoints listed above remain accessible without role checks (or with explicit PENDING allowance).

### Alembic Migration

Add `PENDING` to the `userrole` enum type. For SQLite (dev), this requires no special handling. For PostgreSQL (prod), use `ALTER TYPE userrole ADD VALUE 'pending'`.

---

## Callsign Changes

`PATCH /api/auth/me`

**Request body:**
```json
{
  "callsign": "W0XYZ"
}
```

**Validation:**
- Same regex as registration: `^[A-Z]{1,2}\d[A-Z]{1,4}$`
- Must not already exist (409)
- User must be authenticated

**Implementation:**

Callsign is the primary key of the `users` table and is referenced as a foreign key in:
- `check_ins.callsign`
- `roster_logs` (via session → check-ins)
- Any future tables

The migration adds `ON UPDATE CASCADE` to all foreign keys referencing `users.callsign`. With cascading in place, the update is a single `UPDATE users SET callsign = :new WHERE callsign = :old` — the database propagates the change to all referencing tables automatically.

**Rate limiting:** Not implemented in this phase. Callsign changes are infrequent by nature.

---

## Secrets Management (Documentation Only)

No code changes. Add a `docs/deployment/secrets.md` guide covering:

### Environment Variables

All secrets use the `SKYNET_` prefix (handled by Pydantic Settings):

| Variable | Purpose | Example |
|----------|---------|---------|
| `SKYNET_JWT_SECRET_KEY` | JWT signing key | Random 256-bit hex string |
| `SKYNET_OIDC_CLIENT_ID` | OIDC client identifier | From provider dashboard |
| `SKYNET_OIDC_CLIENT_SECRET` | OIDC client secret | From provider dashboard |
| `SKYNET_OIDC_ISSUER_URL` | OIDC issuer base URL | `https://auth.example.com/application/o/skynetcontrol` |
| `SKYNET_DATABASE_URL` | Database connection string | `postgresql://user:pass@host/db` |

### Deployment Patterns

**NixOS with sops-nix:**
```nix
sops.secrets."skynetcontrol/jwt-secret" = {};
sops.secrets."skynetcontrol/oidc-client-secret" = {};

systemd.services.skynetcontrol.serviceConfig.EnvironmentFile =
  config.sops.secrets."skynetcontrol/env".path;
```

**NixOS with agenix:**
```nix
age.secrets.skynetcontrol-env.file = ../secrets/skynetcontrol-env.age;

systemd.services.skynetcontrol.serviceConfig.EnvironmentFile =
  config.age.secrets.skynetcontrol-env.path;
```

**systemd EnvironmentFile (generic):**
```ini
# /etc/skynetcontrol/env (mode 0600, owned by service user)
SKYNET_JWT_SECRET_KEY=hex-string-here
SKYNET_OIDC_CLIENT_SECRET=secret-here
```

**Docker/OCI:**
```bash
docker run --env-file /path/to/env ghcr.io/owner/skynetcontrol:latest
```

### What NOT to Do

- Do not commit secrets to git
- Do not use the default `jwt_secret_key` value in production
- Do not pass secrets via command-line arguments (visible in `ps`)

---

## File Summary

| File | Action | Description |
|------|--------|-------------|
| `backend/auth/models.py` | Modify | Add `PENDING` to `UserRole` enum |
| `backend/auth/routes.py` | Modify | OIDC discovery endpoints, registration endpoint, callsign change endpoint, PENDING callback logic |
| `backend/auth/dependencies.py` | Modify | Add `require_not_pending()` dependency |
| `backend/auth/service.py` | Modify | Add OIDC discovery fetch function |
| `backend/config.py` | No change | Existing settings suffice |
| `backend/app.py` | Modify | Call OIDC discovery on startup, store in `app.state` |
| `alembic/versions/XXXX_add_pending_role_and_cascade.py` | Create | Add PENDING enum value, add ON UPDATE CASCADE to callsign FKs |
| `docs/deployment/secrets.md` | Create | Secrets management guide |

---

## What This Phase Does NOT Include

- **Social login aggregation** — The app supports any OIDC-compliant provider. Users who want Google/GitHub/etc. login configure that at the OIDC provider level (e.g., Authentik federates social providers). No multi-provider code needed.
- **Email notifications** — Admin approval is manual via the existing `/users/{callsign}` PATCH endpoint. No email/notification on approval.
- **Rate limiting** — Callsign changes and registration are naturally infrequent.
- **Frontend changes** — Frontend will need a registration form and pending-state UI, but that's a separate spec.
