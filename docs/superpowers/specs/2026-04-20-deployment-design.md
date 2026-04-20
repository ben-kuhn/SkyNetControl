# Module 8: Deployment & Packaging — Design Spec

**Goal:** Package SkyNetControl for development, NixOS service deployment, and OCI container deployment — all via Nix. No Dockerfile.

**Architecture:** Four Nix files: `shell.nix` (dev environment), `default.nix` (package derivation), `module.nix` (NixOS service), `oci.nix` (container image). Pydantic Settings class for environment-based configuration.

**Tech Stack:** Nix, NixOS, Python 3.12, Node.js 22, SQLAlchemy, Alembic, Uvicorn

---

## App Settings

Pydantic Settings class in `backend/config.py` with `SKYNET_` environment variable prefix:

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `database_url` | str | `sqlite:///skynetcontrol.db` | SQLAlchemy database URL |
| `static_dir` | str | `frontend/dist` | Frontend static assets path |
| `debug` | bool | False | Debug mode |
| `oidc_issuer_url` | str | `""` | OIDC provider issuer URL |
| `oidc_client_id` | str | `""` | OIDC client ID |
| `oidc_client_secret` | str | `""` | OIDC client secret |
| `oidc_redirect_uri` | str | `http://localhost:8000/api/auth/callback` | OIDC redirect URI |
| `jwt_secret_key` | str | `change-me-in-production` | JWT signing secret |
| `jwt_algorithm` | str | `HS256` | JWT algorithm |
| `jwt_expire_minutes` | int | 1440 | JWT expiry (24 hours) |
| `app_base_url` | str | `http://localhost:8000` | Application base URL |

All configurable via `SKYNET_` prefixed environment variables (e.g., `SKYNET_DATABASE_URL`).

---

## Development Environment (shell.nix)

- Python 3.12 with pip and virtualenv
- Node.js 22 with npm
- Auto-creates `.venv` virtualenv on shell entry
- Installs Python dependencies from `pyproject.toml[dev]`
- Installs frontend dependencies via `npm install`

---

## Nix Package (default.nix)

- Package name: `skynetcontrol`, version 0.1.0
- Build system: setuptools + setuptools-scm
- Python 3.12 application

**Dependencies:** FastAPI, Uvicorn, SQLAlchemy, Alembic, Pydantic, Pydantic-Settings, Authlib, python-jose, httpx, anthropic, jinja2

**Entry points:**
- `skynetcontrol-server` — Uvicorn factory: `backend.app:create_app`
- `skynetcontrol-alembic` — Alembic CLI with bundled config

**Post-install:**
- Copies frontend build to `$out/share/skynetcontrol/static`
- Copies `alembic.ini` and migrations to `$out/share/skynetcontrol/alembic`
- Wraps binaries with correct `PYTHONPATH`
- Sets `SKYNET_STATIC_DIR` environment variable

---

## NixOS Module (module.nix)

Declarative service configuration under `services.skynetcontrol`:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enable` | bool | false | Enable the service |
| `port` | int | 8000 | Listen port |
| `host` | str | `127.0.0.1` | Bind address |
| `stateDir` | str | `/var/lib/skynetcontrol` | State directory |
| `databaseUrl` | str | `sqlite:///{stateDir}/skynetcontrol.db` | Database URL |
| `settings` | attrs | `{}` | Extra env vars (SKYNET_ prefix auto-added) |

**Systemd service:**
- `ExecStartPre` — runs `skynetcontrol-alembic upgrade head` (auto-migrate)
- `ExecStart` — starts `skynetcontrol-server backend.app:create_app --factory`
- `DynamicUser=true` — runs as unprivileged dynamic user
- `StateDirectory=skynetcontrol` — managed state directory
- Security hardening: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`
- Restart policy: on-failure with 5s delay

---

## OCI Image (oci.nix)

- Built via `dockerTools.buildLayeredImage` — no Dockerfile
- Image: `skynetcontrol:latest`
- Includes: skynetcontrol package, coreutils, bash

**Defaults:**
- `SKYNET_DATABASE_URL=sqlite:////data/skynetcontrol.db`
- `SKYNET_STATIC_DIR` points to Nix store static assets
- Binds to `0.0.0.0:8000`
- Volume: `/data` for persistent database
- Exposes: port 8000/tcp

---

## What This Phase Does NOT Include

- **nixpkgs submission** — package structured for it but not yet submitted
- **sops-nix / agenix integration** — secrets currently via environment variables or plain-text DB config
- **CI/CD pipeline** — no automated build/test/deploy pipeline
- **PostgreSQL deployment guide** — supported via `database_url` but no dedicated docs
