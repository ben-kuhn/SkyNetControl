# Config Unification — Phase 5: Env-Var Cleanup

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the env surface honest. Phase 2a switched the runtime read paths to AppConfig; this phase deletes the now-dead env-parsing code in `Settings`, switches the last lingering env reader (the email-delivery backend), removes the obsolete CLI setup wizard (Phase 3's web wizard replaced it), strips `settings` from `module.nix`, and updates the deployment docs to reflect the much smaller env surface.

**Architecture:**

After Phase 5, the entire `SKYNET_*` env surface is:

| Env var | Purpose |
|---------|---------|
| `SKYNET_DATABASE_URL` | SQLAlchemy URL — chicken-and-egg, can't be in DB |
| `SKYNET_JWT_SECRET_KEY` | Stays env-only per the design decision in the spec |
| `SKYNET_APP_BASE_URL` | OAuth redirect URIs need this before any request lands |
| `SKYNET_JWT_ALGORITHM`, `SKYNET_JWT_EXPIRE_MINUTES` | Process-level JWT config |
| `SKYNET_DEBUG` | Process-level flag |
| `SKYNET_STATIC_DIR` | Set by Nix wrapper to point at the bundled frontend |

Everything else — OAuth providers, SMTP, net basics, scanner, callbook, delivery routing — lives in AppConfig and is edited via the wizard / Config page.

The Phase 2a Alembic migration (`603f5040bba2_import_env_to_app_config`) already ran once on every upgraded deployment, so existing env-var values are already in AppConfig. Phase 5 makes the env vars themselves no-ops for the previously-imported fields.

**Tech Stack:** Same as prior phases.

**Spec:** `docs/superpowers/specs/2026-06-12-config-unification-design.md`

---

## File structure

**Modified backend files:**

| Path | Change |
|------|--------|
| `backend/config.py` | Strip `Settings` to: `database_url`, `static_dir`, `debug`, `jwt_secret_key`, `jwt_algorithm`, `jwt_expire_minutes`, `app_base_url`. Delete `ProviderSettings`, `OIDCProviderConfig`, `SmtpSettings`, `_OIDC_ENV_RE`, `_gather_oidc_providers`. |
| `backend/integrations/delivery/service.py` | `build_backend_config` reads SMTP via `get_smtp_config(db)` (Phase 1 accessor) instead of `settings.smtp.*`. |
| `backend/auth/providers.py` | One-line comment update — Phase 2a left a stale reference to `ProviderSettings`. |

**Deleted backend files:**

| Path | Why |
|------|-----|
| `backend/cli/setup.py` | The CLI setup wizard predates Phase 3's web wizard. Phase 3 + Phase 4's recovery CLI cover the same surface (first-boot wizard + break-glass recovery). The CLI's OIDC env scanner is the last consumer of the to-be-deleted env model. |
| `tests/test_setup.py` | Tests for the deleted CLI wizard. |
| `tests/test_config_env_nesting.py` | Tests env parsing for the deleted Settings fields. |
| `tests/test_config_oidc.py` | Tests the deleted `_gather_oidc_providers` validator. |

**Modified config files:**

| Path | Change |
|------|--------|
| `pyproject.toml` | Remove the `skynetcontrol-setup` entry-point line. |
| `module.nix` | Remove `settings` attrset. Add `appBaseUrl` (string), `jwtSecretFile` (path, plumbed through systemd `LoadCredential` and `LoadCredential=jwt:<file>` → `SKYNET_JWT_SECRET_KEY` env). |
| `default.nix` | Possibly drop the `skynetcontrol-setup` wrapper if it exists (it doesn't — `pyproject.toml`'s entry point is the only reference). |

**Modified doc files:**

| Path | Change |
|------|--------|
| `docs/deployment/nix.md` | Replace the `settings.AUTH_*` / `SMTP__*` examples with the new collapsed config + `jwtSecretFile` recipe (age, sops-nix, or plain readable file). Add migration note for users upgrading through Phase 2a→5. |
| `README.md` | Update the NixOS quick-start to the new shape. |

---

## Task 1: Switch the delivery email backend to DB-backed SMTP

The last remaining consumer of `settings.smtp.*` in the runtime code is `backend/integrations/delivery/service.py:build_backend_config` (lines 22-27). Switching it is symmetric with Phase 2a's `backend/auth/email.py` switch and is a prerequisite for stripping `Settings.smtp`.

**Files:**
- Modify: `backend/integrations/delivery/service.py`
- Modify: tests that exercise the delivery email backend (`tests/test_delivery_email.py` if present; possibly `tests/test_delivery_wiring.py`).

### Steps

- [ ] **Step 1: Read the existing code**

```bash
sed -n '1,60p' backend/integrations/delivery/service.py
```

Note the signature of `build_backend_config(db, name)` — it takes `db` already, so we can call `get_smtp_config(db)` directly.

- [ ] **Step 2: Switch the SMTP read path**

Replace the `name == "email"` branch's SMTP lines (currently `config["smtp_host"] = settings.smtp.host`, etc.) with:

```python
from backend.config_mgmt.smtp import get_smtp_config   # top of file

# ...inside build_backend_config, in the `if name == "email":` branch
smtp = get_smtp_config(db)
if smtp is not None:
    config["smtp_host"] = smtp.host
    config["smtp_port"] = smtp.port
    config["smtp_username"] = smtp.username
    config["smtp_password"] = smtp.password
    config["smtp_use_tls"] = smtp.use_tls
    config["smtp_from_address"] = smtp.from_address
else:
    # SMTP not configured; leave the keys absent so EmailBackend.send
    # short-circuits the same way it does today on a blank host.
    pass
```

Drop the `from backend.config import settings` import if it's no longer needed in this file.

- [ ] **Step 3: Update tests**

Read `tests/test_delivery_email.py` (or whatever tests build_backend_config). Tests that constructed `Settings(smtp=SmtpSettings(host=..., port=...))` should now seed `upsert_smtp_config(db, SmtpConfig(...))` instead. The `SmtpConfig` and `upsert_smtp_config` symbols already exist (Phase 1).

If the test file doesn't exist, look for delivery-related tests via `grep -ln "build_backend_config\|EmailBackend" tests/`.

- [ ] **Step 4: Run delivery + lint**

```bash
.venv/bin/pytest tests/test_delivery_*.py -q
nix-shell --run "ruff check backend/ tests/"
```

- [ ] **Step 5: Run the full suite**

Expect a green pass (no new failures; existing tests that don't touch delivery SMTP are unaffected).

- [ ] **Step 6: Commit**

```bash
git add backend/integrations/delivery/service.py tests/test_delivery_*.py
git commit -m "feat(delivery): email backend reads SMTP from app_config

Mirrors Phase 2a's auth/email switch — the integration delivery
backend now also reads SMTP via get_smtp_config(db) instead of from
settings.smtp.*. This was the last runtime consumer of the env-only
SMTP fields and is a prerequisite for Phase 5's Settings cleanup."
```

---

## Task 2: Strip Settings + delete CLI wizard + delete obsolete tests

**Files:**
- Modify: `backend/config.py`
- Modify: `backend/auth/providers.py`
- Modify: `pyproject.toml`
- Delete: `backend/cli/setup.py`
- Delete: `tests/test_setup.py`
- Delete: `tests/test_config_env_nesting.py`
- Delete: `tests/test_config_oidc.py`

### Steps

- [ ] **Step 1: Verify no other consumer of `settings.smtp` or `settings.auth_*`**

```bash
grep -rn 'settings\.smtp\|settings\.auth_\|ProviderSettings\|OIDCProviderConfig\|SmtpSettings' backend/ tests/ --include='*.py'
```

Expected matches after Task 1: only the four files we're about to edit/delete (`backend/config.py`, `backend/cli/setup.py`, `tests/test_config_env_nesting.py`, `tests/test_config_oidc.py`). If anything else shows up, STOP and investigate before proceeding.

- [ ] **Step 2: Strip `backend/config.py`**

The new file body:

```python
"""Process-level configuration loaded from env vars.

After Phase 5 of the config-unification effort, this module is intentionally
tiny: everything that can live in the AppConfig table does. Only knobs that
must be available before the first DB read (database URL, JWT signing key,
the base URL the OAuth providers use as a redirect target) stay here.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///skynetcontrol.db"
    static_dir: str = "frontend/dist"
    debug: bool = False

    # JWT
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # App
    app_base_url: str = "http://localhost:8000"

    model_config = {"env_prefix": "SKYNET_"}


settings = Settings()
```

The `_OIDC_ENV_RE`, the `ProviderSettings`/`OIDCProviderConfig`/`SmtpSettings` models, and the `_gather_oidc_providers` validator are all gone.

- [ ] **Step 3: Fix the stale comment in `backend/auth/providers.py:162`**

```bash
grep -n "ProviderSettings" backend/auth/providers.py
```

Replace the line that mentions "where an empty ProviderSettings was effectively unusable" with something like:

```python
    # client_id check matches the previous Pydantic behaviour where a
    # provider with no credentials was effectively unusable.
```

- [ ] **Step 4: Delete the CLI setup wizard and its tests**

```bash
git rm backend/cli/setup.py tests/test_setup.py
```

Also remove the entry-point line from `pyproject.toml`:

```diff
-skynetcontrol-setup = "backend.cli.setup:main"
```

- [ ] **Step 5: Delete the env-parsing tests**

```bash
git rm tests/test_config_env_nesting.py tests/test_config_oidc.py
```

- [ ] **Step 6: Run the full suite + ruff**

```bash
.venv/bin/pytest -q
nix-shell --run "ruff check backend/ tests/"
```

Expect a green pass. The deleted tests' coverage is fully replaced by Phase 2a's `test_env_import.py` (which tests the migration that imports env→AppConfig) plus Phase 1's accessor tests.

- [ ] **Step 7: Commit**

```bash
git add backend/config.py backend/auth/providers.py pyproject.toml
git rm backend/cli/setup.py tests/test_setup.py tests/test_config_env_nesting.py tests/test_config_oidc.py
git commit -m "refactor(config): strip Settings to bootstrap-only fields

The env surface collapses to what truly cannot live in AppConfig:
DATABASE_URL, JWT_SECRET_KEY (+ algorithm + expiry), APP_BASE_URL,
DEBUG, STATIC_DIR. Everything else — OAuth providers, SMTP, net
basics, scanner, callbook, delivery routing — already moved to
AppConfig across Phases 1-2a; this commit deletes the dead Pydantic
models (ProviderSettings, OIDCProviderConfig, SmtpSettings) and the
_gather_oidc_providers env scanner that fed them.

Deletes:

- backend/cli/setup.py + tests/test_setup.py — the CLI setup wizard
  is obsolete; Phase 3's web wizard plus Phase 4's recovery CLI cover
  the same first-boot + break-glass surface with a much better UX.
- tests/test_config_env_nesting.py + tests/test_config_oidc.py —
  these tested env parsing for the now-removed Settings fields. The
  env-vars-still-work story for upgrading deployments is owned by
  Phase 2a's test_env_import.py (the one-time migration).

The pyproject skynetcontrol-setup entry point goes away alongside."
```

---

## Task 3: Nix module overhaul + deployment docs

**Files:**
- Modify: `module.nix`
- Modify: `docs/deployment/nix.md`
- Modify: `README.md`

### Step 3.1: Module

- [ ] **Step 1: Replace the `settings` attrset with `appBaseUrl` and `jwtSecretFile`**

The new `options.services.skynetcontrol`:

```nix
options.services.skynetcontrol = {
  enable        = lib.mkEnableOption "SkyNetControl Winlink net management";
  port          = lib.mkOption { type = lib.types.port; default = 8000; ... };
  host          = lib.mkOption { type = lib.types.str;  default = "127.0.0.1"; ... };
  stateDir      = lib.mkOption { type = lib.types.path; default = "/var/lib/skynetcontrol"; ... };

  databaseUrl   = lib.mkOption {
    type = lib.types.str;
    default = "sqlite:///${cfg.stateDir}/skynetcontrol.db";
    defaultText = lib.literalExpression ''"sqlite:///''${cfg.stateDir}/skynetcontrol.db"'';
    description = "SQLAlchemy database URL.";
  };

  appBaseUrl = lib.mkOption {
    type = lib.types.str;
    example = "https://skynetcontrol.example.org";
    description = ''
      Externally-visible base URL of the running app. Used to construct
      OAuth provider redirect URIs and to compose links in transactional
      emails. Must match the host the user's browser hits (including
      scheme and any non-default port).
    '';
  };

  jwtSecretFile = lib.mkOption {
    type = lib.types.path;
    description = ''
      Path to a file containing the JWT signing secret on disk. The file
      should be readable only by root; the unit reads it via systemd's
      LoadCredential mechanism, so the secret never appears in the Nix
      store. Generate with e.g. `openssl rand -hex 32 > /etc/skynetcontrol-jwt`.
    '';
  };
};
```

The `settings` attrset is removed.

- [ ] **Step 2: Update the systemd unit body**

```nix
systemd.services.skynetcontrol = {
  # ...
  environment = {
    SKYNET_DATABASE_URL = cfg.databaseUrl;
    SKYNET_APP_BASE_URL = cfg.appBaseUrl;
  };

  serviceConfig = {
    # ...existing hardening...

    # Pipe the JWT secret through LoadCredential so it never lands in the
    # Nix store. The systemd-managed credential file is referenced as
    # $CREDENTIALS_DIRECTORY/jwt inside the unit; ExecStart wraps the
    # server in a small shell that reads it.
    LoadCredential = [ "jwt:${cfg.jwtSecretFile}" ];
    ExecStartPre = "${skynetcontrol}/bin/skynetcontrol-alembic -c ${skynetcontrol}/share/skynetcontrol/alembic.ini upgrade head";
    ExecStart = ''
      ${pkgs.bash}/bin/bash -c '\
        export SKYNET_JWT_SECRET_KEY="$(cat $CREDENTIALS_DIRECTORY/jwt)" && \
        exec ${skynetcontrol}/bin/skynetcontrol-server backend.app:create_app --factory --host ${cfg.host} --port ${toString cfg.port}'
    '';
  };
};
```

(Note: if `LoadCredential` syntax in the user's NixOS version differs, adapt — the goal is "secret read from a file at startup, available as `SKYNET_JWT_SECRET_KEY` env to the process.")

- [ ] **Step 3: Verify the module evaluates cleanly**

```bash
nix-instantiate --parse module.nix > /dev/null && echo OK
nix-instantiate --eval --strict --json -E '
let
  pkgs = import <nixpkgs> {};
  eval = import <nixpkgs/nixos/lib/eval-config.nix> {
    system = builtins.currentSystem;
    modules = [
      ./module.nix
      ({ ... }: {
        boot.isContainer = true;
        services.skynetcontrol = {
          enable = true;
          appBaseUrl = "https://example.org";
          jwtSecretFile = "/etc/skynetcontrol-jwt-test";
        };
      })
    ];
  };
in {
  envBaseUrl = eval.config.systemd.services.skynetcontrol.environment.SKYNET_APP_BASE_URL;
  loadCred = eval.config.systemd.services.skynetcontrol.serviceConfig.LoadCredential;
}
' | tail -3
```

Expect the env vars and LoadCredential to be present.

### Step 3.2: Docs

- [ ] **Step 4: Update `docs/deployment/nix.md`**

Search the file for `settings.AUTH_*`, `SMTP__*`, `PAT_MAILBOX_PATH` examples and rewrite the NixOS quick-start to look like:

```nix
services.skynetcontrol = {
  enable        = true;
  host          = "127.0.0.1";
  port          = 8040;
  stateDir      = "/storage/skynetcontrol";
  appBaseUrl    = "https://skynetcontrol.example.org";
  jwtSecretFile = config.age.secrets.skynetcontrol-jwt.path;
};
users.users.skynetcontrol.extraGroups = [ "pat" ];
```

Add a section "After upgrading":

> If you previously set `services.skynetcontrol.settings.AUTH_*` / `SMTP__*` /
> `PAT_MAILBOX_PATH`, those values were imported into the AppConfig table by
> the one-time `import_env_to_app_config` Alembic migration shipped in the
> earlier release. You can now manage them via the `/config` admin page
> (after signing in) or via the first-boot wizard at `/setup`. Removing the
> `settings.*` lines from your NixOS config is the only required step; the
> values keep working from the database.

- [ ] **Step 5: Update `README.md`**

If the README has a "Quick start" NixOS snippet, update it to the new shape.

- [ ] **Step 6: Commit**

```bash
git add module.nix docs/deployment/nix.md README.md
git commit -m "feat(nix): drop module.nix.settings; add appBaseUrl + jwtSecretFile

Phase 5 collapse. The settings attrset no longer makes sense: the env
vars it covered (AUTH_*, SMTP__*, PAT_MAILBOX_PATH, etc.) are all in
AppConfig now. The remaining knobs that genuinely belong in env are
exposed as first-class options:

- appBaseUrl: a string, plumbed through SKYNET_APP_BASE_URL. Used for
  OAuth redirect URIs and email link bodies.
- jwtSecretFile: a path. The unit reads it via systemd LoadCredential
  and exports SKYNET_JWT_SECRET_KEY at startup, so the secret never
  hits the Nix store.

Docs updated to match: the post-Phase-5 NixOS config is ~6 lines.
Existing deployments only need to delete their settings.* lines; the
already-applied import_env_to_app_config migration kept their data."
```

---

## Out of scope

- **Periodic cleanup of expired `admin_recovery_tokens`.** Phase 4 left these to accumulate; a `delete_expired` helper + a systemd timer could land later.
- **The previously-deferred items** still untouched:
  - Phase 2a per-iteration `get_smtp_config(db)` in `notify_admins_*` loops
  - Phase 2a `resolve_provider` double-scans `list_oauth_providers(db)` per login
  - Phase 1 `_check_slug` bypasses `validate_slug` regex for fixed slugs
  - Phase 3 `_SETUP_SESSIONS` TTL sweep
  - Phase 4 `revoke_by_prefix` doesn't escape LIKE wildcards

**Phase 5 success criterion:** A fresh `git pull` + `nixos-rebuild switch` against a NixOS host running the previous (post-Phase-4) release boots cleanly into the new release with the user's `services.skynetcontrol.settings.AUTH_*` / `SMTP__*` lines removed. Auth, SMTP, and all other previously-env-driven config keep working from the AppConfig rows the Phase 2a migration already populated.
