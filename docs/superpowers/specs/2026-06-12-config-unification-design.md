# Config unification: DB-backed settings + first-boot wizard

## Problem

Configuration today is split inconsistently across two systems:

- **Pydantic `Settings`** (env vars, `backend/config.py`) holds `database_url`, JWT
  parameters, OAuth provider credentials (Google/Microsoft/GitHub/Discord/Facebook +
  a custom OIDC scanner), and SMTP settings.
- **AppConfig DB table** (`backend/config_mgmt/`) holds `net_address`,
  `default_net_control`, `pat_mailbox_path`, scanner toggles, delivery routing,
  callbook credentials, `claude_api_key`, and `checkins.modes`.

The split is principled by neither owner nor volatility. API keys are split across
both layers with no rule (`SMTP_PASSWORD` in env, `claude_api_key` in DB,
`delivery.groupsio.api_key` in DB). Filesystem paths sit in DB despite being host
infrastructure (`pat_mailbox_path`). Operational toggles (`scanner.enabled`) sit
in DB but are read once at startup, so toggling them via UI lies until the next
restart.

The recent `get_config_value` env-fallback was a band-aid that papered over this
without resolving it.

## Goals

- Every configuration value lives in exactly one place.
- The env surface area collapses to what genuinely cannot live in the database:
  `SKYNET_DATABASE_URL`, `SKYNET_JWT_SECRET_KEY`, `SKYNET_APP_BASE_URL`,
  `SKYNET_DEBUG`, `SKYNET_STATIC_DIR`.
- A first-boot web wizard collects everything else interactively. No CLI wizard,
  no "headless" path — the browser is the only management surface.
- Auth and SMTP settings re-read from DB on every use, so admins editing them
  via `/config` see changes take effect without restarting the process.
- A break-glass recovery CLI exists so a fat-fingered OAuth edit doesn't strand
  the operator.

## Non-goals

- Hot-reload of process-level settings (`debug`, `database_url`). These remain
  startup-only.
- Multi-tenant config. One AppConfig table = one net.
- Backwards compatibility with the current `services.skynetcontrol.settings`
  attrset of env vars for OAuth/SMTP. These get removed; the migration is a
  one-time wizard re-run.

## Architectural shape

```
env (immutable, required before app boots)
  SKYNET_DATABASE_URL
  SKYNET_JWT_SECRET_KEY
  SKYNET_APP_BASE_URL          (for OAuth redirect URIs)
  SKYNET_DEBUG
  SKYNET_STATIC_DIR
                  │
                  ▼
AppConfig DB table (mutable, edited via wizard / /config)
  oauth.<slug>.{enabled, name, client_id, client_secret, issuer_url}
  smtp.{host, port, username, password, from_address, use_tls}
  net_address, default_net_control, pat_mailbox_path,
  scanner.enabled, scanner.interval_minutes,
  delivery.*, callbook.*, claude_api_key, checkins.modes,
  setup_completed                                 (sentinel)
                  │
        lazy-read on every use, no caching
                  ▼
  get_oauth_provider(db, slug)
  get_smtp_config(db)
  get_config_value(db, key)
```

`backend/config.py`'s `Settings` shrinks to the env block above. The Pydantic
nested models (`ProviderSettings`, `OIDCProviderConfig`, `SmtpSettings`) and the
`_gather_oidc_providers` env-scanning validator are removed.

## Setup wizard flow

**Entry.** Middleware checks for `setup_completed` in `app_config` on every
request. If absent, every non-`/setup` route 302s to `/setup`. The wizard SPA is
the only thing the unauthenticated server serves until setup finishes.

**Step 1 — Net basics.** Net callsign (`default_net_control`), net Winlink
address (`net_address`), app base URL (`app_base_url`, pre-filled from the
request origin and sanity-checked against it). No verification needed.

**Step 2 — OAuth provider.** Dropdown picker (Google / Microsoft / GitHub /
generic OIDC). Inputs: `client_id`, `client_secret` (plus `issuer_url` + display
name for OIDC). "Test sign-in" button performs a real OAuth round-trip against
the unsaved values in a popup window; reports success or failure. The "Next"
button stays disabled until Test succeeds.

**Step 3 — SMTP (skippable).** Inputs: host, port, username, password, from,
use_tls. "Send test email" button prompts for a destination, fires using
unsaved values, shows the result. Skipping disables admin notification emails
(new-registration / callsign-change / approval pings); no other consequence.

**Step 4 — Claim admin.** Single button: "Sign in with [provider] and finish
setup." Kicks off an OAuth flow against step 2's values. On callback success,
atomically:

- Write all wizard inputs to `app_config`.
- Create the first user (callsign from step 1, identity from OAuth claims),
  role = ADMIN.
- Set `setup_completed = true`.
- Redirect to the dashboard.

### Invariants

1. **Nothing writes to `app_config` until step 4 succeeds.** Wizard state lives
   in the browser (or a short-lived signed cookie). A half-finished wizard is
   just a closed tab.
2. **`/setup/*` returns 410 Gone once `setup_completed = true`.** Ongoing edits
   happen through `/config`.
3. **Only OAuth and SMTP have verify-then-commit semantics.** Net basics are
   trivially valid; delivery / callbook / PAT / scanner are configured later via
   `/config`. The principle is "get the system bootable first, configure
   niceties later."

## Config page after setup

The existing `/config` page becomes the editing surface for everything that
moved out of env. Same React component, new groups:

- **Authentication.** List of configured OAuth providers, each rendered as a
  row with: enabled toggle, display name, "Edit" button (opens modal with the
  provider's fields), "Test sign-in" button, "Delete" button. "Add provider"
  control at the bottom opens a type picker (Google / Microsoft / GitHub /
  generic OIDC) then a form. For generic OIDC, the slug is auto-derived from
  the display name with a manually-overridable input; once saved, the slug is
  immutable (changing it would orphan any user identities tied to it). The
  same component is reused inside the wizard's step 2, pinned there to
  "exactly one provider must be added and tested before Next is enabled."
- **Email (SMTP).** Host, port, username, password, from, use_tls, plus a
  "Send test email" button.

### Test buttons

Anywhere a wrong value silently breaks something, add a verification action:

- OAuth provider rows → "Test sign-in" (popup, real OAuth round-trip).
- SMTP → "Send test email" (modal asks for destination).
- Claude API key → "Test key" (single-token completion).
- Callbook creds → "Test lookup" (single canned lookup).

### Save semantics

"Save" commits the current row to the DB immediately. Test buttons operate on
the *current form values* (not last-saved) so admins can edit, test, then save.
No staged/pending state machine.

The lockout failure mode (saving broken OAuth) is mitigated by (a) the test
button living right next to Save, and (b) the recovery CLI existing.

## NixOS module changes

`module.nix` collapses to:

```nix
options.services.skynetcontrol = {
  enable        = mkEnableOption "SkyNetControl";
  host          = mkOption { default = "127.0.0.1"; ... };
  port          = mkOption { default = 8000; ... };
  stateDir      = mkOption { default = "/var/lib/skynetcontrol"; ... };
  databaseUrl   = mkOption { default = "sqlite:///${cfg.stateDir}/skynetcontrol.db"; ... };
  jwtSecretFile = mkOption { type = path; ... };  # new — replaces putting it in `settings`
};
```

**Removed:** `settings` attrset. Its current consumers (OAuth client IDs, SMTP
host/port/user, OIDC name/issuer/client-id triples, `PAT_MAILBOX_PATH`) all move
to the wizard / DB. Anyone who wants `SKYNET_DEBUG=true` can use
`systemd.services.skynetcontrol.environment` directly.

**Service unit:** unchanged from the recent stateDir work — static
`skynetcontrol` user, `WorkingDirectory = cfg.stateDir`, hardening preserved.

The user-facing NixOS config shrinks from ~20 lines of `settings.AUTH_*` /
`SMTP__*` / `PAT_MAILBOX_PATH` to roughly:

```nix
services.skynetcontrol = {
  enable        = true;
  host          = "127.0.0.1";
  port          = 8040;
  stateDir      = "/storage/skynetcontrol";
  jwtSecretFile = config.age.secrets.skynetcontrol-jwt.path;
};
users.users.skynetcontrol.extraGroups = [ "pat" ];
```

## Recovery CLI

A new wrapped entry point `skynetcontrol-recovery` (sibling to
`skynetcontrol-server` / `skynetcontrol-alembic`). Reads `SKYNET_DATABASE_URL`.

### Subcommands

- `mint-admin-token [--ttl 10m]`
  - Generates a random 32-byte URL-safe token.
  - Stores `(token_hash, expires_at, used_at=null)` in a new
    `admin_recovery_tokens` table.
  - Prints the plaintext token + claim URL
    (`${app_base_url}/recovery?token=…`) to stdout. Token shown once.
- `list-tokens` — outstanding (unused + unexpired) tokens with expiry. No
  plaintext.
- `revoke <prefix>` — match by hash prefix, mark used.

### `/recovery` route lifecycle

- Returns 404 unless at least one unused + unexpired token exists in
  `admin_recovery_tokens`.
- Otherwise serves a token-entry form.

### Claim flow

1. User pastes token → `POST /recovery/claim`.
2. Server hashes, matches, validates not-used + not-expired.
3. Marks token used (single-shot).
4. Sets a short-lived recovery session cookie (30 min, `HttpOnly`, `Secure`,
   `SameSite=Lax`).
5. Redirects to `/setup`.

The recovery cookie is a JWT signed with `SKYNET_JWT_SECRET_KEY` (the same
signer that issues user session tokens) carrying a distinct claim type:

```json
{ "type": "recovery", "token_hash_prefix": "abc12345", "exp": <now+30m> }
```

A middleware reads this claim and admits requests to `/setup/*` and
`/admin/test/*` only — nothing else. No per-request DB lookup is needed because
the cookie self-validates; single-use enforcement happens at claim time on the
underlying `admin_recovery_tokens` row. If the cookie leaks within its 30-min
window an attacker can edit config but cannot reach member data, check-ins, or
any other resource; every save is tagged in the audit log with the token-hash
prefix.

### Recovery-mode wizard

The recovery cookie lifts the `setup_completed` gate for `/setup`. The wizard
SPA re-runs in **edit-existing mode**:

- Pre-fills every step from current AppConfig values.
- Save is per-step rather than atomic at the end — admin can fix just one
  thing and leave.
- The final step becomes "Sign in with [provider]" — verifies the (possibly
  just-fixed) OAuth works against the existing admin account, expires the
  recovery cookie, lands them in the normal app. If only SMTP was fixed and
  OAuth was always fine, "Done" expires the cookie without a re-auth round.

### Audit + scope

- Every save during a recovery session is tagged `actor = recovery:<hash-prefix>`
  in the audit log.
- No user creation. No promotion of any existing user. No access to anything
  outside the wizard and the test endpoints.

### Schema

```python
class AdminRecoveryToken(Base):
    __tablename__ = "admin_recovery_tokens"
    id          : int           # pk
    token_hash  : str           # sha256, indexed
    expires_at  : datetime
    used_at     : datetime | None
    created_at  : datetime
```

## Phasing

Implementation lands as a sequence of bisectable phases, each leaving `main` in
a working state. Every phase gets its own implementation plan under
`docs/superpowers/plans/`.

**Phase 1 — DB-backed Settings layer.** Add `get_oauth_provider(db, slug)`,
`get_smtp_config(db)`, etc. as new accessors over the existing AppConfig
table. No callers change. Introduce the `setup_completed` sentinel concept
(no UI wired). *Implemented per
`docs/superpowers/plans/2026-06-12-config-unification-phase-1-db-backed-settings-layer.md`.*

**Phase 2 — Move OAuth + SMTP read paths to DB.** Switch `init_providers` and
the email backend from `Settings` to the new DB accessors. An Alembic data
migration on upgrade imports current env values into AppConfig and sets
`setup_completed = true`, so existing deployments transparently migrate. The
Config page gains "Authentication" and "Email" groups with test buttons.

The Phase 2 data migration runs once (Alembic tracks the revision) and is
idempotent against re-runs:

```
if any AppConfig key starting with "oauth." or "smtp." exists:
    return                              # already migrated

# Scan os.environ for the current env-var patterns:
#   SKYNET_AUTH_<GOOGLE|MICROSOFT|GITHUB>__<ENABLED|CLIENT_ID|CLIENT_SECRET>
#   SKYNET_AUTH_OIDC_<MIDDLE>_<NAME|ENABLED|CLIENT_ID|CLIENT_SECRET|ISSUER_URL>
#   SKYNET_SMTP__<HOST|PORT|USERNAME|PASSWORD|FROM_ADDRESS|USE_TLS>

# Reuse `slug_from_env_middle` for OIDC slugs (don't reimplement the rule).
# Insert each value into app_config as oauth.<slug>.* and smtp.*.

# If any rows were inserted OR any pre-existing rows are present:
#     set setup_completed = true       # existing deployment, no wizard
# else:
#     leave setup_completed unset      # fresh install, wizard runs
```

The "any pre-existing rows are present" branch covers deployments that ran the
env-fallback PR but never had OAuth env vars (configured purely through the
Config page) — they still mark `setup_completed` because `net_address` etc. are
already there.

**Phase 3 — Setup wizard (fresh install only).** Build the `/setup` SPA +
backend. Middleware enforces the redirect. Atomic commit at step 4. Existing
deployments are unaffected because the Phase 2 migration already marks them
`setup_completed`.

**Phase 4 — Recovery CLI + recovery-mode wizard.** Add the
`skynetcontrol-recovery` entry point, `admin_recovery_tokens` table,
`/recovery` route, recovery-cookie handling in `/setup`. Wizard learns
pre-fill + per-step save behaviour.

**Phase 5 — Env-var cleanup.** Strip `Settings` down to the five env vars
listed in the architectural shape. Delete the OIDC env-scanner. Remove
`settings` attrset from `module.nix`. Update docs.

## Migration

For installations upgrading through Phase 2: the Alembic data migration imports
any present env values for OAuth providers and SMTP into AppConfig, then sets
`setup_completed = true`. The deployment continues to work unchanged. Phase 5
removes the env vars; by that point they are no-ops on the upgraded install.

For the W0NE test server specifically: between Phase 2 and Phase 5 the existing
env-based config is fine; after Phase 5, NixOS config collapses to the shape
shown in the module-changes section, and any future edits happen via the
browser.

