# Auth Provider Configuration & Secrets Management — Design Spec

**Goal:** Support multiple auth providers (Google, Microsoft, GitHub, Discord, Facebook, Generic OIDC) as peers, add a registration flow with admin approval for new users, introduce a PENDING role, allow callsign changes, and document secrets management patterns.

**Architecture:** A provider registry maps provider names to their OAuth2/OIDC configuration. Each provider is independently enabled/disabled via settings. New users land in PENDING status, self-register with a validated callsign, and await admin approval. Callsign changes cascade via foreign keys.

**Tech Stack:** FastAPI, Authlib, python-jose, SQLAlchemy 2.0+, Alembic, httpx

---

## Multi-Provider Auth

### Provider Registry

Six providers supported as peers — each is a named entry with its own enable flag and credentials:

| Provider | Protocol | Discovery | Userinfo Mapping |
|----------|----------|-----------|-----------------|
| Google | OIDC | `https://accounts.google.com/.well-known/openid-configuration` | `sub`, `name`, `email` from ID token |
| Microsoft | OIDC | `https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration` | `sub`, `name`, `email` from ID token |
| GitHub | OAuth2 | Hardcoded (`https://github.com/login/oauth/authorize`, `/access_token`) | `id` as subject, `name` from `https://api.github.com/user` |
| Discord | OAuth2 | Hardcoded (`https://discord.com/api/oauth2/authorize`, `/token`) | `id` as subject, `username` from `https://discord.com/api/users/@me` |
| Facebook | OAuth2 | Hardcoded (`https://www.facebook.com/v19.0/dialog/oauth`, token + userinfo URLs) | `id` as subject, `name` from Graph API `/me?fields=id,name,email` |
| Generic OIDC | OIDC | `{issuer_url}/.well-known/openid-configuration` | `sub`, `name` or `preferred_username` from userinfo endpoint |

Each provider entry in the registry defines:
- `authorize_url`, `token_url`, `userinfo_url` (or `discovery_url` for OIDC providers)
- `scopes` (provider-specific defaults, e.g., `openid email profile` for OIDC, `read:user` for GitHub)
- `extract_subject(userinfo) -> str` — how to get the unique user identifier
- `extract_name(userinfo) -> str` — how to get the display name

### Configuration

Settings use the existing `SKYNET_` env prefix. Each provider has three settings (plus `issuer_url` for Generic OIDC):

```
SKYNET_AUTH_GOOGLE_ENABLED=true
SKYNET_AUTH_GOOGLE_CLIENT_ID=...
SKYNET_AUTH_GOOGLE_CLIENT_SECRET=...

SKYNET_AUTH_MICROSOFT_ENABLED=false
SKYNET_AUTH_MICROSOFT_CLIENT_ID=
SKYNET_AUTH_MICROSOFT_CLIENT_SECRET=

SKYNET_AUTH_GITHUB_ENABLED=true
SKYNET_AUTH_GITHUB_CLIENT_ID=...
SKYNET_AUTH_GITHUB_CLIENT_SECRET=...

SKYNET_AUTH_DISCORD_ENABLED=false
SKYNET_AUTH_DISCORD_CLIENT_ID=
SKYNET_AUTH_DISCORD_CLIENT_SECRET=

SKYNET_AUTH_FACEBOOK_ENABLED=false
SKYNET_AUTH_FACEBOOK_CLIENT_ID=
SKYNET_AUTH_FACEBOOK_CLIENT_SECRET=

SKYNET_AUTH_OIDC_ENABLED=false
SKYNET_AUTH_OIDC_CLIENT_ID=
SKYNET_AUTH_OIDC_CLIENT_SECRET=
SKYNET_AUTH_OIDC_ISSUER_URL=
```

The existing `SKYNET_OIDC_*` settings are replaced by the `SKYNET_AUTH_OIDC_*` and `SKYNET_AUTH_{PROVIDER}_*` settings. The old settings are removed.

**Pydantic Settings structure:**

```python
class ProviderSettings(BaseModel):
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""

class OIDCProviderSettings(ProviderSettings):
    issuer_url: str = ""

class Settings(BaseSettings):
    # ... existing fields ...
    auth_google: ProviderSettings = ProviderSettings()
    auth_microsoft: ProviderSettings = ProviderSettings()
    auth_github: ProviderSettings = ProviderSettings()
    auth_discord: ProviderSettings = ProviderSettings()
    auth_facebook: ProviderSettings = ProviderSettings()
    auth_oidc: OIDCProviderSettings = OIDCProviderSettings()
```

Pydantic Settings with `env_prefix="SKYNET_"` and `env_nested_delimiter="_"` maps `SKYNET_AUTH_GOOGLE_CLIENT_ID` to `settings.auth_google.client_id`.

### App Startup

On startup, for each enabled provider:

1. **OIDC providers** (Google, Microsoft, Generic OIDC): Fetch discovery document, cache endpoints in `app.state.providers[name]`
2. **OAuth2 providers** (GitHub, Discord, Facebook): Endpoints are hardcoded in the registry, stored in `app.state.providers[name]`
3. If no providers are enabled, log an error and raise — the app cannot function without auth
4. If an OIDC discovery fetch fails, log the error and skip that provider (don't block startup for optional providers). Raise only if *all* providers fail.

### Routes

Replace the current single-provider endpoints with provider-aware routes:

**`GET /api/auth/providers`** — Returns list of enabled providers (public, no auth):
```json
[
  {"name": "google", "label": "Google"},
  {"name": "github", "label": "GitHub"}
]
```

**`GET /api/auth/login/{provider}`** — Initiates OAuth2/OIDC flow for the named provider. Sets `oauth_state` cookie with both the CSRF token and the provider name so the callback knows which provider to use.

**`GET /api/auth/callback/{provider}`** — Handles the OAuth2/OIDC callback:
1. Validate state cookie matches (CSRF protection)
2. Exchange code for token using the provider's token endpoint
3. Fetch user info using the provider's userinfo endpoint/method
4. Extract subject and name using the provider's mapping functions
5. Store subject as `{provider}:{subject}` in `oidc_subject` to avoid collisions across providers
6. Continue to user lookup/creation (see Registration Flow)

**`POST /api/auth/logout`** — Unchanged.

### User Model Changes

The `oidc_subject` field stores `{provider}:{subject}` (e.g., `google:1234567890`, `github:42`). This ensures uniqueness across providers and identifies which provider a user authenticated with.

No changes to the `User` model schema — the `oidc_subject` field (String 255) is already sufficient.

### Redirect URI Configuration

Each provider needs a redirect URI registered in its developer console. The pattern is:
`{app_base_url}/api/auth/callback/{provider}`

The existing `oidc_redirect_uri` setting is removed. Redirect URIs are constructed at runtime from `app_base_url` + provider name.

---

## Registration Flow

New users created during OAuth callback start in `PENDING` status with a placeholder callsign. They must complete registration before accessing the app.

### Callback User Creation

When a new user arrives (no existing `oidc_subject` match):

1. Generate a placeholder callsign: `PENDING-{oidc_subject[:12]}` (unique, clearly not a real callsign)
2. Create `User(callsign=placeholder, oidc_subject=..., name=..., role=PENDING)`
3. Issue JWT and redirect to app (frontend handles the PENDING state)

When an existing user arrives: no change — issue JWT and redirect.

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
| `/api/auth/providers` | GET | List providers (public) |

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

## Email Notifications

SMTP-based notifications for two events: new user registration (to admins) and user approval (to the user).

### SMTP Configuration

```
SKYNET_SMTP_HOST=smtp.example.com
SKYNET_SMTP_PORT=587
SKYNET_SMTP_USERNAME=skynetcontrol@example.com
SKYNET_SMTP_PASSWORD=app-password-here
SKYNET_SMTP_USE_TLS=true
SKYNET_SMTP_FROM_ADDRESS=skynetcontrol@example.com
```

**Pydantic Settings:**

```python
class SmtpSettings(BaseModel):
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    use_tls: bool = True
    from_address: str = ""

class Settings(BaseSettings):
    # ... existing fields ...
    smtp: SmtpSettings = SmtpSettings()
```

If `smtp.host` is empty, email is disabled — notifications are skipped silently with a log warning. The app functions normally without email configured.

### Notification: New Registration → Admins

**Triggered by:** `POST /api/auth/register` (after successful callsign registration)

**Recipients:** All users with role `ADMIN` who have an email address on file.

**Email content:**
- Subject: `[SkyNetControl] New registration: {callsign}`
- Body: `{name} has registered as {callsign} and is awaiting approval. Review pending users at {app_base_url}.`

### Notification: Approval → User

**Triggered by:** `PATCH /api/users/{callsign}` when `role` changes from `PENDING` to any non-PENDING role.

**Recipient:** The approved user (by email address).

**Email content:**
- Subject: `[SkyNetControl] Your account has been approved`
- Body: `Your account ({callsign}) has been approved as {role}. You can now access SkyNetControl at {app_base_url}.`

### User Email Storage

Add an `email` field to the `User` model:

```python
email: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

Populated from the OAuth provider's userinfo response during callback (most providers return `email` in their claims). Users without an email from their provider simply don't receive approval notifications — no blocker.

### Implementation

A single `backend/auth/email.py` module with:
- `send_email(to, subject, body)` — sends via SMTP using Python's `smtplib` + `email.message`. Runs in a thread executor (`asyncio.to_thread`) to avoid blocking the event loop.
- `notify_admins_new_registration(db, user, settings)` — queries admins, sends notification
- `notify_user_approved(user, settings)` — sends approval notification

Email sending is fire-and-forget: failures are logged but never raise to the caller. A failed notification should not block registration or approval.

---

## Secrets Management (Documentation Only)

No code changes. Add a `docs/deployment/secrets.md` guide covering:

### Environment Variables

All secrets use the `SKYNET_` prefix (handled by Pydantic Settings):

| Variable | Purpose | Example |
|----------|---------|---------|
| `SKYNET_JWT_SECRET_KEY` | JWT signing key | Random 256-bit hex string |
| `SKYNET_AUTH_GOOGLE_CLIENT_ID` | Google OAuth client ID | From Google Cloud Console |
| `SKYNET_AUTH_GOOGLE_CLIENT_SECRET` | Google OAuth client secret | From Google Cloud Console |
| `SKYNET_AUTH_GITHUB_CLIENT_ID` | GitHub OAuth app client ID | From GitHub Developer Settings |
| `SKYNET_AUTH_GITHUB_CLIENT_SECRET` | GitHub OAuth app client secret | From GitHub Developer Settings |
| `SKYNET_AUTH_OIDC_ISSUER_URL` | Generic OIDC issuer URL | `https://auth.example.com/application/o/skynetcontrol` |
| `SKYNET_AUTH_OIDC_CLIENT_ID` | Generic OIDC client ID | From provider dashboard |
| `SKYNET_AUTH_OIDC_CLIENT_SECRET` | Generic OIDC client secret | From provider dashboard |
| `SKYNET_SMTP_HOST` | SMTP server hostname | `smtp.example.com` |
| `SKYNET_SMTP_PASSWORD` | SMTP auth password | App password from email provider |
| `SKYNET_DATABASE_URL` | Database connection string | `postgresql://user:pass@host/db` |

(Microsoft, Discord, Facebook follow the same `_CLIENT_ID` / `_CLIENT_SECRET` pattern. See SMTP Configuration section for full SMTP settings.)

### Deployment Patterns

**NixOS with sops-nix:**
```nix
sops.secrets."skynetcontrol/env" = {};

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
SKYNET_AUTH_GOOGLE_CLIENT_SECRET=secret-here
SKYNET_AUTH_GITHUB_CLIENT_SECRET=secret-here
SKYNET_SMTP_PASSWORD=app-password-here
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
| `backend/auth/models.py` | Modify | Add `PENDING` to `UserRole` enum, add `email` field to `User` |
| `backend/auth/providers.py` | Create | Provider registry — per-provider OAuth2/OIDC config, endpoint URLs, userinfo extraction |
| `backend/auth/routes.py` | Rewrite | Multi-provider login/callback, registration endpoint, callsign change endpoint, providers list |
| `backend/auth/dependencies.py` | Modify | Add `require_not_pending()` dependency |
| `backend/auth/email.py` | Create | SMTP email sending, admin notification on registration, user notification on approval |
| `backend/auth/service.py` | Modify | Add OIDC discovery fetch, provider initialization |
| `backend/config.py` | Modify | Replace single OIDC settings with per-provider `ProviderSettings` + `SmtpSettings` models, add `env_nested_delimiter` |
| `backend/app.py` | Modify | Initialize enabled providers on startup, store in `app.state.providers` |
| `alembic/versions/XXXX_add_pending_role_and_cascade.py` | Create | Add PENDING enum value, add `email` column to users, add ON UPDATE CASCADE to callsign FKs |
| `docs/deployment/secrets.md` | Create | Secrets management guide |

---

## What This Phase Does NOT Include

- **Apple Sign In** — Unusual POST-based callback and key rotation adds complexity for limited user base benefit. Can be added later using the same provider registry pattern.
- **Rate limiting** — Callsign changes and registration are naturally infrequent.
- **Frontend changes** — Frontend will need a provider selection screen, registration form, and pending-state UI, but that's a separate spec.
- **Provider linking** — A user who signs in with Google and later signs in with GitHub gets two separate accounts. Account linking is a future enhancement.
