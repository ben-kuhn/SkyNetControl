# Config Unification — Phase 2b: Config Page UI for OAuth/SMTP + Test Endpoints

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured CRUD endpoints + Config-page UI for editing OAuth providers and SMTP from the browser. Land the verify-then-commit test mechanics (real OAuth round-trip; live SMTP send) that mitigate the lockout failure mode.

**Architecture:**

- **Backend** — three new route groups under `/api/admin/`:
  - `oauth/providers/*` (list, get, upsert, delete) wrapping the Phase 1 accessor.
  - `smtp` (get, upsert, clear) wrapping the Phase 1 accessor.
  - `test/oauth/{slug}` and `test/smtp` — verify-then-commit hooks that operate on unsaved values.
- **OAuth test flow** — admin clicks "Test sign-in"; frontend POSTs unsaved credentials to `/api/admin/test/oauth/{slug}/start`, gets back an authorize URL + `test_session_id`. Frontend opens a popup at the authorize URL. After the user signs in, the OAuth provider redirects the popup back to a fixed `/api/admin/test/oauth/callback` route (a single shared redirect URI). The backend exchanges the code using the unsaved credentials kept in an in-memory `_TEST_SESSIONS` dict (TTL ~10 min), records success/failure, and serves an auto-closing HTML page that does `window.opener.postMessage(...)` back to the Config page.
- **SMTP test** — POST unsaved values + a destination address; backend opens an SMTP connection synchronously (with a short timeout) and tries to send a small canned message. Returns `{ok, error?}`.
- **Frontend** — `ConfigPage.tsx` gains two new groups (Authentication, Email). The existing flat key-value pattern still handles the other groups; the new groups use richer components (provider rows + modals; SMTP form block). No automated test infrastructure exists for the frontend (no Vitest/Jest/etc.), so frontend tasks have a manual-verification step.

**Tech Stack:** FastAPI + Pydantic (backend), React + TypeScript + Vite + Tailwind (frontend). Existing patterns in `backend/auth/routes.py`, `backend/config_mgmt/routes.py`, and `frontend/src/pages/ConfigPage.tsx`.

**Spec:** `docs/superpowers/specs/2026-06-12-config-unification-design.md`

---

## File structure

**New backend files:**

| Path | Responsibility |
|------|----------------|
| `backend/config_mgmt/oauth_routes.py` | OAuth provider CRUD + slug validation surface. |
| `backend/config_mgmt/smtp_routes.py` | SMTP CRUD. |
| `backend/config_mgmt/test_routes.py` | OAuth round-trip test + SMTP send test, plus the in-memory `_TEST_SESSIONS` dict. |
| `tests/test_oauth_routes.py` | Backend tests for OAuth CRUD. |
| `tests/test_smtp_routes.py` | Backend tests for SMTP CRUD. |
| `tests/test_admin_test_routes.py` | Backend tests for the test endpoints (OAuth start + callback flow, SMTP test). |

**Modified backend files:**

| Path | Change |
|------|--------|
| `backend/app.py` | Mount the three new routers under `/api/admin/`. |

**New frontend files:**

| Path | Responsibility |
|------|----------------|
| `frontend/src/api/oauth.ts` | Typed client for the OAuth CRUD + test endpoints. |
| `frontend/src/api/smtp.ts` | Typed client for the SMTP endpoints. |
| `frontend/src/components/OAuthProviderList.tsx` | Provider list + row + add/edit modal. |
| `frontend/src/components/OAuthTestButton.tsx` | Popup window + postMessage listener for the OAuth test sign-in. |
| `frontend/src/components/SmtpForm.tsx` | SMTP form + test-send modal. |
| `frontend/src/components/TestResultBanner.tsx` | Tiny inline success/error display reused for both tests. |

**Modified frontend files:**

| Path | Change |
|------|--------|
| `frontend/src/pages/ConfigPage.tsx` | Replace the flat `pat_mailbox_path` / `scanner.*` / `claude_api_key` / `delivery.*` group entries with the same flat-key form (unchanged) PLUS the new Authentication and Email groups rendered above them. |

---

## URL surface

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET    | `/api/admin/oauth/providers`            | ADMIN | List all providers (client_secret redacted). |
| GET    | `/api/admin/oauth/providers/{slug}`     | ADMIN | Get one provider (client_secret redacted). |
| PUT    | `/api/admin/oauth/providers/{slug}`     | ADMIN | Upsert. `client_secret=""` in body means "leave existing value untouched"; `"-"` means clear. |
| DELETE | `/api/admin/oauth/providers/{slug}`     | ADMIN | Delete all `oauth.<slug>.*` rows. |
| GET    | `/api/admin/smtp`                       | ADMIN | Get SMTP (password redacted to `"***"` if set). 404 if not configured. |
| PUT    | `/api/admin/smtp`                       | ADMIN | Upsert all 6 fields. Empty password means "leave existing value untouched". |
| DELETE | `/api/admin/smtp`                       | ADMIN | Clear all `smtp.*` rows. |
| POST   | `/api/admin/test/oauth/{slug}/start`    | ADMIN | Body: `{client_id, client_secret, issuer_url, name}`. Returns `{test_session_id, authorize_url}`. |
| GET    | `/api/admin/test/oauth/callback`        | (no auth — state-validated) | OAuth provider redirects here. Looks up `_TEST_SESSIONS[state]`, exchanges code, marks session success/failure, serves auto-close HTML with `postMessage`. |
| GET    | `/api/admin/test/oauth/{test_session_id}/result` | ADMIN | Poll fallback when postMessage misses (popup-blocker etc.). Returns `{status, error?, identity?}`. |
| POST   | `/api/admin/test/smtp`                  | ADMIN | Body: `{host, port, username, password, from_address, use_tls, to_address}`. Sends one test message. Returns `{ok, error?}`. |

**Redaction policy on GET:** never return raw secrets. The frontend has no need to display them; for an edit modal the convention is `placeholder="(unchanged)"` on the secret input.

**Slug rules on PUT:** the `_check_slug` helper from Phase 2a Task 1 enforces this server-side. A new endpoint `POST /api/admin/oauth/providers/slug/derive?name=<friendly>` returns the auto-derived slug for the wizard / add-modal preview (calls `slugify` from `backend/auth/oidc_slug.py`).

---

## Task 1: Backend OAuth provider CRUD routes

**Files:**
- Create: `backend/config_mgmt/oauth_routes.py`
- Create: `tests/test_oauth_routes.py`
- Modify: `backend/app.py` (register router)

### Steps

- [ ] **Step 1: Write the failing tests**

Create `tests/test_oauth_routes.py` with:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from backend.auth.models import User, UserRole
from backend.config_mgmt.oauth import OAuthProviderConfig, upsert_oauth_provider


@pytest.fixture
async def admin_client(test_app):
    from backend.db.session import session_factory_for
    factory = session_factory_for(test_app.state.engine)
    with factory() as db:
        admin = User(callsign="W0NE", oidc_subject="test:admin",
                     name="Admin", role=UserRole.ADMIN)
        db.add(admin)
        db.commit()
        db.refresh(admin)
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # Forge the admin session cookie. The exact mechanism depends on
        # how the existing test_auth_routes.py issues sessions — read
        # that file and use whatever fixture/helper it already has.
        # (See conftest.py 'authenticated_admin_client' if it exists; if
        #  not, mint a JWT manually via backend.auth.service.create_access_token
        #  and set the cookie.)
        yield c, admin


# 1
async def test_list_providers_empty(admin_client):
    client, _ = admin_client
    response = await client.get("/api/admin/oauth/providers")
    assert response.status_code == 200
    assert response.json() == []


# 2
async def test_upsert_and_list_provider(admin_client, test_app):
    client, _ = admin_client
    response = await client.put(
        "/api/admin/oauth/providers/google",
        json={"name": "Google", "enabled": True,
              "client_id": "cid", "client_secret": "csec", "issuer_url": ""},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == "google"
    assert body["client_secret"] == "***"  # redacted
    list_resp = await client.get("/api/admin/oauth/providers")
    assert any(p["slug"] == "google" for p in list_resp.json())


# 3
async def test_get_redacts_client_secret(admin_client):
    client, _ = admin_client
    await client.put(
        "/api/admin/oauth/providers/github",
        json={"name": "GitHub", "enabled": True,
              "client_id": "gh", "client_secret": "REAL-SECRET", "issuer_url": ""},
    )
    response = await client.get("/api/admin/oauth/providers/github")
    assert response.status_code == 200
    assert response.json()["client_secret"] == "***"
    assert "REAL-SECRET" not in response.text


# 4
async def test_upsert_blank_secret_preserves_existing(admin_client):
    client, _ = admin_client
    await client.put(
        "/api/admin/oauth/providers/github",
        json={"name": "GitHub", "enabled": True,
              "client_id": "gh", "client_secret": "KEEP-ME", "issuer_url": ""},
    )
    await client.put(
        "/api/admin/oauth/providers/github",
        json={"name": "GitHub", "enabled": True,
              "client_id": "gh-new", "client_secret": "", "issuer_url": ""},
    )
    # We can't read the secret back, but we can confirm a subsequent
    # round-trip kept whatever was there — for now assert via the DB.
    from backend.config_mgmt.oauth import get_oauth_provider
    from backend.db.session import session_factory_for
    with session_factory_for(client._transport.app.state.engine)() as db:
        p = get_oauth_provider(db, "github")
        assert p is not None
        assert p.client_secret == "KEEP-ME"
        assert p.client_id == "gh-new"


# 5
async def test_upsert_dash_secret_clears(admin_client):
    client, _ = admin_client
    await client.put(
        "/api/admin/oauth/providers/github",
        json={"name": "GitHub", "enabled": True,
              "client_id": "gh", "client_secret": "OLD", "issuer_url": ""},
    )
    await client.put(
        "/api/admin/oauth/providers/github",
        json={"name": "GitHub", "enabled": True,
              "client_id": "gh", "client_secret": "-", "issuer_url": ""},
    )
    from backend.config_mgmt.oauth import get_oauth_provider
    from backend.db.session import session_factory_for
    with session_factory_for(client._transport.app.state.engine)() as db:
        p = get_oauth_provider(db, "github")
        assert p is not None and p.client_secret == ""


# 6
async def test_delete_provider(admin_client):
    client, _ = admin_client
    await client.put(
        "/api/admin/oauth/providers/microsoft",
        json={"name": "Microsoft", "enabled": True,
              "client_id": "m", "client_secret": "s", "issuer_url": ""},
    )
    response = await client.delete("/api/admin/oauth/providers/microsoft")
    assert response.status_code == 204
    assert (await client.get("/api/admin/oauth/providers/microsoft")).status_code == 404


# 7
async def test_invalid_slug_rejected(admin_client):
    client, _ = admin_client
    response = await client.put(
        "/api/admin/oauth/providers/bad-slug!",
        json={"name": "X", "enabled": True,
              "client_id": "c", "client_secret": "s", "issuer_url": ""},
    )
    assert response.status_code == 400
    assert "slug" in response.text.lower()


# 8
async def test_non_admin_forbidden(test_app):
    # Without an admin session, every CRUD call returns 401/403.
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for verb, path in [
            ("get", "/api/admin/oauth/providers"),
            ("get", "/api/admin/oauth/providers/google"),
            ("put", "/api/admin/oauth/providers/google"),
            ("delete", "/api/admin/oauth/providers/google"),
        ]:
            response = await getattr(client, verb)(path, json={})
            assert response.status_code in (401, 403)


# 9
async def test_slug_derive_endpoint(admin_client):
    client, _ = admin_client
    response = await client.post(
        "/api/admin/oauth/providers/slug/derive?name=PocketID Auth",
    )
    assert response.status_code == 200
    assert response.json() == {"slug": "pocketid-auth", "valid": True}


# 10
async def test_slug_derive_rejects_reserved(admin_client):
    client, _ = admin_client
    response = await client.post(
        "/api/admin/oauth/providers/slug/derive?name=Google",
    )
    body = response.json()
    assert body["slug"] == "google"
    assert body["valid"] is False
    assert "reserved" in body["error"].lower()
```

**Note on the admin-client fixture:** read `tests/conftest.py` and `tests/test_auth_routes.py` first. There may already be an authenticated-admin fixture you can reuse. If not, the cleanest path is to mint a JWT manually with `backend.auth.service.create_access_token` and set the `auth_token` cookie.

- [ ] **Step 2: Run to confirm failure**

Run: `.venv/bin/pytest tests/test_oauth_routes.py -q`
Expected: collection errors (`backend.config_mgmt.oauth_routes` doesn't exist).

- [ ] **Step 3: Implement `backend/config_mgmt/oauth_routes.py`**

```python
from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session, require_role
from backend.auth.models import User, UserRole
from backend.auth.oidc_slug import slugify, validate_slug
from backend.config_mgmt.oauth import (
    OAuthProviderConfig,
    delete_oauth_provider,
    get_oauth_provider,
    list_oauth_providers,
    upsert_oauth_provider,
)

oauth_router = APIRouter(prefix="/oauth/providers", tags=["admin-oauth"])

_REDACTED = "***"


class OAuthProviderResponse(BaseModel):
    slug: str
    name: str
    enabled: bool
    client_id: str
    client_secret: str   # always "***" or "" — never the real secret
    issuer_url: str


class OAuthProviderUpsert(BaseModel):
    name: str
    enabled: bool
    client_id: str
    client_secret: str   # "" = preserve existing; "-" = clear; anything else = set
    issuer_url: str


def _to_response(p: OAuthProviderConfig) -> OAuthProviderResponse:
    return OAuthProviderResponse(
        slug=p.slug,
        name=p.name,
        enabled=p.enabled,
        client_id=p.client_id,
        client_secret=_REDACTED if p.client_secret else "",
        issuer_url=p.issuer_url,
    )


@oauth_router.get("")
def list_providers(
    _: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
) -> list[OAuthProviderResponse]:
    return [_to_response(p) for p in list_oauth_providers(db)]


@oauth_router.get("/{slug}")
def get_provider(
    slug: str,
    _: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
) -> OAuthProviderResponse:
    p = get_oauth_provider(db, slug)
    if p is None:
        raise HTTPException(404)
    return _to_response(p)


@oauth_router.put("/{slug}")
def upsert_provider(
    slug: str,
    body: OAuthProviderUpsert,
    _: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
) -> OAuthProviderResponse:
    existing = get_oauth_provider(db, slug)
    if body.client_secret == "":
        secret = existing.client_secret if existing else ""
    elif body.client_secret == "-":
        secret = ""
    else:
        secret = body.client_secret
    provider = OAuthProviderConfig(
        slug=slug,
        name=body.name,
        enabled=body.enabled,
        client_id=body.client_id,
        client_secret=secret,
        issuer_url=body.issuer_url,
    )
    try:
        upsert_oauth_provider(db, provider)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    return _to_response(provider)


@oauth_router.delete("/{slug}", status_code=204)
def delete_provider(
    slug: str,
    _: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db_session),
) -> None:
    delete_oauth_provider(db, slug)


@oauth_router.post("/slug/derive")
def derive_slug(
    name: str,
    _: User = Depends(require_role(UserRole.ADMIN)),
) -> dict:
    slug = slugify(name)
    err = validate_slug(slug)
    if err is not None:
        return {"slug": slug, "valid": False, "error": err}
    return {"slug": slug, "valid": True}
```

- [ ] **Step 4: Register the router in `backend/app.py`**

Find where the existing routers are mounted under `/api`. Add:

```python
from backend.config_mgmt.oauth_routes import oauth_router
# ...
app.include_router(oauth_router, prefix="/api/admin")
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/test_oauth_routes.py -q`
Expected: 10 passed.

- [ ] **Step 6: Full suite + ruff**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check backend/ tests/"`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add backend/config_mgmt/oauth_routes.py tests/test_oauth_routes.py backend/app.py
git commit -m "feat(admin): structured OAuth provider CRUD endpoints

GET/PUT/DELETE /api/admin/oauth/providers[/{slug}] over the Phase 1
accessor. client_secret is never returned (always redacted to ***).
Empty client_secret on PUT means 'preserve existing'; dash means
'clear'. Slug validation is enforced via the existing _check_slug
helper. Slug-derive endpoint returns the auto-slugified name plus
validation result, used by the wizard / add-provider modal."
```

---

## Task 2: Backend SMTP CRUD routes

**Files:**
- Create: `backend/config_mgmt/smtp_routes.py`
- Create: `tests/test_smtp_routes.py`
- Modify: `backend/app.py`

### Steps

- [ ] **Step 1: Failing tests**

Create `tests/test_smtp_routes.py` with 7 tests covering:

1. `test_get_smtp_returns_404_when_unset` — GET on empty DB
2. `test_upsert_and_get_smtp` — PUT, then GET shows password redacted
3. `test_get_redacts_password` — password "REAL" never appears in response body
4. `test_upsert_blank_password_preserves_existing` — explicit empty password keeps previous value
5. `test_upsert_dash_password_clears` — `"-"` clears the stored password
6. `test_delete_smtp` — DELETE clears all `smtp.*` rows
7. `test_non_admin_forbidden` — non-admin gets 401/403 on every verb

Follow the same fixture pattern as Task 1's `tests/test_oauth_routes.py`. Mirror the password-secret behaviour from the OAuth tests (lines 4 and 5 above use the same "" / "-" semantics).

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Implement `backend/config_mgmt/smtp_routes.py`**

Use the same redaction + preserve-existing pattern. The Pydantic models:

```python
class SmtpResponse(BaseModel):
    host: str
    port: int
    username: str
    password: str        # always "***" or "" — never raw
    from_address: str
    use_tls: bool


class SmtpUpsert(BaseModel):
    host: str
    port: int
    username: str
    password: str        # "" = preserve; "-" = clear; else set
    from_address: str
    use_tls: bool
```

The handler is straightforward: `get_smtp_config(db)`, `upsert_smtp_config(db, ...)`, `clear_smtp_config(db)`. Same shape as Task 1's upsert (read existing → resolve password → upsert).

- [ ] **Step 4: Register router under `/api/admin`**

```python
from backend.config_mgmt.smtp_routes import smtp_router
app.include_router(smtp_router, prefix="/api/admin")
```

- [ ] **Step 5: Run tests**

Expected: 7 passed.

- [ ] **Step 6: Full suite + ruff**

- [ ] **Step 7: Commit**

```bash
git add backend/config_mgmt/smtp_routes.py tests/test_smtp_routes.py backend/app.py
git commit -m "feat(admin): structured SMTP CRUD endpoints

GET/PUT/DELETE /api/admin/smtp over the Phase 1 accessor. password
is never returned (always redacted to ***). Empty password on PUT
preserves the existing value; dash clears it. GET returns 404 when
SMTP isn't configured (matching get_smtp_config's None semantics)."
```

---

## Task 3: Backend test endpoints (OAuth round-trip + SMTP send)

The most subtle backend task — the OAuth test does a real round-trip with state-validated callback handling.

**Files:**
- Create: `backend/config_mgmt/test_routes.py`
- Create: `tests/test_admin_test_routes.py`
- Modify: `backend/app.py`

### Storage

In-memory dict `_TEST_SESSIONS: dict[str, _TestSession]` keyed by `state`. Cleaned up via:
- TTL check on every access (`expires_at` < now → discard)
- Successful callback marks `used=True` (still readable via the result endpoint until TTL expiry, then discarded)

```python
@dataclass
class _TestSession:
    test_session_id: str        # opaque id returned to frontend
    state: str                  # OAuth state parameter
    slug: str
    client_id: str
    client_secret: str
    issuer_url: str             # empty for non-OIDC
    expires_at: datetime
    status: str                 # "pending" | "success" | "failed"
    error: str | None = None
    identity: dict | None = None  # captured userinfo on success (display only)
```

`expires_at = now + 10 minutes`.

### URL contract

- `POST /api/admin/test/oauth/{slug}/start` body=`{client_id, client_secret, issuer_url, name}` → `{test_session_id, authorize_url}`
- `GET  /api/admin/test/oauth/callback?code=...&state=...` (no auth — state-validated) → HTML auto-close page
- `GET  /api/admin/test/oauth/{test_session_id}/result` → `{status, error?, identity?}` (admin auth)

The authorize URL is built using the SAME registry pattern as `resolve_provider` — fixed providers use their hardcoded endpoints; OIDC providers use the discovery URL via `_get_discovery`. Reuse the helpers; don't fork them.

### Tests (7)

1. `test_start_returns_authorize_url_with_state` — POST start, verify URL contains `state=`, `client_id=...`, and the correct authorize endpoint for the slug
2. `test_callback_with_unknown_state_returns_404` — GET callback with bad state
3. `test_callback_success_marks_session` — mock the token exchange + userinfo call, GET callback, then GET result returns `status="success"`
4. `test_callback_failure_marks_session` — token exchange fails (mock returns 400), result returns `status="failed"` with error message
5. `test_result_endpoint_admin_only` — non-admin gets 401/403
6. `test_session_expires_after_ttl` — manually fast-forward `expires_at`, callback returns 404
7. `test_session_starts_with_pending_status` — POST start, GET result immediately returns `status="pending"`

### SMTP test endpoint

`POST /api/admin/test/smtp` body=`{host, port, username, password, from_address, use_tls, to_address}` → `{ok: bool, error?: str}`

Implementation: build a `SmtpConfig` from the body, call a synchronous variant of the existing `_send_email_sync` from `backend/auth/email.py` with a short timeout. On failure, capture `str(exc)` and return `ok=False, error=...`. Run in a thread (`asyncio.to_thread`) so the request doesn't block the event loop.

If `password == ""`, look up the current stored password from `get_smtp_config(db)` and use that (so the admin doesn't have to re-enter on every test). If `password == "-"`, send without auth.

### Tests (3 SMTP)

1. `test_smtp_test_success` — mock `smtplib.SMTP` to succeed, expect `ok=True`
2. `test_smtp_test_connection_failure` — `smtplib.SMTP` raises, expect `ok=False` with error in body
3. `test_smtp_test_uses_stored_password_when_blank` — store a password via `upsert_smtp_config`, POST test with `password=""`, mock SMTP, assert the mocked SMTP `.login()` got the stored password

### Steps

- [ ] **Step 1: Tests**
- [ ] **Step 2: Confirm failure**
- [ ] **Step 3: Implement `backend/config_mgmt/test_routes.py`**
- [ ] **Step 4: Register `test_router` in `backend/app.py` under `/api/admin/test`** (NB: the prefix is `/test`, not `/admin/test`, because the parent prefix already includes `/admin` if you mount it that way — pick one and be consistent)
- [ ] **Step 5: Run tests**
- [ ] **Step 6: Full suite + ruff**
- [ ] **Step 7: Commit**

```bash
git add backend/config_mgmt/test_routes.py tests/test_admin_test_routes.py backend/app.py
git commit -m "feat(admin): OAuth round-trip + SMTP send test endpoints

POST /api/admin/test/oauth/{slug}/start kicks off a real OAuth flow
against unsaved credentials, stored in an in-memory _TEST_SESSIONS
dict keyed by state. The provider redirects to a single shared
callback (no auth — state-validated) which exchanges the code,
captures the result, and serves an auto-close HTML page that
postMessages back to the Config-page opener.

POST /api/admin/test/smtp opens an SMTP connection synchronously
with the supplied credentials and tries to send a small test
message to a caller-provided destination. Failures are captured
into a JSON error response rather than raising. Empty password
falls back to the stored value; '-' sends without auth."
```

---

## Task 4: Frontend Authentication group (provider list + modals + test sign-in)

No frontend tests exist. Replace TDD with manual verification — start `./run-dev.sh`, browse to `/config`, exercise the feature.

**Files:**
- Create: `frontend/src/api/oauth.ts`
- Create: `frontend/src/components/OAuthProviderList.tsx`
- Create: `frontend/src/components/OAuthTestButton.tsx`
- Create: `frontend/src/components/TestResultBanner.tsx`
- Modify: `frontend/src/pages/ConfigPage.tsx` (add the new group above the existing flat groups)

### `frontend/src/api/oauth.ts`

```typescript
export interface OAuthProvider {
  slug: string;
  name: string;
  enabled: boolean;
  client_id: string;
  client_secret: string;   // always "***" or "" from the server
  issuer_url: string;
}

export interface OAuthProviderUpsert {
  name: string;
  enabled: boolean;
  client_id: string;
  client_secret: string;   // "" = preserve; "-" = clear
  issuer_url: string;
}

export async function listOAuthProviders(): Promise<OAuthProvider[]> { ... }
export async function getOAuthProvider(slug: string): Promise<OAuthProvider> { ... }
export async function upsertOAuthProvider(slug: string, body: OAuthProviderUpsert): Promise<OAuthProvider> { ... }
export async function deleteOAuthProvider(slug: string): Promise<void> { ... }
export async function deriveSlug(name: string): Promise<{slug: string; valid: boolean; error?: string}> { ... }
export async function startOAuthTest(slug: string, body: {client_id: string; client_secret: string; issuer_url: string; name: string}): Promise<{test_session_id: string; authorize_url: string}> { ... }
export async function getOAuthTestResult(testSessionId: string): Promise<{status: "pending" | "success" | "failed"; error?: string; identity?: object}> { ... }
```

Use the same `fetch`-based helpers the existing `frontend/src/api/config.ts` uses; pattern-match its shape.

### `OAuthProviderList.tsx` shape

Top-level component takes `providers` + callbacks `onEdit`, `onDelete`, `onTest`, `onAdd`. Renders:

```
┌─────────────────────────────────────────────────────────────────┐
│ Authentication providers                                        │
│                                                                 │
│ ☑  Google                          [Edit] [Test] [Delete]      │
│ ☐  GitHub                          [Edit] [Test] [Delete]      │
│ ☑  PocketID (https://id.example.org)                            │
│                                    [Edit] [Test] [Delete]      │
│                                                                 │
│                                         [+ Add provider]        │
└─────────────────────────────────────────────────────────────────┘
```

The enabled checkbox is "live" — toggling it issues a PUT immediately (with `client_secret=""` to preserve). Add/Edit open a modal. Test triggers `<OAuthTestButton>`. Delete prompts confirmation.

### Add/Edit modal

Fields:
- Provider type (only for Add — Google / Microsoft / GitHub / Discord / Facebook / Custom OIDC). For Edit, immutable.
- Slug (only for Custom OIDC; auto-derived from "Display name" via debounced `deriveSlug`; greyed if Add for a fixed type)
- Display name
- Enabled
- Client ID
- Client Secret (placeholder "(unchanged)" on Edit; "(none)" on Add)
- Issuer URL (only for Custom OIDC)

Submit calls `upsertOAuthProvider`. On 400 with "slug" in the error, surface the message under the slug field. After successful submit, refresh the list.

### `OAuthTestButton.tsx`

```typescript
function OAuthTestButton({slug, formValues, ...}) {
  return (
    <button onClick={async () => {
      const {test_session_id, authorize_url} = await startOAuthTest(slug, formValues);
      const popup = window.open(authorize_url, "_blank", "width=600,height=700");

      // Listen for postMessage from the callback page
      const onMessage = (e: MessageEvent) => {
        if (e.data?.type === "oauth_test" && e.data.test_session_id === test_session_id) {
          window.removeEventListener("message", onMessage);
          // surface result via TestResultBanner state
        }
      };
      window.addEventListener("message", onMessage);

      // Fallback poll in case the popup got blocked or postMessage missed
      const poll = setInterval(async () => {
        if (popup?.closed) {
          const result = await getOAuthTestResult(test_session_id);
          if (result.status !== "pending") {
            clearInterval(poll);
            // surface result
          }
        }
      }, 1000);
    }}>
      Test sign-in
    </button>
  );
}
```

### `TestResultBanner.tsx`

Tiny green/red banner taking `{ok: boolean; message: string}`, auto-dismissing after 8s. Reused for SMTP test in Task 5.

### `ConfigPage.tsx` change

Above the existing groups in `GROUPS`, render the new "Authentication" group using `<OAuthProviderList>`. Keep the existing flat keys (`pat_mailbox_path`, scanner, claude, delivery) rendering through the same `ConfigFieldRow` mechanism.

### Manual verification (no automated tests)

- [ ] **Step 1: Start dev server**

```bash
./run-dev.sh
```

- [ ] **Step 2: Browser test plan**

1. Visit `/config` as admin. Confirm the new "Authentication" group renders above the existing groups. Provider list is initially empty.
2. Click "Add provider", pick Google, enter client_id `"test-cid"`, client_secret `"test-csec"`, leave Enabled checked, Save. Row appears in the list.
3. Refresh the page. Row persists. Open the Edit modal. Confirm client_secret shows placeholder `"(unchanged)"`, not the real value.
4. Toggle the row's enabled checkbox off. Refresh. State persists.
5. Click "Test sign-in". A popup opens at Google's OAuth URL. (You'll need a real OAuth app for an end-to-end success; failure mode also tests the path — provide a fake client_id and confirm the failure banner appears.)
6. Click "Delete", confirm prompt. Row disappears. Refresh confirms.
7. Click "Add provider" → "Custom OIDC". Enter display name "PocketID Test". Confirm slug auto-fills to `pocketid-test` and accepts. Save. Row appears with the issuer URL.
8. Try to add a second provider with slug `google` from the "Custom OIDC" path. Confirm the slug field shows the "reserved" error and Save is disabled.

- [ ] **Step 3: Final ruff (frontend has no lint hook beyond `npm run build` typecheck)**

```bash
cd frontend && nix-shell -p nodejs_22 --run "npm run build"
```

Expected: typecheck passes.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/
git commit -m "feat(frontend): Authentication group on /config (CRUD + test sign-in)

Renders configured OAuth providers as rows on the Config page with
edit / delete / test / live enabled-toggle. Add modal lets the admin
configure a fixed provider (Google / Microsoft / GitHub / Discord /
Facebook) or a custom OIDC provider with auto-slugified name. Test
sign-in opens a popup at the provider's authorize URL and listens
for postMessage from the test callback; falls back to polling the
result endpoint if the message is missed. Secrets are never read
back from the server."
```

---

## Task 5: Frontend Email (SMTP) group + send-test modal

**Files:**
- Create: `frontend/src/api/smtp.ts`
- Create: `frontend/src/components/SmtpForm.tsx`
- Modify: `frontend/src/pages/ConfigPage.tsx`

### `frontend/src/api/smtp.ts`

```typescript
export interface SmtpConfig {
  host: string;
  port: number;
  username: string;
  password: string;     // "***" or "" from server
  from_address: string;
  use_tls: boolean;
}

export interface SmtpUpsert extends Omit<SmtpConfig, "password"> {
  password: string;     // "" = preserve; "-" = clear; else set
}

export async function getSmtp(): Promise<SmtpConfig | null> { ... }   // returns null on 404
export async function upsertSmtp(body: SmtpUpsert): Promise<SmtpConfig> { ... }
export async function clearSmtp(): Promise<void> { ... }
export async function sendSmtpTest(body: SmtpUpsert & {to_address: string}): Promise<{ok: boolean; error?: string}> { ... }
```

### `SmtpForm.tsx` shape

```
┌──────────────────────────────────────────────────────────────┐
│ Email (SMTP)                                                 │
│                                                              │
│  Host                  [ smtp.gmail.com           ]          │
│  Port                  [ 587                       ]          │
│  Username              [ alice@example.org         ]          │
│  Password              [ ************              ] [Show]  │
│  From address          [ alice@example.org         ]          │
│  Use TLS               [ x ] (checkbox)                       │
│                                                              │
│            [Send test email]   [Save]   [Clear]              │
└──────────────────────────────────────────────────────────────┘
```

Save button is disabled when no field differs from the saved value. Clear is a destructive action with confirmation. "Send test email" opens a small modal asking only for the destination address; on submit, calls `sendSmtpTest` with the *current form values* (not last-saved) and shows the result in a `TestResultBanner`.

### ConfigPage.tsx change

Below the new "Authentication" group, render the "Email" group as `<SmtpForm>`. Other groups unchanged.

### Manual verification

- [ ] **Step 1: Dev server, browser test plan**

1. Visit `/config`. Confirm "Email (SMTP)" group renders below Authentication.
2. Enter host/port/from/etc. Save. Refresh — state persists.
3. Refresh, confirm password input shows placeholder text rather than the real value.
4. Edit just the port, click "Save". Confirm password is preserved (server-side check via DB if you want to be sure).
5. Click "Send test email" with the form's current values. Modal asks for destination. Enter a real address you control. Submit. On success, green banner. On failure, red banner with the SMTP error.
6. Click "Clear" → confirm prompt. SMTP settings disappear; refresh confirms.
7. Verify the existing transactional emails (e.g. trigger a new-user registration in another browser session) still work after a fresh SMTP setup via the Config page.

- [ ] **Step 2: Build**

```bash
cd frontend && nix-shell -p nodejs_22 --run "npm run build"
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/
git commit -m "feat(frontend): Email group on /config (CRUD + send test email)

SMTP settings now editable via a structured form on the Config page,
with a 'Send test email' modal that exercises the unsaved values
end-to-end (real SMTP connection, real send) and surfaces success
or the underlying error inline. Password input shows a placeholder
rather than the real value; empty password on save preserves the
stored secret."
```

---

## Out of scope (handled in later phases)

- The setup wizard SPA at `/setup` and the redirect middleware — **Phase 3**. Phase 3 will reuse the same `<OAuthProviderList>` / `<SmtpForm>` components built here, pinned to "exactly one provider must exist and be tested before Next is enabled" mode.
- The recovery CLI and `/recovery` route — **Phase 4**.
- Removing the `client_id` / `client_secret` / etc. fields from Pydantic `Settings` and the `_gather_oidc_providers` env-scanning — **Phase 5**.

Phase 2b's success criterion: an admin can configure OAuth providers and SMTP entirely via the browser, can test both end-to-end before saving, and can recover from a misconfigured save by either fixing it via the same UI or (per Phase 4, future) by the recovery CLI.
