# Config Unification — Phase 3: First-Boot Setup Wizard

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the AppConfig sentinel `setup_completed` is absent, force the running app into a four-step web wizard that collects net basics, one OAuth provider, optional SMTP, and an OAuth-claim sign-in that becomes the first admin user. On step 4 success, everything commits atomically. Existing deployments are unaffected (Phase 2a's migration already set `setup_completed=true` for them).

**Architecture:**

- **Backend** — new router `setup_router` at `/api/setup`. Three endpoints:
  - `GET /api/setup/status` → `{setup_completed: bool}` (always reachable)
  - `POST /api/setup/claim/start` → captures wizard inputs in an in-memory `_SETUP_SESSIONS[state]` dict (TTL 30 min), returns `{authorize_url}`
  - `GET /api/setup/claim/callback` → state-validated OAuth callback. On success, commits wizard inputs to AppConfig, creates first admin user, sets `setup_completed=true`, issues JWT cookie, redirects to `/`
- All other `/api/setup/*` endpoints (start/callback) return 410 Gone once `setup_completed=true`. `status` keeps responding so the SPA can detect completion.
- **Frontend** — new `SetupPage.tsx` with four steps managed by local React state (no router navigation between steps; one component, step number in state). `App.tsx` gains a top-level `<SetupGate>` that calls `/api/setup/status` on mount and renders `<SetupPage>` if `setup_completed=false`; otherwise renders the existing router.
- **Atomicity** — the callback's writes (oauth provider, smtp, net config keys, user row, `setup_completed`) all happen in a single SQLAlchemy session that commits once at the end. Phase 1's accessor helpers commit internally; in this context that's acceptable because failure between commits would leave a half-configured state, but the next wizard run is idempotent (each row is overwritten with the same wizard inputs).

**Tech Stack:** Same as prior phases — Python 3.12, FastAPI, SQLAlchemy 2.x, React + TypeScript + Vite.

**Spec:** `docs/superpowers/specs/2026-06-12-config-unification-design.md`

---

## URL surface

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| GET    | `/api/setup/status`       | none | Always responds. `{setup_completed: bool}`. |
| POST   | `/api/setup/claim/start`  | none (only valid when `setup_completed=false`) | Body: wizard inputs. Returns `{claim_session_id, authorize_url}`. 410 if setup already done. |
| GET    | `/api/setup/claim/callback` | none (state-validated) | OAuth provider redirect target. Commits everything, issues JWT, redirects to `/`. 410 if setup already done. |

---

## File structure

**New backend files:**

| Path | Responsibility |
|------|----------------|
| `backend/config_mgmt/setup_routes.py` | `setup_router` + `_SETUP_SESSIONS` in-memory store + claim/commit logic. |
| `tests/test_setup_routes.py` | Tests for status, claim/start, claim/callback (mock OAuth provider), 410 after completion, atomicity. |

**Modified backend files:**

| Path | Change |
|------|--------|
| `backend/app.py` | Mount `setup_router` at `/api/setup`. |

**New frontend files:**

| Path | Responsibility |
|------|----------------|
| `frontend/src/api/setup.ts` | Typed client: `getSetupStatus`, `startSetupClaim`. |
| `frontend/src/pages/SetupPage.tsx` | 4-step wizard SPA. |
| `frontend/src/components/SetupGate.tsx` | Top-level gate that renders the wizard or the router. |

**Modified frontend files:**

| Path | Change |
|------|--------|
| `frontend/src/App.tsx` | Wrap the existing `<Routes>` in `<SetupGate>`. |

---

## Task 1: Backend setup router + claim flow

**Files:**
- Create: `backend/config_mgmt/setup_routes.py`
- Create: `tests/test_setup_routes.py`
- Modify: `backend/app.py`

### Storage shape

```python
from dataclasses import dataclass, field

@dataclass
class _SetupSession:
    state: str
    # Step 1: net basics
    default_net_control: str        # e.g. "W0NE"
    net_address: str                # e.g. "w0ne@winlink.org"
    app_base_url: str
    # Step 2: oauth provider (exactly one)
    oauth_slug: str                 # one of FIXED_PROVIDERS or a custom OIDC slug
    oauth_name: str
    oauth_client_id: str
    oauth_client_secret: str = field(repr=False)
    oauth_issuer_url: str           # empty for non-OIDC
    # Step 3: smtp (optional)
    smtp: SmtpConfig | None
    # Bookkeeping
    expires_at: datetime

_SETUP_SESSIONS: dict[str, _SetupSession] = {}    # keyed by `state`
_SESSION_TTL = timedelta(minutes=30)               # wizard could take a while
```

### Endpoint specs

**`GET /api/setup/status`** — always returns `{setup_completed: bool}`. Reads `is_setup_completed(db)` directly; no caching. (Hot path: called on every SPA mount but cheap.)

**`POST /api/setup/claim/start`** — returns 410 if already complete; otherwise:

Body (Pydantic):
```python
class SetupClaimStart(BaseModel):
    default_net_control: str
    net_address: str
    app_base_url: str
    oauth_slug: str
    oauth_name: str
    oauth_client_id: str
    oauth_client_secret: str
    oauth_issuer_url: str = ""
    smtp: SmtpUpsert | None = None    # reuse the Pydantic model from smtp_routes
```

Validation:
- `_check_slug(oauth_slug)` (use the Phase 1 helper)
- `default_net_control` non-empty
- `net_address` non-empty
- `app_base_url` non-empty
- `oauth_client_id` non-empty
- `oauth_client_secret` non-empty (no preserve-existing semantics here — there's nothing to preserve)
- For custom OIDC slugs (not in `FIXED_PROVIDERS`): `oauth_issuer_url` non-empty

Mint `state = secrets.token_urlsafe(32)`. Store all inputs in `_SETUP_SESSIONS[state]`. Build authorize URL using the same logic as Phase 2b's `start_oauth_test` (FIXED_PROVIDERS / `_get_discovery`). Redirect URI is `{app_base_url}/api/setup/claim/callback` (so OAuth providers always send the user back to the right host, even behind a reverse proxy). Return `{authorize_url}`.

**`GET /api/setup/claim/callback`** — returns 410 if already complete. Otherwise:

- Look up `_SETUP_SESSIONS[state]`; 404 if missing or expired.
- If the provider sent `?error=...`: serve an HTML page that displays the error and a "Try again" link back to the wizard.
- Otherwise: exchange code, fetch userinfo, extract OIDC sub / email / name (or provider-specific extractors — reuse `FIXED_PROVIDERS[slug].extract_*` and the OIDC defaults from `backend/auth/providers.py`).
- On token-exchange or userinfo failure: serve the error HTML.
- On success:
  - Write all wizard inputs to AppConfig via the Phase 1 helpers (`upsert_oauth_provider`, `upsert_smtp_config` if `session.smtp is not None`, `set_config_value` for `net_address` / `default_net_control` / `app_base_url`).
  - Create the first user with `callsign=session.default_net_control`, `name=extracted_name`, `email=extracted_email`, `oidc_subject=f"{slug}:{extracted_sub}"`, `role=UserRole.ADMIN`.
  - `mark_setup_completed(db)`.
  - Issue a JWT cookie via `create_access_token(callsign, "admin", app_settings)` set as `access_token`.
  - Delete `_SETUP_SESSIONS[state]` (single-use).
  - Redirect (302) to `/`.

### Tests (10)

Mock the OAuth provider's token + userinfo endpoints with `unittest.mock.patch` over `httpx.AsyncClient`. Each test uses an `autouse` fixture clearing `_SETUP_SESSIONS` between tests.

1. `test_status_returns_false_when_unset` — GET status, empty DB → `{"setup_completed": False}`.
2. `test_status_returns_true_after_completion` — pre-populate `setup_completed=true` row → `{"setup_completed": True}`.
3. `test_claim_start_returns_authorize_url_with_state` — POST start with full wizard inputs → 200, `authorize_url` contains the state and client_id.
4. `test_claim_start_410_after_setup_complete` — mark setup complete, POST start → 410.
5. `test_claim_start_rejects_invalid_slug` — POST with `oauth_slug="Bad Slug!"` → 400.
6. `test_claim_start_rejects_blank_secret` — POST with `oauth_client_secret=""` → 400 (this is create-only; no preserve semantics).
7. `test_claim_callback_creates_admin_and_marks_complete` — full happy path: start → callback (mocked) → first User exists with role=admin and the wizard callsign, all AppConfig rows present, `setup_completed=true`, JWT cookie set.
8. `test_claim_callback_410_after_setup_complete` — mark complete, GET callback → 410.
9. `test_claim_callback_unknown_state_returns_404` — GET callback with bad state → 404.
10. `test_claim_callback_token_exchange_failure_does_not_commit` — mock token exchange returns no access_token. AppConfig must NOT contain `setup_completed` after; no admin user created.

### Steps

- [ ] **Step 1: Write the failing tests.** Pattern-match `tests/test_admin_test_routes.py` for fixtures and OAuth mocking. The `test_app` fixture must include `setup_router` and (for callback tests) `auth_router` so `create_access_token` works.

- [ ] **Step 2: Run to confirm failure.** `.venv/bin/pytest tests/test_setup_routes.py -q` → import error.

- [ ] **Step 3: Implement `backend/config_mgmt/setup_routes.py`.** Reuse Phase 2b's `_get_discovery` from `backend/auth/service`, `FIXED_PROVIDERS` from `backend/auth/providers`, and the Phase 1 accessors. The atomic-commit body of the callback:

  ```python
  # Inside the success branch of oauth_test_callback:
  upsert_oauth_provider(db, OAuthProviderConfig(
      slug=session.oauth_slug,
      name=session.oauth_name,
      enabled=True,
      client_id=session.oauth_client_id,
      client_secret=session.oauth_client_secret,
      issuer_url=session.oauth_issuer_url,
  ))
  if session.smtp is not None:
      upsert_smtp_config(db, session.smtp)
  set_config_value(db, "default_net_control", session.default_net_control)
  set_config_value(db, "net_address", session.net_address)
  set_config_value(db, "app_base_url", session.app_base_url)
  user = User(
      callsign=session.default_net_control,
      oidc_subject=f"{session.oauth_slug}:{extracted_sub}",
      name=extracted_name,
      email=extracted_email,
      role=UserRole.ADMIN,
  )
  db.add(user)
  db.commit()
  mark_setup_completed(db)
  ```

  (The Phase 1 helpers commit internally, but they're write-only and idempotent — re-running the callback after a partial failure overwrites with the same values, so the only correctness concern is the User row, which `db.add` + `db.commit` together handles.)

- [ ] **Step 4: Register router in `backend/app.py`:** `app.include_router(setup_router, prefix="/api/setup")`.

- [ ] **Step 5: Run tests.** Expect 10 passed.

- [ ] **Step 6: Full suite + ruff.**

- [ ] **Step 7: Commit.**

```bash
git add backend/config_mgmt/setup_routes.py tests/test_setup_routes.py backend/app.py
git commit -m "feat(setup): first-boot wizard backend (status + claim/start + claim/callback)

Three endpoints under /api/setup that gate the rest of the app when
setup_completed is absent:

- GET /api/setup/status — public, returns {setup_completed: bool}.
- POST /api/setup/claim/start — captures wizard inputs in an
  in-memory _SETUP_SESSIONS dict keyed by OAuth state, returns the
  provider's authorize URL. 410 once setup is complete.
- GET /api/setup/claim/callback — state-validated, no auth. On
  successful OAuth round-trip, writes the wizard inputs to
  app_config via the Phase 1 accessors, creates the first user with
  role=admin, sets setup_completed=true, issues an access_token JWT
  cookie, and redirects to /. 410 once setup is complete.

The session store and TTL mirror Phase 2b's _TEST_SESSIONS pattern
(process-local; single-worker deployment assumed)."
```

---

## Task 2: Frontend wizard SPA + setup gate

**Files:**
- Create: `frontend/src/api/setup.ts`
- Create: `frontend/src/pages/SetupPage.tsx`
- Create: `frontend/src/components/SetupGate.tsx`
- Modify: `frontend/src/App.tsx`

No automated tests — manual browser verification.

### `frontend/src/api/setup.ts`

```typescript
import { apiFetch } from "./client";
import type { SmtpUpsert } from "./smtp";

export interface SetupStatus {
  setup_completed: boolean;
}

export interface SetupClaimStart {
  default_net_control: string;
  net_address: string;
  app_base_url: string;
  oauth_slug: string;
  oauth_name: string;
  oauth_client_id: string;
  oauth_client_secret: string;
  oauth_issuer_url: string;
  smtp: SmtpUpsert | null;
}

export interface SetupClaimStartResponse {
  authorize_url: string;
}

export function getSetupStatus(): Promise<SetupStatus> {
  return apiFetch<SetupStatus>("/setup/status");
}

export function startSetupClaim(body: SetupClaimStart): Promise<SetupClaimStartResponse> {
  return apiFetch<SetupClaimStartResponse>("/setup/claim/start", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
```

### `frontend/src/components/SetupGate.tsx`

```typescript
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { getSetupStatus } from "../api/setup";
import { SetupPage } from "../pages/SetupPage";
import { Spinner } from "./Spinner";

export function SetupGate({ children }: { children: ReactNode }) {
  const [setupCompleted, setSetupCompleted] = useState<boolean | null>(null);

  useEffect(() => {
    getSetupStatus()
      .then((s) => setSetupCompleted(s.setup_completed))
      .catch(() => setSetupCompleted(true));   // fail-open: if /setup/status itself fails, fall through to the normal router so errors surface there
  }, []);

  if (setupCompleted === null) {
    return <div className="flex justify-center py-12"><Spinner /></div>;
  }
  if (!setupCompleted) {
    return <SetupPage />;
  }
  return <>{children}</>;
}
```

### `frontend/src/App.tsx`

Wrap the existing `<Routes>` block with `<SetupGate>`:

```typescript
<SetupGate>
  <Routes>
    ...
  </Routes>
</SetupGate>
```

The `LoginPage` still works as before once setup is complete; the setup gate just ensures that before completion, no route ever renders.

### `frontend/src/pages/SetupPage.tsx`

Single React component with `step: 1 | 2 | 3 | 4` in state and a single `formState` object that accumulates wizard inputs across steps. Renders different content per step.

**Step 1 — Net basics:**
- Inputs: callsign, net address, app base URL (pre-filled from `window.location.origin`)
- Validation: all three non-empty
- "Next" advances to step 2

**Step 2 — OAuth provider:**
- Provider type radio: Google / Microsoft / GitHub / Discord / Facebook / Custom OIDC
- For Custom OIDC: name + slug (auto-derived via `deriveSlug` from Phase 2b's `api/oauth.ts`) + issuer URL
- Inputs: client_id, client_secret, name (auto-filled from type for fixed providers)
- "Test sign-in" button — reuse the **mechanism** from Phase 2b's `OAuthTestButton`, but parametrise it so the unsaved values come from the wizard's `formState`, not from any saved provider. Test must succeed before "Next" is enabled.
- After test success: store a `oauth_tested: true` flag in `formState`. Editing any OAuth field clears the flag.

**Step 3 — SMTP (skippable):**
- Inputs: host, port, username, password, from_address, use_tls
- "Send test email" — reuse Phase 2b's pattern; surface result via `<TestResultBanner>`
- "Skip" sets `formState.smtp = null` and advances
- "Next" advances with the SMTP block

**Step 4 — Claim admin:**
- Display summary of what's about to happen
- One button: "Sign in with [provider] and finish setup"
- On click: POST `/api/setup/claim/start` with the full `formState` → receive `authorize_url` → `window.location.href = authorize_url`
- The OAuth callback at `/api/setup/claim/callback` does the atomic commit and redirects to `/` with a session cookie set
- Once back at `/`, the SetupGate re-checks `/api/setup/status` (mounted in `useEffect`) and now renders the app normally

**Visual style:** card-based, matches the existing `OAuthProviderList` and `SmtpForm` aesthetic. Top of the page shows a small step indicator ("Step 2 of 4"). "Back" button on steps 2-4. No router navigation between steps; just state changes.

### Manual verification

- [ ] **Step 1: Start dev server with a fresh DB.**

```bash
rm -f skynetcontrol.db          # or wherever stateDir points
./run-dev.sh
```

- [ ] **Step 2: Browser test plan.**

1. Open the app in a fresh browser session. Confirm `/` redirects (visually) to the setup wizard.
2. Fill step 1 (callsign + net address + base URL pre-filled). "Next" advances.
3. Step 2: pick Google, enter (or use a throwaway test) client_id/secret. Click "Test sign-in" — popup opens, completes OAuth, postMessages back. "Next" is enabled.
4. Step 3: enter SMTP, click "Send test email" with your own address, get a real email. "Next" advances. (Alternatively, "Skip" and confirm wizard still advances.)
5. Step 4: click "Sign in with Google and finish setup". OAuth flow → redirected to `/` with a session cookie. App is now usable.
6. Refresh `/`. App loads normally (no wizard). Visit `/api/setup/status` → `{setup_completed: true}`. Try POSTing `/api/setup/claim/start` → 410.
7. Visit `/config`. The just-configured Google provider is in the Authentication group; the just-configured SMTP is in the Email group.

- [ ] **Step 3: Frontend build.**

```bash
cd frontend && nix-shell -p nodejs_22 --run "npm run build"
```

- [ ] **Step 4: Commit.**

```bash
git add frontend/src/api/setup.ts frontend/src/pages/SetupPage.tsx \
        frontend/src/components/SetupGate.tsx frontend/src/App.tsx
git commit -m "feat(frontend): first-boot setup wizard SPA

A four-step React component at /setup that runs before any route is
served when /api/setup/status reports setup_completed=false:

1. Net basics (callsign, net address, base URL).
2. OAuth provider (one, with test-sign-in gating Next).
3. SMTP (skippable, with send-test).
4. Claim admin — POST /api/setup/claim/start, redirect to the
   provider's authorize URL, OAuth callback atomically commits
   everything server-side and lands the new admin on /.

SetupGate wraps the existing Routes block; once setup_completed
flips to true (refresh after the OAuth callback completes), the
normal app loads. Reuses Phase 2b's test-sign-in popup and
send-test-email patterns."
```

---

## Out of scope (handled in later phases)

- Recovery CLI + `/recovery` route + recovery-mode wizard — **Phase 4**. The recovery cookie will lift the setup_completed gate so the wizard re-renders in edit-existing mode against the current AppConfig.
- Removing OAuth / SMTP fields from `Settings`, the `_gather_oidc_providers` env-scanner, the `module.nix.settings` attrset — **Phase 5**. Phase 3's wizard works regardless because it reads/writes only AppConfig.

**Phase 3 success criterion:** booting a fresh deployment (empty `app_config` table) lands at the wizard. After completing the four steps, the new admin is logged in, all AppConfig rows are populated, and the app is fully usable.
