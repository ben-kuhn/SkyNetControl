# Personal Access Tokens — Design Spec

**Goal:** Add Personal Access Tokens (PATs) to SkyNetControl so that external tools (OpenClaw, scripts, third-party integrations) can authenticate with the API without going through the browser-based OAuth flow. Tokens are scoped, optionally expiring, and revocable by the owner or an admin.

**Architecture:** Opaque tokens (`skynet_` + 32 hex bytes) stored as SHA-256 hashes in the database. The existing `get_current_user` dependency is extended to accept a `Bearer` token in the `Authorization` header alongside the existing cookie-based JWT. A new `require_scope()` dependency enforces per-token scope restrictions. The frontend Profile page replaces its PAT placeholder with a token management UI.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, React/TypeScript (existing frontend)

---

## Data Model

New `PersonalAccessToken` table:

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer, PK | Auto-increment |
| `user_callsign` | String(20), FK → users.callsign | Token owner |
| `name` | String(100), NOT NULL | User-chosen label (e.g., "OpenClaw script") |
| `token_hash` | String(64), UNIQUE, NOT NULL | SHA-256 hex digest of the raw token |
| `token_prefix` | String(8), NOT NULL | First 8 chars of raw token for identification |
| `scopes` | Text, NOT NULL | Comma-separated scope strings |
| `expires_at` | DateTime(tz), nullable | Null = never expires |
| `last_used_at` | DateTime(tz), nullable | Updated on each authenticated request |
| `created_at` | DateTime(tz), NOT NULL | Creation timestamp |
| `revoked_at` | DateTime(tz), nullable | Null = active; set on revocation |

**Token format:** `skynet_` + 32 random hex bytes = 71 characters total. The `skynet_` prefix makes tokens identifiable in logs and config files without exposing the secret portion.

**Storage:** Only the SHA-256 hash is stored. The raw token is returned once at creation time and never retrievable again.

**Scopes storage:** Comma-separated string (e.g., `"schedule:read,checkins:write"`). No join table — the scope set is small and fixed.

---

## Scopes

| Scope | Description | Minimum Role |
|-------|-------------|-------------|
| `schedule:read` | View sessions | VIEWER |
| `schedule:write` | Create/edit/delete sessions | NET_CONTROL |
| `checkins:read` | View check-in data | VIEWER |
| `checkins:write` | Submit/manage check-ins | NET_CONTROL |
| `roster:read` | View roster data | NET_CONTROL |
| `map:read` | View map/GeoJSON data | VIEWER |
| `users:read` | List users | ADMIN |
| `users:write` | Manage users/roles | ADMIN |
| `config:read` | View app configuration | ADMIN |
| `config:write` | Modify app configuration | ADMIN |

### Enforcement Rules

- **Creation constraint:** A user can only request scopes their role permits. A VIEWER cannot create a token with `schedule:write`.
- **Runtime intersection:** On each request, effective permissions = token scopes AND user's current role. If an ADMIN is downgraded to VIEWER, their existing tokens with `users:write` stop working automatically — no revocation needed.
- **Cookie sessions:** Browser-authenticated users (cookie JWT) have implicit full-scope access for their role. The `require_scope()` dependency always passes for cookie auth.

### Scope Constants

Scopes are defined as a Python enum/constants module with a mapping of scope → minimum role. This mapping is used for both creation validation and the frontend checkbox filtering.

---

## API Endpoints

All under `/api/auth/tokens`. All token management endpoints require cookie-based authentication (not PAT auth) — you can't create or revoke tokens using a token.

### Create Token

`POST /api/auth/tokens`

**Auth:** Cookie (VIEWER, NET_CONTROL, ADMIN — not PENDING)

**Request body:**
```json
{
  "name": "OpenClaw integration",
  "scopes": ["schedule:read", "checkins:write"],
  "expires_at": "2027-01-01T00:00:00Z"
}
```

- `name`: required, 1-100 characters
- `scopes`: required, non-empty array of valid scope strings
- `expires_at`: optional, must be in the future if provided

**Response (201):**
```json
{
  "id": 1,
  "name": "OpenClaw integration",
  "token": "skynet_a3f8...full raw token",
  "token_prefix": "skynet_a3",
  "scopes": ["schedule:read", "checkins:write"],
  "expires_at": "2027-01-01T00:00:00Z",
  "created_at": "2026-04-24T12:00:00Z"
}
```

The `token` field is only present in the creation response. It is never returned again.

**Errors:**
- 400: Invalid scope for user's role, name empty/too long, invalid expiry (in the past), max 10 active tokens reached
- 403: PENDING users cannot create tokens

### List Tokens

`GET /api/auth/tokens`

**Auth:** Cookie (VIEWER, NET_CONTROL, ADMIN — not PENDING)

**Response (200):** Array of token objects (without raw token value):
```json
[
  {
    "id": 1,
    "name": "OpenClaw integration",
    "token_prefix": "skynet_a3",
    "scopes": ["schedule:read", "checkins:write"],
    "expires_at": "2027-01-01T00:00:00Z",
    "last_used_at": "2026-04-24T15:30:00Z",
    "created_at": "2026-04-24T12:00:00Z",
    "is_expired": false,
    "is_revoked": false
  }
]
```

Returns only the current user's active (non-revoked) tokens. Includes `is_expired` computed field for convenience.

### Revoke Token

`DELETE /api/auth/tokens/{id}`

**Auth:** Cookie (token owner OR ADMIN)

**Response:** 204 No Content

Sets `revoked_at` to the current timestamp. Does not delete the row (audit trail).

**Errors:**
- 404: Token not found or not owned by user (unless admin)

---

## Auth Dependency Changes

### Extended `get_current_user`

**Current flow:** Extract `access_token` cookie → decode JWT → load User

**New flow:**
1. Check `Authorization: Bearer <token>` header
2. If present and starts with `skynet_`:
   - SHA-256 hash the token
   - Look up `PersonalAccessToken` by `token_hash`
   - Verify: not revoked (`revoked_at IS NULL`) and not expired (`expires_at IS NULL OR expires_at > now`)
   - Load the associated User by `user_callsign`
   - Verify: user exists and role is not PENDING
   - Store token scopes on `request.state.token_scopes`
   - Update `last_used_at` (debounced: only update if last update was >1 minute ago, to avoid write-per-request)
   - Return the User
3. If no Bearer header (or doesn't start with `skynet_`): fall back to existing cookie JWT flow, set `request.state.token_scopes = None` (meaning all scopes)
4. If neither: raise 401

### New `require_scope(*scopes)`

A FastAPI dependency that checks scope permissions:

```python
def require_scope(*scopes: str):
    def dependency(request: Request, user: User = Depends(get_current_user)):
        token_scopes = request.state.token_scopes
        if token_scopes is None:
            return user  # cookie auth = full access
        for scope in scopes:
            if scope not in token_scopes:
                raise HTTPException(403, f"Token missing required scope: {scope}")
        return user
    return dependency
```

### Wiring Scopes to Existing Endpoints

In this spec, we add `require_scope()` to the existing schedule endpoints as a proof of concept:

- `GET /api/schedule/sessions` → `require_scope("schedule:read")`
- Other modules will add scope requirements as they are built out in later sub-projects

---

## Frontend Changes

### ProfilePage Token Manager

Replace the PAT placeholder section with a working token management UI.

**Token list:**
- Shows the user's active tokens in a list/table
- Each row: name, prefix (monospace), scope badges, created date, last used date, expiry
- "Revoke" button per token with confirmation dialog
- "Create Token" button at the top

**Create token form (inline, not modal):**
- Name input (required)
- Scope checkboxes grouped by module, filtered to only show scopes the user's role permits
- Optional expiry date input
- Submit button

**Token reveal (after creation):**
- Raw token displayed in a highlighted box with monospace font
- "Copy to clipboard" button
- Warning text: "Copy this token now. It will not be shown again."
- "Done" button dismisses the reveal and shows the new token in the list

**Revoke confirmation:**
- Uses the existing Modal component
- "Revoke token '{name}'?" with Revoke (danger) and Cancel buttons

### New/Modified Frontend Files

| File | Change |
|------|--------|
| `frontend/src/api/tokens.ts` | Create: createToken, listTokens, revokeToken API functions |
| `frontend/src/types/index.ts` | Modify: add Token, TokenCreate, TokenWithSecret types + SCOPES constant |
| `frontend/src/pages/ProfilePage.tsx` | Modify: replace PAT placeholder with token manager |

---

## Limits and Validation

- **Max active tokens per user:** 10 (active = not revoked)
- **Token name:** 1-100 characters, no format restriction
- **Expiry:** Optional. If provided, must be in the future. No maximum expiry enforced.
- **Scopes:** Must be a non-empty array of valid scope strings. Each scope must be permitted by the user's current role.

---

## Security Considerations

- **Token storage:** Only SHA-256 hashes stored. Raw tokens are never logged, stored, or retrievable after creation.
- **Token management auth:** Create/list/revoke endpoints require cookie auth only. A PAT cannot be used to manage other PATs — prevents token-creates-token escalation.
- **Scope intersection:** Even if a token has elevated scopes, they are checked against the user's current role at request time. Role downgrades are immediately effective.
- **`last_used_at` debounce:** Updated at most once per minute per token to avoid excessive writes.
- **Prefix identification:** The `token_prefix` and `skynet_` format allow tokens to be identified in logs without exposing the secret.

---

## Testing

Backend tests covering:
- Token creation (success, invalid scopes, too many tokens, expired date, PENDING user blocked)
- Token listing (returns only owner's tokens, no raw values)
- Token revocation (owner can revoke, admin can revoke others', non-owner gets 404)
- PAT authentication (valid token, revoked token, expired token, invalid token)
- Scope enforcement (token with limited scopes, cookie auth bypasses scope check)
- Role intersection (admin downgraded, scopes stop working)
- `last_used_at` updates on PAT auth

No frontend tests in this spec (frontend testing strategy to be defined separately).

---

## What This Spec Does NOT Build

- **Token refresh/rotation** — tokens are static until revoked
- **Rate limiting per token** — not needed at current scale
- **Webhook signing** — separate concern
- **Admin token dashboard** — admins can revoke any token but don't get a cross-user listing UI (they can use the API directly)
